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

from tz_utils import riyadh_today
from typing import Optional, Union, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field

# iter-72: scrub apostrophes / BOM / zero-width chars on every webhook
# payload so Make.com data never adds duplicate rows to shipping_breakdown.
from shipping_companies import scrub_shipping_company as _scrub_shipping

from report_builder import build_report
from orders_db import upsert_order, orders_to_parsed
from product_costs import attach_cost_to_order_doc
from import_jobs import get_order_lock
from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION,
    attach_projected_salla_attribution,
)

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


# ── Diagnostic capture: log unparseable webhook bodies ────────────────
#
# The legacy `/make/{token}`, `/tiktok/{token}` and `/meta/{token}`
# endpoints reject any body that is not parseable JSON with 400
# "Invalid JSON". For months we had no way to know WHY a given Make.com
# execution was rejected — the bytes were dropped on the floor.
#
# This helper (added on 2026-06-26 after a 2026-06-19 production
# incident) writes a SMALL diagnostic record before the 400 propagates.
# It does NOT change behaviour: the request still fails, the response
# is still `{"detail":"Invalid JSON"}`. Only operators get a record.
#
# Privacy:
#   • Stores at most 2 KB of the body (truncated UTF-8 with replacement
#     for non-decodable bytes).
#   • Stores only the first 6 chars of the token (so an operator can
#     correlate the failure to a tenant without exposing the secret).
#   • Records the client IP (already in standard server logs anyway).
#
# Retention: a TTL index on `occurred_at` expires rows after 30 days.
async def _capture_parse_failure(
    db, request, token: str, exc: Exception,
) -> None:
    """Best-effort diagnostic capture. Never raises."""
    try:
        raw = await request.body()
        ip = request.client.host if getattr(request, "client", None) else None
        await db.webhook_parse_failures.insert_one({
            "occurred_at":     datetime.now(timezone.utc),
            "token_prefix":    (token or "")[:6] + "…",
            "content_type":    request.headers.get("content-type"),
            "content_length":  len(raw),
            "body_preview":    raw[:2048].decode("utf-8", errors="replace"),
            "parser_error":    f"{type(exc).__name__}: {exc}"[:512],
            "ip":              ip,
            "route":           str(request.url.path),
        })
    except Exception:
        # Diagnostic must never escalate or block the original 400.
        logger.exception("_capture_parse_failure swallowed an internal error")


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
    # Iteration 32: Salla's new order_created webhook ships line items
    # under `items[]` (instead of the older `products[]`). Accept both
    # field names — they're merged at parse time below.
    products: list[ProductItem] = []
    items: list[ProductItem] = []
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
        # Extended metrics (optional — Make.com TikTok scenarios usually send these)
        platform: Optional[str] = "tiktok"
        campaign_name: Optional[str] = None
        campaign_id: Optional[str] = None
        clicks: int = 0
        impressions: int = 0
        reach: int = 0
        video_views: int = 0
        conversions: int = 0     # alias for purchases when present
        cpa: float = 0.0

        class Config:
            extra = "allow"

    @router.post("/tiktok/{token}")
    async def ingest_tiktok(token: str, request: Request):
        """Ingest a single TikTok-Ads daily row pushed by Make.com.

        Body shape (per user spec):
            {"source":"tiktok","date":"2026-05-30","spend":350.75,
             "purchases":12,"revenue":2400.00}
        Or with full TikTok metrics:
            {"platform":"tiktok","date":"2026-05-20","campaign_name":"...",
             "campaign_id":"...","spend":59.83,"clicks":195,"impressions":12762,
             "reach":11315,"video_views":12573,"conversions":8,"cpa":7.48}

        Upserts into `tiktok_ads_daily` keyed by (user_id, date). When the
        same date arrives with multiple campaigns, the values ACCUMULATE
        (sum) so the daily totals reflect cross-campaign performance.
        Posting an identical (date, campaign_id) twice overwrites.
        """
        tok_doc = await db.webhook_tokens.find_one({"token": token})
        if not tok_doc:
            raise HTTPException(status_code=401, detail="Invalid webhook token")
        user_id = tok_doc["user_id"]
        try:
            body = await request.json()
        except Exception as _exc:
            await _capture_parse_failure(db, request, token, _exc)
            raise HTTPException(status_code=400, detail="Invalid JSON")

        # Allow a batch (list) or single object
        items = body if isinstance(body, list) else [body]
        accepted = 0
        errors: list[dict] = []
        synced_dates: set[str] = set()
        for raw in items:
            try:
                payload = TikTokSpendIn(**raw)
            except Exception as exc:
                logger.exception("TikTok webhook payload validation failed", exc_info=exc)
                errors.append({"data": raw, "error": "Invalid TikTok payload"})
                continue
            # Strict date format
            import re
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", payload.date):
                errors.append({"data": raw, "error": "date must be YYYY-MM-DD"})
                continue
            # When `conversions` is set but `purchases` isn't, treat them as
            # the same metric (Make.com TikTok scenarios use "conversions").
            effective_purchases = (
                int(payload.purchases) if payload.purchases else int(payload.conversions or 0)
            )
            base = {
                "user_id": user_id,
                "date": payload.date,
                "spend": round(float(payload.spend or 0), 2),
                "purchases": effective_purchases,
                "revenue": round(float(payload.revenue or 0), 2),
                "clicks": int(payload.clicks or 0),
                "impressions": int(payload.impressions or 0),
                "reach": int(payload.reach or 0),
                "video_views": int(payload.video_views or 0),
                "conversions": int(payload.conversions or 0),
                "cpa": round(float(payload.cpa or 0), 2),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            campaign_id = payload.campaign_id or "_default"
            campaign_key = {"user_id": user_id, "date": payload.date, "campaign_id": campaign_id}
            # Per-campaign upsert (overwrite same campaign+date with latest values)
            await db.tiktok_ads_daily.update_one(
                campaign_key,
                {"$set": {**base,
                          "campaign_id": campaign_id,
                          "campaign_name": payload.campaign_name or "",
                          "platform": payload.platform or "tiktok"},
                 "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": base["updated_at"]}},
                upsert=True,
            )
            accepted += 1
            synced_dates.add(payload.date)

        # Update webhook token stats for visibility on the UI
        await db.webhook_tokens.update_one(
            {"token": token},
            {"$set": {"last_sync_at": datetime.now(timezone.utc).isoformat()}},
        )
        # Make.com is the temporary TikTok transport, while Dashboard Advanced
        # reads spend strictly from ad_account_ledger. Reconcile only the exact
        # dates accepted above into the user's sole TikTok ad account. The shared
        # sync engine applies the cumulative delta, so campaign retries/overwrites
        # never append duplicate spend.
        ledger_sync: dict[str, Any] = {"status": "not_requested", "results": []}
        if synced_dates:
            tiktok_accounts = await db.counterparties.find(
                {"user_id": user_id, "kind": "ad_account", "ad_provider": "tiktok"},
                {"_id": 0, "id": 1},
            ).sort("created_at", 1).to_list(3)
            if len(tiktok_accounts) == 1:
                account_id = tiktok_accounts[0]["id"]
                ledger_results: list[dict] = []
                try:
                    # Lazy import avoids coupling route registration order.
                    from ad_account_routes import _run_sync_for_all

                    for spend_date in sorted(synced_dates):
                        ledger_results.extend(await _run_sync_for_all(
                            db,
                            user_id,
                            spend_date,
                            spend_date,
                            force=True,
                            provider_filter={"tiktok"},
                            include_make=True,
                            account_ids={account_id},
                        ))
                    ledger_sync = {"status": "synced", "results": ledger_results}
                except Exception as exc:
                    logger.exception("TikTok Make ledger reconciliation failed")
                    ledger_sync = {"status": "failed", "reason": "ledger_reconciliation_failed"}
            else:
                ledger_sync = {
                    "status": "skipped",
                    "reason": (
                        "missing_tiktok_ad_account" if not tiktok_accounts
                        else "ambiguous_tiktok_ad_accounts"
                    ),
                    "accounts_found": len(tiktok_accounts),
                }

        return {"accepted": accepted, "errors": errors, "ledger_sync": ledger_sync}

    @router.get("/tiktok/recent")
    async def tiktok_recent(days: int = Query(30, ge=1, le=365), user: dict = Depends(current_user)):
        from datetime import timedelta
        # Iter-140 — Asia/Riyadh calendar cutoff.
        cutoff = (riyadh_today() - timedelta(days=days - 1)).isoformat()
        items = await db.tiktok_ads_daily.find(
            {"user_id": user["id"], "date": {"$gte": cutoff}}, {"_id": 0}
        ).sort("date", -1).to_list(days)
        return {"items": items}

    # ─────────────────────────────────────────────────────────────────────────
    # Meta (Facebook + Instagram) Ads — push from Make.com once a day
    # ─────────────────────────────────────────────────────────────────────────

    class MetaSpendIn(BaseModel):
        """Meta Ads insights row pushed daily by Make.com (Facebook
        Marketing API). One POST per (date, campaign_id) — the endpoint
        upserts so re-runs of the Make scenario are safe."""
        platform: Optional[str] = "meta"
        date: str                                  # YYYY-MM-DD
        account_id: Optional[str] = None
        campaign_id: Optional[str] = None
        campaign_name: Optional[str] = None
        adset_id: Optional[str] = None
        adset_name: Optional[str] = None
        ad_id: Optional[str] = None
        ad_name: Optional[str] = None
        spend: float = 0.0
        impressions: int = 0
        clicks: int = 0
        cpc: float = 0.0
        cpm: float = 0.0
        ctr: float = 0.0
        purchases: int = 0
        purchase_value: float = 0.0

        class Config:
            extra = "allow"

    @router.post("/meta/{token}")
    async def ingest_meta(token: str, request: Request):
        """Ingest a Meta-Ads daily row pushed by Make.com.

        Body example (per user spec):
            {"platform":"meta","date":"2026-05-31","account_id":"...",
             "campaign_id":"...","campaign_name":"...",
             "spend":350.75,"impressions":12000,"clicks":250,
             "cpc":1.40,"cpm":29.20,"ctr":2.08,
             "purchases":8,"purchase_value":1200.50}

        Upserts into `meta_ads_daily` keyed by (user_id, date, campaign_id).
        Re-posting the same key overwrites the row (so Make.com can safely
        replay a day if needed)."""
        tok_doc = await db.webhook_tokens.find_one({"token": token})
        if not tok_doc:
            raise HTTPException(status_code=401, detail="Invalid webhook token")
        user_id = tok_doc["user_id"]
        try:
            body = await request.json()
        except Exception as _exc:
            await _capture_parse_failure(db, request, token, _exc)
            raise HTTPException(status_code=400, detail="Invalid JSON")

        items = body if isinstance(body, list) else [body]
        accepted = 0
        errors: list[dict] = []
        import re
        for raw in items:
            try:
                payload = MetaSpendIn(**raw)
            except Exception as exc:
                errors.append({"data": raw, "error": str(exc)})
                continue
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", payload.date):
                errors.append({"data": raw, "error": "date must be YYYY-MM-DD"})
                continue
            base = {
                "user_id": user_id,
                "date": payload.date,
                "account_id": payload.account_id or "",
                "campaign_id": payload.campaign_id or "_default",
                "campaign_name": payload.campaign_name or "",
                "adset_id": payload.adset_id or "",
                "adset_name": payload.adset_name or "",
                "ad_id": payload.ad_id or "",
                "ad_name": payload.ad_name or "",
                "spend": round(float(payload.spend or 0), 2),
                "impressions": int(payload.impressions or 0),
                "clicks": int(payload.clicks or 0),
                "cpc": round(float(payload.cpc or 0), 2),
                "cpm": round(float(payload.cpm or 0), 2),
                "ctr": round(float(payload.ctr or 0), 4),
                "purchases": int(payload.purchases or 0),
                "purchase_value": round(float(payload.purchase_value or 0), 2),
                "platform": payload.platform or "meta",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.meta_ads_daily.update_one(
                {"user_id": user_id, "date": payload.date,
                 "campaign_id": base["campaign_id"]},
                {"$set": base,
                 "$setOnInsert": {"id": str(uuid.uuid4()),
                                  "created_at": base["updated_at"]}},
                upsert=True,
            )
            accepted += 1

        await db.webhook_tokens.update_one(
            {"token": token},
            {"$set": {"last_sync_at": datetime.now(timezone.utc).isoformat()}},
        )
        return {"accepted": accepted, "errors": errors}

    @router.get("/meta/recent")
    async def meta_recent(days: int = Query(30, ge=1, le=365), user: dict = Depends(current_user)):
        from datetime import timedelta
        # Iter-140 — Asia/Riyadh calendar cutoff.
        cutoff = (riyadh_today() - timedelta(days=days - 1)).isoformat()
        items = await db.meta_ads_daily.find(
            {"user_id": user["id"], "date": {"$gte": cutoff}}, {"_id": 0}
        ).sort("date", -1).to_list(days * 50)
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
        except Exception as _exc:
            await _capture_parse_failure(db, request, token, _exc)
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
        accepted_inferred_date = 0
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
            # Fallback to received date (today) when Make.com sends a payload
            # without any date field. We mark such rows with
            # `order_date_inferred=True` so the UI can highlight them and so
            # Excel re-imports (which always carry the authoritative
            # created_at) can later overwrite the inferred value.
            #
            # IMPORTANT TRADE-OFF: this restores the convenience of
            # auto-appearing orders in the dashboard, BUT it means that if
            # Make.com replays old orders without created_at, those orders
            # will be tagged with today's date. Fix your Make.com scenario
            # to pass `created_at` for authoritative dating.
            authoritative_date = (
                _normalize_order_date(payload.created_at)
                or _normalize_order_date(payload.order_date)
                or _normalize_order_date(raw.get("created_at"))
                or _normalize_order_date(raw.get("order_date"))
                or _normalize_order_date(raw.get("purchase_date"))
                or _normalize_order_date(raw.get("date"))
            )
            if authoritative_date:
                order_date_norm = authoritative_date
                order_date_inferred = False
            else:
                # Iter-177 — when Make.com sends no date, fall back to
                # the current Riyadh calendar day (not UTC). UTC fallback
                # would silently roll back to "yesterday" between 21:00
                # and 24:00 UTC = 00:00–03:00 KSA.
                order_date_norm = riyadh_today().isoformat()
                order_date_inferred = True

            incoming = {
                # Identifiers
                "order_id": str(payload.order_id or "").strip(),
                # Dates
                "order_date": order_date_norm,
                "order_date_raw": (payload.created_at or payload.order_date or "").strip(),
                "order_date_inferred": order_date_inferred,
                # Status
                "order_status": (payload.order_status or payload.status or "").strip(),
                "order_status_slug": (payload.order_status_slug or "").strip(),
                "payment_status": (payload.payment_status or "").strip(),
                # Customer
                "customer_name": (payload.customer_name or "").strip(),
                "customer_mobile": (payload.customer_mobile or "").strip(),
                # Payment
                "payment_method": (payload.payment_method or "").strip(),
                # Shipping — iter-72 scrub apostrophes / BOM at write boundary
                "shipping_company": _scrub_shipping(payload.shipping_company),
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
            }
            # Items + product-cost lookup
            # Iteration 32: merge `items[]` into `products[]`. Salla's
            # new webhook payload ships line items under `items[]`
            # without SKU or product_id — only name + quantity. We
            # normalise both shapes here so downstream code can keep
            # using a single canonical `products` list. Cost matching
            # falls back to name-based lookup (iteration 32) when SKU
            # and product_id are absent.
            merged_items_raw = list(payload.products or []) + list(payload.items or [])
            normalised_products = []
            for p in merged_items_raw:
                d = p.dict() if hasattr(p, "dict") else dict(p)
                name = str(d.get("name") or d.get("product_name") or "").strip()
                if not name:
                    continue
                qty = _to_float(d.get("quantity"), 1.0)
                if qty <= 0:
                    qty = 1.0
                normalised_products.append({
                    "name": name,
                    "quantity": qty,
                    "price": _to_float(d.get("price"), 0.0),
                    "sku": str(d.get("sku") or "").strip(),
                    "product_id": str(d.get("product_id") or d.get("id") or "").strip(),
                    "image_url": str(d.get("image_url") or d.get("image") or "").strip(),
                })
            incoming["products"] = normalised_products
            incoming["tags"] = [str(t).strip() for t in (payload.tags or []) if str(t).strip()]
            # Per-order lock — serialises Excel + Make writes to the SAME
            # order while leaving other orders free to process in parallel.
            lock = get_order_lock(user_id, order_number)
            async with lock:
                res = await upsert_order(
                    db, user_id, order_number, incoming, source="make", raw=raw,
                )
            if res["created"]:
                accepted += 1
            else:
                updated += 1
            if order_date_inferred:
                accepted_inferred_date += 1

            # ── Attach product cost (iteration 19) ─────────────────────────
            # After the order is persisted, look up per-SKU cost and write
            # the computed cost back to the order doc. Best-effort — a
            # missing cost on a single product never fails the ingestion.
            try:
                cost_patch = await attach_cost_to_order_doc(
                    db, user_id, {"products": incoming.get("products") or []},
                )
                await db.unified_orders.update_one(
                    {"user_id": user_id, "order_number": order_number},
                    {"$set": cost_patch},
                )
            except Exception as exc:
                logger.warning("Product-cost attach failed for order %s: %s",
                               order_number, exc)

        await db.webhook_tokens.update_one(
            {"user_id": user_id},
            {"$set": {"last_sync_at": _now_iso()},
             "$inc": {"total_received": accepted + updated}},
        )

        return {
            "ok": True,
            "accepted": accepted,
            "updated": updated,
            "inferred_date": accepted_inferred_date,
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

    # ── PUBLIC TOKEN HEALTH ─────────────────────────────────────────────
    # No auth — Make.com / the merchant can probe these to diagnose
    # "service rejected the webhook token" errors before re-binding.
    @router.get("/validate-token/{token}")
    async def validate_token(token: str):
        """Iter-88 — Health check used by the Settings UI (and Make
        scenario debugger) to confirm a token is recognised by THIS
        environment. Returns 200 with valid=False instead of 401 so
        the caller doesn't have to parse error codes."""
        tok_doc = await db.webhook_tokens.find_one(
            {"token": token},
            {"_id": 0, "token": 1, "created_at": 1, "last_sync_at": 1,
             "total_received": 1, "user_id": 1},
        )
        if not tok_doc:
            return {
                "valid": False,
                "reason": "token_not_found_in_this_environment",
                "environment": (os.environ.get("FRONTEND_URL") or "").rstrip("/"),
            }
        return {
            "valid": True,
            "environment": (os.environ.get("FRONTEND_URL") or "").rstrip("/"),
            "created_at": tok_doc.get("created_at"),
            "last_sync_at": tok_doc.get("last_sync_at"),
            "total_received": tok_doc.get("total_received", 0),
            "webhook_url": _public_webhook_url(token),
        }

    @router.post("/ping/{token}")
    async def webhook_ping(token: str):
        """Iter-88 — Simulates a Make.com call without ingesting an
        order. Lets the merchant click 'Test webhook' from Settings
        and immediately see whether the token is accepted by THIS
        environment (preview vs production)."""
        tok_doc = await db.webhook_tokens.find_one({"token": token})
        if not tok_doc:
            raise HTTPException(
                status_code=401,
                detail={
                    "ok": False,
                    "reason": "token_not_found",
                    "environment": (os.environ.get("FRONTEND_URL") or "").rstrip("/"),
                    "hint": (
                        "هذا الرمز غير معروف في هذه البيئة. على الأرجح "
                        "أنشأت الرمز في بيئة أخرى (Preview/Production). "
                        "افتح صفحة الإعدادات → بوابة Make.com وأنشئ رمزًا جديدًا "
                        "هنا، ثم انسخه إلى سيناريو Make."
                    ),
                },
            )
        # bump a ping counter
        await db.webhook_tokens.update_one(
            {"user_id": tok_doc["user_id"]},
            {"$set": {"last_ping_at": _now_iso()},
             "$inc": {"ping_count": 1}},
        )
        return {
            "ok": True,
            "valid": True,
            "user_id": tok_doc["user_id"],
            "environment": (os.environ.get("FRONTEND_URL") or "").rstrip("/"),
            "webhook_url": _public_webhook_url(token),
            "received_at": _now_iso(),
        }

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
        # Orders whose date was inferred from received_at (because Make.com
        # didn't send created_at). These appear in dashboard tagged with
        # an approximate date — the merchant should fix the Make.com mapping
        # to send the authoritative date.
        inferred_date = await db.unified_orders.count_documents({
            "user_id": user["id"],
            "order_date_inferred": True,
        })
        return {
            "connected": bool(tok),
            "total_orders_in_db": total,
            "total_received_ever": (tok or {}).get("total_received", 0),
            "last_sync_at": (tok or {}).get("last_sync_at"),
            "date_range": rng,
            "by_source": per_source,
            "orders_missing_date": missing_date,
            "orders_inferred_date": inferred_date,
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

        attribution_query = {
            "user_id": user["id"],
            "order_date": {"$gte": payload.date_from, "$lte": payload.date_to},
        }
        attribution_rows = await db.unified_orders.find(
            attribution_query,
            SALLA_RAW_ATTRIBUTION_PROJECTION,
        ).to_list(50000)
        attach_projected_salla_attribution(orders, attribution_rows)

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
