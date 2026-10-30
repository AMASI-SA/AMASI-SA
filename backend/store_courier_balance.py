"""Shared provider COD evidence for courier labels and driver collection.

Pure, read-only calculations. Never infer a zero balance from missing data.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


class CodBalanceUnavailable(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("slug") or value.get("name") or value.get("status")
    return _text(value).lower().replace("-", "_").replace(" ", "_")


def cod_balance_when_salla_omits_remaining(
    order: dict[str, Any],
    remaining_action: dict[str, Any],
) -> dict[str, Any]:
    """Derive only a provable COD balance; Salla can return null for unpaid COD."""
    payment = order.get("payment")
    payment = payment if isinstance(payment, dict) else {}
    amounts = order.get("amounts")
    amounts = amounts if isinstance(amounts, dict) else {}
    total = amounts.get("total") or order.get("total")
    currency = (_text(total.get("currency")) if isinstance(total, dict) else "") or "SAR"
    currency = currency.upper()

    def fail() -> None:
        raise CodBalanceUnavailable("cod_balance_unavailable")

    def amount(value: Any) -> Decimal:
        raw = value.get("amount") if isinstance(value, dict) else value
        if isinstance(value, dict):
            source_currency = _text(value.get("currency")).upper()
            if source_currency and source_currency != currency:
                fail()
        if isinstance(raw, bool) or raw in (None, ""):
            fail()
        try:
            result = Decimal(str(raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            fail()
        if not result.is_finite() or result < 0:
            fail()
        return result

    if total is None:
        fail()
    order_total = amount(total)
    if not currency:
        fail()
    paid = next(
        (value for value in (
            order.get("paid_amount"),
            payment.get("paid_amount"),
            remaining_action.get("paid_amount"),
        ) if value is not None),
        None,
    )
    collection_status = _status(
        order.get("payment_collection_status") or payment.get("collection_status")
    )
    payment_status = _status(order.get("payment_status") or payment.get("status"))

    if payment_status in {"refunded", "refund", "cancelled", "canceled"}:
        fail()

    if paid is None:
        if collection_status == "unpaid" or payment_status == "unpaid":
            paid_amount = Decimal("0.00")
        elif collection_status == "paid" or payment_status in {"paid", "completed"}:
            paid_amount = order_total
        else:
            fail()
    else:
        paid_amount = amount(paid)
    if paid_amount > order_total:
        fail()
    if (
        collection_status == "paid" or payment_status in {"paid", "completed"}
    ) and paid_amount != order_total:
        fail()
    if (collection_status == "partial" or payment_status == "partial") and (
        paid_amount <= 0 or paid_amount >= order_total
    ):
        fail()
    if (collection_status == "unpaid" or payment_status == "unpaid") and paid_amount > 0:
        fail()
    return {"amount": float(order_total - paid_amount), "currency": currency}

