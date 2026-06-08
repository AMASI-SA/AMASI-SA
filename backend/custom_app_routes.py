"""Custom App Integration — Iter-105

Receives orders, products and customers from the merchant's own custom
application via a simple API-key authenticated REST interface.

Architectural rules respected
-----------------------------
- All existing sources (Excel, Make, Salla-direct, settlement files,
  Snapchat / TikTok / Meta ads) keep working untouched.
- Orders land in the same `unified_orders` collection via the existing
  `upsert_order(...)` merge logic with `source="custom_app"`. We extend
  the source precedence: custom_app >= make > salla_direct > excel.
- Three new collections (additive, no replacement of existing ones):
    • `order_items`            one row per line item, keyed by user_id +
                              order_number + sku + product_id.
    • `custom_app_products`    product catalogue from the custom app.
    • `custom_app_customers`   customer catalogue from the custom app.
    • `integration_events`     audit log: every inbound payload (raw),
                              outcome, errors. NEVER mutated.

API surface (all under /api/integrations/custom-app/)
-----------------------------------------------------
POST /orders          single order or batch (`orders: [...]`)
POST /products        single product or batch (`products: [...]`)
POST /customers       single customer or batch (`customers: [...]`)
GET  /status          monitoring counters + last events
GET  /settings        current settings (API key, webhook url, …) — JWT
POST /settings/api-key/regenerate  rotate API key — JWT
POST /test-connection ping endpoint — accepts API key
"""
from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user_from_db
from orders_db import upsert_order


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round(v) -> float:
    return round(float(v or 0), 2)


def _generate_api_key() -> str:
    """64-char URL-safe random — enough entropy for a long-lived key."""
    return f"mzn_{secrets.token_urlsafe(32)}"


# ── Pydantic input models ──────────────────────────────────────────────
class OrderItemIn(BaseModel):
    product_id: Optional[str] = None
    sku: Optional[str] = None
    barcode: Optional[str] = None
    product_name: str
    variant_name: Optional[str] = None
    quantity: float = Field(1.0, gt=0)
    unit_price: float = Field(0.0, ge=0)
    total_price: Optional[float] = None
    cost_price: Optional[float] = None
    weight: Optional[float] = None
    image_url: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None


class OrderIn(BaseModel):
    # Identity (one of these is required)
    order_id: Optional[str] = None
    order_number: Optional[str] = None
    reference_id: Optional[str] = None

    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    order_status: Optional[str] = None
    payment_status: Optional[str] = None
    payment_method: Optional[str] = None
    source: Optional[str] = None
    currency: Optional[str] = "SAR"

    # Amounts
    subtotal: Optional[float] = 0
    discount: Optional[float] = 0
    shipping_cost: Optional[float] = 0
    tax: Optional[float] = 0
    fees: Optional[float] = 0
    total_amount: Optional[float] = 0
    paid_amount: Optional[float] = 0
    refunded_amount: Optional[float] = 0

    # Customer
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None

    # Shipping
    shipping_company: Optional[str] = None
    tracking_number: Optional[str] = None
    shipment_status: Optional[str] = None
    shipping_address: Optional[str] = None

    # Marketing
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    utm_term: Optional[str] = None
    device_type: Optional[str] = None

    # Line items
    items: list[OrderItemIn] = Field(default_factory=list)


class OrderBatchIn(BaseModel):
    orders: list[OrderIn] = Field(default_factory=list)


class ProductIn(BaseModel):
    product_id: Optional[str] = None
    sku: Optional[str] = None
    barcode: Optional[str] = None
    name: str
    cost_price: Optional[float] = None
    sale_price: Optional[float] = None
    quantity: Optional[float] = None
    image_url: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None


class ProductBatchIn(BaseModel):
    products: list[ProductIn] = Field(default_factory=list)


class CustomerIn(BaseModel):
    customer_id: Optional[str] = None
    name: str
    mobile: Optional[str] = None
    email: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None


class CustomerBatchIn(BaseModel):
    customers: list[CustomerIn] = Field(default_factory=list)


