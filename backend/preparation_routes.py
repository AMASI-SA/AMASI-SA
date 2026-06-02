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

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
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


class GenerateRequest(BaseModel):
    """Body for POST /generate/{upload_id}.

    When `selected_indices` is None → print every not-yet-printed item.
    When it's a non-empty list → print only those `idx` values.
    """
    selected_indices: Optional[list[int]] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _line_to_preview(line: ProductLine, idx: int, *, image_source: Optional[str] = None,
                     already_printed: bool = False) -> dict:
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
        "item_index": line.item_index,
        "item_key": line.item_key,
        "has_image": bool(line.image_bytes),
        "image_source": image_source,
        "shipping_company": line.shipping_company,
        "already_printed": already_printed,
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
        "item_index": line.item_index,
        "item_key": line.item_key,
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
        item_index=int(d.get("item_index") or 0),
        image_bytes=img,
        image_mime=d.get("image_mime"),
        shipping_company=d.get("shipping_company"),
    )


def _norm_name(s: Optional[str]) -> str:
    """Normalize a product name for catalog matching (case + whitespace)."""
    return " ".join((s or "").lower().split())


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
    """Image priority chain (per the spec, iteration 34c):

    1. Image already on the line (extracted from the source PDF) — leave as-is.
    2. `product_image_catalog` (user-uploaded persistent catalog) — match by
       normalized name first, then by product_id, then by SKU when available.
    3. `product_costs.image_url` (store catalog imported from Salla Excel).
    4. None → renderer uses a placeholder.

    Step 2 is the **persistent** catalog created when the merchant uploads
    a custom image via PUT /image — the system remembers the image so the
    next file containing the same product picks it up automatically.
    """
    needs_image = [ln for ln in lines if not ln.image_bytes and ln.product_name]
    if not needs_image:
        return

    # ── Step 2: Product Image Catalog (persistent across uploads) ────────
    cat_docs = await db.product_image_catalog.find(
        {"user_id": user_id},
        {"_id": 0, "product_name": 1, "name_norm": 1,
         "image_b64": 1, "image_mime": 1, "product_id": 1, "sku": 1},
    ).to_list(length=5000)
    catalog_by_name: dict[str, dict] = {}
    for d in cat_docs:
        key = d.get("name_norm") or _norm_name(d.get("product_name") or "")
        if key:
            catalog_by_name[key] = d
    for ln in needs_image:
        key = _norm_name(ln.product_name)
        cat = catalog_by_name.get(key)
        if cat and cat.get("image_b64"):
            ln.image_bytes = base64.b64decode(cat["image_b64"])
            ln.image_mime = cat.get("image_mime") or "image/jpeg"

    # ── Step 3: product_costs catalog (Salla import images) ──────────────
    needs_image = [ln for ln in needs_image if not ln.image_bytes]
    if not needs_image:
        return
    docs = await db.product_costs.find(
        {"user_id": user_id, "is_active": True},
        {"_id": 0, "product_name": 1, "image_url": 1},
    ).to_list(length=2000)
    name_to_url = {
        _norm_name(d.get("product_name") or ""): d.get("image_url")
        for d in docs if d.get("image_url")
    }
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as http:
        for ln in needs_image:
            url = name_to_url.get(_norm_name(ln.product_name))
            if not url:
                continue
            try:
                r = await http.get(url)
                if r.status_code == 200 and r.content:
                    ln.image_bytes = r.content
                    ln.image_mime = r.headers.get("content-type") or "image/jpeg"
            except Exception:
                continue


