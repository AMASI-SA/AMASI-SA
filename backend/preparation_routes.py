"""FastAPI router for the "تجهيز المنتجات" (Product Preparation) feature.

Endpoints (all mounted under /api/preparation):
  POST   /upload                  — upload Salla orders PDF, parse, cache
  GET    /preview/{upload_id}     — return parsed preview (groups + lines)
  GET    /image/{upload_id}/{i}   — stream a single product image (preview)
  POST   /generate/{upload_id}    — render final PDF; persist exported order #s
  GET    /excluded/{upload_id}    — list orders skipped because already exported
  GET    /export-log/stats        — counts: total exported, last_exported_at
  DELETE /export-log              — clear the dedup log (with confirmation)

State store: a `preparation_uploads` collection holds the parsed structure
+ image bytes (base64) keyed by user. We keep entries for 24h then expire
via a TTL index (created at startup).

Dedup store: `exported_orders` { user_id, order_number, exported_at }. A
unique compound index prevents the same order being re-exported by the
same user — and the only path to remove rows is the DELETE endpoint.
"""
from __future__ import annotations

import base64
import io
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from preparation_pdf import (
    ProductLine,
    parse_salla_orders_pdf,
    group_and_sort_by_product,
    flatten_sorted,
    generate_preparation_pdf,
)

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MB upload cap
UPLOAD_TTL_HOURS = 24


