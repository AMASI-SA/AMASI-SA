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

    # ── Iter-158 — Unified weekly settlements view (Salla + Tamara + Tabby)
    # with month navigation + Excel export of selected entries.
    @router.get("/_overview/unified")
    async def unified_settlements_list(
        year: int,
        month: int,
        user: dict = Depends(current_user),
    ):
        import calendar
        if not (1 <= month <= 12):
            raise HTTPException(400, "month must be 1-12")
        last_day = calendar.monthrange(year, month)[1]
        from_dt = f"{year:04d}-{month:02d}-01"
        to_dt = f"{year:04d}-{month:02d}-{last_day:02d}"

        files = await db.settlement_files.find(
            {"user_id": user["id"]},
            {"_id": 0},
        ).sort("uploaded_at", -1).to_list(2000)

        rows = []
        for f in files:
            uploaded_at = f.get("uploaded_at")
            if hasattr(uploaded_at, "isoformat"):
                uploaded_at = uploaded_at.isoformat()
            uploaded_at = str(uploaded_at or "")
            settlement_date = (
                f.get("header", {}).get("settlement_date")
                or f.get("header", {}).get("transfer_date")
                or uploaded_at[:10]
            )
            sd = (settlement_date or "")[:10]
            if not (from_dt <= sd <= to_dt):
                continue
            totals = f.get("totals", {}) or {}
            rows.append({
                "file_id": f["id"],
                "provider": f.get("provider"),
                "filename": f.get("filename"),
                "invoice_number": (f.get("header") or {}).get("invoice_number"),
                "payment_method": (f.get("header") or {}).get("payment_method", "متعدد"),
                "settlement_date": sd,
                "gross": round(totals.get("gross") or 0, 2),
                "fees": round(totals.get("fees") or 0, 2),
                "vat": round(totals.get("vat") or 0, 2),
                "net_to_bank": round(totals.get("net") or 0, 2),
                "rows": f.get("rows", 0),
                "matched": f.get("matched", 0),
                "uploaded_at": uploaded_at,
            })

        rows.sort(key=lambda r: (r["settlement_date"] or "", r["uploaded_at"] or ""), reverse=True)

        provider_totals = {}
        for r in rows:
            p = r["provider"]
            t = provider_totals.setdefault(p, {"count": 0, "gross": 0, "fees": 0, "net": 0})
            t["count"] += 1
            t["gross"] += r["gross"]
            t["fees"] += r["fees"]
            t["net"] += r["net_to_bank"]
        for p in provider_totals:
            provider_totals[p] = {
                k: round(v, 2) if k != "count" else v
                for k, v in provider_totals[p].items()
            }

        return {
            "rows": rows,
            "year": year,
            "month": month,
            "provider_totals": provider_totals,
            "grand_total": {
                "count": len(rows),
                "gross": round(sum(r["gross"] for r in rows), 2),
                "fees": round(sum(r["fees"] for r in rows), 2),
                "net": round(sum(r["net_to_bank"] for r in rows), 2),
            },
        }

    @router.post("/_overview/export-excel")
    async def export_settlements_excel(
        payload: dict,
        user: dict = Depends(current_user),
    ):
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from io import BytesIO
        from fastapi.responses import StreamingResponse

        ids = payload.get("file_ids") or []
        if not ids:
            raise HTTPException(400, "اختر تسوية واحدة على الأقل")
        files = await db.settlement_files.find(
            {"user_id": user["id"], "id": {"$in": ids}},
            {"_id": 0},
        ).to_list(2000)
        if not files:
            raise HTTPException(404, "لا توجد تسويات مطابقة")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "التسويات"
        ws.sheet_view.rightToLeft = True
        headers = [
            "المزوّد", "رقم الفاتورة", "تاريخ التسوية",
            "طريقة الدفع", "إجمالي المبيعات", "العمولات",
            "VAT", "صافي التحويل", "اسم الملف",
        ]
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="065F46")
            c.alignment = Alignment(horizontal="center")
        for f in files:
            totals = f.get("totals", {}) or {}
            uploaded_at = f.get("uploaded_at")
            if hasattr(uploaded_at, "isoformat"):
                uploaded_at = uploaded_at.isoformat()
            uploaded_at = str(uploaded_at or "")
            ws.append([
                (f.get("provider") or "").upper(),
                (f.get("header") or {}).get("invoice_number") or "—",
                ((f.get("header") or {}).get("settlement_date")
                 or uploaded_at[:10]),
                (f.get("header") or {}).get("payment_method") or "متعدد",
                round(totals.get("gross") or 0, 2),
                round(totals.get("fees") or 0, 2),
                round(totals.get("vat") or 0, 2),
                round(totals.get("net") or 0, 2),
                f.get("filename"),
            ])
        # Totals row
        ws.append([
            "الإجمالي", "", "", "",
            round(sum((f.get("totals") or {}).get("gross") or 0 for f in files), 2),
            round(sum((f.get("totals") or {}).get("fees") or 0 for f in files), 2),
            round(sum((f.get("totals") or {}).get("vat") or 0 for f in files), 2),
            round(sum((f.get("totals") or {}).get("net") or 0 for f in files), 2),
            "",
        ])
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="D1FAE5")
        for col_letter in ["A", "B", "C", "D", "E", "F", "G", "H", "I"]:
            ws.column_dimensions[col_letter].width = 18

        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition":
                    'attachment; filename="settlements_export.xlsx"',
            },
        )

    api_router.include_router(router)
