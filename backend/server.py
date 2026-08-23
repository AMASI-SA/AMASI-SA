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
from financial_provider_apps import (
    ensure_financial_provider_app_indexes,
    make_financial_provider_apps_router,
)
from courier_cod_fee_rules import (
    CourierCodFeeTier,
    validate_courier_cod_fee_tiers,
)
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
    user = await get_current_user_from_db(request, db)
    # Native employees authenticate as themselves, but Mezan's historical data
    # remains tenant-scoped by the merchant owner id. Bridge those identities
    # only for an explicit native-route allow-list and only after checking the
    # matching independent app-page permission. Browser sessions are unchanged.
    from mobile_app_request_context import mobile_app_request_user

    return await mobile_app_request_user(
        db,
        user,
        path=request.url.path,
        method=request.method,
    )


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
    # MZ2-FIN-CUTOVER-001 — optional per-shipment COD brackets. When at
    # least one tier exists it takes priority over the legacy flat fields.
    # The VAT percentage belongs to the collection commission itself and is
    # kept separate from the normal shipping-service VAT.
    cod_fee_vat_percent: float = Field(default=15.0, ge=0, le=100)
    cod_fee_tiers: List[CourierCodFeeTier] = Field(default_factory=list)

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
        try:
            validate_courier_cod_fee_tiers(values.get("cod_fee_tiers") or [])
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
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
    # Native-app permissions remain a separate namespace and are never merged
    # into Mezan's browser/operational ``permissions`` list. Return the
    # authenticated account's own app-access snapshot on every profile refresh:
    # the mobile client persists this response for navigation, and an auth
    # middleware losing its client marker must not turn 13 saved pages into 0.
    # Operational mobile routes still enforce their signed-session policy.
    from mobile_app_permissions import mobile_app_access_for_user

    mobile_app_access = await mobile_app_access_for_user(db, user)
    return {
        "id": user["id"], "name": user.get("name"), "email": user["email"],
        "role": user.get("role", "user"),
        # iter-51 — surface effective permissions + Salla owner flag so the
        # frontend can drive RBAC navigation without an extra round-trip.
        "permissions": permissions,
        "mobile_app_access": mobile_app_access,
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
            kind = row["_id"]["kind"]
            sub = float(row.get("total") or 0)
            bucket = party_balances.setdefault(
                pid, {"owed_to_party": 0.0, "owed_from_party": 0.0})
            if kind in OWE_THEM_KINDS:
                bucket["owed_to_party"] += sub
            elif kind in THEY_OWE_KINDS:
                bucket["owed_from_party"] += sub

    # Step 4 — attach to each feed item.
    for it in items:
        party_id = item_to_party.get(it.get("ref_id"))
        bal = party_balances.get(party_id, {}) if party_id else {}
        owed_to = round(bal.get("owed_to_party", 0), 2)
        owed_from = round(bal.get("owed_from_party", 0), 2)
        it["party_id"] = party_id
        it["owed_to_party"] = owed_to        # كم له
        it["owed_from_party"] = owed_from    # كم عليه
        # Keep legacy field for backward compatibility (net open balance).
        it["party_open_balance"] = round(owed_to - owed_from, 2) \
            if party_id else None
        # Beneficiary — clean canonical name (supplier or employee).
        it["beneficiary_name"] = party_name_map.get(party_id) or None

    # Step 5 — totals across the FULL filtered feed.  Each party counted
    # once (not per operation row) to avoid double-counting when the same
    # party appears in multiple rows.
    total_owed_to = round(
        sum(b.get("owed_to_party", 0) for b in party_balances.values()), 2)
    total_owed_from = round(
        sum(b.get("owed_from_party", 0) for b in party_balances.values()), 2)
    net_balance = round(total_owed_to - total_owed_from, 2)

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "totals": {
            "owed_to_party": total_owed_to,
            "owed_from_party": total_owed_from,
            "net_balance": net_balance,
            "unique_parties": len(party_balances),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Iter-159d — Party (Counterparty / Employee) Details Drawer
# ─────────────────────────────────────────────────────────────────────────────
# Returns a 360° view of a single party — used by the "المستفيد" column in the
# Financial Input Hub recent-entries table.  Aggregates open balances,
# liability history and bank-transaction history in one call.
@api.get("/parties/{party_id}/details")
async def party_details(party_id: str, user: dict = Depends(current_user)):
    uid = user["id"]

    # Try counterparty first, then operating_salaries.
    party = None
    party_type = None
    cp = await db.counterparties.find_one(
        {"id": party_id, "user_id": uid}, {"_id": 0})
    if cp:
        party = cp
        party_type = "counterparty"
    else:
        es = await db.operating_salaries.find_one(
            {"id": party_id, "user_id": uid}, {"_id": 0})
        if es:
            party = es
            party_type = "employee"

    if not party:
        raise HTTPException(status_code=404, detail="الجهة غير موجودة")

    # All liabilities for this party (open + closed, latest first).
    liab_query = {
        "user_id": uid,
        "$or": [
            {"counterparty_id": party_id},
            {"employee_salary_id": party_id},
        ],
    }
    liabs = await db.liabilities.find(liab_query, {"_id": 0}) \
        .sort("created_at", -1).to_list(2000)

    # All bank transactions linked via peer_liability_id ∈ this party's
    # liabilities. (Transactions don't store party_id directly.)
    liab_ids = [l["id"] for l in liabs]
    txs = []
    if liab_ids:
        txs = await db.account_transactions.find(
            {"user_id": uid, "peer_liability_id": {"$in": liab_ids}},
            {"_id": 0},
        ).sort("created_at", -1).to_list(2000)

    # Compute directional balances.
    OWE_THEM_KINDS = ("supplier", "salary", "ad_account")
    THEY_OWE_KINDS = ("salary_advance", "receivable")
    owed_to = 0.0
    owed_from = 0.0
    for l in liabs:
        if l.get("status") not in ("unpaid", "partial"):
            continue
        rem = float(l.get("expected_amount") or 0) - \
              float(l.get("paid_amount") or 0)
        kind = l.get("kind")
        if kind in OWE_THEM_KINDS:
            owed_to += rem
        elif kind in THEY_OWE_KINDS:
            owed_from += rem

    # Last activity = newest of liabs.created_at or txs.created_at.
    last_activity = None
    for src in (liabs, txs):
        if src and src[0].get("created_at"):
            ts = src[0]["created_at"]
            if last_activity is None or ts > last_activity:
                last_activity = ts

    return {
        "party": {
            "id": party["id"],
            "type": party_type,
            "name": party.get("name"),
            "kind": party.get("kind"),                # counterparty kind
            "category": party.get("category"),        # employee category
            "country": party.get("country"),
            "monthly_amount": party.get("monthly_amount"),
            "status": party.get("status"),
            "notes": party.get("notes"),
            "created_at": party.get("created_at"),
        },
        "totals": {
            "owed_to_party": round(owed_to, 2),
            "owed_from_party": round(owed_from, 2),
            "net_balance": round(owed_to - owed_from, 2),
        },
        "liabilities": liabs,
        "transactions": txs,
        "last_activity": last_activity,
        "counts": {
            "liabilities": len(liabs),
            "transactions": len(txs),
        },
    }


# ── Global App Config (singleton — affects all users) ────────────────────────
# `app_config` is a single-document collection (id='global'). It holds settings
# that affect the public-facing UI (e.g. whether the "create new account" link
# is visible on /login). Only the Owner can modify it.
APP_CONFIG_DEFAULTS = {
    "show_register_link": False,  # Single-store deployment by default — public
                                  # registration is hidden but the endpoint
                                  # still works (UI-level toggle only).
}


async def _get_app_config() -> dict:
    """Fetch the singleton config doc, creating it with defaults if missing."""
    doc = await db.app_config.find_one({"_id": "global"}) or {}
    return {**APP_CONFIG_DEFAULTS, **{k: v for k, v in doc.items() if k != "_id"}}


@api.get("/public/login-config")
async def public_login_config():
    """Public, unauthenticated endpoint that exposes only the flags needed by
    the /login screen. Kept intentionally minimal to avoid leaking any other
    app config to anonymous visitors."""
    cfg = await _get_app_config()
    return {"show_register_link": bool(cfg.get("show_register_link", False))}


class AppConfigIn(BaseModel):
    show_register_link: Optional[bool] = None


@api.get("/app-config")
async def get_app_config(user: dict = Depends(current_user)):
    """Owner-only — full app config readback for the Settings page."""
    _require_owner(user)
    return await _get_app_config()


@api.put("/app-config")
async def update_app_config(payload: AppConfigIn, user: dict = Depends(current_user)):
    _require_owner(user)
    update_doc = {"updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": user["id"]}
    if payload.show_register_link is not None:
        update_doc["show_register_link"] = bool(payload.show_register_link)
    await db.app_config.update_one({"_id": "global"}, {"$set": update_doc}, upsert=True)
    return await _get_app_config()


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
        is_def = bool(c.get("is_deferred"))
        configured.append({
            "name": (c.get("name") or "").strip(),
            "cost": cost_f,
            "vat_rate": float(c.get("vat_rate") or 0),
            "is_deferred": is_def,
            "payment_mode": "deferred" if is_def else "prepaid",
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
    # SSOT — pass company VAT configs so shipping balance = base + tax.
    from shipping_cost_ssot import get_company_configs
    company_cfgs = await get_company_configs(db, user["id"])
    return compute_balances(orders, shipping_approved, cod_approved,
                              company_cfgs=company_cfgs)


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
    """Accept an Excel upload and process it in the background.

    Iter-59: returns within ~50 ms with a job_id. The actual parse +
    upsert loop runs in an `asyncio.create_task` so Make.com webhook
    ingestion is never blocked by a long upload.
    """
    content = await read_safe_xlsx_upload(file, max_bytes=15 * 1024 * 1024)

    job = await create_import_job(
        db,
        user_id=user["id"],
        filename=file.filename,
        total_rows=0,  # filled in once parse completes
        params={
            "name": name,
            "date": date,
            "snapchat_ads": snapchat_ads,
            "tiktok_ads": tiktok_ads,
            "instagram_ads": instagram_ads,
            "product_costs": product_costs,
        },
    )
    schedule_excel_job(
        db=db,
        job_id=job["id"],
        user_id=user["id"],
        file_content=content,
        filename=file.filename,
        params=job["params"],
    )
    return {
        "job_id": job["id"],
        "status": "queued",
        "message": "تم استلام الملف وجاري المعالجة في الخلفية.",
    }


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

    content = await read_safe_xlsx_upload(file, max_bytes=15 * 1024 * 1024)
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
    include_legacy_analyses: bool = Query(default=True, include_in_schema=False),
    allow_self_heal: bool = Query(default=True, include_in_schema=False),
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
    if all_orders:
        attribution_rows = await db.unified_orders.find(
            orders_q,
            SALLA_RAW_ATTRIBUTION_PROJECTION,
        ).to_list(100000)
        attach_projected_salla_attribution(all_orders, attribution_rows)

    # Iteration 31: data_source self-heal. Past orders whose data_source
    # was demoted to "excel" by Excel re-imports (pre-iteration-31 bug)
    # are re-promoted to "make" whenever their data_sources[] history
    # contains any Make write. This corrects historical bucketing
    # WITHOUT requiring a manual recompute or migration script.
    ds_promoted = 0
    if allow_self_heal:
        for o in all_orders:
            if o.get("data_source") == "excel":
                history = o.get("data_sources") or []
                if any((s or {}).get("source") == "make" for s in history):
                    o["data_source"] = "make"
                    ds_promoted += 1
                    # Persist for next request so we don't repeat the work.
                    try:
                        await db.unified_orders.update_one(
                            {"user_id": user["id"], "order_number": o["order_number"]},
                            {"$set": {"data_source": "make"}},
                        )
                    except Exception:
                        pass  # never fail dashboard on heal
        if ds_promoted:
            logger.info("Dashboard: promoted %d orders excel→make", ds_promoted)

    # Iteration 27: lazy self-heal. Any order in the filtered range
    # whose `total_product_cost` is still null/missing → re-run
    # attach_cost_to_order_doc. This guarantees the Dashboard always
    # reflects the latest cost data, even for orders that arrived
    # before a cost was added (or on environments without the
    # iteration-26 auto-recompute hooks). Idempotent + only touches
    # stale rows so it's effectively free when data is healthy.
    if allow_self_heal:
        try:
            from product_costs import attach_cost_to_order_doc as _attach_pc
            stale_indexes: list[int] = []
            for i, o in enumerate(all_orders):
                if o.get("total_product_cost") is None:
                    stale_indexes.append(i)
            for i in stale_indexes[:500]:  # cap heal-per-request for safety
                o = all_orders[i]
                patch = await _attach_pc(db, user["id"], o)
                await db.unified_orders.update_one(
                    {"user_id": user["id"], "order_number": o["order_number"]},
                    {"$set": patch},
                )
                o.update(patch)  # refresh in-memory copy so totals use new values
        except Exception as _exc:
            logger.warning("Dashboard cost self-heal skipped: %s", _exc)

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
    # Iter-207c — Snapshot the Salla-reference universe BEFORE the
    # status filter so the UI can render a transparency badge:
    #   "+X طلب معلَّق/ملغى بقيمة Y ر.س"
    salla_ref_orders_count = len(all_orders)
    salla_ref_gross = round(
        sum(float(o.get("total_amount") or 0) for o in all_orders), 2,
    )
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

    # ── iter-256 — Shipping cost SSOT consolidation ───────────────────
    # Replace match_settings' shipping breakdown with the canonical
    # `shipping_cost_ssot.aggregate_breakdown()` so the dashboard
    # ALWAYS agrees with /api/shipping-ledger (same per-order/per-company
    # math: total = base + tax). Payment breakdown is unrelated and stays
    # on match_settings.
    from shipping_cost_ssot import (
        aggregate_breakdown as _ssot_agg,
        get_company_configs as _ssot_cfgs,
    )
    _ssot_company_cfgs = await _ssot_cfgs(db, user["id"])
    _ssot_agg_result = _ssot_agg(all_orders, _ssot_company_cfgs)
    _ssot_breakdown = []
    _ssot_deferred = 0.0
    for pc in _ssot_agg_result["per_company"].values():
        cfg = _ssot_company_cfgs.get(pc["name"]) or {}
        is_deferred = bool(cfg.get("is_deferred", False))
        cfg_has_cost = (
            cfg.get("cost_per_order") is not None
            or cfg.get("cost") is not None
        )
        row = {
            "name":           pc["name"],
            "orders_count":   pc["orders_count"],
            # Dashboard-legacy fields (kept for backward-compat with
            # /api/dashboard consumers and _merge_breakdown):
            "cost_per_order": pc["cost_per_unit"],
            "base_cost":      pc["base"],
            "vat_amount":     pc["tax"],
            "vat_percent":    round(pc["vat_rate"] * 100, 2),
            "total_cost":     pc["total"],
            "matched":        cfg_has_cost,
            "is_deferred":    is_deferred,
            # SSOT canonical per-unit fields — used by the new unified
            # breakdown table in ProfitSummaryCard:
            "cost_per_unit":  pc["cost_per_unit"],
            "tax_per_unit":   pc["tax_per_unit"],
            "total_per_unit": pc["total_per_unit"],
            "vat_rate":       pc["vat_rate"],
        }
        if is_deferred:
            _ssot_deferred += pc["total"]
        _ssot_breakdown.append(row)
    # Authoritative shipping numbers (SSOT-driven):
    matched_all["shipping_breakdown"]      = _ssot_breakdown
    matched_all["total_shipping_cost"]     = round(
        _ssot_agg_result["total_with_tax"], 2)
    matched_all["deferred_shipping_cost"]  = round(_ssot_deferred, 2)

    total_sales = parsed_all["total_sales"]
    total_orders = parsed_all["total_orders"]
    total_fees = matched_all["total_payment_fees"]
    total_shipping = matched_all["total_shipping_cost"]
    deferred_shipping = matched_all.get("deferred_shipping_cost", 0.0)

    # BNPL / electronic / COD split — iter-64 uses the unified
    # normalize_payment_method() so the same classification logic powers
    # Dashboard, Accounts, and Reports.
    from payment_methods import normalize_payment_method as _npm
    total_vat = 0.0
    bnpl_fees = tamara_fees = tabby_fees = emkan_fees = 0.0
    other_payment_fees = 0.0
    bnpl_sales = other_payment_sales = cod_sales = cod_fees = 0.0
    # iter-47 — Bank transfer split into its own KPI card.
    bank_sales = bank_fees = 0.0
    for p in matched_all.get("payment_breakdown", []):
        total_vat += float(p.get("vat_amount", 0) or 0)
        raw_name = p.get("name", "") or ""
        fee = float(p.get("fee_amount", 0) or 0)
        sales = float(p.get("total_sales", 0) or 0)
        sub_key, _disp, parent = _npm(raw_name)
        # Effective bucket: bank rails / salla rails collapse to their parent.
        if parent == "bank_transfer" or sub_key == "bank_transfer":
            bank_fees += fee; bank_sales += sales
        elif sub_key == "tamara":
            tamara_fees += fee; bnpl_fees += fee; bnpl_sales += sales
        elif sub_key == "tabby":
            tabby_fees += fee; bnpl_fees += fee; bnpl_sales += sales
        elif sub_key == "emkan":
            emkan_fees += fee; bnpl_fees += fee; bnpl_sales += sales
        elif sub_key == "cash_on_delivery":
            cod_fees += fee; cod_sales += sales
        else:
            other_payment_fees += fee; other_payment_sales += sales
    for sh in matched_all.get("shipping_breakdown", []):
        total_vat += float(sh.get("vat_amount", 0) or 0)

    # ── iter-45 — Electronic Net status filter ─────────────────────────────
    # The default `other_payment_sales` / `other_payment_fees` above include
    # EVERY order, even cancelled/refunded/failed ones. Salla's "غير المفوترة"
    # screen only shows transactions actually captured by the gateway. We
    # recompute `other_payment_sales` & `other_payment_fees` using a status
    # filter that mirrors Salla's behaviour. The other buckets (BNPL/COD)
    # stay untouched so we don't break the existing tests/cards.
    elec_excluded_terms = settings.get(
        "electronic_net_excluded_statuses",
    )
    if elec_excluded_terms is None:
        elec_excluded_terms = DEFAULT_ELECTRONIC_NET_EXCLUDED_STATUSES

    def _is_electronic_method(payment_method: str) -> bool:
        """Electronic = Salla card rails (mada, Apple Pay, STC Pay, cards,
        wallet). Bank transfer, BNPL providers, and COD are NOT electronic."""
        sub_key, _disp, parent = _npm(payment_method or "")
        if not sub_key:
            return False
        # The 'salla' parent groups all electronic card rails.
        return parent == "salla"

    # Build a filtered electronic-only order list.
    electronic_orders_included: list[dict] = []
    electronic_orders_excluded: list[dict] = []
    for o in all_orders:
        if not _is_electronic_method(o.get("payment_method", "")):
            continue
        if _is_excluded_for_electronic_net(o.get("order_status", ""), elec_excluded_terms):
            electronic_orders_excluded.append(o)
        else:
            electronic_orders_included.append(o)

    if electronic_orders_included or electronic_orders_excluded:
        parsed_elec = orders_to_parsed(electronic_orders_included)
        matched_elec = match_settings(
            parsed_elec,
            settings.get("payment_methods", DEFAULT_PAYMENT_METHODS),
            settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
        )
        # Override electronic sales/fees with the filtered figures.
        filtered_elec_sales = 0.0
        filtered_elec_fees = 0.0
        for p in matched_elec.get("payment_breakdown", []):
            filtered_elec_sales += float(p.get("total_sales", 0) or 0)
            filtered_elec_fees += float(p.get("fee_amount", 0) or 0)
        # Stash the pre-filter values for transparency in the response.
        electronic_net_breakdown = {
            "included_count": len(electronic_orders_included),
            "excluded_count": len(electronic_orders_excluded),
            "excluded_statuses_active": list(elec_excluded_terms),
            "gross_before_filter": round(other_payment_sales, 2),
            "fees_before_filter": round(other_payment_fees, 2),
            "gross_after_filter": round(filtered_elec_sales, 2),
            "fees_after_filter": round(filtered_elec_fees, 2),
        }
        other_payment_sales = filtered_elec_sales
        other_payment_fees = filtered_elec_fees
    else:
        electronic_net_breakdown = {
            "included_count": 0,
            "excluded_count": 0,
            "excluded_statuses_active": list(elec_excluded_terms),
            "gross_before_filter": round(other_payment_sales, 2),
            "fees_before_filter": round(other_payment_fees, 2),
            "gross_after_filter": round(other_payment_sales, 2),
            "fees_after_filter": round(other_payment_fees, 2),
        }

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
    # Keyword sets used by the legacy-analyses payment_breakdown
    # classifier below. Defined here (not in the inline loop) so the
    # references survive even when the new normalize_payment_method
    # path skips the legacy branch.
    tamara_keywords = ("تمارا", "tamara")
    tabby_keywords = ("تابي", "tabby")
    emkan_keywords = ("إمكان", "امكان", "emkan", "amkan")
    cod_keywords = ("عند الاستلام", "عند الاستلم", "cod",
                     "cash on delivery", "cash_on_delivery")
    bank_keywords = (
        "تحويل بنكي", "حوالة بنكية", "تحويل البنك", "تحويل بنوك",
        "bank transfer", "bank_transfer", "wire transfer",
    )
    if include_legacy_analyses and not included_statuses:
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
                elif any(k in name_lc for k in bank_keywords) or name_lc == "bank":
                    bank_fees += fee; bank_sales += sales
                else:
                    other_payment_fees += fee; other_payment_sales += sales
            for sh in rep.get("shipping_breakdown", []) or []:
                total_vat += float(sh.get("vat_amount", 0) or 0)

    # ── Shipping & COD balance splits (Phase 1) ──────────────────────────────
    from shipping_cost_ssot import get_company_configs as _ssot_get_cfgs
    _ssot_cfgs = await _ssot_get_cfgs(db, user["id"])
    balances = compute_balances(
        all_orders,
        settings.get("shipping_approved_statuses", DEFAULT_SHIPPING_APPROVED),
        settings.get("cod_approved_statuses", DEFAULT_COD_APPROVED),
        company_cfgs=_ssot_cfgs,
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
    # Iter-160 SSOT (Message #737): TikTok spend from ledger only.
    _fd = from_date or "0000-01-01"
    _td = to_date or "9999-12-31"
    _tt_ledger = await _spend_by_date_from_ledger(
        db, user["id"], ("tiktok",), _fd, _td,
    )
    tiktok_spend = sum(_tt_ledger.values())
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
    # Iter-160 SSOT (Message #737): Meta spend from ledger only.
    _meta_ledger = await _spend_by_date_from_ledger(
        db, user["id"], ("meta", "facebook", "instagram"), _fd, _td,
    )
    meta_spend_total = sum(_meta_ledger.values())
    meta_purchases_total = sum(int(r.get("purchases") or 0) for r in meta_rows)
    meta_revenue_total = sum(float(r.get("revenue") or 0) for r in meta_rows)

    # ── Total ads cost across all platforms ──────────────────────────────
    # Iter-160 SSOT (Message #737): all ad spend must come from
    # ad_account_ledger. We no longer read daily_costs.snapchat_ads/
    # snapchat_ads_2 / instagram_ads / google_ads / tiktok_ads for the
    # aggregate spend. Those manual-entry fields are deprecated for
    # accounting purposes — only the unified ledger counts.
    _snap_ledger = await _spend_by_date_from_ledger(
        db, user["id"], ("snapchat", "snap"), _fd, _td,
    )
    snap_spend_total = sum(_snap_ledger.values())
    # Other providers (google/twitter/other) from ledger as well.
    _other_ledger = await _spend_by_date_from_ledger(
        db, user["id"], ("google", "twitter", "other"), _fd, _td,
    )
    other_spend_total = sum(_other_ledger.values())
    daily_ads_total = (snap_spend_total + tiktok_spend
                       + meta_spend_total + other_spend_total)
    daily_products_total = sum((d.get("product_costs", 0) or 0) for d in daily)

    # ── Computed product cost from order line-items (iteration 19) ─────────
    # When `unified_orders.total_product_cost` is populated (via webhook
    # ingestion → product_costs.attach_cost_to_order_doc), prefer THAT as
    # the source of truth for product cost — it reflects real SKU-level
    # costs. The legacy `daily_costs.product_costs` (manual entry) stays
    # as a fallback so single-merchant flows without per-SKU costs still
    # work. We take max() per the same dedupe-via-bigger pattern used for
    # TikTok webhook vs daily_costs.
    # Iter-91 Phase 1: subtract COGS for cancelled/refunded orders and
    # scale partial-refund orders proportionally so the profit KPI no
    # longer over-charges product cost on returned goods.
    from order_status_policy import (
        effective_product_cost as _effective_pc,
        get_policy_map as _get_policy_map,
    )
    policy_overrides_pc = await _get_policy_map(db, user["id"])
    computed_product_cost = round(sum(
        _effective_pc(o, policy_overrides_pc) for o in all_orders
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

    # iter-64 — Roll the per-raw-name payment_breakdown rows up into the
    # SAME canonical buckets used by Accounts (سلة, تحويل بنكي, تابي, …).
    # Each bucket keeps a `sub_methods` array so the UI can still show the
    # original Salla rail / specific bank inside the row.
    def _rollup_payment_breakdown(rows: list[dict]) -> list[dict]:
        buckets: dict[str, dict] = {}
        for r in rows:
            raw = (r.get("name") or "").strip()
            sub_key, sub_disp, parent = _npm(raw)
            if not sub_key:
                # Skip null/unknown markers — they'd pollute the table.
                continue
            top_key = parent or sub_key
            top_disp = PARENT_LABELS.get(parent, sub_disp) if parent else sub_disp
            b = buckets.setdefault(top_key, {
                "name": top_disp,
                "key": top_key,
                "total_sales": 0.0,
                "fee_amount": 0.0,
                "vat_amount": 0.0,
                "orders_count": 0,
                "commission_percent": r.get("commission_percent"),
                "fixed_fee": r.get("fixed_fee"),
                "vat_percent": r.get("vat_percent"),
                "sub_methods": [],
            })
            sales = float(r.get("total_sales") or 0)
            fee   = float(r.get("fee_amount") or 0)
            vat   = float(r.get("vat_amount") or 0)
            cnt   = int(r.get("orders_count") or 0)
            b["total_sales"] += sales
            b["fee_amount"]  += fee
            b["vat_amount"]  += vat
            b["orders_count"] += cnt
            # Aggregate sub-methods by their CANONICAL key so multiple raw
            # spellings of e.g. الراجحي collapse into one sub-row.
            sm_idx = {s["key"]: i for i, s in enumerate(b["sub_methods"])}
            if sub_key in sm_idx:
                s = b["sub_methods"][sm_idx[sub_key]]
                s["total_sales"] += sales
                s["fee_amount"]  += fee
                s["orders_count"] += cnt
            else:
                b["sub_methods"].append({
                    "key": sub_key,
                    "display": sub_disp,
                    "name": sub_disp,
                    "total_sales": sales,
                    "fee_amount": fee,
                    "orders_count": cnt,
                })
        # Sort sub_methods by sales desc, round everything for the response.
        out = []
        for b in buckets.values():
            b["sub_methods"].sort(key=lambda s: s["total_sales"], reverse=True)
            for s in b["sub_methods"]:
                s["total_sales"] = round(s["total_sales"], 2)
                s["fee_amount"]  = round(s["fee_amount"], 2)
            b["total_sales"] = round(b["total_sales"], 2)
            b["fee_amount"]  = round(b["fee_amount"], 2)
            b["vat_amount"]  = round(b["vat_amount"], 2)
            out.append(b)
        out.sort(key=lambda x: x["total_sales"], reverse=True)
        return out

    from payment_methods import PARENT_LABELS
    payment_breakdown_merged = _rollup_payment_breakdown(payment_breakdown_merged)

    # ── Iter-44: Cross-platform ROAS + Average Cost Per Order ────────────
    # ROAS (Return On Ad Spend) — how many SAR of revenue each SAR of ad
    # spend produced. Industry-standard formula is gross sales ÷ total
    # ad spend (matches GA, Meta, TikTok, Snap conventions); merchants
    # who prefer net-sales-based ROAS can flip a setting later.
    # CPA (متوسط تكلفة الطلب) — total ad spend ÷ number of orders.
    # Both surfaced as null when the denominator is 0 so the UI can show
    # "—" instead of "Infinity" or "NaN".
    overall_roas = round(total_sales / daily_ads_total, 2) if daily_ads_total > 0 else None
    avg_cost_per_order = (
        round(daily_ads_total / total_orders, 2) if total_orders > 0 and daily_ads_total > 0 else None
    )

    # ── iter-56 — Payment Adjustments (الاسترجاعات/التسويات) ────────────────
    # Subtract any partial refunds / item removals / cancellations that were
    # recorded against orders in the period (or even pre-period orders whose
    # adjustment date falls in the period — matching Salla's actual wallet
    # behavior). This affects per-provider NET sales; gross sales remain
    # untouched so totals stay traceable to raw orders.
    settlements_by_provider = await aggregate_settlements_by_provider(
        db, user["id"], from_date, to_date
    )
    salla_adj         = settlements_by_provider["salla"]["total_adjustment"]
    tamara_adj        = settlements_by_provider["tamara"]["total_adjustment"]
    tabby_adj         = settlements_by_provider["tabby"]["total_adjustment"]
    emkan_adj         = settlements_by_provider["emkan"]["total_adjustment"]
    bank_adj          = settlements_by_provider["bank_transfer"]["total_adjustment"]
    cod_adj           = settlements_by_provider["cod"]["total_adjustment"]
    other_adj         = settlements_by_provider["other"]["total_adjustment"]

    # iter-73 — surface refunded_amount + refunds_count per provider on the
    # payment_breakdown rows so the Reports commission cards can show how
    # much was reversed via settlements for each gateway. The keys of
    # `settlements_by_provider` are the same canonical names used by the
    # rollup ('salla', 'tamara', 'tabby', 'emkan', 'bank_transfer', 'cod').
    _settlement_key_map = {
        "salla": "salla",
        "tamara": "tamara",
        "tabby": "tabby",
        "emkan": "emkan",
        "bank_transfer": "bank_transfer",
        "cash_on_delivery": "cod",
    }
    for _b in payment_breakdown_merged:
        _sk = _settlement_key_map.get(_b.get("key"))
        _agg = settlements_by_provider.get(_sk) if _sk else None
        _b["refunded_amount"] = round(float(_agg.get("total_adjustment") or 0), 2) if _agg else 0.0
        _b["refunds_count"]   = int(_agg.get("count") or 0) if _agg else 0
    total_adjustments = round(
        salla_adj + tamara_adj + tabby_adj + emkan_adj + bank_adj + cod_adj + other_adj, 2,
    )

    # Salla-specific: split adjustments by whether the original order is
    # still within Salla's 14-day pending wallet. Used by the "Salla wallet
    # alert" badge to explain reference mismatches.
    salla_settle_inside = salla_settle_outside = 0.0
    salla_settle_docs = await db.payment_adjustments.find(
        {
            "user_id": user["id"],
            "provider": "salla",
            **({"adjusted_at": {**({"$gte": from_date} if from_date else {}),
                                **({"$lte": to_date} if to_date else {})}}
               if (from_date or to_date) else {}),
        },
        {"_id": 0, "adjustment_amount": 1, "order_created_at": 1},
    ).to_list(20000)
    for d in salla_settle_docs:
        if classify_14d_window(d.get("order_created_at", "")) == "inside_14d":
            salla_settle_inside += float(d.get("adjustment_amount", 0) or 0)
        else:
            salla_settle_outside += float(d.get("adjustment_amount", 0) or 0)

    return {
        "range": {"from_date": from_date, "to_date": to_date},
        "totals": {
            "total_sales": round(total_sales, 2),
            "net_sales": round(net_sales, 2),
            "total_orders": int(total_orders),
            # iter-44 — cross-platform marketing KPIs
            "overall_roas": overall_roas,
            "avg_cost_per_order": avg_cost_per_order,
            "total_payment_fees": round(total_fees, 2),
            "bnpl_fees": round(bnpl_fees, 2),
            "tamara_fees": round(tamara_fees, 2),
            "tabby_fees": round(tabby_fees, 2),
            "emkan_fees": round(emkan_fees, 2),
            "other_payment_fees": round(other_payment_fees, 2),
            # iter-56 — electronic_net now subtracts Salla settlements too
            "electronic_net": round(
                other_payment_sales - other_payment_fees - salla_adj, 2,
            ),
            "electronic_net_before_settlements": round(
                other_payment_sales - other_payment_fees, 2,
            ),
            # iter-45 — visible filtering metadata for the UI
            "electronic_net_breakdown": electronic_net_breakdown,
            "salla_electronic_net_reference": settings.get("salla_electronic_net_reference"),
            # iter-56 — per-provider adjustment totals + breakdown
            "settlements_total": total_adjustments,
            "settlements_by_provider": settlements_by_provider,
            "salla_settlements_inside_14d": round(salla_settle_inside, 2),
            "salla_settlements_outside_14d": round(salla_settle_outside, 2),
            "bnpl_net": round(bnpl_sales - bnpl_fees - tamara_adj - tabby_adj - emkan_adj, 2),
            # iter-47 — Bank transfer is now a dedicated KPI; the figures
            # below give the UI everything it needs to render the new card.
            "bank_sales": round(bank_sales, 2),
            "bank_fees": round(bank_fees, 2),
            "bank_net": round(bank_sales - bank_fees - bank_adj, 2),
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
            # ── Iter-207c — Salla-reference transparency block ─────────────
            # The platform (Salla) counts every order it creates regardless
            # of status. We count only orders that pass
            # `report_included_statuses`. Surface the gap so the UI can
            # render a badge "+X معلَّق/ملغى بقيمة Y ر.س" next to the main
            # orders count, and a tooltip explaining the methodology.
            "salla_reference_orders_count": int(salla_ref_orders_count),
            "salla_reference_gross": float(salla_ref_gross),
            "excluded_orders_count": int(
                max(0, salla_ref_orders_count - total_orders)),
            "excluded_gross": round(
                max(0.0, salla_ref_gross - float(total_sales)), 2),
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


# ── Iter-207d — Excluded Orders Drill-down ────────────────────────────────
@api.get("/dashboard/excluded-orders")
async def excluded_orders_list(
    user: dict = Depends(current_user),
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    """Return the orders that the Dashboard's `total_orders` /
    `total_sales` figures EXCLUDED (because their status didn't match
    `report_included_statuses` OR they were classified pending /
    cancelled).

    Used by the transparency badge on `ProfitSummaryCard` and
    `UnifiedPaymentGatewaysCard` to let the merchant inspect exactly
    which 3 orders cause the 554.56 ر.س gap with the Salla platform.
    """
    settings = await ensure_user_settings(db, user["id"])
    included_statuses = settings.get("report_included_statuses") or []
    # Pull every order in the window; we'll classify in Python so we
    # can mirror the EXACT semantics of the dashboard filter.
    q: dict = {"user_id": user["id"]}
    if from_date or to_date:
        q["order_date"] = {}
        if from_date:
            q["order_date"]["$gte"] = from_date
        if to_date:
            q["order_date"]["$lte"] = to_date
    all_orders = await db.unified_orders.find(
        q, {"_id": 0, "raw_by_source": 0},
    ).to_list(50000)

    excluded: list = []
    for o in all_orders:
        status = (o.get("order_status") or "").strip()
        # Same case-insensitive partial-match logic used by
        # /api/dashboard (line ~1842-1846).
        if included_statuses:
            if not _matches_any(status, included_statuses):
                excluded.append(o)
                continue
        # No-status orders are also excluded by the dashboard
        # filter when included_statuses is non-empty.
        # If included_statuses is empty we still bucket pending/
        # cancelled separately to mirror the gateway endpoint's
        # behaviour. Resolve via the existing order_status_policy.
        from order_status_policy import (
            get_policy_map as _gp,
            resolve_category as _rc,
        )
        # Cache the policy map across the loop for perf.
        if "_policy" not in locals():
            _policy = await _gp(db, user["id"])
        else:
            _policy = locals()["_policy"]
        cat = _rc(status, _policy)
        if cat in ("pending", "cancelled"):
            excluded.append(o)

    # Slim payload — only fields the modal needs to render the table.
    rows = []
    total_value = 0.0
    for o in excluded:
        amt = float(o.get("total_amount") or 0)
        total_value += amt
        rows.append({
            "order_number": str(o.get("order_number") or ""),
            "order_date": o.get("order_date") or "",
            "order_status": (o.get("order_status") or "").strip(),
            "payment_method": (o.get("payment_method") or "غير محدد").strip()
                              or "غير محدد",
            "shipping_company": (o.get("shipping_company") or "—").strip()
                                or "—",
            "customer_name": (o.get("customer_name") or "").strip()
                             or (o.get("customer") or {}).get("name") or "",
            "total_amount": round(amt, 2),
        })
    rows.sort(key=lambda x: (x["order_date"], x["order_number"]))
    return {
        "from_date": from_date,
        "to_date": to_date,
        "included_statuses": included_statuses,
        "orders_count": len(rows),
        "total_amount": round(total_value, 2),
        "orders": rows,
    }


# ── Ads-cost breakdown — drill-down for ProfitSummaryCard ───────────────────
@api.get("/dashboard/ads-cost-breakdown")
async def ads_cost_breakdown(
    user: dict = Depends(current_user),
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    """READ-ONLY drill-down for the "إجمالي تكاليف الإعلانات" tile on
    the dashboard. Returns every `ad_account_ledger.type=spend` row in
    the inclusive date window [from_date, to_date], joined with the
    counterparty (so the UI can show ad-account name + provider).

    Same accounting source the Dashboard total uses (Iter-160 SSOT —
    `ad_account_ledger`), so the totals always reconcile with the
    aggregated KPI value.
    """
    uid = user["id"]
    q: dict = {"user_id": uid, "type": "spend"}
    if from_date or to_date:
        q["date"] = {}
        if from_date:
            q["date"]["$gte"] = from_date
        if to_date:
            q["date"]["$lte"] = to_date

    rows = await db.ad_account_ledger.find(
        q,
        {"_id": 0, "id": 1, "counterparty_id": 1, "amount": 1,
         "date": 1, "description": 1, "breakdown": 1, "created_at": 1,
         "balance_after": 1, "debt_after": 1},
    ).sort([("date", -1), ("created_at", -1)]).to_list(5000)

    cp_ids = list({r.get("counterparty_id") for r in rows
                   if r.get("counterparty_id")})
    cp_map: dict = {}
    if cp_ids:
        async for cp in db.counterparties.find(
            {"user_id": uid, "id": {"$in": cp_ids}},
            {"_id": 0, "id": 1, "name": 1, "ad_provider": 1},
        ):
            cp_map[cp["id"]] = {
                "name": cp.get("name") or "—",
                "ad_provider": cp.get("ad_provider") or "—",
            }

    items = []
    grand = 0.0
    by_provider: dict = {}
    by_account: dict = {}
    for r in rows:
        cp_id = r.get("counterparty_id")
        cp_info = cp_map.get(cp_id, {"name": "—", "ad_provider": "—"})
        amount = round(float(r.get("amount") or 0), 2)
        bd = r.get("breakdown") or {}
        source_tag = (
            "cron (مزامنة تلقائية)"
            if bd.get("auto_cron") else "manual"
        )
        items.append({
            "id": r.get("id"),
            "date": r.get("date"),
            "ad_account_id": cp_id,
            "ad_account_name": cp_info["name"],
            "ad_provider": cp_info["ad_provider"],
            "amount": amount,
            "description": r.get("description") or "",
            "source": source_tag,
            "covered_from_balance": round(
                float(bd.get("from_balance") or 0), 2),
            "created_debt": round(
                float(bd.get("created_debt")
                      or bd.get("uncovered") or 0), 2),
            "platform_total": round(
                float(bd.get("platform_total") or 0), 2),
            "balance_after": round(
                float(r.get("balance_after") or 0), 2),
            "debt_after": round(float(r.get("debt_after") or 0), 2),
            "created_at": r.get("created_at"),
        })
        grand += amount
        by_provider[cp_info["ad_provider"]] = (
            by_provider.get(cp_info["ad_provider"], 0.0) + amount)
        by_account[cp_info["name"]] = (
            by_account.get(cp_info["name"], 0.0) + amount)

    return {
        "ok": True,
        "from_date": from_date,
        "to_date": to_date,
        "total_amount": round(grand, 2),
        "total_entries": len(items),
        "by_provider": {k: round(v, 2) for k, v in by_provider.items()},
        "by_account": {k: round(v, 2) for k, v in by_account.items()},
        "items": items,
    }


# ── iter-45 — Electronic Net debug / verification endpoint ────────────────
@api.get("/dashboard/electronic-net-debug")
async def electronic_net_debug(
    user: dict = Depends(current_user),
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    """Return a fully-itemized breakdown of the "صافي المدفوعات الإلكترونية"
    KPI so the merchant can audit it against Salla's "غير المفوترة" screen.
    """
    settings = await ensure_user_settings(db, user["id"])
    elec_excluded_terms = settings.get("electronic_net_excluded_statuses")
    if elec_excluded_terms is None:
        elec_excluded_terms = DEFAULT_ELECTRONIC_NET_EXCLUDED_STATUSES

    orders_q: dict = {"user_id": user["id"]}
    if from_date or to_date:
        orders_q["order_date"] = {}
        if from_date:
            orders_q["order_date"]["$gte"] = from_date
        if to_date:
            orders_q["order_date"]["$lte"] = to_date
    all_orders = await db.unified_orders.find(orders_q, {"_id": 0}).to_list(20000)

    if settings.get("hide_inferred_date_orders"):
        all_orders = [o for o in all_orders if not bool(o.get("order_date_inferred"))]

    included_statuses = settings.get("report_included_statuses") or []
    if included_statuses:
        # Substring match (case-insensitive) — inlined here because
        # the dashboard's `_matches_any` helper is a nested function and
        # not in this module's scope.
        included_lower = [s.strip().lower() for s in included_statuses if s and s.strip()]
        all_orders = [
            o for o in all_orders
            if any(t in (o.get("order_status", "") or "").strip().lower()
                   for t in included_lower)
        ]

    tamara_keywords = ("تمارا", "tamara")
    tabby_keywords = ("تابي", "tabby")
    emkan_keywords = ("إمكان", "امكان", "emkan", "amkan")
    cod_keywords = ("عند الاستلام", "عند الاستلم", "cod", "cash on delivery", "cash_on_delivery")
    # iter-47 — bank transfer is excluded from the electronic-net audit
    bank_keywords = (
        "تحويل بنكي", "حوالة بنكية", "تحويل البنك", "تحويل بنوك",
        "bank transfer", "bank_transfer", "wire transfer",
    )

    def _is_electronic(name: str) -> bool:
        n = (name or "").strip().lower()
        if not n:
            return False
        if any(k in n for k in tamara_keywords):
            return False
        if any(k in n for k in tabby_keywords):
            return False
        if any(k in n for k in emkan_keywords):
            return False
        if any(k in n for k in cod_keywords):
            return False
        # iter-47 — bank has its own KPI; never count in electronic-net.
        if any(k in n for k in bank_keywords):
            return False
        if n == "bank":
            return False
        return True

    electronic_total = 0
    electronic_included: list[dict] = []
    electronic_excluded: list[dict] = []
    status_excluded_counts: dict[str, int] = {}

    for o in all_orders:
        pm = o.get("payment_method", "")
        if not _is_electronic(pm):
            continue
        electronic_total += 1
        status = (o.get("order_status") or "").strip()
        if _is_excluded_for_electronic_net(status, elec_excluded_terms):
            electronic_excluded.append(o)
            key = status or "(فارغ)"
            status_excluded_counts[key] = status_excluded_counts.get(key, 0) + 1
        else:
            electronic_included.append(o)

    def _compute_with_fees(orders: list[dict]) -> tuple[float, float, list[dict]]:
        parsed = orders_to_parsed(orders)
        matched = match_settings(
            parsed,
            settings.get("payment_methods", DEFAULT_PAYMENT_METHODS),
            settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
        )
        gross = sum(float(p.get("total_sales", 0) or 0) for p in matched.get("payment_breakdown", []))
        fees = sum(float(p.get("fee_amount", 0) or 0) for p in matched.get("payment_breakdown", []))
        return gross, fees, matched.get("payment_breakdown", [])

    all_electronic_orders = electronic_included + electronic_excluded
    pre_gross, pre_fees, _ = _compute_with_fees(all_electronic_orders)
    post_gross, post_fees, post_breakdown = _compute_with_fees(electronic_included)

    def _short(o: dict, *, reason: Optional[str] = None) -> dict:
        d = {
            "order_number": o.get("order_number") or "",
            "order_date": o.get("order_date") or "",
            "payment_method": o.get("payment_method") or "",
            "order_status": o.get("order_status") or "",
            "total_amount": round(float(o.get("total_amount") or 0), 2),
        }
        if reason:
            d["exclusion_reason"] = reason
        return d

    excluded_sample = [
        _short(o, reason=_first_match_reason(o.get("order_status", ""), elec_excluded_terms))
        for o in electronic_excluded[:50]
    ]
    included_sample = [_short(o) for o in electronic_included[:50]]

    ref = settings.get("salla_electronic_net_reference")
    computed_net = round(post_gross - post_fees, 2)
    gap = None
    gap_pct = None
    if isinstance(ref, (int, float)) and ref > 0:
        gap = round(computed_net - float(ref), 2)
        gap_pct = round((gap / float(ref)) * 100, 2) if ref else None

    return {
        "range": {"from_date": from_date, "to_date": to_date},
        "excluded_statuses_active": list(elec_excluded_terms),
        "totals": {
            "electronic_orders_total": electronic_total,
            "electronic_orders_included": len(electronic_included),
            "electronic_orders_excluded": len(electronic_excluded),
            "pre_filter_gross": round(pre_gross, 2),
            "pre_filter_fees": round(pre_fees, 2),
            "pre_filter_net": round(pre_gross - pre_fees, 2),
            "post_filter_gross": round(post_gross, 2),
            "post_filter_fees": round(post_fees, 2),
            "post_filter_net": computed_net,
        },
        "salla_reference": {
            "value": ref,
            "gap_vs_computed": gap,
            "gap_percent": gap_pct,
        },
        "excluded_by_status": [
            {"status": k, "count": v}
            for k, v in sorted(status_excluded_counts.items(), key=lambda x: -x[1])
        ],
        "payment_breakdown_after_filter": [
            {
                "name": p.get("name"),
                "orders_count": int(p.get("orders_count") or 0),
                "total_sales": round(float(p.get("total_sales") or 0), 2),
                "fee_amount": round(float(p.get("fee_amount") or 0), 2),
                "net_amount": round(
                    float(p.get("total_sales") or 0) - float(p.get("fee_amount") or 0), 2,
                ),
                "commission_percent": float(p.get("commission_percent") or 0),
                "fixed_fee": float(p.get("fixed_fee") or 0),
                "vat_percent": float(p.get("vat_percent") or 0),
            }
            for p in post_breakdown
        ],
        "excluded_orders_sample": excluded_sample,
        "included_orders_sample": included_sample,
    }


def _first_match_reason(status: str, excluded_terms: list[str]) -> str:
    """Return the first excluded-term that matched this status."""
    s = (status or "").strip().lower()
    if not s:
        return ""
    for t in excluded_terms:
        if t and t.strip().lower() in s:
            return t
    return ""


# ── iter-45 — One-click "Sync to Salla" preset ────────────────────────────
@api.post("/settings/electronic-net/sync-to-salla")
async def electronic_net_sync_to_salla(user: dict = Depends(current_user)):
    """Restore the Salla-compatible default exclusion list."""
    new_list = list(DEFAULT_ELECTRONIC_NET_EXCLUDED_STATUSES)
    await db.settings.update_one(
        {"user_id": user["id"]},
        {"$set": {
            "electronic_net_excluded_statuses": new_list,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    return {"ok": True, "electronic_net_excluded_statuses": new_list}




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
    """Count orders + revenue using normalized and raw Salla source paths.

    Source matching includes the current ``source_details.utm_source`` shape
    as well as durable normalized fields (case-insensitive, partial-match).
    Date filter uses `order_date` BETWEEN start and end (inclusive).

    Returns (orders, revenue).
    """
    if not source_aliases:
        return 0, 0.0
    # Build case-insensitive regex matching ANY alias substring.
    pattern = "|".join(source_aliases)
    source_paths = (
        "utm_source",
        "source_native",
        "traffic_source",
        "marketing_source",
        "ad_platform_source",
        "raw_by_source.salla_direct.utm_source",
        "raw_by_source.salla_direct.source_details.utm_source",
        "raw_by_source.salla_direct.utm.source",
        "raw_by_source.salla_direct.marketing.utm_source",
        "raw_by_source.salla_direct.attribution.utm_source",
        "raw_by_source.salla_direct.campaign.source",
    )
    pipeline = [
        {"$match": {
            "user_id": uid,
            "order_date": {"$gte": start, "$lte": end},
            "$or": [
                {path: {"$regex": pattern, "$options": "i"}}
                for path in source_paths
            ],
        }},
        {"$group": {"_id": None,
                    "orders": {"$sum": 1},
                    "revenue": {"$sum": {"$ifNull": ["$total_amount", 0]}}}},
    ]
    async for d in db.unified_orders.aggregate(pipeline):
        return int(d.get("orders", 0)), round(float(d.get("revenue", 0)), 2)
    return 0, 0.0


# ── Iter-160 (Message #737 directive) ────────────────────────────────────────
# Single Source of Truth: ad spend on the Dashboard MUST be aggregated
# strictly from `ad_account_ledger` (type=spend). Old paths reading from
# `daily_costs.snapchat_ads`, `snapchat_ads_daily`, `meta_ads_daily`,
# `tiktok_ads_daily` are deprecated for spend totals. The raw API
# collections remain for purchases/impressions/clicks (NON-accounting
# metrics) but NEVER for the spend figure shown to merchants.
async def _spend_by_date_from_ledger(
    db, uid: str, providers: tuple, start: str, end: str,
) -> dict[str, float]:
    """Return {date_iso: total_spend} aggregated from ad_account_ledger
    for the given user, ad_provider aliases, and date window.

    Joins `ad_account_ledger.counterparty_id` → `counterparties.id` to
    filter by `ad_provider`. Returns ONE row per calendar date with
    the sum across all accounts of that provider for that user.
    """
    if not providers:
        return {}
    # Resolve counterparty ids matching any of these provider aliases.
    cps = await db.counterparties.find(
        {"user_id": uid, "kind": "ad_account",
         "ad_provider": {"$in": list(providers)}},
        {"_id": 0, "id": 1},
    ).to_list(200)
    cp_ids = [c["id"] for c in cps]
    if not cp_ids:
        return {}
    pipeline = [
        {"$match": {
            "user_id": uid,
            "counterparty_id": {"$in": cp_ids},
            "type": "spend",
            "date": {"$gte": start, "$lte": end},
        }},
        {"$group": {
            "_id": "$date",
            "spend": {"$sum": "$amount"},
        }},
    ]
    out: dict[str, float] = {}
    async for row in db.ad_account_ledger.aggregate(pipeline):
        out[row["_id"]] = float(row.get("spend") or 0)
    return out


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

    # 1) Snapchat spend — STRICTLY from ad_account_ledger (Iter-160 SSOT).
    #    Old reads from daily_costs.snapchat_ads* are no longer used to
    #    prevent double-counting between manual entries and API syncs.
    by_date_spend = await _spend_by_date_from_ledger(
        db, uid, ("snapchat", "snap"), d30_start_str, today_str,
    )

    spend_today = round(by_date_spend.get(today_str, 0.0), 2)
    spend_month = round(sum(v for k, v in by_date_spend.items() if k >= month_start_str), 2)
    spend_30d = round(sum(by_date_spend.values()), 2)

    # Display Snapchat spend in BOTH currencies. Stored values are SAR
    # (already converted at ingest), USD is derived using the user's
    # preferred USD→SAR rate from `ads_currency_settings` (Iter-243).
    # Falls back to 3.752 when no setting exists.
    _ads_cfg = await db.ads_currency_settings.find_one(
        {"user_id": uid}, {"_id": 0, "usd_to_sar_rate": 1},
    ) or {}
    USD_RATE = float(_ads_cfg.get("usd_to_sar_rate") or 3.752)

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

    def _cpo(spend: float, orders: int) -> Optional[float]:
        """Iter-89 — Cost per order = spend / orders.
        Returns None when either is zero so the UI shows '—'."""
        if not orders or not spend:
            return None
        return round(spend / orders, 2)

    return {
        "today": {
            "date": today_str,
            "spend": spend_today,
            "spend_usd": _to_usd(spend_today),
            "orders": orders_today,
            "revenue": revenue_today,
            "roas": _roas(revenue_today, spend_today),
            "cost_per_order": _cpo(spend_today, orders_today),
        },
        "month": {
            "start": month_start_str,
            "spend": spend_month,
            "spend_usd": _to_usd(spend_month),
            "orders": orders_month,
            "revenue": revenue_month,
            "roas": _roas(revenue_month, spend_month),
            "cost_per_order": _cpo(spend_month, orders_month),
        },
        "last_30d": {
            "start": d30_start_str,
            "spend": spend_30d,
            "spend_usd": _to_usd(spend_30d),
            "orders": orders_30d,
            "revenue": revenue_30d,
            "roas": _roas(revenue_30d, spend_30d),
            "cost_per_order": _cpo(spend_30d, orders_30d),
        },
        "usd_rate": USD_RATE,
        "last_fetched_at": last_fetched_at,
        "source": "snapchat_pixel" if snap_pixel_active else "store_orders",
        "history": history,
    }


# ── Iter-159j — Per-Snapchat-account dashboard summary ─────────────────
# Same numbers as `snapchat-summary` but BROKEN OUT per ad-account so the
# merchant can see each account's contribution independently in the
# dashboard. Orders + revenue are prorated by spend share (Snapchat Pixel
# doesn't report which ad-account drove each conversion).
@api.get("/dashboard/snapchat-accounts-summary")
async def snapchat_accounts_summary(user: dict = Depends(current_user)):
    uid = user["id"]
    today_d = _local_today_date()
    today_str = today_d.isoformat()
    month_start_str = today_str[:8] + "01"

    # All snapchat ad-account counterparties for this user.
    accts = await db.counterparties.find(
        {"user_id": uid, "kind": "ad_account", "ad_provider": "snapchat"},
        {"_id": 0, "id": 1, "name": 1, "external_account_id": 1,
         "credit_limit": 1, "alert_threshold_pct": 1,
         "currency": 1, "apply_bank_commission": 1},
    ).sort("name", 1).to_list(50)

    if not accts:
        return {"accounts": [], "month_start": month_start_str,
                "today": today_str}

    # Per-account month spend — from ad_account_ledger.type=spend.
    spend_pipeline = await db.ad_account_ledger.aggregate([
        {"$match": {"user_id": uid, "type": "spend",
                    "date": {"$gte": month_start_str, "$lte": today_str},
                    "counterparty_id": {"$in": [a["id"] for a in accts]}}},
        {"$group": {"_id": "$counterparty_id",
                    "spend": {"$sum": "$amount"}}},
    ]).to_list(100)
    spend_by_acct = {r["_id"]: float(r["spend"] or 0) for r in spend_pipeline}

    # Combined Snapchat orders + revenue this month (Pixel → fallback store).
    snap_stats = await db.snapchat_daily_stats.find(
        {"user_id": uid, "date": {"$gte": month_start_str, "$lte": today_str}},
        {"_id": 0, "purchases": 1, "revenue": 1},
    ).to_list(60)
    total_orders = sum(int(r.get("purchases") or 0) for r in snap_stats)
    total_revenue = round(
        sum(float(r.get("revenue") or 0) for r in snap_stats), 2)
    if total_orders == 0 and total_revenue == 0:
        # Fallback to store attribution
        total_orders, total_revenue = await _attributed_orders_from_store(
            db, uid, ("snapchat", "snap"), month_start_str, today_str,
        )

    # NOTE: `total_spend` (in raw account currency) was misleading
    # and removed in Iter-246l.  See `total_spend_sar` below, computed
    # AFTER per-account conversion.

    # Per-account open debt (for the "near limit" indicator).
    debt_pipeline = await db.liabilities.aggregate([
        {"$match": {"user_id": uid, "kind": "ad_account",
                    "counterparty_id": {"$in": [a["id"] for a in accts]},
                    "status": {"$in": ["unpaid", "partial"]}}},
        {"$group": {"_id": "$counterparty_id",
                    "open": {"$sum": {"$subtract": [
                        "$expected_amount", "$paid_amount"]}}}},
    ]).to_list(100)
    debt_by_acct = {r["_id"]: round(float(r["open"] or 0), 2)
                    for r in debt_pipeline}

    # Iter-246l — FX + bank-commission normalisation per account.
    # The ledger stores raw `amount` in the account's billing currency
    # (USD for most Snapchat accounts).  Without this normalisation
    # the per-account card showed «0.00 ر.س ≈ 420.65 USD» while the
    # aggregated card converted properly, hence the merchant's report.
    fx_doc = await db.ads_currency_settings.find_one(
        {"user_id": uid}, {"_id": 0})
    usd_to_sar = float(
        (fx_doc or {}).get("usd_to_sar_rate") or 3.7544)
    default_bank_pct = float(
        (fx_doc or {}).get("bank_commission_pct") or 0.0)

    def _to_sar(spend_raw: float, currency: str,
                apply_bank: bool) -> tuple[float, float, float]:
        """Returns (sar_no_fees, bank_fee, total_sar)."""
        if not spend_raw:
            return 0.0, 0.0, 0.0
        if (currency or "USD").upper() == "USD":
            sar = spend_raw * usd_to_sar
        else:
            sar = spend_raw
        fee = sar * (default_bank_pct / 100.0) if apply_bank else 0.0
        return round(sar, 2), round(fee, 2), round(sar + fee, 2)

    rows = []
    # First pass: convert each row to SAR so we can build a real total.
    converted: list[dict] = []
    for a in accts:
        spend_raw = round(spend_by_acct.get(a["id"], 0.0), 2)
        cur = (a.get("currency") or "USD").upper()
        apply_bank = bool(a.get("apply_bank_commission", True))
        sar_amount, bank_fee, spend_sar = _to_sar(
            spend_raw, cur, apply_bank)
        converted.append({
            "acct": a, "spend_raw": spend_raw, "currency": cur,
            "apply_bank": apply_bank, "sar_amount": sar_amount,
            "bank_fee": bank_fee, "spend_sar": spend_sar,
        })
    total_spend_sar = round(
        sum(c["spend_sar"] for c in converted), 2)

    for c in converted:
        a = c["acct"]
        spend_sar = c["spend_sar"]
        share = (spend_sar / total_spend_sar) if total_spend_sar > 0 else 0
        # Prorate orders + revenue by SAR-spend share so the cards
        # never split a percentage of zero or mix USD vs SAR.
        acc_orders = int(round(total_orders * share)) if share else 0
        acc_revenue = round(total_revenue * share, 2) if share else 0.0
        cpo = (round(spend_sar / acc_orders, 2)
               if acc_orders > 0 and spend_sar > 0 else None)
        roas = (round(acc_revenue / spend_sar, 2)
                if spend_sar > 0 else 0.0)
        rows.append({
            "id": a["id"],
            "name": a.get("name"),
            "external_account_id": a.get("external_account_id"),
            # `spend` keeps backward-compat — now the SAR total.
            "spend": spend_sar,
            "spend_raw": c["spend_raw"],
            "spend_currency": c["currency"],
            "spend_sar": c["sar_amount"],
            "bank_fee_sar": c["bank_fee"],
            "spend_total_sar": spend_sar,
            "fx_rate_used": usd_to_sar if c["currency"] == "USD" else 1.0,
            "bank_commission_pct_used": (
                default_bank_pct if c["apply_bank"] else 0.0),
            "spend_share_pct": round(share * 100, 1),
            "orders": acc_orders,
            "revenue": acc_revenue,
            "cost_per_order": cpo,
            "roas": roas,
            "open_debt": debt_by_acct.get(a["id"], 0.0),
            "credit_limit": a.get("credit_limit"),
            "alert_threshold_pct": a.get("alert_threshold_pct"),
        })

    return {
        "accounts": rows,
        "month_start": month_start_str,
        "today": today_str,
        "totals": {
            "spend": total_spend_sar,
            "spend_sar": total_spend_sar,
            "spend_raw_usd": round(
                sum(c["spend_raw"] for c in converted
                    if c["currency"] == "USD"), 2),
            "orders": total_orders,
            "revenue": total_revenue,
            "fx_rate_used": usd_to_sar,
            "bank_commission_pct_used": default_bank_pct,
        },
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

    # Iter-160 SSOT: spend now comes from ad_account_ledger (not from
    # meta_ads_daily). Other Meta metrics (purchases, impressions, clicks,
    # purchase_value) still come from meta_ads_daily as those are
    # non-accounting performance metrics.
    spend_by_date = await _spend_by_date_from_ledger(
        db, uid, ("meta", "facebook", "instagram"), d30_start_str, today_str,
    )

    def _agg(start: str, end: str):
        bucket = {"spend": 0.0, "purchases": 0, "purchase_value": 0.0,
                  "impressions": 0, "clicks": 0}
        for r in rows:
            if start <= r["date"] <= end:
                # NOTE: spend is intentionally NOT taken from r["spend"]
                # anymore. We aggregate spend from the ledger below.
                bucket["purchases"] += int(r.get("purchases") or 0)
                bucket["purchase_value"] += float(r.get("purchase_value") or 0)
                bucket["impressions"] += int(r.get("impressions") or 0)
                bucket["clicks"] += int(r.get("clicks") or 0)
        # Aggregate spend from ledger for this window
        for d, s in spend_by_date.items():
            if start <= d <= end:
                bucket["spend"] += s
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
            "cost_per_order": (
                round(spend / purchases, 2)
                if spend > 0 and purchases > 0 else None
            ),
            # CPC = spend / clicks
            "cpc": round(spend / clicks, 2) if clicks > 0 else 0.0,
            # CPM = (spend / impressions) * 1000
            "cpm": round((spend / impressions) * 1000, 2) if impressions > 0 else 0.0,
            # CTR = (clicks / impressions) * 100
            "ctr": round((clicks / impressions) * 100, 2) if impressions > 0 else 0.0,
        }

    # 30-day spend history for sparkline — STRICTLY from ledger now.
    by_date_spend = spend_by_date
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
                b["cost_per_order"] = (
                    round(b["spend"] / attr_orders, 2)
                    if b["spend"] > 0 and attr_orders > 0 else None
                )
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

    # Iter-160 SSOT: TikTok spend now comes STRICTLY from
    # ad_account_ledger (Message #737 directive). tiktok_ads_daily is
    # retained ONLY for the non-accounting metrics (purchases, revenue).
    # Note: TikTok counterparties may have ad_provider="tiktok".
    tt_rows = await db.tiktok_ads_daily.find(
        {"user_id": uid, "date": {"$gte": d30_start_str, "$lte": today_str}},
        {"_id": 0},
    ).to_list(500)
    tt_by_date: dict = {}
    for r in tt_rows:
        d = r.get("date")
        if not d:
            continue
        agg = tt_by_date.setdefault(d, {"purchases": 0, "revenue": 0.0})
        agg["purchases"] += int(r.get("purchases") or 0)
        agg["revenue"] += float(r.get("revenue") or 0)

    # Spend by date — STRICTLY from ledger.
    spend_by_date = await _spend_by_date_from_ledger(
        db, uid, ("tiktok",), d30_start_str, today_str,
    )

    def _row_spend(date_key: str) -> float:
        return float(spend_by_date.get(date_key) or 0)

    def _agg(start: str, end: str):
        date_keys = {k for k in tt_by_date if start <= k <= end} \
                    | {k for k in spend_by_date if start <= k <= end}
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
        cost_per_order = (
            round(spend / purchases, 2)
            if spend > 0 and purchases > 0 else None
        )
        return {"spend": round(spend, 2), "orders": int(purchases),
                "revenue": round(revenue, 2), "roas": roas, "cpa": cpa,
                "cost_per_order": cost_per_order}

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
                b["cost_per_order"] = (
                    round(b["spend"] / attr_orders, 2)
                    if b["spend"] > 0 and attr_orders > 0 else None
                )
        return b

    return {
        "today": {"date": today_str, **(await _agg_with_fallback(today_str, today_str))},
        "month": {"start": month_start_str, **(await _agg_with_fallback(month_start_str, today_str))},
        "last_30d": {"start": d30_start_str, **(await _agg_with_fallback(d30_start_str, today_str))},
        "history": history,
        "last_fetched_at": last_fetched_at,
        "source": "make_webhook",
        "has_data": len(tt_by_date) > 0 or any(v > 0 for v in spend_by_date.values()),
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
api.include_router(make_financial_provider_apps_router(db, current_user))
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
    await ensure_financial_provider_app_indexes(db)
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
    # Production stability: long-running provider/AI analysis must not run
    # inside the FastAPI web process. Interactive API availability wins.
    app.state.campaign_ai_monitor_task = None
    logger.warning(
        "production stability: campaign AI background worker disabled in web process"
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
    # Production stability: AI learning is deferred to a separate worker.
    # Inbound channel/webhook ingestion remains enabled.
    app.state.customer_learning_task = None
    logger.warning(
        "production stability: customer AI learning worker disabled in web process"
    )
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

    logger.warning(
        "production stability: ad-account background sync disabled in web process"
    )

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

    logger.warning(
        "production stability: ad-spend catch-up worker disabled in web process"
    )

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

    logger.warning(
        "production stability: BNPL hourly scheduler disabled in web process"
    )

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

    logger.warning(
        "production stability: Tamara daily sweep disabled in web process"
    )

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

    logger.warning(
        "production stability: Tamara startup sweep disabled in web process"
    )

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
