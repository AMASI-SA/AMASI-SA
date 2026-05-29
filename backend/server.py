"""Hesab — accounting backend for Salla-platform analytics."""
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import uuid
import logging
from datetime import datetime, timezone
from typing import List, Optional

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
from shipping_accounts import attach_shipping_accounts_routes
from webhook_routes import attach_webhook_routes
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


class SettingsIn(BaseModel):
    payment_methods: List[PaymentMethod]
    shipping_companies: List[ShippingCompany]
    # NEW (Phase 1): which order_status values are "approved" for accounting purposes
    shipping_approved_statuses: Optional[List[str]] = None
    cod_approved_statuses: Optional[List[str]] = None


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
    await db.settings.update_one(
        {"user_id": user["id"]},
        {"$set": update_doc},
        upsert=True,
    )
    return {"ok": True}


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

    # ── Unified orders aggregation (THE source of truth) ─────────────────────
    orders_q = {"user_id": user["id"]}
    if from_date or to_date:
        orders_q["order_date"] = {}
        if from_date:
            orders_q["order_date"]["$gte"] = from_date
        if to_date:
            orders_q["order_date"]["$lte"] = to_date

    all_orders = await db.unified_orders.find(
        orders_q, {"_id": 0, "raw_by_source": 0}
    ).to_list(100000)

    if pm_list or ship_list:
        all_orders = [
            o for o in all_orders
            if _matches_any(o.get("payment_method", ""), pm_list)
            and _matches_any(o.get("shipping_company", ""), ship_list)
        ]

    settings = await ensure_user_settings(db, user["id"])
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

    daily_ads_total = sum(
        (d.get("snapchat_ads", 0) or 0) + (d.get("snapchat_ads_2", 0) or 0)
        + (d.get("tiktok_ads", 0) or 0) + (d.get("instagram_ads", 0) or 0)
        + (d.get("google_ads", 0) or 0)
        for d in daily
    )
    daily_products_total = sum((d.get("product_costs", 0) or 0) for d in daily)
    daily_totals = daily_ads_total + daily_products_total

    # Net profit (orders P&L − daily ads − daily products)
    orders_profit = total_sales - total_fees - total_shipping
    net_profit_adjusted = orders_profit - daily_ads_total - daily_products_total

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

    return {
        "range": {"from_date": from_date, "to_date": to_date},
        "totals": {
            "total_sales": round(total_sales, 2),
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
            "total_product_cost": 0.0,
            "daily_expenses_total": round(daily_products_total, 2),
            "net_profit": round(net_profit_adjusted, 2),
            "total_vat": round(total_vat, 2),
            "daily_costs_total": round(daily_totals, 2),
            "daily_ads_total": round(daily_ads_total, 2),
            "daily_products_total": round(daily_products_total, 2),
            "orders_excel_count": src_counts.get("excel", 0),
            "orders_make_count": src_counts.get("make", 0),
            "legacy_analyses_count": len(legacy_analyses),
            # Backward-compatible aliases used by older UI code
            "analyses_count": len(recent),
            "make_orders_count": src_counts.get("make", 0),
            # ── Phase 1: shipping & COD balance splits ───────────────────────
            "shipping_approved": shipping_balance_approved,
            "shipping_unapproved": shipping_balance_unapproved,
            "cod_approved": cod_balance_approved,
            "cod_unapproved": cod_balance_unapproved,
        },
        "monthly": monthly,
        "recent_analyses": [
            {
                "id": a["id"],
                "name": a.get("name"),
                "date": a.get("date"),
                "total_sales": a["report"]["summary"]["total_sales"],
                "net_profit": a["report"]["summary"]["net_profit"],
                "total_orders": a["report"]["summary"]["total_orders"],
            } for a in recent
        ],
    }


# ── Health ────────────────────────────────────────────────────────────────────
@api.get("/")
async def root():
    return {"message": "Hesab API is running"}


# ── App wiring ────────────────────────────────────────────────────────────────
attach_snapchat_routes(api, db)
attach_shipping_accounts_routes(api, db)
attach_webhook_routes(api, db)
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
    await db.shipping_payments.create_index([("user_id", 1), ("company_name", 1), ("payment_date", -1)])
    await db.webhook_tokens.create_index("user_id", unique=True)
    await db.webhook_tokens.create_index("token", unique=True)
    await db.unified_orders.create_index([("user_id", 1), ("order_number", 1)], unique=True)
    await db.unified_orders.create_index([("user_id", 1), ("order_date", -1)])
    await db.unified_orders.create_index([("user_id", 1), ("data_source", 1)])
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
    await seed_admin(db)
    logger.info("Hesab backend started successfully.")


@app.on_event("shutdown")
async def on_shutdown():
    client.close()
