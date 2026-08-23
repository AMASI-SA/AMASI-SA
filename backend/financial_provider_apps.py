"""Mezan 2 financial-provider applications.

This module exposes one safe, tenant-scoped catalogue for payment providers
and shipping companies.  It deliberately keeps three concepts separate:

* provider account: Salla, Tamara, Tabby, Emkan, or one shipping company;
* fee rules: merchant configuration used only for estimates;
* provider tax invoice: authoritative evidence attached to the provider.

No legacy balance, legacy sale, or opening balance is accepted here.  Tax
invoices are evidence-only in this phase and never post to ``general_ledger``
by themselves.  The appropriate settlement/payment workflow owns accounting
posting so a document cannot create a duplicate journal entry.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo.errors import DuplicateKeyError

from auth import DEFAULT_PAYMENT_METHODS, DEFAULT_SHIPPING_COMPANIES, ensure_user_settings
from payment_methods import normalize_payment_method
from shipping_companies import normalize_shipping_company


OPERATION_ID = "MZ2-FIN-CUTOVER-001"
PAYMENT_PROVIDERS = frozenset({"salla", "tamara", "tabby", "emkan"})
PAYMENT_PROVIDER_LABELS = {
    "salla": "سلة وطرق الدفع",
    "tamara": "تمارا",
    "tabby": "تابي",
    "emkan": "إمكان",
}
PAYMENT_PROVIDER_KINDS = {
    "salla": "payment_gateway",
    "tamara": "bnpl",
    "tabby": "bnpl",
    "emkan": "bnpl",
}

PAYMENT_PROVIDER_FEE_POLICIES = {
    "tamara": {
        "evidence_version": "tamara-statements-2026-08-v1",
        "capture_basis": "per_captured_order",
        "fee_rounding": "round_fee_then_vat_per_event",
        "refund_treatment": "no_commission_rebate",
        "cancellation_treatment": "fixed_fee_only_plus_vat",
        "settlement_fee_per_statement": 0.0,
        "period_start_weekday": "saturday",
        "period_end_weekday": "friday",
        "statement_issue_weekday": "saturday",
        "cutoff_time_verified": False,
        "summary_ar": (
            "البيع: 6.99% + 1.50 ر.س؛ الاسترداد لا يعكس العمولة؛ "
            "الإلغاء: 1.50 ر.س + ضريبتها؛ الفترة من السبت إلى الجمعة "
            "ويصدر الكشف السبت. ساعة القطع غير ظاهرة في الملفات."
        ),
    },
}


class ProviderTaxInvoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_number: str = Field(min_length=1, max_length=120)
    issue_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    service_period_start: Optional[str] = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    service_period_end: Optional[str] = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    net_amount: float = Field(ge=0)
    vat_amount: float = Field(ge=0)
    total_amount: float = Field(gt=0)
    currency: Literal["SAR"] = "SAR"
    verification_status: Literal["draft", "verified"] = "draft"
    evidence_ref: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _validate_document(self):
        net = round(float(self.net_amount or 0), 2)
        vat = round(float(self.vat_amount or 0), 2)
        total = round(float(self.total_amount or 0), 2)
        if abs((net + vat) - total) > 0.01:
            raise ValueError("total_amount must equal net_amount + vat_amount")
        start = self.service_period_start
        end = self.service_period_end
        if start and end and start > end:
            raise ValueError("service_period_start must not be after service_period_end")
        if self.verification_status == "verified" and not (self.evidence_ref or "").strip():
            raise ValueError("evidence_ref is required for a verified invoice")
        return self


def _payment_fee_lines(settings: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {key: [] for key in PAYMENT_PROVIDERS}
    rows = settings.get("payment_methods") or DEFAULT_PAYMENT_METHODS
    for row in rows:
        sub_key, display, parent = normalize_payment_method(row.get("name") or "")
        provider = parent or sub_key
        if provider not in grouped:
            continue
        grouped[provider].append({
            "code": sub_key,
            "name": display or row.get("name") or sub_key,
            "commission_percent": round(float(row.get("commission_percent") or 0), 4),
            "fixed_fee": round(float(row.get("fixed_fee") or 0), 2),
            "vat_percent": round(float(row.get("vat_percent") or 0), 4),
            "source": "settings.payment_methods",
            "authority": "estimate_until_provider_invoice_or_settlement",
        })
    return grouped


def provider_operation_route(provider_id: str) -> dict:
    """Return the canonical posting workflow for a provider.

    A settlement is not a generic internal transfer.  Keeping these routes
    explicit prevents money held by a provider from being treated as cash.
    """
    if provider_id == "payment:salla":
        return {
            "kind": "provider_settlement",
            "href": "/salla-settlements",
            "label": "رفع فاتورة/تسوية سلة",
        }
    if provider_id in {"payment:tamara", "payment:tabby", "payment:emkan"}:
        return {
            "kind": "provider_settlement",
            "href": "/bnpl-settlements/register",
            "label": "تسجيل تسوية المزود",
        }
    if provider_id.startswith("shipping:"):
        return {
            "kind": "courier_cod_settlement",
            "href": "/new-transaction?operation=courier_cod_settle",
            "label": "تسجيل تسوية شركة الشحن",
        }
    return {
        "kind": "unsupported",
        "href": "/new-transaction",
        "label": "فتح الحركة المالية الموحّدة",
    }


def build_provider_catalog(
    settings: dict,
    *,
    invoice_summary: Optional[dict[str, dict]] = None,
) -> list[dict]:
    """Build the provider cards without reading financial legacy facts."""
    summaries = invoice_summary or {}
    fee_lines = _payment_fee_lines(settings)
    apps: list[dict] = []

    for provider in ("salla", "tamara", "tabby", "emkan"):
        provider_id = f"payment:{provider}"
        summary = summaries.get(provider_id) or {}
        apps.append({
            "provider_id": provider_id,
            "provider_code": provider,
            "display_name": PAYMENT_PROVIDER_LABELS[provider],
            "kind": PAYMENT_PROVIDER_KINDS[provider],
            "currency": "SAR",
            "configured": bool(fee_lines[provider]),
            "fee_rules": fee_lines[provider],
            "fee_policy": PAYMENT_PROVIDER_FEE_POLICIES.get(provider),
            "fee_rule_authority": "estimated",
            "actual_source_priority": [
                "provider_tax_invoice",
                "provider_settlement_statement",
                "merchant_fee_settings",
            ],
            "tax_invoice_count": int(summary.get("count") or 0),
            "tax_invoice_total": round(float(summary.get("total") or 0), 2),
            "latest_tax_invoice": summary.get("latest"),
            "operation": provider_operation_route(provider_id),
            "opening_balance_policy": "trusted_cutover_only",
            "legacy_financial_data_included": False,
        })

    shipping_rows = settings.get("shipping_companies") or DEFAULT_SHIPPING_COMPANIES
    seen_shipping: set[str] = set()
    for row in shipping_rows:
        key, display = normalize_shipping_company(row.get("name"))
        if key == "unknown" or key in seen_shipping:
            continue
        seen_shipping.add(key)
        provider_id = f"shipping:{key}"
        summary = summaries.get(provider_id) or {}
        raw_cod_fee_percent = float(row.get("cod_fee_percent") or 0)
        cod_fee_percent = (
            raw_cod_fee_percent * 100
            if raw_cod_fee_percent <= 1
            else raw_cod_fee_percent
        )
        cod_fee_tiers = []
        for index, tier in enumerate(row.get("cod_fee_tiers") or []):
            tier_percent = float(tier.get("commission_percent") or 0)
            if tier_percent <= 1:
                tier_percent *= 100
            cod_fee_tiers.append({
                "code": f"cod_tier_{index + 1}",
                "name": "شريحة عمولة التحصيل عند الاستلام",
                "calculation_basis": "per_delivered_cod_shipment_amount",
                "min_amount": round(float(tier.get("min_amount") or 0), 2),
                "max_amount": (
                    round(float(tier["max_amount"]), 2)
                    if tier.get("max_amount") is not None else None
                ),
                "min_inclusive": tier.get("min_inclusive", True) is not False,
                "max_inclusive": tier.get("max_inclusive", True) is not False,
                "commission_percent": round(tier_percent, 4),
                "fixed_fee": round(float(tier.get("fixed_fee") or 0), 2),
                "vat_percent": round(float(tier.get("vat_percent") or 0), 4),
                "source": "settings.shipping_companies.cod_fee_tiers",
                "authority": "estimate_until_provider_invoice_or_statement",
            })
        fee_rules = cod_fee_tiers or [{
            "code": "shipping_and_cod",
            "name": "تكلفة الشحن ورسوم التحصيل",
            "calculation_basis": "per_delivered_cod_shipment_amount",
            "cost_per_order": round(float(row.get("cost_per_order") or 0), 2),
            "cod_fee_percent": round(cod_fee_percent, 4),
            "cod_fee_fixed_per_order": round(
                float(row.get("cod_fee_fixed_per_order") or 0), 2,
            ),
            "vat_percent": round(float(
                row.get("cod_fee_vat_percent")
                if row.get("cod_fee_vat_percent") is not None
                else (row.get("vat_percent") or 0)
            ), 4),
            "source": "settings.shipping_companies",
            "authority": "estimate_until_provider_invoice_or_statement",
        }]
        apps.append({
            "provider_id": provider_id,
            "provider_code": key,
            "display_name": display,
            "kind": "shipping_company",
            "currency": "SAR",
            "configured": True,
            "payment_mode": "deferred" if row.get("is_deferred") else "prepaid",
            "fee_rules": fee_rules,
            "cod_fee_rule_mode": "tiered" if cod_fee_tiers else "flat",
            "settlement_netting_supported": True,
            "bank_transfer_optional": True,
            "fee_rule_authority": "estimated",
            "actual_source_priority": [
                "provider_tax_invoice",
                "provider_statement",
                "merchant_fee_settings",
            ],
            "tax_invoice_count": int(summary.get("count") or 0),
            "tax_invoice_total": round(float(summary.get("total") or 0), 2),
            "latest_tax_invoice": summary.get("latest"),
            "operation": provider_operation_route(provider_id),
            "opening_balance_policy": "trusted_cutover_only",
            "legacy_financial_data_included": False,
        })
    return apps


async def ensure_financial_provider_app_indexes(db) -> None:
    await db.financial_provider_tax_invoices_v2.create_index(
        [("user_id", 1), ("provider_id", 1), ("invoice_number", 1)],
        name="uniq_provider_tax_invoice_v2",
        unique=True,
    )
    await db.financial_provider_tax_invoices_v2.create_index(
        [("user_id", 1), ("provider_id", 1), ("issue_date", -1)],
        name="provider_tax_invoice_timeline_v2",
    )


async def _invoice_summaries(db, user_id: str) -> dict[str, dict]:
    summaries: dict[str, dict] = {}
    async for doc in db.financial_provider_tax_invoices_v2.aggregate([
        {"$match": {"user_id": user_id, "status": {"$ne": "void"}}},
        {"$sort": {"issue_date": -1, "created_at": -1}},
        {"$group": {
            "_id": "$provider_id",
            "count": {"$sum": 1},
            "total": {"$sum": "$total_amount"},
            "latest": {"$first": {
                "id": "$id",
                "invoice_number": "$invoice_number",
                "issue_date": "$issue_date",
                "total_amount": "$total_amount",
                "verification_status": "$verification_status",
                "accounting_status": "$accounting_status",
            }},
        }},
    ]):
        summaries[str(doc.get("_id"))] = {
            "count": int(doc.get("count") or 0),
            "total": round(float(doc.get("total") or 0), 2),
            "latest": doc.get("latest"),
        }
    return summaries


def make_financial_provider_apps_router(db, current_user) -> APIRouter:
    router = APIRouter(prefix="/financial-provider-apps", tags=["financial-provider-apps"])

    def _require_owner(user: dict) -> None:
        if str(user.get("role") or "").lower() != "owner":
            raise HTTPException(status_code=403, detail="هذه العملية متاحة للمالك فقط")

    async def _known_provider(user_id: str, provider_id: str) -> dict:
        settings = await ensure_user_settings(db, user_id)
        catalog = build_provider_catalog(settings)
        provider = next((row for row in catalog if row["provider_id"] == provider_id), None)
        if not provider:
            raise HTTPException(status_code=404, detail="حساب المزود غير موجود")
        return provider

    @router.get("")
    async def list_provider_apps(user: dict = Depends(current_user)):
        settings = await ensure_user_settings(db, user["id"])
        summaries = await _invoice_summaries(db, user["id"])
        apps = build_provider_catalog(settings, invoice_summary=summaries)
        return {
            "operation_id": OPERATION_ID,
            "apps": apps,
            "summary": {
                "providers": len(apps),
                "payment_providers": sum(
                    1 for app in apps if app["kind"] in {"payment_gateway", "bnpl"}
                ),
                "shipping_companies": sum(
                    1 for app in apps if app["kind"] == "shipping_company"
                ),
                "tax_invoices": sum(app["tax_invoice_count"] for app in apps),
                "verified_tax_invoices": await db.financial_provider_tax_invoices_v2.count_documents({
                    "user_id": user["id"],
                    "verification_status": "verified",
                    "status": {"$ne": "void"},
                }),
            },
            "accounting_policy": {
                "generic_internal_transfer": "bank_cash_and_salla_wallet_only_when_eligible",
                "provider_to_bank": "provider_settlement_not_generic_transfer",
                "shipping_cod": "courier_cod_settlement",
                "tax_invoice": "evidence_only_until_posted_by_canonical_workflow",
                "legacy_financial_data_included": False,
            },
        }

    @router.get("/{provider_id}/tax-invoices")
    async def list_tax_invoices(provider_id: str, user: dict = Depends(current_user)):
        await _known_provider(user["id"], provider_id)
        items = await db.financial_provider_tax_invoices_v2.find(
            {"user_id": user["id"], "provider_id": provider_id},
            {"_id": 0, "user_id": 0},
        ).sort([("issue_date", -1), ("created_at", -1)]).to_list(500)
        return {"operation_id": OPERATION_ID, "items": items}

    @router.post("/{provider_id}/tax-invoices", status_code=201)
    async def add_tax_invoice(
        provider_id: str,
        payload: ProviderTaxInvoiceIn,
        user: dict = Depends(current_user),
    ):
        _require_owner(user)
        provider = await _known_provider(user["id"], provider_id)
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "provider_id": provider_id,
            "provider_name": provider["display_name"],
            **payload.model_dump(),
            "status": "active",
            "accounting_status": "unposted_evidence",
            "source": "provider_tax_invoice_direct",
            "operation_id": OPERATION_ID,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await db.financial_provider_tax_invoices_v2.insert_one(doc)
        except DuplicateKeyError as exc:
            raise HTTPException(
                status_code=409,
                detail="رقم الفاتورة مسجل مسبقاً لهذا المزود",
            ) from exc
        doc.pop("_id", None)
        doc.pop("user_id", None)
        return doc

    return router
