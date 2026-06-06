"""Salla → unified_orders sync engine (Phase 2).

Pulls Orders, Refunds (via Transactions), and Products from Salla and
upserts into the existing `unified_orders` collection using
`orders_db.upsert_order(source="salla_direct")` so:
    • The merge-rules already in place protect Make.com (real-time) data.
    • Excel uploads keep working unchanged.
    • The dashboard, reconciliation, accounts pages see the new source
      automatically (without any code change in those pages).

Run modes
---------
    • Manual: triggered by the UI "Sync Now" button (routes.py).
    • Scheduled: NOT enabled in Phase 2 (per user — only manual button
      for now). When enabled later, just call `run_orders_sync()` on
      a 15-minute timer.

Sync log
--------
Every invocation creates a row in `salla_sync_logs` with start/end
timestamps, source, counts (created/updated/errors), and the cursor
state (page, last order_id). The UI reads this to render a log feed.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from .service import SallaError, call_salla

# orders_db is imported at module top-level to keep `salla_direct` writes
# routed through the same merge logic Make/Excel use.
from orders_db import upsert_order


# Salla's /orders endpoint uses page-based pagination. Default per_page is
# 15; we ask for the max (50) for fewer round-trips.
ORDERS_PER_PAGE = 50
MAX_PAGES_PER_RUN = 40  # 50 * 40 = 2000 orders / run — protects against runaway pulls
PRODUCTS_PER_PAGE = 60
MAX_PRODUCT_PAGES = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Salla order → unified_orders document shape ──────────────────────────
def _str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _money(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, dict):
        # Salla often nests money as {"amount": 123.45, "currency": "SAR"}
        return float(v.get("amount") or 0)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _normalize_date(v: Any) -> Optional[str]:
    """Salla ISO with TZ → YYYY-MM-DD."""
    if not v:
        return None
    s = str(v).strip()
    if not s:
        return None
    # Salla returns dict {"date": "2024-...", "timezone": "Asia/Riyadh", ...}
    if isinstance(v, dict):
        return _normalize_date(v.get("date"))
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.split("+")[0].split("Z")[0], fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return None


def _salla_order_to_doc(salla_order: dict) -> dict:
    """Map Salla /orders payload → unified_orders document fields.

    Salla nests heavily; we extract only the fields the rest of the system
    needs. The full raw payload is kept in raw_by_source['salla_direct']
    via upsert_order(raw=...).
    """
    # Salla shapes nested objects: customer, payment_method, shipping,
    # amounts.total, status, etc. We defend against missing keys
    # because store configurations vary widely.
    customer = salla_order.get("customer") or {}
    amounts = salla_order.get("amounts") or {}
    total_obj = amounts.get("total") or {}
    shipping_obj = amounts.get("shipping_cost") or {}
    discount_obj = amounts.get("discounts") or {}
    tax_obj = amounts.get("tax") or {}
    subtotal_obj = amounts.get("sub_total") or amounts.get("subtotal") or {}

    payment_method = (
        salla_order.get("payment_method")
        or (salla_order.get("payment") or {}).get("method")
        or ""
    )
    if isinstance(payment_method, dict):
        payment_method = payment_method.get("name") or payment_method.get("code") or ""

    shipping_company = ""
    shipment = salla_order.get("shipments") or []
    if shipment and isinstance(shipment, list):
        first = shipment[0] or {}
        shipping_company = (first.get("courier") or {}).get("name") or first.get("courier_name") or ""
    if not shipping_company:
        shipping = salla_order.get("shipping") or {}
        if isinstance(shipping, dict):
            shipping_company = (shipping.get("company") or {}).get("name") or shipping.get("company_name") or ""

    status_obj = salla_order.get("status") or {}
    if isinstance(status_obj, dict):
        order_status = status_obj.get("name") or status_obj.get("customized") or ""
        order_status_slug = status_obj.get("slug") or ""
    else:
        order_status = str(status_obj)
        order_status_slug = ""

    payment_status = ""
    payment_obj = salla_order.get("payment") or {}
    if isinstance(payment_obj, dict):
        payment_status = payment_obj.get("status") or ""

    # Products
    items = salla_order.get("items") or []
    products: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        prod = it.get("product") or {}
        products.append({
            "product_id": _str(prod.get("id") or it.get("product_id")),
            "name": _str(prod.get("name") or it.get("name")),
            "sku": _str(prod.get("sku") or it.get("sku")),
            "quantity": int(it.get("quantity") or 0),
            "price": _money(it.get("amounts", {}).get("price_without_tax") or it.get("price")),
            "total": _money(it.get("amounts", {}).get("total") or it.get("total")),
            "image_url": _str(prod.get("main_image") or prod.get("image", {}).get("url") if isinstance(prod.get("image"), dict) else prod.get("image")),
        })

    order_date_raw = (salla_order.get("date") or {}).get("date") if isinstance(salla_order.get("date"), dict) else salla_order.get("date")
    order_date = _normalize_date(salla_order.get("date") or salla_order.get("created_at"))

    return {
        "order_id": _str(salla_order.get("id")),
        "order_number": _str(salla_order.get("reference_id") or salla_order.get("id")),
        "order_date": order_date,
        "order_date_raw": _str(order_date_raw),
        "order_date_inferred": False,
        "order_status": _str(order_status),
        "order_status_slug": _str(order_status_slug),
        "payment_status": _str(payment_status),
        "customer_name": _str(customer.get("full_name") or customer.get("first_name") or ""),
        "customer_mobile": _str(customer.get("mobile") or customer.get("phone") or ""),
        "payment_method": _str(payment_method),
        "shipping_company": _str(shipping_company),
        "shipping_cost": _money(shipping_obj),
        "subtotal": _money(subtotal_obj),
        "discount": _money(discount_obj),
        "tax": _money(tax_obj),
        "total_amount": _money(total_obj),
        "currency": _str(total_obj.get("currency") if isinstance(total_obj, dict) else "") or "SAR",
        "source": _str(salla_order.get("source") or "salla_direct"),
        "products": products,
    }


# ── Sync log helpers ──────────────────────────────────────────────────────
async def create_sync_log(db, user_id: str, kind: str) -> str:
    log_id = str(uuid.uuid4())
    await db.salla_sync_logs.insert_one({
        "id": log_id,
        "user_id": user_id,
        "kind": kind,            # "orders" | "products" | "refunds"
        "status": "running",
        "started_at": _now(),
        "ended_at": None,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors_count": 0,
        "pages_fetched": 0,
        "last_error": None,
        "errors_sample": [],
        "cursor": {},
    })
    return log_id


async def finish_sync_log(db, log_id: str, status: str, *, extra: Optional[dict] = None) -> None:
    payload: dict = {"status": status, "ended_at": _now()}
    if extra:
        payload.update(extra)
    await db.salla_sync_logs.update_one({"id": log_id}, {"$set": payload})


async def update_sync_log(db, log_id: str, **counters) -> None:
    inc = {k: v for k, v in counters.items() if isinstance(v, (int, float))}
    set_ = {k: v for k, v in counters.items() if not isinstance(v, (int, float))}
    update: dict = {}
    if inc:
        update["$inc"] = inc
    if set_:
        update["$set"] = set_
    if update:
        await db.salla_sync_logs.update_one({"id": log_id}, update)


# ── Public sync routines ──────────────────────────────────────────────────
async def run_orders_sync(
    db,
    user_id: str,
    *,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    updated_since_hours: Optional[int] = None,
) -> dict:
    """Pull orders from Salla and upsert into unified_orders.

    Parameters
    ----------
    from_date / to_date : optional ISO YYYY-MM-DD bounds (Salla filters by
        creation date when both are present).
    updated_since_hours : if set, ask Salla for orders updated in the last
        N hours (uses the `updated_at_gt` filter). Convenient for cron-style
        incremental syncs.
    """
    log_id = await create_sync_log(db, user_id, "orders")

    created = 0
    updated = 0
    skipped = 0
    errors_count = 0
    errors_sample: list[dict] = []
    pages_fetched = 0

    try:
        page = 1
        while page <= MAX_PAGES_PER_RUN:
            params: dict = {"page": page, "per_page": ORDERS_PER_PAGE, "expanded": "true"}
            if from_date:
                params["from_date"] = from_date
            if to_date:
                params["to_date"] = to_date
            if updated_since_hours:
                params["updated_at_gt"] = (
                    (_now() - timedelta(hours=updated_since_hours)).strftime("%Y-%m-%d %H:%M:%S")
                )

            try:
                resp = await call_salla(db, user_id, "GET", "/orders", params=params)
            except SallaError as e:
                errors_count += 1
                errors_sample.append({"page": page, "error": str(e)[:300]})
                await finish_sync_log(db, log_id, "failed", extra={
                    "created": created, "updated": updated, "skipped": skipped,
                    "errors_count": errors_count, "errors_sample": errors_sample[:20],
                    "pages_fetched": pages_fetched, "last_error": str(e)[:500],
                })
                raise

            data = resp.get("data") or []
            pages_fetched += 1
            if not data:
                break

            for raw in data:
                try:
                    doc = _salla_order_to_doc(raw)
                    if not doc.get("order_number"):
                        skipped += 1
                        continue
                    res = await upsert_order(
                        db, user_id, doc["order_number"], doc,
                        source="salla_direct", raw=raw,
                    )
                    if res.get("created"):
                        created += 1
                    else:
                        updated += 1
                except Exception as exc:  # pragma: no cover — defensive
                    errors_count += 1
                    if len(errors_sample) < 20:
                        errors_sample.append({
                            "order_id": str(raw.get("id") or "")[:60],
                            "error": str(exc)[:300],
                        })

            # Salla pagination meta (when available)
            pagination = resp.get("pagination") or {}
            total_pages = int(pagination.get("totalPages") or pagination.get("total_pages") or 0)
            if total_pages and page >= total_pages:
                break
            if len(data) < ORDERS_PER_PAGE:
                break

            page += 1
            # Be polite to Salla's rate limiter
            await asyncio.sleep(0.15)

        await finish_sync_log(db, log_id, "completed", extra={
            "created": created, "updated": updated, "skipped": skipped,
            "errors_count": errors_count, "errors_sample": errors_sample[:20],
            "pages_fetched": pages_fetched,
        })
    except Exception as exc:
        await finish_sync_log(db, log_id, "failed", extra={
            "created": created, "updated": updated, "skipped": skipped,
            "errors_count": errors_count + 1,
            "errors_sample": (errors_sample + [{"error": str(exc)[:300]}])[:20],
            "pages_fetched": pages_fetched,
            "last_error": str(exc)[:500],
        })
        raise

    return {
        "log_id": log_id,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors_count": errors_count,
        "pages_fetched": pages_fetched,
    }


async def run_products_sync(db, user_id: str) -> dict:
    """Pull products into salla_products collection (cached metadata).

    We DON'T merge products into the existing `product_costs` catalogue
    (that's user-curated); instead we keep a separate cache the UI can
    use to lookup product names/images by `product_id`.
    """
    log_id = await create_sync_log(db, user_id, "products")
    created = 0
    updated = 0
    pages_fetched = 0
    errors_count = 0
    errors_sample: list[dict] = []

    try:
        page = 1
        while page <= MAX_PRODUCT_PAGES:
            params = {"page": page, "per_page": PRODUCTS_PER_PAGE}
            try:
                resp = await call_salla(db, user_id, "GET", "/products", params=params)
            except SallaError as e:
                errors_count += 1
                errors_sample.append({"page": page, "error": str(e)[:300]})
                await finish_sync_log(db, log_id, "failed", extra={
                    "created": created, "updated": updated,
                    "errors_count": errors_count, "errors_sample": errors_sample[:20],
                    "pages_fetched": pages_fetched, "last_error": str(e)[:500],
                })
                raise

            data = resp.get("data") or []
            pages_fetched += 1
            if not data:
                break

            for prod in data:
                if not isinstance(prod, dict):
                    continue
                pid = str(prod.get("id") or "")
                if not pid:
                    continue
                doc = {
                    "user_id": user_id,
                    "product_id": pid,
                    "name": prod.get("name") or "",
                    "sku": prod.get("sku") or "",
                    "status": (prod.get("status") or "").lower(),
                    "price": _money((prod.get("price") or {}).get("amount") if isinstance(prod.get("price"), dict) else prod.get("price")),
                    "main_image": prod.get("main_image") or "",
                    "url": prod.get("url") or "",
                    "updated_at": _now(),
                }
                res = await db.salla_products.update_one(
                    {"user_id": user_id, "product_id": pid},
                    {"$set": doc, "$setOnInsert": {"created_at": _now()}},
                    upsert=True,
                )
                if res.upserted_id:
                    created += 1
                else:
                    updated += 1

            pagination = resp.get("pagination") or {}
            total_pages = int(pagination.get("totalPages") or pagination.get("total_pages") or 0)
            if total_pages and page >= total_pages:
                break
            if len(data) < PRODUCTS_PER_PAGE:
                break
            page += 1
            await asyncio.sleep(0.15)

        await finish_sync_log(db, log_id, "completed", extra={
            "created": created, "updated": updated,
            "errors_count": errors_count, "errors_sample": errors_sample[:20],
            "pages_fetched": pages_fetched,
        })
    except Exception as exc:
        await finish_sync_log(db, log_id, "failed", extra={
            "created": created, "updated": updated,
            "errors_count": errors_count + 1,
            "errors_sample": (errors_sample + [{"error": str(exc)[:300]}])[:20],
            "pages_fetched": pages_fetched,
            "last_error": str(exc)[:500],
        })
        raise

    return {
        "log_id": log_id, "created": created, "updated": updated,
        "errors_count": errors_count, "pages_fetched": pages_fetched,
    }


# ── Sources comparison report ────────────────────────────────────────────
async def compute_sources_comparison(db, user_id: str, *, from_date: Optional[str] = None,
                                     to_date: Optional[str] = None) -> dict:
    """Group unified_orders by their data sources and return counts +
    totals so the merchant can verify Salla Direct vs Make vs Excel."""
    match: dict = {"user_id": user_id}
    if from_date:
        match["order_date"] = {"$gte": from_date}
    if to_date:
        match.setdefault("order_date", {})["$lte"] = to_date

    # Aggregate by the touched-source flags
    pipeline = [
        {"$match": match},
        {"$project": {
            "order_number": 1,
            "total_amount": {"$ifNull": ["$total_amount", 0]},
            "data_source": 1,
            "has_make": {"$cond": [{"$ifNull": ["$last_make_update_at", False]}, 1, 0]},
            "has_excel": {"$cond": [{"$ifNull": ["$last_excel_import_at", False]}, 1, 0]},
            "has_salla": {"$cond": [{"$gt": [{"$type": "$raw_by_source.salla_direct"}, "missing"]}, 1, 0]},
        }},
    ]
    cursor = db.unified_orders.aggregate(pipeline)
    rows = [r async for r in cursor]

    def _bucket():
        return {"orders": 0, "amount": 0.0}

    by_source = {
        "make_only": _bucket(),
        "excel_only": _bucket(),
        "salla_only": _bucket(),
        "make_and_salla": _bucket(),
        "excel_and_salla": _bucket(),
        "make_excel_and_salla": _bucket(),
        "make_and_excel": _bucket(),
        "unknown": _bucket(),
    }
    grand = _bucket()
    in_salla_set: set[str] = set()
    in_make_set: set[str] = set()
    in_excel_set: set[str] = set()

    for r in rows:
        m, e, s = bool(r.get("has_make")), bool(r.get("has_excel")), bool(r.get("has_salla"))
        if m and e and s:
            key = "make_excel_and_salla"
        elif m and s:
            key = "make_and_salla"
        elif e and s:
            key = "excel_and_salla"
        elif m and e:
            key = "make_and_excel"
        elif m:
            key = "make_only"
        elif e:
            key = "excel_only"
        elif s:
            key = "salla_only"
        else:
            key = "unknown"
        bkt = by_source[key]
        bkt["orders"] += 1
        bkt["amount"] += float(r.get("total_amount") or 0)
        grand["orders"] += 1
        grand["amount"] += float(r.get("total_amount") or 0)
        ordn = str(r.get("order_number") or "")
        if s:
            in_salla_set.add(ordn)
        if m:
            in_make_set.add(ordn)
        if e:
            in_excel_set.add(ordn)

    # Round amounts
    for v in by_source.values():
        v["amount"] = round(v["amount"], 2)
    grand["amount"] = round(grand["amount"], 2)

    return {
        "from_date": from_date,
        "to_date": to_date,
        "totals": grand,
        "by_combination": by_source,
        "per_source_totals": {
            "make": {
                "orders": sum(v["orders"] for k, v in by_source.items() if "make" in k),
                "amount": round(sum(v["amount"] for k, v in by_source.items() if "make" in k), 2),
            },
            "excel": {
                "orders": sum(v["orders"] for k, v in by_source.items() if "excel" in k),
                "amount": round(sum(v["amount"] for k, v in by_source.items() if "excel" in k), 2),
            },
            "salla_direct": {
                "orders": sum(v["orders"] for k, v in by_source.items() if "salla" in k),
                "amount": round(sum(v["amount"] for k, v in by_source.items() if "salla" in k), 2),
            },
        },
        # Set-diff helpers: orders Salla has but Make/Excel don't
        "missing_from_make": sorted(in_salla_set - in_make_set)[:50],
        "missing_from_salla": sorted((in_make_set | in_excel_set) - in_salla_set)[:50],
        "missing_from_make_count": len(in_salla_set - in_make_set),
        "missing_from_salla_count": len((in_make_set | in_excel_set) - in_salla_set),
    }


async def ensure_sync_indexes(db) -> None:
    await db.salla_sync_logs.create_index([("user_id", 1), ("started_at", -1)])
    await db.salla_products.create_index(
        [("user_id", 1), ("product_id", 1)], unique=True
    )
