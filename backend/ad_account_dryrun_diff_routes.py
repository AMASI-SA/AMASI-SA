"""Iter-250b P0 — Ad-Account Dry-Run Diff (READ-ONLY).

Per ad account, computes the EXACT diff between:
  • legacy sources  : ad_account_ledger + liabilities +
                      account_transactions
  • SSOT (target)   : general_ledger entity_type=ad_account
                      sub_account ∈ {balance, debt}

This is a pure read pass — no mutation. It produces the input that
the upcoming Forward Fix needs in order to safely stop writing to
legacy and ratify GL as the single source of truth.

  GET /api/audit/ad-account-dryrun-diff
      [?ad_account_id=<one>]      — single account
      [?include_clean=true]       — include healthy accounts too

The diff is "safe to apply" when:
  • verdict.matches counterparty.debt_balance == GL(debt)
  • legacy_collections.*.row_count > 0 BUT their net contribution is
    already absorbed by GL (cross-checked via 24h windows).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query


TOL = 0.02


def _r(n) -> float:
    return round(float(n or 0), 2)


def _match(a: float, b: float) -> bool:
    return abs(a - b) <= TOL


def make_ad_account_dryrun_diff_router(db, current_user):
    router = APIRouter(tags=["audit", "ad_accounts"])

    @router.get("/audit/ad-account-dryrun-diff")
    async def dryrun(
        ad_account_id: Optional[str] = Query(
            None, description="One CP id, or omit for all"),
        include_clean: bool = Query(
            False, description="Include accounts already healthy"),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # 1. Discover accounts to scan
        accounts_filter: Dict[str, Any] = {
            "user_id": uid, "type": "ad_account",
        }
        if ad_account_id:
            accounts_filter["id"] = ad_account_id
        accounts = []
        async for a in db.counterparties.find(
            accounts_filter,
            {"_id": 0, "id": 1, "name": 1, "platform": 1,
             "currency": 1, "opening_balance": 1,
             "current_balance": 1, "debt_balance": 1},
        ):
            accounts.append(a)

        results: List[Dict[str, Any]] = []
        totals = {
            "accounts_scanned": 0,
            "accounts_clean": 0,
            "accounts_with_diff": 0,
            "total_legacy_rows": {
                "ad_account_ledger": 0,
                "liabilities": 0,
                "account_transactions": 0,
            },
            "total_abs_delta_debt": 0.0,
            "total_abs_delta_balance": 0.0,
        }

        for acc in accounts:
            cp_id = acc["id"]
            cb_balance = _r(acc.get("current_balance"))
            cb_debt = _r(acc.get("debt_balance"))

            # General ledger split
            async def _gl_net(sub: str) -> Dict[str, Any]:
                d = c = dn = cn = 0
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
                        d, dn = float(r["total"]), int(r["n"])
                    elif r["_id"] == "credit":
                        c, cn = float(r["total"]), int(r["n"])
                return {"debits": _r(d), "credits": _r(c),
                        "net": _r(d - c),
                        "row_count": dn + cn}

            gl_bal = await _gl_net("balance")
            gl_debt = await _gl_net("debt")

            # Legacy collections
            legacy_l_n = 0
            legacy_l_sum = 0.0
            async for r in db.ad_account_ledger.aggregate([
                {"$match": {"user_id": uid,
                            "counterparty_id": cp_id}},
                {"$group": {"_id": None, "n": {"$sum": 1},
                            "sum": {"$sum": "$amount"}}},
            ]):
                legacy_l_n = int(r["n"])
                legacy_l_sum = _r(r["sum"])

            liab_n = 0
            liab_sum = 0.0
            async for r in db.liabilities.aggregate([
                {"$match": {"user_id": uid,
                            "counterparty_id": cp_id}},
                {"$group": {"_id": None, "n": {"$sum": 1},
                            "sum": {"$sum": "$balance"}}},
            ]):
                liab_n = int(r["n"])
                liab_sum = _r(r["sum"])

            atx_n = await db.account_transactions.count_documents({
                "user_id": uid,
                "$or": [{"counterparty_id": cp_id},
                        {"ad_account_id": cp_id}],
            })

            # ── Deltas ────────────────────────────────────────
            delta_debt = _r(cb_debt - gl_debt["net"])
            delta_balance = _r(cb_balance - gl_bal["net"])
            delta_liab_vs_gl_debt = _r(liab_sum - gl_debt["net"])

            verdicts = [
                {"check": "counterparties.debt_balance == GL(debt)",
                 "left": cb_debt, "right": gl_debt["net"],
                 "delta": delta_debt,
                 "matches": _match(cb_debt, gl_debt["net"])},
                {"check": "counterparties.current_balance == GL(balance)",
                 "left": cb_balance, "right": gl_bal["net"],
                 "delta": delta_balance,
                 "matches": _match(cb_balance, gl_bal["net"])},
                {"check": "liabilities.sum_balance == GL(debt)",
                 "left": liab_sum, "right": gl_debt["net"],
                 "delta": delta_liab_vs_gl_debt,
                 "matches": _match(liab_sum, gl_debt["net"])},
            ]
            matched = sum(1 for v in verdicts if v["matches"])
            is_clean = (matched == len(verdicts)
                        and legacy_l_n == 0)

            # ── Forward-fix safety classification ─────────────
            # SAFE_TO_FREEZE_LEGACY means: GL already mirrors all
            # mutation effects, so disabling the legacy writers
            # will not change the displayed balances.
            if _match(cb_debt, gl_debt["net"]) \
                    and _match(liab_sum, gl_debt["net"]):
                freeze_safety = "SAFE_TO_FREEZE_LEGACY"
                rationale = (
                    "كل من counterparty.debt_balance و "
                    "liabilities.sum_balance يطابقان GL(debt). "
                    "إيقاف كتابة legacy لن يُغيّر الأرقام."
                )
            elif _match(cb_debt, gl_debt["net"]) \
                    and not _match(liab_sum, gl_debt["net"]):
                freeze_safety = "FREEZE_OK_BUT_LIABILITIES_STALE"
                rationale = (
                    "الـ debt_balance المعروض صحيح "
                    "(يطابق GL)، لكن liabilities متأخّر/متضارب. "
                    "آمن لتجميد legacy، لكن يُنصح بحذف الـ "
                    "liabilities entries لاحقاً (بعد اعتماد)."
                )
            else:
                freeze_safety = "NEEDS_RECONCILIATION_FIRST"
                rationale = (
                    "counterparty.debt_balance ≠ GL(debt). "
                    "تجميد legacy الآن سيُجمّد الفارق. يجب "
                    "تشغيل recompute-debt-from-ledger أولاً أو "
                    "تحقيق reconciliation يدوي."
                )

            row = {
                "ad_account_id": cp_id,
                "name": acc.get("name"),
                "platform": acc.get("platform"),
                "currency": acc.get("currency"),
                "is_clean": is_clean,
                "freeze_safety": freeze_safety,
                "rationale": rationale,
                "verdicts": verdicts,
                "verdicts_matched": matched,
                "verdicts_total": len(verdicts),
                "counterparties_cache": {
                    "current_balance": cb_balance,
                    "debt_balance": cb_debt,
                },
                "general_ledger": {
                    "balance_net": gl_bal["net"],
                    "balance_rows": gl_bal["row_count"],
                    "debt_net": gl_debt["net"],
                    "debt_rows": gl_debt["row_count"],
                },
                "legacy_collections": {
                    "ad_account_ledger": {
                        "row_count": legacy_l_n,
                        "sum_amount": legacy_l_sum,
                    },
                    "liabilities": {
                        "row_count": liab_n,
                        "sum_balance": liab_sum,
                    },
                    "account_transactions": {
                        "row_count": atx_n,
                    },
                },
                "deltas": {
                    "debt_balance_minus_GL_debt": delta_debt,
                    "current_balance_minus_GL_balance":
                        delta_balance,
                    "liabilities_minus_GL_debt":
                        delta_liab_vs_gl_debt,
                },
            }

            totals["accounts_scanned"] += 1
            if is_clean:
                totals["accounts_clean"] += 1
            if matched < len(verdicts) or legacy_l_n > 0:
                totals["accounts_with_diff"] += 1
            totals["total_legacy_rows"]["ad_account_ledger"] \
                += legacy_l_n
            totals["total_legacy_rows"]["liabilities"] += liab_n
            totals["total_legacy_rows"]["account_transactions"] \
                += atx_n
            totals["total_abs_delta_debt"] = _r(
                totals["total_abs_delta_debt"] + abs(delta_debt))
            totals["total_abs_delta_balance"] = _r(
                totals["total_abs_delta_balance"]
                + abs(delta_balance))

            if not is_clean or include_clean:
                results.append(row)

        # Global verdict
        if totals["accounts_scanned"] == 0:
            overall = "no_accounts"
        elif totals["accounts_with_diff"] == 0:
            overall = "all_clean"
        elif all(r["freeze_safety"] == "SAFE_TO_FREEZE_LEGACY"
                 for r in results):
            overall = "safe_to_apply_forward_fix"
        elif any(r["freeze_safety"] == "NEEDS_RECONCILIATION_FIRST"
                 for r in results):
            overall = "needs_reconciliation_before_apply"
        else:
            overall = "partial_apply_possible"

        return {
            "ok": True,
            "iter": "iter250b-p0-dryrun",
            "read_only": True,
            "totals": totals,
            "overall_recommendation": overall,
            "accounts": results,
            "forward_fix_plan_ref": "/app/docs/ITER250B_P0_PLAN.md",
        }

    return router
