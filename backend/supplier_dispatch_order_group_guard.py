"""Preview the exact physical pieces requested for a supplier dispatch.

The quantity chosen by an employee is authoritative, even when another piece
of the same product belongs to the same customer order.
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
) -> dict[str, Any]:
    requested_count = len(planned)
    return {
        "ok": True,
        "expansion_required": False,
        "requested_piece_count": requested_count,
        "suggested_piece_count": requested_count,
        "affected_order_numbers": [],
        "files": _exact_file_selections(planned),
        "message": "سيُرسل فقط عدد القطع المحددة.",
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
    return planned, planned, _preview_result(planned)


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
    "make_supplier_dispatch_order_group_preview_router",
]
