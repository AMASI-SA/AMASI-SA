"""Iter-250b Phase 0.7 — Ad-Account Actual Unpaid Debt Dry-Run.

Accounting rule (operator-approved):
  actual_unpaid_debt = actual_ad_spend - actual_topups - actual_payments

Where the following entries are EXCLUDED from each total:
  • entry_type ∈ {opening_balance, opening_debt, manual_debt,
                  manual_opening, migration, migration_opening,
                  migration_debt, ad_account_opening_debt}
  • metadata.source ∈ {opening_balance, manual_debt, migration,
                       opening_entry, manual_opening,
                       ad_account_migration}
  • metadata.is_opening == true
  • metadata.is_manual_debt == true
  • notes matching opening/manual-debt patterns (Arabic + English)

  GET /api/audit/ad-account-actual-debt-dryrun
      [?ad_account_id=<one>]
      [?include_clean=true]

100% read-only — no writes, no recomputes, no DB mutations.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query


TOL = 0.02


def _r(n) -> float:
    return round(float(n or 0), 2)


# Opening/manual/migration signatures — entries matching ANY of
# these are excluded from "actual" computations.
EXCLUDED_ENTRY_TYPES = [
    "opening_balance", "opening_debt", "opening_entry",
    "manual_debt", "manual_opening",
    "migration", "migration_opening", "migration_debt",
    "ad_account_opening_debt",
]
EXCLUDED_SOURCES = [
    "opening_balance", "manual_debt", "migration",
    "opening_entry", "manual_opening", "ad_account_migration",
]
EXCLUDE_FILTER_OR = [
    {"entry_type": {"$in": EXCLUDED_ENTRY_TYPES}},
    {"metadata.source": {"$in": EXCLUDED_SOURCES}},
    {"metadata.is_opening": True},
    {"metadata.is_manual_debt": True},
    {"notes": {"$regex":
               "افتتاح|مدين افتتاحي|opening|manual debt|migration",
               "$options": "i"}},
]


def _classify_entry(entry_type: str) -> str:
    """Bucket a non-excluded entry into spend / topup / payment."""
    et = (entry_type or "").lower()
    if "spend" in et or "expense" in et or et == "ad_spend":
        return "spend"
    if "topup" in et or "deposit" in et or et == "ad_topup":
        return "topup"
    if ("payment" in et or "settle" in et
            or "transfer" in et or "pay_" in et):
        return "payment"
    if "adjustment" in et:
        return "adjustment"
    return "other"


def make_ad_account_actual_debt_router(db, current_user):
    router = APIRouter(tags=["audit", "ad_accounts"])

    @router.get("/audit/ad-account-actual-debt-dryrun")
    async def dryrun(
        ad_account_id: Optional[str] = Query(
            None, description="One CP id, or omit for all"),
        include_clean: bool = Query(
            False,
            description="Include accounts whose proposed = 0"),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # ── 1. Discover ad accounts (kind=ad_account + GL fallback)
        accounts_by_id: Dict[str, Dict[str, Any]] = {}
        cp_filter: Dict[str, Any] = {
            "user_id": uid, "kind": "ad_account",
        }
        if ad_account_id:
            cp_filter["id"] = ad_account_id
        async for a in db.counterparties.find(
            cp_filter,
            {"_id": 0, "id": 1, "name": 1, "platform": 1,
             "currency": 1, "opening_balance": 1,
             "current_balance": 1, "debt_balance": 1},
        ):
            accounts_by_id[a["id"]] = a

        gl_match = {"user_id": uid, "entity_type": "ad_account",
                    "status": "posted"}
        if ad_account_id:
            gl_match["entity_id"] = ad_account_id
        gl_ids = await db.general_ledger.distinct(
            "entity_id", gl_match)
        for gid in gl_ids:
            if gid and gid not in accounts_by_id:
                cp = await db.counterparties.find_one(
                    {"id": gid, "user_id": uid},
                    {"_id": 0, "id": 1, "name": 1, "platform": 1,
                     "currency": 1, "opening_balance": 1,
                     "current_balance": 1, "debt_balance": 1},
                )
                accounts_by_id[gid] = cp or {
                    "id": gid,
                    "name": f"<orphan:{gid[:8]}>",
                    "platform": None, "currency": None,
                    "opening_balance": 0,
                    "current_balance": 0, "debt_balance": 0,
                }

        results: List[Dict[str, Any]] = []
        totals = {
            "accounts_scanned": 0,
            "accounts_becoming_zero": 0,
            "accounts_with_real_debt": 0,
            "accounts_with_credit_overpaid": 0,
            "total_current_gl_debt": 0.0,
            "total_excluded_opening_debt": 0.0,
            "total_actual_ad_spend": 0.0,
            "total_actual_topups": 0.0,
            "total_actual_payments": 0.0,
            "total_proposed_actual_unpaid_debt": 0.0,
        }

        for cp_id, acc in accounts_by_id.items():
            # 1) Current GL.debt (all posted, excluding reversals)
            cur_debt_d = cur_debt_c = 0.0
            async for r in db.general_ledger.aggregate([
                {"$match": {
                    "user_id": uid, "entity_type": "ad_account",
                    "entity_id": cp_id, "sub_account": "debt",
                    "status": "posted",
                    "entry_type": {"$ne": "reversal"},
                }},
                {"$group": {"_id": "$side",
                            "sum": {"$sum": "$amount"}}},
            ]):
                if r["_id"] == "debit":
                    cur_debt_d = float(r["sum"])
                elif r["_id"] == "credit":
                    cur_debt_c = float(r["sum"])
            current_gl_debt = _r(cur_debt_d - cur_debt_c)

            # 2) Excluded opening/manual/migration totals
            exc_d = exc_c = 0.0
            exc_count = 0
            exc_breakdown: Dict[str, Dict[str, Any]] = {}
            async for r in db.general_ledger.aggregate([
                {"$match": {
                    "user_id": uid, "entity_type": "ad_account",
                    "entity_id": cp_id, "sub_account": "debt",
                    "status": "posted",
                    "entry_type": {"$ne": "reversal"},
                    "$or": EXCLUDE_FILTER_OR,
                }},
                {"$group": {
                    "_id": {"et": "$entry_type",
                            "side": "$side"},
                    "n": {"$sum": 1},
                    "sum": {"$sum": "$amount"},
                }},
            ]):
                et = r["_id"].get("et") or "<null>"
                side = r["_id"].get("side") or "<null>"
                amt = float(r["sum"])
                n = int(r["n"])
                bucket = exc_breakdown.setdefault(et, {
                    "debit_sum": 0.0, "credit_sum": 0.0,
                    "count": 0,
                })
                bucket["count"] += n
                if side == "debit":
                    bucket["debit_sum"] = _r(
                        bucket["debit_sum"] + amt)
                    exc_d += amt
                else:
                    bucket["credit_sum"] = _r(
                        bucket["credit_sum"] + amt)
                    exc_c += amt
                exc_count += n
            for k, v in exc_breakdown.items():
                v["net"] = _r(v["debit_sum"] - v["credit_sum"])
            excluded_opening_debt = _r(exc_d - exc_c)

            # 3) Actual spends / topups / payments (NON-excluded)
            actual = {"spend": 0.0, "topup": 0.0,
                      "payment": 0.0, "adjustment": 0.0,
                      "other": 0.0}
            actual_counts = {"spend": 0, "topup": 0,
                             "payment": 0, "adjustment": 0,
                             "other": 0}
            async for r in db.general_ledger.aggregate([
                {"$match": {
                    "user_id": uid, "entity_type": "ad_account",
                    "entity_id": cp_id, "sub_account": "debt",
                    "status": "posted",
                    "entry_type": {"$ne": "reversal"},
                    "$nor": EXCLUDE_FILTER_OR,
                }},
                {"$group": {
                    "_id": {"et": "$entry_type",
                            "side": "$side"},
                    "n": {"$sum": 1},
                    "sum": {"$sum": "$amount"},
                }},
            ]):
                et = r["_id"].get("et") or ""
                side = r["_id"].get("side") or ""
                bucket = _classify_entry(et)
                amt = float(r["sum"])
                n = int(r["n"])
                # In sub_account=debt:
                #   debit  increases debt → spend
                #   credit decreases debt → topup / payment
                if bucket == "spend":
                    # Sum debit legs only
                    if side == "debit":
                        actual["spend"] = _r(
                            actual["spend"] + amt)
                    else:
                        # spend entry with credit side: refund.
                        actual["spend"] = _r(
                            actual["spend"] - amt)
                    actual_counts["spend"] += n
                elif bucket == "topup":
                    if side == "credit":
                        actual["topup"] = _r(
                            actual["topup"] + amt)
                    else:
                        actual["topup"] = _r(
                            actual["topup"] - amt)
                    actual_counts["topup"] += n
                elif bucket == "payment":
                    if side == "credit":
                        actual["payment"] = _r(
                            actual["payment"] + amt)
                    else:
                        actual["payment"] = _r(
                            actual["payment"] - amt)
                    actual_counts["payment"] += n
                elif bucket == "adjustment":
                    signed = amt if side == "debit" else -amt
                    actual["adjustment"] = _r(
                        actual["adjustment"] + signed)
                    actual_counts["adjustment"] += n
                else:
                    signed = amt if side == "debit" else -amt
                    actual["other"] = _r(
                        actual["other"] + signed)
                    actual_counts["other"] += n

            actual_spend = _r(actual["spend"])
            actual_topups = _r(actual["topup"])
            actual_payments = _r(actual["payment"])
            # Formula: actual_unpaid_debt = spend - topups - payments
            proposed = _r(
                actual_spend - actual_topups - actual_payments
                + actual["adjustment"]  # signed
            )
            delta = _r(proposed - current_gl_debt)

            # Sanity: current = proposed + excluded
            #   (excluded_opening_debt is the net signed contribution)
            sanity_residual = _r(
                current_gl_debt - proposed - excluded_opening_debt)

            if abs(proposed) <= TOL:
                status = "becomes_zero"
                totals["accounts_becoming_zero"] += 1
            elif proposed > TOL:
                status = "has_real_unpaid_debt"
                totals["accounts_with_real_debt"] += 1
            else:
                status = "has_credit_overpaid"
                totals["accounts_with_credit_overpaid"] += 1

            row = {
                "ad_account_id": cp_id,
                "name": acc.get("name"),
                "platform": acc.get("platform"),
                "currency": acc.get("currency"),
                "current_gl_debt": current_gl_debt,
                "excluded_opening_debt": excluded_opening_debt,
                "excluded_entries_count": exc_count,
                "excluded_breakdown_by_entry_type": exc_breakdown,
                "actual_ad_spend": actual_spend,
                "actual_topups": actual_topups,
                "actual_payments": actual_payments,
                "actual_adjustments": _r(actual["adjustment"]),
                "actual_other": _r(actual["other"]),
                "actual_counts": actual_counts,
                "proposed_actual_unpaid_debt": proposed,
                "delta_vs_current_gl_debt": delta,
                "sanity_residual":
                    sanity_residual,
                "final_status": status,
                "formula": (
                    f"{actual_spend} (spend) − {actual_topups} "
                    f"(topups) − {actual_payments} (payments) "
                    f"+ {_r(actual['adjustment'])} (adj) "
                    f"= {proposed}"
                ),
                "interpretation": (
                    "الحساب سيُصبح صفراً — لا دين فعلي بعد "
                    "استبعاد الافتتاحي."
                    if status == "becomes_zero" else
                    f"سيبقى دين فعلي = {proposed} ريال (مصاريف "
                    "إعلانية لم تُسدَّد بعد)."
                    if status == "has_real_unpaid_debt" else
                    f"رصيد دائن = {abs(proposed)} ريال (مدفوعات "
                    "تفوق المصاريف الفعلية بعد استبعاد "
                    "الافتتاحي — يحتاج تحقيقاً)."
                ),
            }

            # totals
            totals["accounts_scanned"] += 1
            totals["total_current_gl_debt"] = _r(
                totals["total_current_gl_debt"] + current_gl_debt)
            totals["total_excluded_opening_debt"] = _r(
                totals["total_excluded_opening_debt"]
                + excluded_opening_debt)
            totals["total_actual_ad_spend"] = _r(
                totals["total_actual_ad_spend"] + actual_spend)
            totals["total_actual_topups"] = _r(
                totals["total_actual_topups"] + actual_topups)
            totals["total_actual_payments"] = _r(
                totals["total_actual_payments"]
                + actual_payments)
            totals["total_proposed_actual_unpaid_debt"] = _r(
                totals["total_proposed_actual_unpaid_debt"]
                + proposed)

            if include_clean or status != "becomes_zero":
                results.append(row)

        # Overall
        if totals["accounts_scanned"] == 0:
            overall = "no_accounts"
        elif totals["accounts_with_credit_overpaid"] > 0:
            overall = "some_accounts_show_overpayment_needs_review"
        elif totals["accounts_with_real_debt"] == 0:
            overall = "all_accounts_will_become_zero"
        else:
            overall = "mixed_some_zero_some_real_debt"

        return {
            "ok": True,
            "iter": "iter250b-p0.7-actual-debt-dryrun",
            "read_only": True,
            "accounting_rule": (
                "actual_unpaid_debt = actual_ad_spend "
                "− actual_topups − actual_payments  "
                "(+ adjustments); opening/manual/migration "
                "entries are excluded from all 'actual' totals."
            ),
            "exclusion_criteria": {
                "entry_types_excluded": EXCLUDED_ENTRY_TYPES,
                "sources_excluded": EXCLUDED_SOURCES,
                "metadata_flags_excluded": [
                    "metadata.is_opening == true",
                    "metadata.is_manual_debt == true",
                ],
                "notes_regex": (
                    "افتتاح|مدين افتتاحي|opening|manual debt"
                    "|migration"
                ),
            },
            "totals": totals,
            "overall": overall,
            "constraints_honored": [
                "No writes to general_ledger",
                "No writes to counterparties",
                "No writes to liabilities",
                "No writes to ad_account_ledger",
                "No recompute / migration / cleanup",
                "No feature flag toggled",
            ],
            "accounts": results,
        }

    return router
