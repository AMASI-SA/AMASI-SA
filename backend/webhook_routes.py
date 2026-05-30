"""Make.com webhook integration — secondary data source.

Flow:  Salla → Make.com → POST /api/webhook/make/{token}
The webhook stores each order in `webhook_orders` (upsert by order_number per
user) so duplicates and updates are handled naturally. The merchant then
clicks "Build analysis from Make.com" (UI) which converts the accumulated
orders for a given date range into an `analyses` document using the SAME
match_settings + _build_report pipeline used by Excel uploads.

That way the dashboard, reports, daily costs, shipping accounts… everything
keeps working unchanged — only the data origin differs.

Endpoints under /api/webhook:
- POST   /make/{token}              → public, token-authed: ingest one or many orders
- GET    /settings                  → JWT: current token + URL + stats
- POST   /settings/rotate-token     → JWT: generate new token
- DELETE /settings                  → JWT: disconnect (delete token + orders)
- GET    /orders                    → JWT: list received orders (paginated)
- GET    /stats                     → JWT: counts and last sync per period
- POST   /build-analysis            → JWT: aggregate orders in date range → new analysis
"""
import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, Union, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field

from report_builder import build_report
from orders_db import upsert_order, orders_to_parsed

logger = logging.getLogger(__name__)


# ── Input models ──────────────────────────────────────────────────────────
class ProductItem(BaseModel):
    name: str = ""
    quantity: float = 0
    price: float = 0

    class Config:
        extra = "allow"  # keep product extras like sku, image_url, options…


