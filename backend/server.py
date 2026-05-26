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


class SettingsIn(BaseModel):
    payment_methods: List[PaymentMethod]
    shipping_companies: List[ShippingCompany]


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
@api.get("/settings")
async def get_settings(user: dict = Depends(current_user)):
    s = await ensure_user_settings(db, user["id"])
    return {
        "payment_methods": s.get("payment_methods", DEFAULT_PAYMENT_METHODS),
        "shipping_companies": s.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
    }


@api.put("/settings")
async def update_settings(payload: SettingsIn, user: dict = Depends(current_user)):
    await db.settings.update_one(
        {"user_id": user["id"]},
        {"$set": {
            "user_id": user["id"],
            "payment_methods": [pm.model_dump() for pm in payload.payment_methods],
            "shipping_companies": [sc.model_dump() for sc in payload.shipping_companies],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    return {"ok": True}


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
def _build_report(parsed: dict, payment_settings, shipping_settings,
                  snapchat_ads=0.0, tiktok_ads=0.0, instagram_ads=0.0, product_costs=0.0) -> dict:
    matched = match_settings(parsed, payment_settings, shipping_settings)
    total_ads = round(float(snapchat_ads) + float(tiktok_ads) + float(instagram_ads), 2)
    net_profit = round(
        float(parsed["total_sales"])
        - float(matched["total_payment_fees"])
        - float(matched["total_shipping_cost"])
        - total_ads
        - float(product_costs),
        2,
    )
    return {
        "summary": {
            "total_sales": parsed["total_sales"],
            "total_orders": parsed["total_orders"],
            "total_payment_fees": matched["total_payment_fees"],
            "total_shipping_cost": matched["total_shipping_cost"],
            "total_ads_cost": total_ads,
            "total_product_cost": float(product_costs),
            "net_revenue_after_fees": round(parsed["total_sales"] - matched["total_payment_fees"], 2),
            "net_profit": net_profit,
        },
        "payment_breakdown": matched["payment_breakdown"],
        "shipping_breakdown": matched["shipping_breakdown"],
        "daily_costs": {
            "snapchat_ads": float(snapchat_ads),
            "tiktok_ads": float(tiktok_ads),
            "instagram_ads": float(instagram_ads),
            "product_costs": float(product_costs),
        },
        "detected_columns": parsed.get("detected_columns", {}),
        "orders_sample": parsed.get("orders_sample", []),
    }


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

    analysis = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "name": name or file.filename,
        "filename": file.filename,
        "date": date or datetime.now(timezone.utc).date().isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report": report,
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
async def dashboard(user: dict = Depends(current_user)):
    """Return aggregated totals across all saved analyses and daily costs."""
    analyses = await db.analyses.find({"user_id": user["id"]}, {"_id": 0, "report.orders_sample": 0}).to_list(1000)
    daily = await db.daily_costs.find({"user_id": user["id"]}, {"_id": 0}).to_list(1000)

    total_sales = sum(a["report"]["summary"]["total_sales"] for a in analyses)
    total_orders = sum(a["report"]["summary"]["total_orders"] for a in analyses)
    total_fees = sum(a["report"]["summary"]["total_payment_fees"] for a in analyses)
    total_shipping = sum(a["report"]["summary"]["total_shipping_cost"] for a in analyses)
    total_ads = sum(a["report"]["summary"].get("total_ads_cost", 0) for a in analyses)
    total_products = sum(a["report"]["summary"].get("total_product_cost", 0) for a in analyses)
    net_profit = sum(a["report"]["summary"]["net_profit"] for a in analyses)

    daily_totals = sum(
        d.get("snapchat_ads", 0) + d.get("snapchat_ads_2", 0)
        + d.get("tiktok_ads", 0) + d.get("instagram_ads", 0)
        + d.get("google_ads", 0) + d.get("product_costs", 0)
        for d in daily
    )

    # Monthly trend (sum sales by month)
    from collections import defaultdict
    monthly_sales = defaultdict(float)
    monthly_profit = defaultdict(float)
    for a in analyses:
        d = (a.get("date") or a["created_at"])[:7]
        monthly_sales[d] += a["report"]["summary"]["total_sales"]
        monthly_profit[d] += a["report"]["summary"]["net_profit"]
    monthly = sorted([
        {"month": k, "sales": round(v, 2), "profit": round(monthly_profit[k], 2)}
        for k, v in monthly_sales.items()
    ], key=lambda x: x["month"])

    return {
        "totals": {
            "total_sales": round(total_sales, 2),
            "total_orders": int(total_orders),
            "total_payment_fees": round(total_fees, 2),
            "total_shipping_cost": round(total_shipping, 2),
            "total_ads_cost": round(total_ads, 2),
            "total_product_cost": round(total_products, 2),
            "net_profit": round(net_profit, 2),
            "daily_costs_total": round(daily_totals, 2),
            "analyses_count": len(analyses),
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
            } for a in sorted(analyses, key=lambda x: x.get("created_at", ""), reverse=True)[:5]
        ],
    }


# ── Health ────────────────────────────────────────────────────────────────────
@api.get("/")
async def root():
    return {"message": "Hesab API is running"}


# ── App wiring ────────────────────────────────────────────────────────────────
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
    await seed_admin(db)
    logger.info("Hesab backend started successfully.")


@app.on_event("shutdown")
async def on_shutdown():
    client.close()
