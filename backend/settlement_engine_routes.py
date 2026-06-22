"""Iter-251 · Phase 2A — Settlement Dry-Run Engine.

READ-ONLY analysis layer that introspects existing order/settlement
data and reports what the future Settlement Engine WOULD generate
for each payment provider (Tamara, Tabby, Emkan, Salla).

Hard constraints (per user mandate):
  • NO writes anywhere (no GL, no balances, no webhooks).
  • NO new collections.
  • NO modification of historical records.
  • Feature flags default to False — actual generation is gated.

Endpoints
---------
GET /api/settlement-engine/dry-run
    Aggregates per-provider stats: orders count, expected invoices,
    settlement entries, expected pending reviews, GL drift, target
    bank, date range.

GET   /api/settlement-engine/feature-flags
PATCH /api/settlement-engine/feature-flags
    Read/toggle the 3 phase-2 feature flags (admin-only writes).

Provider detection
------------------
For now we recognise orders via `payment_method` substring match.
This is intentionally loose so the dry-run captures everything the
Settlement Engine will later see; the engine itself will use stricter
provider-specific gates.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from settlement_engine_generation import (
    generate_for_provider as _generate_for_provider,
    cancel_invoice as _cancel_invoice,
    INVOICE_STATUSES as _INVOICE_STATUSES,
    PERIOD_STATUSES as _PERIOD_STATUSES,
    EXPECTED_TRANSFER_STATUSES as _XFER_STATUSES,
)


# Provider → list of payment_method substrings that classify an order.
PROVIDER_MATCHERS: dict[str, list[str]] = {
    "tamara":  ["تمارا", "tamara"],
    "tabby":   ["تابي", "tabby"],
    "imkan":   ["إمكان", "امكان", "imkan", "emkan"],
    "salla":   [
        # Salla gateway payment-methods (everything routed through Salla
        # checkout that ends up in Salla's wallet for later transfer).
        "مدى", "mada", "apple pay", "applepay",
        "البطاقة الإئتمانية", "بطاقة ائتمانية",
        "credit", "stc pay", "stcpay",
    ],
}

# Default settlement cycle (ISO weekday → end of cycle).
# 1 = Mon ... 7 = Sun. Convention: Tamara/Tabby/Emkan close weekly
# on Sunday (ISO weekday 7); Salla relies on actual settlement files.
PROVIDER_CYCLES: dict[str, dict] = {
    "tamara": {"period": "weekly", "anchor_weekday": 7,
                "commission_rate": 0.06,
                "vat_rate_on_commission": 0.15},
    "tabby":  {"period": "weekly", "anchor_weekday": 7,
                "commission_rate": 0.06,
                "vat_rate_on_commission": 0.15},
    "imkan":  {"period": "weekly", "anchor_weekday": 7,
                "commission_rate": 0.05,
                "vat_rate_on_commission": 0.15},
    "salla":  {"period": "settlement_entries"},
}

PROVIDER_AR = {
    "tamara": "تمارا", "tabby": "تابي",
    "imkan":  "إمكان", "salla":  "سلة",
}


async def _resolve_provider_rules(db, uid: str, provider: str) -> dict:
    """Single source of truth for provider settlement rules.

    Reads from the SAME pipeline used by `/bnpl-settlements/register`
    via `bnpl.settlements_service._merchant_fee_rates`. Falls back to
    sensible defaults only when the provider isn't BNPL.
    """
    src = "estimated_default"
    rate = {"commission_pct": 0.0, "vat_pct": 0.0,
            "fixed_fee_per_order": 0.0}
    if provider in ("tamara", "tabby"):
        try:
            from bnpl.settlements_service import _merchant_fee_rates
            r = await _merchant_fee_rates(db, uid, provider) or {}
            rate["commission_pct"] = float(r.get("commission_pct") or 0)
            rate["vat_pct"]        = float(r.get("vat_pct") or 0)
            src = r.get("fee_source") or "bnpl_settlements_service"
        except Exception:
            pass
    # Cycle metadata still comes from PROVIDER_CYCLES for now (Phase 2B
    # will move it to a dedicated `provider_settlement_rules`
    # collection that the Settings page edits).
    cycle = PROVIDER_CYCLES.get(provider, {})
    return {
        "provider":            provider,
        "commission_rate":     rate["commission_pct"] / 100.0,
        "vat_rate_on_commission": rate["vat_pct"] / 100.0,
        "fixed_fee_per_order": rate.get("fixed_fee_per_order", 0.0),
        "period":              cycle.get("period", "weekly"),
        "anchor_weekday":      cycle.get("anchor_weekday", 7),
        "fee_source":          src,
    }


# Iter-251 · Phase 2A — Page/endpoint dependency registry.
# Static map of every UI page and backend endpoint that depends on
# provider settlement rules / formulas, so refactors stay coordinated.
PROVIDER_DEPENDENCIES = [
    {"page": "/bnpl-settlements/register",
     "endpoint": "/api/bnpl/settlements/*",
     "uses": ["commission_pct", "vat_pct"],
     "role": "primary — actual settlement creation + GL post"},
    {"page": "/settlement-engine",
     "endpoint": "/api/settlement-engine/dry-run-details",
     "uses": ["commission_pct", "vat_pct", "cycle"],
     "role": "simulation — read-only"},
    {"page": "/bank-transfer-review",
     "endpoint": "/api/bank-transfer-review",
     "uses": ["default_bank_for_<provider>"],
     "role": "review queue for incoming transfers"},
    {"page": "/dashboard",
     "endpoint": "/api/dashboard",
     "uses": ["commission_pct", "vat_pct"],
     "role": "exec profit summary (BNPL deductions)"},
    {"page": "/tamara",
     "endpoint": "/api/tamara/forensic & /apply",
     "uses": ["_merchant_fee_rates(tamara)"],
     "role": "provider page — Tamara health & repair"},
    {"page": "/tabby",
     "endpoint": "/api/tabby/*",
     "uses": ["_merchant_fee_rates(tabby)"],
     "role": "provider page — Tabby health"},
    {"page": "/financial-position",
     "endpoint": "/api/financial-position",
     "uses": ["GL balances (post-settlement)"],
     "role": "consolidated financial position report"},
]


class FeatureFlagsIn(BaseModel):
    settlement_engine_enabled:           Optional[bool] = None
    platform_settlement_to_review_enabled: Optional[bool] = None
    bank_transfer_review_enabled:         Optional[bool] = None


# ─── Iter-251 · Phase 2B — Generation DTOs ────────────────────────
class GenerateIn(BaseModel):
    provider:  str
    date_from: Optional[str] = None
    date_to:   Optional[str] = None
    dry_run:   bool = False


class CancelInvoiceIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_settlement_engine_router(db, current_user):
    router = APIRouter(prefix="/settlement-engine",
                       tags=["settlement-engine"])

    async def _resolve_default_bank(
        uid: str, provider: str,
    ) -> tuple[Optional[str], Optional[str]]:
        s = await db.settings.find_one(
            {"user_id": uid},
            {"_id": 0, f"default_bank_for_{provider}": 1},
        ) or {}
        bid = s.get(f"default_bank_for_{provider}")
        if not bid:
            return None, None
        acc = await db.accounts.find_one(
            {"user_id": uid, "id": bid},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not acc:
            return None, None
        return acc["id"], acc.get("name")

    async def _get_flags(uid: str) -> dict:
        s = await db.settings.find_one(
            {"user_id": uid}, {"_id": 0}) or {}
        return {
            "settlement_engine_enabled":
                bool(s.get("settlement_engine_enabled", False)),
            "platform_settlement_to_review_enabled":
                bool(s.get("platform_settlement_to_review_enabled", False)),
            "bank_transfer_review_enabled":
                bool(s.get("bank_transfer_review_enabled", False)),
        }

    # ─────────── Feature Flags ───────────
    @router.get("/feature-flags")
    async def get_feature_flags(user: dict = Depends(current_user)):
        return await _get_flags(user["id"])

    @router.patch("/feature-flags")
    async def patch_feature_flags(
        payload: FeatureFlagsIn,
        user: dict = Depends(current_user),
    ):
        upd: dict = {}
        for k in ("settlement_engine_enabled",
                  "platform_settlement_to_review_enabled",
                  "bank_transfer_review_enabled"):
            v = getattr(payload, k, None)
            if v is not None:
                upd[k] = bool(v)
        if not upd:
            raise HTTPException(400, "لم يتم تمرير أي تعديل")
        upd["updated_at"] = _now()
        await db.settings.update_one(
            {"user_id": user["id"]}, {"$set": upd}, upsert=True,
        )
        return await _get_flags(user["id"])

    # ─────────── Dry-Run Report ───────────
    @router.get("/dry-run")
    async def dry_run(user: dict = Depends(current_user)):
        uid = user["id"]
        flags = await _get_flags(uid)

        per_provider = []
        grand_orders, grand_settle, grand_pending, grand_gl = 0, 0, 0, 0
        grand_amount = 0.0

        for provider, matchers in PROVIDER_MATCHERS.items():
            # 1) Orders that belong to this provider via payment_method.
            or_clauses = [
                {"payment_method": {"$regex": m, "$options": "i"}}
                for m in matchers
            ]
            order_q = {"user_id": uid, "$or": or_clauses}

            order_stats = await db.unified_orders.aggregate([
                {"$match": order_q},
                {"$group": {
                    "_id": None,
                    "count": {"$sum": 1},
                    "total": {"$sum": {"$ifNull": ["$total", 0]}},
                    "min_date": {"$min": "$created_at"},
                    "max_date": {"$max": "$created_at"},
                }},
            ]).to_list(1)
            os_row = order_stats[0] if order_stats else {}
            orders_count = int(os_row.get("count") or 0)
            orders_total = float(os_row.get("total") or 0)

            # 2) Existing settlement_entries already in DB for this
            #    provider (these would NOT need a re-generation).
            sett_stats = await db.settlement_entries.aggregate([
                {"$match": {"user_id": uid, "provider": provider}},
                {"$group": {
                    "_id": None,
                    "count": {"$sum": 1},
                    "net":   {"$sum": {"$ifNull": ["$actual_net_amount", 0]}},
                    "matched":   {"$sum": {"$cond": ["$matched", 1, 0]}},
                    "unmatched": {"$sum": {"$cond": ["$matched", 0, 1]}},
                }},
            ]).to_list(1)
            ss = sett_stats[0] if sett_stats else {}
            sett_count = int(ss.get("count") or 0)
            sett_net   = float(ss.get("net") or 0)
            sett_matched   = int(ss.get("matched") or 0)
            sett_unmatched = int(ss.get("unmatched") or 0)

            # 3) Already-created bank_transfer_reviews for this provider.
            existing_review_count = (
                await db.bank_transfer_reviews.count_documents(
                    {"user_id": uid, "source_type": provider})
            )
            existing_review_pending = (
                await db.bank_transfer_reviews.count_documents(
                    {"user_id": uid, "source_type": provider,
                     "status": "pending"})
            )
            existing_review_missing = (
                await db.bank_transfer_reviews.count_documents(
                    {"user_id": uid, "source_type": provider,
                     "status": "missing_target_bank"})
            )

            # 4) Default bank routing.
            bid, bname = await _resolve_default_bank(uid, provider)

            # 5) Future-projection:
            #    • expected_invoices       ≈ orders_count
            #    • expected_settlements    ≈ sett_count  (1:1 with rows)
            #    • expected_pending_review ≈ sett_count - already_in_review
            #    • would_have_hit_gl       = orders that, with legacy flow,
            #      already produced a GL entry (we approximate via
            #      financial_movements / general_ledger matches).
            #    These are HEURISTICS — pure analysis, no DB writes.
            expected_invoices    = orders_count
            expected_settlements = sett_count
            expected_pending_new = max(
                sett_count - existing_review_count, 0)

            # 6) Count GL legs that look like a platform→bank movement
            #    for this provider via the metadata tags.
            gl_legs = await db.general_ledger.count_documents({
                "user_id": uid,
                "$or": [
                    {"metadata.provider":       provider},
                    {"metadata.platform":      provider},
                    {"metadata.source_type":   provider},
                ],
            })

            per_provider.append({
                "provider":           provider,
                "provider_ar":        PROVIDER_AR[provider],
                "orders": {
                    "count":  orders_count,
                    "total":  round(orders_total, 2),
                    "from":   os_row.get("min_date"),
                    "to":     os_row.get("max_date"),
                },
                "settlement_entries": {
                    "count":         sett_count,
                    "net_total":     round(sett_net, 2),
                    "matched":       sett_matched,
                    "unmatched":     sett_unmatched,
                },
                "existing_reviews": {
                    "total":               existing_review_count,
                    "pending":             existing_review_pending,
                    "missing_target_bank": existing_review_missing,
                },
                "expected": {
                    "invoices":     expected_invoices,
                    "settlements":  expected_settlements,
                    "new_pending_reviews": expected_pending_new,
                },
                "current_gl_legs":   gl_legs,
                "default_bank": {
                    "configured": bool(bid),
                    "id":         bid,
                    "name":       bname,
                },
                "readiness": {
                    "has_default_bank":    bool(bid),
                    "has_settlement_data": sett_count > 0,
                    "has_orders":          orders_count > 0,
                },
            })

            grand_orders  += orders_count
            grand_settle  += sett_count
            grand_pending += expected_pending_new
            grand_gl      += gl_legs
            grand_amount  += sett_net

        # Aggregate review state (across providers).
        review_totals = {}
        async for row in db.bank_transfer_reviews.aggregate([
            {"$match": {"user_id": uid}},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]):
            review_totals[row["_id"]] = row["count"]

        return {
            "generated_at": _now(),
            "user_id":      uid,
            "feature_flags": flags,
            "summary": {
                "providers":              len(PROVIDER_MATCHERS),
                "total_orders":           grand_orders,
                "total_settlement_rows":  grand_settle,
                "total_settlement_net":   round(grand_amount, 2),
                "expected_new_pending_reviews": grand_pending,
                "existing_gl_legs_legacy":      grand_gl,
                "review_totals":          review_totals,
            },
            "per_provider": per_provider,
            "notes": [
                "هذا التقرير قراءة فقط — لا كتابة في قاعدة البيانات.",
                "الـ Webhooks الحالية لم تُمَس.",
                "حقل `current_gl_legs` يقدّر القيود الحالية المرتبطة "
                "بكل مزود عبر metadata.provider / platform / source_type.",
                "حقل `expected.new_pending_reviews` = settlement_entries - "
                "existing_reviews (لا يقل عن صفر).",
                "حتى تفعيل feature flag «settlement_engine_enabled» لن "
                "يحدث أي توليد فعلي.",
            ],
        }

    # ─────────── Provider rules (central SSOT lookup) ───────────
    # Surfaces what `_resolve_provider_rules` returns for each provider
    # so the merchant can verify Settlement Engine uses the SAME
    # numbers as the BNPL Settlement page.
    @router.get("/rules")
    async def get_rules(user: dict = Depends(current_user)):
        uid = user["id"]
        out = {}
        for prov in PROVIDER_MATCHERS:
            out[prov] = await _resolve_provider_rules(db, uid, prov)
        return {
            "rules": out,
            "notes": [
                "تمارا/تابي يقرآن من نفس مصدر صفحة "
                "/bnpl-settlements/register عبر _merchant_fee_rates.",
                "إمكان/سلة يستخدمان defaults حتى نضيف إعداداتهما لاحقاً.",
                "أي تعديل في commission_pct/vat_pct من صفحة الإعدادات "
                "ينعكس تلقائياً هنا وفي Dry-Run والتقارير الأخرى.",
            ],
        }

    # ─────────── Dependencies registry (page/endpoint map) ───────────
    @router.get("/dependencies")
    async def get_dependencies(_: dict = Depends(current_user)):
        return {
            "dependencies": PROVIDER_DEPENDENCIES,
            "explanation":  (
                "خريطة الصفحات والـ endpoints التي تعتمد على معادلات "
                "تسوية المزودين. أي تعديل في مصدر القاعدة يجب اختباره "
                "ضد كل صفحة هنا."
            ),
        }

    # ─────────── Dry-Run Details (per-invoice simulation) ───────────
    # Read-only simulation that groups orders into virtual invoices
    # following each provider's settlement cycle.  No DB writes.
    @router.get("/dry-run-details")
    async def dry_run_details(
        provider: Optional[str] = None,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        out: dict[str, dict] = {}
        targets = (
            [provider] if provider in PROVIDER_MATCHERS
            else list(PROVIDER_MATCHERS.keys())
        )
        for prov in targets:
            cycle = PROVIDER_CYCLES.get(prov, {})
            if cycle.get("period") == "settlement_entries":
                out[prov] = await _simulate_from_settlements(uid, prov)
            else:
                out[prov] = await _simulate_weekly(uid, prov, cycle)
        return {
            "generated_at": _now(),
            "providers":    out,
            "notes": [
                "هذي محاكاة بحتة — لا فواتير حقيقية تُنشأ.",
                "أرقام العمولة و VAT اقتراحات افتراضية. اضبطها من "
                "إعدادات المزود لاحقاً.",
                "السلَّة تستخدم settlement_entries الفعلية كأساس "
                "(لا تخمين).",
            ],
        }

    # Helpers — pulled outside the router for testability.
    async def _simulate_weekly(uid, prov, cycle):
        # Iter-251 · Phase 2A v2 — Rule resolution is delegated to the
        # central `_resolve_provider_rules` helper, which reads from
        # the same source as `/bnpl-settlements/register` for BNPL
        # providers. Hard-coded rates removed.
        rules = await _resolve_provider_rules(db, uid, prov)
        commission_rate = rules["commission_rate"]
        vat_rate        = rules["vat_rate_on_commission"]
        matchers = PROVIDER_MATCHERS[prov]
        or_clauses = [
            {"payment_method": {"$regex": m, "$options": "i"}}
            for m in matchers
        ]
        cursor = db.unified_orders.find(
            {"user_id": uid, "$or": or_clauses},
            {"_id": 0, "id": 1, "order_date": 1, "total_amount": 1,
             "status": 1, "refund_amount": 1},
        )
        # Bucket by ISO week.
        buckets: dict[str, dict] = {}
        async for o in cursor:
            d_str = o.get("order_date") or ""
            try:
                d = datetime.strptime(d_str[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            # Snap to the Monday of the week.
            from datetime import timedelta
            monday = d - timedelta(days=d.weekday())
            sunday = monday + timedelta(days=6)
            key = monday.isoformat()
            b = buckets.setdefault(key, {
                "period_from": monday.isoformat(),
                "period_to":   sunday.isoformat(),
                "orders":      0,
                "gross":       0.0,
                "refunds":     0.0,
            })
            b["orders"]  += 1
            b["gross"]   += float(o.get("total_amount") or 0)
            b["refunds"] += float(o.get("refund_amount") or 0)

        invoices = []
        for idx, (_, b) in enumerate(
                sorted(buckets.items(), key=lambda x: x[0]), start=1):
            net_sales  = round(b["gross"] - b["refunds"], 2)
            commission = round(net_sales * commission_rate, 2)
            vat        = round(commission * vat_rate, 2)
            expected_transfer = round(
                net_sales - commission - vat, 2)
            invoices.append({
                "dry_invoice_id":   f"DRY-{prov.upper()}-{idx:03d}",
                "period_from":      b["period_from"],
                "period_to":        b["period_to"],
                "orders_count":     b["orders"],
                "gross_sales":      round(b["gross"], 2),
                "refunds":          round(b["refunds"], 2),
                "net_sales":        net_sales,
                "estimated_commission": commission,
                "estimated_vat":    vat,
                "expected_transfer": expected_transfer,
            })
        totals = {
            "invoices_count":      len(invoices),
            "orders_count":        sum(i["orders_count"]   for i in invoices),
            "gross_sales":         round(sum(i["gross_sales"]
                                              for i in invoices), 2),
            "refunds":             round(sum(i["refunds"]
                                              for i in invoices), 2),
            "net_sales":           round(sum(i["net_sales"]
                                              for i in invoices), 2),
            "estimated_commission":round(sum(i["estimated_commission"]
                                              for i in invoices), 2),
            "estimated_vat":       round(sum(i["estimated_vat"]
                                              for i in invoices), 2),
            "expected_transfer":   round(sum(i["expected_transfer"]
                                              for i in invoices), 2),
        }
        return {
            "provider":       prov,
            "provider_ar":    PROVIDER_AR[prov],
            "source":         "unified_orders → weekly buckets",
            "formula_source": (
                "BNPL Settlement Formula" if prov in ("tamara", "tabby")
                else "Estimated Formula"
            ),
            "cycle":          cycle,
            "rules":          rules,
            "totals":         totals,
            "invoices":       invoices,
        }

    async def _simulate_from_settlements(uid, prov):
        # Group existing settlement_entries by settlement_reference.
        cursor = db.settlement_entries.aggregate([
            {"$match": {"user_id": uid, "provider": prov}},
            {"$group": {
                "_id": "$settlement_reference",
                "orders_count": {"$sum": 1},
                "gross":        {"$sum": {"$ifNull":
                    ["$actual_gross_amount", 0]}},
                "refunds":      {"$sum": {"$ifNull":
                    ["$actual_refund_amount", 0]}},
                "fee":          {"$sum": {"$ifNull":
                    ["$actual_payment_fee", 0]}},
                "net":          {"$sum": {"$ifNull":
                    ["$actual_net_amount", 0]}},
                "min_date":     {"$min": "$settlement_date"},
                "max_date":     {"$max": "$settlement_date"},
            }},
            {"$sort": {"min_date": 1}},
        ])
        invoices = []
        idx = 0
        async for r in cursor:
            idx += 1
            invoices.append({
                "dry_invoice_id":     f"DRY-{prov.upper()}-{idx:03d}",
                "settlement_reference": r["_id"],
                "period_from":        r.get("min_date"),
                "period_to":          r.get("max_date"),
                "orders_count":       r["orders_count"],
                "gross_sales":        round(r.get("gross", 0)   or 0, 2),
                "refunds":            round(r.get("refunds", 0) or 0, 2),
                "estimated_commission": round(r.get("fee", 0)   or 0, 2),
                "estimated_vat":      0.0,
                "expected_transfer":  round(r.get("net", 0)     or 0, 2),
            })
        totals = {
            "invoices_count":      len(invoices),
            "orders_count":        sum(i["orders_count"]   for i in invoices),
            "gross_sales":         round(sum(i["gross_sales"]
                                              for i in invoices), 2),
            "refunds":             round(sum(i["refunds"]
                                              for i in invoices), 2),
            "estimated_commission":round(sum(i["estimated_commission"]
                                              for i in invoices), 2),
            "estimated_vat":       0.0,
            "expected_transfer":   round(sum(i["expected_transfer"]
                                              for i in invoices), 2),
        }
        return {
            "provider":       prov,
            "provider_ar":    PROVIDER_AR[prov],
            "source":         "settlement_entries (real data)",
            "formula_source": "Actual Settlement Formula",
            "cycle":          PROVIDER_CYCLES.get(prov, {}),
            "totals":         totals,
            "invoices":       invoices,
        }

    # ═════════════════════════════════════════════════════════════════
    # Iter-251 · Phase 2B — Generation endpoints (FLAG-GATED writes)
    # ═════════════════════════════════════════════════════════════════
    async def _require_flag(uid: str):
        """Block writes unless `settlement_engine_enabled` is True."""
        flags = await _get_flags(uid)
        if not flags.get("settlement_engine_enabled"):
            raise HTTPException(
                403,
                "محرّك التسويات معطّل. فعّل feature flag "
                "settlement_engine_enabled من نفس الصفحة قبل التوليد.",
            )

    @router.post("/generate")
    async def generate(
        payload: GenerateIn,
        user: dict = Depends(current_user),
    ):
        """Generate settlement_periods + settlement_invoices +
        expected_transfers for ``payload.provider`` over the requested
        window.

        Rules:
          • Writes are blocked unless ``settlement_engine_enabled``.
          • ``dry_run=True`` simulates the inserts without persisting.
          • Idempotent on (provider, period_from, period_to).
          • NO GL writes, NO bank_transfer_review creation here —
            those happen in later phases.
        """
        uid = user["id"]
        if not payload.dry_run:
            await _require_flag(uid)
        if payload.provider not in PROVIDER_MATCHERS:
            raise HTTPException(
                400, f"مزوّد غير معروف: {payload.provider}")
        try:
            result = await _generate_for_provider(
                db, uid, user, payload.provider,
                payload.date_from, payload.date_to,
                dry_run=payload.dry_run,
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                500,
                f"فشل التوليد: {type(e).__name__}: {e}",
            )
        return {"generated_at": _now(), **result}

    @router.get("/periods")
    async def list_periods(
        provider: Optional[str] = Query(None),
        status:   Optional[str] = Query(None),
        from_date: Optional[str] = Query(None),
        to_date:   Optional[str] = Query(None),
        skip: int = 0,
        limit: int = 100,
        user: dict = Depends(current_user),
    ):
        q: dict = {"user_id": user["id"]}
        if provider:
            q["provider"] = provider
        if status:
            sts = [s for s in status.split(",") if s in _PERIOD_STATUSES]
            if sts:
                q["status"] = {"$in": sts}
        if from_date or to_date:
            rng: dict = {}
            if from_date:
                rng["$gte"] = from_date
            if to_date:
                rng["$lte"] = to_date
            q["period_from"] = rng
        total = await db.settlement_periods.count_documents(q)
        items = []
        async for d in (db.settlement_periods.find(q, {"_id": 0})
                        .sort([("period_from", -1)])
                        .skip(max(0, skip))
                        .limit(max(1, min(limit, 500)))):
            items.append(d)
        return {"items": items, "total": total,
                "skip": skip, "limit": limit}

    @router.get("/invoices")
    async def list_invoices(
        provider: Optional[str] = Query(None),
        status:   Optional[str] = Query(None),
        from_date: Optional[str] = Query(None),
        to_date:   Optional[str] = Query(None),
        skip: int = 0,
        limit: int = 100,
        user: dict = Depends(current_user),
    ):
        q: dict = {"user_id": user["id"]}
        if provider:
            q["provider_name"] = provider
        if status:
            sts = [s for s in status.split(",") if s in _INVOICE_STATUSES]
            if sts:
                q["status"] = {"$in": sts}
        if from_date or to_date:
            rng: dict = {}
            if from_date:
                rng["$gte"] = from_date
            if to_date:
                rng["$lte"] = to_date
            q["period_from"] = rng
        total = await db.settlement_invoices.count_documents(q)
        items = []
        async for d in (db.settlement_invoices.find(q, {"_id": 0})
                        .sort([("period_from", -1), ("invoice_no", 1)])
                        .skip(max(0, skip))
                        .limit(max(1, min(limit, 500)))):
            items.append(d)
        return {"items": items, "total": total,
                "skip": skip, "limit": limit}

    @router.get("/invoices/{invoice_id}")
    async def get_invoice(
        invoice_id: str,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        inv = await db.settlement_invoices.find_one(
            {"id": invoice_id, "user_id": uid}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "الفاتورة غير موجودة")
        period = await db.settlement_periods.find_one(
            {"id": inv["settlement_period_id"], "user_id": uid},
            {"_id": 0},
        )
        xfer = None
        if inv.get("expected_transfer_id"):
            xfer = await db.expected_transfers.find_one(
                {"id": inv["expected_transfer_id"], "user_id": uid},
                {"_id": 0},
            )
        return {"invoice": inv, "period": period,
                "expected_transfer": xfer}

    @router.post("/invoices/{invoice_id}/cancel")
    async def cancel_invoice_route(
        invoice_id: str,
        payload: CancelInvoiceIn,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        res = await _cancel_invoice(
            db, uid, user, invoice_id, payload.reason)
        if res.get("error") == "not_found":
            raise HTTPException(404, "الفاتورة غير موجودة")
        if res.get("error") == "cannot_cancel_after_confirm":
            raise HTTPException(
                400,
                f"لا يمكن إلغاء فاتورة بحالة «{res.get('status')}».",
            )
        return res

    @router.get("/expected-transfers")
    async def list_expected_transfers(
        provider: Optional[str] = Query(None),
        status:   Optional[str] = Query(None),
        user: dict = Depends(current_user),
    ):
        q: dict = {"user_id": user["id"]}
        if provider:
            q["provider_name"] = provider
        if status:
            sts = [s for s in status.split(",") if s in _XFER_STATUSES]
            if sts:
                q["status"] = {"$in": sts}
        items = []
        async for d in (db.expected_transfers.find(q, {"_id": 0})
                        .sort([("expected_transfer_date", -1)])
                        .limit(500)):
            items.append(d)
        return {"items": items, "total": len(items)}

    @router.get("/stats")
    async def stats(user: dict = Depends(current_user)):
        uid = user["id"]
        out: dict[str, dict] = {}
        for coll, key in (("settlement_periods",   "periods"),
                          ("settlement_invoices",  "invoices"),
                          ("expected_transfers",   "expected_transfers")):
            buckets: dict[str, int] = {}
            async for r in db[coll].aggregate([
                {"$match": {"user_id": uid}},
                {"$group": {"_id": "$status",
                            "n": {"$sum": 1}}},
            ]):
                buckets[r["_id"] or "unknown"] = r["n"]
            total = sum(buckets.values())
            out[key] = {"total": total, "by_status": buckets}
        per_provider: dict[str, dict] = {}
        async for r in db.settlement_invoices.aggregate([
            {"$match": {"user_id": uid}},
            {"$group": {
                "_id": "$provider_name",
                "n":   {"$sum": 1},
                "amount": {"$sum": "$expected_transfer_amount"},
            }},
        ]):
            per_provider[r["_id"]] = {
                "invoices": r["n"],
                "expected_amount": round(float(r.get("amount") or 0), 2),
            }
        return {"summary": out, "per_provider": per_provider}

    return router
