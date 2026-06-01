"""Hesab — accounting backend for Salla-platform analytics."""
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

try:
    from zoneinfo import ZoneInfo
    RIYADH_TZ = ZoneInfo("Asia/Riyadh")
except ImportError:  # pragma: no cover
    RIYADH_TZ = timezone(timedelta(hours=3))  # fallback to fixed UTC+3


def _local_today_iso() -> str:
    """Return today's date in Asia/Riyadh timezone as YYYY-MM-DD.
    Saudi merchants operate in Riyadh time; the Snapchat/Meta bulk fetchers
    and the browser save dates in this local timezone, so all dashboard
    aggregations must read from it too (otherwise a refresh at 02:00 AM
    Riyadh time saves under the next-day's UTC date and the dashboard
    shows 0 until 03:00 UTC = 06:00 Riyadh)."""
    return datetime.now(RIYADH_TZ).date().isoformat()


def _local_today_date():
    """Same as _local_today_iso but returns a date object."""
    return datetime.now(RIYADH_TZ).date()

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, UploadFile, File
from fastapi.responses import StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, Field

from auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    set_auth_cookies,
    clear_auth_cookies,
    get_current_user_from_db,
    seed_admin,
    ensure_user_settings,
    DEFAULT_PAYMENT_METHODS,
    DEFAULT_SHIPPING_COMPANIES,
)
from excel_parser import parse_salla_excel, match_settings
from exports import export_report_excel, export_report_pdf
from report_builder import build_report as _build_report
from snapchat_routes import attach_snapchat_routes
from meta_routes import attach_meta_routes
from shipping_accounts import attach_shipping_accounts_routes
from webhook_routes import attach_webhook_routes
from product_costs import attach_product_costs_routes, attach_cost_to_order_doc
from expenses_routes import (
    attach_operating_expenses_routes,
    compute_operating_expenses_for_range,
)
from orders_db import upsert_order, orders_to_parsed
from balances import compute_balances


