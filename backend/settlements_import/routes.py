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

    # ── Iter-156 — Salla-specific analytics for the new
    # SallaSettlements page (file list + per-payment-method breakdown).
    @router.get("/_analytics/salla")
    async def salla_analytics(
        user: dict = Depends(current_user),
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ):
        files = await db.settlement_files.find(
            {"user_id": user["id"], "provider": "salla"},
            {"_id": 0},
        ).sort("uploaded_at", -1).to_list(500)

        # Aggregate per-payment-method across all entries belonging to
        # Salla files of this user.
        entries_pipeline = [
            {"$match": {"user_id": user["id"], "provider": "salla",
                        "event_type": {"$in": ["sale", "refund"]}}},
            {"$group": {
                "_id": "$actual_payment_method",
                "count": {"$sum": 1},
                "gross": {"$sum": "$actual_gross_amount"},
                "fees": {"$sum": "$actual_payment_fee"},
                "vat": {"$sum": "$actual_payment_vat"},
                "net": {"$sum": "$actual_net_amount"},
                "refund_full": {"$sum": "$actual_refund_amount"},
                "refund_partial": {"$sum": "$actual_partial_refund_amount"},
            }},
            {"$sort": {"net": -1}},
        ]
        per_method = []
        async for r in db.settlement_entries.aggregate(entries_pipeline):
            per_method.append({
                "payment_method": r["_id"] or "unknown",
                "count": r["count"],
                "gross": round(r.get("gross") or 0, 2),
                "fees": round(r.get("fees") or 0, 2),
                "vat": round(r.get("vat") or 0, 2),
                "net": round(r.get("net") or 0, 2),
                "refund_full": round(r.get("refund_full") or 0, 2),
                "refund_partial": round(r.get("refund_partial") or 0, 2),
                "effective_fee_rate": round(
                    (r["fees"] / r["gross"]) * 100 if (r.get("gross") or 0) > 0 else 0,
                    2,
                ),
            })

        totals = {
            "files": len(files),
            "gross": round(sum(m["gross"] for m in per_method), 2),
            "fees": round(sum(m["fees"] for m in per_method), 2),
            "vat": round(sum(m["vat"] for m in per_method), 2),
            "net": round(sum(m["net"] for m in per_method), 2),
            "refund_full": round(sum(m["refund_full"] for m in per_method), 2),
            "refund_partial": round(sum(m["refund_partial"] for m in per_method), 2),
        }
        return {
            "files": files,
            "per_method": per_method,
            "totals": totals,
        }

    api_router.include_router(router)
