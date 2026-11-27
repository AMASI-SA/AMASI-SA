"""Read Salla's actual bank-transfer order amount from Mezan's canonical DTO.

Never use an amount typed by the WhatsApp customer or interpreted from a
receipt screenshot as the order total. The receipt may be fake or incomplete.
If Mezan has not synchronized the Salla order, return None and keep the
receipt under human review without treating the amount as matched.

This is read-only. No bank balance, GL, paid status or Salla order writes.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable


class BankOrderNotVerified(ValueError):
    pass


def _money(value: Any) -> Decimal | None:
    if type(value) not in (str, int, float, Decimal):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if (not amount.is_finite() or amount <= 0 or
            amount.as_tuple().exponent < -2):
        return None
    return amount


async def read_synced_bank_order_amount(
    *, owner_id: str, salla_order_id: int, reference_id: int | None,
    load_order: Callable[[str, str], Awaitable[Any]],
) -> str | None:
    if (not owner_id or type(salla_order_id) is not int or salla_order_id < 1):
        raise BankOrderNotVerified("verified_salla_bank_order_required")
    if reference_id is None:
        return None
    if type(reference_id) is not int or reference_id < 1:
        raise BankOrderNotVerified("verified_bank_reference_required")
    try:
        order = await load_order(owner_id, str(reference_id))
    except LookupError:
        return None
    if order is None:
        return None
    if str(getattr(order, "order_id", "")) != str(salla_order_id) or (
            str(getattr(order, "order_number", "")) != str(reference_id)):
        raise BankOrderNotVerified("foreign_or_mismatched_salla_bank_order")
    method = str(getattr(getattr(order, "payment", None), "method", "") or "").strip().casefold()
    native = str(getattr(getattr(order, "payment", None), "method_native", "") or "").strip()
    if method and method not in ("bank", "bank_transfer", "bank transfer") and (
            native not in ("تحويل بنكي", "حوالة بنكية")):
        raise BankOrderNotVerified("salla_synced_order_not_bank_transfer")
    payment = getattr(order, "payment", None)
    totals = getattr(order, "totals", None)
    if not payment or not totals:
        return None
    if (getattr(payment, "collection_status", None) in ("paid", "partial") or
            str(getattr(payment, "status", "") or "").casefold() == "paid"):
        raise BankOrderNotVerified("bank_payment_already_recorded")
    paid = getattr(payment, "paid_amount", None)
    if type(paid) not in (int, float, str, Decimal) or Decimal(str(paid)) != 0:
        raise BankOrderNotVerified("bank_payment_has_paid_amount")
    if str(getattr(totals, "currency", "")).upper() != "SAR":
        raise BankOrderNotVerified("bank_order_currency_not_sar")
    total = _money(getattr(totals, "total", None))
    if total is None:
        return None
    return f"{total:.2f}"