async def _save_to_image_catalog(
    db, user_id: str, product_name: str, image_b64: str, image_mime: str,
    *, product_id: Optional[str] = None, sku: Optional[str] = None,
) -> None:
    """Persist a user-uploaded image into product_image_catalog so the next
    upload containing the same product picks it up automatically.

    Upserts by normalized product_name (the only key we always have);
    product_id/SKU are stored as metadata for the management UI but not
    used as match keys (would over-segment when SKU is missing)."""
    name = (product_name or "").strip()
    if not name:
        return
    now = _now_iso()
    await db.product_image_catalog.update_one(
        {"user_id": user_id, "name_norm": _norm_name(name)},
        {"$set": {
            "user_id": user_id,
            "product_name": name,
            "name_norm": _norm_name(name),
            "image_b64": image_b64,
            "image_mime": image_mime,
            "product_id": product_id or None,
            "sku": sku or None,
            "updated_at": now,
        }, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )


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

        # ── Identify already-printed ITEMS for this user (not orders!) ──
        # Per iteration 34c spec the dedup key is item-level, not order-
        # level: a single order with 3 products can have 1 printed and 2
        # remaining. We compute each line's `item_key` and check whether
        # it lives in `exported_items`.
        item_keys = [ln.item_key for ln in lines]
        already = await db.exported_items.find(
            {"user_id": uid, "item_key": {"$in": item_keys}},
            {"_id": 0, "item_key": 1, "order_number": 1,
             "product_name": 1, "exported_at": 1, "upload_id": 1},
        ).to_list(length=len(item_keys) or 1)
        already_keys = {d["item_key"] for d in already}

        # Lines that are NOT yet printed go to the active preview. The
        # already-printed items are kept in storage too (so future
        # toggling can resurrect them via clear-log) but flagged.
        kept_lines = [ln for ln in lines if ln.item_key not in already_keys]
        groups = group_and_sort_by_product(kept_lines)

        # For the "excluded" panel: list of {order_number, product_name}
        excluded_items = [
            {"order_number": d["order_number"],
             "product_name": d.get("product_name"),
             "exported_at": d.get("exported_at"),
             "item_key": d["item_key"]}
            for d in already
        ]
        excluded_order_set = sorted({d["order_number"] for d in already})

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
            "excluded_item_keys": sorted(list(already_keys)),
        }
        await db.preparation_uploads.insert_one(doc)

        return {
            "upload_id": upload_id,
            "filename": file.filename,
            "total_orders": len({ln.order_number for ln in lines}),
            "total_product_lines": len(lines),
            "kept_lines": len(kept_lines),
            "excluded_orders_count": len(excluded_order_set),
            "excluded_items_count": len(already_keys),
            "excluded_orders": excluded_order_set,
            "excluded_items": excluded_items,
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
        uid = user["id"]
        doc = await db.preparation_uploads.find_one(
            {"user_id": uid, "upload_id": upload_id}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="المعاينة غير موجودة أو انتهت صلاحيتها")
        stored = doc.get("lines", [])
        lines = [_line_from_storage(d) for d in stored]
        source_by_idx = {i: (d.get("image_source") or None) for i, d in enumerate(stored)}
        idx_by_id = {id(ln): i for i, ln in enumerate(lines)}

        # Re-check exported_items (rather than relying on the snapshot in
        # the upload doc) so it stays fresh as the user generates batches.
        item_keys = [ln.item_key for ln in lines]
        already = await db.exported_items.find(
            {"user_id": uid, "item_key": {"$in": item_keys}},
            {"_id": 0, "item_key": 1, "order_number": 1,
             "product_name": 1, "exported_at": 1},
        ).to_list(length=len(item_keys) or 1)
        already_keys = {d["item_key"] for d in already}

        kept = [ln for ln in lines if ln.item_key not in already_keys]
        groups = group_and_sort_by_product(kept)
        excluded_items = [
            {"order_number": d["order_number"],
             "product_name": d.get("product_name"),
             "exported_at": d.get("exported_at"),
             "item_key": d["item_key"]}
            for d in already
        ]
        return {
            "upload_id": upload_id,
            "filename": doc.get("filename"),
            "uploaded_at": doc.get("uploaded_at"),
            "total_orders": len({ln.order_number for ln in lines}),
            "total_product_lines": len(lines),
            "kept_lines": len(kept),
            "excluded_orders": sorted({d["order_number"] for d in already}),
            "excluded_orders_count": len({d["order_number"] for d in already}),
            "excluded_items_count": len(already_keys),
            "excluded_items": excluded_items,
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

        # 5) Persist to the Product Image Catalog so the NEXT upload that
        #    contains the same product gets this image automatically — the
        #    merchant only needs to upload an image once per product.
        catalog_saved = False
        if target_name:
            await _save_to_image_catalog(
                db, uid, target_name, b64, mime,
                product_id=(stored[idx].get("product_id") or None),
                sku=(stored[idx].get("sku") or None),
            )
            catalog_saved = True

        return {
            "ok": True,
            "applied_to_indices": indices,
            "applied_count": len(indices),
            "product_name": target_name or None,
            "scope": "product" if (scope == "product" and target_name) else "line",
            "catalog_saved": catalog_saved,
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
    # Body: optional { "selected_indices": [int, ...] } — when present,
    # only those line indices (storage idx) are printed. When omitted,
    # ALL not-yet-printed lines are printed (legacy "print everything" mode).
    @router.post("/generate/{upload_id}")
    async def generate(
        upload_id: str,
        body: GenerateRequest = Body(default_factory=GenerateRequest),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        doc = await db.preparation_uploads.find_one(
            {"user_id": uid, "upload_id": upload_id}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="المعاينة غير موجودة أو انتهت صلاحيتها")

        all_lines = [_line_from_storage(d) for d in doc.get("lines", [])]
        # ── Step 1: filter by user selection (if provided) ──────────────
        selected_idx_set: Optional[set[int]] = None
        if body and body.selected_indices is not None:
            selected_idx_set = {int(i) for i in body.selected_indices}
            if not selected_idx_set:
                raise HTTPException(
                    status_code=400,
                    detail="لم يتم اختيار أي منتج للطباعة. حدّد منتجاً واحداً على الأقل أولاً.",
                )

        # ── Step 2: drop items that are already in exported_items ───────
        # (covers race conditions between /upload and /generate)
        item_keys = [ln.item_key for ln in all_lines]
        already = await db.exported_items.find(
            {"user_id": uid, "item_key": {"$in": item_keys}},
            {"_id": 0, "item_key": 1},
        ).to_list(length=10000)
        already_keys = {d["item_key"] for d in already}

        def _eligible(i: int, ln: ProductLine) -> bool:
            if ln.item_key in already_keys:
                return False
            if selected_idx_set is not None and i not in selected_idx_set:
                return False
            return True

        kept = [ln for i, ln in enumerate(all_lines) if _eligible(i, ln)]
        if not kept:
            if selected_idx_set is not None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "المنتجات المختارة تم طباعتها مسبقاً. "
                        "اختر منتجات أخرى أو استخدم 'مسح سجل التصدير'."
                    ),
                )
            raise HTTPException(
                status_code=400,
                detail=(
                    "كل المنتجات في هذا الملف تم طباعتها مسبقاً. "
                    "إذا كنت ترغب بإعادة طباعتها، استخدم زر 'مسح سجل التصدير'."
                ),
            )

        groups = group_and_sort_by_product(kept)
        sorted_lines = flatten_sorted(groups)
        try:
            pdf_bytes = generate_preparation_pdf(sorted_lines)
        except Exception as e:
            logger.exception("PDF generation failed")
            raise HTTPException(status_code=500, detail=f"فشل إنشاء الملف: {e}")

        # ── Step 3: persist exported_items BEFORE returning the file ────
        now = _now_iso()
        rows = [{
            "user_id": uid,
            "item_key": ln.item_key,
            "order_number": ln.order_number,
            "product_name": ln.product_name,
            "customer_name": ln.customer_name,
            "item_index": ln.item_index,
            "exported_at": now,
            "upload_id": upload_id,
        } for ln in kept]
        if rows:
            try:
                await db.exported_items.insert_many(rows, ordered=False)
            except Exception as e:
                # DuplicateKey on the unique index is benign — race between
                # multiple selections of the same item.
                logger.info("exported_items insert_many duplicates: %s", e)

        # Count distinct orders touched (for the toast in the UI)
        order_count = len({ln.order_number for ln in kept})
        filename = f"product_preparation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Exported-Orders": str(order_count),
                "X-Exported-Cards": str(len(sorted_lines)),
                "X-Exported-Items": str(len(rows)),
            },
        )

    # ── GET /excluded/{upload_id} ─────────────────────────────────────
    @router.get("/excluded/{upload_id}")
    async def excluded(upload_id: str, user: dict = Depends(current_user)):
        uid = user["id"]
        doc = await db.preparation_uploads.find_one(
            {"user_id": uid, "upload_id": upload_id},
            {"_id": 0, "lines": 1},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="not found")
        item_keys = [d.get("item_key") for d in (doc.get("lines") or []) if d.get("item_key")]
        meta = await db.exported_items.find(
            {"user_id": uid, "item_key": {"$in": item_keys}},
            {"_id": 0, "item_key": 1, "order_number": 1, "product_name": 1,
             "exported_at": 1, "upload_id": 1},
        ).to_list(length=len(item_keys) or 1)
        return {
            "excluded_items": [
                {"order_number": m["order_number"],
                 "product_name": m.get("product_name"),
                 "exported_at": m.get("exported_at"),
                 "item_key": m["item_key"],
                 "upload_id": m.get("upload_id")}
                for m in sorted(meta, key=lambda x: x.get("exported_at") or "")
            ],
            "count": len(meta),
        }

    # ── GET /export-log/stats ─────────────────────────────────────────
    @router.get("/export-log/stats")
    async def export_log_stats(user: dict = Depends(current_user)):
        uid = user["id"]
        items = await db.exported_items.count_documents({"user_id": uid})
        # Distinct orders (an item dedup is finer-grained but the merchant
        # also wants to see the "orders touched" count for context).
        order_count = len(await db.exported_items.distinct("order_number", {"user_id": uid}))
        last = await db.exported_items.find_one(
            {"user_id": uid}, sort=[("exported_at", -1)],
        )
        return {
            "total_exported_items": items,
            "total_exported_orders": order_count,
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
        uid = user["id"]
        # Clear both the new item-level log AND the legacy order-level log
        # so older data doesn't keep filtering current uploads.
        r1 = await db.exported_items.delete_many({"user_id": uid})
        r2 = await db.exported_orders.delete_many({"user_id": uid})
        return {
            "deleted_count": r1.deleted_count + r2.deleted_count,
            "deleted_items": r1.deleted_count,
            "deleted_legacy_orders": r2.deleted_count,
        }

    # ════════════════════════════════════════════════════════════════
    # Product Image Catalog — persistent cross-upload image memory.
    # ════════════════════════════════════════════════════════════════
    # The catalog grows organically as the merchant uploads custom images
    # for products without PDF/store images. Once saved, the next upload
    # containing the same product picks it up automatically in
    # `_enrich_lines_with_catalog_images()`.

    @router.get("/image-catalog")
    async def list_image_catalog(user: dict = Depends(current_user)):
        uid = user["id"]
        docs = await db.product_image_catalog.find(
            {"user_id": uid},
            {"_id": 0, "product_name": 1, "name_norm": 1,
             "product_id": 1, "sku": 1,
             "created_at": 1, "updated_at": 1, "image_mime": 1},
        ).sort("updated_at", -1).to_list(length=2000)
        return {"items": docs, "count": len(docs)}

    @router.get("/image-catalog/image/{name_norm}")
    async def get_catalog_image(name_norm: str, user: dict = Depends(current_user)):
        doc = await db.product_image_catalog.find_one(
            {"user_id": user["id"], "name_norm": name_norm},
            {"_id": 0, "image_b64": 1, "image_mime": 1},
        )
        if not doc or not doc.get("image_b64"):
            raise HTTPException(status_code=404, detail="not found")
        return Response(
            content=base64.b64decode(doc["image_b64"]),
            media_type=doc.get("image_mime") or "image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @router.delete("/image-catalog/{name_norm}")
    async def delete_catalog_image(name_norm: str, user: dict = Depends(current_user)):
        res = await db.product_image_catalog.delete_one(
            {"user_id": user["id"], "name_norm": name_norm},
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="not found")
        return {"deleted_count": res.deleted_count}

    @router.put("/image-catalog/{name_norm}")
    async def upsert_catalog_image(
        name_norm: str,
        file: Optional[UploadFile] = File(None),
        product_name: Optional[str] = None,
        product_id: Optional[str] = None,
        sku: Optional[str] = None,
        user: dict = Depends(current_user),
    ):
        """Manual upsert endpoint for the catalog management UI. Allows
        the merchant to upload images proactively, without going through
        an order's preparation_upload first.

        `file` is OPTIONAL: when omitted, only the metadata (product_name,
        product_id, sku) is updated and the existing stored image is kept
        verbatim. Both `file` and `name_norm` together implicitly create
        a new row when no existing one matches.
        """
        uid = user["id"]

        # ── Metadata-only branch: no file → just update product_id/sku ──
        if file is None or not getattr(file, "filename", None):
            existing = await db.product_image_catalog.find_one(
                {"user_id": uid, "name_norm": name_norm},
                {"_id": 0, "image_b64": 1, "image_mime": 1, "product_name": 1},
            )
            if not existing:
                raise HTTPException(
                    status_code=400,
                    detail="ارفع صورة للمنتج عند الإضافة لأول مرة",
                )
            pname = (product_name or existing.get("product_name") or name_norm).strip()
            await _save_to_image_catalog(
                db, uid, pname,
                existing["image_b64"], existing.get("image_mime") or "image/jpeg",
                product_id=product_id, sku=sku,
            )
            return {
                "ok": True,
                "product_name": pname,
                "name_norm": _norm_name(pname),
                "metadata_only": True,
            }

        # ── Full upsert branch: validate + normalize image ──
        if not (file.content_type or "").startswith("image/"):
            raise HTTPException(status_code=400, detail="يجب رفع ملف صورة")
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="الملف فارغ")
        if len(raw) > 8 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="حجم الصورة يتجاوز 8MB")
        try:
            from PIL import Image
            import io as _io
            im = Image.open(_io.BytesIO(raw))
            im.load()
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            if max(im.width, im.height) > 800:
                scale = 800 / max(im.width, im.height)
                im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
            out = _io.BytesIO()
            im.save(out, format="JPEG", quality=85, optimize=True)
            normalized = out.getvalue()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"تعذّر قراءة الصورة: {e}")

        b64 = base64.b64encode(normalized).decode("ascii")
        # We accept product_name in the body OR fall back to the URL slug
        pname = (product_name or name_norm).strip()
        await _save_to_image_catalog(
            db, uid, pname, b64, "image/jpeg",
            product_id=product_id, sku=sku,
        )
        return {"ok": True, "product_name": pname, "name_norm": _norm_name(pname)}

    return router


