"""Read-only fee estimates using the owner's saved legacy payment-method terms."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from bnpl.settlements_service import _capture_fee_components
from excel_parser import normalize_name, resolve_payment_setting
from operational_fee_methods import fee_rail
from operational_provider_fees import provider_projection
from payment_methods import KNOWN_PAYMENT_SUB_KEYS, normalize_payment_method

CENT = Decimal(".01")
ZERO = Decimal(0)
PROVIDERS = {"salla", "tamara", "tabby", "emkan"}
RIYADH = timezone(timedelta(hours=3))


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value))
        return value if value.is_finite() and value >= 0 else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def cash(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def configured_payment_setting(settings, provider, method=None):
    """Use the same exact-name and canonical precedence as the settings export."""
    key = fee_rail(method) if provider == "salla" else provider
    if key is None:
        return None
    exact_rows, canonical_rows = {}, {}
    for row in settings.get("payment_methods") or []:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        exact_rows[normalize_name(row["name"])] = row
        subkey, _name, _parent = normalize_payment_method(row["name"])
        if subkey in KNOWN_PAYMENT_SUB_KEYS:
            canonical_rows.setdefault(subkey, row)
    lookup = method if provider == "salla" and isinstance(method, str) else key
    return resolve_payment_setting(lookup, exact_rows, canonical_rows)


def configured_terms(settings, provider, method=None):
    """Validate every term of the selected row before using its saved values."""
    row = configured_payment_setting(settings, provider, method)
    if row is None:
        return None
    values = [number(row.get(k)) for k in ("commission_percent", "fixed_fee", "vat_percent")]
    if any(v is None for v in values):
        return None
    rate, fixed, vat = values
    if rate > 100 or vat > 100 or fixed != cash(fixed):
        return None
    return rate / 100, fixed, vat / 100


def has_capture_policies(policies, provider, currency="SAR"):
    return any(row.get('provider') == provider and row.get('currency') == currency
               and row.get('status') == 'active' and row.get('event_type', 'capture') == 'capture'
               for row in policies)


def _policy_day(value):
    if isinstance(value, dict):
        stamp = datetime.fromisoformat(str(value.get('date') or '').replace('Z', '+00:00'))
        if stamp.tzinfo is None and value.get('timezone'):
            stamp = stamp.replace(tzinfo=ZoneInfo(value['timezone']))
        value = stamp
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or '')
    if len(text) == 10:
        return date.fromisoformat(text)
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(text.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('provider_fee_transaction_date_required')
    return stamp.astimezone(RIYADH).date()


def configured_projection(policies, provider, gross, *, currency="SAR", method=None,
                          refunded=ZERO, refunds=None, payment=None, created_at=None, observed_at=None,
                          source_mode="legacy_settings", as_of=None):
    """Price a known amount with the existing policy engine, without posting facts."""
    if not has_capture_policies(policies, provider, currency):
        if source_mode == 'mz2_policies':
            raise ValueError('provider_fee_policy_incomplete_or_ambiguous')
        return None
    source = payment or {}
    captures = source.get('captures') or []
    try:
        dates = [_policy_day(row['captured_at']) for row in captures]
        if not dates:
            dates = [_policy_day(created_at)]
            if observed_at:
                dates.append(_policy_day(observed_at))
    except (ValueError, TypeError, KeyError):
        raise ValueError('provider_fee_transaction_date_required') from None
    calculation = {
        'provider': provider, 'currency': currency, 'gross': str(gross),
        'payment_method': fee_rail(method) if provider == 'salla' else None,
        'plan_id': source.get('plan_id'), 'fee_plan_issue': source.get('fee_plan_issue'),
        'refunds': refunds if refunds is not None else source.get('refunds', []),
    }
    result = provider_projection(calculation, policies, max(dates), earliest_date=min(dates),
                                 as_of_date=_policy_day(as_of or observed_at) if as_of or observed_at else None)
    if number(result['refunded']) != number(refunded):
        raise ValueError('provider_refund_fee_source_incomplete')
    return result


def estimate_fee(settings, provider, gross, *, method=None, refunded=ZERO, policies=(),
                 refunds=None, payment=None, created_at=None, observed_at=None, source_mode="legacy_settings", as_of=None):
    """Apply the existing per-order rounding contract to evidenced SAR payments."""
    gross, refunded = number(gross), number(refunded)
    if gross is None or refunded is None or refunded > gross:
        return None
    try:
        projection = configured_projection(policies, provider, gross, method=method, refunded=refunded,
            refunds=refunds, payment=payment, created_at=created_at, observed_at=observed_at,
            source_mode=source_mode, as_of=as_of)
    except (ValueError, TypeError, KeyError, InvalidOperation):
        return None
    if projection is not None:
        return number(projection['estimated_fees'])
    terms = configured_terms(settings, provider, method)
    if terms is None:
        return None
    if gross == 0:
        return ZERO
    rate, fixed, vat = terms
    if provider == "salla":
        if refunded:
            # Salla refund fee credits require their executed statement terms.
            return None
        raw = gross * rate + fixed
        return cash(raw) + cash(raw * vat)
    if provider not in PROVIDERS:
        return None
    fee, tax = _capture_fee_components(provider, gross, rate, fixed, vat)
    total = cash(Decimal(str(fee)) + Decimal(str(tax)))
    if refunded and provider == "tabby":
        # Existing Tabby contract caps its refundable percentage leg at 4.99%.
        # The total percentage, fixed fee and VAT always come from saved terms.
        refundable_rate = min(rate, Decimal(".0499"))
        credit = cash(refunded * refundable_rate)
        credit_vat = (credit * vat).quantize(CENT, rounding=ROUND_HALF_EVEN)
        total -= credit + credit_vat
    elif refunded and provider != "tamara":
        return None
    return max(total, ZERO)


def refresh_provider_fee_inputs(state, sources):
    """Retain report inputs independently of whether a paid claim passed posting gates."""
    if "provider_fee_settings" not in sources:
        return
    previous = state["engine"].get("provider_fee_inputs", {}).get("orders", {})
    orders = deepcopy(previous)
    for order in sources.get("orders", []):
        payment = order.get("payment") or {}
        valuation = order.get("customer_payment_sar") or {}
        if (payment.get("provider") not in PROVIDERS and not payment.get("split")
                and not PROVIDERS.intersection(valuation.get("native", {}))):
            continue
        identity = str(order["id"])
        orders[identity] = {k: deepcopy(order[k]) for k in (
            "currency", "created_at", "updated_at", "payment", "customer_payment_sar") if k in order}
    state["engine"]["provider_fee_inputs"] = {
        "settings": deepcopy(sources["provider_fee_settings"]), "orders": orders,
        "policies": deepcopy(sources.get("fee_policies", [])),
        "source_mode": sources.get("provider_fee_source_mode", "legacy_settings"),
    }
