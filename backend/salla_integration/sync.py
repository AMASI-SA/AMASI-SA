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

from pymongo.errors import DuplicateKeyError

from .service import SallaError, call_salla

# orders_db is imported at module top-level to keep `salla_direct` writes
# routed through the same merge logic Make/Excel use.
from orders_db import upsert_order

# Plan-B bridge (2026-02) — Salla Direct rows are also written to
# `integration_inbox` so Plan B Pending UI can source orders from the
# API pull without depending on Make.com webhooks. Import lazily inside
# the helper to avoid a circular import at module load time.


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

                    # Plan-B bridge — mirror the raw Salla order into
                    # `integration_inbox` so Pending UI sees Salla Direct
                    # orders even when Make.com webhooks are off.
                    # Never blocks the main sync loop: any failure here
                    # is counted as an error but the order stays committed
                    # in `unified_orders`.
                    try:
                        inbox_res = await upsert_salla_direct_to_inbox(
                            db, user_id=user_id, raw_salla_order=raw,
                        )
                        if not inbox_res.get("ok") and len(errors_sample) < 20:
                            errors_sample.append({
                                "order_number": doc.get("order_number"),
                                "inbox_reason": inbox_res.get("reason"),
                                "inbox_error": inbox_res.get("error"),
                            })
                    except Exception as inbox_exc:  # pragma: no cover
                        if len(errors_sample) < 20:
                            errors_sample.append({
                                "order_number": doc.get("order_number"),
                                "inbox_error":
                                    f"{inbox_exc.__class__.__name__}: {inbox_exc}"[:300],
                            })
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


async def resync_single_order(db, user_id: str, order_number: str) -> dict:
    """Iter-87 — Pull a single order from Salla by its reference_id
    (order_number) and re-upsert it into unified_orders. This is the
    manual "re-check" path the merchant can trigger from the Orders
    page when they suspect Make.com missed an update event.

    Iter-91 Phase 2 — additionally:
      • Snapshot total_amount + products items list BEFORE the upsert.
      • Call attach_cost_to_order_doc after upsert so COGS reflects the
        new product list immediately (Dashboard/Reports stay accurate).
      • If total_amount changed OR the items list changed, write a diff
        row to the `order_adjustments` collection so we have a paper
        trail of every Salla-side modification.

    Returns: { ok, found, created, updated, before, after, adjustment }.
    """
    order_number = str(order_number).strip()
    if not order_number:
        return {"ok": False, "found": False, "error": "missing order_number"}

    # Snapshot current state so we can show before/after on the UI +
    # detect order-value / product-list changes (Iter-91 Phase 2).
    before = await db.unified_orders.find_one(
        {"user_id": user_id, "order_number": order_number},
        {"_id": 0, "order_status": 1, "payment_status": 1,
         "total_amount": 1, "payment_method": 1, "updated_at": 1,
         "products": 1, "total_product_cost": 1},
    )

    # Salla supports keyword search on reference_id
    try:
        resp = await call_salla(
            db, user_id, "GET", "/orders",
            params={"keyword": order_number, "expanded": "true", "per_page": 10},
        )
    except SallaError as e:
        return {"ok": False, "found": False, "error": str(e),
                "needs_reauth": e.needs_reauth}

    data = resp.get("data") or []
    # Find exact match (keyword can return partials)
    raw = None
    for o in data:
        if str(o.get("reference_id") or o.get("id")) == order_number:
            raw = o
            break
    if raw is None and data:
        # Some Salla tenants only return id-based matches when reference_id
        # is searched; accept the single result if it's a unique hit.
        if len(data) == 1:
            raw = data[0]

    if raw is None:
        return {"ok": True, "found": False, "before": before,
                "error": "not_found_in_salla"}

    doc = _salla_order_to_doc(raw)
    if not doc.get("order_number"):
        return {"ok": False, "found": False, "error": "order_number missing in payload"}

    res = await upsert_order(
        db, user_id, doc["order_number"], doc,
        source="salla_direct", raw=raw,
    )

    # Iter-91 Phase 2 — recompute COGS from the (possibly mutated)
    # products[] array so total_product_cost reflects current items.
    post = await db.unified_orders.find_one(
        {"user_id": user_id, "order_number": order_number},
        {"_id": 0, "order_number": 1, "products": 1, "total_amount": 1,
         "total_product_cost": 1},
    )
    adjustment = None
    if post is not None:
        try:
            from product_costs import attach_cost_to_order_doc
            cost_patch = await attach_cost_to_order_doc(db, user_id, post)
            await db.unified_orders.update_one(
                {"user_id": user_id, "order_number": order_number},
                {"$set": cost_patch},
            )
            post["total_product_cost"] = cost_patch.get("total_product_cost")
        except Exception:
            pass  # never fail resync if COGS recompute fails

        # Compare BEFORE vs AFTER → write an `order_adjustments` row when
        # total_amount changed OR the items list changed (item added,
        # removed, qty/price modified).
        adjustment = await _record_order_adjustment(
            db, user_id, order_number, before, post, reason="resync"
        )

    # Refresh snapshot
    after = await db.unified_orders.find_one(
        {"user_id": user_id, "order_number": order_number},
        {"_id": 0, "raw_by_source": 0, "raw_by_user": 0, "products": 0},
    )
    return {
        "ok": True,
        "found": True,
        "created": bool(res.get("created")),
        "updated": not bool(res.get("created")),
        "before": before,
        "after": after,
        "adjustment": adjustment,
    }