def _to_float(value: Any, default: float = 0.0) -> float:
    """Make.com may send numbers as strings; coerce safely."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class WebhookOrderIn(BaseModel):
    """Liberal schema — accepts every field listed in the user's Make.com mapping.

    Required: at least one of {order_number, order_id} must be present (we use
    order_number as the dedup key; falls back to order_id if not provided).
    All numeric fields accept strings (Make.com tends to stringify numbers).
    """
    # Identifiers
    order_id: Optional[Union[str, int]] = ""
    order_number: Optional[Union[str, int]] = None

    # Dates
    created_at: Optional[str] = None  # ISO 8601 from Salla via Make
    order_date: Optional[str] = None  # Legacy alias

    # Status
    status: Optional[str] = ""
    order_status: Optional[str] = ""
    order_status_slug: Optional[str] = ""
    payment_status: Optional[str] = ""

    # Customer
    customer_name: Optional[str] = ""
    customer_mobile: Optional[str] = ""

    # Payment
    payment_method: Optional[str] = ""

    # Shipping
    shipping_company: Optional[str] = ""
    shipping_cost: Optional[Union[str, float, int]] = None

    # Amounts
    subtotal: Optional[Union[str, float, int]] = None
    discount: Optional[Union[str, float, int]] = None
    tax: Optional[Union[str, float, int]] = None
    total: Optional[Union[str, float, int]] = None
    total_amount: Optional[Union[str, float, int]] = None  # legacy alias for total
    currency: Optional[str] = ""

    # Items + meta
    products: list[ProductItem] = []
    tags: list[str] = []
    source: Optional[str] = ""

    class Config:
        extra = "allow"  # keep unknown Make.com fields under .raw


class BuildAnalysisIn(BaseModel):
    name: Optional[str] = ""
    date_from: str  # YYYY-MM-DD
    date_to: str    # YYYY-MM-DD
    snapchat_ads: float = 0.0
    tiktok_ads: float = 0.0
    instagram_ads: float = 0.0
    product_costs: float = 0.0


# ── Helpers ───────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_order_date(value: Any) -> Optional[str]:
    """Accept ISO 8601, 'YYYY-MM-DD', or 'YYYY-MM-DD HH:MM:SS'. Return YYYY-MM-DD or None."""
    if value is None or value == "":
        return None
    s = str(value).strip()
    # Try common formats
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    # Last resort: fromisoformat (handles offsets)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return None


def _build_router(db) -> APIRouter:
    from auth import get_current_user_from_db, ensure_user_settings, DEFAULT_PAYMENT_METHODS, DEFAULT_SHIPPING_COMPANIES

    router = APIRouter(prefix="/webhook", tags=["webhook"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    async def _get_or_create_token_doc(user_id: str) -> dict:
        doc = await db.webhook_tokens.find_one({"user_id": user_id})
        if doc:
            return doc
        new_doc = {
            "user_id": user_id,
            "token": uuid.uuid4().hex,
            "created_at": _now_iso(),
            "last_sync_at": None,
            "total_received": 0,
        }
        await db.webhook_tokens.insert_one(new_doc)
        return new_doc

    def _public_webhook_url(token: str) -> str:
        # Prefer explicit BACKEND_PUBLIC_URL when set; otherwise fall back to FRONTEND_URL
        # because in this environment /api is proxied from frontend domain to backend.
        base = (
            os.environ.get("BACKEND_PUBLIC_URL")
            or os.environ.get("FRONTEND_URL", "")
        ).rstrip("/")
        return f"{base}/api/webhook/make/{token}" if base else f"/api/webhook/make/{token}"

    # ── PUBLIC INGESTION ──────────────────────────────────────────────────

    # TikTok Ads daily metrics (push from Make.com)
    class TikTokSpendIn(BaseModel):
        date: str  # YYYY-MM-DD
        spend: float = 0.0
        purchases: int = 0
        revenue: float = 0.0
        source: Optional[str] = "tiktok"   # informational only

        class Config:
            extra = "allow"

    @router.post("/tiktok/{token}")
    async def ingest_tiktok(token: str, request: Request):
        """Ingest a single TikTok-Ads daily row pushed by Make.com.

        Body shape (per user spec):
            {"source":"tiktok","date":"2026-05-30","spend":350.75,
             "purchases":12,"revenue":2400.00}

        Upserts into `tiktok_ads_daily` keyed by (user_id, date). Posting the
        same date twice overwrites the previous row, so Make.com can safely
        re-send a day if it gets refreshed.
        """
        tok_doc = await db.webhook_tokens.find_one({"token": token})
        if not tok_doc:
            raise HTTPException(status_code=401, detail="Invalid webhook token")
        user_id = tok_doc["user_id"]
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        # Allow a batch (list) or single object
        items = body if isinstance(body, list) else [body]
        accepted = 0
        errors: list[dict] = []
        for raw in items:
            try:
                payload = TikTokSpendIn(**raw)
            except Exception as exc:
                errors.append({"data": raw, "error": str(exc)})
                continue
            # Strict date format
            import re
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", payload.date):
                errors.append({"data": raw, "error": "date must be YYYY-MM-DD"})
                continue
            doc = {
                "user_id": user_id,
                "date": payload.date,
                "spend": round(float(payload.spend or 0), 2),
                "purchases": int(payload.purchases or 0),
                "revenue": round(float(payload.revenue or 0), 2),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.tiktok_ads_daily.update_one(
                {"user_id": user_id, "date": payload.date},
                {"$set": doc, "$setOnInsert": {"id": str(uuid.uuid4()),
                                                 "created_at": doc["updated_at"]}},
                upsert=True,
            )
            accepted += 1

        # Update webhook token stats for visibility on the UI
        await db.webhook_tokens.update_one(
            {"token": token},
            {"$set": {"last_sync_at": datetime.now(timezone.utc).isoformat()}},
        )
        return {"accepted": accepted, "errors": errors}

    @router.get("/tiktok/recent")
    async def tiktok_recent(days: int = Query(30, ge=1, le=365), user: dict = Depends(current_user)):
        from datetime import timedelta, date as _date
        cutoff = (_date.today() - timedelta(days=days - 1)).isoformat()
        items = await db.tiktok_ads_daily.find(
            {"user_id": user["id"], "date": {"$gte": cutoff}}, {"_id": 0}
        ).sort("date", -1).to_list(days)
        return {"items": items}

    @router.post("/make/{token}")
    async def ingest_orders(token: str, request: Request):
        tok_doc = await db.webhook_tokens.find_one({"token": token})
        if not tok_doc:
            raise HTTPException(status_code=401, detail="Invalid webhook token")
        user_id = tok_doc["user_id"]

        # Accept: single object, or list of objects, or {"orders": [...]} wrapper
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        if isinstance(body, dict) and "orders" in body and isinstance(body["orders"], list):
            items = body["orders"]
        elif isinstance(body, list):
            items = body
        elif isinstance(body, dict):
            items = [body]
        else:
            raise HTTPException(status_code=400, detail="Expected object or array of orders")

        accepted = 0
        updated = 0
        accepted_without_date = 0
        errors: list[dict] = []
        for raw in items:
            try:
                payload = WebhookOrderIn(**raw)
            except Exception as exc:
                errors.append({"data": raw, "error": str(exc)})
                continue

            # order_number is dedup key; fall back to order_id when absent
            order_number = str(payload.order_number or payload.order_id or "").strip()
            if not order_number:
                errors.append({"data": raw, "error": "missing order_number/order_id"})
                continue

            # total: prefer `total`; fall back to legacy `total_amount` only if `total` not provided
            if payload.total is not None and str(payload.total) != "":
                total_val = _to_float(payload.total)
            else:
                total_val = _to_float(payload.total_amount)

            # normalize date — prefer created_at, then order_date.
            # We DO NOT fall back to today when both are missing: doing so
            # silently labels orders with the wrong month (e.g. a March order
            # that Make.com forwards today without a created_at field would
            # land in May, inflating the current month's stats). Leaving
            # order_date=None makes the missing-date case explicit — the
            # order is still saved and visible on the Make.com page, but it
            # won't appear in date-filtered dashboard/reports until the
            # merchant fixes their Make.com mapping (or re-sends with
            # created_at).
            order_date_norm = (
                _normalize_order_date(payload.created_at)
                or _normalize_order_date(payload.order_date)
                or _normalize_order_date(raw.get("created_at"))
                or _normalize_order_date(raw.get("order_date"))
                or _normalize_order_date(raw.get("purchase_date"))
                or _normalize_order_date(raw.get("date"))
            )

            incoming = {
                # Identifiers
                "order_id": str(payload.order_id or "").strip(),
                # Dates
                "order_date": order_date_norm,
                "order_date_raw": (payload.created_at or payload.order_date or "").strip(),
                # Status
                "order_status": (payload.order_status or payload.status or "").strip(),
                "order_status_slug": (payload.order_status_slug or "").strip(),
                "payment_status": (payload.payment_status or "").strip(),
                # Customer
                "customer_name": (payload.customer_name or "").strip(),
                "customer_mobile": (payload.customer_mobile or "").strip(),
                # Payment
                "payment_method": (payload.payment_method or "").strip(),
                # Shipping
                "shipping_company": (payload.shipping_company or "").strip(),
                "shipping_cost": round(_to_float(payload.shipping_cost), 2),
                # Amounts
                "subtotal": round(_to_float(payload.subtotal), 2),
                "discount": round(_to_float(payload.discount), 2),
                "tax": round(_to_float(payload.tax), 2),
                "total_amount": round(total_val, 2),
                "currency": (payload.currency or "").strip(),
                # Marketing meta
                "source": (payload.source or "").strip(),
                "utm_source": str(raw.get("utm_source") or "").strip(),
                "utm_medium": str(raw.get("utm_medium") or "").strip(),
                "utm_campaign": str(raw.get("utm_campaign") or "").strip(),
                "device": str(raw.get("device") or "").strip(),
                # Items
                "products": [p.dict() for p in payload.products],
                "tags": [str(t).strip() for t in (payload.tags or []) if str(t).strip()],
            }
            res = await upsert_order(
                db, user_id, order_number, incoming, source="make", raw=raw,
            )
            if res["created"]:
                accepted += 1
            else:
                updated += 1
            if order_date_norm is None:
                accepted_without_date += 1

        await db.webhook_tokens.update_one(
            {"user_id": user_id},
            {"$set": {"last_sync_at": _now_iso()},
             "$inc": {"total_received": accepted + updated}},
        )

        return {
            "ok": True,
            "accepted": accepted,
            "updated": updated,
            "without_date": accepted_without_date,
            "errors": errors[:20],  # cap response size
            "error_count": len(errors),
        }

    # ── AUTHED MANAGEMENT ─────────────────────────────────────────────────
    @router.get("/settings")
    async def get_webhook_settings(user: dict = Depends(current_user)):
        tok = await _get_or_create_token_doc(user["id"])
        total_orders = await db.unified_orders.count_documents({"user_id": user["id"]})
        return {
            "token": tok["token"],
            "webhook_url": _public_webhook_url(tok["token"]),
            "tiktok_webhook_url": _public_webhook_url(tok["token"]).replace("/make/", "/tiktok/"),
            "last_sync_at": tok.get("last_sync_at"),
            "total_received": tok.get("total_received", 0),
            "total_orders_in_db": total_orders,
            "sample_payload": {
                "order_id": "987654321",
                "order_number": "12345",
                "created_at": "2026-02-15T14:30:00+03:00",
                "customer_name": "أحمد محمد",
                "customer_mobile": "+966500000000",
                "payment_method": "مدى",
                "payment_status": "paid",
                "shipping_company": "سمسا",
                "shipping_cost": 23.0,
                "subtotal": 240.0,
                "discount": 10.0,
                "total": 285.0,
                "currency": "SAR",
                "products": [
                    {"name": "منتج 1", "quantity": 2, "price": 100.0},
                    {"name": "منتج 2", "quantity": 1, "price": 50.0}
                ],
                "tags": ["new-customer", "weekend"],
                "source": "store"
            },
        }

    @router.post("/settings/rotate-token")
    async def rotate_token(user: dict = Depends(current_user)):
        new_token = uuid.uuid4().hex
        await db.webhook_tokens.update_one(
            {"user_id": user["id"]},
            {"$set": {"token": new_token, "created_at": _now_iso()}},
            upsert=True,
        )
        return {"token": new_token, "webhook_url": _public_webhook_url(new_token)}

    @router.delete("/settings")
    async def disconnect(user: dict = Depends(current_user)):
        await db.webhook_tokens.delete_many({"user_id": user["id"]})
        # Only delete orders that came from Make (preserve Excel-imported ones)
        deleted = await db.unified_orders.delete_many({"user_id": user["id"], "data_source": "make"})
        return {"ok": True, "deleted_orders": deleted.deleted_count}

    @router.get("/orders")
    async def list_orders(
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        data_source: Optional[str] = Query(None, description="excel | make"),
        limit: int = Query(100, ge=1, le=500),
        user: dict = Depends(current_user),
    ):
        q: dict = {"user_id": user["id"]}
        if data_source in {"excel", "make"}:
            q["data_source"] = data_source
        if date_from or date_to:
            q["order_date"] = {}
            if date_from:
                q["order_date"]["$gte"] = date_from
            if date_to:
                q["order_date"]["$lte"] = date_to
        cur = (
            db.unified_orders.find(q, {"_id": 0, "raw_by_source": 0})
            .sort([("received_at", -1), ("updated_at", -1)])
            .limit(limit)
        )
        items = await cur.to_list(limit)
        total = await db.unified_orders.count_documents(q)
        return {"orders": items, "total": total, "limit": limit}

    @router.get("/stats")
    async def stats(user: dict = Depends(current_user)):
        tok = await db.webhook_tokens.find_one({"user_id": user["id"]}, {"_id": 0})
        total = await db.unified_orders.count_documents({"user_id": user["id"]})
        # earliest + latest order_date
        pipeline = [
            {"$match": {"user_id": user["id"], "order_date": {"$ne": None}}},
            {"$group": {"_id": None,
                        "min_date": {"$min": "$order_date"},
                        "max_date": {"$max": "$order_date"}}},
        ]
        rng = None
        async for doc in db.unified_orders.aggregate(pipeline):
            rng = {"earliest": doc.get("min_date"), "latest": doc.get("max_date")}
        # Per-source breakdown
        per_source: dict = {"excel": 0, "make": 0}
        async for doc in db.unified_orders.aggregate([
            {"$match": {"user_id": user["id"]}},
            {"$group": {"_id": "$data_source", "n": {"$sum": 1}}},
        ]):
            key = doc.get("_id") or "unknown"
            per_source[key] = int(doc.get("n", 0))
        # Orders missing creation date (data quality signal)
        missing_date = await db.unified_orders.count_documents({
            "user_id": user["id"],
            "$or": [
                {"order_date": None},
                {"order_date": ""},
                {"order_date": {"$exists": False}},
            ],
        })
        return {
            "connected": bool(tok),
            "total_orders_in_db": total,
            "total_received_ever": (tok or {}).get("total_received", 0),
            "last_sync_at": (tok or {}).get("last_sync_at"),
            "date_range": rng,
            "by_source": per_source,
            "orders_missing_date": missing_date,
        }

    @router.get("/orders-missing-date")
    async def orders_missing_date(
        limit: int = Query(100, ge=1, le=500),
        user: dict = Depends(current_user),
    ):
        """List orders that have no order_date — usually because Make.com
        sent the webhook without `created_at`. The merchant should fix
        their Make.com scenario to include the order creation date.
        """
        q: dict = {
            "user_id": user["id"],
            "$or": [
                {"order_date": None},
                {"order_date": ""},
                {"order_date": {"$exists": False}},
            ],
        }
        cur = (
            db.unified_orders.find(q, {"_id": 0, "raw_by_source": 0})
            .sort("received_at", -1)
            .limit(limit)
        )
        items = await cur.to_list(limit)
        total = await db.unified_orders.count_documents(q)
        return {"orders": items, "total": total, "limit": limit}

    @router.post("/build-analysis")
    async def build_analysis(payload: BuildAnalysisIn, user: dict = Depends(current_user)):
        try:
            datetime.strptime(payload.date_from, "%Y-%m-%d")
            datetime.strptime(payload.date_to, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="صيغة التاريخ يجب أن تكون YYYY-MM-DD")

        cur = db.unified_orders.find(
            {
                "user_id": user["id"],
                "order_date": {"$gte": payload.date_from, "$lte": payload.date_to},
            },
            {"_id": 0, "raw_by_source": 0},
        )
        orders = await cur.to_list(50000)
        if not orders:
            raise HTTPException(
                status_code=400,
                detail=f"لا توجد طلبات بين {payload.date_from} و {payload.date_to}",
            )

        parsed = orders_to_parsed(orders)
        settings = await ensure_user_settings(db, user["id"])
        report = build_report(
            parsed,
            settings.get("payment_methods", DEFAULT_PAYMENT_METHODS),
            settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
            payload.snapchat_ads, payload.tiktok_ads, payload.instagram_ads, payload.product_costs,
        )

        name = payload.name or f"Unified {payload.date_from} → {payload.date_to}"
        analysis = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "name": name,
            "filename": f"unified_{payload.date_from}_{payload.date_to}.json",
            "source": "unified",
            "date_from": payload.date_from,
            "date_to": payload.date_to,
            "date": payload.date_to,
            "created_at": _now_iso(),
            "report": report,
            "orders_count": len(orders),
        }
        await db.analyses.insert_one(analysis)
        analysis.pop("_id", None)
        return analysis

    return router


def attach_webhook_routes(parent_router: APIRouter, db) -> None:
    parent_router.include_router(_build_router(db))