# ── Helpers ────────────────────────────────────────────────────────────
async def _get_or_create_settings(db, user_id: str) -> dict:
    """Read (and lazily seed) custom_app settings for a user."""
    s = await db.settings.find_one({"user_id": user_id}, {"_id": 0})
    s = s or {"user_id": user_id}
    cfg = (s.get("custom_app") or {})
    if not cfg.get("api_key"):
        cfg["api_key"] = _generate_api_key()
        cfg["created_at"] = _now()
        cfg["enabled"] = True
        await db.settings.update_one(
            {"user_id": user_id},
            {"$set": {"custom_app": cfg, "user_id": user_id}},
            upsert=True,
        )
    return cfg


async def _resolve_user_by_api_key(db, api_key: str) -> Optional[dict]:
    """Find the owner of a given API key."""
    if not api_key or not api_key.startswith("mzn_"):
        return None
    doc = await db.settings.find_one(
        {"custom_app.api_key": api_key},
        {"_id": 0, "user_id": 1, "custom_app": 1},
    )
    if not doc:
        return None
    if not (doc.get("custom_app") or {}).get("enabled", True):
        return None
    user = await db.users.find_one(
        {"id": doc["user_id"]},
        {"_id": 0, "id": 1, "email": 1, "name": 1},
    )
    return user


async def _log_event(
    db, user_id: str, event_type: str, status: str,
    payload: Any, summary: str, error: Optional[str] = None,
) -> None:
    """Append to integration_events. Capped at 2000 rows per user (newest kept)."""
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "event_type": event_type,        # "orders" / "products" / "customers" / "test"
        "source": "custom_app",
        "status": status,                # "success" / "error"
        "summary": summary,
        "error": error,
        "payload": payload,              # raw inbound body for audit
        "created_at": _now(),
    }
    await db.integration_events.insert_one(doc)
    # Cap: keep the newest 2000 per user (lightweight LRU).
    count = await db.integration_events.count_documents({"user_id": user_id})
    if count > 2000:
        excess_cur = db.integration_events.find(
            {"user_id": user_id}, {"_id": 0, "id": 1, "created_at": 1},
        ).sort([("created_at", 1)]).limit(count - 2000)
        ids = [d["id"] async for d in excess_cur]
        if ids:
            await db.integration_events.delete_many({"id": {"$in": ids}})


def _normalize_order_for_unified(o: OrderIn) -> dict:
    """Flatten the OrderIn payload into the shape `unified_orders` expects."""
    out: dict = {
        # identity
        "order_id": (o.order_id or o.order_number or o.reference_id or "").strip(),
        "reference_id": (o.reference_id or "").strip() or None,
        # status
        "order_status": o.order_status,
        "payment_status": o.payment_status,
        "payment_method": o.payment_method,
        # amounts
        "subtotal": _round(o.subtotal),
        "discount": _round(o.discount),
        "shipping_cost": _round(o.shipping_cost),
        "tax": _round(o.tax),
        "fees": _round(o.fees),
        "total_amount": _round(o.total_amount),
        "paid_amount": _round(o.paid_amount),
        "refunded_amount": _round(o.refunded_amount),
        "currency": (o.currency or "SAR"),
        # customer
        "customer_id": o.customer_id,
        "customer_name": o.customer_name,
        "customer_mobile": o.mobile,
        "customer_email": o.email,
        "customer_city": o.city,
        "customer_country": o.country,
        # shipping
        "shipping_company": o.shipping_company,
        "tracking_number": o.tracking_number,
        "shipment_status": o.shipment_status,
        "shipping_address": o.shipping_address,
        # marketing
        "source": o.source,
        "utm_source": o.utm_source,
        "utm_medium": o.utm_medium,
        "utm_campaign": o.utm_campaign,
        "utm_content": o.utm_content,
        "utm_term": o.utm_term,
        "device": o.device_type,
    }
    # Date normalisation — try to extract YYYY-MM-DD from created_at.
    if o.created_at:
        out["order_date_raw"] = o.created_at
        try:
            out["order_date"] = o.created_at[:10]
        except Exception:
            pass
    if o.updated_at:
        out["updated_at"] = o.updated_at
    return out