def _summarise_items(products) -> list[dict]:
    """Compact representation of an order's items used for diffing.

    Each entry: { key, name, sku, product_id, quantity, price }.
    `key` = first non-empty of (sku, product_id, name) — used to align
    rows between two snapshots.
    """
    out: list[dict] = []
    for p in (products or []):
        sku = str(p.get("sku") or "").strip()
        pid = str(p.get("product_id") or p.get("id") or "").strip()
        name = str(p.get("name") or "").strip()
        key = sku or pid or name
        if not key:
            continue
        try:
            qty = float(p.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        try:
            price = float(p.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        out.append({
            "key": key, "name": name, "sku": sku, "product_id": pid,
            "quantity": qty, "price": price,
        })
    return out


def _diff_items(old_items: list[dict], new_items: list[dict]) -> dict:
    """Return { added, removed, modified } between two item snapshots."""
    by_key_old = {it["key"]: it for it in old_items}
    by_key_new = {it["key"]: it for it in new_items}
    added: list[dict] = []
    removed: list[dict] = []
    modified: list[dict] = []
    for k, n in by_key_new.items():
        if k not in by_key_old:
            added.append(n)
        else:
            o = by_key_old[k]
            if (round(float(o.get("quantity") or 0), 4)
                    != round(float(n.get("quantity") or 0), 4)
                    or round(float(o.get("price") or 0), 2)
                    != round(float(n.get("price") or 0), 2)):
                modified.append({
                    "key": k, "name": n.get("name") or o.get("name"),
                    "before": {"quantity": o.get("quantity"),
                               "price": o.get("price")},
                    "after":  {"quantity": n.get("quantity"),
                               "price": n.get("price")},
                })
    for k, o in by_key_old.items():
        if k not in by_key_new:
            removed.append(o)
    return {"added": added, "removed": removed, "modified": modified}


async def _record_order_adjustment(
    db, user_id: str, order_number: str,
    before: dict | None, after: dict | None, reason: str = "resync",
) -> dict | None:
    """Iter-91 Phase 2 — persist a diff row in `order_adjustments` whenever
    a resync (or any future hook) detects a meaningful change.

    Meaningful change = total_amount differs OR items list differs.
    Returns the stored row dict (or None when no change was detected).
    """
    if before is None or after is None:
        return None

    old_total = round(float(before.get("total_amount") or 0), 2)
    new_total = round(float(after.get("total_amount") or 0), 2)
    old_items = _summarise_items(before.get("products"))
    new_items = _summarise_items(after.get("products"))
    items_diff = _diff_items(old_items, new_items)
    items_changed = bool(
        items_diff["added"] or items_diff["removed"] or items_diff["modified"]
    )
    total_changed = (old_total != new_total)
    if not total_changed and not items_changed:
        return None

    old_cogs = round(float(before.get("total_product_cost") or 0), 2)
    new_cogs = round(float(after.get("total_product_cost") or 0), 2)

    row = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "order_number": order_number,
        "reason": reason,
        "old_total": old_total,
        "new_total": new_total,
        "delta_total": round(new_total - old_total, 2),
        "old_cogs": old_cogs,
        "new_cogs": new_cogs,
        "delta_cogs": round(new_cogs - old_cogs, 2),
        "items_changed": items_changed,
        "total_changed": total_changed,
        "items_diff": items_diff,
        "created_at": _now(),
    }
    await db.order_adjustments.insert_one(row)
    row.pop("_id", None)
    return row


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


# ── Plan-B bridge — Salla Direct → integration_inbox (upsert) ────────────
# Constants scoped so the tests can monkey-patch them if needed.
SALLA_DIRECT_CONNECTOR_KEY = "salla_direct"
SALLA_DIRECT_SOURCE_TAG = "salla_direct"


def _salla_direct_idempotency_key(order_number: str) -> str:
    """Stable per-order key — NO status suffix.

    User directive (2026-02): a single Salla order maps to exactly ONE
    `integration_inbox` row from the Salla Direct pull, regardless of
    how many times its status changes. Status transitions UPDATE the
    same row instead of inserting new ones.
    """
    return f"salla_direct:order:{order_number}"


async def upsert_salla_direct_to_inbox(
    db, *, user_id: str, raw_salla_order: dict,
) -> dict:
    """Idempotent writer: Salla Direct raw order → `integration_inbox`.

    Contract (user directive 2026-02):
      • Exactly one active row per (user_id, connector_key="salla_direct",
        salla_order_number).
      • Status transitions on the same order UPDATE that row (do NOT
        create a second).
      • NEVER calls Qoyod. NEVER auto-sends. Pure canonical persistence.
      • Preserves any real invoice markers already on the row
        (`manual_qoyod_invoice_id`, `qoyod_invoice_id`) — Plan B send.py
        may have written them from a prior push.
      • Preserves `pipeline_stage` when the existing row is past
        NORMALIZED (e.g. INVOICE_CREATED / COMPLETED). We only bring a
        NEW row up to NORMALIZED.

    Cross-source dedup with Make.com:
      Plan-B `list_pending_orders` groups by `salla_order_number` at the
      aggregation level, so a Make row + a Salla-Direct row for the
      same order collapse to a single Pending entry. The distinct
      `connector_key` values keep their unique-index namespaces clean.

    Returns:
        {"ok": True/False, "created": bool, "row_id": str, ...}
    """
    # Local imports — avoid circular deps at module load time.
    from integrations.qoyod.normalizer import (
        validate as _validate,
        normalize as _normalize,
        NormalizationError,
    )
    from integrations.qoyod.state_machine import initial_history_entry

    order_number = str(
        raw_salla_order.get("reference_id")
        or raw_salla_order.get("id")
        or ""
    ).strip()
    if not order_number:
        return {"ok": False, "reason": "missing_order_number"}

    wrapped = {"event": "salla_direct_sync", "data": raw_salla_order}
    now = _now()
    idem_key = _salla_direct_idempotency_key(order_number)
    salla_order_id = str(raw_salla_order.get("id") or "") or None

    ok, err = _validate(wrapped)
    if not ok:
        return {"ok": False, "reason": "invalid_payload", "error": err}

    try:
        dto = _normalize(wrapped, received_at=now)
        canonical = dto.model_dump(mode="json")
    except NormalizationError as ne:
        return {"ok": False, "reason": "normalization_error",
                "error": ne.to_log_dict()}
    except Exception as exc:  # defensive — never crash the sync loop
        return {"ok": False, "reason": "normalizer_crash",
                "error": f"{exc.__class__.__name__}: {exc}"}

    filt = {
        "user_id": user_id,
        "connector_key": SALLA_DIRECT_CONNECTOR_KEY,
        "idempotency_key": idem_key,
    }
    existing = await db.integration_inbox.find_one(
        filt, {"_id": 0, "id": 1, "pipeline_stage": 1, "trace_id": 1},
    )

    # ── Path A: new row ────────────────────────────────────────────
    if existing is None:
        row_id = uuid.uuid4().hex
        trace_id = uuid.uuid4().hex
        item_count = len(canonical.get("items") or [])
        history = [
            initial_history_entry(
                actor="salla_direct_sync",
                note=f"trace_id={trace_id} · connector=salla_direct",
            ),
            {
                "stage": "NORMALIZED",
                "actor": "salla_direct_sync",
                "at": now,
                "note": f"DTO built · {item_count} items",
            },
        ]
        doc = {
            "id": row_id,
            "schema_version": 1,
            "user_id": user_id,
            "trace_id": trace_id,
            "connector_key": SALLA_DIRECT_CONNECTOR_KEY,
            "source": SALLA_DIRECT_SOURCE_TAG,
            "received_at": now,
            "raw_payload": wrapped,
            "adapted_payload": None,
            "adapter_meta": {
                "adapter_applied": False,
                "items_source": "items",
                "legacy_status_slug": None,
                "legacy_extras": {},
            },
            "enrichment_fallback_used": False,
            "raw_headers": {},
            "signature_status": "internal",
            "salla_order_id": salla_order_id,
            "salla_order_number": order_number,
            "idempotency_key": idem_key,
            "pipeline_stage": "NORMALIZED",
            "pipeline_error": None,
            "attempts": 0,
            "next_retry_at": None,
            "processed_at": None,
            "canonical_payload": canonical,
            "pipeline_started_at": now,
            "stage_history": history,
        }
        try:
            await db.integration_inbox.insert_one(doc)
            return {"ok": True, "created": True, "row_id": row_id,
                    "trace_id": trace_id}
        except DuplicateKeyError:
            # Race: another writer inserted between find and insert.
            # Fall through to the update path.
            pass

    # ── Path B: existing row (upsert / status refresh) ────────────
    prior_stage = (existing or {}).get("pipeline_stage") if existing else None

    update_set: dict = {
        "raw_payload": wrapped,
        "canonical_payload": canonical,
        "salla_order_id": salla_order_id,
        "salla_order_number": order_number,
        "received_at": now,
    }
    # Only advance NEW → NORMALIZED. NEVER regress an advanced stage
    # (INVOICE_CREATED / COMPLETED / DEAD_LETTER) — Plan B may have
    # already moved the row past NORMALIZED.
    if prior_stage in (None, "NEW"):
        update_set["pipeline_stage"] = "NORMALIZED"

    history_entry = {
        "stage": prior_stage or "NORMALIZED",
        "actor": "salla_direct_sync",
        "at": now,
        "note": "Salla Direct sync refreshed payload",
    }
    await db.integration_inbox.update_one(
        filt,
        {"$set": update_set,
         "$push": {"stage_history": history_entry}},
    )
    return {
        "ok": True,
        "created": False,
        "row_id": (existing or {}).get("id"),
        "trace_id": (existing or {}).get("trace_id"),
    }
