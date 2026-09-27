"""Current Order Engine eligibility for the waiting queue, never stored on a piece.

Order reads are tenant-scoped batches. Operational pieces and supplier history are
not deleted or reassigned when external order status changes.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from order_engine import service as order_service
from order_engine.repository import MongoOrderRepository

_WAITING_REVIEW_ELIGIBLE = frozenset({
    # Business rule for the first "بانتظار المراجعة" stage in AMASI:
    # only orders whose CURRENT Salla status is reviewed or processing may
    # remain actionable here. The internal stage name must never be confused
    # with Salla's pre-review under_review status.
    "reviewed", "تم المراجعة", "تمت المراجعة",
    "processing", "in progress", "قيد التنفيذ", "جاري التنفيذ",
})


def _text(value: Any) -> str:
    return str(value or "").strip()


def order_waiting_fields(order: Any) -> dict[str, Any]:
    status = _text(getattr(order, "status", None))
    native = _text(getattr(order, "status_native", None))
    # Order Engine's native field applies current/custom status precedence.
    # Eligibility is intentionally limited to the two current Salla workflow
    # states approved for this AMASI stage: reviewed or processing.
    effective = " ".join((native or status).replace("_", " ").casefold().split())
    return {
        "order_status": status or None,
        "order_status_native": native or None,
        "waiting_review_eligible": bool(
            order is not None and effective in _WAITING_REVIEW_ELIGIBLE
        ),
    }


async def annotate_waiting_pieces(
    db: Any, *, user_id: str, pieces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    numbers = sorted({_text(p.get("order_number")) for p in pieces} - {""})
    orders = await order_service.get_orders(
        MongoOrderRepository(db), user_id=user_id, order_numbers=numbers,
    ) if numbers else {}
    # Copies only: a persisted piece/status snapshot can never grant eligibility.
    return [{**piece, **order_waiting_fields(orders.get(_text(piece.get("order_number"))))}
            for piece in pieces]


async def require_current_under_review(
    db: Any, *, user_id: str, pieces: list[dict[str, Any]],
) -> None:
    checked = await annotate_waiting_pieces(db, user_id=user_id, pieces=pieces)
    invalid = {
        _text(piece.get("order_number")): {
            "order_status": piece["order_status"],
            "order_status_native": piece["order_status_native"],
        }
        for piece in checked if piece["waiting_review_eligible"] is not True
    }
    if invalid:
        raise HTTPException(status_code=409, detail={
            "code": "supplier_dispatch_order_not_under_review",
            "message": "تغيرت حالة أحد الطلبات ولم يعد بانتظار المراجعة. حدّث القائمة.",
            "order_numbers": sorted(invalid),
            "current_statuses": invalid,
            "dispatch_created": False,
            "pieces_modified": 0,
            "events_created": 0,
            "pdf_created": False,
        })