async def _upsert_order_items(db, user_id: str, order_number: str,
                               items: list[OrderItemIn]) -> int:
    """Replace this order's line items in `order_items`. Returns count inserted."""
    await db.order_items.delete_many(
        {"user_id": user_id, "order_number": order_number,
         "source": "custom_app"},
    )
    if not items:
        return 0
    docs = []
    now = _now()
    for it in items:
        q = _round(it.quantity)
        p = _round(it.unit_price)
        line_total = _round(it.total_price if it.total_price is not None else q * p)
        docs.append({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "order_number": order_number,
            "source": "custom_app",
            "product_id": it.product_id,
            "sku": it.sku,
            "barcode": it.barcode,
            "product_name": it.product_name,
            "variant_name": it.variant_name,
            "quantity": q,
            "unit_price": p,
            "total_price": line_total,
            "cost_price": _round(it.cost_price) if it.cost_price is not None else None,
            "weight": it.weight,
            "image_url": it.image_url,
            "category": it.category,
            "brand": it.brand,
            "created_at": now,
        })
    await db.order_items.insert_many(docs)
    return len(docs)


# ── Index setup ────────────────────────────────────────────────────────
async def ensure_custom_app_indexes(db) -> None:
    for col, spec, name in [
        ("order_items",        [("user_id", 1), ("order_number", 1)], "items_owner_order"),
        ("custom_app_products", [("user_id", 1), ("product_id", 1)], "products_owner_pid"),
        ("custom_app_products", [("user_id", 1), ("sku", 1)],         "products_owner_sku"),
        ("custom_app_customers", [("user_id", 1), ("customer_id", 1)], "customers_owner_cid"),
        ("integration_events", [("user_id", 1), ("created_at", -1)], "events_owner_date"),
        ("settings",           [("custom_app.api_key", 1)],            "settings_capi"),
    ]:
        try:
            await db[col].create_index(spec, name=name)
        except Exception:
            pass


