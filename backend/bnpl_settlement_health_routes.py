"""Iter-246x — BNPL settlement health (READ-ONLY).

`GET /api/audit/bnpl-settlement-health?provider=tamara|tabby|all`

Returns for each provider:
  • current receivable balance (live, computed from general_ledger)
  • last settlement (date, reference, amount, txn_group_id)
  • recent settlements (5 most recent)
  • suspected duplicates (same period saved more than once — should
    never happen after the iter246x period guard, but reported here
    so historical drift is surfaced)
  • per-source breakdown of every entry that touched the receivable
  • alerts (negative receivable, no settlement ever, etc.)

STRICT READ-ONLY.  No writes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query


_PROVIDERS = ("tamara", "tabby")


def _r(n) -> float:
    return round(float(n or 0), 2)


def make_bnpl_settlement_health_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit", "bnpl"])

    async def _provider_health(uid: str, prov: str) -> Dict[str, Any]:
        from ledger_core import compute_balance
        bal = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id=prov, sub_account="receivable",
        )
        receivable = _r(bal.get("net_balance", 0))

        # Per-source rollup on the receivable.
        source_rollup: Dict[str, Dict[str, Any]] = {}
        async for e in db.general_ledger.find(
            {"user_id": uid,
             "entity_type": "payment_gateway",
             "entity_id": prov,
             "sub_account": "receivable",
             "status": "posted"},
            {"_id": 0, "side": 1, "amount": 1, "metadata": 1,
             "entry_type": 1},
        ):
            md = e.get("metadata") or {}
            src = (
                md.get("created_by_endpoint")
                or md.get("source")
                or e.get("entry_type")
                or "unknown"
            )
            b = source_rollup.setdefault(
                src,
                {"debit_count": 0, "debit_sum": 0.0,
                 "credit_count": 0, "credit_sum": 0.0})
            amt = float(e.get("amount") or 0)
            if e.get("side") == "debit":
                b["debit_count"] += 1
                b["debit_sum"] = _r(b["debit_sum"] + amt)
            else:
                b["credit_count"] += 1
                b["credit_sum"] = _r(b["credit_sum"] + amt)

        # Recent settlements (5 most recent posted bnpl_settlement
        # txn_groups for this provider).
        recent: List[Dict[str, Any]] = []
        async for e in db.general_ledger.find(
            {"user_id": uid,
             "entry_type": "bnpl_settlement",
             "status": "posted",
             "metadata.provider": prov,
             "entity_type": "payment_gateway",
             "entity_id": prov,
             "sub_account": "receivable",
             "side": "credit"},
            {"_id": 0, "txn_group_id": 1, "amount": 1,
             "transaction_date": 1, "created_at": 1, "metadata": 1},
        ).sort([("created_at", -1)]).limit(5):
            md = e.get("metadata") or {}
            recent.append({
                "txn_group_id": e.get("txn_group_id"),
                "transaction_date": e.get("transaction_date"),
                "created_at": e.get("created_at"),
                "settlement_reference": md.get("settlement_reference"),
                "settlement_date": md.get("settlement_date"),
                "amount_closed": _r(e.get("amount")),
                "period_from": md.get("period_from") or "",
                "period_to": md.get("period_to") or "",
            })
        last_settlement = recent[0] if recent else None

        # Suspected duplicate periods — pairs of bnpl_settlement
        # txn_groups that share the same (period_from, period_to).
        dup_buckets: Dict[str, List[Dict[str, Any]]] = {}
        async for e in db.general_ledger.find(
            {"user_id": uid,
             "entry_type": "bnpl_settlement",
             "status": "posted",
             "metadata.provider": prov,
             "side": "credit"},
            {"_id": 0, "txn_group_id": 1, "amount": 1,
             "transaction_date": 1, "metadata": 1},
        ):
            md = e.get("metadata") or {}
            pf, pt = md.get("period_from"), md.get("period_to")
            if pf and pt:
                dup_buckets.setdefault(f"{pf}__{pt}", []).append({
                    "txn_group_id": e.get("txn_group_id"),
                    "settlement_reference": md.get("settlement_reference"),
                    "amount_closed": _r(e.get("amount")),
                    "transaction_date": e.get("transaction_date"),
                })
        duplicates: List[Dict[str, Any]] = [
            {"period_from": k.split("__")[0],
             "period_to": k.split("__")[1],
             "occurrences": len(v),
             "entries": v}
            for k, v in dup_buckets.items() if len(v) >= 2
        ]

        # Alerts
        alerts: List[str] = []
        if receivable < -0.01:
            alerts.append(
                f"⚠️ رصيد {prov} سالب ({receivable}) — مرتجعات أكثر "
                "من مبيعات مُرحَّلة. تحقّق من cutoff الجسر."
            )
        if not recent:
            alerts.append(
                f"ℹ️ لا توجد تسويات سابقة مُسجَّلة لـ {prov} حتى الآن."
            )
        if duplicates:
            alerts.append(
                f"🔴 يوجد {len(duplicates)} فترة مكررة على {prov} "
                "(تسوية مُسجَّلة مرتين لنفس الفترة)."
            )

        return {
            "provider": prov,
            "current_receivable_balance": receivable,
            "last_settlement": last_settlement,
            "recent_settlements": recent,
            "duplicate_period_settlements": duplicates,
            "rollup_by_source_endpoint": source_rollup,
            "alerts": alerts,
        }

    @router.get("/bnpl-settlement-health")
    async def bnpl_settlement_health(
        user: dict = Depends(current_user),
        provider: str = Query(
            "all", pattern=r"^(tamara|tabby|all)$"),
    ):
        uid = user["id"]
        providers = (
            list(_PROVIDERS)
            if (provider or "all").lower() == "all"
            else [provider.lower()]
        )
        out = []
        for p in providers:
            out.append(await _provider_health(uid, p))
        return {
            "ok": True,
            "iter": "iter246x",
            "read_only": True,
            "providers": out,
        }

    return router
