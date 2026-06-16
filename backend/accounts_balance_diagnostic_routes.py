"""Iter-238 — Account balance reconciliation diagnostic (READ-ONLY).

Zero writes, zero modifications.

Surfaces, per account:
  • stored_balance              — accounts.current_balance
  • computed_from_transactions  — Σ in − Σ out across account_transactions
  • computed_from_ledger        — net from general_ledger SSOT
  • ssot_balance                — what /accounts actually shows the user
  • discrepancy: stored vs computed vs ssot
  • last 10 transactions in account_transactions
  • last 10 ledger entries for this account
  • Indicates which "source" the UI is actually trusting.

Mounted at: GET /api/audit/accounts-balance-diagnostic
"""
from __future__ import annotations
import os
from typing import Optional

from fastapi import APIRouter, Depends, Query


def _r(v) -> float:
    return round(float(v or 0), 2)


def make_accounts_balance_diagnostic_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("/accounts-balance-diagnostic")
    async def accounts_balance_diagnostic(
        account_name_contains: Optional[str] = Query(None),
        account_id: Optional[str] = Query(None),
        limit_transactions: int = Query(10, ge=1, le=100),
        only_mismatches: bool = Query(False),
        today_only: bool = Query(False),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        # ── DB source identification ─────────────────────────────────
        from os import environ as _env
        db_source = {
            "mongo_url_host": (
                _env.get("MONGO_URL", "").split("@")[-1].split("/")[0]
                if "@" in _env.get("MONGO_URL", "")
                else _env.get("MONGO_URL", "").split("/")[2]
                if "://" in _env.get("MONGO_URL", "")
                else "unknown"
            ),
            "db_name": _env.get("DB_NAME"),
        }

        # ── Build the account filter ─────────────────────────────────
        acc_filter: dict = {"user_id": uid}
        if account_id:
            acc_filter["id"] = account_id
        if account_name_contains:
            acc_filter["name"] = {
                "$regex": account_name_contains, "$options": "i",
            }

        # ── Today's date for the "today_only" filter ─────────────────
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()

        # ── Loop every account ───────────────────────────────────────
        from financial_position_ssot import account_balance_ssot

        accounts_out: list[dict] = []
        async for acc in db.accounts.find(acc_filter, {"_id": 0}):
            acc_id = acc["id"]
            stored = _r(acc.get("current_balance"))

            # ▸ Compute from account_transactions only.
            txn_q = {"user_id": uid, "account_id": acc_id}
            sum_in = 0.0
            sum_out = 0.0
            txn_count = 0
            today_count = 0
            async for t in db.account_transactions.find(
                txn_q,
                {"_id": 0, "amount": 1, "direction": 1,
                 "transaction_date": 1, "created_at": 1},
            ):
                txn_count += 1
                amt = float(t.get("amount") or 0)
                if t.get("direction") == "in":
                    sum_in += amt
                else:
                    sum_out += amt
                if (t.get("transaction_date") == today
                        or str(t.get("created_at") or "")[:10] == today):
                    today_count += 1
            computed_from_txns = _r(
                _r(acc.get("expected_orders_balance"))
                + sum_in - sum_out
            )

            # ▸ Compute from general_ledger.
            from financial_position_ssot import compute_balance
            try:
                bal = await compute_balance(
                    db, user_id=uid, entity_type="bank",
                    entity_id=acc_id, sub_account="main",
                )
                ledger_net = _r(bal.get("net_balance"))
            except Exception:
                ledger_net = 0.0
            ledger_entries_count = await db.general_ledger.count_documents({
                "user_id": uid, "entity_type": "bank",
                "entity_id": acc_id, "status": "posted",
            })
            has_opening = bool(await db.general_ledger.find_one({
                "user_id": uid, "entity_type": "bank",
                "entity_id": acc_id,
                "entry_type": "opening_balance",
                "status": "posted",
            }))

            # ▸ SSOT — exactly what the /accounts page shows.
            ssot_balance = _r(await account_balance_ssot(
                db, user_id=uid, account=acc,
            ))

            # ▸ Determine which source the UI is trusting.
            if ledger_entries_count > 0:
                if has_opening:
                    ui_source = "general_ledger_only"
                else:
                    ui_source = "general_ledger_plus_current_balance_as_opening"
            else:
                ui_source = "legacy_current_balance"

            # ▸ Last N transactions in account_transactions.
            last_txns = []
            async for t in db.account_transactions.find(
                txn_q, {"_id": 0, "id": 1, "amount": 1,
                        "direction": 1, "transaction_type": 1,
                        "description": 1, "transaction_date": 1,
                        "balance_after": 1, "created_at": 1,
                        "metadata": 1},
            ).sort([("created_at", -1)]).limit(limit_transactions):
                last_txns.append({
                    "id": t.get("id"),
                    "amount": _r(t.get("amount")),
                    "direction": t.get("direction"),
                    "type": t.get("transaction_type"),
                    "description": (t.get("description") or "")[:80],
                    "transaction_date": t.get("transaction_date"),
                    "balance_after": t.get("balance_after"),
                    "created_at": t.get("created_at"),
                    "idempotency_key": (t.get("metadata") or {})
                                       .get("idempotency_key"),
                })

            # ▸ Last N ledger entries for this account.
            last_ledger = []
            async for e in db.general_ledger.find(
                {"user_id": uid, "entity_type": "bank",
                 "entity_id": acc_id, "status": "posted"},
                {"_id": 0, "id": 1, "entry_type": 1, "side": 1,
                 "amount": 1, "posted_at": 1, "txn_group_id": 1,
                 "notes": 1},
            ).sort([("posted_at", -1)]).limit(limit_transactions):
                last_ledger.append(e)

            # ▸ Discrepancies.
            disc = {
                "stored_vs_computed_txns": _r(stored - computed_from_txns),
                "stored_vs_ssot": _r(stored - ssot_balance),
                "computed_txns_vs_ssot": _r(computed_from_txns - ssot_balance),
                "ledger_vs_txns": _r(ledger_net - (sum_in - sum_out)),
            }

            row = {
                "account_id": acc_id,
                "account_name": acc.get("name"),
                "account_type": acc.get("account_type"),
                "is_active": acc.get("is_active", True),
                "stored_balance": stored,
                "expected_orders_balance": _r(
                    acc.get("expected_orders_balance"),
                ),
                "txn_aggregate": {
                    "transactions_count": txn_count,
                    "transactions_today_count": today_count,
                    "sum_in": _r(sum_in),
                    "sum_out": _r(sum_out),
                    "computed_balance": computed_from_txns,
                },
                "ledger_aggregate": {
                    "ledger_entries_count": ledger_entries_count,
                    "has_opening_balance_entry": has_opening,
                    "ledger_net": ledger_net,
                },
                "ssot_balance_shown_in_ui": ssot_balance,
                "ui_source": ui_source,
                "discrepancy": disc,
                "stale": any(abs(v) > 0.01 for v in disc.values()),
                "last_transactions": last_txns,
                "last_ledger_entries": last_ledger,
                "updated_at": acc.get("updated_at"),
            }

            if today_only and today_count == 0:
                continue
            if only_mismatches and not row["stale"]:
                continue
            accounts_out.append(row)

        # Sort: largest discrepancy first.
        def _max_abs_disc(r):
            return max(abs(v) for v in r["discrepancy"].values())
        accounts_out.sort(key=_max_abs_disc, reverse=True)

        return {
            "success": True,
            "read_only": True,
            "iteration": "iter238-diagnostic",
            "db_source": db_source,
            "today": today,
            "filters": {
                "account_id": account_id,
                "account_name_contains": account_name_contains,
                "today_only": today_only,
                "only_mismatches": only_mismatches,
                "limit_transactions": limit_transactions,
            },
            "summary": {
                "total_accounts_checked": len(accounts_out),
                "accounts_with_stale_balance": sum(
                    1 for r in accounts_out if r["stale"]
                ),
                "accounts_with_today_activity": sum(
                    1 for r in accounts_out
                    if r["txn_aggregate"]["transactions_today_count"] > 0
                ),
            },
            "accounts": accounts_out,
            "notes": [
                "stored_balance = accounts.current_balance",
                "computed_balance (txn) = expected_orders_balance + sum_in − sum_out",
                "ledger_net = sum of general_ledger debits − credits",
                "ssot_balance_shown_in_ui = what /accounts page renders",
                "Any non-zero discrepancy = an inconsistency.",
                "READ-ONLY: this endpoint never modifies any data.",
            ],
        }

    return router


def make_accounts_balance_repair_preview_router(db, current_user):
    """Iter-239 — Read-only preview of the proposed ledger-side fix.

    For every account whose `account_transactions` aggregate doesn't
    match its `general_ledger` aggregate, this endpoint proposes a
    SINGLE adjustment entry that would post the delta into the ledger
    so the SSOT balance matches the user's manual transactions.

    NET-BASED reconciliation (NOT per-transaction).  Rationale:
      • Avoids double-counting transactions that DO have a matching
        ledger entry (e.g. BNPL settlements that already double-write).
      • Keeps audit history clean — ONE clearly-labeled adjustment per
        account, never per row.
      • Original `account_transactions` stay untouched; the adjustment
        carries `metadata.original_transaction_ids[]` so the auditor
        can trace which rows it covers.
    """
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("/accounts-balance-repair-preview")
    async def accounts_balance_repair_preview(
        account_id: Optional[str] = Query(None),
        account_name_contains: Optional[str] = Query(None),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        from financial_position_ssot import (
            account_balance_ssot, compute_balance,
        )

        acc_filter: dict = {"user_id": uid}
        if account_id:
            acc_filter["id"] = account_id
        if account_name_contains:
            acc_filter["name"] = {
                "$regex": account_name_contains, "$options": "i",
            }

        proposals: list[dict] = []
        async for acc in db.accounts.find(acc_filter, {"_id": 0}):
            acc_id = acc["id"]
            # txn aggregate.
            sum_in = 0.0
            sum_out = 0.0
            txn_ids: list[str] = []
            async for t in db.account_transactions.find(
                {"user_id": uid, "account_id": acc_id},
                {"_id": 0, "id": 1, "amount": 1, "direction": 1},
            ):
                amt = float(t.get("amount") or 0)
                if t.get("direction") == "in":
                    sum_in += amt
                else:
                    sum_out += amt
                if t.get("id"):
                    txn_ids.append(t["id"])
            txn_net = _r(sum_in - sum_out)

            # ledger aggregate.
            try:
                bal = await compute_balance(
                    db, user_id=uid, entity_type="bank",
                    entity_id=acc_id, sub_account="main",
                )
                ledger_net = _r(bal.get("net_balance"))
            except Exception:
                ledger_net = 0.0
            ledger_entries_count = await db.general_ledger.count_documents({
                "user_id": uid, "entity_type": "bank",
                "entity_id": acc_id, "status": "posted",
            })

            # SSOT shown in UI today.
            ssot_now = _r(await account_balance_ssot(
                db, user_id=uid, account=acc,
            ))

            # Already-applied repair (don't double-up).
            existing_repair = await db.general_ledger.find_one({
                "user_id": uid, "entity_type": "bank",
                "entity_id": acc_id,
                "entry_type": "balance_repair",
                "status": "posted",
            }, {"_id": 1, "amount": 1, "side": 1})

            adjustment_needed = _r(txn_net - ledger_net)
            if abs(adjustment_needed) < 0.01:
                continue   # nothing to repair
            # Expected SSOT after repair = current SSOT + adjustment.
            expected_after = _r(ssot_now + adjustment_needed)

            proposals.append({
                "account_id": acc_id,
                "account_name": acc.get("name"),
                "account_type": acc.get("account_type"),
                "current_ssot_balance": ssot_now,
                "txn_net": txn_net,
                "ledger_net": ledger_net,
                "adjustment_needed": adjustment_needed,
                "side_to_post": (
                    "debit" if adjustment_needed > 0 else "credit"
                ),
                "expected_ssot_after_repair": expected_after,
                "transactions_to_link_count": len(txn_ids),
                "ledger_entries_already_present": ledger_entries_count,
                "already_repaired": existing_repair is not None,
                "original_transaction_ids_sample": txn_ids[:20],
                "original_transaction_ids_total": len(txn_ids),
            })

        proposals.sort(key=lambda p: -abs(p["adjustment_needed"]))
        return {
            "success": True,
            "read_only": True,
            "iteration": "iter239-preview",
            "summary": {
                "accounts_with_drift": len(proposals),
                "total_positive_adjustments": _r(sum(
                    p["adjustment_needed"] for p in proposals
                    if p["adjustment_needed"] > 0
                )),
                "total_negative_adjustments": _r(sum(
                    p["adjustment_needed"] for p in proposals
                    if p["adjustment_needed"] < 0
                )),
                "already_repaired_count": sum(
                    1 for p in proposals if p["already_repaired"]
                ),
            },
            "proposals": proposals,
            "notes": [
                "READ-ONLY: no data is modified.",
                "Net-based reconciliation prevents double-counting "
                "transactions that already have matching ledger entries.",
                "One adjustment entry per account will be created on apply.",
                "Adjustment side: debit (asset+) if txn_net > ledger_net.",
                "Counterpart: 'balance_repair' adjustment account "
                "(auto-created on apply).",
            ],
        }

    return router