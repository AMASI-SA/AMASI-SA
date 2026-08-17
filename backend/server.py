Warning: truncated output (original token count: 59628)
Total output lines: 5126

"""Hesab — accounting backend for Salla-platform analytics."""
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import uuid
import logging
import asyncio as _asyncio
from datetime import datetime, timezone, timedelta
from tz_utils import riyadh_date_from_utc, riyadh_today_iso
from typing import List, Optional, Dict

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

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from browser_security import BrowserSecurityMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, Field, validator, root_validator

from auth import (
    hash_password,
    validate_bcrypt_secret,
    verify_password,
    account_is_disabled,
    create_access_token,
    create_refresh_token,
    set_auth_cookies,
    clear_auth_cookies,
    refresh_browser_session,
    get_current_user_from_db,
    seed_admin,
    ensure_user_settings,
    DEFAULT_PAYMENT_METHODS,
    DEFAULT_SHIPPING_COMPANIES,
)
from excel_parser import parse_salla_excel, match_settings
from excel_upload_security import read_safe_xlsx_upload
from exports import export_report_excel, export_report_pdf
from release_identity import BOOT_RELEASE_IDENTITY
from report_builder import build_report as _build_report
from meta_routes import attach_meta_routes
from shipping_accounts import attach_shipping_accounts_routes
from webhook_routes import attach_webhook_routes
from product_costs import attach_product_costs_routes, attach_cost_to_order_doc
from preparation_routes import attach_preparation_routes, ensure_preparation_indexes
from salla_integration import attach_salla_routes, ensure_salla_indexes
from settlements_import import attach_payment_settlements_routes, ensure_settlements_indexes as ensure_payment_settlements_indexes
from refunds_alert_routes import attach_refunds_alert_routes
from payment_gateway_metrics import attach_payment_gateway_metrics_routes
from order_status_policy import attach_order_status_policy_routes
from shipping_ledger_routes import attach_shipping_ledger_routes
from orders_explorer_routes import attach_orders_explorer_routes
from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION,
    attach_projected_salla_attribution,
)
from order_engine.routes import make_order_engine_router
from order_activity_routes import make_order_activity_router
from order_item_engine.routes import make_order_item_engine_router
from order_review_image_modes import make_order_review_router
from return_decision_routes import make_return_decision_router
from mezan_mcp import make_mezan_mcp_router
from first_party_attribution import (
    ensure_first_party_attribution_indexes,
    make_first_party_attribution_router,
)
from settlement_cycle import attach_settlement_cycle_routes
from expenses_routes import (
    attach_operating_expenses_routes,
    compute_operating_expenses_for_range,
)
from recurring_obligations_routes import (
    ensure_recurring_obligation_indexes,
    make_recurring_obligations_router,
)
from settlements_routes import (
    attach_settlements_routes,
    aggregate_settlements_by_provider,
    detect_provider as detect_settlement_provider,
    classify_14d_window,
    SALLA_PAYOUT_DAYS,
    ensure_settlements_indexes,
    backfill_settlement_provenance,
)
from accounts_routes import attach_accounts_routes, ensure_accounts_indexes
from liabilities_routes import attach_liabilities_routes, ensure_liabilities_indexes
from counterparties_routes import attach_counterparties_routes, ensure_counterparties_indexes
from purchase_invoices_routes import attach_purchase_invoice_routes, ensure_purchase_invoices_indexes
from custom_app_routes import attach_custom_app_routes, ensure_custom_app_indexes
from ad_account_routes import attach_ad_account_routes, ensure_ad_account_indexes
from bnpl import attach_bnpl_routes, ensure_bnpl_indexes, attach_bnpl_webhook_routes, attach_bnpl_diagnostics_routes, attach_bnpl_audit_routes, attach_bnpl_auto_sync_routes, attach_bnpl_refund_audit_routes, attach_bnpl_settlements_routes, run_auto_sync_for_all_users
from bnpl.auto_sync_service import run_tamara_attribution_sweep
from transfers_routes import attach_transfers_routes, ensure_transfers_indexes
from reconciliation_routes import attach_reconciliation_routes
from diagnostics_routes import attach_diagnostics_routes
from orders_db import upsert_order, orders_to_parsed
from import_jobs import (
    attach_import_jobs_routes,
    ensure_import_jobs_indexes,
    create_job as create_import_job,
    schedule_excel_job,
)
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


