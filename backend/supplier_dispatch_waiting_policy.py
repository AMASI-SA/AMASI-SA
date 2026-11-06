"""Current Order Engine eligibility for the waiting queue, never stored on a piece.

Order reads are tenant-scoped batches. Operational pieces and supplier history are
not deleted or reassigned when external order status changes.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from order_engine import service as order_service
from order_engine.repository import MongoOrderRepository, _STATUS_PATTERNS, _NATIVE_PAYMENT_FIELDS
from review_local_policy import load_review_operational_context, local_review_stage_eligible, execution_status_allowed

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


def order_waiting_fields(order: Any, local_workflow: dict[str, Any] | None = None) -> dict[str, Any]:
    status = _text(getattr(order, "status", None))
    native = _text(getattr(order, "status_native", None))
    # Order Engine's native field applies current/custom status precedence.
    # Legacy contracts retain their external status rule. Pending can become
    # actionable only with independently loaded, completed local review proof.
    effective = " ".join((native or status).replace("_", " ").casefold().split())
    eligible = (
        local_review_stage_eligible(order, local_workflow)
        if local_workflow is not None else effective in _WAITING_REVIEW_ELIGIBLE
    )
    return {
        "order_status": status or None,
        "order_status_native": native or None,
        # Current native status already resolves Order Engine source/custom
        # precedence. Reuse its canonical completed/shipping status groups;
        # neither stale piece facts nor supplier progress can make it terminal.
        "preparation_terminal": bool(order is not None and any(
            re.fullmatch(_STATUS_PATTERNS[group], effective, re.IGNORECASE)
            for group in ("completed", "shipping")
        )),
        "waiting_review_eligible": bool(
            order is not None and eligible
        ),
    }


async def annotate_waiting_pieces(
    db: Any, *, user_id: str, pieces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    numbers = sorted({_text(p.get("order_number")) for p in pieces} - {""})
    orders = await order_service.get_orders(
        MongoOrderRepository(db), user_id=user_id, order_numbers=numbers,
    ) if numbers else {}
    local_workflows, blocked = await load_review_operational_context(db, user_id=user_id, order_numbers=numbers)
    # Copies only: a persisted piece/status snapshot can never grant eligibility.
    annotated = []
    for piece in pieces:
        number = _text(piece.get("order_number"))
        fields = order_waiting_fields(orders.get(number), local_workflows.get(number))
        if number in blocked:
            fields["waiting_review_eligible"] = False
        annotated.append({**piece, **fields})
    return annotated


class _HeaderOrderRepository(MongoOrderRepository):
    """Read only the inputs to canonical status/payment eligibility.

    This private repository cannot supply product/customer detail or write
    authority. The normal detail/dispatch paths keep their complete fresh reads.
    """

    async def get_salla_orders(self, *, user_id: str, order_numbers: list[str]):
        fields = (
            "id", "reference_id", "order_number", "date", "created_at", "order_date",
            "updated_at", "date_updated", "status_slug", *_NATIVE_PAYMENT_FIELDS,
        )
        projection = {"_id": 0, **{field: 1 for field in (
            "user_id", "order_number", "order_date", "order_status", "order_status_slug",
            "salla_order_id", "g47_salla_snapshot", "payment_method", "paid_amount",
            "remaining_amount", "has_remaining_amount", "payment_collection_status",
        )}, **{"raw_by_source.salla_direct." + field: 1 for field in fields}}
        rows = self._collection.find({
            "user_id": user_id, "order_number": {"$in": order_numbers},
            "raw_by_source.salla_direct": {"$type": "object"},
        }, projection)
        result = []
        async for row in rows:
            raw = (row.get("raw_by_source") or {}).get("salla_direct") or {}
            native = order_service._provider_status_native(raw, current_status=row.get("order_status"))
            # Negative prefilter only; positive membership still requires the
            # same current-order/local-proof policy as details and dispatch.
            if not execution_status_allowed("", native):
                continue
            mapped = self._to_discovery_row(row)
            if mapped is not None:
                result.append(mapped)
        return result


async def waiting_header_order_fields(db: Any, *, user_id: str, order_numbers: list[str]) -> dict[str, dict]:
    """Fresh, request-local navigation eligibility without options or photos."""
    orders = await order_service.get_orders(
        _HeaderOrderRepository(db), user_id=user_id, order_numbers=order_numbers,
    )
    workflows, blocked = await load_review_operational_context(
        db, user_id=user_id, order_numbers=order_numbers, metadata_only=True,
    )
    result = {}
    for number in order_numbers:
        fields = order_waiting_fields(orders.get(number), workflows.get(number))
        if number in blocked:
            fields["waiting_review_eligible"] = False
        result[number] = {**fields, "created_at": orders[number].created_at if number in orders else None}
    return result


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
