"""Read-only fee estimates using the owner's saved legacy payment-method terms."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP

from bnpl.settlements_service import _capture_fee_components
from operational_fee_methods import fee_rail
from payment_methods import CARD_FALLBACK_SUB_KEYS, normalize_payment_method

CENT = Decimal(".01")
ZERO = Decimal(0)
PROVIDERS = {"salla", "tamara", "tabby", "emkan"}


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


def configured_terms(settings, provider, method=None):
    """Resolve one explicit, valid configured row. Zero must be explicitly saved."""
    key = fee_rail(method) if provider == "salla" else provider
    rows = {}
    for row in settings.get("payment_methods") or []:
        if not isinstance(row, dict):
            continue
        subkey, _name, _parent = normalize_payment_method(row.get("name") or "")
        rows.setdefault(subkey, []).append(row)
    matches = rows.get(key, [])
    if not matches and key in CARD_FALLBACK_SUB_KEYS:
        matches = rows.get("credit_card", [])
    if len(matches) != 1:
        return None
    row = matches[0]
    values = [number(row.get(k)) for k in ("commission_percent", "fixed_fee", "vat_percent")]
    if any(v is None for v in values):
        return None
    rate, fixed, vat = values
    if rate > 100 or vat > 100 or fixed != cash(fixed):
        return None
    return rate / 100, fixed, vat / 100


def estimate_fee(settings, provider, gross, *, method=None, refunded=ZERO):
    """Apply the existing per-order rounding contract to evidenced SAR payments."""
    gross, refunded = number(gross), number(refunded)
    terms = configured_terms(settings, provider, method)
    if gross is None or refunded is None or refunded > gross or terms is None:
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
    }
