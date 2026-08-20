"""Approved Amasi A4 PDF for an employee-to-supplier dispatch.

The PDF is rebuilt from the dispatch's immutable piece ids and their original
reviewed batch snapshots. This preserves the approved 3x5 product-file design,
original product image/specification snapshot and permanent per-piece QR while
showing supplier + responsible employee in the header.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Response

from order_review_routes import _merchant_user_id, _text
from preparation_pdf_amasi_a4_layout import generate_amasi_product_file_pdf
from preparation_supplier_dispatch import (
    DISPATCHES,
    _is_manager,
    _require_preparation_worker,
)
from preparation_piece_operations import PIECES
from reviewed_preparation_batches import BATCHES, _line_from_batch_storage


def _line_match(piece: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    order_item_id = _text(piece.get("order_item_id"))
    group_key = _text(piece.get("group_key"))
    for row in rows:
        if not isinstance(row, dict):
            continue
        if order_item_id and _text(row.get("order_item_id")) == order_item_id:
            return row
        if group_key and _text(row.get("group_key")) == group_key:
            return row
    return None


async def build_supplier_dispatch_pdf(db: Any, *, user_id: str, dispatch: dict[str, Any]) -> bytes:
    piece_ids = [_text(value) for value in dispatch.get("piece_ids") or [] if _text(value)]
    if not piece_ids:
        raise HTTPException(status_code=409, detail={"code": "supplier_dispatch_empty"})

    pieces = await db[PIECES].find(
        {"user_id": user_id, "piece_id": {"$in": piece_ids}},
        {"_id": 0, "image_b64": 0},
    ).to_list(max(len(piece_ids), 1))
    by_piece = {_text(row.get("piece_id")): row for row in pieces}
    ordered = [by_piece[piece_id] for piece_id in piece_ids if piece_id in by_piece]
    if len(ordered) != len(piece_ids):
        raise HTTPException(status_code=409, detail={"code": "supplier_dispatch_piece_snapshot_missing"})

    batch_ids = sorted({_text(piece.get("batch_id")) for piece in ordered if _text(piece.get("batch_id"))})
    batches = await db[BATCHES].find(
        {"user_id": user_id, "id": {"$in": batch_ids}},
        {"_id": 0},
    ).to_list(max(len(batch_ids), 1))
    by_batch = {_text(row.get("id")): row for row in batches}

    lines = []
    for piece in ordered:
        batch = by_batch.get(_text(piece.get("batch_id")))
        if not batch:
            raise HTTPException(status_code=409, detail={"code": "supplier_dispatch_batch_snapshot_missing"})
        source = _line_match(piece, list(batch.get("lines") or []))
        if not source:
            raise HTTPException(status_code=409, detail={"code": "supplier_dispatch_line_snapshot_missing"})
        unit_row = deepcopy(source)
        unit_row["unit_index"] = int(piece.get("unit_index") or 1)
        unit_row["unit_indices"] = [unit_row["unit_index"]]
        unit_row["quantity"] = 1
        lines.append(_line_from_batch_storage(unit_row, batch))

    return generate_amasi_product_file_pdf(
        lines,
        serial_start=1,
        title="ملف المنتجات",
        supplier_name=_text(dispatch.get("supplier_name")),
        responsible_employee_name=_text(dispatch.get("sent_by_name")),
        file_number=_text(dispatch.get("supplier_file_number") or dispatch.get("file_number")),
        file_date=str(dispatch.get("sent_at") or dispatch.get("created_at") or ""),
    )


def make_supplier_dispatch_pdf_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/supplier-dispatch-pdf-v1", tags=["Supplier Dispatch PDF"])

    @router.get("/{dispatch_id}/pdf")
    async def pdf(dispatch_id: str, user: dict = Depends(current_user)) -> Response:
        worker = await _require_preparation_worker(db, user, permission="preparation.assigned.read")
        user_id = _merchant_user_id(worker)
        dispatch = await db[DISPATCHES].find_one(
            {"user_id": user_id, "id": _text(dispatch_id)},
            {"_id": 0},
        )
        if not dispatch:
            raise HTTPException(status_code=404, detail={"code": "supplier_dispatch_not_found"})
        if not _is_manager(worker) and _text(dispatch.get("sent_by_id")) != _text(worker.get("id")):
            raise HTTPException(status_code=403, detail={"code": "supplier_dispatch_not_owned_by_employee"})

        data = await build_supplier_dispatch_pdf(db, user_id=user_id, dispatch=dispatch)
        safe = _text(dispatch.get("supplier_file_number") or dispatch.get("id"))
        return Response(
            content=data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="supplier-{safe}.pdf"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router


__all__ = ["build_supplier_dispatch_pdf", "make_supplier_dispatch_pdf_router"]
