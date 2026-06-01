"""Product Cost Management — iteration 19.

Provides CRUD endpoints for a merchant-maintained catalogue of product
purchase costs (per SKU + supplier), plus integration helpers that the
Make.com webhook ingestion and dashboard summary call to compute the
TRUE net profit of each order.

Lookup strategy (per merchant requirement):
    1. Match by SKU (case-insensitive, primary key — most stable in Salla).
    2. Fall back to Salla product_id.
    3. If neither matches → the order line item is flagged in
       `missing_product_cost_lines` (list returned with each ingestion +
       persisted on the order doc so the dashboard can show
       "X منتجات بدون تكلفة").

Endpoints registered under `/api/product-costs`:
    GET    /                    paginated catalogue (search + filter)
    POST   /                    create one item
    PUT    /{id}                update fields
    DELETE /{id}                soft-delete (sets is_active=False)
    POST   /import              Excel upload (bulk insert/update)
    GET    /missing             order lines with no matching cost entry
    GET    /summary             today / month / avg / top profitable
    POST   /recompute           recompute persisted cost on every existing
                                order (used after import or bulk edits)

Collections:
    product_costs    — catalogue (one doc per (user_id, sku))
    unified_orders   — enriched per-line `cost_price` + per-order
                       `total_product_cost` + `missing_product_cost_lines`
                       (added in this module, never overwritten by other
                       writers).
"""
from __future__ import annotations

import io
import logging
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_sku(value: Any) -> str:
    """Normalise a SKU: strip, upper-case so 'neck001' == 'NECK001'."""
    return str(value or "").strip().upper()


def _norm_product_id(value: Any) -> str:
    return str(value or "").strip()


