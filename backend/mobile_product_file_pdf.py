"""Mobile reviewed-stage product file PDF.

The mobile reviewed-stage file uses the approved Amasi A4 card geometry but is
not a supplier handoff yet, so supplier-specific header fields are deliberately
omitted. The endpoint returns a real PDF for the phone's native file/PDF viewer.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Response

from order_review_routes import _merchant_user_id, _text
from preparation_file_registry import REGISTRY
from preparation_pdf_amasi_a4_layout import generate_amasi_product_file_pdf
from preparation_pdf_physical_piece_overlay import expand_batch_lines_to_physical_pieces
from preparation_supplier_dispatch import _is_manager, _require_preparation_worker
from reviewed_preparation_batches import BATCHES, _line_from_batch_storage


def make_mobile_product_file_pdf_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/mobile-product-file-v1", tags=["Mobile Product File PDF"])

    @router.get("/{batch_id}/pdf")
    async def pdf(batch_id: str, user: dict = Depends(current_user)) -> Response:
        worker = await _require_preparation_worker(
            db,
            user,
            permission="preparation.assigned.read",
        )
        user_id = _merchant_user_id(worker)
        normalized_batch_id = _text(batch_id)
        registry = await db[REGISTRY].find_one(
            {"user_id": user_id, "batch_id": normalized_batch_id, "status": "ready"},
            {"_id": 0},
        )
        if not registry:
            raise HTTPException(status_code=404, detail={"code": "preparation_file_not_found"})
        if (
            not _is_manager(worker)
            and _text(registry.get("responsible_employee_id")) != _text(worker.get("id"))
        ):
            raise HTTPException(status_code=403, detail={"code": "preparation_file_not_assigned_to_employee"})

        batch = await db[BATCHES].find_one(
            {"user_id": user_id, "id": normalized_batch_id, "status": "ready"},
            {"_id": 0},
        )
        if not batch:
            raise HTTPException(status_code=404, detail={"code": "preparation_batch_not_ready"})

        render_row = dict(batch)
        render_row["lines"] = expand_batch_lines_to_physical_pieces(batch)
        lines = [
            _line_from_batch_storage(row, render_row)
            for row in render_row.get("lines") or []
            if isinstance(row, dict)
        ]
        if not lines:
            raise HTTPException(status_code=409, detail={"code": "preparation_file_empty"})

        data = generate_amasi_product_file_pdf(
            lines,
            serial_start=1,
            title="ملف المنتجات",
            supplier_name="",
            responsible_employee_name="",
            file_number=_text(registry.get("file_number")),
            file_date=_text(registry.get("file_date_display") or registry.get("file_date")),
        )
        safe_number = _text(registry.get("file_number")) or normalized_batch_id
        return Response(
            content=data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="products-{safe_number}.pdf"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router


__all__ = ["make_mobile_product_file_pdf_router"]
