"""Iter-247 — BNPL settlement trace (READ-ONLY).

`GET /api/audit/bnpl-settlement-trace?txn_group_id=<id>`

Surfaces EVERY artifact created (or NOT created) by a single
`bnpl_settlement` save:

  • all general_ledger legs of that txn_group_id
  • the linked bank account
  • any financial_movements records referencing the group
  • any account_transactions on the bank account that match by
    txn_group_id, idempotency_key, or settlement_reference
  • inferred diagnosis explaining why a bank-account UI page may
    or may not display the inflow

STRICT READ-ONLY.
"""
from __future__ import annotations
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, Query


def _r(n) -> float: return round(float(n or 0), 2)


def make_bnpl_settlement_trace_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit", "bnpl"])

    @router.get("/bnpl-settlement-trace")
    async def trace(
        user: dict = Depends(current_user),
        txn_group_id: str = Query(..., min_length=8),
    ):
        uid = user["id"]

        # 1) All ledger legs of this group.
        legs: List[Dict[str, Any]] = []
        async for e in db.general_ledger.find(
            {"user_id": uid, "txn_group_id": txn_group_id},
            {"_id": 0, "id": 1, "side": 1, "amount": 1,
             "entity_type": 1, "entity_id": 1, "sub_account": 1,
             "entry_type": 1, "transaction_date": 1, "created_at": 1,
             "description": 1, "metadata": 1},
        ):
            legs.append({
                "leg_id": e.get("id"),
                "side": e.get("side"),
                "amount": _r(e.get("amount")),
                "account": (
                    f"{e.get('entity_type')}."
                    f"{e.get('entity_id')}"
                    f"{('.' + e['sub_account']) if e.get('sub_account') else ''}"
                ),
                "entry_type": e.get("entry_type"),
                "description": e.get("description"),
                "transaction_date": e.get("transaction_date"),
                "created_at": e.get("created_at"),
                "metadata": e.get("metadata") or {},
            })

        # 2) Extract metadata hints from settlement leg.
        settlement_legs = [l for l in legs
                           if l["entry_type"] == "bnpl_settlement"]
        md = settlement_legs[0]["metadata"] if settlement_legs else {}
        bank_account_id = md.get("bank_account_id")
        bank_account_name = md.get("bank_account_name")
        settlement_reference = md.get("settlement_reference")
        idem = md.get("idempotency_key")
        transferred_amount = _r(md.get("transferred_amount"))

        # 3) Bank legs in this group.
        bank_legs = [l for l in legs if l["account"].startswith("bank.")]

        # 4) Bank account doc.
        bank_doc = None
        if bank_account_id:
            bank_doc = await db.accounts.find_one(
                {"user_id": uid, "id": bank_account_id},
                {"_id": 0, "id": 1, "name": 1, "current_balance": 1,
                 "account_type": 1, "currency": 1},
            )

        # 5) financial_movements references.
        fin_moves: List[Dict[str, Any]] = []
        for q in (
            {"user_id": uid, "txn_group_id": txn_group_id},
            {"user_id": uid, "reference": settlement_reference}
                if settlement_reference else None,
            {"user_id": uid, "idempotency_key": idem} if idem else None,
        ):
            if not q: continue
            async for m in db.financial_movements.find(q, {"_id": 0}):
                fin_moves.append(m)

        # 6) account_transactions on the bank account.
        bank_txns: List[Dict[str, Any]] = []
        if bank_account_id:
            async for t in db.account_transactions.find(
                {"user_id": uid, "account_id": bank_account_id,
                 "$or": [
                     {"txn_group_id": txn_group_id},
                     {"reference": settlement_reference}
                     if settlement_reference else {"_x": "_skip"},
                     {"idempotency_key": idem}
                     if idem else {"_x": "_skip"},
                 ]},
                {"_id": 0},
            ):
                bank_txns.append(t)

        # 7) Diagnosis.
        has_ledger = bool(settlement_legs)
        has_bank_leg = bool(bank_legs)
        has_fin_move = bool(fin_moves)
        has_bank_txn = bool(bank_txns)

        if has_ledger and has_bank_leg and not has_bank_txn:
            cause = (
                "تسوية BNPL أنشأت قيد دفتر الأستاذ مع leg على الحساب "
                "البنكي، لكنها لم تُسجّل سطراً في `account_transactions` "
                "(جدول حركات الحسابات الذي تستهلكه شاشة كشف الحساب "
                "البنكي).  لذلك المبلغ موجود محاسبياً (يظهر في الـ "
                "ledger وفي `current_balance`) لكنه غير ظاهر كسطر "
                "حركة في صفحة كشف بنك الإنماء.  هذا missing bank "
                "transaction — يحتاج توصيل خط mirror مماثل لما تفعله "
                "شاشة 'تحويلات بين الحسابات' عبر "
                "`mirror_account_txn_to_ledger`."
            )
        elif has_ledger and not has_bank_leg:
            cause = (
                "قيد التسوية لا يحتوي أصلاً على leg على الحساب البنكي "
                "— الجسر يكتب الـ receivable + commission + vat + fee "
                "فقط دون debit على البنك.  هذا bug في الجسر."
            )
        elif has_bank_txn:
            cause = (
                "السطر مُسجَّل في account_transactions — مشكلة UI فقط "
                "(الـ filter / range / pagination في صفحة كشف الحساب)."
            )
        else:
            cause = "لم يُعثر على txn_group — تأكّد من الـ ID."

        return {
            "ok": True,
            "iter": "iter247",
            "read_only": True,
            "txn_group_id": txn_group_id,
            "settlement_metadata": {
                "settlement_reference": settlement_reference,
                "transferred_amount": transferred_amount,
                "bank_account_id": bank_account_id,
                "bank_account_name": bank_account_name,
                "idempotency_key": idem,
            },
            "general_ledger": {
                "legs_count": len(legs),
                "settlement_legs_found": len(settlement_legs),
                "bank_legs_found": len(bank_legs),
                "legs": legs,
            },
            "bank_account_doc": bank_doc,
            "financial_movements": {
                "count": len(fin_moves), "entries": fin_moves,
            },
            "account_transactions_on_bank": {
                "count": len(bank_txns), "entries": bank_txns,
            },
            "checks": {
                "ledger_settlement_created": has_ledger,
                "ledger_has_bank_leg": has_bank_leg,
                "financial_movement_created": has_fin_move,
                "bank_account_transaction_created": has_bank_txn,
            },
            "inferred_cause": cause,
        }

    return router