def _normalize_date_str(s: str) -> Optional[str]:
    """Salla dates: 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD'. Return YYYY-MM-DD or None."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return None


def _parse_date_or(s: Optional[str], fallback):
    """Parse YYYY-MM-DD to a date object, falling back when invalid/empty."""
    if not s:
        return fallback
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback


# ── Database ──────────────────────────────────────────────────────────────────
mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]


# ── App / Router ──────────────────────────────────────────────────────────────
app = FastAPI(title="Hesab — Salla Accounting API")
api = APIRouter(prefix="/api")


# ── Dependencies ──────────────────────────────────────────────────────────────
async def current_user(request: Request) -> dict:
    return await get_current_user_from_db(request, db)


# ── Schemas ───────────────────────────────────────────────────────────────────
class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class PaymentMethod(BaseModel):
    name: str
    commission_percent: float = Field(ge=0, le=100, default=0.0)
    fixed_fee: float = Field(ge=0, default=0.0)
    vat_percent: float = Field(ge=0, le=100, default=0.0)


class ShippingCompany(BaseModel):
    name: str
    cost_per_order: float = Field(ge=0)
    vat_percent: float = Field(ge=0, le=100, default=0.0)
    is_deferred: bool = False  # if True: cost not deducted from Salla→bank transfer (accounts payable)


class NetSalesConfig(BaseModel):
    """Phase 3 — controls which line items get subtracted from total_sales
    when computing the "صافي المبيعات" (net sales) KPI shown in dashboard.

    Each flag is independent so the merchant can model their accounting
    preference (e.g. some count VAT as part of revenue, others don't).
    Defaults reflect the most common Salla seller workflow."""
    deduct_payment_fees: bool = True       # عمولات بوابات الدفع
    deduct_shipping: bool = True           # تكاليف الشحن (الفورية فقط)
    deduct_deferred_shipping: bool = False  # تكاليف الشحن الآجلة
    deduct_ads: bool = True                # تكاليف الإعلانات اليومية
    deduct_product_costs: bool = True      # تكاليف المنتجات
    deduct_vat: bool = False               # ضريبة القيمة المضافة
    deduct_daily_expenses: bool = False    # مصاريف يومية أخرى
    deduct_operating_expenses: bool = True  # المصروفات التشغيلية (رواتب + إيجارات + يومية أخرى)


DEFAULT_NET_SALES_CONFIG = NetSalesConfig().model_dump()


class SettingsIn(BaseModel):
    payment_methods: List[PaymentMethod]
    shipping_companies: List[ShippingCompany]
    # NEW (Phase 1): which order_status values are "approved" for accounting purposes
    shipping_approved_statuses: Optional[List[str]] = None
    cod_approved_statuses: Optional[List[str]] = None
    # NEW: which order statuses are counted in dashboard/reports KPIs.
    # Empty list = include ALL statuses (backwards compatible default).
    report_included_statuses: Optional[List[str]] = None
    # NEW (Phase 5): per-user list of dashboard KPI card ids to hide.
    dashboard_hidden_cards: Optional[List[str]] = None
    # NEW (Phase 3): toggles for what gets deducted from "net sales" KPI.
    net_sales_config: Optional[NetSalesConfig] = None
    # NEW: hide Make.com orders with inferred (approximate) date from dashboard/reports.
    # When True, only orders with authoritative date (from Excel or Make.com
    # webhook that included created_at) are counted in dashboard KPIs.
    hide_inferred_date_orders: Optional[bool] = None


class DailyCostsIn(BaseModel):
    date: str  # YYYY-MM-DD
    snapchat_ads: float = 0.0
    snapchat_ads_2: float = 0.0
    tiktok_ads: float = 0.0
    instagram_ads: float = 0.0
    google_ads: float = 0.0
    product_costs: float = 0.0
    notes: Optional[str] = ""


class AnalysisCreate(BaseModel):
    name: Optional[str] = ""
    date: Optional[str] = None  # YYYY-MM-DD; defaults to today
    snapchat_ads: float = 0.0
    tiktok_ads: float = 0.0
    instagram_ads: float = 0.0
    product_costs: float = 0.0


# ── Auth Routes ───────────────────────────────────────────────────────────────
@api.post("/auth/register")
async def register(payload: RegisterIn, response: Response):
    email = payload.email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="هذا البريد الإلكتروني مسجل بالفعل")
    user = {
        "id": str(uuid.uuid4()),
        "name": payload.name,
        "email": email,
        "password_hash": hash_password(payload.password),
        "role": "user",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(user)
    await ensure_user_settings(db, user["id"])
    access = create_access_token(user["id"], user["email"])
    refresh = create_refresh_token(user["id"])
    set_auth_cookies(response, access, refresh)
    return {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"], "access_token": access}


@api.post("/auth/login")
async def login(payload: LoginIn, response: Response):
    email = payload.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="البريد الإلكتروني أو كلمة المرور غير صحيحة")
    await ensure_user_settings(db, user["id"])
    access = create_access_token(user["id"], user["email"])
    refresh = create_refresh_token(user["id"])
    set_auth_cookies(response, access, refresh)
    return {"id": user["id"], "name": user["name"], "email": user["email"], "role": user.get("role", "user"), "access_token": access}


@api.post("/auth/logout")
async def logout(response: Response, user: dict = Depends(current_user)):
    clear_auth_cookies(response)
    return {"ok": True}


@api.get("/auth/me")
async def me(user: dict = Depends(current_user)):
    return {"id": user["id"], "name": user.get("name"), "email": user["email"], "role": user.get("role", "user")}


# ── Settings ──────────────────────────────────────────────────────────────────
DEFAULT_SHIPPING_APPROVED = ["تم التوصيل", "delivered", "completed", "تم الاستلام"]
DEFAULT_COD_APPROVED = ["تم التوصيل", "delivered", "completed"]


@api.get("/settings")
async def get_settings(user: dict = Depends(current_user)):
    s = await ensure_user_settings(db, user["id"])
    return {
        "payment_methods": s.get("payment_methods", DEFAULT_PAYMENT_METHODS),
        "shipping_companies": s.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
        "shipping_approved_statuses": s.get("shipping_approved_statuses", DEFAULT_SHIPPING_APPROVED),
        "cod_approved_statuses": s.get("cod_approved_statuses", DEFAULT_COD_APPROVED),
        "report_included_statuses": s.get("report_included_statuses", []),
        "dashboard_hidden_cards": s.get("dashboard_hidden_cards", []),
        "net_sales_config": s.get("net_sales_config", DEFAULT_NET_SALES_CONFIG),
        "hide_inferred_date_orders": bool(s.get("hide_inferred_date_orders", False)),
    }


@api.put("/settings")
async def update_settings(payload: SettingsIn, user: dict = Depends(current_user)):
    update_doc = {
        "user_id": user["id"],
        "payment_methods": [pm.model_dump() for pm in payload.payment_methods],
        "shipping_companies": [sc.model_dump() for sc in payload.shipping_companies],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if payload.shipping_approved_statuses is not None:
        update_doc["shipping_approved_statuses"] = [s.strip() for s in payload.shipping_approved_statuses if s.strip()]
    if payload.cod_approved_statuses is not None:
        update_doc["cod_approved_statuses"] = [s.strip() for s in payload.cod_approved_statuses if s.strip()]
    if payload.report_included_statuses is not None:
        update_doc["report_included_statuses"] = [s.strip() for s in payload.report_included_statuses if s.strip()]
    if payload.dashboard_hidden_cards is not None:
        update_doc["dashboard_hidden_cards"] = [s.strip() for s in payload.dashboard_hidden_cards if s.strip()]
    if payload.net_sales_config is not None:
        update_doc["net_sales_config"] = payload.net_sales_config.model_dump()
    if payload.hide_inferred_date_orders is not None:
        update_doc["hide_inferred_date_orders"] = bool(payload.hide_inferred_date_orders)
    await db.settings.update_one(
        {"user_id": user["id"]},
        {"$set": update_doc},
        upsert=True,
    )
    return {"ok": True}


@api.get("/order-statuses")
async def list_order_statuses(user: dict = Depends(current_user)):
    """Return distinct order_status values observed in the user's unified_orders
    plus their counts. Used by Settings to power the multi-select that decides
    which statuses are included in dashboard/reports KPIs.
    """
    pipeline = [
        {"$match": {"user_id": user["id"]}},
        {"$group": {"_id": "$order_status", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    items = []
    async for doc in db.unified_orders.aggregate(pipeline):
        name = (doc.get("_id") or "").strip()
        if not name:
            continue
        items.append({"name": name, "count": int(doc.get("count") or 0)})

    # Also surface statuses present only in legacy analyses (orders_sample)
    # so the user can still configure them.
    seen = {i["name"] for i in items}
    async for a in db.analyses.find({"user_id": user["id"]}, {"_id": 0, "report.orders_sample": 1}):
        for o in (a.get("report", {}) or {}).get("orders_sample", []) or []:
            st = (o.get("status") or "").strip()
            if st and st not in seen:
                items.append({"name": st, "count": 0})
                seen.add(st)
    return {"statuses": items}


@api.get("/shipping-companies/discover")
async def discover_shipping_companies(user: dict = Depends(current_user)):
    """Compare shipping_companies configured in settings vs the company values
    actually present in `unified_orders`.

    Returns:
      configured: list of {name, cost, vat_rate, is_deferred, status}
        status ∈ {"ok", "missing_cost"} — flag for the UI.
      observed: distinct shipping_company strings in user's orders.
      unconfigured: observed names that don't match any configured company
        (case-insensitive partial match). UI can offer "Add to settings".
    """
    settings = await ensure_user_settings(db, user["id"])
    configured_raw = settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES) or []

    # Aggregate distinct shipping companies from orders + their order counts
    pipeline = [
        {"$match": {"user_id": user["id"]}},
        {"$group": {
            "_id": "$shipping_company",
            "count": {"$sum": 1},
            "sum_cost": {"$sum": {"$ifNull": ["$shipping_cost", 0]}},
        }},
        {"$sort": {"count": -1}},
    ]
    observed_raw: list[dict] = []
    async for doc in db.unified_orders.aggregate(pipeline):
        name = (doc.get("_id") or "").strip()
        if not name:
            continue
        observed_raw.append({
            "name": name,
            "orders_count": int(doc.get("count") or 0),
            "sum_shipping_cost": round(float(doc.get("sum_cost") or 0), 2),
            "avg_shipping_cost": round((doc.get("sum_cost") or 0) / max(int(doc.get("count") or 0), 1), 2),
        })

    # Build a normalised lookup from settings to match observed names
    def _norm(s: str) -> str:
        return (s or "").strip().lower()

    cfg_index = {}
    for c in configured_raw:
        n = (c.get("name") or "").strip()
        if n:
            cfg_index[_norm(n)] = c

    def _resolve(observed_name: str):
        n = _norm(observed_name)
        if n in cfg_index:
            return cfg_index[n]
        # substring match
        for k, c in cfg_index.items():
            if k and (k in n or n in k):
                return c
        return None

    # Configured list with status flag
    configured = []
    for c in configured_raw:
        cost = c.get("cost")
        # None or 0 → flag as missing_cost
        try:
            cost_f = float(cost) if cost is not None else 0.0
        except (TypeError, ValueError):
            cost_f = 0.0
        configured.append({
            "name": (c.get("name") or "").strip(),
            "cost": cost_f,
            "vat_rate": float(c.get("vat_rate") or 0),
            "is_deferred": bool(c.get("is_deferred")),
            "status": "ok" if cost_f > 0 else "missing_cost",
        })

    # Unconfigured = observed but no settings entry matches
    unconfigured = []
    for o in observed_raw:
        if _resolve(o["name"]) is None:
            unconfigured.append(o)

    return {
        "configured": configured,
        "observed": observed_raw,
        "unconfigured": unconfigured,
    }


class ShippingAutoDiscoverIn(BaseModel):
    # Optional whitelist of names to add. If omitted, adds ALL unconfigured.
    names: Optional[List[str]] = None


@api.post("/shipping-companies/autodiscover")
async def autodiscover_shipping_companies(
    payload: ShippingAutoDiscoverIn,
    user: dict = Depends(current_user),
):
    """Append shipping company entries to settings for every name observed
    in the user's orders that isn't already configured.

    New entries default to: cost = avg_shipping_cost from orders (rounded),
    vat_rate = 0.15, is_deferred = False. The user can then fine-tune
    each row from the Settings UI.
    """
    discover = await discover_shipping_companies(user)
    settings = await ensure_user_settings(db, user["id"])
    existing = list(settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES) or [])

    target_names = set((payload.names or [])) if payload.names is not None else None
    added = []
    for u_item in discover["unconfigured"]:
        name = u_item["name"]
        if target_names is not None and name not in target_names:
            continue
        cost = float(u_item.get("avg_shipping_cost") or 0)
        existing.append({
            "name": name,
            "cost": round(cost, 2),
            "vat_rate": 0.15,
            "is_deferred": False,
        })
        added.append({"name": name, "cost": round(cost, 2)})

    if added:
        await db.settings.update_one(
            {"user_id": user["id"]},
            {"$set": {
                "shipping_companies": existing,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
    return {"added": added, "count": len(added)}


@api.get("/balances")
async def balances_endpoint(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    payment_methods: Optional[str] = None,
    shipping_companies: Optional[str] = None,
    user: dict = Depends(current_user),
):
    """Shipping & COD balance splits (approved/unapproved) based on order_status."""
    s = await ensure_user_settings(db, user["id"])
    shipping_approved = s.get("shipping_approved_statuses", DEFAULT_SHIPPING_APPROVED)
    cod_approved = s.get("cod_approved_statuses", DEFAULT_COD_APPROVED)

    pm_list = [x.strip() for x in (payment_methods or "").split(",") if x.strip()]
    ship_list = [x.strip() for x in (shipping_companies or "").split(",") if x.strip()]

    q = {"user_id": user["id"]}
    if from_date or to_date:
        q["order_date"] = {}
        if from_date:
            q["order_date"]["$gte"] = from_date
        if to_date:
            q["order_date"]["$lte"] = to_date
    if s.get("hide_inferred_date_orders"):
        q["order_date_inferred"] = {"$ne": True}

    orders = await db.unified_orders.find(q, {"_id": 0, "raw_by_source": 0}).to_list(50000)
    if pm_list or ship_list:
        def _any(v, allowed):
            if not allowed:
                return True
            vv = (v or "").strip().lower()
            return any(a.strip().lower() in vv or vv in a.strip().lower() for a in allowed if a.strip())
        orders = [
            o for o in orders
            if _any(o.get("payment_method", ""), pm_list)
            and _any(o.get("shipping_company", ""), ship_list)
        ]
    # Honour the user-configured "report_included_statuses" filter so balances
    # are computed only on the same scope as dashboard/reports.
    included_statuses = s.get("report_included_statuses") or []
    if included_statuses:
        def _any2(v, allowed):
            vv = (v or "").strip().lower()
            return any(a.strip().lower() in vv or vv in a.strip().lower() for a in allowed if a.strip())
        orders = [o for o in orders if _any2(o.get("order_status", ""), included_statuses)]
    return compute_balances(orders, shipping_approved, cod_approved)


# ── Daily Costs ───────────────────────────────────────────────────────────────
@api.get("/daily-costs")
async def list_daily_costs(user: dict = Depends(current_user)):
    items = await db.daily_costs.find({"user_id": user["id"]}, {"_id": 0}).sort("date", -1).to_list(1000)
    return items


@api.post("/daily-costs")
async def upsert_daily_costs(payload: DailyCostsIn, user: dict = Depends(current_user)):
    doc = {
        "user_id": user["id"],
        "date": payload.date,
        "snapchat_ads": payload.snapchat_ads,
        "snapchat_ads_2": payload.snapchat_ads_2,
        "tiktok_ads": payload.tiktok_ads,
        "instagram_ads": payload.instagram_ads,
        "google_ads": payload.google_ads,
        "product_costs": payload.product_costs,
        "notes": payload.notes or "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.daily_costs.update_one(
        {"user_id": user["id"], "date": payload.date},
        {"$set": doc, "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    doc["id"] = (await db.daily_costs.find_one({"user_id": user["id"], "date": payload.date}, {"_id": 0, "id": 1}))["id"]
    return doc


@api.delete("/daily-costs/{date}")
async def delete_daily_costs(date: str, user: dict = Depends(current_user)):
    await db.daily_costs.delete_one({"user_id": user["id"], "date": date})
    return {"ok": True}


# ── Analyses (Excel upload) ───────────────────────────────────────────────────


@api.post("/analyses")
async def create_analysis(
    file: UploadFile = File(...),
    name: str = "",
    date: Optional[str] = None,
    snapchat_ads: float = 0.0,
    tiktok_ads: float = 0.0,
    instagram_ads: float = 0.0,
    product_costs: float = 0.0,
    user: dict = Depends(current_user),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".xlsm")):
        raise HTTPException(status_code=400, detail="يرجى رفع ملف Excel بصيغة .xlsx")
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="الملف فارغ")
    try:
        parsed = parse_salla_excel(content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.exception("excel parse error")
        raise HTTPException(status_code=400, detail=f"تعذر قراءة الملف: {e}")

    settings = await ensure_user_settings(db, user["id"])
    report = _build_report(
        parsed,
        settings.get("payment_methods", DEFAULT_PAYMENT_METHODS),
        settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
        snapchat_ads, tiktok_ads, instagram_ads, product_costs,
    )

    # Upsert every individual order into the unified orders store so Excel
    # data joins the same pipeline as Make.com webhook orders. Dedupes by
    # order_number and merges fields source-by-source.
    excel_individual = parsed.get("orders_individual") or []
    orders_imported = 0
    orders_updated = 0
    for o in excel_individual:
        order_number = (o.get("order_number") or "").strip()
        if not order_number:
            continue
        # Normalize Salla date strings (e.g. "2026-02-15 14:30:00") → YYYY-MM-DD
        order_date = _normalize_date_str(o.get("order_date_raw") or "")
        incoming = {
            "order_id": o.get("order_id") or "",
            "order_date": order_date,
            "order_date_raw": o.get("order_date_raw") or "",
            "order_date_inferred": False,  # Excel = authoritative Salla export
            "order_status": o.get("order_status") or "",
            "customer_name": o.get("customer_name") or "",
            "customer_mobile": o.get("customer_mobile") or "",
            "payment_method": o.get("payment_method") or "",
            "shipping_company": o.get("shipping_company") or "",
            "shipping_cost": float(o.get("shipping_cost") or 0),
            "subtotal": float(o.get("subtotal") or 0),
            "discount": float(o.get("discount") or 0),
            "total_amount": float(o.get("total_amount") or 0),
            "currency": o.get("currency") or "",
            "source": o.get("source") or "",
        }
        res = await upsert_order(db, user["id"], order_number, incoming, source="excel", raw=o)
        if res["created"]:
            orders_imported += 1
        else:
            orders_updated += 1

    analysis = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "name": name or file.filename,
        "filename": file.filename,
        "source": "excel",
        "date": date or datetime.now(timezone.utc).date().isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report": report,
        "orders_imported": orders_imported,
        "orders_updated": orders_updated,
    }
    await db.analyses.insert_one(analysis)
    analysis.pop("_id", None)
    return analysis


@api.get("/analyses")
async def list_analyses(user: dict = Depends(current_user)):
    items = await db.analyses.find(
        {"user_id": user["id"]},
        {"_id": 0, "report.orders_sample": 0},
    ).sort("created_at", -1).to_list(500)
    return items


@api.get("/analyses/{analysis_id}")
async def get_analysis(analysis_id: str, user: dict = Depends(current_user)):
    item = await db.analyses.find_one({"id": analysis_id, "user_id": user["id"]}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="التحليل غير موجود")
    return item


@api.delete("/analyses/{analysis_id}")
async def delete_analysis(analysis_id: str, user: dict = Depends(current_user)):
    res = await db.analyses.delete_one({"id": analysis_id, "user_id": user["id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="التحليل غير موجود")
    return {"ok": True}


@api.post("/analyses/{analysis_id}/reprocess")
async def reprocess_analysis(
    analysis_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
):
    """Re-upload the original Excel file for a legacy analysis.

    Re-parses the file with the current parser (which captures column Q as the
    order-creation date) and writes every individual order into
    `unified_orders`. Replaces the legacy analysis with an updated record that
    includes `orders_imported > 0` so the dashboard's legacy fallback no
    longer counts this analysis twice.
    """
    existing = await db.analyses.find_one({"id": analysis_id, "user_id": user["id"]}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="التحليل غير موجود")

    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".xlsm")):
        raise HTTPException(status_code=400, detail="يرجى رفع ملف Excel بصيغة .xlsx")
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="الملف فارغ")
    try:
        parsed = parse_salla_excel(content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.exception("reprocess parse error")
        raise HTTPException(status_code=400, detail=f"تعذر قراءة الملف: {e}")

    settings = await ensure_user_settings(db, user["id"])
    report = _build_report(
        parsed,
        settings.get("payment_methods", DEFAULT_PAYMENT_METHODS),
        settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
        0.0, 0.0, 0.0, 0.0,
    )

    excel_individual = parsed.get("orders_individual") or []
    orders_imported = 0
    orders_updated = 0
    for o in excel_individual:
        order_number = (o.get("order_number") or "").strip()
        if not order_number:
            continue
        order_date = _normalize_date_str(o.get("order_date_raw") or "")
        incoming = {
            "order_id": o.get("order_id") or "",
            "order_date": order_date,
            "order_date_raw": o.get("order_date_raw") or "",
            "order_date_inferred": False,  # Excel = authoritative Salla export
            "order_status": o.get("order_status") or "",
            "customer_name": o.get("customer_name") or "",
            "customer_mobile": o.get("customer_mobile") or "",
            "payment_method": o.get("payment_method") or "",
            "shipping_company": o.get("shipping_company") or "",
            "shipping_cost": float(o.get("shipping_cost") or 0),
            "subtotal": float(o.get("subtotal") or 0),
            "discount": float(o.get("discount") or 0),
            "total_amount": float(o.get("total_amount") or 0),
            "currency": o.get("currency") or "",
            "source": o.get("source") or "",
        }
        res = await upsert_order(db, user["id"], order_number, incoming, source="excel", raw=o)
        if res["created"]:
            orders_imported += 1
        else:
            orders_updated += 1

    # Replace the analysis in-place (preserve id/name/date for continuity)
    await db.analyses.update_one(
        {"id": analysis_id, "user_id": user["id"]},
        {"$set": {
            "filename": file.filename,
            "report": report,
            "orders_imported": orders_imported,
            "orders_updated": orders_updated,
            "reprocessed_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    return {
        "ok": True,
        "orders_imported": orders_imported,
        "orders_updated": orders_updated,
        "analysis_id": analysis_id,
    }


@api.get("/analyses/{analysis_id}/export/excel")
async def export_excel(analysis_id: str, user: dict = Depends(current_user)):
    item = await db.analyses.find_one({"id": analysis_id, "user_id": user["id"]}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="التحليل غير موجود")
    blob = export_report_excel(item["report"])
    return StreamingResponse(
        iter([blob]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=hesab-report-{analysis_id}.xlsx"},
    )


@api.get("/analyses/{analysis_id}/export/pdf")
async def export_pdf(analysis_id: str, user: dict = Depends(current_user)):
    item = await db.analyses.find_one({"id": analysis_id, "user_id": user["id"]}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="التحليل غير موجود")
    blob = export_report_pdf(item["report"])
    return StreamingResponse(
        iter([blob]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=hesab-report-{analysis_id}.pdf"},
    )


# ── Dashboard aggregate ───────────────────────────────────────────────────────
@api.get("/dashboard")
async def dashboard(
    user: dict = Depends(current_user),
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    payment_methods: Optional[str] = None,
    shipping_companies: Optional[str] = None,
):
    """Return aggregated totals from unified_orders (single source of truth).

    All KPI cards reflect actual order-level data filtered by each order's
    own `order_date` (i.e. the date the order was created on Salla). Excel
    uploads and Make.com webhook orders are both included.

    Optional filters:
      - from_date / to_date (YYYY-MM-DD) on per-order `order_date`
      - payment_methods: comma-separated names
      - shipping_companies: comma-separated names

    `daily_costs` are still filtered by the day (`date`) the cost is logged
    against, and `recent_analyses` returns the 5 most recent uploads as a
    convenience list.
    """
    pm_list = [s.strip() for s in (payment_methods or "").split(",") if s.strip()]
    ship_list = [s.strip() for s in (shipping_companies or "").split(",") if s.strip()]

    def _matches_any(value: str, allowed: list[str]) -> bool:
        if not allowed:
            return True
        v = (value or "").strip().lower()
        for a in allowed:
            a_lc = a.strip().lower()
            if a_lc and (a_lc == v or a_lc in v or v in a_lc):
                return True
        return False

    settings = await ensure_user_settings(db, user["id"])

    # ── Unified orders aggregation (THE source of truth) ─────────────────────
    orders_q = {"user_id": user["id"]}
    if from_date or to_date:
        orders_q["order_date"] = {}
        if from_date:
            orders_q["order_date"]["$gte"] = from_date
        if to_date:
            orders_q["order_date"]["$lte"] = to_date
    # User opt-in: exclude orders whose date was inferred (Make.com webhook
    # without created_at). When enabled, only authoritatively-dated orders
    # are counted in dashboard KPIs.
    if settings.get("hide_inferred_date_orders"):
        orders_q["order_date_inferred"] = {"$ne": True}

    all_orders = await db.unified_orders.find(
        orders_q, {"_id": 0, "raw_by_source": 0}
    ).to_list(100000)

    if pm_list or ship_list:
        all_orders = [
            o for o in all_orders
            if _matches_any(o.get("payment_method", ""), pm_list)
            and _matches_any(o.get("shipping_company", ""), ship_list)
        ]

    # Apply user-configured "report_included_statuses" filter:
    # if non-empty, only orders whose order_status matches any of the configured
    # statuses (case-insensitive partial match) are counted in dashboard KPIs.
    included_statuses = settings.get("report_included_statuses") or []
    if included_statuses:
        all_orders = [
            o for o in all_orders
            if _matches_any(o.get("order_status", ""), included_statuses)
        ]

    parsed_all = orders_to_parsed(all_orders)
    matched_all = match_settings(
        parsed_all,
        settings.get("payment_methods", DEFAULT_PAYMENT_METHODS),
        settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
    )

    total_sales = parsed_all["total_sales"]
    total_orders = parsed_all["total_orders"]
    total_fees = matched_all["total_payment_fees"]
    total_shipping = matched_all["total_shipping_cost"]
    deferred_shipping = matched_all.get("deferred_shipping_cost", 0.0)

    # BNPL / electronic / COD split
    total_vat = 0.0
    bnpl_fees = tamara_fees = tabby_fees = emkan_fees = 0.0
    other_payment_fees = 0.0
    bnpl_sales = other_payment_sales = cod_sales = cod_fees = 0.0
    tamara_keywords = ("تمارا", "tamara")
    tabby_keywords = ("تابي", "tabby")
    emkan_keywords = ("إمكان", "امكان", "emkan", "amkan")
    cod_keywords = ("عند الاستلام", "عند الاستلم", "cod", "cash on delivery", "cash_on_delivery")
    for p in matched_all.get("payment_breakdown", []):
        total_vat += float(p.get("vat_amount", 0) or 0)
        name_lc = (p.get("name", "") or "").strip().lower()
        fee = float(p.get("fee_amount", 0) or 0)
        sales = float(p.get("total_sales", 0) or 0)
        if any(k in name_lc for k in tamara_keywords):
            tamara_fees += fee; bnpl_fees += fee; bnpl_sales += sales
        elif any(k in name_lc for k in tabby_keywords):
            tabby_fees += fee; bnpl_fees += fee; bnpl_sales += sales
        elif any(k in name_lc for k in emkan_keywords):
            emkan_fees += fee; bnpl_fees += fee; bnpl_sales += sales
        elif any(k in name_lc for k in cod_keywords):
            cod_fees += fee; cod_sales += sales
        else:
            other_payment_fees += fee; other_payment_sales += sales
    for sh in matched_all.get("shipping_breakdown", []):
        total_vat += float(sh.get("vat_amount", 0) or 0)

    # ── Legacy analyses fallback ────────────────────────────────────────────
    # Older Excel uploads (pre unified_orders migration) wrote ONLY aggregate
    # summaries into `analyses` without saving the per-order details, so they
    # don't appear in unified_orders. To prevent the dashboard from showing
    # less data than the Reports page, we add those legacy summaries here.
    # Filter is by analysis.date because per-order dates aren't available.
    #
    # IMPORTANT: when `report_included_statuses` is configured, legacy analyses
    # CANNOT be filtered by per-order status (they lack the data), so we
    # exclude them entirely to avoid skewing the user-curated totals. Users
    # who want their old data filtered by status must reprocess those analyses.
    legacy_analyses: list[dict] = []
    if not included_statuses:
        legacy_q: dict = {
            "user_id": user["id"],
            "$or": [
                {"orders_imported": {"$exists": False}},
                {"orders_imported": {"$in": [None, 0]}},
            ],
        }
        if from_date or to_date:
            legacy_q["date"] = {}
            if from_date:
                legacy_q["date"]["$gte"] = from_date
            if to_date:
                legacy_q["date"]["$lte"] = to_date
        legacy_analyses = await db.analyses.find(
            legacy_q, {"_id": 0, "report.orders_sample": 0}
        ).to_list(1000)

        for a in legacy_analyses:
            rep = a.get("report") or {}
            s = rep.get("summary") or {}
            total_sales += float(s.get("total_sales") or 0)
            total_orders += int(s.get("total_orders") or 0)
            total_fees += float(s.get("total_payment_fees") or 0)
            total_shipping += float(s.get("total_shipping_cost") or 0)
            deferred_shipping += float(s.get("deferred_shipping_cost") or 0)
            for p in rep.get("payment_breakdown", []) or []:
                total_vat += float(p.get("vat_amount", 0) or 0)
                name_lc = (p.get("name", "") or "").strip().lower()
                fee = float(p.get("fee_amount", 0) or 0)
                sales = float(p.get("total_sales", 0) or 0)
                if any(k in name_lc for k in tamara_keywords):
                    tamara_fees += fee; bnpl_fees += fee; bnpl_sales += sales
                elif any(k in name_lc for k in tabby_keywords):
                    tabby_fees += fee; bnpl_fees += fee; bnpl_sales += sales
                elif any(k in name_lc for k in emkan_keywords):
                    emkan_fees += fee; bnpl_fees += fee; bnpl_sales += sales
                elif any(k in name_lc for k in cod_keywords):
                    cod_fees += fee; cod_sales += sales
                else:
                    other_payment_fees += fee; other_payment_sales += sales
            for sh in rep.get("shipping_breakdown", []) or []:
                total_vat += float(sh.get("vat_amount", 0) or 0)

    # ── Shipping & COD balance splits (Phase 1) ──────────────────────────────
    balances = compute_balances(
        all_orders,
        settings.get("shipping_approved_statuses", DEFAULT_SHIPPING_APPROVED),
        settings.get("cod_approved_statuses", DEFAULT_COD_APPROVED),
    )
    shipping_balance_approved = balances["shipping"]["total_approved"]
    shipping_balance_unapproved = balances["shipping"]["total_unapproved"]
    cod_balance_approved = balances["cod"]["total_approved"]
    cod_balance_unapproved = balances["cod"]["total_unapproved"]

    # ── Daily costs (still keyed by `date` field) ────────────────────────────
    daily_q = {"user_id": user["id"]}
    if from_date or to_date:
        date_filter = {}
        if from_date:
            date_filter["$gte"] = from_date
        if to_date:
            date_filter["$lte"] = to_date
        daily_q["date"] = date_filter
    daily = await db.daily_costs.find(daily_q, {"_id": 0}).to_list(1000)

    # ── TikTok Ads daily (pushed by Make.com via /api/webhook/tiktok/...) ────
    tt_q = {"user_id": user["id"]}
    if from_date or to_date:
        df = {}
        if from_date: df["$gte"] = from_date
        if to_date: df["$lte"] = to_date
        tt_q["date"] = df
    tt_rows = await db.tiktok_ads_daily.find(tt_q, {"_id": 0}).to_list(1000)
    tiktok_spend = sum(float(r.get("spend") or 0) for r in tt_rows)
    tiktok_purchases = sum(int(r.get("purchases") or 0) for r in tt_rows)
    tiktok_revenue = sum(float(r.get("revenue") or 0) for r in tt_rows)
    tiktok_roas = round(tiktok_revenue / tiktok_spend, 2) if tiktok_spend > 0 else 0.0

    # ── Meta Ads daily (pulled via /api/meta/sync from Meta Marketing API) ──
    meta_q = {"user_id": user["id"]}
    if from_date or to_date:
        df = {}
        if from_date: df["$gte"] = from_date
        if to_date: df["$lte"] = to_date
        meta_q["date"] = df
    meta_rows = await db.meta_ads_daily.find(meta_q, {"_id": 0}).to_list(2000)
    meta_spend_total = sum(float(r.get("spend") or 0) for r in meta_rows)
    meta_purchases_total = sum(int(r.get("purchases") or 0) for r in meta_rows)
    meta_revenue_total = sum(float(r.get("revenue") or 0) for r in meta_rows)

    # ── Total ads cost across all platforms ──────────────────────────────
    # IMPORTANT (iteration 16 fix): the legacy `daily_costs.tiktok_ads`
    # path was the ONLY TikTok source in this sum, but Make.com webhooks
    # populate `tiktok_ads_daily` (collection above) — NOT `daily_costs`.
    # As a result, every merchant whose TikTok feed went through the
    # webhook (the supported path) had their TikTok spend dropped from
    # the master "إجمالي تكلفة الإعلانات" card. Take the MAX per-day to
    # avoid double-counting if someone happens to have both sources.
    dc_tt_total = sum((d.get("tiktok_ads", 0) or 0) for d in daily)
    tiktok_total_for_dashboard = max(tiktok_spend, dc_tt_total)
    daily_ads_total = sum(
        (d.get("snapchat_ads", 0) or 0) + (d.get("snapchat_ads_2", 0) or 0)
        + (d.get("instagram_ads", 0) or 0)
        + (d.get("google_ads", 0) or 0)
        for d in daily
    ) + tiktok_total_for_dashboard + meta_spend_total
    daily_products_total = sum((d.get("product_costs", 0) or 0) for d in daily)

    # ── Computed product cost from order line-items (iteration 19) ─────────
    # When `unified_orders.total_product_cost` is populated (via webhook
    # ingestion → product_costs.attach_cost_to_order_doc), prefer THAT as
    # the source of truth for product cost — it reflects real SKU-level
    # costs. The legacy `daily_costs.product_costs` (manual entry) stays
    # as a fallback so single-merchant flows without per-SKU costs still
    # work. We take max() per the same dedupe-via-bigger pattern used for
    # TikTok webhook vs daily_costs.
    computed_product_cost = round(sum(
        float(o.get("total_product_cost") or 0) for o in all_orders
    ), 2)
    # Distinct missing-cost lines across the filtered orders (UI badge).
    missing_cost_skus: set = set()
    for o in all_orders:
        for ln in (o.get("missing_product_cost_lines") or []):
            key = (ln.get("sku") or ln.get("product_id") or ln.get("name") or "").strip().upper()
            if key:
                missing_cost_skus.add(key)
    # ── Profit-status counts (iteration 24) ───────────────────────────────
    # `profit_status` is set per-order by attach_cost_to_order_doc. It
    # tells the dashboard whether the order's REAL profit can be trusted:
    #   - complete                : every product matched a cost entry
    #   - incomplete_missing_cost : ≥1 product has no cost (UI prompts add)
    #   - incomplete_no_products  : no products[] (typically Excel orders)
    incomplete_profit_orders_count = 0
    no_products_orders_count = 0
    excel_no_products_count = 0
    for o in all_orders:
        ps = (o.get("profit_status") or "").strip()
        ds = (o.get("data_source") or "").strip().lower()
        # Fallback: if profit_status was never written (legacy orders),
        # infer it from products[] presence so the UI still works for
        # pre-iteration-24 data without forcing a backfill.
        if not ps:
            if not (o.get("products") or []):
                ps = "incomplete_no_products"
            elif (o.get("missing_product_cost_lines") or []):
                ps = "incomplete_missing_cost"
            else:
                ps = "complete"
        if ps != "complete":
            incomplete_profit_orders_count += 1
        if ps == "incomplete_no_products":
            no_products_orders_count += 1
            if ds in ("excel", ""):
                excel_no_products_count += 1
    product_cost_effective = max(computed_product_cost, daily_products_total)
    daily_totals = daily_ads_total + product_cost_effective

    # ── Operating Expenses (المصروفات التشغيلية) ─────────────────────────────
    # Aggregate the per-day salary + rental + variable-expense costs over the
    # active date range. When no range is supplied we still aggregate so the
    # dashboard reflects month-to-date operating cost.
    today_d = datetime.now(timezone.utc).date()
    fd = _parse_date_or(from_date, today_d.replace(day=1))
    td = _parse_date_or(to_date, today_d)
    if td < fd:
        fd, td = td, fd
    op_range = await compute_operating_expenses_for_range(db, user["id"], fd, td)
    operating_expenses_total = float(op_range.get("operating_total") or 0)
    operating_salaries_total = float(op_range.get("salaries_total") or 0)
    operating_rentals_total = float(op_range.get("rentals_total") or 0)
    operating_prepaid_total = float(op_range.get("prepaid_total") or 0)
    operating_daily_other_total = float(op_range.get("daily_other_total") or 0)

    # Net profit (orders P&L − daily ads − product cost (computed or manual) − operating expenses)
    orders_profit = total_sales - total_fees - total_shipping
    net_profit_adjusted = (
        orders_profit
        - daily_ads_total
        - product_cost_effective
        - operating_expenses_total
    )

    # ── Phase 3: configurable "صافي المبيعات" KPI ────────────────────────────
    # Merchants disagree on what counts as "net sales" — some treat shipping
    # collected as revenue, others net it out; some deduct VAT, others don't.
    # Settings.net_sales_config gives them 7 independent toggles.
    cfg = settings.get("net_sales_config") or DEFAULT_NET_SALES_CONFIG
    regular_shipping = total_shipping - deferred_shipping
    net_sales = total_sales
    if cfg.get("deduct_payment_fees", True):
        net_sales -= total_fees
    if cfg.get("deduct_shipping", True):
        net_sales -= regular_shipping
    if cfg.get("deduct_deferred_shipping", False):
        net_sales -= deferred_shipping
    if cfg.get("deduct_ads", True):
        net_sales -= daily_ads_total
    if cfg.get("deduct_product_costs", True):
        net_sales -= product_cost_effective
    if cfg.get("deduct_vat", False):
        net_sales -= total_vat
    if cfg.get("deduct_daily_expenses", False):
        # daily_expenses_total is currently aliased to daily_products_total;
        # keep this independent for future expansion.
        pass
    if cfg.get("deduct_operating_expenses", True):
        net_sales -= operating_expenses_total

    # ── Monthly trend from unified orders + legacy analyses ─────────────────
    from collections import defaultdict
    monthly_sales = defaultdict(float)
    for o in all_orders:
        d = (o.get("order_date") or "")[:7]
        if not d:
            continue
        monthly_sales[d] += float(o.get("total_amount") or 0)
    for a in legacy_analyses:
        d = (a.get("date") or a.get("created_at") or "")[:7]
        if not d:
            continue
        monthly_sales[d] += float(((a.get("report") or {}).get("summary") or {}).get("total_sales") or 0)
    monthly = sorted([
        {"month": k, "sales": round(v, 2), "profit": 0}
        for k, v in monthly_sales.items()
    ], key=lambda x: x["month"])

    # Recent analyses (informational only — independent of date filter)
    recent = await db.analyses.find(
        {"user_id": user["id"]},
        {"_id": 0, "report.orders_sample": 0},
    ).sort("created_at", -1).to_list(5)

    # Source breakdown (excel vs make vs unified)
    src_counts = {"excel": 0, "make": 0, "unified": 0}
    for o in all_orders:
        ds = o.get("data_source") or "unified"
        src_counts[ds] = src_counts.get(ds, 0) + 1

    # Merge live + legacy breakdowns into a single payload
    def _merge_breakdown(live: list[dict], legacy_list: list[dict], key: str) -> list[dict]:
        m: dict[str, dict] = {}
        for b in live:
            n = (b.get("name") or "").strip()
            if not n:
                continue
            cur = m.setdefault(n, {**b, "name": n})
            # ensure numeric sums (live already aggregated; just copy)
        for a in legacy_list:
            for b in (a.get("report") or {}).get(key, []) or []:
                n = (b.get("name") or "").strip()
                if not n:
                    continue
                if n in m:
                    cur = m[n]
                    for f in ("total_sales", "total_cost", "fee_amount", "orders_count", "vat_amount"):
                        if f in b and isinstance(b.get(f), (int, float)):
                            cur[f] = float(cur.get(f, 0) or 0) + float(b.get(f) or 0)
                else:
                    m[n] = {**b, "name": n}
        return list(m.values())

    payment_breakdown_merged = _merge_breakdown(
        matched_all.get("payment_breakdown", []), legacy_analyses, "payment_breakdown",
    )
    shipping_breakdown_merged = _merge_breakdown(
        matched_all.get("shipping_breakdown", []), legacy_analyses, "shipping_breakdown",
    )

    return {
        "range": {"from_date": from_date, "to_date": to_date},
        "totals": {
            "total_sales": round(total_sales, 2),
            "net_sales": round(net_sales, 2),
            "total_orders": int(total_orders),
            "total_payment_fees": round(total_fees, 2),
            "bnpl_fees": round(bnpl_fees, 2),
            "tamara_fees": round(tamara_fees, 2),
            "tabby_fees": round(tabby_fees, 2),
            "emkan_fees": round(emkan_fees, 2),
            "other_payment_fees": round(other_payment_fees, 2),
            "electronic_net": round(other_payment_sales - other_payment_fees, 2),
            "bnpl_net": round(bnpl_sales - bnpl_fees, 2),
            "total_shipping_cost": round(total_shipping, 2),
            "deferred_shipping_cost": round(deferred_shipping, 2),
            "regular_shipping_cost": round(total_shipping - deferred_shipping, 2),
            "expected_salla_transfer": round(
                total_sales - total_fees - (total_shipping - deferred_shipping), 2
            ),
            "total_ads_cost": round(daily_ads_total, 2),
            "total_product_cost": round(product_cost_effective, 2),
            "computed_product_cost": round(computed_product_cost, 2),
            "manual_product_cost": round(daily_products_total, 2),
            "missing_product_cost_count": len(missing_cost_skus),
            # ── Iteration 24: profit-completeness flags ───────────────────
            "incomplete_profit_orders_count": int(incomplete_profit_orders_count),
            "no_products_orders_count": int(no_products_orders_count),
            "excel_no_products_count": int(excel_no_products_count),
            "daily_expenses_total": round(daily_products_total, 2),
            "net_profit": round(net_profit_adjusted, 2),
            "total_vat": round(total_vat, 2),
            "daily_costs_total": round(daily_totals, 2),
            "daily_ads_total": round(daily_ads_total, 2),
            "daily_products_total": round(daily_products_total, 2),
            # ── Operating Expenses (المصروفات التشغيلية اليومية) ──────────────
            "operating_expenses_total": round(operating_expenses_total, 2),
            "operating_salaries_total": round(operating_salaries_total, 2),
            "operating_salaries_employee": float(op_range.get("salaries_employee") or 0),
            "operating_salaries_household": float(op_range.get("salaries_household") or 0),
            "operating_salaries_charity": float(op_range.get("salaries_charity") or 0),
            "operating_rentals_total": round(operating_rentals_total, 2),
            "operating_prepaid_total": round(operating_prepaid_total, 2),
            "operating_prepaid_by_type": op_range.get("prepaid_by_type") or {},
            "operating_daily_other_total": round(operating_daily_other_total, 2),
            "orders_excel_count": src_counts.get("excel", 0),
            "orders_make_count": src_counts.get("make", 0),
            "legacy_analyses_count": len(legacy_analyses),
            "report_included_statuses_active": bool(included_statuses),
            # ── TikTok Ads (pushed via Make.com webhook) ────────────────────
            "tiktok_spend": round(tiktok_spend, 2),
            "tiktok_purchases": int(tiktok_purchases),
            "tiktok_revenue": round(tiktok_revenue, 2),
            "tiktok_roas": tiktok_roas,
            # ── Meta Ads aggregated total (over the active date range) ──────
            "meta_spend": round(meta_spend_total, 2),
            "meta_purchases": int(meta_purchases_total),
            "meta_revenue": round(meta_revenue_total, 2),
            "meta_roas": round(meta_revenue_total / meta_spend_total, 2) if meta_spend_total > 0 else 0.0,
            # Backward-compatible aliases used by older UI code
            "analyses_count": len(recent),
            "make_orders_count": src_counts.get("make", 0),
            # ── Phase 1: shipping & COD balance splits ───────────────────────
            "shipping_approved": shipping_balance_approved,
            "shipping_unapproved": shipping_balance_unapproved,
            "cod_approved": cod_balance_approved,
            "cod_unapproved": cod_balance_unapproved,
        },
        "net_sales_config": cfg,
        "hide_inferred_date_orders": bool(settings.get("hide_inferred_date_orders")),
        "monthly": monthly,
        "payment_breakdown": payment_breakdown_merged,
        "shipping_breakdown": shipping_breakdown_merged,
        "source_breakdown": [
            {"name": k, "count": v} for k, v in src_counts.items() if v > 0
        ],
        "recent_analyses": [
            {
                "id": a["id"],
                "name": a.get("name"),
                "date": a.get("date"),
                "filename": a.get("filename"),
                "total_sales": a["report"]["summary"]["total_sales"],
                "net_profit": a["report"]["summary"]["net_profit"],
                "total_orders": a["report"]["summary"]["total_orders"],
                "orders_imported": int(a.get("orders_imported") or 0),
                "is_legacy": not bool(a.get("orders_imported")),
            } for a in recent
        ],
    }


# ── Shared attribution helper ─────────────────────────────────────────────
# Used by snapchat-summary / tiktok-summary / meta-summary to backfill
# orders + revenue from `unified_orders` when the platform's Pixel
# numbers come back zero (very common for fresh accounts, accounts
# without Pixel setup, or specific date ranges where attribution is
# missing). Without this fallback the merchant sees `0 orders` on the
# card despite spend > 0 and real attributed orders in the store
# (iteration 21 bug fix).
async def _attributed_orders_from_store(
    db, uid: str, source_aliases: tuple, start: str, end: str,
) -> tuple[int, float]:
    """Count orders + revenue in `unified_orders` whose `utm_source`
    matches any of `source_aliases` (case-insensitive, partial-match).
    Date filter uses `order_date` BETWEEN start and end (inclusive).

    Returns (orders, revenue).
    """
    if not source_aliases:
        return 0, 0.0
    # Build case-insensitive regex matching ANY alias substring.
    pattern = "|".join(source_aliases)
    pipeline = [
        {"$match": {
            "user_id": uid,
            "order_date": {"$gte": start, "$lte": end},
            "utm_source": {"$regex": pattern, "$options": "i"},
        }},
        {"$group": {"_id": None,
                    "orders": {"$sum": 1},
                    "revenue": {"$sum": {"$ifNull": ["$total_amount", 0]}}}},
    ]
    async for d in db.unified_orders.aggregate(pipeline):
        return int(d.get("orders", 0)), round(float(d.get("revenue", 0)), 2)
    return 0, 0.0


# ── Snapchat Ads dashboard summary ────────────────────────────────────────────
@api.get("/dashboard/snapchat-summary")
async def snapchat_summary(user: dict = Depends(current_user)):
    """Auto-computed Snapchat Ads card data: today + this-month spend
    (from daily_costs.snapchat_ads + snapchat_ads_2) and the matching
    store performance (orders + revenue from unified_orders). Plus a
    30-day history strip so the UI can render a sparkline. Mirrors the
    TikTok card behavior — auto-refreshes when the dashboard polls.

    All dates use Asia/Riyadh timezone (matches the merchant's locale and
    the date used by both the Snapchat bulk fetch and the browser refresh
    button — so a 02:00 AM Riyadh refresh updates the card immediately
    instead of writing to tomorrow's UTC date)."""
    uid = user["id"]
    today_d = _local_today_date()
    today_str = today_d.isoformat()
    month_start_str = today_str[:8] + "01"
    d30_start_str = (today_d - timedelta(days=29)).isoformat()

    # 1) Snapchat spend — from manually-logged daily_costs.
    #    (Snapchat Marketing API pulls also drop into this collection.)
    daily_rows = await db.daily_costs.find(
        {
            "user_id": uid,
            "date": {"$gte": d30_start_str, "$lte": today_str},
        },
        {"_id": 0, "date": 1, "snapchat_ads": 1, "snapchat_ads_2": 1},
    ).to_list(60)

    by_date_spend: dict = {}
    for r in daily_rows:
        d = r.get("date")
        if not d:
            continue
        s = float(r.get("snapchat_ads") or 0) + float(r.get("snapchat_ads_2") or 0)
        by_date_spend[d] = by_date_spend.get(d, 0.0) + s

    spend_today = round(by_date_spend.get(today_str, 0.0), 2)
    spend_month = round(sum(v for k, v in by_date_spend.items() if k >= month_start_str), 2)
    spend_30d = round(sum(by_date_spend.values()), 2)

    # Display Snapchat spend in BOTH currencies. Stored values are SAR
    # (already converted at ingest), USD is derived using the user-visible
    # exchange rate of 3.752 SAR/USD.
    USD_RATE = 3.752

    def _to_usd(sar: float) -> float:
        return round(sar / USD_RATE, 2) if sar else 0.0

    # 2) Orders + revenue — prefer Snapchat's own Pixel-reported conversions
    #    (stored in snapchat_daily_stats by the bulk fetch). Fall back to
    #    unified_orders only when the Snapchat-side numbers are completely
    #    absent (legacy users who haven't fetched yet).
    settings = await ensure_user_settings(db, uid)
    snap_stats_rows = await db.snapchat_daily_stats.find(
        {"user_id": uid, "date": {"$gte": d30_start_str, "$lte": today_str}},
        {"_id": 0, "date": 1, "purchases": 1, "revenue": 1},
    ).to_list(60)
    snap_by_date: dict = {r["date"]: r for r in snap_stats_rows}

    def _snap_agg(start: str, end: str):
        rows = [r for k, r in snap_by_date.items() if start <= k <= end]
        orders = sum(int(r.get("purchases") or 0) for r in rows)
        revenue = round(sum(float(r.get("revenue") or 0) for r in rows), 2)
        return orders, revenue, len(rows) > 0

    orders_today, revenue_today, has_today = _snap_agg(today_str, today_str)
    orders_month, revenue_month, has_month = _snap_agg(month_start_str, today_str)
    orders_30d, revenue_30d, has_30d = _snap_agg(d30_start_str, today_str)
    snap_pixel_active = has_30d  # any data within 30d → snap pixel is reporting

    # Backfill from unified_orders when Pixel returned 0 (iteration 21 fix).
    # We backfill PER-WINDOW so a window with real Pixel data is preserved,
    # while a window where Pixel returned 0 falls back to store attribution.
    SNAP_ALIASES = ("snapchat", "snap")
    if orders_today == 0 and revenue_today == 0:
        orders_today, revenue_today = await _attributed_orders_from_store(
            db, uid, SNAP_ALIASES, today_str, today_str,
        )
    if orders_month == 0 and revenue_month == 0:
        orders_month, revenue_month = await _attributed_orders_from_store(
            db, uid, SNAP_ALIASES, month_start_str, today_str,
        )
    if orders_30d == 0 and revenue_30d == 0:
        orders_30d, revenue_30d = await _attributed_orders_from_store(
            db, uid, SNAP_ALIASES, d30_start_str, today_str,
        )

    # Legacy fallback: ONLY if there's no Pixel data AT ALL anywhere in
    # the last 30d AND the utm-based attribution above didn't yield
    # anything either, fall back to ALL store orders (un-attributed). This
    # is the original behaviour for users with no Pixel setup and no UTMs.
    if not has_30d:
        base_q = {"user_id": uid}
        if settings.get("hide_inferred_date_orders"):
            base_q["order_date_inferred"] = {"$ne": True}

        async def _summarize(q):
            pipeline = [
                {"$match": q},
                {"$group": {"_id": None,
                            "orders": {"$sum": 1},
                            "revenue": {"$sum": {"$ifNull": ["$total_amount", 0]}}}},
            ]
            async for d in db.unified_orders.aggregate(pipeline):
                return int(d.get("orders", 0)), round(float(d.get("revenue", 0)), 2)
            return 0, 0.0

        if not has_today:
            orders_today, revenue_today = await _summarize({**base_q, "order_date": today_str})
        if not has_month:
            orders_month, revenue_month = await _summarize({**base_q,
                "order_date": {"$gte": month_start_str, "$lte": today_str}})
        orders_30d, revenue_30d = await _summarize({**base_q,
            "order_date": {"$gte": d30_start_str, "$lte": today_str}})

    # Build 30-day spend history (filled with zeros for missing days)
    history: list = []
    for i in range(29, -1, -1):
        d = (today_d - timedelta(days=i)).isoformat()
        history.append({"date": d, "spend": round(by_date_spend.get(d, 0.0), 2)})

    # Pick the most recent update_at across this month's daily_costs rows for
    # the "آخر تحديث" line on the dashboard.
    last_fetched_doc = await db.daily_costs.find_one(
        {"user_id": uid, "snapchat_ads": {"$gt": 0}},
        {"_id": 0, "updated_at": 1, "created_at": 1},
        sort=[("updated_at", -1)],
    )
    last_fetched_at = None
    if last_fetched_doc:
        last_fetched_at = last_fetched_doc.get("updated_at") or last_fetched_doc.get("created_at")

    def _roas(rev: float, spend: float) -> float:
        return round(rev / spend, 2) if spend > 0 else 0.0

    return {
        "today": {
            "date": today_str,
            "spend": spend_today,
            "spend_usd": _to_usd(spend_today),
            "orders": orders_today,
            "revenue": revenue_today,
            "roas": _roas(revenue_today, spend_today),
        },
        "month": {
            "start": month_start_str,
            "spend": spend_month,
            "spend_usd": _to_usd(spend_month),
            "orders": orders_month,
            "revenue": revenue_month,
            "roas": _roas(revenue_month, spend_month),
        },
        "last_30d": {
            "start": d30_start_str,
            "spend": spend_30d,
            "spend_usd": _to_usd(spend_30d),
            "orders": orders_30d,
            "revenue": revenue_30d,
            "roas": _roas(revenue_30d, spend_30d),
        },
        "usd_rate": USD_RATE,
        "last_fetched_at": last_fetched_at,
        "source": "snapchat_pixel" if snap_pixel_active else "store_orders",
        "history": history,
    }


# ── Meta Ads dashboard summary ────────────────────────────────────────────────
@api.get("/dashboard/meta-summary")
async def meta_summary(user: dict = Depends(current_user)):
    """Auto-computed Meta Ads card data: today + this-month + last 30d.
    Pulled directly from meta_ads_daily (populated by the Meta Marketing
    API direct integration). Also returns a per-campaign breakdown for
    the dedicated Meta report page.

    All dates use Asia/Riyadh timezone — same rationale as snapchat-summary:
    matches the merchant's locale and the date written by the meta /sync
    endpoint, so "today" lines up across browser, fetch, and aggregation."""
    uid = user["id"]
    today_d = _local_today_date()
    today_str = today_d.isoformat()
    month_start_str = today_str[:8] + "01"
    d30_start_str = (today_d - timedelta(days=29)).isoformat()

    rows = await db.meta_ads_daily.find(
        {"user_id": uid, "date": {"$gte": d30_start_str, "$lte": today_str}},
        {"_id": 0},
    ).to_list(2000)

    def _agg(start: str, end: str):
        bucket = {"spend": 0.0, "purchases": 0, "purchase_value": 0.0,
                  "impressions": 0, "clicks": 0}
        for r in rows:
            if start <= r["date"] <= end:
                bucket["spend"] += float(r.get("spend") or 0)
                bucket["purchases"] += int(r.get("purchases") or 0)
                bucket["purchase_value"] += float(r.get("purchase_value") or 0)
                bucket["impressions"] += int(r.get("impressions") or 0)
                bucket["clicks"] += int(r.get("clicks") or 0)
        spend = round(bucket["spend"], 2)
        purchases = bucket["purchases"]
        revenue = round(bucket["purchase_value"], 2)
        impressions = bucket["impressions"]
        clicks = bucket["clicks"]
        return {
            "spend": spend,
            "orders": purchases,
            "revenue": revenue,
            "impressions": impressions,
            "clicks": clicks,
            "roas": round(revenue / spend, 2) if spend > 0 else 0.0,
            "cpa": round(spend / purchases, 2) if purchases > 0 else 0.0,
            # CPC = spend / clicks
            "cpc": round(spend / clicks, 2) if clicks > 0 else 0.0,
            # CPM = (spend / impressions) * 1000
            "cpm": round((spend / impressions) * 1000, 2) if impressions > 0 else 0.0,
            # CTR = (clicks / impressions) * 100
            "ctr": round((clicks / impressions) * 100, 2) if impressions > 0 else 0.0,
        }

    # 30-day spend history for sparkline
    by_date_spend: dict = {}
    for r in rows:
        d = r["date"]
        by_date_spend[d] = by_date_spend.get(d, 0.0) + float(r.get("spend") or 0)
    history: list = []
    for i in range(29, -1, -1):
        d = (today_d - timedelta(days=i)).isoformat()
        history.append({"date": d, "spend": round(by_date_spend.get(d, 0.0), 2)})

    # Per-campaign breakdown (current month)
    campaigns_map: dict = {}
    for r in rows:
        if r["date"] >= month_start_str:
            key = (r.get("campaign_id") or "_default", r.get("campaign_name") or "بدون اسم")
            c = campaigns_map.setdefault(key, {
                "campaign_id": key[0], "campaign_name": key[1],
                "spend": 0.0, "purchases": 0, "revenue": 0.0,
                "impressions": 0, "clicks": 0,
            })
            c["spend"] += float(r.get("spend") or 0)
            c["purchases"] += int(r.get("purchases") or 0)
            c["revenue"] += float(r.get("purchase_value") or 0)
            c["impressions"] += int(r.get("impressions") or 0)
            c["clicks"] += int(r.get("clicks") or 0)
    campaigns = []
    for c in campaigns_map.values():
        c["spend"] = round(c["spend"], 2)
        c["revenue"] = round(c["revenue"], 2)
        c["roas"] = round(c["revenue"] / c["spend"], 2) if c["spend"] > 0 else 0.0
        campaigns.append(c)
    campaigns.sort(key=lambda x: x["spend"], reverse=True)

    # Last sync timestamp + connection health (so the dashboard can show
    # an "expired link" banner instead of a confusing 0.00 figure).
    last_sync = None
    latest = await db.meta_ads_daily.find_one(
        {"user_id": uid}, {"_id": 0, "updated_at": 1},
        sort=[("updated_at", -1)],
    )
    if latest:
        last_sync = latest.get("updated_at")

    meta_conn = await db.meta_connections.find_one(
        {"user_id": uid},
        {"_id": 0, "connection_status": 1, "last_error_message": 1, "last_error_at": 1},
    )
    connection_status = (meta_conn or {}).get("connection_status") or "ok"
    last_error_message = (meta_conn or {}).get("last_error_message")
    last_error_at = (meta_conn or {}).get("last_error_at")

    # utm-source attribution fallback (iteration 21 fix) — applied when
    # Meta API returns 0 purchases for a window despite spend > 0. Meta
    # bundles Facebook + Instagram, so we match both source aliases.
    META_ALIASES = ("facebook", "fb", "instagram", "ig", "meta")

    async def _agg_with_fallback(start: str, end: str) -> dict:
        b = _agg(start, end)
        if b["orders"] == 0 and b["revenue"] == 0:
            attr_orders, attr_rev = await _attributed_orders_from_store(
                db, uid, META_ALIASES, start, end,
            )
            if attr_orders or attr_rev:
                b["orders"] = attr_orders
                b["revenue"] = attr_rev
                b["roas"] = round(attr_rev / b["spend"], 2) if b["spend"] > 0 else 0.0
                b["cpa"] = round(b["spend"] / attr_orders, 2) if attr_orders > 0 else 0.0
        return b

    return {
        "today": {"date": today_str, **(await _agg_with_fallback(today_str, today_str))},
        "month": {"start": month_start_str, **(await _agg_with_fallback(month_start_str, today_str))},
        "last_30d": {"start": d30_start_str, **(await _agg_with_fallback(d30_start_str, today_str))},
        "history": history,
        "campaigns": campaigns,
        "last_sync_at": last_sync,
        "connection_status": connection_status,
        "last_error_message": last_error_message,
        "last_error_at": last_error_at,
    }


# ── TikTok Ads dashboard summary ──────────────────────────────────────────────
@api.get("/dashboard/tiktok-summary")
async def tiktok_summary(user: dict = Depends(current_user)):
    """Auto-computed TikTok Ads card data: today + this-month + last 30d.

    Aggregates from `tiktok_ads_daily` (currently populated by Make.com
    webhook — direct TikTok Marketing API integration is on the roadmap)
    plus the local `daily_costs.tiktok_ads` field as a fallback for legacy
    manually-entered spend.

    All dates use Asia/Riyadh timezone (matches Snapchat/Meta cards)."""
    uid = user["id"]
    today_d = _local_today_date()
    today_str = today_d.isoformat()
    month_start_str = today_str[:8] + "01"
    d30_start_str = (today_d - timedelta(days=29)).isoformat()

    # tiktok_ads_daily: rich per-day rows (spend, purchases, revenue, +
    # optional CPC/CPM/CTR fields from TikTok native fields). Make.com may
    # push MULTIPLE rows per date (one per active campaign), so we MUST
    # aggregate across rows for the same date — using a naive `{date: row}`
    # dict would drop all-but-one campaign's spend (iteration 16 fix).
    tt_rows = await db.tiktok_ads_daily.find(
        {"user_id": uid, "date": {"$gte": d30_start_str, "$lte": today_str}},
        {"_id": 0},
    ).to_list(500)
    tt_by_date: dict = {}
    for r in tt_rows:
        d = r.get("date")
        if not d:
            continue
        agg = tt_by_date.setdefault(d, {"spend": 0.0, "purchases": 0, "revenue": 0.0})
        agg["spend"] += float(r.get("spend") or 0)
        agg["purchases"] += int(r.get("purchases") or 0)
        agg["revenue"] += float(r.get("revenue") or 0)

    # daily_costs fallback (rare — most users will have webhook data)
    daily_rows = await db.daily_costs.find(
        {"user_id": uid, "date": {"$gte": d30_start_str, "$lte": today_str}},
        {"_id": 0, "date": 1, "tiktok_ads": 1},
    ).to_list(60)
    dc_spend_by_date: dict = {r["date"]: float(r.get("tiktok_ads") or 0)
                              for r in daily_rows if r.get("date")}

    def _row_spend(date_key: str) -> float:
        webhook = float((tt_by_date.get(date_key) or {}).get("spend") or 0)
        manual = float(dc_spend_by_date.get(date_key) or 0)
        # Use the bigger of the two so we never double-count, but we ALSO
        # never silently drop webhook data when the merchant happens to
        # have a 0-valued daily_costs row for the same date (this exact
        # case broke /dashboard/tiktok-summary in iteration 15 — fixed in 16).
        return max(webhook, manual)

    def _agg(start: str, end: str):
        # Iterate over the UNION of dates from BOTH sources — never
        # restrict to one (the previous bug). For each date, use
        # max(webhook, manual) so we never double-count.
        date_keys = {k for k in tt_by_date if start <= k <= end} \
                    | {k for k in dc_spend_by_date if start <= k <= end}
        spend = 0.0
        purchases = 0
        revenue = 0.0
        for k in date_keys:
            spend += _row_spend(k)
            row = tt_by_date.get(k) or {}
            purchases += int(row.get("purchases") or 0)
            revenue += float(row.get("revenue") or 0)
        roas = round(revenue / spend, 2) if spend > 0 else 0.0
        cpa = round(spend / purchases, 2) if purchases > 0 else 0.0
        return {"spend": round(spend, 2), "orders": int(purchases),
                "revenue": round(revenue, 2), "roas": roas, "cpa": cpa}

    # 30-day spend history (for the sparkline)
    history: list = []
    for i in range(29, -1, -1):
        d = (today_d - timedelta(days=i)).isoformat()
        history.append({"date": d, "spend": round(_row_spend(d), 2)})

    # Last update timestamp from tiktok_ads_daily
    last_doc = await db.tiktok_ads_daily.find_one(
        {"user_id": uid}, {"_id": 0, "updated_at": 1, "received_at": 1},
        sort=[("updated_at", -1)],
    )
    last_fetched_at = None
    if last_doc:
        last_fetched_at = last_doc.get("updated_at") or last_doc.get("received_at")

    # Apply utm-source attribution fallback when webhook reports 0 orders
    # for a window despite spend > 0 (iteration 21 fix — same root cause
    # as the Snap card: TikTok webhook from Make.com often omits
    # `purchases`/`revenue` even when Salla has attributed orders).
    TIKTOK_ALIASES = ("tiktok", "tik_tok", "tik-tok")

    async def _agg_with_fallback(start: str, end: str) -> dict:
        b = _agg(start, end)
        if b["orders"] == 0 and b["revenue"] == 0:
            attr_orders, attr_rev = await _attributed_orders_from_store(
                db, uid, TIKTOK_ALIASES, start, end,
            )
            if attr_orders or attr_rev:
                b["orders"] = attr_orders
                b["revenue"] = attr_rev
                b["roas"] = round(attr_rev / b["spend"], 2) if b["spend"] > 0 else 0.0
                b["cpa"] = round(b["spend"] / attr_orders, 2) if attr_orders > 0 else 0.0
        return b

    return {
        "today": {"date": today_str, **(await _agg_with_fallback(today_str, today_str))},
        "month": {"start": month_start_str, **(await _agg_with_fallback(month_start_str, today_str))},
        "last_30d": {"start": d30_start_str, **(await _agg_with_fallback(d30_start_str, today_str))},
        "history": history,
        "last_fetched_at": last_fetched_at,
        "source": "make_webhook",
        "has_data": len(tt_by_date) > 0 or any(v > 0 for v in dc_spend_by_date.values()),
    }


# ── Unified Ads Report (Snap + TikTok + Meta) ────────────────────────────────
@api.get("/reports/ads")
async def unified_ads_report(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    user: dict = Depends(current_user),
):
    """Unified daily/comparison report for all three ad platforms.

    Returns per-platform aggregates over [from_date, to_date] (defaults to
    current month-to-date) plus a per-day series so the UI can chart
    spend/ROAS comparisons. Each platform reports the same metric shape:
      spend, impressions, clicks, purchases, revenue,
      cpc, cpm, ctr, cpa, roas.

    Snapchat numbers come from `daily_costs.snapchat_ads(+_2)` (spend) and
    `snapchat_daily_stats` (purchases/revenue from Pixel). Performance
    metrics (CPC/CPM/CTR/CPA) for Snap aren't fetched yet — they default
    to 0 until we hit the Marketing API stats endpoint. TikTok and Meta
    expose the full metric set from their daily collections.
    """
    uid = user["id"]
    today_d = datetime.now(timezone.utc).date()
    month_start = today_d.replace(day=1)

    fd = _parse_date_or(from_date, month_start)
    td = _parse_date_or(to_date, today_d)
    if td < fd:
        fd, td = td, fd
    fd_str, td_str = fd.isoformat(), td.isoformat()

    def _metrics(spend, impressions, clicks, purchases, revenue):
        spend = float(spend or 0)
        impressions = int(impressions or 0)
        clicks = int(clicks or 0)
        purchases = int(purchases or 0)
        revenue = float(revenue or 0)
        return {
            "spend": round(spend, 2),
            "impressions": impressions,
            "clicks": clicks,
            "purchases": purchases,
            "revenue": round(revenue, 2),
            "cpc": round(spend / clicks, 2) if clicks > 0 else 0.0,
            "cpm": round((spend / impressions) * 1000, 2) if impressions > 0 else 0.0,
            "ctr": round((clicks / impressions) * 100, 2) if impressions > 0 else 0.0,
            "cpa": round(spend / purchases, 2) if purchases > 0 else 0.0,
            "roas": round(revenue / spend, 2) if spend > 0 else 0.0,
        }

    # ── Snapchat ─────────────────────────────────────────────────────────
    snap_spend_by_date: dict = {}
    async for r in db.daily_costs.find(
        {"user_id": uid, "date": {"$gte": fd_str, "$lte": td_str}},
        {"_id": 0, "date": 1, "snapchat_ads": 1, "snapchat_ads_2": 1},
    ):
        d = r.get("date")
        if not d:
            continue
        snap_spend_by_date[d] = snap_spend_by_date.get(d, 0.0) \
            + float(r.get("snapchat_ads") or 0) + float(r.get("snapchat_ads_2") or 0)
    snap_stats_by_date: dict = {}
    async for r in db.snapchat_daily_stats.find(
        {"user_id": uid, "date": {"$gte": fd_str, "$lte": td_str}},
        {"_id": 0, "date": 1, "purchases": 1, "revenue": 1},
    ):
        snap_stats_by_date[r["date"]] = r
    snap_spend = round(sum(snap_spend_by_date.values()), 2)
    snap_purchases = sum(int(r.get("purchases") or 0) for r in snap_stats_by_date.values())
    snap_revenue = round(sum(float(r.get("revenue") or 0) for r in snap_stats_by_date.values()), 2)
    snap = {
        "platform": "snapchat",
        "label": "Snapchat",
        **_metrics(snap_spend, 0, 0, snap_purchases, snap_revenue),
    }

    # ── TikTok ───────────────────────────────────────────────────────────
    tt_rows = await db.tiktok_ads_daily.find(
        {"user_id": uid, "date": {"$gte": fd_str, "$lte": td_str}},
        {"_id": 0},
    ).to_list(5000)
    tt_spend = sum(float(r.get("spend") or 0) for r in tt_rows)
    tt_imp = sum(int(r.get("impressions") or 0) for r in tt_rows)
    tt_clicks = sum(int(r.get("clicks") or 0) for r in tt_rows)
    tt_purchases = sum(int(r.get("purchases") or 0) for r in tt_rows)
    tt_revenue = sum(float(r.get("revenue") or 0) for r in tt_rows)
    tiktok = {
        "platform": "tiktok",
        "label": "TikTok",
        **_metrics(tt_spend, tt_imp, tt_clicks, tt_purchases, tt_revenue),
    }
    tt_spend_by_date: dict = {}
    for r in tt_rows:
        tt_spend_by_date[r["date"]] = tt_spend_by_date.get(r["date"], 0.0) + float(r.get("spend") or 0)

    # ── Meta ─────────────────────────────────────────────────────────────
    meta_rows = await db.meta_ads_daily.find(
        {"user_id": uid, "date": {"$gte": fd_str, "$lte": td_str}},
        {"_id": 0},
    ).to_list(5000)
    m_spend = sum(float(r.get("spend") or 0) for r in meta_rows)
    m_imp = sum(int(r.get("impressions") or 0) for r in meta_rows)
    m_clicks = sum(int(r.get("clicks") or 0) for r in meta_rows)
    m_purchases = sum(int(r.get("purchases") or 0) for r in meta_rows)
    m_revenue = sum(float(r.get("purchase_value") or 0) for r in meta_rows)
    meta_p = {
        "platform": "meta",
        "label": "Meta",
        **_metrics(m_spend, m_imp, m_clicks, m_purchases, m_revenue),
    }
    meta_spend_by_date: dict = {}
    for r in meta_rows:
        meta_spend_by_date[r["date"]] = meta_spend_by_date.get(r["date"], 0.0) + float(r.get("spend") or 0)

    # ── Daily series (one row per date in range) ─────────────────────────
    series = []
    cur = fd
    while cur <= td:
        ds = cur.isoformat()
        series.append({
            "date": ds,
            "snapchat": round(snap_spend_by_date.get(ds, 0.0), 2),
            "tiktok": round(tt_spend_by_date.get(ds, 0.0), 2),
            "meta": round(meta_spend_by_date.get(ds, 0.0), 2),
        })
        cur = cur + timedelta(days=1)

    # ── Combined totals ──────────────────────────────────────────────────
    combined_spend = snap["spend"] + tiktok["spend"] + meta_p["spend"]
    combined_revenue = snap["revenue"] + tiktok["revenue"] + meta_p["revenue"]
    combined_purchases = snap["purchases"] + tiktok["purchases"] + meta_p["purchases"]
    combined_clicks = tiktok["clicks"] + meta_p["clicks"]  # Snap not available
    combined_impressions = tiktok["impressions"] + meta_p["impressions"]
    combined = {
        "label": "الإجمالي",
        **_metrics(combined_spend, combined_impressions, combined_clicks,
                   combined_purchases, combined_revenue),
    }

    return {
        "range": {"from_date": fd_str, "to_date": td_str},
        "platforms": [snap, tiktok, meta_p],
        "combined": combined,
        "series": series,
    }


# ── Health ────────────────────────────────────────────────────────────────────
@api.get("/")
async def root():
    return {"message": "Hesab API is running"}


# ── App wiring ────────────────────────────────────────────────────────────────
attach_snapchat_routes(api, db)
attach_meta_routes(api, db)
attach_shipping_accounts_routes(api, db)
attach_webhook_routes(api, db)
attach_operating_expenses_routes(api, db)
attach_product_costs_routes(api, db, current_user)
app.include_router(api)

# CORS
frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
extra = os.environ.get("CORS_ORIGINS", "").split(",")
origins = list({o.strip() for o in [frontend_url, "http://localhost:3000"] + extra if o and o.strip() and o.strip() != "*"})

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def on_startup():
    await db.users.create_index("email", unique=True)
    await db.settings.create_index("user_id", unique=True)
    await db.daily_costs.create_index([("user_id", 1), ("date", 1)], unique=True)
    await db.analyses.create_index([("user_id", 1), ("created_at", -1)])
    await db.snapchat_connections.create_index("user_id", unique=True)
    # Multi-account Snapchat selection (iteration 15) — one doc per
    # (user_id, ad_account_id). `enabled` toggles whether the account
    # participates in /sync-all-accounts and /accounts-summary.
    await db.snapchat_ad_accounts.create_index(
        [("user_id", 1), ("ad_account_id", 1)], unique=True,
    )
    # Per-account, per-day spend rows. Stores BOTH native and SAR amounts +
    # fx_rate so accounting traces are auditable.
    await db.snapchat_account_daily.create_index(
        [("user_id", 1), ("ad_account_id", 1), ("date", 1)], unique=True,
    )
    await db.snapchat_account_daily.create_index(
        [("user_id", 1), ("date", -1)],
    )
    # ── Product cost catalogue (iteration 19) ──────────────────────────────
    # One doc per (user_id, sku_normalized). Provides the cost lookup table
    # used by webhook ingestion + dashboard's total_product_cost field.
    await db.product_costs.create_index(
        [("user_id", 1), ("sku_normalized", 1)], unique=True,
    )
    await db.product_costs.create_index(
        [("user_id", 1), ("is_active", 1)],
    )
    await db.product_costs.create_index(
        [("user_id", 1), ("product_id", 1)],
    )
    await db.shipping_payments.create_index([("user_id", 1), ("company_name", 1), ("payment_date", -1)])
    await db.webhook_tokens.create_index("user_id", unique=True)
    await db.webhook_tokens.create_index("token", unique=True)
    await db.unified_orders.create_index([("user_id", 1), ("order_number", 1)], unique=True)
    await db.unified_orders.create_index([("user_id", 1), ("order_date", -1)])
    await db.unified_orders.create_index([("user_id", 1), ("data_source", 1)])
    await db.operating_salaries.create_index([("user_id", 1), ("status", 1)])
    await db.operating_rentals.create_index([("user_id", 1), ("status", 1)])
    await db.operating_daily_expenses.create_index([("user_id", 1), ("date", -1)])
    await db.operating_prepaid_expenses.create_index([("user_id", 1), ("status", 1)])
    await db.operating_prepaid_expenses.create_index([("user_id", 1), ("expense_type", 1)])
    # Backfill: older salaries created before the country column existed are
    # treated as Saudi by default (most common merchant home country).
    legacy_country = await db.operating_salaries.update_many(
        {"country": {"$exists": False}}, {"$set": {"country": "saudi"}}
    )
    if legacy_country.modified_count:
        logger.info(f"Backfilled country=saudi on {legacy_country.modified_count} legacy operating_salaries.")
    # One-time migration: copy any pre-existing webhook_orders into unified_orders
    # so users who already have Make.com data don't lose it on the cutover.
    if await db.webhook_orders.count_documents({}) > 0:
        async for old in db.webhook_orders.find({}):
            existing = await db.unified_orders.find_one(
                {"user_id": old["user_id"], "order_number": old.get("order_number")}
            )
            if existing:
                continue
            old.pop("_id", None)
            old.setdefault("data_source", "make")
            old.setdefault("data_sources", [{"source": "make", "at": old.get("received_at") or datetime.now(timezone.utc).isoformat()}])
            old.setdefault("field_sources", {})
            # Normalize legacy 'status' -> 'order_status'
            if "status" in old and "order_status" not in old:
                old["order_status"] = old.pop("status")
            await db.unified_orders.insert_one(old)

    # Backfill: normalize any order_date_raw to order_date (handles older uploads
    # made before the date-column auto-detect fix).
    backfill_cursor = db.unified_orders.find(
        {"$and": [
            {"$or": [{"order_date": {"$in": [None, ""]}}, {"order_date": {"$exists": False}}]},
            {"order_date_raw": {"$exists": True, "$nin": [None, ""]}},
        ]},
        {"_id": 1, "order_date_raw": 1},
    )
    backfilled = 0
    async for o in backfill_cursor:
        norm = _normalize_date_str(o.get("order_date_raw") or "")
        if norm:
            await db.unified_orders.update_one({"_id": o["_id"]}, {"$set": {"order_date": norm}})
            backfilled += 1
    if backfilled:
        logger.info(f"Backfilled order_date on {backfilled} unified_orders documents.")

    # ALSO backfill order_date when raw is missing entirely (Make.com payload
    # without created_at/order_date). Use the row's received_at when available.
    # IMPORTANT (2026-05 fix): previously we fell back to "today" as the last
    # resort, but that silently labels March/April orders that Make.com
    # forwards today (without their original created_at) as "May orders",
    # inflating the current month's KPIs. New behavior:
    #   - If received_at is available → use received_at[:10] (still
    #     approximate, but at least bounded to when we first saw the order).
    #   - Otherwise → leave order_date NULL so the missing-date case is
    #     explicit and reviewable from the Make.com page.
    nulldate_cursor = db.unified_orders.find(
        {"$or": [{"order_date": None}, {"order_date": ""},
                 {"order_date": {"$exists": False}}]},
        {"_id": 1, "received_at": 1, "created_at": 1},
    )
    nulldate_fixed = 0
    nulldate_skipped = 0
    async for o in nulldate_cursor:
        recv = o.get("received_at") or o.get("created_at")
        date_str: Optional[str] = None
        if recv:
            try:
                date_str = datetime.fromisoformat(str(recv).replace("Z", "+00:00")).date().isoformat()
            except Exception:
                date_str = None
        if not date_str:
            # No received_at metadata → cannot safely guess. Skip.
            nulldate_skipped += 1
            continue
        await db.unified_orders.update_one({"_id": o["_id"]}, {"$set": {"order_date": date_str}})
        nulldate_fixed += 1
    if nulldate_fixed:
        logger.info(f"Backfilled order_date=received_at on {nulldate_fixed} unified_orders documents (skipped {nulldate_skipped}).")

    # 2026-05 corrective migration: undo the previous "fallback to today"
    # logic for Make.com orders that arrived without created_at. Those
    # rows are identifiable because they have:
    #   - data_source = 'make'
    #   - order_date_raw is empty (Make.com never sent a date string)
    #   - order_date is currently set, but matches received_at[:10] AND
    #     differs from the actual creation date the merchant expected.
    # We CANNOT recover the true creation date — but we can at least clear
    # the spurious "today" value so the orders don't inflate the wrong
    # month's KPIs. The merchant can then either re-import them via Excel
    # (which has the correct date) or fix their Make.com scenario to push
    # `created_at` correctly going forward.
    fake_today_cursor = db.unified_orders.find(
        {
            "data_source": "make",
            "$or": [
                {"order_date_raw": {"$in": ["", None]}},
                {"order_date_raw": {"$exists": False}},
            ],
            "order_date": {"$exists": True, "$nin": [None, ""]},
            "received_at": {"$exists": True, "$nin": [None, ""]},
        },
        {"_id": 1, "order_date": 1, "received_at": 1},
    )
    today_fallback_cleared = 0
    async for o in fake_today_cursor:
        # The fallback set order_date to the day the webhook arrived (UTC).
        try:
            recv_day = datetime.fromisoformat(
                str(o["received_at"]).replace("Z", "+00:00")
            ).date().isoformat()
        except Exception:
            continue
        if o.get("order_date") == recv_day:
            await db.unified_orders.update_one(
                {"_id": o["_id"]},
                {"$set": {"order_date": None, "order_date_missing": True}},
            )
            today_fallback_cleared += 1
    # 2026-05 corrective migration v2: restore order_date for rows the
    # previous migration cleared. The user explicitly opted-in to having
    # Make.com orders auto-appear in the dashboard, so we now refill the
    # cleared rows with received_at[:10] and mark them as inferred.
    cleared_cursor = db.unified_orders.find(
        {
            "order_date_missing": True,
            "received_at": {"$exists": True, "$nin": [None, ""]},
        },
        {"_id": 1, "received_at": 1},
    )
    restored = 0
    async for o in cleared_cursor:
        try:
            recv_day = datetime.fromisoformat(
                str(o["received_at"]).replace("Z", "+00:00")
            ).date().isoformat()
        except Exception:
            continue
        await db.unified_orders.update_one(
            {"_id": o["_id"]},
            {
                "$set": {
                    "order_date": recv_day,
                    "order_date_inferred": True,
                },
                "$unset": {"order_date_missing": ""},
            },
        )
        restored += 1
    if restored:
        logger.info(f"Restored order_date=received_at on {restored} previously-cleared Make.com orders (marked as inferred).")

    # Tag legacy tiktok_ads_daily rows with campaign_id="_default" so the
    # new schema (which uses (user_id, date, campaign_id) as upsert key)
    # treats them as the single-campaign default and doesn't duplicate.
    tt_legacy = await db.tiktok_ads_daily.update_many(
        {"campaign_id": {"$exists": False}},
        {"$set": {"campaign_id": "_default"}},
    )
    if tt_legacy.modified_count:
        logger.info(f"Tagged {tt_legacy.modified_count} legacy tiktok_ads_daily rows with campaign_id='_default'.")

    await seed_admin(db)
    logger.info("Hesab backend started successfully.")


@app.on_event("shutdown")
async def on_shutdown():
    client.close()
