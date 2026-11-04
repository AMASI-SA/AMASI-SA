"""Value remaining USD advertising balances using the saved account cost terms."""
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


COLLECTION = "mezan_ad_account_cost_settings_v2"
CENT = Decimal("0.01")
HUNDRED = Decimal("100")
BANK_COMMISSION_VAT_PCT = Decimal("15")


def _decimal(value):
    if isinstance(value, bool):
        raise ValueError("invalid_ad_cost_number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("invalid_ad_cost_number") from exc
    if not number.is_finite():
        raise ValueError("invalid_ad_cost_number")
    return number


def _money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def saved_basis(binding, account, settings, as_of):
    """Read the exact configured terms for one bound account."""
    matches = [row for row in settings
               if row.get("mezan_integration_account_id") == account.get("mezan_integration_account_id")]
    if len(matches) != 1:
        return None
    setting = matches[0]
    if (account.get("currency") != "USD" or setting.get("native_currency") != "USD"
            or setting.get("user_id") != binding.get("user_id")
            or setting.get("provider") != account.get("provider")
            or setting.get("external_account_id") != account.get("external_account_id")
            or not isinstance(setting.get("apply_bank_commission"), bool)):
        return None
    try:
        rate = _decimal(setting.get("exchange_rate_to_sar"))
        commission = _decimal(setting.get("bank_commission_pct"))
        updated_at = datetime.fromisoformat(str(setting.get("updated_at")).replace("Z", "+00:00"))
        if updated_at.tzinfo is None or updated_at > as_of or not 0 < rate <= 20 or not 0 <= commission <= 20:
            return None
    except (ValueError, TypeError):
        return None
    return {
        "currency": "USD", "exchange_rate_to_sar": str(rate),
        "bank_commission_pct": str(commission),
        "apply_bank_commission": setting["apply_bank_commission"],
        "bank_commission_vat_pct": str(BANK_COMMISSION_VAT_PCT),
        "integration_account_id": account["mezan_integration_account_id"],
        "source": COLLECTION, "updated_at": updated_at.isoformat(),
    }


def project_parties(parties, bases, issues):
    """Convert the current unpaid balance once after native funding allocation."""
    for key, party in list(parties.items()):
        if party["party_type"] != "ad_account" or party["currency"] != "USD":
            continue
        basis = bases.get(party["party_id"], {})
        target_key = ("ad_account", party["party_id"], "SAR")
        try:
            if basis.get("currency") != "USD" or basis.get("source") != COLLECTION:
                raise ValueError("ad_balance_cost_settings_missing")
            rate = _decimal(basis["exchange_rate_to_sar"])
            pct = _decimal(basis["bank_commission_pct"])
            vat_pct = _decimal(basis["bank_commission_vat_pct"])
            if (not 0 < rate <= 20 or not 0 <= pct <= 20 or not 0 <= vat_pct <= 100
                    or not isinstance(basis.get("apply_bank_commission"), bool)
                    or any(row["party_type"] == target_key[0] and row["party_id"] == target_key[1]
                           and row["currency"] == target_key[2] for row in parties.values())):
                raise ValueError("ad_balance_cost_settings_invalid")
        except (ValueError, KeyError, TypeError):
            issues.append({"code": "ad_balance_sar_unavailable", "source_id": party["party_id"],
                           "currency": "SAR", "source_currency": "USD",
                           "message": "إعدادات تحويل الحساب الإعلاني الدولاري تحتاج إلى استكمال."})
            continue
        native = {field: format(value, ".2f") for field, value in party.items() if isinstance(value, Decimal)}
        converted = {field: _money(value * rate) if isinstance(value, Decimal) else value
                     for field, value in party.items()}
        payable = converted["outstanding_payable"]
        commission = _money(payable * pct / HUNDRED) if basis["apply_bank_commission"] else Decimal("0.00")
        vat = _money(commission * vat_pct / HUNDRED)
        converted["outstanding_payable"] += commission + vat
        converted["confirmed_payable"] += commission + vat
        converted["ad_payable"] += commission + vat
        converted.update(currency="SAR", native_balance={"currency": "USD", **native},
                         ad_sar_valuation={**basis, "amount_sar": format(payable, ".2f"),
                                           "bank_commission": format(commission, ".2f"),
                                           "bank_commission_vat": format(vat, ".2f")})
        parties[key] = converted
