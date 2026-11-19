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


def bank_charges(net, pct, vat_pct):
    commission = _money(net * pct / HUNDRED)
    return commission, _money(commission * vat_pct / HUNDRED)


def funding_quote(amount, currency, basis):
    """Quote a payment using the same rounded charges as the account valuation."""
    if not basis or not isinstance(basis.get("apply_bank_commission"), bool):
        raise ValueError("missing_terms")
    rate = _decimal(basis.get("exchange_rate_to_sar"))
    pct = _decimal(basis.get("bank_commission_pct"))
    vat_pct = _decimal(basis.get("bank_commission_vat_pct"))
    if not 2 <= rate <= 6 or not 0 <= pct <= 20 or not 0 <= vat_pct <= 100:
        raise ValueError("invalid_terms")
    if not basis["apply_bank_commission"]:
        pct = Decimal(0)
    if currency == "USD":
        usd = amount
        net = _money(usd * rate)
        fee, vat = bank_charges(net, pct, vat_pct)
        gross = net + fee + vat
    else:
        gross = amount
        approximate = _money(gross / (1 + pct / HUNDRED * (1 + vat_pct / HUNDRED)))
        for net in (approximate, approximate - CENT, approximate + CENT):
            fee, vat = bank_charges(net, pct, vat_pct)
            if net > 0 and net + fee + vat == gross:
                break
        else:
            raise ValueError("receipt_required")
        usd = _money(net / rate)
    if usd <= 0 or not 2 <= net / usd <= 6:
        raise ValueError("receipt_required")
    return {"amount_usd": format(usd, ".2f"), "gross_sar": format(gross, ".2f"),
            "fee_sar": format(fee + vat, ".2f"), "commission_sar": format(fee, ".2f"),
            "fee_vat_sar": format(vat, ".2f"), "exchange_rate": str(rate)}


def _valued_party(party, basis):
    if basis.get("currency") != "USD" or basis.get("source") != COLLECTION:
        raise ValueError("ad_balance_cost_settings_missing")
    rate = _decimal(basis["exchange_rate_to_sar"])
    pct = _decimal(basis["bank_commission_pct"])
    vat_pct = _decimal(basis["bank_commission_vat_pct"])
    if (not 0 < rate <= 20 or not 0 <= pct <= 20 or not 0 <= vat_pct <= 100
            or not isinstance(basis.get("apply_bank_commission"), bool)):
        raise ValueError("ad_balance_cost_settings_invalid")
    native = {field: format(value, ".2f") for field, value in party.items() if isinstance(value, Decimal)}
    converted = {field: _money(value * rate) if isinstance(value, Decimal) else value
                 for field, value in party.items()}
    payable = converted["outstanding_payable"]
    commission, vat = bank_charges(payable, pct if basis["apply_bank_commission"] else Decimal(0), vat_pct)
    for field in ("outstanding_payable", "confirmed_payable", "ad_payable"):
        if field in converted:
            converted[field] += commission + vat
    converted.update(currency="SAR", native_balance={"currency": "USD", **native},
                     ad_sar_valuation={**basis, "amount_sar": format(payable, ".2f"),
                                       "bank_commission": format(commission, ".2f"),
                                       "bank_commission_vat": format(vat, ".2f")})
    return converted


def workspace_amounts(row, basis, has_sar_row=False):
    """Return display amounts only. Entry and statement currencies stay native."""
    if not row:
        return None
    currency = row["currency"]
    amounts = {"currency": currency, "prepaid": row["ad_wallet_balance"], "payable": row["outstanding_payable"]}
    if currency != "USD" or has_sar_row:
        return amounts
    try:
        converted = _valued_party({"outstanding_payable": _decimal(amounts["payable"]),
                                   "ad_wallet_balance": _decimal(amounts["prepaid"])}, basis)
    except (KeyError, TypeError, ValueError):
        return amounts
    return {**amounts, "currency": "SAR", "prepaid": format(converted["ad_wallet_balance"], ".2f"),
            "payable": format(converted["outstanding_payable"], ".2f"), "native": amounts}


def project_parties(parties, bases, issues):
    """Convert the current unpaid balance once after native funding allocation."""
    for key, party in list(parties.items()):
        if party["party_type"] != "ad_account" or party["currency"] != "USD":
            continue
        try:
            if any(row["party_type"] == "ad_account" and row["party_id"] == party["party_id"]
                   and row["currency"] == "SAR" for row in parties.values()):
                raise ValueError("ad_balance_cost_settings_invalid")
            parties[key] = _valued_party(party, bases.get(party["party_id"], {}))
        except (ValueError, KeyError, TypeError):
            issues.append({"code": "ad_balance_sar_unavailable", "source_id": party["party_id"],
                           "currency": "SAR", "source_currency": "USD",
                           "message": "إعدادات تحويل الحساب الإعلاني الدولاري تحتاج إلى استكمال."})