class ClearExportLogRequest(BaseModel):
    confirm: bool = Field(
        default=False,
        description="Must be true — the UI shows an explicit confirmation dialog before sending.",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _line_to_preview(line: ProductLine, idx: int, *, image_source: Optional[str] = None) -> dict:
    """Public preview shape — strips raw image bytes."""
    return {
        "idx": idx,
        "order_number": line.order_number,
        "order_date": line.order_date,
        "product_name": line.product_name,
        "customer_name": line.customer_name,
        "note": line.note,
        "quantity": line.quantity,
        "total_products_in_order": line.total_products_in_order,
        "has_image": bool(line.image_bytes),
        "image_source": image_source,
        "shipping_company": line.shipping_company,
    }


def _line_to_storage(line: ProductLine, idx: int) -> dict:
    """How a single line is persisted in `preparation_uploads.lines[]`."""
    return {
        "idx": idx,
        "order_number": line.order_number,
        "order_date": line.order_date,
        "product_name": line.product_name,
        "customer_name": line.customer_name,
        "note": line.note,
        "quantity": line.quantity,
        "total_products_in_order": line.total_products_in_order,
        "image_b64": (
            base64.b64encode(line.image_bytes).decode("ascii")
            if line.image_bytes else None
        ),
        "image_mime": line.image_mime,
        "shipping_company": line.shipping_company,
    }


def _line_from_storage(d: dict) -> ProductLine:
    img = base64.b64decode(d["image_b64"]) if d.get("image_b64") else None
    return ProductLine(
        order_number=d["order_number"],
        order_date=d.get("order_date"),
        product_name=d.get("product_name"),
        customer_name=d.get("customer_name"),
        note=d.get("note"),
        quantity=int(d.get("quantity") or 1),
        total_products_in_order=int(d.get("total_products_in_order") or 1),
        image_bytes=img,
        image_mime=d.get("image_mime"),
        shipping_company=d.get("shipping_company"),
    )


async def _enrich_lines_with_shipping(db, user_id: str, lines: list[ProductLine]) -> None:
    """For each parsed line, look up the order in unified_orders (if present)
    and copy the shipping_company. Salla's PDF doesn't carry carrier info,
    so we cross-reference our existing order store (Make/Excel imports)."""
    order_nums = sorted({ln.order_number for ln in lines})
    if not order_nums:
        return
    cursor = db.unified_orders.find(
        {"user_id": user_id, "order_number": {"$in": order_nums}},
        {"_id": 0, "order_number": 1, "shipping_company": 1, "carrier": 1, "shipping_method": 1},
    )
    docs = await cursor.to_list(length=len(order_nums) * 2)
    by_num = {}
    for d in docs:
        nm = (d.get("shipping_company") or d.get("carrier") or d.get("shipping_method") or "").strip()
        if nm:
            by_num[str(d["order_number"])] = nm
    for ln in lines:
        if ln.shipping_company:
            continue
        ln.shipping_company = by_num.get(str(ln.order_number)) or None


async def _enrich_lines_with_catalog_images(db, user_id: str, lines: list[ProductLine]) -> None:
    """Fallback chain (per the spec):
        1. image from the PDF (already on the line)
        2. catalogue image matched by product name
        3. None → renderer uses a placeholder.
    """
    needs_image = [ln for ln in lines if not ln.image_bytes and ln.product_name]
    if not needs_image:
        return
    docs = await db.product_costs.find(
        {"user_id": user_id, "is_active": True},
        {"_id": 0, "product_name": 1, "image_url": 1},
    ).to_list(length=2000)
    name_to_url = {(d.get("product_name") or "").strip(): d.get("image_url") for d in docs if d.get("image_url")}

    def norm(s: str) -> str:
        return " ".join((s or "").lower().split())
    norm_map = {norm(k): v for k, v in name_to_url.items()}
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as http:
        for ln in needs_image:
            url = norm_map.get(norm(ln.product_name))
            if not url:
                continue
            try:
                r = await http.get(url)
                if r.status_code == 200 and r.content:
                    ln.image_bytes = r.content
                    ln.image_mime = r.headers.get("content-type") or "image/jpeg"
            except Exception:
                continue


def _build_router(db) -> APIRouter:
    from auth import get_current_user_from_db

    router = APIRouter(prefix="/preparation", tags=["preparation"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    # ── POST /upload — parse + cache ───────────────────────────────────
    @router.post("/upload")
    async def upload_orders_pdf(
        file: UploadFile = File(...),
        user: dict = Depends(current_user),
    ):
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="الملف يجب أن يكون PDF")
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="الملف فارغ")
        if len(data) > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail=f"حجم الملف يتجاوز {MAX_PDF_BYTES // 1024 // 1024}MB")

        try:
            lines = parse_salla_orders_pdf(data)
        except Exception as e:
            logger.exception("PDF parse failed")
            raise HTTPException(status_code=400, detail=f"تعذّر قراءة الملف: {e}")
        if not lines:
            raise HTTPException(status_code=400, detail="لم يتم العثور على أي طلبات داخل الملف.")

        # Enrich with shipping_company from unified_orders + catalog images.
        uid = user["id"]
        await _enrich_lines_with_shipping(db, uid, lines)
        await _enrich_lines_with_catalog_images(db, uid, lines)

        # Identify already-exported orders for this user.
        order_nums = sorted({ln.order_number for ln in lines})
        already = await db.exported_orders.find(
            {"user_id": uid, "order_number": {"$in": order_nums}},
            {"_id": 0, "order_number": 1, "exported_at": 1, "upload_id": 1},
        ).to_list(length=len(order_nums))
        already_set = {d["order_number"] for d in already}

        # Sort + group (ignores excluded — they'll be filtered out at generate time)
        kept_lines = [ln for ln in lines if ln.order_number not in already_set]
        groups = group_and_sort_by_product(kept_lines)

        # Persist the upload (full lines + groupings) into preparation_uploads.
        upload_id = uuid.uuid4().hex
        doc = {
            "user_id": uid,
            "upload_id": upload_id,
            "filename": file.filename,
            "uploaded_at": _now_iso(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=UPLOAD_TTL_HOURS)).isoformat(),
            "expires_at_dt": datetime.now(timezone.utc) + timedelta(hours=UPLOAD_TTL_HOURS),  # for TTL index
            "lines": [_line_to_storage(ln, i) for i, ln in enumerate(lines)],
            "groups_order": [g["product_name"] for g in groups],
            "excluded_orders": sorted(list(already_set)),
        }
        await db.preparation_uploads.insert_one(doc)

        return {
            "upload_id": upload_id,
            "filename": file.filename,
            "total_orders": len(order_nums),
            "total_product_lines": len(lines),
            "kept_lines": len(kept_lines),
            "excluded_orders_count": len(already_set),
            "excluded_orders": sorted(list(already_set)),
            "groups": [
                {"product_name": g["product_name"],
                 "count": g["count"],
                 "preview_lines": [_line_to_preview(ln, _idx_in_all(lines, ln)) for ln in g["lines"]]}
                for g in groups
            ],
        }

    def _idx_in_all(all_lines: list[ProductLine], target: ProductLine) -> int:
        """Stable index of `target` within the original parsed list — used
        so the frontend can request the right thumbnail URL."""
        for i, ln in enumerate(all_lines):
            if ln is target:
                return i
        return -1

    # ── GET /preview/{upload_id} ──────────────────────────────────────
    @router.get("/preview/{upload_id}")
    async def get_preview(upload_id: str, user: dict = Depends(current_user)):
        doc = await db.preparation_uploads.find_one(
            {"user_id": user["id"], "upload_id": upload_id}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="المعاينة غير موجودة أو انتهت صلاحيتها")
        # Re-hydrate ProductLines for grouping + remember the per-idx
        # image_source so we can surface "صورة مخصّصة" in the UI.
        stored = doc.get("lines", [])
        lines = [_line_from_storage(d) for d in stored]
        source_by_idx = {i: (d.get("image_source") or None) for i, d in enumerate(stored)}
        # Map back from object identity → storage idx (in case a single ln
        # appears in two groups, identity still works).
        idx_by_id = {id(ln): i for i, ln in enumerate(lines)}
        excluded = set(doc.get("excluded_orders") or [])
        kept = [ln for ln in lines if ln.order_number not in excluded]
        groups = group_and_sort_by_product(kept)
        return {
            "upload_id": upload_id,
            "filename": doc.get("filename"),
            "uploaded_at": doc.get("uploaded_at"),
            "total_orders": len({ln.order_number for ln in lines}),
            "total_product_lines": len(lines),
            "kept_lines": len(kept),
            "excluded_orders": sorted(list(excluded)),
            "excluded_orders_count": len(excluded),
            "groups": [
                {"product_name": g["product_name"],
                 "count": g["count"],
                 "preview_lines": [
                     _line_to_preview(
                         ln, idx_by_id.get(id(ln), -1),
                         image_source=source_by_idx.get(idx_by_id.get(id(ln), -1)),
                     )
                     for ln in g["lines"]
                 ]}
                for g in groups
            ],
        }

    # ── PUT /image/{upload_id}/{idx} — user-uploaded product image ────
    # Lets the merchant supply a custom image for a product when the PDF
    # did not include one (and the catalogue fallback also missed). By
    # default the new image is applied to ALL lines that share the same
    # product_name (which is what users almost always want — it's a
    # "product" image, not an "order" image). Pass scope=line to override.
    @router.put("/image/{upload_id}/{idx}")
    async def upload_product_image(
        upload_id: str,
        idx: int,
        file: UploadFile = File(...),
        scope: str = "product",
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        # 1) Validate input
        if not (file.content_type or "").startswith("image/"):
            raise HTTPException(status_code=400, detail="يجب رفع ملف صورة (PNG/JPG/WEBP)")
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="الملف فارغ")
        if len(raw) > 8 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="حجم الصورة يتجاوز 8MB")

        # 2) Validate + resize via Pillow. We cap the longest edge at 800px
        #    so we don't bloat MongoDB docs; the printable PDF only needs
        #    ~300×300. Strip metadata and re-encode as JPEG (smaller).
        try:
            from PIL import Image
            import io as _io
            im = Image.open(_io.BytesIO(raw))
            im.load()
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            max_side = 800
            if max(im.width, im.height) > max_side:
                scale = max_side / max(im.width, im.height)
                im = im.resize(
                    (int(im.width * scale), int(im.height * scale)),
                    Image.LANCZOS,
                )
            out = _io.BytesIO()
            im.save(out, format="JPEG", quality=85, optimize=True)
            normalized = out.getvalue()
            mime = "image/jpeg"
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"تعذّر قراءة الصورة: {e}")

        # 3) Locate the upload + the line, find which sibling lines should
        #    also get the image (same product_name → scope=product).
        doc = await db.preparation_uploads.find_one(
            {"user_id": uid, "upload_id": upload_id},
            {"_id": 0, "lines": 1},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="المعاينة غير موجودة أو انتهت صلاحيتها")
        stored = doc.get("lines") or []
        if idx < 0 or idx >= len(stored):
            raise HTTPException(status_code=404, detail="index out of range")

        target_name = (stored[idx].get("product_name") or "").strip()
        if scope == "product" and target_name:
            # Apply to all lines whose product_name matches (case+space normalized)
            tgt_norm = " ".join(target_name.lower().split())
            indices = [
                i for i, ln in enumerate(stored)
                if " ".join((ln.get("product_name") or "").lower().split()) == tgt_norm
            ]
        else:
            indices = [idx]

        b64 = base64.b64encode(normalized).decode("ascii")
        # 4) Persist the new image on every matching line. We update the
        #    nested array via positional-filter so we don't have to rewrite
        #    the whole doc each call.
        updates = {}
        for i in indices:
            updates[f"lines.{i}.image_b64"] = b64
            updates[f"lines.{i}.image_mime"] = mime
            updates[f"lines.{i}.image_source"] = "user_upload"
        await db.preparation_uploads.update_one(
            {"user_id": uid, "upload_id": upload_id},
            {"$set": updates},
        )
        return {
            "ok": True,
            "applied_to_indices": indices,
            "applied_count": len(indices),
            "product_name": target_name or None,
            "scope": "product" if (scope == "product" and target_name) else "line",
            "bytes": len(normalized),
        }

    # ── GET /image/{upload_id}/{idx} — stream thumbnail ──────────────
    @router.get("/image/{upload_id}/{idx}")
    async def get_image(upload_id: str, idx: int, user: dict = Depends(current_user)):
        doc = await db.preparation_uploads.find_one(
            {"user_id": user["id"], "upload_id": upload_id},
            {"_id": 0, "lines": 1},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="not found")
        lines = doc.get("lines") or []
        if idx < 0 or idx >= len(lines):
            raise HTTPException(status_code=404, detail="index out of range")
        b64 = lines[idx].get("image_b64")
        if not b64:
            # No image — return a 1px transparent PNG so the <img> tag falls
            # gracefully (frontend renders an explicit placeholder anyway).
            return Response(
                content=base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
                ),
                media_type="image/png",
                headers={"Cache-Control": "private, max-age=3600"},
            )
        return Response(
            content=base64.b64decode(b64),
            media_type=lines[idx].get("image_mime") or "image/png",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    # ── POST /generate/{upload_id} ────────────────────────────────────
    @router.post("/generate/{upload_id}")
    async def generate(upload_id: str, user: dict = Depends(current_user)):
        uid = user["id"]
        doc = await db.preparation_uploads.find_one(
            {"user_id": uid, "upload_id": upload_id}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="المعاينة غير موجودة أو انتهت صلاحيتها")

        lines = [_line_from_storage(d) for d in doc.get("lines", [])]
        # Filter out already-exported (dedup) AT generation time too — in
        # case the user exported something between /upload and /generate.
        already = await db.exported_orders.find(
            {"user_id": uid,
             "order_number": {"$in": sorted({ln.order_number for ln in lines})}},
            {"_id": 0, "order_number": 1},
        ).to_list(length=10000)
        excluded_set = {d["order_number"] for d in already}
        kept = [ln for ln in lines if ln.order_number not in excluded_set]
        if not kept:
            raise HTTPException(
                status_code=400,
                detail=(
                    "كل الطلبات في هذا الملف تم تصديرها مسبقاً. "
                    "إذا كنت ترغب بإعادة تصديرها، استخدم زر 'مسح سجل التصدير'."
                ),
            )

        groups = group_and_sort_by_product(kept)
        sorted_lines = flatten_sorted(groups)
        try:
            pdf_bytes = generate_preparation_pdf(sorted_lines)
        except Exception as e:
            logger.exception("PDF generation failed")
            raise HTTPException(status_code=500, detail=f"فشل إنشاء الملف: {e}")

        # Persist exported_orders BEFORE returning the file. Use unordered
        # bulk inserts so already-exported rows (race condition) don't fail
        # the whole batch.
        now = _now_iso()
        new_order_nums = sorted({ln.order_number for ln in kept})
        if new_order_nums:
            try:
                await db.exported_orders.insert_many(
                    [{"user_id": uid, "order_number": n,
                      "exported_at": now, "upload_id": upload_id}
                     for n in new_order_nums],
                    ordered=False,
                )
            except Exception as e:
                # Most likely a DuplicateKeyError on the unique index — safe
                # to ignore because it just means the order is already logged.
                logger.info("exported_orders insert_many had duplicates: %s", e)

        filename = f"product_preparation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Exported-Orders": str(len(new_order_nums)),
                "X-Exported-Cards": str(len(sorted_lines)),
            },
        )

    # ── GET /excluded/{upload_id} ─────────────────────────────────────
    @router.get("/excluded/{upload_id}")
    async def excluded(upload_id: str, user: dict = Depends(current_user)):
        doc = await db.preparation_uploads.find_one(
            {"user_id": user["id"], "upload_id": upload_id},
            {"_id": 0, "excluded_orders": 1, "lines": 1},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="not found")
        excluded_set = set(doc.get("excluded_orders") or [])
        # Add metadata: when was each excluded order originally exported?
        meta = await db.exported_orders.find(
            {"user_id": user["id"], "order_number": {"$in": list(excluded_set)}},
            {"_id": 0, "order_number": 1, "exported_at": 1, "upload_id": 1},
        ).to_list(length=len(excluded_set) or 1)
        meta_by = {m["order_number"]: m for m in meta}
        return {
            "excluded_orders": [
                {"order_number": n,
                 "exported_at": meta_by.get(n, {}).get("exported_at"),
                 "upload_id": meta_by.get(n, {}).get("upload_id")}
                for n in sorted(excluded_set)
            ],
            "count": len(excluded_set),
        }

    # ── GET /export-log/stats ─────────────────────────────────────────
    @router.get("/export-log/stats")
    async def export_log_stats(user: dict = Depends(current_user)):
        uid = user["id"]
        total = await db.exported_orders.count_documents({"user_id": uid})
        last = await db.exported_orders.find_one(
            {"user_id": uid},
            sort=[("exported_at", -1)],
        )
        return {
            "total_exported_orders": total,
            "last_exported_at": (last or {}).get("exported_at"),
        }

    # ── DELETE /export-log ────────────────────────────────────────────
    @router.delete("/export-log")
    async def clear_export_log(
        body: ClearExportLogRequest,
        user: dict = Depends(current_user),
    ):
        if not body.confirm:
            raise HTTPException(
                status_code=400,
                detail="يجب تأكيد العملية. يرجى الضغط على زر التأكيد في النافذة المنبثقة.",
            )
        res = await db.exported_orders.delete_many({"user_id": user["id"]})
        return {"deleted_count": res.deleted_count}

    return router


def attach_preparation_routes(parent_router: APIRouter, db) -> None:
    parent_router.include_router(_build_router(db))


async def ensure_preparation_indexes(db) -> None:
    """Create indexes for the two new collections.

    - `exported_orders`: unique compound (user_id, order_number) so the
      same order can't be exported twice per user.
    - `preparation_uploads`: TTL on `expires_at_dt` to auto-clean stale
      previews after 24h, plus unique (user_id, upload_id).
    """
    try:
        await db.exported_orders.create_index(
            [("user_id", 1), ("order_number", 1)], unique=True,
        )
        await db.exported_orders.create_index([("user_id", 1), ("exported_at", -1)])
        await db.preparation_uploads.create_index(
            [("user_id", 1), ("upload_id", 1)], unique=True,
        )
        await db.preparation_uploads.create_index(
            "expires_at_dt", expireAfterSeconds=0,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("preparation indexes setup warning: %s", exc)
