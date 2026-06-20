"""Iter-250b Phase 0.5 — Ad-Account Reconciliation Dry-Run (READ-ONLY).

Per ad account, computes what `counterparties.{current,debt}_balance`
WOULD become if we re-derived them from `general_ledger` (the
canonical SSOT). Also classifies each account as either:

  • RECOMPUTE_CACHE_ONLY   — safe to refresh the cache from GL
  • MANUAL_REVIEW_REQUIRED — anomalies detected, do NOT auto-recompute

  GET /api/audit/ad-account-recompute-dryrun
      [?ad_account_id=<one>]
      [?include_clean=true]

Critical: this is a PROPOSAL only — no writes, no recomputes, no DB
mutations. Surfaces enough evidence so the operator can decide which
accounts (if any) to refresh manually.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query


TOL = 0.02


def _r(n) -> float:
    return round(float(n or 0), 2)


def make_ad_account_recompute_dryrun_router(db, current_user):
    router = APIRouter(tags=["audit", "ad_accounts"])

    @router.get("/audit/ad-account-recompute-dryrun")
    async def recompute_dryrun(
        ad_account_id: Optional[str] = Query(
            None, description="One CP id, or omit for all"),
        include_clean: bool = Query(
            False, description="Include accounts with zero delta"),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # ── 1. Discover accounts (same logic as dryrun-diff) ──────
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
            a["_source"] = "counterparties.kind"
            accounts_by_id[a["id"]] = a

        gl_match: Dict[str, Any] = {
            "user_id": uid, "entity_type": "ad_account",
            "status": "posted",
        }
        if ad_account_id:
            gl_match["entity_id"] = ad_account_id
        gl_ids = await db.general_ledger.distinct("entity_id",
                                                  gl_match)
        gl_only_orphans: List[str] = []
        for gid in gl_ids:
            if gid and gid not in accounts_by_id:
                gl_only_orphans.append(gid)
                cp = await db.counterparties.find_one(
                    {"id": gid, "user_id": uid},
                    {"_id": 0, "id": 1, "name": 1, "platform": 1,
                     "currency": 1, "opening_balance": 1,
                     "current_balance": 1, "debt_balance": 1},
                )
                if cp:
                    cp["_source"] = "general_ledger.entity_id"
                    accounts_by_id[gid] = cp
                else:
                    accounts_by_id[gid] = {
                        "id": gid,
                        "name": f"<orphan:{gid[:8]}>",
                        "platform": None, "currency": None,
                        "opening_balance": 0,
                        "current_balance": 0, "debt_balance": 0,
                        "_source": "general_ledger.orphan",
                    }

        results: List[Dict[str, Any]] = []
        totals = {
            "accounts_scanned": 0,
            "recompute_cache_only": 0,
            "manual_review_required": 0,
            "total_abs_delta_current_balance": 0.0,
            "total_abs_delta_debt_balance": 0.0,
            "total_legacy_rows_remaining": {
                "ad_account_ledger": 0,
                "liabilities": 0,
                "account_transactions": 0,
            },
        }

        for cp_id, acc in accounts_by_id.items():
            cur_balance = _r(acc.get("current_balance"))
            cur_debt = _r(acc.get("debt_balance"))

            # GL net for both sub_accounts
            async def _net(sub: str) -> Dict[str, Any]:
                d = c = n_d = n_c = 0
                async for r in db.general_ledger.aggregate([
                    {"$match": {
                        "user_id": uid,
                        "entity_type": "ad_account",
                        "entity_id": cp_id,
                        "sub_account": sub,
                        "status": "posted",
                        "entry_type": {"$ne": "reversal"},
                    }},
                    {"$group": {"_id": "$side",
                                "total": {"$sum": "$amount"},
                                "n": {"$sum": 1}}},
                ]):
                    if r["_id"] == "debit":
                        d, n_d = float(r["total"]), int(r["n"])
                    elif r["_id"] == "credit":
                        c, n_c = float(r["total"]), int(r["n"])
                return {"debits": _r(d), "credits": _r(c),
                        "net": _r(d - c),
                        "row_count": n_d + n_c}

            gl_balance = await _net("balance")
            gl_debt = await _net("debt")

            # Proposed cache values:
            #  • current_balance ← GL(balance).net (signed; debit
            #    increases, credit decreases)
            #  • debt_balance    ← GL(debt).net  (positive = ad
            #    account owes us; negative = we owe them)
            proposed_current = gl_balance["net"]
            proposed_debt = gl_debt["net"]
            delta_current = _r(proposed_current - cur_balance)
            delta_debt = _r(proposed_debt - cur_debt)

            # Legacy row counts (will remain untouched)
            legacy_l_n = await db.ad_account_ledger.count_documents({
                "user_id": uid, "counterparty_id": cp_id,
            })
            liab_n = await db.liabilities.count_documents({
                "user_id": uid, "counterparty_id": cp_id,
            })
            atx_n = await db.account_transactions.count_documents({
                "user_id": uid,
                "$or": [{"counterparty_id": cp_id},
                        {"ad_account_id": cp_id}],
            })

            # ── Recommendation logic ──────────────────────────────
            reasons: List[str] = []
            recommendation = "RECOMPUTE_CACHE_ONLY"

            # Reason A — orphan (no counterparty doc anymore)
            if acc.get("_source") == "general_ledger.orphan":
                recommendation = "MANUAL_REVIEW_REQUIRED"
                reasons.append(
                    "حساب يتيم: لا يوجد counterparty doc — "
                    "موجود فقط في general_ledger."
                )

            # Reason B — both balance AND debt are non-trivial
            # (signals accounting confusion).
            if (abs(gl_balance["net"]) > 0.02
                    and abs(gl_debt["net"]) > 0.02):
                recommendation = "MANUAL_REVIEW_REQUIRED"
                reasons.append(
                    f"كل من GL(balance)={gl_balance['net']} و "
                    f"GL(debt)={gl_debt['net']} غير صفريين. "
                    "هذا يعني خلط محاسبي بين رصيد التزود (balance) "
                    "والدين (debt) — يحتاج مراجعة يدوية."
                )

            # Reason C — GL(debt) is negative beyond tolerance.
            # Debt should be positive (we owe the platform). A
            # negative net means we paid more than we spent.
            if gl_debt["net"] < -0.02:
                if recommendation != "MANUAL_REVIEW_REQUIRED":
                    recommendation = "MANUAL_REVIEW_REQUIRED"
                reasons.append(
                    f"GL(debt) سالب ({gl_debt['net']}). يعني أن "
                    "المدفوعات تفوق المصاريف المسجّلة — "
                    "إما مدفوعات زائدة، أو مصاريف ناقصة، أو "
                    "تحويلات مُنسوبة لحساب خطأ."
                )

            # Reason D — counterparty cache is "0/0" but GL has
            # real activity (delta huge). Suggests cache never
            # synced. Safe to recompute IF none of A/B/C triggered.
            cache_was_zero = (cur_balance == 0 and cur_debt == 0)
            gl_has_activity = (
                gl_balance["row_count"] + gl_debt["row_count"] > 0
            )

            # Reason E — liabilities have non-zero rows; we WILL
            # leave them deprecated. Not a blocker, just a note.
            liabilities_note = (
                f"{liab_n} سطر liabilities سيبقى deprecated "
                "(لن يُحذَف ولن يُحدَّث)."
                if liab_n > 0 else "لا توجد سطور liabilities."
            )

            # Final classification:
            if recommendation == "RECOMPUTE_CACHE_ONLY":
                if abs(delta_current) <= TOL \
                        and abs(delta_debt) <= TOL:
                    recommendation_detail = (
                        "الكاش يطابق GL بالفعل — لا حاجة لـ "
                        "recompute."
                    )
                elif cache_was_zero and gl_has_activity:
                    recommendation_detail = (
                        "الكاش = 0/0 بينما GL يحتوي نشاطاً. "
                        "recompute يُحدّث الكاش لتعكس GL، آمن."
                    )
                else:
                    recommendation_detail = (
                        "تحديث الكاش من GL يُغلق الفجوة دون "
                        "تأثير على ledger أو liabilities."
                    )
            else:
                recommendation_detail = (
                    "لا يُسمح بـ recompute آلي. حدّد السبب من "
                    "reasons ثم أنشئ تسوية محاسبية يدوية في "
                    "general_ledger قبل إعادة الحساب."
                )

            row = {
                "ad_account_id": cp_id,
                "name": acc.get("name"),
                "platform": acc.get("platform"),
                "currency": acc.get("currency"),
                "source": acc.get("_source"),
                "current_cache": {
                    "current_balance": cur_balance,
                    "debt_balance": cur_debt,
                },
                "proposed_from_gl": {
                    "current_balance": proposed_current,
                    "debt_balance": proposed_debt,
                },
                "deltas": {
                    "current_balance_delta": delta_current,
                    "debt_balance_delta": delta_debt,
                },
                "gl_evidence": {
                    "sub_account_balance_rows":
                        gl_balance["row_count"],
                    "sub_account_balance_net": gl_balance["net"],
                    "sub_account_debt_rows": gl_debt["row_count"],
                    "sub_account_debt_net": gl_debt["net"],
                },
                "legacy_rows_remaining": {
                    "ad_account_ledger": legacy_l_n,
                    "liabilities": liab_n,
                    "account_transactions": atx_n,
                    "handling": (
                        "لن يتم حذف أو تعديل أي سطر legacy. "
                        "Recompute يُحدّث الكاش فقط من GL."
                    ),
                },
                "liabilities_handling": (
                    "deprecated_will_not_touch"
                ),
                "liabilities_note": liabilities_note,
                "recommendation": recommendation,
                "recommendation_detail": recommendation_detail,
                "reasons": reasons,
            }

            # Skip clean accounts if not requested
            is_clean = (
                abs(delta_current) <= TOL
                and abs(delta_debt) <= TOL
                and recommendation == "RECOMPUTE_CACHE_ONLY"
            )

            totals["accounts_scanned"] += 1
            if recommendation == "MANUAL_REVIEW_REQUIRED":
                totals["manual_review_required"] += 1
            else:
                totals["recompute_cache_only"] += 1
            totals["total_abs_delta_current_balance"] = _r(
                totals["total_abs_delta_current_balance"]
                + abs(delta_current))
            totals["total_abs_delta_debt_balance"] = _r(
                totals["total_abs_delta_debt_balance"]
                + abs(delta_debt))
            totals["total_legacy_rows_remaining"][
                "ad_account_ledger"] += legacy_l_n
            totals["total_legacy_rows_remaining"][
                "liabilities"] += liab_n
            totals["total_legacy_rows_remaining"][
                "account_transactions"] += atx_n

            if include_clean or not is_clean:
                results.append(row)

        # Overall verdict
        if totals["accounts_scanned"] == 0:
            overall = "no_accounts"
        elif totals["manual_review_required"] == 0:
            overall = "all_safe_to_recompute"
        elif totals["recompute_cache_only"] == 0:
            overall = "all_need_manual_review"
        else:
            overall = "mixed_partial_recompute_possible"

        return {
            "ok": True,
            "iter": "iter250b-p0.5-recompute-dryrun",
            "read_only": True,
            "totals": totals,
            "overall_recommendation": overall,
            "gl_only_orphans": gl_only_orphans,
            "constraints_honored": [
                "No writes to counterparties",
                "No writes to liabilities",
                "No writes to ad_account_ledger",
                "No writes to general_ledger",
                "No recompute executed",
                "No feature flag toggled",
            ],
            "accounts": results,
        }

    return router