def _to_float(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── Public helper used by webhook ingestion + dashboard ────────────────────
async def compute_order_cost(
    db, user_id: str, products: list,
) -> tuple[float, list[dict], list[dict]]:
    """Look up cost for every product line in an order.

    Returns:
        total_cost   — sum of (cost_price * quantity) across matched lines, in SAR.
        items        — per-line breakdown:
                       [{sku, product_id, name, quantity, unit_cost,
                         line_cost, matched_by: 'sku'|'product_id'|None}]
        missing      — lines with no cost match:
                       [{sku, product_id, name, quantity}]
    """
    total = 0.0
    items: list[dict] = []
    missing: list[dict] = []
    if not products:
        return 0.0, items, missing

    # Bulk-fetch all costs the user might need (faster than N round-trips).
    skus = {_norm_sku(p.get("sku")) for p in products if p.get("sku")}
    pids = {_norm_product_id(p.get("product_id") or p.get("id"))
            for p in products if (p.get("product_id") or p.get("id"))}
    or_clauses: list[dict] = []
    if skus:
        or_clauses.append({"sku_normalized": {"$in": list(skus)}})
    if pids:
        or_clauses.append({"product_id": {"$in": list(pids)}})
    cost_docs: list[dict] = []
    if or_clauses:
        cost_docs = await db.product_costs.find(
            {"user_id": user_id, "is_active": True, "$or": or_clauses},
            {"_id": 0},
        ).to_list(500)
    by_sku = {_norm_sku(d.get("sku")): d for d in cost_docs if d.get("sku")}
    by_pid = {_norm_product_id(d.get("product_id")): d
              for d in cost_docs if d.get("product_id")}

    for p in products:
        qty = _to_float(p.get("quantity"), 1.0)
        sku = _norm_sku(p.get("sku"))
        pid = _norm_product_id(p.get("product_id") or p.get("id"))
        name = (p.get("name") or "").strip()
        cost_doc = None
        matched_by = None
        if sku and sku in by_sku:
            cost_doc = by_sku[sku]
            matched_by = "sku"
        elif pid and pid in by_pid:
            cost_doc = by_pid[pid]
            matched_by = "product_id"
        if cost_doc:
            unit_cost = round(_to_float(cost_doc.get("cost_price")), 2)
            line_cost = round(unit_cost * qty, 2)
            total += line_cost
            items.append({
                "sku": sku or "", "product_id": pid or "",
                "name": name, "quantity": qty,
                "unit_cost": unit_cost, "line_cost": line_cost,
                "currency": cost_doc.get("currency") or "SAR",
                "supplier_name": cost_doc.get("supplier_name") or "",
                "matched_by": matched_by,
            })
        else:
            items.append({
                "sku": sku or "", "product_id": pid or "",
                "name": name, "quantity": qty,
                "unit_cost": 0.0, "line_cost": 0.0,
                "matched_by": None,
            })
            missing.append({
                "sku": sku or "", "product_id": pid or "",
                "name": name, "quantity": qty,
            })
    return round(total, 2), items, missing


async def attach_cost_to_order_doc(db, user_id: str, order_doc: dict) -> dict:
    """Enrich an order doc with `cost_items`, `total_product_cost`, and
    `missing_product_cost_lines` (idempotent — safe to call on every
    upsert). Returns a $set patch dict so the caller can merge it into
    its update query.
    """
    products = order_doc.get("products") or []
    total, items, missing = await compute_order_cost(db, user_id, products)
    return {
        "total_product_cost": total,
        "cost_items": items,
        "missing_product_cost_lines": missing,
        "cost_computed_at": _now_iso(),
    }


# ── Pydantic models ────────────────────────────────────────────────────────
class ProductCostIn(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    product_id: Optional[str] = ""
    product_name: str = Field(min_length=1, max_length=200)
    supplier_name: Optional[str] = ""
    cost_price: float = Field(ge=0)
    currency: str = Field(default="SAR", max_length=8)


class ProductCostUpdate(BaseModel):
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    supplier_name: Optional[str] = None
    cost_price: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = None
    is_active: Optional[bool] = None


# ── Router factory ─────────────────────────────────────────────────────────
def _build_router(db, current_user_dep) -> APIRouter:
    router = APIRouter(prefix="/product-costs", tags=["product-costs"])

    # ── List with search/filter ──────────────────────────────────────────
    @router.get("/")
    async def list_costs(
        search: Optional[str] = Query(None, description="Match name/sku/supplier"),
        is_active: Optional[bool] = Query(None),
        supplier: Optional[str] = Query(None),
        limit: int = Query(200, ge=1, le=1000),
        skip: int = Query(0, ge=0),
        user: dict = Depends(current_user_dep),
    ):
        q: dict = {"user_id": user["id"]}
        if is_active is not None:
            q["is_active"] = is_active
        if supplier:
            q["supplier_name"] = supplier
        if search:
            rx = re.escape(search.strip())
            q["$or"] = [
                {"product_name": {"$regex": rx, "$options": "i"}},
                {"sku": {"$regex": rx, "$options": "i"}},
                {"supplier_name": {"$regex": rx, "$options": "i"}},
            ]
        total = await db.product_costs.count_documents(q)
        rows = await db.product_costs.find(q, {"_id": 0}).sort(
            "updated_at", -1,
        ).skip(skip).limit(limit).to_list(limit)
        return {"items": rows, "total": total, "limit": limit, "skip": skip}

    @router.post("/")
    async def create_cost(payload: ProductCostIn, user: dict = Depends(current_user_dep)):
        uid = user["id"]
        sku = payload.sku.strip()
        sku_norm = _norm_sku(sku)
        # Check uniqueness on sku_normalized per user
        existing = await db.product_costs.find_one(
            {"user_id": uid, "sku_normalized": sku_norm}, {"_id": 0, "id": 1, "is_active": 1},
        )
        if existing and existing.get("is_active", True):
            raise HTTPException(
                status_code=409,
                detail=f"المنتج بهذا SKU ({sku}) موجود مسبقاً. عدّل القائمة الموجودة بدلاً من إضافة جديد.",
            )
        now = _now_iso()
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "sku": sku,
            "sku_normalized": sku_norm,
            "product_id": _norm_product_id(payload.product_id),
            "product_name": payload.product_name.strip(),
            "supplier_name": (payload.supplier_name or "").strip(),
            "cost_price": round(float(payload.cost_price), 2),
            "currency": (payload.currency or "SAR").upper(),
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        if existing:
            # Re-activate the soft-deleted row instead of creating a duplicate
            await db.product_costs.update_one(
                {"user_id": uid, "sku_normalized": sku_norm},
                {"$set": {**{k: v for k, v in doc.items() if k != "id" and k != "created_at"},
                          "is_active": True}},
            )
            doc = await db.product_costs.find_one(
                {"user_id": uid, "sku_normalized": sku_norm}, {"_id": 0},
            )
            return doc
        await db.product_costs.insert_one(doc)
        # Strip the BSON _id that pymongo silently added before returning.
        doc.pop("_id", None)
        return doc

    @router.put("/{item_id}")
    async def update_cost(item_id: str, payload: ProductCostUpdate,
                          user: dict = Depends(current_user_dep)):
        patch = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
        if "product_name" in patch:
            patch["product_name"] = str(patch["product_name"]).strip()
        if "supplier_name" in patch:
            patch["supplier_name"] = str(patch["supplier_name"]).strip()
        if "currency" in patch:
            patch["currency"] = str(patch["currency"]).upper()
        if "cost_price" in patch:
            patch["cost_price"] = round(float(patch["cost_price"]), 2)
        if "product_id" in patch:
            patch["product_id"] = _norm_product_id(patch["product_id"])
        patch["updated_at"] = _now_iso()
        r = await db.product_costs.update_one(
            {"user_id": user["id"], "id": item_id}, {"$set": patch},
        )
        if r.matched_count == 0:
            raise HTTPException(status_code=404, detail="Product cost not found")
        doc = await db.product_costs.find_one(
            {"user_id": user["id"], "id": item_id}, {"_id": 0},
        )
        return doc

    @router.delete("/{item_id}")
    async def delete_cost(item_id: str, user: dict = Depends(current_user_dep)):
        r = await db.product_costs.update_one(
            {"user_id": user["id"], "id": item_id},
            {"$set": {"is_active": False, "updated_at": _now_iso()}},
        )
        if r.matched_count == 0:
            raise HTTPException(status_code=404, detail="Product cost not found")
        return {"ok": True}

    # ── Excel import ─────────────────────────────────────────────────────
    # Accept Arabic OR English column headers. Required: SKU, name, cost.
    HEADER_ALIASES = {
        "sku": {"sku", "كود المنتج", "كود", "SKU", "الرمز", "رقم المنتج"},
        "product_name": {"product_name", "name", "اسم المنتج", "الاسم", "المنتج"},
        "cost_price": {"cost", "cost_price", "التكلفة", "تكلفة الشراء", "سعر التكلفة", "سعر الشراء"},
        "supplier_name": {"supplier", "supplier_name", "المورد", "اسم المورد"},
        "product_id": {"product_id", "id", "معرف المنتج"},
        "currency": {"currency", "العملة"},
    }

    def _find_col(headers: list, target: str) -> Optional[int]:
        targets = {h.lower() for h in HEADER_ALIASES[target]}
        for i, h in enumerate(headers):
            if str(h or "").strip().lower() in targets:
                return i
        return None

    @router.post("/import")
    async def import_excel(file: UploadFile = File(...),
                           user: dict = Depends(current_user_dep)):
        if not (file.filename or "").lower().endswith((".xlsx", ".xls")):
            raise HTTPException(status_code=400,
                                detail="الملف يجب أن يكون Excel (.xlsx أو .xls)")
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise HTTPException(status_code=500,
                                detail="openpyxl missing — install it on the backend")
        content = await file.read()
        try:
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            raise HTTPException(status_code=400,
                                detail=f"تعذر قراءة الملف: {exc}")
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            raise HTTPException(status_code=400, detail="الملف فارغ")
        headers = [str(c or "").strip() for c in rows[0]]
        idx_sku = _find_col(headers, "sku")
        idx_name = _find_col(headers, "product_name")
        idx_cost = _find_col(headers, "cost_price")
        idx_supplier = _find_col(headers, "supplier_name")
        idx_product_id = _find_col(headers, "product_id")
        idx_currency = _find_col(headers, "currency")
        if idx_sku is None or idx_name is None or idx_cost is None:
            raise HTTPException(
                status_code=400,
                detail="الأعمدة المطلوبة: SKU، اسم المنتج، التكلفة. (المورد اختياري)",
            )

        uid = user["id"]
        now = _now_iso()
        created = 0
        updated = 0
        errors: list[dict] = []
        for row_num, r in enumerate(rows[1:], start=2):
            try:
                sku = str(r[idx_sku] or "").strip()
                if not sku:
                    continue
                name = str(r[idx_name] or "").strip()
                if not name:
                    errors.append({"row": row_num, "error": "اسم المنتج فارغ"})
                    continue
                cost = _to_float(r[idx_cost])
                if cost < 0:
                    errors.append({"row": row_num, "error": "التكلفة سالبة"})
                    continue
                supplier = (str(r[idx_supplier] or "").strip()
                            if idx_supplier is not None else "")
                product_id = (str(r[idx_product_id] or "").strip()
                              if idx_product_id is not None else "")
                currency = ((str(r[idx_currency] or "SAR").strip().upper() or "SAR")
                            if idx_currency is not None else "SAR")
                sku_norm = _norm_sku(sku)
                doc = {
                    "user_id": uid,
                    "sku": sku,
                    "sku_normalized": sku_norm,
                    "product_id": _norm_product_id(product_id),
                    "product_name": name,
                    "supplier_name": supplier,
                    "cost_price": round(cost, 2),
                    "currency": currency,
                    "is_active": True,
                    "updated_at": now,
                }
                res = await db.product_costs.update_one(
                    {"user_id": uid, "sku_normalized": sku_norm},
                    {"$set": doc,
                     "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": now}},
                    upsert=True,
                )
                if res.upserted_id:
                    created += 1
                else:
                    updated += 1
            except Exception as exc:
                errors.append({"row": row_num, "error": str(exc)[:200]})

        return {"created": created, "updated": updated, "errors": errors,
                "total_processed": created + updated}

    # ── Missing costs (orders with unmatched products) ───────────────────
    @router.get("/missing")
    async def missing_costs(
        days: int = Query(60, ge=1, le=365),
        user: dict = Depends(current_user_dep),
    ):
        """Aggregate every order line in the last `days` whose SKU/product_id
        has NO matching active product_costs entry. Returns counts so the
        merchant knows which products to add cost for first (top-occurring
        first)."""
        uid = user["id"]
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        # Fetch recent orders' products + their missing-lines arrays.
        cursor = db.unified_orders.find(
            {"user_id": uid, "order_date": {"$gte": cutoff}},
            {"_id": 0, "missing_product_cost_lines": 1, "products": 1, "order_number": 1},
        )
        agg: dict[str, dict] = {}
        async for o in cursor:
            lines = o.get("missing_product_cost_lines")
            if lines is None:
                # Order has not been cost-enriched yet — compute lazily.
                _, _, missing = await compute_order_cost(
                    db, uid, o.get("products") or [],
                )
                lines = missing
            for ln in (lines or []):
                key = (_norm_sku(ln.get("sku")) or
                       _norm_product_id(ln.get("product_id")) or
                       (ln.get("name") or "").strip())
                if not key:
                    continue
                cur = agg.setdefault(key, {
                    "sku": ln.get("sku") or "",
                    "product_id": ln.get("product_id") or "",
                    "name": ln.get("name") or "",
                    "occurrences": 0,
                    "total_quantity": 0.0,
                })
                cur["occurrences"] += 1
                cur["total_quantity"] += _to_float(ln.get("quantity"), 1.0)
        rows = sorted(agg.values(), key=lambda r: r["occurrences"], reverse=True)
        return {"items": rows, "count": len(rows), "window_days": days}

    # ── Summary stats ────────────────────────────────────────────────────
    @router.get("/summary")
    async def summary(user: dict = Depends(current_user_dep)):
        uid = user["id"]
        today_d = datetime.now(timezone.utc).date()
        today_str = today_d.isoformat()
        month_start = today_str[:8] + "01"
        # Today / month: sum total_product_cost on orders in that range.
        today_total = 0.0
        month_total = 0.0
        async for o in db.unified_orders.find(
            {"user_id": uid, "order_date": {"$gte": month_start, "$lte": today_str}},
            {"_id": 0, "order_date": 1, "total_product_cost": 1},
        ):
            cost = float(o.get("total_product_cost") or 0)
            if o.get("order_date") == today_str:
                today_total += cost
            month_total += cost
        # Avg per active product
        active = await db.product_costs.count_documents(
            {"user_id": uid, "is_active": True},
        )
        if active:
            agg = db.product_costs.aggregate([
                {"$match": {"user_id": uid, "is_active": True}},
                {"$group": {"_id": None, "avg": {"$avg": "$cost_price"}}},
            ])
            r = await agg.to_list(1)
            avg_cost = round(float(r[0]["avg"]) if r else 0.0, 2)
        else:
            avg_cost = 0.0
        # Top profitable products in last 30 days (by line_cost in cost_items)
        cutoff_30 = (today_d - timedelta(days=29)).isoformat()
        rev_by_sku: dict[str, dict] = {}
        async for o in db.unified_orders.find(
            {"user_id": uid, "order_date": {"$gte": cutoff_30, "$lte": today_str}},
            {"_id": 0, "cost_items": 1, "products": 1, "total_amount": 1},
        ):
            items = o.get("cost_items") or []
            for it in items:
                sku = _norm_sku(it.get("sku")) or _norm_product_id(it.get("product_id"))
                if not sku:
                    continue
                cur = rev_by_sku.setdefault(sku, {
                    "sku": it.get("sku") or "",
                    "product_id": it.get("product_id") or "",
                    "name": it.get("name") or "",
                    "qty_sold": 0.0,
                    "total_cost": 0.0,
                })
                cur["qty_sold"] += _to_float(it.get("quantity"), 1.0)
                cur["total_cost"] += _to_float(it.get("line_cost"), 0.0)
        top = sorted(rev_by_sku.values(), key=lambda r: r["total_cost"], reverse=True)[:10]
        return {
            "today_total": round(today_total, 2),
            "month_total": round(month_total, 2),
            "month_start": month_start,
            "active_products": active,
            "avg_cost": avg_cost,
            "top_products_last_30d": top,
            "currency": "SAR",
        }

    # ── Recompute (after import or bulk edits) ───────────────────────────
    @router.post("/recompute")
    async def recompute_orders(
        days: int = Query(60, ge=1, le=365),
        user: dict = Depends(current_user_dep),
    ):
        """Re-attach cost data to every order in the last `days`. Useful
        after a bulk Excel import — orders ingested BEFORE the cost was
        known will now reflect the new cost."""
        uid = user["id"]
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        updated = 0
        async for o in db.unified_orders.find(
            {"user_id": uid, "order_date": {"$gte": cutoff}},
            {"_id": 0, "order_number": 1, "products": 1},
        ):
            patch = await attach_cost_to_order_doc(db, uid, o)
            await db.unified_orders.update_one(
                {"user_id": uid, "order_number": o["order_number"]},
                {"$set": patch},
            )
            updated += 1
        return {"orders_updated": updated, "window_days": days}

    return router


def attach_product_costs_routes(parent_router, db, current_user_dep) -> None:
    parent_router.include_router(_build_router(db, current_user_dep))
