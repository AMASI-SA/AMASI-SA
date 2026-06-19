"""Iter-246w — Tamara settlement history forensic (READ-ONLY).

Lists EVERY general_ledger entry that has touched the Tamara
receivable balance, with the source endpoint that created it.
Surfaces the exact list of past Tamara settlements / transfers so the
merchant can detect duplicates that have silently drained the
receivable below the iter246r-correct Net Sales.

Per entry returned:
  • ledger_entry_id  (general_ledger.id)
  • txn_group_id     (groups balanced debit/credit legs)
  • created_at
  • amount
  • side             (debit | credit)
  • entry_type       (bnpl_sale | bnpl_refund | bnpl_settlement |
                      internal_transfer | ...)
  • source_endpoint  (POST /api/transfers, POST /api/bnpl/settlements/register,
                      ledger_double_write, etc.)
  • description
  • affected_accounts (all legs of the same txn_group_id)

Plus running balance to show how each entry moved the receivable.

STRICT READ-ONLY.  No writes.  No mutations.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def make_tamara_settlement_history_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit", "tamara"])

    @router.get("/tamara-settlement-history")
    async def tamara_settlement_history(
        user: dict = Depends(current_user),
        provider: str = Query("tamara"),
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
    ):
        uid = user["id"]
        prov = (provider or "tamara").lower()

        # Match every posted entry on the receivable balance.
        match: Dict[str, Any] = {
            "user_id": uid,
            "entity_type": "payment_gateway",
            "entity_id": prov,
            "sub_account": "receivable",
            "status": "posted",
        }
        if date_from or date_to:
            rng: Dict[str, str] = {}
            if date_from:
                rng["$gte"] = date_from
            if date_to:
                rng["$lte"] = date_to + "T23:59:59Z"
            match["transaction_date"] = rng

        # Fetch the receivable legs, oldest first, so we can compute a
        # running balance to show how each entry moved the receivable.
        legs: List[Dict[str, Any]] = []
        async for e in db.general_ledger.find(
            match,
            {"_id": 0, "id": 1, "txn_group_id": 1, "side": 1,
             "amount": 1, "entry_type": 1, "transaction_date": 1,
             "description": 1, "metadata": 1, "created_at": 1,
             "created_by": 1, "currency": 1},
        ).sort([("transaction_date", 1), ("created_at", 1)]):
            legs.append(e)

        # For each leg, resolve the full balanced group so the user
        # can see what the counter-leg(s) hit (bank? expense? revenue?).
        group_ids = list({e["txn_group_id"] for e in legs
                          if e.get("txn_group_id")})
        groups: Dict[str, List[Dict[str, Any]]] = {}
        if group_ids:
            async for g in db.general_ledger.find(
                {"user_id": uid, "txn_group_id": {"$in": group_ids},
                 "status": "posted"},
                {"_id": 0, "id": 1, "txn_group_id": 1, "side": 1,
                 "amount": 1, "entity_type": 1, "entity_id": 1,
                 "sub_account": 1, "entry_type": 1},
            ):
                groups.setdefault(g["txn_group_id"], []).append({
                    "leg_id": g["id"],
                    "side": g.get("side"),
                    "amount": _r(g.get("amount")),
                    "account": (
                        f"{g.get('entity_type')}."
                        f"{g.get('entity_id')}"
                        f"{'.' + g['sub_account'] if g.get('sub_account') else ''}"
                    ),
                    "entry_type": g.get("entry_type"),
                })

        # Build the output rows.
        running = 0.0
        rows: List[Dict[str, Any]] = []
        for e in legs:
            amt = _r(e.get("amount"))
            side = e.get("side")
            # On the receivable: DEBIT raises balance, CREDIT lowers it.
            delta = amt if side == "debit" else -amt
            running = _r(running + delta)

            md = e.get("metadata") or {}
            rows.append({
                "ledger_entry_id": e.get("id"),
                "txn_group_id": e.get("txn_group_id"),
                "transaction_date": e.get("transaction_date"),
                "created_at": e.get("created_at"),
                "created_by": e.get("created_by"),
                "side": side,
                "amount": amt,
                "delta_on_receivable": delta,
                "running_receivable_balance": running,
                "entry_type": e.get("entry_type"),
                "currency": e.get("currency"),
                "description": e.get("description"),
                "source_endpoint": (
                    md.get("created_by_endpoint")
                    or md.get("source")
                    or "unknown"
                ),
                "idempotency_key": md.get("idempotency_key"),
                "all_legs_in_group": groups.get(
                    e.get("txn_group_id") or "", []),
            })

        # Roll-up by entry_type for a quick high-level view.
        rollup_by_type: Dict[str, Dict[str, Any]] = {}
        rollup_by_source: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            et = r["entry_type"] or "unknown"
            src = r["source_endpoint"] or "unknown"
            for bucket, key in ((rollup_by_type, et),
                                (rollup_by_source, src)):
                b = bucket.setdefault(
                    key,
                    {"debit_count": 0, "debit_sum": 0.0,
                     "credit_count": 0, "credit_sum": 0.0})
                if r["side"] == "debit":
                    b["debit_count"] += 1
                    b["debit_sum"] = _r(b["debit_sum"] + r["amount"])
                else:
                    b["credit_count"] += 1
                    b["credit_sum"] = _r(b["credit_sum"] + r["amount"])

        # Detect potential duplicate weekly settlements by scanning
        # CREDIT entries (which close out the receivable).
        suspected_duplicates: List[Dict[str, Any]] = []
        credit_amounts: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            if r["side"] != "credit":
                continue
            key = f"{r['amount']:.2f}"
            credit_amounts.setdefault(key, []).append({
                "ledger_entry_id": r["ledger_entry_id"],
                "transaction_date": r["transaction_date"],
                "entry_type": r["entry_type"],
                "source_endpoint": r["source_endpoint"],
            })
        for amount_key, entries in credit_amounts.items():
            if len(entries) >= 2:
                suspected_duplicates.append({
                    "amount": float(amount_key),
                    "occurrences": len(entries),
                    "entries": entries,
                })

        return {
            "ok": True,
            "iter": "iter246w",
            "provider": prov,
            "read_only": True,
            "filter": {"date_from": date_from, "date_to": date_to},
            "final_receivable_balance": running,
            "entries_count": len(rows),
            "rollup_by_entry_type": rollup_by_type,
            "rollup_by_source_endpoint": rollup_by_source,
            "suspected_duplicate_closures": suspected_duplicates,
            "entries": rows,
        }

    return router
