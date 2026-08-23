"""Keep same-order pieces of the same product together in supplier dispatches.

The native app allows an employee to select a partial quantity of a product for
one supplier.  A quantity boundary must never split a customer's multiple
pieces of that same product across supplier files.  This module provides a
read-only preview used by the app and installs a server-side planner guard so
bypassing the preview still cannot silently split an order inside a source
preparation file.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import preparation_supplier_dispatch as _dispatch


class SupplierDispatchOrderGroupPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[_dispatch.SupplierDispatchFileSelection] = Field(
        min_length=1,
        max_length=_dispatch.MAX_SELECTIONS,
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _product_identity(piece: dict[str, Any]) -> str:
    product_id = _text(piece.get("product_id"))
    if product_id:
        return f"id:{product_id}"
    sku = _text(piece.get("sku")).casefold()
    if sku:
        return f"sku:{sku}"
    name = " ".join(_text(piece.get("product_name")).casefold().split())
    return f"name:{name}" if name else f"piece:{_text(piece.get('piece_id'))}"


def _order_identity(piece: dict[str, Any]) -> str:
    order_number = _text(piece.get("order_number"))
    if order_number:
        return order_number
    # Missing order identity must never group unrelated pieces together.
    return f"piece:{_text(piece.get('piece_id') or piece.get('id'))}"


def _piece_identity(piece: dict[str, Any]) -> str:
    return _text(piece.get("piece_id") or piece.get("id"))


def expand_same_order_product_closure(
    candidates: list[dict[str, Any]],
    planned: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add every available same-order/same-product peer of selected pieces.

    The closure is limited to the candidate set supplied by the caller, which is
    already tenant/employee/status/file scoped.  Already dispatched or stopped
    pieces therefore cannot be pulled back into a new supplier file.
    """
    selected_keys = {
        (_order_identity(piece), _product_identity(piece))
        for piece in planned
    }
    by_id: dict[str, dict[str, Any]] = {}
    for piece in planned:
        identity = _piece_identity(piece)
        if identity:
            by_id[identity] = piece
    for piece in candidates:
        if (_order_identity(piece), _product_identity(piece)) not in selected_keys:
            continue
        identity = _piece_identity(piece)
        if identity and identity not in by_id:
            by_id[identity] = piece
    return sorted(by_id.values(), key=_dispatch._piece_sort_key)


def _added_order_numbers(
    planned: list[dict[str, Any]],
    expanded: list[dict[str, Any]],
) -> list[str]:
    planned_ids = {_piece_identity(piece) for piece in planned}
    return sorted({
        _text(piece.get("order_number"))
        for piece in expanded
        if _piece_identity(piece) not in planned_ids
        and _text(piece.get("order_number"))
    })


def _exact_file_selections(
    pieces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for piece in sorted(pieces, key=_dispatch._piece_sort_key):
        file_number = _text(piece.get("file_number"))
        selector = _dispatch._piece_selector_group_key(piece)
        if file_number and selector:
            by_file[file_number].append({"group_key": selector, "quantity": 1})
    return [
        {"file_number": file_number, "selections": rows}
        for file_number, rows in by_file.items()
    ]


_ORIGINAL_PLAN = _dispatch.plan_piece_selections
_GUARD_INSTALLED = False


def install_supplier_dispatch_order_group_guard() -> None:
    """Make the canonical dispatch planner close selections over order groups."""
    global _GUARD_INSTALLED
    if _GUARD_INSTALLED:
        return

    def guarded_plan(
        pieces: list[dict[str, Any]],
        selections: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        planned = _ORIGINAL_PLAN(pieces, selections)
        expanded = expand_same_order_product_closure(pieces, planned)
        if len(expanded) > _dispatch.MAX_SELECTED_PIECES:
            raise ValueError("supplier_dispatch_order_group_expansion_limit_exceeded")
        return expanded

    _dispatch.plan_piece_selections = guarded_plan
    _GUARD_INSTALLED = True


def make_supplier_dispatch_order_group_preview_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/supplier-dispatch-v1",
        tags=["Preparation Supplier Dispatch"],
    )

    @router.post("/order-group-preview")
    async def order_group_preview(
        payload: SupplierDispatchOrderGroupPreviewRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        worker = await _dispatch._require_preparation_worker(
            db,
            user,
            permission="preparation.assigned.work",
        )
        user_id = _dispatch._merchant_user_id(worker)
        employee_id = _dispatch._actor_id(worker)
        file_numbers = [_text(row.file_number) for row in payload.files]

        candidates = await db[_dispatch.PIECES].find(
            {
                "user_id": user_id,
                "file_number": {"$in": file_numbers},
                "responsible_employee_id": employee_id,
                "experiment_archived_at": None,
                "status": {"$in": [
                    _dispatch.PIECE_STATUS_ASSIGNED,
                    _dispatch.PIECE_STATUS_IN_PROGRESS,
                ]},
                "$or": [
                    {"supplier_dispatch_status": {"$exists": False}},
                    {"supplier_dispatch_status": None},
                    {"supplier_dispatch_status": ""},
                    {"supplier_dispatch_status": _dispatch.DISPATCH_STATUS_PARTIAL},
                ],
            },
            {"_id": 0},
        ).to_list(50000)
        candidates = [
            piece for piece in candidates
            if _dispatch.piece_is_available_for_supplier_dispatch(piece)
        ]
        by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for piece in candidates:
            by_file[_text(piece.get("file_number"))].append(piece)

        planned: list[dict[str, Any]] = []
        try:
            for file_request in payload.files:
                file_number = _text(file_request.file_number)
                planned.extend(_ORIGINAL_PLAN(
                    by_file[file_number],
                    [row.model_dump() for row in file_request.selections],
                ))
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": str(exc),
                    "message": "الكمية المختارة لم تعد متاحة؛ حدّث الملف وأعد المحاولة.",
                },
            ) from exc

        expanded = expand_same_order_product_closure(candidates, planned)
        if len(expanded) > _dispatch.MAX_SELECTED_PIECES:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_dispatch_order_group_expansion_limit_exceeded"},
            )
        requested_count = len(planned)
        suggested_count = len(expanded)
        affected_orders = _added_order_numbers(planned, expanded)
        return {
            "ok": True,
            "expansion_required": suggested_count > requested_count,
            "requested_piece_count": requested_count,
            "suggested_piece_count": suggested_count,
            "affected_order_numbers": affected_orders,
            "files": _exact_file_selections(expanded),
            "message": (
                f"للحفاظ على طلب العميل كاملًا، هذه الدفعة ستصبح {suggested_count} قطعة."
                if suggested_count > requested_count
                else "الكمية المحددة لا تقسّم قطع أي طلب."
            ),
            "read_only": True,
            "mezan_only": True,
        }

    return router


__all__ = [
    "SupplierDispatchOrderGroupPreviewRequest",
    "expand_same_order_product_closure",
    "install_supplier_dispatch_order_group_guard",
    "make_supplier_dispatch_order_group_preview_router",
]
