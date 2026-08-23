"""Keep same-order pieces of the same product together in supplier dispatches.

A quantity boundary must never split a customer's multiple pieces of the same
product across supplier files.  The native app gets a read-only preview before
sending; the server also expands the canonical dispatch request so a stale or
bypassing client cannot silently split an order.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
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
    # Never combine unrelated legacy pieces when an order number is missing.
    return f"piece:{_text(piece.get('piece_id') or piece.get('id'))}"


def _piece_identity(piece: dict[str, Any]) -> str:
    return _text(piece.get("piece_id") or piece.get("id"))


def expand_same_order_product_closure(
    candidates: list[dict[str, Any]],
    planned: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add every eligible same-order/same-product peer of selected pieces."""
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


async def _eligible_employee_candidates(
    db: Any,
    *,
    user_id: str,
    employee_id: str,
) -> list[dict[str, Any]]:
    rows = await db[_dispatch.PIECES].find(
        {
            "user_id": user_id,
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
    return [
        piece for piece in rows
        if _dispatch.piece_is_available_for_supplier_dispatch(piece)
    ]


_ORIGINAL_PLAN = _dispatch.plan_piece_selections
_ORIGINAL_ROUTER_FACTORY = _dispatch.make_preparation_supplier_dispatch_router
_GUARD_INSTALLED = False


def _requested_plan(
    candidates: list[dict[str, Any]],
    files: list[_dispatch.SupplierDispatchFileSelection],
) -> list[dict[str, Any]]:
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for piece in candidates:
        by_file[_text(piece.get("file_number"))].append(piece)
    planned: list[dict[str, Any]] = []
    for file_request in files:
        file_number = _text(file_request.file_number)
        planned.extend(_ORIGINAL_PLAN(
            by_file[file_number],
            [row.model_dump() for row in file_request.selections],
        ))
    return planned


def _preview_result(
    planned: list[dict[str, Any]],
    expanded: list[dict[str, Any]],
) -> dict[str, Any]:
    requested_count = len(planned)
    suggested_count = len(expanded)
    return {
        "ok": True,
        "expansion_required": suggested_count > requested_count,
        "requested_piece_count": requested_count,
        "suggested_piece_count": suggested_count,
        "affected_order_numbers": _added_order_numbers(planned, expanded),
        "files": _exact_file_selections(expanded),
        "message": (
            f"للحفاظ على طلب العميل كاملًا، هذه الدفعة ستصبح {suggested_count} قطعة."
            if suggested_count > requested_count
            else "الكمية المحددة لا تقسّم قطع أي طلب."
        ),
        "read_only": True,
        "mezan_only": True,
    }


async def _plan_for_worker(
    db: Any,
    *,
    worker: dict[str, Any],
    files: list[_dispatch.SupplierDispatchFileSelection],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    user_id = _dispatch._merchant_user_id(worker)
    employee_id = _dispatch._actor_id(worker)
    candidates = await _eligible_employee_candidates(
        db,
        user_id=user_id,
        employee_id=employee_id,
    )
    planned = _requested_plan(candidates, files)
    expanded = expand_same_order_product_closure(candidates, planned)
    if len(expanded) > _dispatch.MAX_SELECTED_PIECES:
        raise ValueError("supplier_dispatch_order_group_expansion_limit_exceeded")
    return planned, expanded, _preview_result(planned, expanded)


def install_supplier_dispatch_order_group_guard() -> None:
    """Install both planner and route-level fail-safe expansion exactly once."""
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

    def guarded_router_factory(db: Any, current_user: Callable[..., Any]) -> APIRouter:
        router = _ORIGINAL_ROUTER_FACTORY(db, current_user)
        for route in router.routes:
            if getattr(route, "path", "") != "/supplier-dispatch-v1/dispatches":
                continue
            original_call = route.dependant.call

            async def guarded_create_dispatch(
                payload: _dispatch.CreateSupplierDispatchRequest,
                user: dict,
                _original_call=original_call,
            ):
                user_id = _dispatch._merchant_user_id(user)
                existing = await db[_dispatch.DISPATCHES].find_one(
                    {"user_id": user_id, "client_request_id": payload.client_request_id},
                    {"_id": 1},
                )
                if existing:
                    return await _original_call(payload=payload, user=user)
                worker = await _dispatch._require_preparation_worker(
                    db,
                    user,
                    permission="preparation.assigned.work",
                )
                try:
                    _, expanded, _ = await _plan_for_worker(
                        db,
                        worker=worker,
                        files=payload.file_requests(),
                    )
                except ValueError as exc:
                    raise HTTPException(
                        status_code=409,
                        detail={"code": str(exc)},
                    ) from exc
                exact_files = [
                    _dispatch.SupplierDispatchFileSelection(**row)
                    for row in _exact_file_selections(expanded)
                ]
                expanded_payload = payload.model_copy(update={
                    "file_number": None,
                    "selections": None,
                    "files": exact_files,
                })
                return await _original_call(payload=expanded_payload, user=user)

            route.endpoint = guarded_create_dispatch
            route.dependant.call = guarded_create_dispatch
            break

        return router

    _dispatch.plan_piece_selections = guarded_plan
    _dispatch.make_preparation_supplier_dispatch_router = guarded_router_factory
    _GUARD_INSTALLED = True


def make_supplier_dispatch_order_group_preview_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/supplier-dispatch-v1",
        tags=["Preparation Supplier Dispatch"],
    )

    async def build_preview(
        payload: SupplierDispatchOrderGroupPreviewRequest,
        user: dict,
    ) -> dict[str, Any]:
        worker = await _dispatch._require_preparation_worker(
            db,
            user,
            permission="preparation.assigned.work",
        )
        try:
            _, _, result = await _plan_for_worker(
                db,
                worker=worker,
                files=payload.files,
            )
            return result
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": str(exc),
                    "message": "الكمية المختارة لم تعد متاحة؛ حدّث الملف وأعد المحاولة.",
                },
            ) from exc

    @router.post("/order-group-preview")
    async def order_group_preview_post(
        payload: SupplierDispatchOrderGroupPreviewRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        return await build_preview(payload, user)

    @router.get("/order-group-preview")
    async def order_group_preview_get(
        payload_json: str = Query(..., min_length=2, max_length=100000),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        try:
            raw = json.loads(payload_json)
            payload = SupplierDispatchOrderGroupPreviewRequest.model_validate(raw)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "supplier_dispatch_order_group_preview_invalid"},
            ) from exc
        return await build_preview(payload, user)

    return router


__all__ = [
    "SupplierDispatchOrderGroupPreviewRequest",
    "expand_same_order_product_closure",
    "install_supplier_dispatch_order_group_guard",
    "make_supplier_dispatch_order_group_preview_router",
]
