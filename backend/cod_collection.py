"""Read-only COD collection amount derived from Salla's order payment facts.

Salla can report remaining_action.remaining_amount=null and
has_remaining_amount=false for an unpaid COD order. Those checkout-action
fields do not describe the cash the customer owes on delivery. Only derive
an amount when method, total, paid and currency agree; never use the
shipment cash_on_delivery fee as the amount due.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

_CENT = Decimal("0.01")
_COD_NAMES = {
    "cod",
    "cash on delivery",
    "الدفع عند الاستلام",
    "دفع عند الاستلام",
    "الدفع عند التسليم",
    "دفع عند التسليم",
}
_PAID_STATES = {"paid", "completed", "مدفوع", "مكتمل"}


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("_", " ").replace("-", " ").split())


def is_cash_on_delivery(method: Any) -> bool:
    if isinstance(method, dict):
        return any(
            is_cash_on_delivery(method.get(key))
            for key in ("code", "slug", "name", "label")
        )
    return _normalized(method) in _COD_NAMES


def _amount_and_currency(value: Any) -> tuple[Decimal, str | None] | None:
    currencies: set[str] = set()
    for _ in range(8):
        if not isinstance(value, dict):
            break
        currency = str(value.get("currency") or "").strip().upper()
        if currency:
            currencies.add(currency)
        if "amount" in value:
            value = value["amount"]
        elif "value" in value:
            value = value["value"]
        else:
            return None
    if isinstance(value, bool) or value is None or len(currencies) > 1:
        return None
    try:
        amount = Decimal(str(value).strip())
        if not amount.is_finite():
            return None
        return amount.quantize(_CENT, rounding=ROUND_HALF_UP), next(iter(currencies), None)
    except (InvalidOperation, TypeError, ValueError):
        return None


def cod_expected_due(
    method: Any,
    *,
    total: Any,
    paid: Any,
    remaining: Any = None,
    payment_status: Any = None,
    collection_status: Any = None,
) -> float | None:
    """Return COD amount due only for a provably unpaid/partially paid order.

    A positive provider remaining amount remains authoritative. A null or
    zero checkout-action balance may be replaced by total minus paid, provided
    Salla has supplied both amounts in the same currency and has not marked
    the payment paid. None means callers should keep their existing value.
    """
    if not is_cash_on_delivery(method):
        return None
    if _normalized(payment_status) in _PAID_STATES:
        return None
    if _normalized(collection_status) in _PAID_STATES:
        return None

    total_value = _amount_and_currency(total)
    paid_value = _amount_and_currency(paid)
    remaining_value = (
        _amount_and_currency(remaining) if remaining is not None else None
    )
    if total_value is None or paid_value is None:
        return None
    if remaining is not None and remaining_value is None:
        return None
    currencies = {
        currency
        for _, currency in (total_value, paid_value, *([remaining_value] if remaining_value else []))
        if currency
    }
    if len(currencies) > 1:
        return None

    total_amount, paid_amount = total_value[0], paid_value[0]
    if total_amount <= 0 or paid_amount < 0 or paid_amount >= total_amount:
        return None
    if remaining_value and remaining_value[0] != 0:
        return None
    return float(total_amount - paid_amount)
