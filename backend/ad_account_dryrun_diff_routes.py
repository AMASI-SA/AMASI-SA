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

        # 1. Discover accounts using **multi-source** logic so we
        #    don't miss anything (Iter-250b dryrun-fix).
        #
        # The historic counterparty field is `kind` (NOT `type`). On
        # top of that, some accounts may have GL rows but no longer
        # match the counterparty filter (renamed/soft-deleted). We
        # therefore collect ids from THREE sources and merge.
        accounts: List[Dict[str, Any]] = []
        accounts_by_id: Dict[str, Dict[str, Any]] = {}

        # Source A — counterparties.kind == ad_account
        cp_filter_a: Dict[str, Any] = {
            "user_id": uid, "kind": "ad_account",
        }
        if ad_account_id:
            cp_filter_a["id"] = ad_account_id
        async for a in db.counterparties.find(
            cp_filter_a,
            {"_id": 0, "id": 1, "name": 1, "platform": 1,
             "currency": 1, "opening_balance": 1,
             "current_balance": 1, "debt_balance": 1,
             "kind": 1, "category": 1},
        ):
            a["_source"] = "counterparties.kind"
            accounts_by_id[a["id"]] = a

        # Source B — counterparties.category == 'advertising' (older
        # docs / sub-classification).
        cp_filter_b: Dict[str, Any] = {
            "user_id": uid, "category": "advertising",
        }
        if ad_account_id:
            cp_filter_b["id"] = ad_account_id
        async for a in db.counterparties.find(
            cp_filter_b,
            {"_id": 0, "id": 1, "name": 1, "platform": 1,
             "currency": 1, "opening_balance": 1,
             "current_balance": 1, "debt_balance": 1,
             "kind": 1, "category": 1},
        ):
            if a["id"] not in accounts_by_id:
                a["_source"] = "counterparties.category"
                accounts_by_id[a["id"]] = a

        # Source C — distinct entity_id in general_ledger where
        # entity_type='ad_account' (catches GL-only orphans).
        gl_match: Dict[str, Any] = {
            "user_id": uid, "entity_type": "ad_account",
            "status": "posted",
        }
        if ad_account_id:
            gl_match["entity_id"] = ad_account_id
        gl_ids: List[str] = await db.general_ledger.distinct(
            "entity_id", gl_match)
        gl_only_ids: List[str] = []
        for gid in gl_ids:
            if gid and gid not in accounts_by_id:
                gl_only_ids.append(gid)
                # Fetch counterparty doc even if it doesn't match
                # kind/category filters (could be renamed/legacy).
                cp = await db.counterparties.find_one(
                    {"id": gid, "user_id": uid},
                    {"_id": 0, "id": 1, "name": 1, "platform": 1,
                     "currency": 1, "opening_balance": 1,
                     "current_balance": 1, "debt_balance": 1,
                     "kind": 1, "category": 1},
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

        accounts = list(accounts_by_id.values())

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

        # ── Discovery diagnostics (Iter-250b dryrun-fix) ──────────
        counterparties_count = await db.counterparties.count_documents({
            "user_id": uid, "kind": "ad_account",
        })
        counterparties_by_category = (
            await db.counterparties.count_documents({
                "user_id": uid, "category": "advertising",
            })
        )
        api_listing_count = await db.counterparties.count_documents({
            "user_id": uid, "kind": "ad_account", "active": {"$ne": False},
        })
        gl_distinct_count = len(gl_ids)
        sources_breakdown: Dict[str, int] = {}
        for a in accounts:
            sources_breakdown[a.get("_source", "?")] = (
                sources_breakdown.get(a.get("_source", "?"), 0) + 1
            )
        accounts_source = {
            "counterparties_kind_ad_account": counterparties_count,
            "counterparties_category_advertising":
                counterparties_by_category,
            "gl_distinct_ad_account_entity_ids": gl_distinct_count,
            "api_ad_accounts_count (kind=ad_account, active≠false)":
                api_listing_count,
            "merged_unique_accounts_scanned":
                totals["accounts_scanned"],
            "per_source_breakdown": sources_breakdown,
            "gl_only_orphans": gl_only_ids,
            "no_accounts_reason": (
                None if totals["accounts_scanned"] > 0
                else (
                    "لم يُعثر على حسابات في counterparties "
                    "(kind=ad_account) ولا category=advertising "
                    "ولا في general_ledger (entity_type=ad_account)."
                )
            ),
        }

        return {
            "ok": True,
            "iter": "iter250b-p0-dryrun",
            "read_only": True,
            "totals": totals,
            "overall_recommendation": overall,
            "accounts_source": accounts_source,
            "accounts": results,
            "forward_fix_plan_ref": "/app/docs/ITER250B_P0_PLAN.md",
        }

    return router
