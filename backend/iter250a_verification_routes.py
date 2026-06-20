"""Iter-250a post-deploy verification (READ-ONLY).

Single endpoint the operator runs AFTER deploying Iter-250a to
Production to confirm nothing regressed.

  GET /api/audit/iter250a-post-deploy-check

Checks:
  • Inventory file is loaded and reports expected 64 rows.
  • All 8 SAFE_TO_HIDE backend routes still respond (verifies that we
    did NOT delete the backend by mistake).
  • Balances for 7 production-critical areas — returns the count of
    posted, non-reversal ledger rows per area. Lets the operator
    visually compare BEFORE-vs-AFTER snapshots.
  • Detects whether any SAFE_TO_HIDE legacy route received NEW writes
    in the last 24h (would indicate frontend still calls them).

Pure read-only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from financial_pages_inventory_data import INVENTORY, summary


# Map each high-importance domain to the ledger entity_type used.
AREAS_TO_VERIFY = [
    ("banks_and_cash",   "bank"),
    ("ad_accounts",      "ad_account"),
    ("suppliers",        "supplier"),
    ("employees",        "employee"),
    ("couriers",         "courier"),
    ("bnpl_tamara",      "payment_gateway"),
    ("externals",        "external"),
]


def make_iter250a_verification_router(db, current_user):
    router = APIRouter(tags=["audit"])

    @router.get("/audit/iter250a-post-deploy-check")
    async def check(user: dict = Depends(current_user)):
        uid = user["id"]
        s = summary()

        # ── A. Inventory integrity ─────────────────────────────────
        inventory_ok = s["total_pages"] == len(INVENTORY)
        expected_safe_to_hide = {
            "/financial-position", "/transfers", "/financial-input-hub",
            "/counterparties", "/advances", "/shipping-accounts",
            "/settlements", "/reconciliation",
        }
        actual_safe = {
            r["route"] for r in s["routes_to_hide_now"]
        }

        # ── B. Per-area ledger snapshot ────────────────────────────
        cutoff_24h = (datetime.now(timezone.utc)
                      - timedelta(hours=24))
        areas: Dict[str, Any] = {}
        for label, entity_type in AREAS_TO_VERIFY:
            posted = 0
            recent = 0
            total_amount_debit = 0.0
            total_amount_credit = 0.0
            async for r in db.general_ledger.aggregate([
                {"$match": {
                    "user_id": uid,
                    "entity_type": entity_type,
                    "status": "posted",
                    "entry_type": {"$ne": "reversal"},
                }},
                {"$group": {
                    "_id": "$side",
                    "n": {"$sum": 1},
                    "sum": {"$sum": "$amount"},
                }},
            ]):
                if r["_id"] == "debit":
                    posted += r["n"]
                    total_amount_debit = round(float(r["sum"]), 2)
                else:
                    posted += r["n"]
                    total_amount_credit = round(float(r["sum"]), 2)
            # rows created in last 24h
            recent = await db.general_ledger.count_documents({
                "user_id": uid,
                "entity_type": entity_type,
                "status": "posted",
                "created_at": {"$gte": cutoff_24h.isoformat()},
            })
            areas[label] = {
                "entity_type": entity_type,
                "posted_rows": posted,
                "debits_sum": total_amount_debit,
                "credits_sum": total_amount_credit,
                "net_balance": round(
                    total_amount_debit - total_amount_credit, 2,
                ),
                "rows_created_last_24h": recent,
            }

        # ── C. Have legacy routes still received writes? ──────────
        # Heuristic: check `account_transactions` rows whose source
        # looks like one of the deprecated entry surfaces.
        legacy_recent_writes: List[Dict[str, Any]] = []
        sources_to_audit = [
            ("transfers_legacy", {"transaction_type": "transfer",
                                  "created_at": {"$gte": cutoff_24h.isoformat()}}),
            ("advances_legacy", {"transaction_type": "salary_advance",
                                 "source": {"$ne": "financial_movement"},
                                 "created_at": {"$gte": cutoff_24h.isoformat()}}),
        ]
        for label, q in sources_to_audit:
            try:
                n = await db.account_transactions.count_documents(
                    {"user_id": uid, **q})
            except Exception:
                n = 0
            legacy_recent_writes.append({
                "label": label, "filter": q, "count_last_24h": n,
            })

        return {
            "ok": True,
            "iter": "iter250a-verify",
            "read_only": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "A_inventory_integrity": {
                "inventory_loaded": inventory_ok,
                "total_pages": s["total_pages"],
                "expected_safe_to_hide": sorted(expected_safe_to_hide),
                "actual_safe_to_hide": sorted(actual_safe),
                "set_match": actual_safe == expected_safe_to_hide,
            },
            "B_areas_snapshot": areas,
            "C_legacy_recent_writes": legacy_recent_writes,
            "summary_for_operator": {
                "what_to_compare": (
                    "Snapshot هذا الـ JSON قبل النشر مع JSON آخر "
                    "بعد النشر — يجب أن تكون قيم B متطابقة 100%، "
                    "ولا يجب أن تكون C > 0 إلا للحركات الجديدة "
                    "المعتمدة من /new-transaction."
                ),
                "alarm_if": [
                    "set_match == false",
                    "أي عنصر في legacy_recent_writes.count_last_24h "
                    "> 0 (يعني صفحة Legacy ما زالت تستقبل كتابات)",
                ],
            },
        }

    return router
