"""Iter-249c — Bank balance sub_account diagnostic (READ-ONLY).

Goal: BEFORE we apply any fix to the BNPL sub_account mismatch
(detected by Iter-249b), determine whether the account's
`current_balance` includes the `sub_account="balance"` legs or not.

This dictates the fix path:
  • If current_balance == ledger(main)  →  bnpl is INVISIBLE in
    balance. Backfilling balance→main is safe AND will surface the
    BNPL amounts (current_balance will increase accordingly).
  • If current_balance == ledger(main+balance)  →  bnpl is already
    counted via a parallel pipeline. Backfilling balance→main risks
    DOUBLE-COUNTING. Safer fix is to widen the UI read filter only.
  • Otherwise  →  needs_manual_review.

  GET /api/audit/bank-balance-subaccount-diagnostic
       ?account_id=<BANK_ID>

100% read-only — no writes, no recomputes, no DB mutations.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


TOLERANCE = 0.02  # SAR — accounts for cumulative rounding


def _r(n) -> float:
    return round(float(n or 0), 2)


def make_bank_balance_subaccount_diag_router(db, current_user):
    router = APIRouter(tags=["audit", "bnpl"])

    @router.get("/audit/bank-balance-subaccount-diagnostic")
    async def diag(
        account_id: str = Query(..., description="Bank account id"),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # ── 1. Account ─────────────────────────────────────────────
        acc = await db.accounts.find_one(
            {"id": account_id, "user_id": uid},
            {"_id": 0, "id": 1, "name": 1, "account_type": 1,
             "currency": 1, "current_balance": 1,
             "opening_balance": 1, "opening_balance_date": 1,
             "status": 1},
        )
        if not acc:
            raise HTTPException(404, "Account not found.")
        if acc.get("account_type") not in ("bank", "cash"):
            raise HTTPException(
                400,
                "هذا الـ endpoint مخصص للحسابات البنكية/النقدية "
                "فقط.",
            )

        current_balance = _r(acc.get("current_balance"))

        # ── 2. Helper: posted, non-reversal, non-legacy aggregation
        async def _agg(sub_filter: Dict[str, Any]) -> Dict[str, Any]:
            match = {
                "user_id": uid,
                "entity_type": "bank",
                "entity_id": account_id,
                "status": "posted",
                "entry_type": {"$ne": "reversal"},
                "metadata.legacy_orphan": {"$ne": True},
                **sub_filter,
            }
            pipeline = [
                {"$match": match},
                {"$group": {
                    "_id": "$side",
                    "total": {"$sum": "$amount"},
                    "count": {"$sum": 1},
                }},
            ]
            debits = 0.0
            credits = 0.0
            d_count = 0
            c_count = 0
            async for row in db.general_ledger.aggregate(pipeline):
                if row["_id"] == "debit":
                    debits = float(row["total"])
                    d_count = int(row["count"])
                elif row["_id"] == "credit":
                    credits = float(row["total"])
                    c_count = int(row["count"])
            return {
                "debits": _r(debits),
                "credits": _r(credits),
                "net_balance": _r(debits - credits),
                "debit_count": d_count,
                "credit_count": c_count,
                "row_count": d_count + c_count,
            }

        # ── 3. Three balance views ────────────────────────────────
        bal_main = await _agg({"sub_account": "main"})
        bal_balance = await _agg({"sub_account": "balance"})
        bal_all = await _agg({})  # any sub_account

        # ── 4. sub_account census ─────────────────────────────────
        sub_census: Dict[str, int] = {}
        async for r in db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid,
                "entity_type": "bank",
                "entity_id": account_id,
                "status": "posted",
                "entry_type": {"$ne": "reversal"},
                "metadata.legacy_orphan": {"$ne": True},
            }},
            {"$group": {"_id": "$sub_account",
                        "n": {"$sum": 1}}},
        ]):
            sub_census[r["_id"] or "<null>"] = r["n"]

        # ── 5. BNPL settlement legs sitting under sub_account="balance"
        bnpl_balance_rows: List[Dict[str, Any]] = []
        async for r in db.general_ledger.find(
            {"user_id": uid,
             "entity_type": "bank",
             "entity_id": account_id,
             "sub_account": "balance",
             "entry_type": "bnpl_settlement",
             "status": "posted"},
            {"_id": 0, "id": 1, "side": 1, "amount": 1,
             "metadata": 1, "posted_at": 1, "txn_group_id": 1,
             "notes": 1},
        ).sort("posted_at", -1):
            bnpl_balance_rows.append(r)

        bnpl_balance_sum = _r(sum(
            (1 if r["side"] == "debit" else -1) * float(r["amount"])
            for r in bnpl_balance_rows
        ))

        # Also count: any OTHER entry_types under "balance" (so the
        # operator knows whether the fix scope is BNPL-only or wider).
        other_balance_rows: Dict[str, Any] = {}
        async for r in db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid,
                "entity_type": "bank",
                "entity_id": account_id,
                "sub_account": "balance",
                "status": "posted",
                "entry_type": {"$ne": "reversal"},
            }},
            {"$group": {"_id": "$entry_type",
                        "n": {"$sum": 1},
                        "sum_debit": {"$sum": {"$cond": [
                            {"$eq": ["$side", "debit"]},
                            "$amount", 0]}},
                        "sum_credit": {"$sum": {"$cond": [
                            {"$eq": ["$side", "credit"]},
                            "$amount", 0]}}}},
        ]):
            other_balance_rows[r["_id"] or "<null>"] = {
                "count": r["n"],
                "sum_debit": _r(r["sum_debit"]),
                "sum_credit": _r(r["sum_credit"]),
                "net": _r(r["sum_debit"] - r["sum_credit"]),
            }

        # ── 6. Deltas vs current_balance ──────────────────────────
        d_main = _r(current_balance - bal_main["net_balance"])
        d_balance = _r(current_balance - bal_balance["net_balance"])
        d_all = _r(current_balance - bal_all["net_balance"])

        def _matches(delta: float) -> bool:
            return abs(delta) <= TOLERANCE

        # ── 7. Auto-recommendation ────────────────────────────────
        # Hypothesis A: current_balance == ledger(main)
        #   → BNPL legs are NOT in current_balance (because they live
        #     under "balance"). Safe to backfill balance→main, AND
        #     the balance will rise by `bnpl_balance_sum`.
        # Hypothesis B: current_balance == ledger(main+balance)
        #   → BNPL is already counted. Backfilling balance→main is
        #     safe (no double counting), but widening UI read filter
        #     to {main, balance} is equally safe and minimal.
        # Hypothesis C: current_balance == ledger(balance) only
        #   → exotic; needs manual review.
        # Otherwise: needs_manual_review.

        recommendation = "needs_manual_review"
        rationale: List[str] = []

        match_main_only = (
            _matches(d_main)
            and not _matches(d_all)
            and not _matches(d_balance)
        )
        match_main_plus_balance = (
            _matches(d_all)
            and not _matches(d_main)
        )
        match_main_and_all_equal = (
            _matches(d_main) and _matches(d_all)
        )  # happens when balance sub-account is empty/zero

        if match_main_only:
            recommendation = "safe_to_backfill_to_main"
            rationale.append(
                "current_balance يطابق ledger(main) فقط، ولا يطابق "
                "ledger(main+balance). هذا يعني أن قيود BNPL "
                "settlement تحت sub_account='balance' ليست محتسبة "
                "حالياً في الرصيد المعروض."
            )
            rationale.append(
                "Backfilling balance→main سيُضيف هذه القيود إلى "
                "main → سيرتفع current_balance بمقدار "
                f"{bnpl_balance_sum} ر.س تقريباً. هذا هو الرصيد "
                "الحقيقي للبنك بعد دخول التسويات."
            )
        elif match_main_plus_balance:
            recommendation = "safe_to_read_main_plus_balance"
            rationale.append(
                "current_balance يطابق ledger(main+balance). إذن "
                "قيود sub_account='balance' محتسبة فعلاً في الرصيد."
            )
            rationale.append(
                "Backfilling balance→main لن يغيّر الرصيد (آمن من "
                "الاحتساب المضاعف)، لكن البديل الأبسط هو توسيع "
                "فلتر _ledger_based_tx_feed ليشمل sub_account ∈ "
                "{main, balance} — تغيير سطر واحد."
            )
        elif match_main_and_all_equal:
            recommendation = (
                "no_balance_sub_account_rows"
                if bal_balance["row_count"] == 0
                else "safe_to_backfill_to_main"
            )
            rationale.append(
                "ledger(main) == ledger(main+balance) لأن sub_"
                "account='balance' فارغ أو متعادل صفرياً."
            )
        else:
            rationale.append(
                "current_balance لا يطابق أي من ledger(main) ولا "
                "ledger(main+balance) ضمن tolerance "
                f"{TOLERANCE} ر.س. توجد كتابات موازية (حقل "
                "current_balance يُحدّث من مسار آخر غير universal "
                "ledger — على الأرجح account_transactions). يحتاج "
                "مراجعة يدوية قبل أي تعديل."
            )
            rationale.append(
                f"Δ(main)={d_main}, Δ(balance)={d_balance}, "
                f"Δ(main+balance)={d_all}."
            )

        # Double-count risk flag
        risk_double_count = (
            recommendation == "safe_to_read_main_plus_balance"
        ) and bnpl_balance_sum > 0
        # Only really a risk if someone tries to BOTH backfill AND
        # widen the filter. We surface it explicitly.

        # ── 8. Impact summary on UI vs balance ────────────────────
        # "Will widening the UI filter to {main,balance} change the
        # running balance shown in the statement?"
        # Yes, because _ledger_based_tx_feed walks every row and
        # accumulates `running` from debit/credit. So if we widen the
        # filter, the new last `balance_after` shown in the UI will
        # equal ledger(main+balance).
        ui_filter_widen_effect = {
            "current_ui_last_balance_will_become":
                bal_all["net_balance"],
            "current_ui_last_balance_today_is":
                bal_main["net_balance"],
            "delta_shown_to_user":
                _r(bal_all["net_balance"]
                   - bal_main["net_balance"]),
            "note": (
                "هذا الفرق يساوي صافي قيود BNPL تحت 'balance'. "
                "إذا كان current_balance المعروض في الواجهة "
                "(الكارت العلوي) لا يطابق آخر balance_after في "
                "الجدول حالياً، فقد يكون التعديل يُصلح هذه "
                "التفرقة أيضاً."
            ),
        }

        return {
            "ok": True,
            "iter": "iter249c",
            "read_only": True,
            "account": acc,
            "balances": {
                "account_current_balance": current_balance,
                "ledger_main": bal_main,
                "ledger_balance": bal_balance,
                "ledger_main_plus_balance": bal_all,
            },
            "deltas_vs_current_balance": {
                "current_minus_ledger_main": d_main,
                "current_minus_ledger_balance": d_balance,
                "current_minus_ledger_main_plus_balance": d_all,
                "tolerance_sar": TOLERANCE,
            },
            "sub_account_census": sub_census,
            "bnpl_settlement_under_balance": {
                "count": len(bnpl_balance_rows),
                "net_sum": bnpl_balance_sum,
                "rows": bnpl_balance_rows,
            },
            "other_entry_types_under_balance": other_balance_rows,
            "ui_filter_widen_effect": ui_filter_widen_effect,
            "recommendation": {
                "code": recommendation,
                "risk_of_double_counting": risk_double_count,
                "rationale": rationale,
                "next_action": {
                    "safe_to_backfill_to_main": (
                        "نفّذ Iter-249d backfill (dry-run أولاً) "
                        "لتحويل sub_account='balance' إلى 'main' "
                        "للقيود bnpl_settlement فقط. الرصيد "
                        "المعروض سيرتفع بمقدار net_sum."
                    ),
                    "safe_to_read_main_plus_balance": (
                        "وسّع فلتر _ledger_based_tx_feed في "
                        "accounts_routes.py ليقبل sub_account ∈ "
                        "{main, balance} للـ bank entity فقط. لا "
                        "تنفّذ backfill في نفس الوقت."
                    ),
                    "no_balance_sub_account_rows": (
                        "لا يوجد ما يجب إصلاحه على هذا الحساب — "
                        "أعد تشغيل التشخيص على حسابات بنكية أخرى."
                    ),
                    "needs_manual_review": (
                        "افحص آلية تحديث account.current_balance "
                        "(محتمل أن account_transactions يُغذّيها "
                        "بشكل موازٍ). لا تُنفّذ أي إصلاح."
                    ),
                }.get(recommendation),
            },
        }

    return router