def attach_preparation_routes(parent_router: APIRouter, db) -> None:
    parent_router.include_router(_build_router(db))


async def ensure_preparation_indexes(db) -> None:
    """Create indexes for the preparation feature collections.

    - `exported_items`: unique (user_id, item_key) — item-level dedup so
      that a single order with N products can be partially printed
      (Iteration 34c).
    - `exported_orders`: legacy unique compound — kept for backward
      compatibility with rows written before the migration; new code only
      reads `exported_items`.
    - `preparation_uploads`: TTL on `expires_at_dt` + unique (user_id, upload_id).
    - `product_image_catalog`: unique (user_id, name_norm) — Product Image
      Catalog for cross-upload persistent images.
    """
    try:
        await db.exported_items.create_index(
            [("user_id", 1), ("item_key", 1)], unique=True,
        )
        await db.exported_items.create_index([("user_id", 1), ("exported_at", -1)])
        await db.exported_items.create_index([("user_id", 1), ("order_number", 1)])
        # Legacy collection (still has unique index from initial setup)
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
        await db.product_image_catalog.create_index(
            [("user_id", 1), ("name_norm", 1)], unique=True,
        )
        await db.product_image_catalog.create_index(
            [("user_id", 1), ("updated_at", -1)],
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("preparation indexes setup warning: %s", exc)