@app.get("/health", include_in_schema=False)
@api.get("/health", include_in_schema=False)
async def health_check(response: Response):
    """Deployment health probe; no database or external API calls."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return {
        "ok": True,
        "service": "backend",
        "release": BOOT_RELEASE_IDENTITY,
    }


# ── Dependencies ──────────────────────────────────────────────────────────────
async def current_user(request: Request) -> dict:
    return await get_current_user_from_db(request, db)


# ── Schemas ───────────────────────────────────────────────────────────────────
MIN_PASSWORD_LENGTH = 12


def _public_registration_enabled() -> bool:
    """Public signup is closed unless deployment explicitly opts in."""
    return os.environ.get("AUTH_PUBLIC_REGISTRATION_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _security_question_recovery_enabled() -> bool:
    """Legacy knowledge-based recovery is disabled unless explicitly enabled."""
    return os.environ.get("AUTH_SECURITY_QUESTION_RESET_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)
    _password_utf8_limit = validator("password", allow_reuse=True)(validate_bcrypt_secret)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


# iter-51 — Profile/account management schemas
class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)
    _password_utf8_limit = validator("new_password", allow_reuse=True)(validate_bcrypt_secret)


class ChangeEmailIn(BaseModel):
    current_password: str = Field(min_length=1)
    new_email: EmailStr


class ChangeNameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class SecurityQuestionIn(BaseModel):
    """Save / update the merchant's security question + plain answer.
    The answer is hashed on the server before storage."""
    current_password: str = Field(min_length=1)
    question: str = Field(min_length=4, max_length=200)
    answer: str = Field(min_length=8, max_length=200)
    _answer_utf8_limit = validator("answer", allow_reuse=True)(validate_bcrypt_secret)


class ForgotPasswordCheckIn(BaseModel):
    email: EmailStr


class ForgotPasswordResetIn(BaseModel):
    email: EmailStr
    answer: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)
    _password_utf8_limit = validator("new_password", allow_reuse=True)(validate_bcrypt_secret)


# iter-51 — Multi-user / RBAC schemas
# Roles ranked from highest privilege to lowest. Stored as a single
# string on the user document.
_ROLE_HIERARCHY: tuple[str, ...] = ("owner", "admin", "accountant", "operations", "viewer")

# Permission catalogue — drives the UI checkbox list AND the @require_perm
# dependency on protected routes. Keep keys short, descriptive, lowercase.
PERMISSIONS_CATALOGUE: dict[str, str] = {
    "dashboard.view":         "عرض لوحة التحكم",
    "reports.view":           "عرض التقارير",
    "orders.view":            "عرض الطلبات",
    "orders.manage":          "إدارة الطلبات (إعادة معالجة، حذف)",
    "preparation.view":       "عرض تجهيز المنتجات",
    "preparation.manage":     "إدارة تجهيز المنتجات",
    "product_costs.view":     "عرض تكاليف المنتجات",
    "product_costs.manage":   "إدارة تكاليف المنتجات",
    "operating_expenses.view":   "عرض المصروفات التشغيلية",
    "operating_expenses.manage": "إدارة المصروفات التشغيلية",
    # Iter-250b · P1.5.q — Spend operating-expenses from ANY employee's
    # custody. Without this perm a user may only spend from their own
    # linked employee custody (via `users.linked_employee_id`).
    "accounting.custody.spend_any": "الصرف من عهدة أي موظف (عرفات / مدير)",
    "daily_costs.view":       "عرض التكاليف اليومية",
    "daily_costs.manage":     "إدارة التكاليف اليومية",
    "ads.view":               "عرض الإعلانات (Meta/TikTok/Snap)",
    "ads.manage":             "إدارة الإعلانات وربط الحسابات",
    "salla.view":             "عرض تكامل سلة",
    "salla.manage":           "إدارة تكامل سلة (OAuth + Webhooks)",
    "settings.view":          "عرض الإعدادات",
    "settings.manage":        "إدارة الإعدادات العامة",
    "users.manage":           "إدارة المستخدمين والصلاحيات (Owner فقط)",
}

# Default permissions per role. Override on the user document via
# `extra_permissions` (add) and `denied_permissions` (subtract).
ROLE_DEFAULT_PERMS: dict[str, list[str]] = {
    "meta_reviewer": [],
    "owner": list(PERMISSIONS_CATALOGUE.keys()),  # owner = all
    "admin": [k for k in PERMISSIONS_CATALOGUE if k != "users.manage"],
    "accountant": [
        "dashboard.view", "reports.view", "orders.view",
        "product_costs.view", "product_costs.manage",
        "operating_expenses.view", "operating_expenses.manage",
        "daily_costs.view", "daily_costs.manage",
        "ads.view",
        # Iter-250b · P1.5.q — accountants regularly post entries on
        # behalf of the owner, so they need spend_any too.
        "accounting.custody.spend_any",
    ],
    "operations": [
        "dashboard.view", "orders.view", "orders.manage",
        "preparation.view", "preparation.manage",
    ],
    "viewer": [
        "dashboard.view", "reports.view", "orders.view",
        "preparation.view", "product_costs.view",
        "operating_expenses.view", "daily_costs.view",
        "ads.view", "salla.view", "settings.view",
    ],
}


class TeamUserCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)
    role: str = Field(default="viewer")
    extra_permissions: list[str] = Field(default_factory=list)
    denied_permissions: list[str] = Field(default_factory=list)
    _password_utf8_limit = validator("password", allow_reuse=True)(validate_bcrypt_secret)


class TeamUserUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    role: Optional[str] = None
    extra_permissions: Optional[list[str]] = None
    denied_permissions: Optional[list[str]] = None
    new_password: Optional[str] = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=128)
    _password_utf8_limit = validator("new_password", allow_reuse=True)(validate_bcrypt_secret)


def _effective_perms(user_doc: dict) -> set[str]:
    """Resolve the effective permission set for a user document.

    Formula:  role_defaults ∪ extra_permissions  −  denied_permissions
    The owner ALWAYS has every permission and cannot be downgraded.
    """
    role = (user_doc.get("role") or "").lower()
    if role == "owner":
        return set(PERMISSIONS_CATALOGUE.keys())
    # Unknown/missing roles fail closed. They must never inherit viewer access.
    base = set(ROLE_DEFAULT_PERMS.get(role, []))
    base |= set(user_doc.get("extra_permissions") or [])
    base -= set(user_doc.get("denied_permissions") or [])
    return base


def _is_owner(user_doc: dict) -> bool:
    return (user_doc.get("role") or "").lower() == "owner"


class PaymentMethod(BaseModel):
    name: str
    commission_percent: float = Field(ge=0, le=100, default=0.0)
    fixed_fee: float = Field(ge=0, default=0.0)
    vat_percent: float = Field(ge=0, le=100, default=0.0)


class ShippingCompany(BaseModel):
    name: str
    cost_per_order: float = Field(ge=0)
    vat_percent: float = Field(ge=0, le=100, default=0.0)
    # Iter-189 — `payment_mode` is the new merchant-facing label that
    # supersedes the boolean `is_deferred`. Both fields are accepted on
    # write and BOTH are returned on read so the existing UIs that
    # still read `is_deferred` keep working unchanged. Internally only
    # `is_deferred` is persisted (single source of truth).
    #   payment_mode="prepaid"  ⇒ is_deferred=False (دفع مقدم)
    #   payment_mode="deferred" ⇒ is_deferred=True  (دفع آجل)
    payment_mode: Optional[str] = Field(default=None,
                                        description="prepaid|deferred")
    is_deferred: bool = False  # SSOT field persisted to the DB
    # Iter-155 — COD fee fields used by the new ShippingCompanySettings
    # page. The frontend persists these into shipping_companies but
    # they were previously stripped on the way out because they
    # weren't declared here.
    cod_fee_percent: Optional[float] = Field(default=0.0, ge=0, le=1)
    cod_fee_fixed_per_order: Optional[float] = Field(default=0.0, ge=0)

    @root_validator(pre=False, skip_on_failure=True)
    def _sync_payment_mode(cls, values):  # noqa: N805
        """Bi-directional sync between `payment_mode` and `is_deferred`.
        `payment_mode` is the merchant-facing label; `is_deferred` is the
        persisted SSOT. If both are provided, `payment_mode` wins.
        """
        pm = values.get("payment_mode")
        if isinstance(pm, str):
            pm = pm.strip().lower()
            if pm not in ("prepaid", "deferred"):
                raise ValueError(
                    "payment_mode must be 'prepaid' or 'deferred'")
            values["payment_mode"] = pm
            values["is_deferred"] = (pm == "deferred")
        else:
            values["payment_mode"] = (
                "deferred" if values.get("is_deferred") else "prepaid")
        return values


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


# ── iter-45 — Electronic Net status semantics ─────────────────────────────
# "صافي المدفوعات الإلكترونية" should match Salla's "غير المفوترة" screen,
# which only shows transactions where money was successfully captured by
# the gateway. Until we have a per-transaction store from Salla's Payments
# API, we approximate by EXCLUDING orders whose status indicates the
# payment was never captured (or was reversed).
#
# Defaults below cover Salla's standard Arabic + the most common English
# equivalents. Match is case-insensitive partial substring — "ملغ" matches
# "ملغي", "ملغية", "تم الإلغاء" etc.
DEFAULT_ELECTRONIC_NET_EXCLUDED_STATUSES: list[str] = [
    "ملغ",          # ملغي / ملغية / تم الإلغاء
    "مسترد",        # مسترد / تم الاسترداد
    "مرتجع",        # مرتجع / تم الإرجاع
    "فشل",          # فشل الدفع
    "مرفوض",        # مرفوض من البوابة
    "بانتظار الدفع",  # لم يتم استلام المبلغ بعد
    "cancel",       # cancelled / canceled
    "refund",       # refunded
    "fail",         # failed
    "reject",       # rejected
    "pending payment",
]


def _is_excluded_for_electronic_net(status: str, excluded_terms: list[str]) -> bool:
    """Return True when the order status matches ANY of the excluded terms.

    Matching is case-insensitive substring — captures "ملغ" → "تم إلغاء الطلب",
    "fail" → "Payment Failed", etc. Empty status is NOT excluded (kept for
    backwards compatibility with orders that lack a status field).
    """
    if not status:
        return False
    s = status.strip().lower()
    for t in excluded_terms:
        if t and t.strip().lower() in s:
            return True
    return False


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
    # Iter-141 — per-user list of sidebar nav-item testids to hide.
    # Persisted in MongoDB so the choice is mirrored across every
    # device the merchant logs in from (replaces the old
    # localStorage-only `mezan.sidebar.hidden_pages`).
    sidebar_hidden_pages: Optional[List[str]] = None
    # Iter-182 — operation types hidden from the «حركة مالية جديدة»
    # screen. Stored as a list of OP_TYPES.value strings. Empty list
    # means "show all" (default). Setting is account-wide.
    hidden_transaction_types: Optional[List[str]] = None
    # Iter-184 — per-operation allowed-accounts binding. Maps an
    # operation type (e.g. "salary_settle", "supplier_pay") to the
    # list of account IDs the merchant explicitly allows for that
    # operation. Empty list / missing key = "السماح للكل" (legacy
    # default). The Backend enforces the binding in every
    # cash-touching endpoint, not just the UI.
    operation_account_bindings: Optional[Dict[str, List[str]]] = None
    # Iter-246c — Per-op allow-list of withdrawal methods
    # ("cash" / "transfer" / "pos").  Empty / missing = allow-all.
    operation_withdrawal_methods: Optional[Dict[str, List[str]]] = None
    # NEW (Phase 3): toggles for what gets deducted from "net sales" KPI.
    net_sales_config: Optional[NetSalesConfig] = None
    # NEW: hide Make.com orders with inferred (approximate) date from dashboard/reports.
    # When True, only orders with authoritative date (from Excel or Make.com
    # webhook that included created_at) are counted in dashboard KPIs.
    hide_inferred_date_orders: Optional[bool] = None
    # iter-45 — extra statuses that should NEVER count in the "صافي المدفوعات
    # الإلكترونية" KPI, even when `report_included_statuses` is empty. When
    # None (default) the bundled DEFAULT_ELECTRONIC_NET_EXCLUDED_STATUSES is
    # used. Empty list means "include all statuses" (legacy behaviour).
    electronic_net_excluded_statuses: Optional[List[str]] = None
    # Optional Salla reference figure used in the debug endpoint to show
    # the gap between our computed net and the merchant's actual Salla
    # "غير المفوترة" total. Pure UX — not used in any calculation.
    salla_electronic_net_reference: Optional[float] = None
    # Iter-78 — whether the delete button on the Payment Settlements
    # history table is shown. Defaults to False so the merchant doesn't
    # accidentally roll back a settlement file. Can be toggled from the
    # Settings page.
    settlements_allow_delete: Optional[bool] = None
    # Iter-110 — whether the delete button on each Ad-Account card is
    # shown. Defaults to False (hidden) so the merchant doesn't
    # accidentally delete an active counterparty. The DELETE endpoint
    # itself still blocks deletion when balance>0 or open debt exists.
    ad_account_allow_delete: Optional[bool] = None
    # Iter-250b · Phase 3.7 — visibility toggles for the supplier-
    # invoice line-item columns (discount / tax / notes). Hidden by
    # default to keep the form clean for merchants who don't apply
    # per-line VAT or discounts. Can be enabled from /settings.
    supplier_invoice_show_discount: Optional[bool] = None
    supplier_invoice_show_tax:      Optional[bool] = None
    supplier_invoice_show_notes:    Optional[bool] = None
    # Iter-251 · Phase 1.5 — Default receiving bank per payment
    # provider. When an auto-created BankTransferReview is missing a
    # target_bank_id, the system tries to resolve from these fields.
    # If still empty → status `missing_target_bank` until a Reviewer
    # assigns a bank manually. NO GL post happens until then.
    default_bank_for_salla:  Optional[str] = None
    default_bank_for_tamara: Optional[str] = None
    default_bank_for_tabby:  Optional[str] = None
    default_bank_for_imkan:  Optional[str] = None
    # Iter-251 · Phase 2A — Settlement Engine feature flags.
    # All default to False — actual automation stays disabled until
    # the merchant explicitly toggles via /settlement-engine/feature-flags.
    settlement_engine_enabled:             Optional[bool] = None
    platform_settlement_to_review_enabled: Optional[bool] = None
    bank_transfer_review_enabled:          Optional[bool] = None


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
@api.post("/auth/register", include_in_schema=_public_registration_enabled())
async def register(payload: RegisterIn, response: Response):
    if not _public_registration_enabled():
        raise HTTPException(status_code=404, detail="Not found")
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
    if (
        not user
        or account_is_disabled(user)
        or not verify_password(payload.password, user.get("password_hash", ""))
    ):
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


@api.post("/auth/refresh")
async def refresh_session(request: Request, response: Response):
    return await refresh_browser_session(request, response, db)


@api.get("/auth/me")
async def me(user: dict = Depends(current_user)):
    # Employee OS V2 is the authority for operational roles.  Merge its
    # effective permissions into the session payload so the UI and the API
    # make the same Customer Intelligence decision.  Legacy Admin does not
    # inherit these capabilities merely from its broad legacy role.
    from ai_store_access_contract import merged_session_permissions

    permissions = await merged_session_permissions(
        db,
        user,
        _effective_perms(user),
    )
    return {
        "id": user["id"], "name": user.get("name"), "email": user["email"],
        "role": user.get("role", "user"),
        # iter-51 — surface effective permissions + Salla owner flag so the
        # frontend can drive RBAC navigation without an extra round-trip.
        "permissions": permissions,
        "is_owner": _is_owner(user),
        "has_security_question": bool(user.get("security_question")),
        "review_scopes": user.get("review_scopes") or [],
        "review_access_expires_at": user.get("review_access_expires_at"),
    }


# ── iter-51 — Profile / account self-management ────────────────────────────
@api.put("/auth/profile/name")
async def update_my_name(payload: ChangeNameIn, user: dict = Depends(current_user)):
    """Lets a logged-in user update their display name."""
    new_name = payload.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="الاسم لا يمكن أن يكون فارغاً")
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"name": new_name, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {"ok": True, "name": new_name}


@api.put("/auth/profile/password")
async def change_my_password(payload: ChangePasswordIn, user: dict = Depends(current_user)):
    """Change own password. Requires the current password for security."""
    # `current_user` strips password_hash for safety; re-fetch the full doc.
    full = await db.users.find_one({"id": user["id"]})
    if not full or not verify_password(payload.current_password, full.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="كلمة المرور الحالية غير صحيحة")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="كلمة المرور الجديدة يجب أن تختلف عن الحالية")
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            "password_hash": hash_password(payload.new_password),
            "password_updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    return {"ok": True}


@api.put("/auth/profile/email")
async def change_my_email(payload: ChangeEmailIn, user: dict = Depends(current_user)):
    """Change own email. Requires current password + email uniqueness."""
    full = await db.users.find_one({"id": user["id"]})
    if not full or not verify_password(payload.current_password, full.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="كلمة المرور الحالية غير صحيحة")
    new_email = payload.new_email.lower()
    if new_email == user["email"]:
        raise HTTPException(status_code=400, detail="البريد الإلكتروني الجديد مطابق للحالي")
    existing = await db.users.find_one({"email": new_email})
    if existing and existing.get("id") != user["id"]:
        raise HTTPException(status_code=400, detail="هذا البريد الإلكتروني مسجل بالفعل")
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"email": new_email, "email_updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {"ok": True, "email": new_email}


@api.put("/auth/profile/security-question")
async def set_security_question(payload: SecurityQuestionIn, user: dict = Depends(current_user)):
    """Legacy knowledge-based recovery is unavailable by default."""
    if not _security_question_recovery_enabled():
        raise HTTPException(
            status_code=410,
            detail={
                "code": "security_question_recovery_disabled",
                "message": "سؤال الأمان متوقف. استخدم استرداداً يوافق عليه مالك النظام.",
            },
        )
    full = await db.users.find_one({"id": user["id"]})
    if not full or not verify_password(payload.current_password, full.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="كلمة المرور الحالية غير صحيحة")
    normalized_answer = payload.answer.strip().lower()
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            "security_question": payload.question.strip(),
            "security_answer_hash": hash_password(normalized_answer),
            "security_question_updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    return {"ok": True, "question": payload.question.strip()}


# ── iter-51 — Password recovery via security question (no email needed) ────
@api.post("/auth/forgot-password/check")
async def forgot_password_check(payload: ForgotPasswordCheckIn):
    """Return no account-specific data while legacy recovery is disabled."""
    if not _security_question_recovery_enabled():
        return {
            "question": "الاسترداد الذاتي متوقف. تواصل مع مالك النظام.",
            "has_question": False,
            "recovery_method": "contact_owner",
        }
    user = await db.users.find_one({"email": payload.email.lower()})
    if user and user.get("security_question"):
        return {"question": user["security_question"], "has_question": True}
    return {"question": "تعذّر بدء الاسترداد الذاتي.", "has_question": False}


@api.post("/auth/forgot-password/reset")
async def forgot_password_reset(payload: ForgotPasswordResetIn):
    """Legacy reset remains unavailable until purpose-bound email OTP lands."""
    if not _security_question_recovery_enabled():
        raise HTTPException(
            status_code=403,
            detail={
                "code": "security_question_recovery_disabled",
                "message": "الاسترداد الذاتي متوقف. تواصل مع مالك النظام.",
            },
        )
    user = await db.users.find_one({"email": payload.email.lower()})
    if not user or not user.get("security_answer_hash"):
        # Same generic message → no enumeration.
        raise HTTPException(status_code=400, detail="تعذّر التحقق من بيانات الاسترداد")
    normalized = payload.answer.strip().lower()
    if not verify_password(normalized, user["security_answer_hash"]):
        raise HTTPException(status_code=400, detail="إجابة سؤال الأمان غير صحيحة")
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            "password_hash": hash_password(payload.new_password),
            "password_updated_at": datetime.now(timezone.utc).isoformat(),
            "password_reset_via_security": datetime.now(timezone.utc).isoformat(),
        }},
    )
    return {"ok": True}


# ── iter-51 — Permissions catalogue (drives the Settings UI) ──────────────
@api.get("/auth/permissions/catalogue")
async def get_permissions_catalogue(user: dict = Depends(current_user)):
    """Return the permissions list + role defaults so the team-management
    UI can render checkboxes without hardcoding strings."""
    return {
        "permissions": [{"key": k, "label": v} for k, v in PERMISSIONS_CATALOGUE.items()],
        "role_defaults": ROLE_DEFAULT_PERMS,
        "roles_ordered": list(_ROLE_HIERARCHY),
    }


# ── iter-51 — Team / multi-user CRUD (owner only) ─────────────────────────
def _require_owner(user: dict) -> None:
    if not _is_owner(user):
        raise HTTPException(status_code=403, detail="هذه العملية متاحة لـ Owner فقط")


def _public_user_view(u: dict) -> dict:
    """Strip sensitive fields before sending a user document to the UI."""
    return {
        "id": u["id"],
        "name": u.get("name", ""),
        "email": u.get("email", ""),
        "role": u.get("role", "viewer"),
        "extra_permissions": sorted(u.get("extra_permissions") or []),
        "denied_permissions": sorted(u.get("denied_permissions") or []),
        "effective_permissions": sorted(_effective_perms(u)),
        "is_owner": _is_owner(u),
        "disabled": u.get("disabled") is True,
        "is_active": u.get("is_active") is not False,
        "created_at": u.get("created_at"),
        "updated_at": u.get("updated_at"),
        "last_login_at": u.get("last_login_at"),
    }


@api.get("/team/users")
async def list_team_users(user: dict = Depends(current_user)):
    _require_owner(user)
    # Sort newest first so freshly created users always appear at the top
    # of the team management screen. Cap at 5000 (production-safe).
    docs = await db.users.find(
        {}, {"_id": 0, "password_hash": 0, "security_answer_hash": 0}
    ).sort("created_at", -1).to_list(5000)
    return [_public_user_view(u) for u in docs]


@api.post("/team/users")
async def create_team_user(payload: TeamUserCreateIn, user: dict = Depends(current_user)):
    _require_owner(user)
    role = (payload.role or "viewer").lower()
    if role not in _ROLE_HIERARCHY:
        raise HTTPException(status_code=400, detail=f"الدور غير صالح: {role}")
    if role == "owner":
        raise HTTPException(
            status_code=400,
            detail="لا يمكن إنشاء مستخدم Owner — Owner واحد فقط لكل حساب",
        )
    email = payload.email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="هذا البريد الإلكتروني مسجل بالفعل")
    # Validate permission keys
    for p in (payload.extra_permissions + payload.denied_permissions):
        if p not in PERMISSIONS_CATALOGUE:
            raise HTTPException(status_code=400, detail=f"صلاحية غير معروفة: {p}")
    now = datetime.now(timezone.utc).isoformat()
    new_doc = {
        "id": str(uuid.uuid4()),
        "name": payload.name.strip(),
        "email": email,
        "password_hash": hash_password(payload.password),
        "role": role,
        "extra_permissions": payload.extra_permissions,
        "denied_permissions": payload.denied_permissions,
        "created_at": now,
        "created_by": user["id"],
    }
    await db.users.insert_one(new_doc)
    return _public_user_view(new_doc)


@api.put("/team/users/{user_id}")
async def update_team_user(
    user_id: str,
    payload: TeamUserUpdateIn,
    user: dict = Depends(current_user),
):
    _require_owner(user)
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    if _is_owner(target) and user_id != user["id"]:
        raise HTTPException(status_code=403, detail="لا يمكن تعديل Owner")
    set_ops: dict = {}
    if payload.name is not None:
        new_name = payload.name.strip()
        if new_name:
            set_ops["name"] = new_name
    if payload.role is not None:
        role = payload.role.lower()
        if role not in _ROLE_HIERARCHY:
            raise HTTPException(status_code=400, detail=f"الدور غير صالح: {role}")
        if role == "owner":
            raise HTTPException(status_code=400, detail="لا يمكن ترقية مستخدم إلى Owner")
        set_ops["role"] = role
    if payload.extra_permissions is not None:
        for p in payload.extra_permissions:
            if p not in PERMISSIONS_CATALOGUE:
                raise HTTPException(status_code=400, detail=f"صلاحية غير معروفة: {p}")
        set_ops["extra_permissions"] = payload.extra_permissions
    if payload.denied_permissions is not None:
        for p in payload.denied_permissions:
            if p not in PERMISSIONS_CATALOGUE:
                raise HTTPException(status_code=400, detail=f"صلاحية غير معروفة: {p}")
        set_ops["denied_permissions"] = payload.denied_permissions
    if payload.new_password:
        set_ops["password_hash"] = hash_password(payload.new_password)
        set_ops["password_updated_at"] = datetime.now(timezone.utc).isoformat()
    if not set_ops:
        raise HTTPException(status_code=400, detail="لا يوجد ما يُحدَّث")
    set_ops["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.users.update_one({"id": user_id}, {"$set": set_ops})
    updated = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0, "security_answer_hash": 0})
    return _public_user_view(updated)


@api.delete("/team/users/{user_id}")
async def delete_team_user(user_id: str, user: dict = Depends(current_user)):
    _require_owner(user)
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="لا يمكنك حذف نفسك")
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    if _is_owner(target):
        raise HTTPException(status_code=403, detail="لا يمكن حذف Owner")
    await db.users.delete_one({"id": user_id})
    return {"ok": True}


# ── Settings ──────────────────────────────────────────────────────────────────
DEFAULT_SHIPPING_APPROVED = ["تم التوصيل", "delivered", "completed", "تم الاستلام"]
DEFAULT_COD_APPROVED = ["تم التوصيل", "delivered", "completed"]


@api.get("/settings")
async def get_settings(user: dict = Depends(current_user)):
    s = await ensure_user_settings(db, user["id"])
    # Iter-189 — augment every shipping_companies entry with a derived
    # `payment_mode` so the UI can render it without recomputing.
    sc_raw = s.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES) or []
    sc_enriched = [
        {**c, "payment_mode": "deferred" if c.get("is_deferred") else "prepaid"}
        for c in sc_raw
    ]
    return {
        "payment_methods": s.get("payment_methods", DEFAULT_PAYMENT_METHODS),
        "shipping_companies": sc_enriched,
        "shipping_approved_statuses": s.get("shipping_approved_statuses", DEFAULT_SHIPPING_APPROVED),
        "cod_approved_statuses": s.get("cod_approved_statuses", DEFAULT_COD_APPROVED),
        "report_included_statuses": s.get("report_included_statuses", []),
        "dashboard_hidden_cards": s.get("dashboard_hidden_cards", []),
        # Iter-141 — sidebar visibility now syncs across devices.
        "sidebar_hidden_pages": s.get("sidebar_hidden_pages", []),
        "net_sales_config": s.get("net_sales_config", DEFAULT_NET_SALES_CONFIG),
        "hide_inferred_date_orders": bool(s.get("hide_inferred_date_orders", False)),
        # iter-45 — electronic net status filter
        "electronic_net_excluded_statuses": s.get(
            "electronic_net_excluded_statuses", DEFAULT_ELECTRONIC_NET_EXCLUDED_STATUSES,
        ),
        "salla_electronic_net_reference": s.get("salla_electronic_net_reference"),
        # Iter-78 — payment-settlements delete-button visibility
        "settlements_allow_delete": bool(s.get("settlements_allow_delete", False)),
        # Iter-110 — ad-account delete-button visibility
        "ad_account_allow_delete": bool(s.get("ad_account_allow_delete", False)),
        # Iter-250b · Phase 3.7 — supplier-invoice line-item columns
        # (hidden by default).
        "supplier_invoice_show_discount": bool(s.get("supplier_invoice_show_discount", False)),
        "supplier_invoice_show_tax":      bool(s.get("supplier_invoice_show_tax", False)),
        "supplier_invoice_show_notes":    bool(s.get("supplier_invoice_show_notes", False)),
        # Iter-251 · Phase 1.5 — default receiving bank per provider.
        "default_bank_for_salla":  s.get("default_bank_for_salla")  or None,
        "default_bank_for_tamara": s.get("default_bank_for_tamara") or None,
        "default_bank_for_tabby":  s.get("default_bank_for_tabby")  or None,
        "default_bank_for_imkan":  s.get("default_bank_for_imkan")  or None,
        # Iter-251 · Phase 2A — Settlement Engine feature flags.
        "settlement_engine_enabled":
            bool(s.get("settlement_engine_enabled", False)),
        "platform_settlement_to_review_enabled":
            bool(s.get("platform_settlement_to_review_enabled", False)),
        "bank_transfer_review_enabled":
            bool(s.get("bank_transfer_review_enabled", False)),
        # Iter-182 — operation types hidden from "new transaction" picker.
        "hidden_transaction_types": s.get("hidden_transaction_types", []),
        # Iter-184 — operation→accounts binding (per-op allow-list).
        "operation_account_bindings": s.get("operation_account_bindings", {}),
        # Iter-246c — operation→withdrawal-methods binding.
        "operation_withdrawal_methods": s.get("operation_withdrawal_methods", {}),
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
    # Iter-141 — cross-device sidebar visibility.
    if payload.sidebar_hidden_pages is not None:
        update_doc["sidebar_hidden_pages"] = [
            s.strip() for s in payload.sidebar_hidden_pages if s.strip()
        ]
    if payload.net_sales_config is not None:
        update_doc["net_sales_config"] = payload.net_sales_config.model_dump()
    if payload.hide_inferred_date_orders is not None:
        update_doc["hide_inferred_date_orders"] = bool(payload.hide_inferred_date_orders)
    # iter-45 — electronic net status filter overrides
    if payload.electronic_net_excluded_statuses is not None:
        update_doc["electronic_net_excluded_statuses"] = [
            s.strip() for s in payload.electronic_net_excluded_statuses if s.strip()
        ]
    if payload.salla_electronic_net_reference is not None:
        # Allow 0 to mean "clear the reference" (settable to None via JSON null).
        ref = float(payload.salla_electronic_net_reference)
        update_doc["salla_electronic_net_reference"] = ref if ref > 0 else None
    if payload.settlements_allow_delete is not None:
        update_doc["settlements_allow_delete"] = bool(payload.settlements_allow_delete)
    if payload.ad_account_allow_delete is not None:
        update_doc["ad_account_allow_delete"] = bool(payload.ad_account_allow_delete)
    # Iter-250b · Phase 3.7 — supplier-invoice line-item column visibility.
    if payload.supplier_invoice_show_discount is not None:
        update_doc["supplier_invoice_show_discount"] = bool(payload.supplier_invoice_show_discount)
    if payload.supplier_invoice_show_tax is not None:
        update_doc["supplier_invoice_show_tax"] = bool(payload.supplier_invoice_show_tax)
    if payload.supplier_invoice_show_notes is not None:
        update_doc["supplier_invoice_show_notes"] = bool(payload.supplier_invoice_show_notes)
    # Iter-251 · Phase 1.5 — default receiving bank per provider.
    for _prov in ("salla", "tamara", "tabby", "imkan"):
        _val = getattr(payload, f"default_bank_for_{_prov}", None)
        if _val is not None:
            update_doc[f"default_bank_for_{_prov}"] = (
                (_val or "").strip() or None)
    # Iter-182 — hidden transaction types in the unified entry picker.
    if payload.hidden_transaction_types is not None:
        update_doc["hidden_transaction_types"] = [
            s.strip() for s in payload.hidden_transaction_types if s and s.strip()
        ]
    # Iter-184 — operation→accounts binding. Empty list per op means
    # "السماح للكل". We sanitize each value to strings and drop dups.
    if payload.operation_account_bindings is not None:
        cleaned: Dict[str, List[str]] = {}
        for op, accs in payload.operation_account_bindings.items():
            if not isinstance(op, str) or not op.strip():
                continue
            seen: set = set()
            cleaned[op.strip()] = [
                a for a in (accs or [])
                if isinstance(a, str) and a.strip() and not (a in seen or seen.add(a))
            ]
        update_doc["operation_account_bindings"] = cleaned
    # Iter-246c — operation→withdrawal-methods binding.  Empty list per
    # op means "السماح للكل".  Sanitize to {"cash","transfer","pos"}.
    if payload.operation_withdrawal_methods is not None:
        _ALLOWED_WM = {"cash", "transfer", "pos"}
        cleaned_wm: Dict[str, List[str]] = {}
        for op, methods in payload.operation_withdrawal_methods.items():
            if not isinstance(op, str) or not op.strip():
                continue
            seen_wm: set = set()
            cleaned_wm[op.strip()] = [
                m for m in (methods or [])
                if isinstance(m, str) and m in _ALLOWED_WM
                and not (m in seen_wm or seen_wm.add(m))
            ]
        update_doc["operation_withdrawal_methods"] = cleaned_wm
    await db.settings.update_one(
        {"user_id": user["id"]},
        {"$set": update_doc},
        upsert=True,
    )
    return {"ok": True}


# ── Iter-157 — Financial Input Hub: recent entries feed ─────────────────────
# Returns a paginated, unified list of the merchant's recent inputs across
# the four hub tabs: new liabilities, payments, salary advances, employee
# settlements. Used by the table at the bottom of the Hub page.
@api.get("/financial-input-hub/recent")
async def financial_input_hub_recent(
    page: int = 1,
    page_size: int = 10,
    q: Optional[str] = None,
    op_filter: Optional[str] = None,
    user: dict = Depends(current_user),
):
    uid = user["id"]
    page = max(1, page)
    page_size = max(1, min(50, page_size))

    # Source 1: all liabilities (creations + auto-generated salary rows
    # are excluded — only merchant-initiated entries).
    liabs = await db.liabilities.find(
        {"user_id": uid, "auto_generated": {"$ne": True}},
        {"_id": 0},
    ).sort("created_at", -1).to_list(1000)

    # Source 2: ALL merchant-facing bank transactions from the input
    # hub (debt_payment, salary_advance, ad_account_topup,
    # salary_settlement, deposit, expense, courier_transfer, cod_transfer,
    # receivable_collect, shipping_payment, etc.).  We exclude only
    # bank↔bank internal transfers since those have a dedicated page.
    tx_types_in_hub = [
        "debt_payment", "salary_advance", "ad_account_topup",
        "salary_settlement", "expense", "deposit", "withdrawal",
        "courier_transfer", "cod_transfer", "receivable_collect",
        "shipping_payment", "expense_payment", "topup",
    ]
    txs = await db.account_transactions.find(
        {"user_id": uid,
         "transaction_type": {"$in": tx_types_in_hub}},
        {"_id": 0},
    ).sort("created_at", -1).to_list(1000)

    # Normalize into a single feed.
    feed = []
    for l in liabs:
        kind = l.get("kind", "")
        op = (
            "إنشاء التزام" if kind == "supplier"
            else "إنشاء التزام إعلانات" if kind == "ad_account"
            else "سلفة موظف" if kind == "salary_advance"
            else "التزام راتب" if kind == "salary"
            else "إنشاء عميل دائن" if kind == "receivable"
            else f"التزام ({kind})"
        )
        feed.append({
            "id": l["id"],
            "type": "liability",
            "ref_id": l["id"],
            "operation": op,
            "kind": kind,
            "party_name": (l.get("counterparty_name") or l.get("description") or "—"),
            "amount": float(l.get("expected_amount") or 0),
            "paid_amount": float(l.get("paid_amount") or 0),
            "status": l.get("status"),
            "created_at": l.get("created_at"),
            "editable": kind != "salary_advance",
        })
    for t in txs:
        op_label = {
            "debt_payment": "سداد التزام",
            "salary_advance": "صرف سلفة",
            "ad_account_topup": "شحن حساب إعلاني",
            "salary_settlement": "تسوية موظف",
            "expense": "مصروف يومي",
            "expense_payment": "دفع مصروف",
            "deposit": "إيداع",
            "withdrawal": "سحب",
            "courier_transfer": "تحويل شركة شحن",
            "cod_transfer": "تحويل COD",
            "receivable_collect": "تحصيل من عميل",
            "shipping_payment": "دفع شركة شحن",
            "topup": "شحن",
        }.get(t.get("transaction_type") or "", t.get("transaction_type"))
        feed.append({
            "id": t["id"],
            "type": "transaction",
            "ref_id": t.get("peer_liability_id") or t["id"],
            "operation": op_label,
            "kind": t.get("transaction_type"),
            "party_name": (
                t.get("description", "")
                .replace("سداد لـ ", "")
                .replace("شحن لـ ", "") or "—"
            )[:80],
            "amount": float(t.get("amount") or 0),
            "paid_amount": float(t.get("amount") or 0),
            "status": "posted",
            "created_at": t.get("created_at") or t.get("transaction_date"),
            "editable": False,  # editing a posted bank tx requires care
        })

    feed.sort(key=lambda x: (x.get("created_at") or ""), reverse=True)

    # ── Iter-157b — Search + operation filter ──
    if q:
        q_low = q.strip().lower()
        if q_low:
            feed = [
                it for it in feed
                if q_low in (it.get("party_name") or "").lower()
                or q_low in (it.get("operation") or "").lower()
            ]
    if op_filter:
        f = op_filter.lower()
        if f in ("create", "إنشاء"):
            feed = [it for it in feed if it["type"] == "liability"]
        elif f in ("pay", "سداد"):
            feed = [it for it in feed
                    if "سداد" in (it.get("operation") or "")
                    or "تسوية" in (it.get("operation") or "")]
        elif f in ("advance", "سلفة"):
            feed = [it for it in feed
                    if "سلفة" in (it.get("operation") or "")]
        elif f in ("expense", "مصروف"):
            feed = [it for it in feed
                    if "مصروف" in (it.get("operation") or "")]

    total = len(feed)
    start = (page - 1) * page_size
    items = feed[start: start + page_size]

    # Enrich with directional balances for the party:
    #  • owed_to_party   (كم له)  → what WE owe them right now
    #                                (supplier / salary / ad_account)
    #  • owed_from_party (كم عليه) → what THEY owe US right now
    #                                (salary_advance / receivable)
    #
    # Step 1 — collect referenced liability ids from the FULL filtered feed
    # (so totals reflect the active search/filter, not just the visible page).
    liab_id_refs = set()
    for it in feed:
        if it.get("ref_id"):
            liab_id_refs.add(it["ref_id"])

    # Step 2 — fetch those liabilities to map (liability_id) → party_id.
    item_to_party = {}    # liability_id → party_id
    item_to_party_kind = {}  # liability_id → "counterparty" | "employee"
    party_ids_all = set()
    counterparty_ids = set()
    employee_salary_ids = set()
    if liab_id_refs:
        rows = await db.liabilities.find(
            {"user_id": uid, "id": {"$in": list(liab_id_refs)}},
            {"_id": 0, "id": 1, "counterparty_id": 1, "employee_salary_id": 1},
        ).to_list(2000)
        for r in rows:
            cp = r.get("counterparty_id")
            es = r.get("employee_salary_id")
            pid = cp or es
            if pid:
                item_to_party[r["id"]] = pid
                party_ids_all.add(pid)
                if cp:
                    counterparty_ids.add(cp)
                    item_to_party_kind[r["id"]] = "counterparty"
                else:
                    employee_salary_ids.add(es)
                    item_to_party_kind[r["id"]] = "employee"

    # Step 2b — resolve party_id → clean beneficiary name from canonical
    # entities (counterparties + operating_salaries).  This fixes rows like
    # "تسوية سلفة" where description was used as a placeholder for the
    # actual employee name.
    party_name_map = {}
    if counterparty_ids:
        for r in await db.counterparties.find(
            {"user_id": uid, "id": {"$in": list(counterparty_ids)}},
            {"_id": 0, "id": 1, "name": 1},
        ).to_list(2000):
            party_name_map[r["id"]] = r.get("name") or ""
    if employee_salary_ids:
        for r in await db.operating_salaries.find(
            {"user_id": uid, "id": {"$in": list(employee_salary_ids)}},
            {"_id": 0, "id": 1, "name": 1},
        ).to_list(2000):
            party_name_map[r["id"]] = r.get("name") or ""

    # Step 3 — for each unique party, compute both directional balances in
    # a single aggregation grouped by kind.
    OWE_THEM_KINDS = ("supplier", "salary", "ad_account")
    THEY_OWE_KINDS = ("salary_advance", "receivable")
    party_balances = {}  # party_id → {"owed_to_party": x, "owed_from_party": y}
    if party_ids_all:
        agg = await db.liabilities.aggregate([
            {"$match": {
                "user_id": uid,
                "status": {"$in": ["unpaid", "partial"]},
                "$or": [
                    {"counterparty_id": {"$in": list(party_ids_all)}},
                    {"employee_salary_id": {"$in": list(party_ids_all)}},
                ],
            }},
            {"$group": {
                "_id": {
                    "party": {"$ifNull": ["$counterparty_id",
                                          "$employee_salary_id"]},
                    "kind": "$kind",
                },
                "total": {"$sum": {"$subtract": [
                    "$expected_amount", "$paid_amount"]}},
            }},
        ]).to_list(5000)
        for row in agg:
            pid = row["_id"]["party"]
            kind =…29628 tokens truncated… they default
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
    # Iter-160 SSOT: spend comes from ad_account_ledger only.
    snap_spend_by_date = await _spend_by_date_from_ledger(
        db, uid, ("snapchat", "snap"), fd_str, td_str,
    )
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
    # Iter-160 SSOT: spend from ledger; other metrics from tiktok_ads_daily
    tt_spend_by_date = await _spend_by_date_from_ledger(
        db, uid, ("tiktok",), fd_str, td_str,
    )
    tt_spend = sum(tt_spend_by_date.values())
    tt_imp = sum(int(r.get("impressions") or 0) for r in tt_rows)
    tt_clicks = sum(int(r.get("clicks") or 0) for r in tt_rows)
    tt_purchases = sum(int(r.get("purchases") or 0) for r in tt_rows)
    tt_revenue = sum(float(r.get("revenue") or 0) for r in tt_rows)
    tiktok = {
        "platform": "tiktok",
        "label": "TikTok",
        **_metrics(tt_spend, tt_imp, tt_clicks, tt_purchases, tt_revenue),
    }

    # ── Meta ─────────────────────────────────────────────────────────────
    meta_rows = await db.meta_ads_daily.find(
        {"user_id": uid, "date": {"$gte": fd_str, "$lte": td_str}},
        {"_id": 0},
    ).to_list(5000)
    # Iter-160 SSOT: spend from ledger
    meta_spend_by_date = await _spend_by_date_from_ledger(
        db, uid, ("meta", "facebook", "instagram"), fd_str, td_str,
    )
    m_spend = sum(meta_spend_by_date.values())
    m_imp = sum(int(r.get("impressions") or 0) for r in meta_rows)
    m_clicks = sum(int(r.get("clicks") or 0) for r in meta_rows)
    m_purchases = sum(int(r.get("purchases") or 0) for r in meta_rows)
    m_revenue = sum(float(r.get("purchase_value") or 0) for r in meta_rows)
    meta_p = {
        "platform": "meta",
        "label": "Meta",
        **_metrics(m_spend, m_imp, m_clicks, m_purchases, m_revenue),
    }

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
attach_meta_routes(api, db)
attach_shipping_accounts_routes(api, db)
attach_webhook_routes(api, db)
attach_operating_expenses_routes(api, db)
api.include_router(make_recurring_obligations_router(db, current_user))
attach_settlements_routes(api, db)
attach_accounts_routes(api, db)
attach_transfers_routes(api, db)
attach_reconciliation_routes(api, db)
attach_diagnostics_routes(api, db)
attach_import_jobs_routes(api, db)
attach_product_costs_routes(api, db, current_user)
attach_preparation_routes(api, db)
attach_salla_routes(api, db)
attach_payment_settlements_routes(api, db)
attach_refunds_alert_routes(api, db)
attach_payment_gateway_metrics_routes(api, db)
attach_order_status_policy_routes(api, db)
attach_shipping_ledger_routes(api, db, current_user)
attach_orders_explorer_routes(api, db)
from dashboard_v2_routes import make_dashboard_v2_router
api.include_router(
    make_dashboard_v2_router(db, current_user, dashboard, _require_owner)
)
api.include_router(make_order_engine_router(db, current_user))
api.include_router(make_order_activity_router(db, current_user))
api.include_router(make_first_party_attribution_router(db, current_user))
api.include_router(
    make_order_item_engine_router(db, current_user)
)
from employees_v2_routes import (
    ensure_employee_v2_indexes,
    make_employees_v2_router,
)
api.include_router(make_employees_v2_router(db, current_user))
api.include_router(make_order_review_router(db, current_user))
api.include_router(make_return_decision_router(db, current_user))
attach_settlement_cycle_routes(api, db)
attach_liabilities_routes(api, db)
attach_counterparties_routes(api, db)
attach_purchase_invoice_routes(api, db)
attach_custom_app_routes(api, db)
attach_ad_account_routes(api, db)
attach_bnpl_routes(api, db)
attach_bnpl_webhook_routes(api, db)
attach_bnpl_diagnostics_routes(api, db)
attach_bnpl_audit_routes(api, db)
attach_bnpl_auto_sync_routes(api, db=db, get_current_user=current_user)
attach_bnpl_refund_audit_routes(api, db=db, get_current_user=current_user)
attach_bnpl_settlements_routes(api, db=db, get_current_user=current_user)
# Iter-114 — Operational reports (daily/monthly/yearly aggregated)
from operational_reports_routes import attach_operational_reports_routes
attach_operational_reports_routes(api, db, current_user)
# Iter-149 — Per-provider accounting cutoff dates
from accounting_cutoffs_routes import attach_accounting_cutoffs_routes
attach_accounting_cutoffs_routes(api, db, current_user)
# Iter-159h — Smart Settlement Alerts (in-app notifications)
from alerts_routes import attach_alerts_routes, ensure_alerts_indexes
attach_alerts_routes(api, db, current_user)
# Iter-160 — Universal Ledger + Audit Log (ERP-grade single source of truth)
from ledger_routes import make_ledger_router
api.include_router(make_ledger_router(db))
# Iter-161 — Phase 2: Universal Accounting (employees, suppliers, externals, bank, expenses)
from universal_accounting_routes import make_universal_router
api.include_router(make_universal_router(db))
# Iter-161 — Migration with dry-run + before/after comparison
from migration_routes import make_migration_router
api.include_router(make_migration_router(db))
from cod_diagnostic_routes import make_cod_diagnostic_router
api.include_router(make_cod_diagnostic_router(db, current_user))
from audit_routes import make_audit_router, make_employee_lookup_debug_router, make_forensic_report_router, make_tabby_phase2_router, make_double_write_health_router, make_ad_account_cron_forensic_router, make_ad_account_sync_diagnostic_router
from expense_categories_routes import make_expense_categories_router, make_suppliers_router
from financial_movements_routes import make_financial_movements_router
api.include_router(make_audit_router(db, current_user))
api.include_router(make_employee_lookup_debug_router(db, current_user))
api.include_router(make_forensic_report_router(db, current_user))
api.include_router(make_tabby_phase2_router(db, current_user))
api.include_router(make_double_write_health_router(db, current_user))
api.include_router(make_ad_account_cron_forensic_router(db, current_user))
api.include_router(make_ad_account_sync_diagnostic_router(db, current_user))
api.include_router(make_expense_categories_router(db, current_user))
api.include_router(make_suppliers_router(db, current_user))
api.include_router(make_financial_movements_router(db, current_user))
# Iter-251 · Phase 1 — Bank Transfer Review (independent layer).
from bank_transfer_review_routes import make_bank_transfer_review_router
api.include_router(
    make_bank_transfer_review_router(db, current_user))
# Iter-251 · Phase 2A — Settlement Dry-Run Engine (READ-ONLY analysis).
from settlement_engine_routes import make_settlement_engine_router
api.include_router(make_settlement_engine_router(db, current_user))

# Iter-251 v10 — Ad-spend RCA (Read-Only) for Meta/Snapchat
from ad_spend_rca_routes import make_ad_spend_rca_router
api.include_router(make_ad_spend_rca_router(db, current_user))

# ── Ads V2 (Phase 0) ────────────────────────────────────────────────
from ads_v2.routes import make_ads_v2_router
from ads_v2.models import ensure_indexes as _ads_v2_ensure_indexes
api.include_router(make_ads_v2_router(db, current_user))

# ── Apps & Integrations Control Center V2 (read-only control plane) ──
from integrations_control_center import (
    ensure_integrations_control_center_indexes,
    make_integrations_control_center_router,
)
api.include_router(make_integrations_control_center_router(db, current_user))

# ── Unified Ads Manager Phase 1 (owner-only, local reads only) ──────
from ads_manager import make_ads_manager_router
api.include_router(make_ads_manager_router(db, current_user))

# ── Customer Intelligence Phase 1 preview + channel-neutral memory core ──
from customer_intelligence import (
    make_customer_intelligence_router,
)
from customer_intelligence.foundation import (
    ensure_customer_intelligence_foundation_indexes,
)
from customer_intelligence.whatsapp import make_whatsapp_inbound_router
from customer_intelligence.instagram import make_instagram_inbound_router
from customer_intelligence.whatsapp_360dialog import make_360dialog_inbound_router
from customer_intelligence.temporary_whatsapp_provisioning import (
    make_temporary_whatsapp_provisioning_router,
)
api.include_router(make_customer_intelligence_router(current_user, db=db))
api.include_router(make_whatsapp_inbound_router(db))
api.include_router(make_instagram_inbound_router(db))
api.include_router(make_360dialog_inbound_router(db))
api.include_router(make_temporary_whatsapp_provisioning_router(db, current_user))

# ── Qoyod Invoice MVP — Day 2 (Settings + Catalogs + Health) ───────
# Pipeline (webhook, normalization, push) lands in Day 3-4. Today we
# expose only the merchant-facing config surface so a real API key can
# be plugged in and verified against Qoyod. See
# /app/docs/adr/ADR-001-architecture-principles.md and
# /app/backend/integrations/qoyod/README.md.
from integrations.qoyod.routes import make_qoyod_router as _make_qoyod_router
api.include_router(_make_qoyod_router(db, current_user))

# Plan-B Manual Send (2026-02) — isolated, manual, one-order-at-a-time
# push path. Legacy Rev32→Rev48 pipeline stays frozen via
# `qoyod_settings.legacy_pipeline_frozen=true`; only this router can
# push new invoices to Qoyod.
from integrations.qoyod_manual.routes import (
    make_qoyod_manual_router as _make_qoyod_manual_router,
)
api.include_router(_make_qoyod_manual_router(db, current_user))

from ad_spend_scheduler_diagnostics import (
    make_ad_spend_scheduler_diagnostics_router,
)
api.include_router(
    make_ad_spend_scheduler_diagnostics_router(db, current_user))
# Iter-246 — Read-only audit of legacy screens still in use.
from legacy_usage_report_routes import make_legacy_usage_report_router
api.include_router(make_legacy_usage_report_router(db, current_user))
# Iter-246i — Per-account balance diagnostic.
from account_balance_diagnostic_iter246i import make_balance_diagnostic_router
api.include_router(make_balance_diagnostic_router(db, current_user))
# Iter-246k — Suppliers analytical report.
from suppliers_report_routes import make_suppliers_report_router
api.include_router(make_suppliers_report_router(db, current_user))
# Iter-246m — Read-only Tamara settlement forensic.
from tamara_forensic_routes import make_tamara_forensic_router
api.include_router(make_tamara_forensic_router(db, current_user))
# Iter-246o — Read-only Tamara refund-sync & old-capture audit.
from tamara_refund_audit_routes import make_tamara_refund_audit_router
api.include_router(make_tamara_refund_audit_router(db, current_user))
# Iter-246p — Read-only Tamara 4-fix dry-run preview.
from tamara_fix_plan_dryrun_routes import make_tamara_fix_plan_dryrun_router
api.include_router(make_tamara_fix_plan_dryrun_router(db, current_user))
# Iter-246q — Tamara Apply (Final Dry-Run + Gated Execute).
from tamara_apply_routes import make_tamara_apply_router
api.include_router(make_tamara_apply_router(db, current_user))
# Iter-246t — Tamara SSOT diagnostic (modal vs forensic divergence).
from tamara_ssot_diagnostic_routes import make_tamara_ssot_diagnostic_router
api.include_router(make_tamara_ssot_diagnostic_router(db, current_user))
# Iter-246v — Tamara receivable composition diagnostic.
from tamara_receivable_diagnostic_routes import (
    make_tamara_receivable_diagnostic_router,
)
api.include_router(
    make_tamara_receivable_diagnostic_router(db, current_user))
# Iter-246w — Tamara settlement history forensic (READ-ONLY).
from tamara_settlement_history_routes import (
    make_tamara_settlement_history_router,
)
api.include_router(
    make_tamara_settlement_history_router(db, current_user))
# Iter-246x — BNPL settlement health (Tamara + Tabby).
from bnpl_settlement_health_routes import (
    make_bnpl_settlement_health_router,
)
api.include_router(
    make_bnpl_settlement_health_router(db, current_user))
# Iter-246y — Tamara refund ledger backfill (Dry-Run + Gated Apply).
from tamara_refund_backfill_routes import (
    make_tamara_refund_backfill_router,
)
api.include_router(
    make_tamara_refund_backfill_router(db, current_user))
# Iter-246z — BNPL Timezone SSOT health.
from bnpl_timezone_health_routes import make_timezone_health_router
api.include_router(make_timezone_health_router(current_user))
# Iter-247 — BNPL settlement trace (READ-ONLY diagnostic).
from bnpl_settlement_trace_routes import (
    make_bnpl_settlement_trace_router,
)
api.include_router(
    make_bnpl_settlement_trace_router(db, current_user))
# Iter-248 — BNPL settlement bank-txn backfill & health.
from bnpl_settlement_banktx_routes import (
    make_bnpl_settlement_banktx_router,
)
api.include_router(
    make_bnpl_settlement_banktx_router(db, current_user))
# Iter-249 — Bank statement UI audit (READ-ONLY).
from bnpl_statement_ui_audit_routes import (
    make_bnpl_statement_ui_audit_router,
)
api.include_router(
    make_bnpl_statement_ui_audit_router(db, current_user))
# Iter-249c — Bank balance sub_account diagnostic (READ-ONLY).
from bank_balance_subaccount_diagnostic_routes import (
    make_bank_balance_subaccount_diag_router,
)
api.include_router(
    make_bank_balance_subaccount_diag_router(db, current_user))
# Iter-249d — Bank current_balance source diagnostic (READ-ONLY).
from bank_current_balance_source_routes import (
    make_bank_current_balance_source_router,
)
api.include_router(
    make_bank_current_balance_source_router(db, current_user))
# Iter-250a — Financial pages inventory (READ-ONLY).
from financial_pages_inventory_routes import (
    make_financial_pages_inventory_router,
)
api.include_router(
    make_financial_pages_inventory_router(current_user))
# Iter-250b P1.5 — Balance drift diagnostic (READ-ONLY).
from balance_drift_diagnostic_routes import (
    make_balance_drift_diagnostic_router,
)
api.include_router(
    make_balance_drift_diagnostic_router(db, current_user))
# Iter-250b P1.5.b — account_transactions vs general_ledger walk
# (READ-ONLY).
from account_tx_vs_ledger_walk_routes import (
    make_account_tx_vs_ledger_walk_router,
)
api.include_router(
    make_account_tx_vs_ledger_walk_router(db, current_user))
# Iter-250b P1.5.c — Salla Balance Forensic (READ-ONLY).
from salla_balance_forensic_routes import (
    make_salla_balance_forensic_router,
)
api.include_router(
    make_salla_balance_forensic_router(db, current_user))
# Iter-250b P1.5.d — Settlement File Forensic (READ-ONLY).
from settlement_file_forensic_routes import (
    make_settlement_file_forensic_router,
)
api.include_router(
    make_settlement_file_forensic_router(db, current_user))
# Iter-250b P1.5.m — Employee Ledger Forensic (READ-ONLY).
from employee_ledger_forensic_routes import (
    make_employee_ledger_forensic_router,
)
api.include_router(
    make_employee_ledger_forensic_router(db, current_user))
# Iter-250b P1.5.n — Employee Lookup Forensic (READ-ONLY).
from employee_lookup_diagnostic_routes import (
    make_employee_lookup_diagnostic_router,
)
api.include_router(
    make_employee_lookup_diagnostic_router(db, current_user))
# Iter-250b P1.5.s — Supplier Ledger Detail (READ-ONLY, SSOT-strict).
from supplier_ledger_detail_routes import (
    make_supplier_ledger_detail_router,
)
api.include_router(
    make_supplier_ledger_detail_router(db, current_user))
# Iter-250b P1.5.t — Movements↔GL Drift Analyzer (READ-ONLY).
from movements_gl_drift_routes import (
    make_movements_gl_drift_router,
)
api.include_router(
    make_movements_gl_drift_router(db, current_user))
# Iter-250b P1.5.ab — Suppliers Unification Forensic (READ-ONLY).
from suppliers_unification_forensic_routes import (
    make_suppliers_unification_forensic_router,
)
api.include_router(
    make_suppliers_unification_forensic_router(db, current_user))
# Iter-250b P2 — Products & Categories Excel import.
from products_import_routes import (
    make_products_import_router,
    make_products_router_phase2,
)
api.include_router(make_products_import_router(db, current_user))
api.include_router(make_products_router_phase2(db, current_user))
# Iter-250a — Post-deploy verification (READ-ONLY).
from iter250a_verification_routes import (
    make_iter250a_verification_router,
)
api.include_router(
    make_iter250a_verification_router(db, current_user))
# Iter-250b P0 — Ad-account write-paths forensic (READ-ONLY).
from ad_account_forensic_routes import (
    make_ad_account_forensic_router,
)
api.include_router(
    make_ad_account_forensic_router(db, current_user))
# Iter-250b P0 — Ad-account dry-run diff (READ-ONLY).
from ad_account_dryrun_diff_routes import (
    make_ad_account_dryrun_diff_router,
)
api.include_router(
    make_ad_account_dryrun_diff_router(db, current_user))
# Iter-250b P0.5 — Ad-account recompute dry-run (READ-ONLY).
from ad_account_recompute_dryrun_routes import (
    make_ad_account_recompute_dryrun_router,
)
api.include_router(
    make_ad_account_recompute_dryrun_router(db, current_user))
# Iter-250b P0.6 — Ad-account root cause forensic (READ-ONLY).
from ad_account_root_cause_routes import (
    make_ad_account_root_cause_router,
)
api.include_router(
    make_ad_account_root_cause_router(db, current_user))
# Iter-250b P0.7 — Ad-account actual unpaid debt dry-run (READ-ONLY).
from ad_account_actual_debt_routes import (
    make_ad_account_actual_debt_router,
)
api.include_router(
    make_ad_account_actual_debt_router(db, current_user))
# Iter-217b — Reversal Impact Report (read-only audit of Iter-217 fix)
from reversal_impact_audit_routes import make_reversal_impact_router
api.include_router(make_reversal_impact_router(db, current_user))
# Iter-196 — Employee misposting correction (employee ledger only)
from corrections_routes import make_corrections_router
api.include_router(make_corrections_router(db, current_user))
# Iter-197 — Reconciliation Forensic (read-only diff classifier)
from reconciliation_forensic_routes import make_reconciliation_forensic_router
api.include_router(make_reconciliation_forensic_router(db, current_user))
# Iter-199 — Salary Payment Full Reversal (bank/cash IS impacted)
from reversals_routes import make_reversals_router
api.include_router(make_reversals_router(db, current_user))
# Iter-222 — Employee Opening Orphans Diagnostic (READ-ONLY)
from employee_orphan_diagnostic_routes import make_employee_orphan_router
api.include_router(make_employee_orphan_router(db, current_user))
# Iter-230 — Ad Debt Diagnostic (READ-ONLY, compares walk vs SSOT)
from ad_debt_diagnostic_routes import make_ad_debt_diagnostic_router
api.include_router(make_ad_debt_diagnostic_router(db, current_user))

from ads_currency_routes import attach_ads_currency_routes
attach_ads_currency_routes(api, db)

from accounts_balance_diagnostic_routes import (
    make_accounts_balance_diagnostic_router,
    make_accounts_balance_repair_preview_router,
    make_repair_audit_router,
    make_account_drift_detail_router,
    make_endpoint_ledger_coverage_router,
)
api.include_router(make_accounts_balance_diagnostic_router(db, current_user))
api.include_router(
    make_accounts_balance_repair_preview_router(db, current_user)
)
api.include_router(make_repair_audit_router(db, current_user))
api.include_router(make_account_drift_detail_router(db, current_user))
api.include_router(make_endpoint_ledger_coverage_router(db, current_user))

app.include_router(make_mezan_mcp_router(db))
app.include_router(api)

# CORS — production origins are explicit and never fall back to wildcard.
frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
extra = os.environ.get("CORS_ORIGINS", "").split(",")
_localhost_origins = (
    ["http://localhost:3000"]
    if os.environ.get("CORS_ALLOW_LOCALHOST", "").strip().lower() in {"1", "true", "yes", "on"}
    else []
)
origins = list({o.strip() for o in [
    frontend_url,
    "https://amasi-sa.com",
    "https://www.amasi-sa.com",
] + _localhost_origins + extra if o and o.strip() and o.strip() != "*"})

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# CORS controls response sharing; this separate guard blocks cross-site browser
# mutations that carry Mezan's HttpOnly session cookies.
app.add_middleware(
    BrowserSecurityMiddleware,
    trusted_origins={
        frontend_url,
        "https://mezansalla.com",
        "https://www.mezansalla.com",
        "https://amasi-sa.com",
        "https://www.amasi-sa.com",
    },
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def on_startup():
    await db.users.create_index("email", unique=True)
    await db.settings.create_index("user_id", unique=True)
    await db.daily_costs.create_index([("user_id", 1), ("date", 1)], unique=True)
    await db.analyses.create_index([("user_id", 1), ("created_at", -1)])
    # iter-260 — diagnostic capture for unparseable webhook bodies.
    # TTL: 30 days. Index is idempotent (no-op if already present).
    await db.webhook_parse_failures.create_index(
        "occurred_at", expireAfterSeconds=30 * 24 * 60 * 60)
    await ensure_import_jobs_indexes(db)
    await ensure_transfers_indexes(db)
    await ensure_accounts_indexes(db)
    await ensure_liabilities_indexes(db)
    await ensure_recurring_obligation_indexes(db)
    await ensure_counterparties_indexes(db)
    await ensure_purchase_invoices_indexes(db)
    await ensure_custom_app_indexes(db)
    await ensure_ad_account_indexes(db)
    await ensure_bnpl_indexes(db)
    await ensure_alerts_indexes(db)
    # Employees V2 — canonical employee identity and read-only salary-contract
    # shadow.  These indexes do not migrate or recalculate legacy payroll.
    await ensure_employee_v2_indexes(db)
    # Iter-160 — Universal Ledger + Audit Log indexes
    from ledger_core import ensure_indexes as ensure_ledger_indexes
    await ensure_ledger_indexes(db)
    # Ads V2 (Phase 0) — create indexes on the 3 new collections.
    await _ads_v2_ensure_indexes(db)
    # Apps & Integrations V2 — isolated metadata, health, activity,
    # errors, and future campaign↔product identity links.
    await ensure_integrations_control_center_indexes(db)
    await ensure_first_party_attribution_indexes(db)
    # Five-hour Snapchat + Meta campaign monitoring. The worker only refreshes
    # analytical facts and persists recommendations; provider changes remain
    # behind the separate owner-only approval endpoint.
    from campaign_ai_monitor import (
        ensure_campaign_ai_indexes as _ensure_campaign_ai_indexes,
        start_campaign_ai_worker as _start_campaign_ai_worker,
    )
    await _ensure_campaign_ai_indexes(db)
    app.state.campaign_ai_monitor_task = _start_campaign_ai_worker(
        db,
        business_context_loader=dashboard,
    )
    # Customer Intelligence conversation core.  This creates only Mongo
    # indexes and reuses the encrypted customer identity vault; it does not
    # connect a channel, send a message or expose any mutation endpoint.
    await ensure_customer_intelligence_foundation_indexes(db)
    # Every inbound customer-authored channel event is queued by the gateway.
    # Reuse the already-configured Mezan OpenAI runtime to turn that queue into
    # encrypted, evidence-linked signals, problems and decision proposals.
    # The worker contains no channel-send or commerce-mutation capability.
    from customer_intelligence.learning_worker import (
        queue_existing_channel_evidence as _queue_existing_customer_evidence,
        start_worker as _start_customer_learning_worker,
    )
    queued_customer_evidence = await _queue_existing_customer_evidence(db)
    if any(queued_customer_evidence.values()):
        logger.info("Queued existing customer-channel evidence for governed AI analysis: %s", queued_customer_evidence)
    app.state.customer_learning_task = _start_customer_learning_worker(db)
    # ── Qoyod Invoice MVP (Day 1) — create indexes on the 5 new
    # `qoyod_*` collections. Idempotent — safe to call on every boot.
    # See ADR-001 (architecture principles) and integrations/qoyod/models.py.
    from integrations.qoyod.models import (
        ensure_qoyod_indexes as _qoyod_ensure_indexes,
    )
    await _qoyod_ensure_indexes(db)
    # Iter-139 — half-hourly ad-account sync (replaces the previous
    # 23:55 daily cron).  Runs every 30 minutes, syncs TODAY only,
    # uses force=True so each pass reverses prior cron rows for the
    # same day and applies fresh totals — ad-balance + ad-liability
    # stay near-realtime without double-counting.
    from ad_account_routes import run_daily_cron, run_yesterday_final_sync
    import asyncio as _asyncio
    from datetime import datetime as _dt, timedelta as _td

    AD_ACCOUNT_SYNC_INTERVAL_SECONDS = 30 * 60   # 30 minutes

    async def _ad_account_halfhour_sync():
        # Stagger first run by 90s so the server is fully up.
        await _asyncio.sleep(90)
        while True:
            try:
                logger.info("iter-139: starting ad-account half-hour sync")
                result = await run_daily_cron(db)
                logger.info(
                    "iter-139: ad-account half-hour sync done — "
                    "%d users processed (today=%s)",
                    result.get("users_processed", 0),
                    result.get("today"),
                )
                # Iter-159k — once per day, finalise YESTERDAY's spend
                # to catch delayed conversions/impressions posted after
                # midnight.  Idempotent via the per-user marker.
                try:
                    y_result = await run_yesterday_final_sync(db)
                    if y_result.get("users_processed", 0) > 0:
                        logger.info(
                            "iter-159k: yesterday final sync done — "
                            "%d users processed (yesterday=%s)",
                            y_result["users_processed"],
                            y_result["yesterday"],
                        )
                except Exception as _e:
                    logger.warning(
                        "iter-159k: yesterday sync failed: %s", _e,
                    )

                # Persist run report so the UI / diagnostics can show
                # the last successful pass.
                try:
                    await db.cron_runs.insert_one({
                        "id": str(uuid.uuid4()),
                        "type": "ad_account_halfhour_sync",
                        "ran_at": result["ran_at"],
                        "today": result["today"],
                        "users_processed": result["users_processed"],
                        "summary": result["details"][:50],
                    })
                except Exception as _e:
                    logger.warning(
                        "iter-139: cron run-log insert failed: %s", _e,
                    )
            except Exception as e:
                logger.exception(
                    "iter-139: ad-account half-hour sync failed: %s", e,
                )
                # Back off 60s after a hard failure before next attempt.
                await _asyncio.sleep(60)
                continue
            await _asyncio.sleep(AD_ACCOUNT_SYNC_INTERVAL_SECONDS)

    _asyncio.create_task(_ad_account_halfhour_sync())

    # ── Iter-215 — Ad-spend AM/PM window posting (Snap/Meta) ─────────
    # The half-hour cron (above) is now FETCH-ONLY for Snap/Meta. The
    # SSOT posting happens twice a day, aligned with Meta's ~40-min
    # reporting lag:
    #   • AM window (12:30–13:30 Riyadh)  → books today's 00–12 spend.
    #   • PM window (00:30–01:30 Riyadh)  → books yesterday's 12–24
    #                                       spend (lagging Meta data
    #                                       arrives by then).
    # Outside those windows the task wakes every 5 minutes and sleeps.
    # Once per hour it also runs a 7-day catch-up scan to recover from
    # any missed window (server downtime, lag, etc.).
    async def _ad_spend_window_post_loop():
        from ad_spend_windows import (
            current_window, run_window_post, catch_up_window_posts,
            PERIOD_AM,
        )
        from tz_utils import riyadh_now_aware as _r_now
        from datetime import timedelta as _td2
        await _asyncio.sleep(120)  # let the half-hour cron seed data
        # Iter-251 v12 — record loop-start event so the scheduler
        # diagnostics endpoint can prove the task actually launched.
        try:
            await db.cron_runs.insert_one({
                "id":      str(uuid.uuid4()),
                "type":    "ad_spend_window_post_loop_start",
                "ran_at":  datetime.now(timezone.utc).isoformat(),
                "note":    "iter-215 ad_spend window post loop started",
            })
        except Exception:
            pass
        last_catchup_hour = -1
        last_posted_key = None

        def _aggregate_reasons(items: list) -> dict:
            """Bucket skipped items by `reason` for the heartbeat."""
            out: dict[str, int] = {}
            for it in items or []:
                reason = (it or {}).get("reason") or "unknown"
                out[reason] = out.get(reason, 0) + 1
            return out

        while True:
            try:
                # 7-day catch-up scan once per hour.
                hour_now = _r_now().hour
                if hour_now != last_catchup_hour:
                    last_catchup_hour = hour_now
                    res = await catch_up_window_posts(db)
                    posted = res.get("summary", {}).get("posted", 0)
                    skipped = res.get("summary", {}).get("skipped", 0)
                    if posted:
                        logger.info(
                            "iter-215: catch-up posted=%d skipped=%d",
                            posted, skipped,
                        )
                    # Iter-251 v12 — persist catch-up heartbeat so the
                    # diagnostics endpoint can show last-ran time +
                    # skip-reason histogram.
                    try:
                        await db.cron_runs.insert_one({
                            "id":               str(uuid.uuid4()),
                            "type":             "ad_spend_window_catchup",
                            "ran_at":           datetime.now(
                                timezone.utc).isoformat(),
                            "posted_count":     posted,
                            "skipped_count":    skipped,
                            "skipped_reasons":  _aggregate_reasons(
                                res.get("skipped") or []),
                            "posted_sample":    (
                                res.get("posted") or [])[:10],
                            "skipped_sample":   (
                                res.get("skipped") or [])[:10],
                        })
                    except Exception:
                        pass
                # Current window post.
                w = current_window()
                if w is not None:
                    period, target_date = w
                    posted_key = f"{period}:{target_date}:{hour_now}"
                    # Within a window, post once per hour bucket so we
                    # don't loop hammering Mongo (idempotency would
                    # block double-posts anyway, but this is cheaper).
                    if posted_key != last_posted_key:
                        last_posted_key = posted_key
                        result = await run_window_post(
                            db, period, target_date,
                        )
                        logger.info(
                            "iter-215: window-post period=%s "
                            "target=%s posted=%d skipped=%d",
                            period, target_date,
                            result["summary"]["posted"],
                            result["summary"]["skipped"],
                        )
                        # Iter-251 v12 — persist window-post heartbeat.
                        try:
                            await db.cron_runs.insert_one({
                                "id":              str(uuid.uuid4()),
                                "type":            "ad_spend_window_post",
                                "ran_at":          datetime.now(
                                    timezone.utc).isoformat(),
                                "period":          period,
                                "target_date":     target_date,
                                "posted_count":    result["summary"]["posted"],
                                "skipped_count":   result["summary"]["skipped"],
                                "skipped_reasons": _aggregate_reasons(
                                    result.get("skipped") or []),
                                "posted_sample":   (
                                    result.get("posted") or [])[:10],
                                "skipped_sample":  (
                                    result.get("skipped") or [])[:10],
                            })
                        except Exception:
                            pass
                        # In AM window we additionally apply yesterday's
                        # PM_CORRECTION sweep (catches late Meta data).
                        if period == PERIOD_AM:
                            yest = (
                                _r_now().date() - _td2(days=1)
                            ).isoformat()
                            corr = await run_window_post(
                                db, "AM_FOLLOWING_CORRECTION", yest,
                            )
                            corr_posted = corr["summary"]["posted"]
                            if corr_posted:
                                logger.info(
                                    "iter-215: yesterday-correction "
                                    "posted=%d", corr_posted,
                                )
                            try:
                                await db.cron_runs.insert_one({
                                    "id":             str(uuid.uuid4()),
                                    "type":           "ad_spend_window_post",
                                    "ran_at":         datetime.now(
                                        timezone.utc).isoformat(),
                                    "period":         "AM_FOLLOWING_CORRECTION",
                                    "target_date":    yest,
                                    "posted_count":   corr["summary"]["posted"],
                                    "skipped_count":  corr["summary"]["skipped"],
                                    "skipped_reasons": _aggregate_reasons(
                                        corr.get("skipped") or []),
                                    "posted_sample":   (
                                        corr.get("posted") or [])[:10],
                                    "skipped_sample":  (
                                        corr.get("skipped") or [])[:10],
                                })
                            except Exception:
                                pass
            except Exception as e:
                logger.exception(
                    "iter-215: window post loop failed: %s", e,
                )
            await _asyncio.sleep(300)  # 5-minute heartbeat

    _asyncio.create_task(_ad_spend_window_post_loop())

    # ── Iter-117 — Hourly BNPL auto-sync (Tabby + Tamara) ────────
    # Runs every hour to fetch new/updated payments and refunds
    # incrementally from each merchant's BNPL providers, keeping
    # `unified_orders` (the single source of truth) up to date so
    # all UI pages (Dashboard / Reports / Profits / Assets /
    # Settlements) reflect the latest provider state automatically.
    async def _bnpl_hourly_auto_sync():
        from bnpl.auto_sync_service import SYNC_INTERVAL_SECONDS
        # Stagger first run by 60s so server has time to settle.
        await _asyncio.sleep(60)
        while True:
            try:
                logger.info("iter-117: starting BNPL hourly auto-sync")
                summary = await run_auto_sync_for_all_users(db)
                logger.info(
                    "iter-117: bnpl auto-sync done — %d pairs, "
                    "users=%d, failures=%s, duration=%.1fs",
                    summary.get("pairs_processed", 0),
                    summary.get("users_processed", 0),
                    summary.get("any_failures"),
                    summary.get("duration_seconds", 0.0),
                )
            except Exception as e:
                logger.exception("iter-117: bnpl auto-sync iteration failed: %s", e)
                # Back off briefly so we don't hammer providers on a bad config.
                await _asyncio.sleep(120)
                continue
            await _asyncio.sleep(SYNC_INTERVAL_SECONDS)

    _asyncio.create_task(_bnpl_hourly_auto_sync())

    # ── Iter-147 — Daily Tamara attribution sweep ───────────────────
    # Re-derives `effective_settlement_date` + `settlement_source` for
    # every Tamara payment_transactions row so estimated → billing →
    # provider_official transitions are picked up automatically (without
    # the merchant manually hitting the recompute endpoint each week).
    # Runs every 24h, staggered 5 minutes after server boot so the
    # hourly sync finishes its first round first.
    async def _tamara_attribution_daily_sweep():
        await _asyncio.sleep(300)  # 5-min stagger
        DAILY_SECONDS = 24 * 60 * 60
        while True:
            try:
                logger.info("iter-147: starting daily Tamara attribution sweep")
                summary = await run_tamara_attribution_sweep(db)
                logger.info(
                    "iter-147: tamara attribution sweep done — "
                    "users=%d rows_scanned=%d rows_updated=%d duration=%.1fs",
                    summary.get("users_processed", 0),
                    summary.get("rows_scanned", 0),
                    summary.get("rows_updated", 0),
                    summary.get("duration_seconds", 0.0),
                )
            except Exception as e:
                logger.exception("iter-147: tamara attribution sweep failed: %s", e)
                await _asyncio.sleep(300)
                continue
            await _asyncio.sleep(DAILY_SECONDS)

    _asyncio.create_task(_tamara_attribution_daily_sweep())

    # ── Iter-147 — One-shot startup migration ────────────────────────
    # Backfills `effective_settlement_date` + `settlement_source` on
    # every legacy Tamara payment_transactions row that pre-existed
    # before Iter-147 shipped.  Without this, the new settlements
    # engine would exclude legacy sales (no effective date set) while
    # refunds still match by `refunded_at`, producing negative
    # net-sales rows in the weekly invoice UI.  Runs in the background
    # so it doesn't block startup.
    #
    # Iter-147 v2 — also runs when there are rows with raw_payload
    # captures[] but no `captured_at_provider` extracted yet (the new
    # capture-date priority needs that field populated).
    async def _tamara_attribution_startup_migration():
        # Tiny stagger so MongoDB / indexes finish initialising first.
        await _asyncio.sleep(15)
        try:
            unattributed = await db.payment_transactions.count_documents({
                "provider": "tamara",
                "$or": [
                    {"effective_settlement_date": {"$exists": False}},
                    {"effective_settlement_date": None},
                    {"effective_settlement_date": ""},
                ],
            })
            # Iter-147 v2 — also count rows where captured_at_provider
            # was never populated.  Even if effective_settlement_date
            # exists, we may want to re-attribute with a stronger
            # capture-date signal.
            uncaptured = await db.payment_transactions.count_documents({
                "provider": "tamara",
                "$or": [
                    {"captured_at_provider": {"$exists": False}},
                    {"captured_at_provider": None},
                    {"captured_at_provider": ""},
                ],
                "raw_payload.captures.0": {"$exists": True},
            })
            if unattributed == 0 and uncaptured == 0:
                logger.info("iter-147: startup migration — nothing to backfill")
                return
            logger.info(
                "iter-147: startup migration — unattributed=%d uncaptured=%d",
                unattributed, uncaptured,
            )
            summary = await run_tamara_attribution_sweep(db)
            logger.info(
                "iter-147: startup migration done — users=%d scanned=%d "
                "updated=%d captured_extracted=%d",
                summary.get("users_processed", 0),
                summary.get("rows_scanned", 0),
                summary.get("rows_updated", 0),
                summary.get("captured_extracted", 0),
            )
        except Exception as e:
            logger.exception("iter-147: startup migration failed: %s", e)

    _asyncio.create_task(_tamara_attribution_startup_migration())

    # iter-262 — Qoyod Pipeline Worker. Auto-advances `integration_inbox`
    # rows from NORMALIZED → CUSTOMER_RESOLVED → INVOICE_CREATED →
    # RECEIPT_CREATED. Without this, the webhook handler stops at
    # NORMALIZED and nothing reaches Qoyod.
    try:
        from integrations.qoyod.worker import start_worker as _qoyod_worker_start
        _qoyod_worker_start(db, interval_sec=5.0, batch_limit=25)
        logger.info("iter-262: qoyod pipeline worker started")
    except Exception as e:
        logger.exception("iter-262: qoyod pipeline worker failed to start: %s", e)

    # Validated Plan-B automatic sender. Starting the task on every deploy is
    # safe: it is a no-op until the existing Qoyod settings switches arm it
    # after the closed canary succeeds.
    try:
        from integrations.qoyod_manual.auto_send import (
            start_worker as _qoyod_plan_b_auto_start,
        )
        _qoyod_plan_b_auto_start(db, interval_sec=15.0, batch_limit=5)
        logger.info("Plan-B Qoyod automatic sender started")
    except Exception as e:
        logger.exception(
            "Plan-B Qoyod automatic sender failed to start: %s", e)

    await ensure_settlements_indexes(db)
    _bf = await backfill_settlement_provenance(db)
    if _bf:
        logger.info("iter-70.1: backfilled detection_source on %d legacy settlements", _bf)
    # iter-72 — clean up legacy shipping_company values
    from shipping_migrations import migrate_shipping_company_values
    _ship_mig = await migrate_shipping_company_values(db)
    _uo = _ship_mig.get("unified_orders", {})
    if _uo.get("updated"):
        logger.info("iter-72: scrubbed %d/%d shipping_company values in unified_orders",
                    _uo["updated"], _uo["scanned"])
    # Legacy Snapchat V1 collections are intentionally left untouched as an
    # archive. Their router and writers are frozen; Mezan 2 owns the only live
    # Snapchat credential and reporting data plane.
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
    # iter-56 — Payment Settlements ledger
    await db.payment_adjustments.create_index([("user_id", 1), ("adjusted_at", -1)])
    await db.payment_adjustments.create_index([("user_id", 1), ("order_number", 1)])
    await db.payment_adjustments.create_index([("user_id", 1), ("provider", 1)])
    # iter-57 — Financial Accounts foundation
    await db.accounts.create_index([("user_id", 1), ("account_type", 1)])
    await db.accounts.create_index([("user_id", 1), ("status", 1)])
    await db.account_transactions.create_index([("user_id", 1), ("account_id", 1), ("transaction_date", -1)])
    # Indexes for the "تجهيز المنتجات" feature (iteration 34).
    await ensure_preparation_indexes(db)
    await ensure_salla_indexes(db)
    # OAuth access tokens last 14 days and Salla refresh tokens rotate on
    # every use.  Refresh connected stores one day early in a background
    # loop; the Mongo lease in salla_integration.service makes this safe even
    # when the deployment runs multiple backend workers.
    from salla_integration.service import salla_token_maintenance_loop
    app.state.salla_token_maintenance_task = _asyncio.create_task(
        salla_token_maintenance_loop(db)
    )
    await ensure_payment_settlements_indexes(db)
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
                # Iter-177 — interpret UTC `received_at` as Riyadh date
                # so orders received at 21:30 UTC (00:30 KSA next day)
                # land on the merchant's actual calendar day.
                _dt = datetime.fromisoformat(str(recv).replace("Z", "+00:00"))
                date_str = riyadh_date_from_utc(_dt).isoformat()
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
            # Iter-177 — interpret UTC `received_at` as Riyadh date.
            _dt = datetime.fromisoformat(
                str(o["received_at"]).replace("Z", "+00:00")
            )
            recv_day = riyadh_date_from_utc(_dt).isoformat()
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
            # Iter-177 — interpret UTC `received_at` as Riyadh date.
            _dt = datetime.fromisoformat(
                str(o["received_at"]).replace("Z", "+00:00")
            )
            recv_day = riyadh_date_from_utc(_dt).isoformat()
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
    campaign_ai_monitor_task = getattr(
        app.state, "campaign_ai_monitor_task", None
    )
    if campaign_ai_monitor_task is not None:
        campaign_ai_monitor_task.cancel()
        try:
            await campaign_ai_monitor_task
        except _asyncio.CancelledError:
            pass
    customer_learning_task = getattr(app.state, "customer_learning_task", None)
    if customer_learning_task is not None:
        customer_learning_task.cancel()
        try:
            await customer_learning_task
        except _asyncio.CancelledError:
            pass
    maintenance_task = getattr(
        app.state, "salla_token_maintenance_task", None
    )
    if maintenance_task is not None:
        maintenance_task.cancel()
        try:
            await maintenance_task
        except _asyncio.CancelledError:
            pass
    client.close()
