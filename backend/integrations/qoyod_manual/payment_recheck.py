"""Read-only Salla payment recheck for Qoyod operators.

No function in this module writes to the database or imports/calls a Qoyod
client. Results are intentionally ephemeral and returned to the operator.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from integrations.qoyod.candidate_orders import (
    PAYMENT_ELIGIBLE,
    PAYMENT_INELIGIBLE,
    eligible_status_key,
    payment_eligibility,
)


async def recheck_payment_read_only(
    db,
    *,
    orders_user_id: str,
    order_number: str,
    fetch_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Read current Salla facts and classify them without side effects."""
    order_number = str(order_number or "").strip()
    if not order_number or not order_number.isdigit():
        return {
            "ok": False,
            "order_number": order_number,
            "outcome": "error",
            "code": "invalid_order_number",
            "message": "رقم الطلب غير صالح",
        }

    if fetch_fn is None:
        from salla_integration.sync import fetch_single_order_status
        fetch_fn = fetch_single_order_status

    result = await fetch_fn(db, orders_user_id, order_number)
    if not result.get("ok") or not result.get("found"):
        code = result.get("error") or "salla_lookup_failed"
        return {
            "ok": False,
            "order_number": order_number,
            "outcome": "error",
            "code": code,
            "stage": result.get("stage"),
            "needs_reauth": bool(result.get("needs_reauth")),
            "message": (
                "الطلب غير موجود في سلة"
                if code == "not_found_in_salla"
                else "تعذر قراءة أحدث بيانات الدفع من سلة"
            ),
        }

    row = result.get("order") or {}
    status_key = eligible_status_key(
        row.get("order_status_slug"),
        row.get("order_status"),
    )
    payment = payment_eligibility(row)
    if status_key and payment == PAYMENT_ELIGIBLE:
        outcome = "ready"
        message = "حالة الطلب والدفع مؤهلتان، ولم يتم إرسال أي فاتورة"
    elif payment == PAYMENT_INELIGIBLE:
        outcome = "unpaid"
        message = "أحدث بيانات سلة لا تثبت دفعًا مؤهلًا"
    else:
        outcome = "review"
        message = (
            "حالة الطلب الحالية غير مؤهلة"
            if not status_key
            else "بيانات الدفع الحالية تحتاج مراجعة"
        )

    return {
        "ok": True,
        "order_number": order_number,
        "outcome": outcome,
        "message": message,
        "status_key": status_key,
        "status_slug": row.get("order_status_slug"),
        "status_native": row.get("order_status"),
        "payment_eligibility": payment,
        "payment_method": row.get("payment_method"),
        "payment_status": row.get("payment_status"),
        "payment_collection_status": row.get("payment_collection_status"),
        "is_pending_payment": row.get("is_pending_payment"),
        "paid_amount": row.get("paid_amount"),
        "remaining_amount": row.get("remaining_amount"),
        "total_amount": row.get("total_amount"),
        "read_only": True,
        "invoice_sent": False,
    }


async def recheck_payment_batch_read_only(
    db,
    *,
    orders_user_id: str,
    order_numbers: list[str],
    fetch_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Sequentially scan a bounded list to respect Salla rate limits."""
    results = []
    for order_number in order_numbers:
        results.append(await recheck_payment_read_only(
            db,
            orders_user_id=orders_user_id,
            order_number=order_number,
            fetch_fn=fetch_fn,
        ))
    counts: dict[str, int] = {}
    for row in results:
        outcome = str(row.get("outcome") or "error")
        counts[outcome] = counts.get(outcome, 0) + 1
    return {
        "ok": True,
        "read_only": True,
        "invoice_sent_count": 0,
        "total": len(results),
        "counts": counts,
        "results": results,
    }
