"""HTTP endpoints for /api/payment-settlements/* (Phase 80)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from auth import get_current_user_from_db

from .service import (
    coverage_analytics,
    delete_file,
    ensure_settlements_indexes,
    get_file_detail,
    import_file,
    list_files,
)


MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB — settlement files are tiny xlsx


def attach_payment_settlements_routes(api_router: APIRouter, db) -> None:
    router = APIRouter(prefix="/payment-settlements", tags=["payment-settlements"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    # ── Upload ─────────────────────────────────────────────────────────
    @router.post("/upload")
    async def upload(
        file: UploadFile = File(...),
        provider_hint: Optional[str] = Form(default=None),
        user: dict = Depends(current_user),
    ):
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="الملف فارغ.")
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="حجم الملف يتجاوز 10 ميجابايت.")
        try:
            result = await import_file(
                db, user["id"],
                filename=file.filename or "settlement.xlsx",
                content=content,
                provider_hint=(provider_hint or None),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return result

    # ── List uploaded files ────────────────────────────────────────────
    @router.get("")
    async def list_settlement_files(
        limit: int = 50,
        user: dict = Depends(current_user),
    ):
        files = await list_files(db, user["id"], limit=limit)
        return {"files": files}

    # ── File detail (incl. unmatched orders) ──────────────────────────
    @router.get("/{file_id}")
    async def file_detail(file_id: str, user: dict = Depends(current_user)):
        doc = await get_file_detail(db, user["id"], file_id)
        if not doc:
            raise HTTPException(status_code=404, detail="الملف غير موجود.")
        return doc

    # ── Delete + rollback actual_* on orders ──────────────────────────
    @router.delete("/{file_id}")
    async def remove_file(file_id: str, user: dict = Depends(current_user)):
        result = await delete_file(db, user["id"], file_id)
        if not result.get("removed"):
            raise HTTPException(status_code=404, detail="الملف غير موجود.")
        return {"ok": True, **result}

    # ── Coverage analytics (estimated vs actual) ──────────────────────
    @router.get("/_analytics/coverage")
    async def analytics(user: dict = Depends(current_user)):
        return await coverage_analytics(db, user["id"])

    api_router.include_router(router)