# ── Router ─────────────────────────────────────────────────────────────
def attach_custom_app_routes(parent_router: APIRouter, db) -> None:
    """Two parallel auth surfaces:
       • API-key endpoints  — for the customer's own application.
       • JWT endpoints      — for the UI (settings / monitoring page).
    """

    router = APIRouter(prefix="/integrations/custom-app", tags=["custom-app"])

    # ── shared API-key auth dependency ────────────────────────────────
    async def api_key_user(
        x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
        authorization: Optional[str] = Header(None),
    ) -> dict:
        key = x_api_key
        if not key and authorization and authorization.startswith("Bearer mzn_"):
            key = authorization.replace("Bearer ", "", 1)
        if not key:
            raise HTTPException(401, "Missing X-API-Key header")
        user = await _resolve_user_by_api_key(db, key)
        if not user:
            raise HTTPException(401, "Invalid or revoked API key")
        return user

    async def jwt_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    # ── POST /orders ──────────────────────────────────────────────────
    @router.post("/orders")
    async def receive_orders(body: dict, user: dict = Depends(api_key_user)):
        """Accepts a single order ({order_id, ...}) OR a batch
        ({orders: [...]}). Each order:
          1. Becomes a unified_orders upsert (via existing merge logic).
          2. Its line items are stored separately in `order_items`.
          3. The full raw payload is saved in `integration_events`.
        """
        raw_payload = body
        try:
            if "orders" in body:
                batch = OrderBatchIn(**body).orders
            else:
                batch = [OrderIn(**body)]
        except Exception as e:
            await _log_event(
                db, user["id"], "orders", "error",
                raw_payload, "Validation error", error=str(e),
            )
            raise HTTPException(400, f"Invalid payload: {e}")

        if not batch:
            raise HTTPException(400, "No orders supplied")

        results = []
        created_n = 0
        updated_n = 0
        for o in batch:
            order_number = (o.order_number or o.order_id or o.reference_id or "").strip()
            if not order_number:
                results.append({"ok": False, "error": "Missing order identifier"})
                continue
            normalised = _normalize_order_for_unified(o)
            try:
                up = await upsert_order(
                    db, user["id"], order_number, normalised,
                    source="custom_app",
                    raw=o.model_dump(),
                )
                items_count = await _upsert_order_items(
                    db, user["id"], order_number, o.items,
                )
                if up["created"]:
                    created_n += 1
                else:
                    updated_n += 1
                results.append({
                    "ok": True, "order_number": order_number,
                    "created": up["created"], "items": items_count,
                })
            except Exception as e:
                results.append({"ok": False, "order_number": order_number, "error": str(e)})

        ok_n = sum(1 for r in results if r["ok"])
        err_n = len(results) - ok_n
        await _log_event(
            db, user["id"], "orders",
            "success" if err_n == 0 else "error",
            raw_payload,
            f"{ok_n} orders processed ({created_n} new, {updated_n} updated)",
            error=None if err_n == 0 else f"{err_n} failed",
        )
        return {
            "ok": err_n == 0,
            "received": len(batch),
            "created": created_n,
            "updated": updated_n,
            "errors": err_n,
            "results": results,
        }

    # ── POST /products ────────────────────────────────────────────────
    @router.post("/products")
    async def receive_products(body: dict, user: dict = Depends(api_key_user)):
        try:
            if "products" in body:
                batch = ProductBatchIn(**body).products
            else:
                batch = [ProductIn(**body)]
        except Exception as e:
            await _log_event(db, user["id"], "products", "error", body, "Validation error", error=str(e))
            raise HTTPException(400, f"Invalid payload: {e}")
        if not batch:
            raise HTTPException(400, "No products supplied")

        created = updated = 0
        for p in batch:
            key = p.product_id or p.sku
            if not key:
                continue
            q = {"user_id": user["id"]}
            if p.product_id:
                q["product_id"] = p.product_id
            else:
                q["sku"] = p.sku
            existing = await db.custom_app_products.find_one(q, {"_id": 0, "id": 1})
            doc = {
                "user_id": user["id"],
                "product_id": p.product_id,
                "sku": p.sku,
                "barcode": p.barcode,
                "name": p.name,
                "cost_price": _round(p.cost_price) if p.cost_price is not None else None,
                "sale_price": _round(p.sale_price) if p.sale_price is not None else None,
                "quantity": p.quantity,
                "image_url": p.image_url,
                "category": p.category,
                "brand": p.brand,
                "updated_at": _now(),
                "source": "custom_app",
            }
            if existing:
                await db.custom_app_products.update_one(
                    {"id": existing["id"]}, {"$set": doc},
                )
                updated += 1
            else:
                doc["id"] = str(uuid.uuid4())
                doc["created_at"] = _now()
                await db.custom_app_products.insert_one(doc)
                created += 1

        await _log_event(db, user["id"], "products", "success", body,
                         f"{created} new, {updated} updated")
        return {"ok": True, "received": len(batch), "created": created, "updated": updated}

    # ── POST /customers ───────────────────────────────────────────────
    @router.post("/customers")
    async def receive_customers(body: dict, user: dict = Depends(api_key_user)):
        try:
            if "customers" in body:
                batch = CustomerBatchIn(**body).customers
            else:
                batch = [CustomerIn(**body)]
        except Exception as e:
            await _log_event(db, user["id"], "customers", "error", body, "Validation error", error=str(e))
            raise HTTPException(400, f"Invalid payload: {e}")
        if not batch:
            raise HTTPException(400, "No customers supplied")

        created = updated = 0
        for c in batch:
            q = {"user_id": user["id"]}
            if c.customer_id:
                q["customer_id"] = c.customer_id
            elif c.mobile:
                q["mobile"] = c.mobile
            else:
                continue
            existing = await db.custom_app_customers.find_one(q, {"_id": 0, "id": 1})
            doc = {
                "user_id": user["id"],
                "customer_id": c.customer_id,
                "name": c.name,
                "mobile": c.mobile,
                "email": c.email,
                "city": c.city,
                "country": c.country,
                "updated_at": _now(),
                "source": "custom_app",
            }
            if existing:
                await db.custom_app_customers.update_one(
                    {"id": existing["id"]}, {"$set": doc},
                )
                updated += 1
            else:
                doc["id"] = str(uuid.uuid4())
                doc["created_at"] = _now()
                await db.custom_app_customers.insert_one(doc)
                created += 1

        await _log_event(db, user["id"], "customers", "success", body,
                         f"{created} new, {updated} updated")
        return {"ok": True, "received": len(batch), "created": created, "updated": updated}

    # ── POST /test-connection ─────────────────────────────────────────
    @router.post("/test-connection")
    async def test_connection(user: dict = Depends(api_key_user)):
        await _log_event(
            db, user["id"], "test", "success",
            {"action": "ping"}, "Test connection from custom app",
        )
        return {"ok": True, "user_email": user.get("email"), "now": _now()}

    # ── GET /status (JWT) ─────────────────────────────────────────────
    @router.get("/status")
    async def status(user: dict = Depends(jwt_user)):
        uid = user["id"]
        orders_n = await db.unified_orders.count_documents(
            {"user_id": uid, "$or": [
                {"data_source": "custom_app"},
                {"last_source": "custom_app"},
                {"data_sources.source": "custom_app"},
            ]},
        )
        products_n = await db.custom_app_products.count_documents({"user_id": uid})
        customers_n = await db.custom_app_customers.count_documents({"user_id": uid})
        last_event = await db.integration_events.find_one(
            {"user_id": uid, "source": "custom_app"},
            {"_id": 0}, sort=[("created_at", -1)],
        )
        last_success = await db.integration_events.find_one(
            {"user_id": uid, "source": "custom_app", "status": "success"},
            {"_id": 0, "created_at": 1, "summary": 1, "event_type": 1},
            sort=[("created_at", -1)],
        )
        last_order = await db.unified_orders.find_one(
            {"user_id": uid, "data_source": "custom_app"},
            {"_id": 0, "order_number": 1, "total_amount": 1,
             "received_at": 1, "order_date": 1, "customer_name": 1},
            sort=[("received_at", -1)],
        )
        errors_n = await db.integration_events.count_documents(
            {"user_id": uid, "source": "custom_app", "status": "error"},
        )
        recent_errors = [
            d async for d in db.integration_events.find(
                {"user_id": uid, "source": "custom_app", "status": "error"},
                {"_id": 0, "id": 1, "event_type": 1, "summary": 1,
                 "error": 1, "created_at": 1},
            ).sort([("created_at", -1)]).limit(20)
        ]
        recent_events = [
            d async for d in db.integration_events.find(
                {"user_id": uid, "source": "custom_app"},
                {"_id": 0, "id": 1, "event_type": 1, "status": 1,
                 "summary": 1, "created_at": 1},
            ).sort([("created_at", -1)]).limit(20)
        ]
        return {
            "orders_count": orders_n,
            "products_count": products_n,
            "customers_count": customers_n,
            "last_order": last_order,
            "last_sync_at": (last_success or {}).get("created_at"),
            "last_event": last_event,
            "errors_count": errors_n,
            "recent_errors": recent_errors,
            "recent_events": recent_events,
            "connection_status": "connected" if last_event and last_event.get("status") == "success" else (
                "no_data" if not last_event else "error"
            ),
        }

    # ── GET /settings (JWT) ───────────────────────────────────────────
    @router.get("/settings")
    async def get_settings(request: Request, user: dict = Depends(jwt_user)):
        cfg = await _get_or_create_settings(db, user["id"])
        # Webhook URL from request origin (works behind proxies via X-Forwarded headers)
        scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        base_url = os.environ.get("PUBLIC_BASE_URL") or f"{scheme}://{host}"
        return {
            "api_key": cfg.get("api_key"),
            "enabled": cfg.get("enabled", True),
            "created_at": cfg.get("created_at"),
            "rotated_at": cfg.get("rotated_at"),
            "base_url": base_url,
            "endpoints": {
                "orders": f"{base_url}/api/integrations/custom-app/orders",
                "products": f"{base_url}/api/integrations/custom-app/products",
                "customers": f"{base_url}/api/integrations/custom-app/customers",
                "test": f"{base_url}/api/integrations/custom-app/test-connection",
            },
        }

    # ── POST /settings/api-key/regenerate (JWT) ───────────────────────
    @router.post("/settings/api-key/regenerate")
    async def regenerate_key(user: dict = Depends(jwt_user)):
        new_key = _generate_api_key()
        await db.settings.update_one(
            {"user_id": user["id"]},
            {"$set": {
                "user_id": user["id"],
                "custom_app.api_key": new_key,
                "custom_app.rotated_at": _now(),
                "custom_app.enabled": True,
            }},
            upsert=True,
        )
        return {"api_key": new_key, "rotated_at": _now()}

    # ── POST /settings/toggle (JWT) ───────────────────────────────────
    @router.post("/settings/toggle")
    async def toggle_enabled(body: dict, user: dict = Depends(jwt_user)):
        enabled = bool(body.get("enabled", True))
        await db.settings.update_one(
            {"user_id": user["id"]},
            {"$set": {"user_id": user["id"],
                      "custom_app.enabled": enabled}},
            upsert=True,
        )
        return {"enabled": enabled}

    parent_router.include_router(router)
