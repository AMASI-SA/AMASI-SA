"""Iter-246v — Tamara receivable composition diagnostic (READ-ONLY).

When the BNPL settlement bridge rejects a save because
"إجمالي التسوية يتجاوز الرصيد المستحق على tamara", the merchant
has no idea WHY the receivable is lower than the iter246r-correct
Net Sales.  This endpoint surfaces the full composition of the
`payment_gateway.tamara.receivable` ledger balance:

  • All sale postings        →  DEBIT  receivable
  • All refund postings      →  CREDIT receivable
  • All settlement closures  →  CREDIT receivable

Plus a per-period breakdown so we can spot pre-cutoff refunds (that
got posted) whose underlying sales were skipped by the bridge cutoff.

STRICT READ-ONLY.  No writes.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def make_tamara_receivable_diagnostic_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit", "tamara"])

    @router.get("/tamara-receivable-breakdown")
    async def tamara_receivable_breakdown(
        user: dict = Depends(current_user),
        provider: str = Query("tamara"),
    ):
        uid = user["id"]
        prov = (provider or "tamara").lower()

        # 1. Net receivable balance via the SSoT compute.
        from ledger_core import compute_balance
        bal = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id=prov, sub_account="receivable",
        )
        net_balance = _r(bal.get("net_balance", 0))

        # 2. Per-entry-type breakdown.
        pipeline = [
            {"$match": {
                "user_id": uid,
                "entity_type": "payment_gateway",
                "entity_id": prov,
                "sub_account": "receivable",
                "status": "posted",
            }},
            {"$group": {
                "_id": {"entry_type": "$entry_type", "side": "$side"},
                "count": {"$sum": 1},
                "sum": {"$sum": "$amount"},
            }},
            {"$sort": {"_id.entry_type": 1, "_id.side": 1}},
        ]
        rows = []
        async for r in db.general_ledger.aggregate(pipeline):
            rows.append({
                "entry_type": r["_id"]["entry_type"],
                "side": r["_id"]["side"],
                "count": r["count"],
                "sum": _r(r["sum"]),
            })

        total_debit = _r(sum(r["sum"] for r in rows if r["side"] == "debit"))
        total_credit = _r(sum(r["sum"] for r in rows if r["side"] == "credit"))

        # 3. Pre-cutoff drift — refunds posted WHOSE captures were
        # skipped by the bridge cutoff (so their corresponding sale
        # postings are missing).
        cutoff = (os.environ.get("BNPL_BRIDGE_CUTOFF_ISO") or "").strip()
        drift_sum = 0.0
        drift_count = 0
        if cutoff:
            # Walk refund entries on the receivable and join to the
            # underlying capture's `created_at_provider`.
            async for entry in db.general_ledger.find(
                {"user_id": uid,
                 "entity_type": "payment_gateway",
                 "entity_id": prov,
                 "sub_account": "receivable",
                 "entry_type": "bnpl_refund",
                 "side": "credit",
                 "status": "posted"},
                {"_id": 0, "amount": 1, "metadata": 1},
            ):
                md = entry.get("metadata") or {}
                ref_id = (md.get("order_reference_id")
                          or md.get("provider_payment_id"))
                if not ref_id:
                    continue
                cap = await db.payment_transactions.find_one(
                    {"user_id": uid, "provider": prov,
                     "$or": [
                         {"provider_id": ref_id},
                         {"order_reference_id": ref_id},
                     ]},
                    {"_id": 0, "created_at_provider": 1},
                )
                if not cap:
                    continue
                cap_iso = str(cap.get("created_at_provider") or "")
                if cap_iso and cap_iso < cutoff:
                    drift_sum += float(entry.get("amount") or 0)
                    drift_count += 1

        # 4. Inferred diagnosis.
        cause = None
        if drift_sum > 0.01:
            cause = (
                f"Pre-cutoff refund drift detected: {drift_count} refund "
                f"postings totalling {_r(drift_sum)} SAR closed against "
                "receivable WITHOUT their underlying sales being posted "
                "(the captures pre-date BNPL_BRIDGE_CUTOFF_ISO="
                f"{cutoff!r}).  This artificially lowers the receivable "
                "balance below the iter246r-correct Net Sales.  "
                "Fix options: (a) post a one-off 'pre-cutoff adjustment' "
                "ledger entry that DEBITs receivable and CREDITs "
                "'expense.bnpl_pre_accounting_adjustment' for the drift "
                "amount; or (b) tighten BNPL_BRIDGE_CUTOFF_ISO so the "
                "refunds are no longer posted."
            )
        else:
            cause = (
                "No pre-cutoff drift detected.  The receivable shortfall "
                "is most likely from sales that have not yet been booked "
                "by the BNPL → ledger bridge (network/cron lag).  Wait "
                "for the next sync or trigger a manual sales sync."
            )

        return {
            "ok": True,
            "iter": "iter246v",
            "provider": prov,
            "read_only": True,
            "net_receivable_balance": net_balance,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "by_entry_type": rows,
            "bnpl_bridge_cutoff": cutoff or None,
            "pre_cutoff_refund_drift": {
                "count": drift_count,
                "sum": _r(drift_sum),
            },
            "inferred_cause": cause,
        }

    return router
