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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel


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

PROVIDER_AR = {
    "tamara": "تمارا", "tabby": "تابي",
    "imkan":  "إمكان", "salla":  "سلة",
}


class FeatureFlagsIn(BaseModel):
    settlement_engine_enabled:           Optional[bool] = None
    platform_settlement_to_review_enabled: Optional[bool] = None
    bank_transfer_review_enabled:         Optional[bool] = None


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

    return router
