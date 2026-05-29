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
from datetime import datetime, timezone, date as date_cls
from typing import Optional, Union, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Input models ──────────────────────────────────────────────────────────
class ProductItem(BaseModel):
    name: str = ""
    quantity: float = 0
    price: float = 0


class WebhookOrderIn(BaseModel):
    """Liberal schema — Make.com mappings vary, so most fields are optional."""
    order_number: Union[str, int] = Field(..., description="رقم الطلب (المفتاح)")
    order_date: Optional[str] = None  # YYYY-MM-DD or ISO
    status: Optional[str] = ""
    customer_name: Optional[str] = ""
    total_amount: float = 0.0
    discount: float = 0.0
    tax: float = 0.0
    payment_method: Optional[str] = ""
    shipping_company: Optional[str] = ""
    products: list[ProductItem] = []

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


def _orders_to_parsed(orders: list[dict]) -> dict:
    """Reduce raw webhook orders → the same dict shape parse_salla_excel produces.

    This is the bridge that lets us reuse match_settings() + _build_report()
    without ANY change to the existing pipeline.
    """
    total_sales = 0.0
    total_orders = 0
    payments: dict[str, dict] = {}
    shippings: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    sample_orders: list[dict] = []

    for o in orders:
        amount = float(o.get("total_amount") or 0)
        pay = (o.get("payment_method") or "غير محدد").strip() or "غير محدد"
        ship = (o.get("shipping_company") or "غير محدد").strip() or "غير محدد"
        src = "Make.com"

        total_sales += amount
        total_orders += 1

        p = payments.setdefault(pay, {"name": pay, "orders_count": 0, "total_sales": 0.0})
        p["orders_count"] += 1
        p["total_sales"] += amount

        s = shippings.setdefault(ship, {"name": ship, "orders_count": 0})
        s["orders_count"] += 1

        sr = sources.setdefault(src, {"name": src, "orders_count": 0, "total_sales": 0.0})
        sr["orders_count"] += 1
        sr["total_sales"] += amount

        if len(sample_orders) < 10:
            sample_orders.append({
                "order_id": str(o.get("order_number", "")),
                "amount": amount,
                "payment_method": pay,
                "shipping_company": ship,
                "status": o.get("status") or "",
                "date": o.get("order_date") or "",
            })

    return {
        "total_sales": round(total_sales, 2),
        "total_orders": total_orders,
        "payment_methods": [
            {**v, "total_sales": round(v["total_sales"], 2)}
            for v in sorted(payments.values(), key=lambda x: -x["total_sales"])
        ],
        "shipping_companies": [
            v for v in sorted(shippings.values(), key=lambda x: -x["orders_count"])
        ],
        "order_sources": [
            {**v, "total_sales": round(v["total_sales"], 2)}
            for v in sorted(sources.values(), key=lambda x: -x["orders_count"])
        ],
        "orders_sample": sample_orders,
        "detected_columns": {"source": "Make.com"},
    }


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
        errors: list[dict] = []
        for raw in items:
            try:
                payload = WebhookOrderIn(**raw)
            except Exception as exc:
                errors.append({"data": raw, "error": str(exc)})
                continue
            order_number = str(payload.order_number).strip()
            if not order_number:
                errors.append({"data": raw, "error": "missing order_number"})
                continue

            doc = {
                "user_id": user_id,
                "order_number": order_number,
                "order_date": _normalize_order_date(payload.order_date) or _normalize_order_date(raw.get("order_date")),
                "status": (payload.status or "").strip(),
                "customer_name": (payload.customer_name or "").strip(),
                "total_amount": round(float(payload.total_amount or 0), 2),
                "discount": round(float(payload.discount or 0), 2),
                "tax": round(float(payload.tax or 0), 2),
                "payment_method": (payload.payment_method or "").strip(),
                "shipping_company": (payload.shipping_company or "").strip(),
                "products": [p.dict() for p in payload.products],
                "raw": raw,
                "updated_at": _now_iso(),
            }
            res = await db.webhook_orders.update_one(
                {"user_id": user_id, "order_number": order_number},
                {"$set": doc, "$setOnInsert": {"received_at": _now_iso()}},
                upsert=True,
            )
            if res.upserted_id is not None:
                accepted += 1
            else:
                updated += 1

        await db.webhook_tokens.update_one(
            {"user_id": user_id},
            {"$set": {"last_sync_at": _now_iso()},
             "$inc": {"total_received": accepted + updated}},
        )

        return {
            "ok": True,
            "accepted": accepted,
            "updated": updated,
            "errors": errors[:20],  # cap response size
            "error_count": len(errors),
        }

    # ── AUTHED MANAGEMENT ─────────────────────────────────────────────────
    @router.get("/settings")
    async def get_webhook_settings(user: dict = Depends(current_user)):
        tok = await _get_or_create_token_doc(user["id"])
        total_orders = await db.webhook_orders.count_documents({"user_id": user["id"]})
        return {
            "token": tok["token"],
            "webhook_url": _public_webhook_url(tok["token"]),
            "last_sync_at": tok.get("last_sync_at"),
            "total_received": tok.get("total_received", 0),
            "total_orders_in_db": total_orders,
            "sample_payload": {
                "order_number": "12345",
                "order_date": "2026-02-15",
                "status": "completed",
                "customer_name": "Ahmed",
                "total_amount": 250.0,
                "discount": 10.0,
                "tax": 32.5,
                "payment_method": "مدى",
                "shipping_company": "سمسا",
                "products": [{"name": "منتج 1", "quantity": 2, "price": 100.0}],
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
        deleted = await db.webhook_orders.delete_many({"user_id": user["id"]})
        return {"ok": True, "deleted_orders": deleted.deleted_count}

    @router.get("/orders")
    async def list_orders(
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        limit: int = Query(100, ge=1, le=500),
        user: dict = Depends(current_user),
    ):
        q: dict = {"user_id": user["id"]}
        if date_from or date_to:
            q["order_date"] = {}
            if date_from:
                q["order_date"]["$gte"] = date_from
            if date_to:
                q["order_date"]["$lte"] = date_to
        cur = db.webhook_orders.find(q, {"_id": 0, "raw": 0}).sort("order_date", -1).limit(limit)
        items = await cur.to_list(limit)
        total = await db.webhook_orders.count_documents(q)
        return {"orders": items, "total": total, "limit": limit}

    @router.get("/stats")
    async def stats(user: dict = Depends(current_user)):
        tok = await db.webhook_tokens.find_one({"user_id": user["id"]}, {"_id": 0})
        if not tok:
            return {"connected": False}
        total = await db.webhook_orders.count_documents({"user_id": user["id"]})
        # earliest + latest order_date
        pipeline = [
            {"$match": {"user_id": user["id"], "order_date": {"$ne": None}}},
            {"$group": {"_id": None,
                        "min_date": {"$min": "$order_date"},
                        "max_date": {"$max": "$order_date"}}},
        ]
        rng = None
        async for doc in db.webhook_orders.aggregate(pipeline):
            rng = {"earliest": doc.get("min_date"), "latest": doc.get("max_date")}
        return {
            "connected": True,
            "total_orders_in_db": total,
            "total_received_ever": tok.get("total_received", 0),
            "last_sync_at": tok.get("last_sync_at"),
            "date_range": rng,
        }

    @router.post("/build-analysis")
    async def build_analysis(payload: BuildAnalysisIn, user: dict = Depends(current_user)):
        # Import inside to avoid circular import at module load
        from excel_parser import match_settings  # noqa: F401  (sanity check)
        from server import _build_report  # reuse the exact same pipeline

        try:
            datetime.strptime(payload.date_from, "%Y-%m-%d")
            datetime.strptime(payload.date_to, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="صيغة التاريخ يجب أن تكون YYYY-MM-DD")

        cur = db.webhook_orders.find(
            {
                "user_id": user["id"],
                "order_date": {"$gte": payload.date_from, "$lte": payload.date_to},
            },
            {"_id": 0, "raw": 0},
        )
        orders = await cur.to_list(50000)
        if not orders:
            raise HTTPException(
                status_code=400,
                detail=f"لا توجد طلبات من Make.com بين {payload.date_from} و {payload.date_to}",
            )

        parsed = _orders_to_parsed(orders)
        settings = await ensure_user_settings(db, user["id"])
        report = _build_report(
            parsed,
            settings.get("payment_methods", DEFAULT_PAYMENT_METHODS),
            settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
            payload.snapchat_ads, payload.tiktok_ads, payload.instagram_ads, payload.product_costs,
        )

        name = payload.name or f"Make.com {payload.date_from} → {payload.date_to}"
        analysis = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "name": name,
            "filename": f"make_{payload.date_from}_{payload.date_to}.json",
            "source": "make",
            "date_from": payload.date_from,
            "date_to": payload.date_to,
            "date": payload.date_to,
            "created_at": _now_iso(),
            "report": report,
        }
        await db.analyses.insert_one(analysis)
        analysis.pop("_id", None)
        return analysis

    return router


def attach_webhook_routes(parent_router: APIRouter, db) -> None:
    parent_router.include_router(_build_router(db))
