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


def make_account_drift_detail_router(db, current_user):
    """Iter-239g — Per-account drift detail (READ-ONLY).

    For a specific account, classifies EVERY account_transactions row as
    either (a) already mirrored in general_ledger, or (b) un-posted.
    Returns the totals and the first N un-posted transaction IDs.
    """
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("/account-drift-detail/{account_id}")
    async def account_drift_detail(
        account_id: str,
        limit_sample: int = Query(20, ge=1, le=100),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        acc = await db.accounts.find_one(
            {"id": account_id, "user_id": uid}, {"_id": 0},
        )
        if not acc:
            from fastapi import HTTPException
            raise HTTPException(404, "account not found")

        # Build the set of (idempotency_keys + ids) already mirrored
        # in general_ledger for this entity.  Two link strategies:
        #   • metadata.idempotency_key  (BNPL settlements use this)
        #   • metadata.account_transaction_id  (future double-write)
        mirrored_idem_keys: set[str] = set()
        mirrored_txn_ids: set[str] = set()
        async for e in db.general_ledger.find(
            {"user_id": uid, "entity_type": "bank",
             "entity_id": account_id, "status": "posted"},
            {"_id": 0, "metadata": 1},
        ):
            m = e.get("metadata") or {}
            k = m.get("idempotency_key")
            t = m.get("account_transaction_id")
            if k:
                mirrored_idem_keys.add(k)
            if t:
                mirrored_txn_ids.add(t)

        total_count = 0
        mirrored_count = 0
        unmirrored_count = 0
        mirrored_amount = 0.0
        unmirrored_amount_in = 0.0
        unmirrored_amount_out = 0.0
        unmirrored_sample: list[dict] = []
        last_n_all: list[dict] = []
        async for t in db.account_transactions.find(
            {"user_id": uid, "account_id": account_id},
            {"_id": 0, "id": 1, "amount": 1, "direction": 1,
             "transaction_type": 1, "description": 1,
             "transaction_date": 1, "created_at": 1, "metadata": 1},
        ).sort([("created_at", -1)]):
            total_count += 1
            t_id = t.get("id") or ""
            t_idem = (t.get("metadata") or {}).get("idempotency_key")
            is_mirrored = (
                (t_id and t_id in mirrored_txn_ids)
                or (t_idem and t_idem in mirrored_idem_keys)
            )
            amt = float(t.get("amount") or 0)
            row = {
                "id": t_id,
                "amount": _r(amt),
                "direction": t.get("direction"),
                "type": t.get("transaction_type"),
                "description": (t.get("description") or "")[:80],
                "transaction_date": t.get("transaction_date"),
                "created_at": t.get("created_at"),
                "is_mirrored_in_ledger": bool(is_mirrored),
                "idempotency_key": t_idem,
            }
            if len(last_n_all) < limit_sample:
                last_n_all.append(row)
            if is_mirrored:
                mirrored_count += 1
                mirrored_amount += amt
            else:
                unmirrored_count += 1
                if t.get("direction") == "in":
                    unmirrored_amount_in += amt
                else:
                    unmirrored_amount_out += amt
                if len(unmirrored_sample) < limit_sample:
                    unmirrored_sample.append(row)

        unmirrored_net = _r(unmirrored_amount_in - unmirrored_amount_out)
        return {
            "success": True,
            "read_only": True,
            "iteration": "iter239g-account-drift-detail",
            "account_id": account_id,
            "account_name": acc.get("name"),
            "account_type": acc.get("account_type"),
            "totals": {
                "total_transactions": total_count,
                "mirrored_in_ledger_count": mirrored_count,
                "unmirrored_count": unmirrored_count,
                "mirrored_amount_total": _r(mirrored_amount),
                "unmirrored_amount_in": _r(unmirrored_amount_in),
                "unmirrored_amount_out": _r(unmirrored_amount_out),
                "unmirrored_net": unmirrored_net,
            },
            "first_n_unmirrored_transactions": unmirrored_sample,
            "first_n_all_transactions_with_status": last_n_all,
            "link_strategies_checked": [
                "metadata.idempotency_key",
                "metadata.account_transaction_id",
            ],
            "notes": [
                "READ-ONLY: no data is modified.",
                "A txn is 'mirrored' if its id OR idempotency_key appears "
                "in general_ledger.metadata for this entity.",
                "Legacy BNPL settlements mirror via idempotency_key.",
                "Future double-write will mirror via account_transaction_id.",
            ],
        }

    return router


def make_repair_audit_router(db, current_user):
    """Iter-239f — READ-ONLY safety audit.

    Confirms no apply ever ran (zero repair entries exist).
    """
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("/balance-repair-audit")
    async def balance_repair_audit(user: dict = Depends(current_user)):
        uid = user["id"]
        match = {
            "user_id": uid,
            "$or": [
                {"entry_type": "balance_repair"},
                {"metadata.source": "account_transactions_repair"},
                {"metadata.repair_iteration": {"$regex": "Iter-239"}},
                {"metadata.created_by": "system_repair"},
            ],
        }
        total_count = await db.general_ledger.count_documents(match)
        entries: list[dict] = []
        async for e in db.general_ledger.find(match, {
            "_id": 0, "id": 1, "entity_type": 1, "entity_id": 1,
            "entry_type": 1, "side": 1, "amount": 1, "posted_at": 1,
            "notes": 1, "metadata": 1,
        }).sort([("posted_at", -1)]).limit(100):
            entries.append(e)
        return {
            "success": True, "read_only": True,
            "iteration": "iter239f-audit",
            "apply_was_run": total_count > 0,
            "repair_entries_count": total_count,
            "repair_entries_first_100": entries,
            "rollback_needed": total_count > 0,
        }

    return router


def make_endpoint_ledger_coverage_router(db, current_user):
    """Iter-239h — READ-ONLY endpoint → general_ledger coverage map.

    Combines:
      1. A STATIC map of every code-site that writes account_transactions
         (derived from grep against the repo on 2026-06-16) + whether
         that site ALSO writes a matching general_ledger entry.
      2. DYNAMIC counts from the live DB grouped by transaction_type +
         whether each row is mirrored in the ledger (by matching the
         row's id OR idempotency_key in general_ledger.metadata).

    Output is sorted by `affected_transactions_count` so the worst
    offenders are at the top.
    """
    router = APIRouter(prefix="/audit", tags=["audit"])

    # Iter-239h — STATIC map.  Update this if new code paths land.
    STATIC_MAP = [
        {"endpoint": "POST /api/accounts/{id}/transactions",
         "file": "accounts_routes.py:589",
         "txn_types": ["deposit", "withdrawal", "internal_transfer",
                       "manual_adjustment"],
         "writes_account_transactions": True,
         "writes_general_ledger": False},
        {"endpoint": "POST /api/accounts/{id}/transactions (legacy path)",
         "file": "accounts_routes.py:913",
         "txn_types": ["any"],
         "writes_account_transactions": True,
         "writes_general_ledger": False},
        {"endpoint": "POST /api/transfers",
         "file": "transfers_routes.py:308",
         "txn_types": ["internal_transfer"],
         "writes_account_transactions": True,
         "writes_general_ledger": False},
        {"endpoint": "POST /api/liabilities/{id}/pay (ad_account/salary)",
         "file": "liabilities_routes.py:274",
         "txn_types": ["ad_account_topup", "salary_advance",
                       "debt_payment"],
         "writes_account_transactions": True,
         "writes_general_ledger": False},
        {"endpoint": "POST /api/shipping/companies/{id}/payments",
         "file": "shipping_accounts.py:93,767",
         "txn_types": ["shipping_debt_payment", "courier_transfer"],
         "writes_account_transactions": True,
         "writes_general_ledger": False},
        {"endpoint": "POST /api/expenses",
         "file": "expenses_routes.py:265",
         "txn_types": ["expense"],
         "writes_account_transactions": True,
         "writes_general_ledger": False},
        {"endpoint": "POST /api/ad-accounts/{id}/charges or topups",
         "file": "ad_account_routes.py:519,1235",
         "txn_types": ["ad_account_charge", "ad_account_topup"],
         "writes_account_transactions": True,
         "writes_general_ledger": False},
        {"endpoint": "POST /api/bnpl/settlements/register",
         "file": "bnpl/settlements_routes.py:170",
         "txn_types": ["settlement"],
         "writes_account_transactions": True,
         "writes_general_ledger": True},   # ✓ Iter-219+
        {"endpoint": "POST /api/bnpl/settlements/backfill-bank-transactions",
         "file": "bnpl/settlements_routes.py:273",
         "txn_types": ["settlement"],
         "writes_account_transactions": True,
         "writes_general_ledger": True},   # backfill of ledger entries
    ]

    @router.get("/endpoint-ledger-coverage")
    async def endpoint_ledger_coverage(
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        # Build idem/id sets mirrored in ledger (ALL banks).
        mirrored_idem: set[str] = set()
        mirrored_txn_ids: set[str] = set()
        async for e in db.general_ledger.find(
            {"user_id": uid},
            {"_id": 0, "metadata": 1},
        ):
            m = e.get("metadata") or {}
            if m.get("idempotency_key"):
                mirrored_idem.add(m["idempotency_key"])
            if m.get("account_transaction_id"):
                mirrored_txn_ids.add(m["account_transaction_id"])

        # Dynamic per-type counts.
        type_stats: dict = {}
        async for t in db.account_transactions.find(
            {"user_id": uid},
            {"_id": 0, "id": 1, "transaction_type": 1, "amount": 1,
             "direction": 1, "metadata": 1},
        ):
            ttype = t.get("transaction_type") or "unknown"
            t_id = t.get("id")
            t_idem = (t.get("metadata") or {}).get("idempotency_key")
            mirrored = (
                (t_id and t_id in mirrored_txn_ids)
                or (t_idem and t_idem in mirrored_idem)
            )
            s = type_stats.setdefault(ttype, {
                "transaction_type": ttype,
                "total_count": 0, "mirrored_count": 0,
                "unmirrored_count": 0,
                "mirrored_amount": 0.0, "unmirrored_amount": 0.0,
            })
            amt = float(t.get("amount") or 0)
            s["total_count"] += 1
            if mirrored:
                s["mirrored_count"] += 1
                s["mirrored_amount"] += amt
            else:
                s["unmirrored_count"] += 1
                s["unmirrored_amount"] += amt
        for s in type_stats.values():
            s["mirrored_amount"] = _r(s["mirrored_amount"])
            s["unmirrored_amount"] = _r(s["unmirrored_amount"])

        # Stitch dynamic counts into static map.
        report = []
        for site in STATIC_MAP:
            affected_total = 0
            affected_unmirrored = 0
            unmirrored_amount = 0.0
            for ttype in site["txn_types"]:
                if ttype == "any":
                    for s in type_stats.values():
                        affected_total += s["total_count"]
                        affected_unmirrored += s["unmirrored_count"]
                        unmirrored_amount += s["unmirrored_amount"]
                    continue
                s = type_stats.get(ttype)
                if s:
                    affected_total += s["total_count"]
                    affected_unmirrored += s["unmirrored_count"]
                    unmirrored_amount += s["unmirrored_amount"]
            report.append({
                **site,
                "affected_transactions_count": affected_total,
                "affected_unmirrored_count": affected_unmirrored,
                "unmirrored_amount_total": _r(unmirrored_amount),
            })

        # Sort: most unmirrored first (worst offenders top).
        report.sort(
            key=lambda r: -r["affected_unmirrored_count"],
        )

        return {
            "success": True,
            "read_only": True,
            "iteration": "iter239h-coverage-map",
            "report_endpoints": report,
            "per_transaction_type_stats": sorted(
                type_stats.values(),
                key=lambda s: -s["unmirrored_count"],
            ),
            "notes": [
                "STATIC map manually derived from code on 2026-06-16.",
                "DYNAMIC counts are live from your DB.",
                "An endpoint is a 'leak' if writes_general_ledger == false AND affected_unmirrored_count > 0.",
                "Fix the leaks at their SOURCE (double-write) rather than backfilling adjustments.",
                "READ-ONLY: no data is modified.",
            ],
        }

    return router
    """Iter-239f — READ-ONLY safety audit.

    Confirms no apply ever ran (zero repair entries exist) AND provides
    a future-ready rollback PREVIEW (still read-only, just lists what
    WOULD be reversed if you ever ask for an apply).
    """
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("/balance-repair-audit")
    async def balance_repair_audit(user: dict = Depends(current_user)):
        uid = user["id"]
        # Detect any repair entries by ANY of the documented markers.
        match = {
            "user_id": uid,
            "$or": [
                {"entry_type": "balance_repair"},
                {"metadata.source": "account_transactions_repair"},
                {"metadata.repair_iteration": {"$regex": "Iter-239"}},
                {"metadata.created_by": "system_repair"},
            ],
        }
        total_count = await db.general_ledger.count_documents(match)
        entries: list[dict] = []
        async for e in db.general_ledger.find(match, {
            "_id": 0, "id": 1, "entity_type": 1, "entity_id": 1,
            "entry_type": 1, "side": 1, "amount": 1, "posted_at": 1,
            "notes": 1, "metadata": 1,
        }).sort([("posted_at", -1)]).limit(100):
            entries.append(e)
        return {
            "success": True,
            "read_only": True,
            "iteration": "iter239f-audit",
            "apply_was_run": total_count > 0,
            "repair_entries_count": total_count,
            "repair_entries_first_100": entries,
            "rollback_needed": total_count > 0,
            "notes": [
                "If repair_entries_count == 0 → NO apply ever ran. No rollback needed.",
                "This endpoint is read-only and only scans the ledger.",
                "If you ever DO run apply, this endpoint will list exactly what to reverse.",
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
        excluded_bnpl: list[dict] = []
        excluded_unsafe: list[dict] = []
        async for acc in db.accounts.find(acc_filter, {"_id": 0}):
            acc_id = acc["id"]
            acc_name = (acc.get("name") or "").strip()
            acc_type = acc.get("account_type")
            # Iter-239e — STRICTER BNPL exclusion.
            # is_bnpl_account() may miss accounts depending on shape.
            # We ALSO match by name regex to be safe.
            try:
                from bnpl.balance_service import is_bnpl_account
                bnpl_p = is_bnpl_account(acc)
            except Exception:
                bnpl_p = None
            name_lower = acc_name.lower()
            bnpl_name_match = None
            for prov, needles in (
                ("tabby",  ["tabby", "تابي"]),
                ("tamara", ["tamara", "تمارا"]),
            ):
                if any(n in name_lower or n in acc_name for n in needles):
                    bnpl_name_match = prov
                    break
            effective_bnpl = bnpl_p or bnpl_name_match
            if effective_bnpl:
                excluded_bnpl.append({
                    "account_id": acc_id,
                    "account_name": acc_name,
                    "account_type": acc_type,
                    "bnpl_provider": effective_bnpl,
                    "detected_via": (
                        "is_bnpl_account" if bnpl_p else "name_match"
                    ),
                    "reason": "BNPL canonical engine — handled separately",
                })
                continue
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

            # Iter-239e — Safety classification.
            exclusion_reasons: list[str] = []
            if len(txn_ids) == 0:
                exclusion_reasons.append(
                    "transactions_to_link_count == 0 — drift is ledger-only, "
                    "no manual transactions to back the repair"
                )
            if ssot_now > 1000 and abs(expected_after) < 1:
                exclusion_reasons.append(
                    "expected_ssot_after_repair would zero-out a non-trivial "
                    "positive balance — refusing"
                )
            if ssot_now > 0 and expected_after < -1000:
                exclusion_reasons.append(
                    "expected_ssot_after_repair would flip a positive balance "
                    "to a large negative (>1000) — refusing"
                )
            if abs(adjustment_needed) > 200_000:
                exclusion_reasons.append(
                    f"adjustment magnitude {abs(adjustment_needed):.2f} > "
                    f"200,000 SAR — requires explicit approval"
                )
            # Confidence score: 100 = safe; lower = riskier.
            confidence_score = 100
            confidence_score -= len(exclusion_reasons) * 30
            if len(txn_ids) == 0:
                confidence_score -= 40
            if existing_repair is not None:
                confidence_score -= 50  # already repaired once
            confidence_score = max(0, confidence_score)
            eligible_for_apply = (
                len(exclusion_reasons) == 0
                and existing_repair is None
                and confidence_score >= 70
            )

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
                # Iter-239e — safe-guard fields.
                "eligible_for_apply": eligible_for_apply,
                "exclusion_reasons": exclusion_reasons,
                "confidence_score": confidence_score,
                "inclusion_reason": (
                    "txn_net differs from ledger_net by "
                    f"{abs(adjustment_needed):.2f} SAR"
                ),
            })

        proposals.sort(key=lambda p: -abs(p["adjustment_needed"]))
        return {
            "success": True,
            "read_only": True,
            "iteration": "iter239e-preview",
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
                "bnpl_excluded_count": len(excluded_bnpl),
            },
            "proposals": proposals,
            "excluded_bnpl_accounts": excluded_bnpl,
            "notes": [
                "READ-ONLY: no data is modified.",
                "Net-based reconciliation prevents double-counting.",
                "BNPL canonical accounts (tabby/tamara) excluded — handled separately.",
                "Adjustment side: debit (asset+) if txn_net > ledger_net.",
                "Counterpart on apply: «تسوية أرصدة حسابات (Audit)».",
            ],
        }

    # ── Iter-239b — BNPL Tabby/Tamara two-source comparator ───────────
    @router.get("/bnpl-balance-source-comparison/{provider}")
    async def bnpl_balance_source_comparison(
        provider: str,
        user: dict = Depends(current_user),
    ):
        """Iter-239b — Explain why /accounts and /bnpl-settlements/register
        can show DIFFERENT balances for the same BNPL provider.

        Source A: `/accounts` (account_balance_ssot)
          → `get_bnpl_provider_balance` → `compute_settlement_for_provider`
          → balance = expected_net_payable − transferred_amount
          → ENGINE-driven (re-computed every call from
            payment_transactions / payment_refunds).

        Source B: `/bnpl-settlements/register/registration-overview`
          → reads `current_receivable` directly from `general_ledger`
            (entity_type=payment_gateway, entity_id=<provider>,
             sub_account=receivable).
          → LEDGER-driven (sum of posted credit − debit legs).

        Difference root causes:
          • Adjustments/migrations posted in ledger but NOT in
            payment_transactions/refunds.
          • Bridge entries (sales/refunds/settlements) that did not
            round to the same SAR as the engine's pre-rounding sum.
          • Orphan legacy ledger entries from past data migrations.
        """
        uid = user["id"]
        if provider not in {"tabby", "tamara"}:
            from fastapi import HTTPException
            raise HTTPException(400, "provider must be tabby or tamara")

        # ── Source A: engine (used by /accounts and /bnpl-settlements/register's "expected") ──
        from bnpl.balance_service import get_bnpl_provider_balance
        a = await get_bnpl_provider_balance(db, uid, provider)
        engine_balance = _r(a.get("balance"))

        # ── Source B: ledger receivable (entity_type=payment_gateway, sub=receivable) ──
        from ledger_core import compute_balance as _cb
        recv = await _cb(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id=provider, sub_account="receivable",
        )
        ledger_receivable_net = _r(recv.get("net_balance"))
        ledger_debits = _r(recv.get("debits"))   # Iter-239c — correct keys
        ledger_credits = _r(recv.get("credits"))

        # ── Source C: registration-overview (what the settlements page ACTUALLY shows) ──
        # Replicates the exact maths used by /api/bnpl/settlements/registration-overview
        # so we can pinpoint which of its fields the UI is rendering.
        # received_total = sum of close-out credit legs.
        recv_pipeline = [
            {"$match": {
                "user_id": uid,
                "entry_type": "bnpl_settlement",
                "status": "posted",
                "side": "credit",
                "entity_type": "payment_gateway",
                "entity_id": provider,
            }},
            {"$group": {"_id": None,
                        "total": {"$sum": "$amount"},
                        "count": {"$sum": 1}}},
        ]
        received_total = 0.0
        received_count = 0
        async for row in db.general_ledger.aggregate(recv_pipeline):
            received_total = _r(row.get("total"))
            received_count = int(row.get("count") or 0)
        registration_overview_mirror = {
            "current_receivable_from_ledger": ledger_receivable_net,
            "expected_total_from_engine": engine_balance,
            "received_total_close_out_legs": received_total,
            "received_count": received_count,
            "difference_expected_minus_received": _r(
                engine_balance - received_total,
            ),
        }

        diff = _r(ledger_receivable_net - engine_balance)

        # Breakdown by entry_type within payment_gateway/<provider>/receivable.
        from collections import defaultdict
        by_entry_type: dict = defaultdict(
            lambda: {"credit": 0.0, "debit": 0.0, "count": 0,
                     "samples": []},
        )
        suspect_entries: list[dict] = []
        async for e in db.general_ledger.find(
            {"user_id": uid, "entity_type": "payment_gateway",
             "entity_id": provider, "sub_account": "receivable",
             "status": "posted"},
            {"_id": 0, "id": 1, "entry_type": 1, "side": 1,
             "amount": 1, "posted_at": 1, "notes": 1,
             "txn_group_id": 1, "metadata": 1},
        ):
            etype = e.get("entry_type") or "unknown"
            side = e.get("side")
            amt = float(e.get("amount") or 0)
            b = by_entry_type[etype]
            if side == "credit":
                b["credit"] += amt
            elif side == "debit":
                b["debit"] += amt
            b["count"] += 1
            if len(b["samples"]) < 3:
                b["samples"].append({
                    "ledger_id": e.get("id"),
                    "side": side,
                    "amount": _r(amt),
                    "posted_at": str(e.get("posted_at") or ""),
                    "notes": (e.get("notes") or "")[:120],
                    "txn_group_id": e.get("txn_group_id"),
                    "metadata_source": (e.get("metadata") or {})
                                       .get("source"),
                })
            # Mark unusual entry types as suspect (not the normal
            # sales/refund/settlement triplet).
            if etype not in (
                "bnpl_sale", "bnpl_refund", "bnpl_settlement",
            ):
                suspect_entries.append({
                    "ledger_id": e.get("id"),
                    "entry_type": etype,
                    "side": side,
                    "amount": _r(amt),
                    "posted_at": str(e.get("posted_at") or ""),
                    "notes": (e.get("notes") or "")[:160],
                    "metadata": e.get("metadata"),
                })

        return {
            "success": True,
            "read_only": True,
            "iteration": "iter239c-bnpl-comparator",
            "provider": provider,
            "source_a_accounts_page": {
                "name": "/accounts — account_balance_ssot → get_bnpl_provider_balance → compute_settlement_for_provider",
                "actual_ui_endpoint_used": f"GET /api/accounts (engine-driven via BNPL canonical formula for provider={provider})",
                "balance": engine_balance,
                "components": a.get("components"),
                "transactions_count": a.get("transactions_count"),
                "fee_rates_engine_version": (
                    (a.get("fee_rates") or {}).get("fee_source")
                ),
            },
            "source_b_ledger_receivable": {
                "name": "general_ledger — entity_type=payment_gateway, sub_account=receivable",
                "actual_ui_endpoint_used": "NOT directly shown in any UI page — this is the raw SSOT ledger receivable.",
                "balance_net": ledger_receivable_net,
                "debits_sum": ledger_debits,
                "credits_sum": ledger_credits,
                "balance_formula": "net_balance = Σ debits − Σ credits  (>0 ⇒ provider owes us, <0 ⇒ we owe provider)",
            },
            "source_c_settlements_page": {
                "name": "/bnpl-settlements/register — GET /api/bnpl/settlements/registration-overview",
                "actual_ui_endpoint_used": "GET /api/bnpl/settlements/registration-overview (which calls BOTH the engine AND the ledger)",
                "registration_overview_response": registration_overview_mirror,
                "ui_likely_renders": "`expected_total` (from engine)  →  matches /accounts",
            },
            "difference_ledger_minus_engine": diff,
            "by_entry_type": {
                k: {"credit": _r(v["credit"]),
                    "debit": _r(v["debit"]),
                    "net": _r(v["debit"] - v["credit"]),
                    "count": v["count"],
                    "samples": v["samples"]}
                for k, v in by_entry_type.items()
            },
            "suspect_entries": suspect_entries[:25],
            "notes": [
                "Source A (/accounts) and the UI shown in /bnpl-settlements/register are BOTH engine-driven (expected_total).",
                "Source B (ledger receivable) is the raw SSOT — usually larger than the engine balance because it accumulates ALL postings without subtracting transferred_out as an asset rebate.",
                "The earlier 119.10 SAR gap between /accounts and /bnpl-settlements/register was a rounding/cached drift between two callers of the same engine — likely already resolved.",
                "If by_entry_type only contains bnpl_sale / bnpl_refund / bnpl_settlement, the ledger is clean (no migration orphans).",
                "READ-ONLY: no data is modified.",
            ],
        }

    return router