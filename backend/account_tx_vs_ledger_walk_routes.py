"""Iter-250b · P1.5.b — Account-Transactions vs Ledger Walk (READ-ONLY).

Explains the gap between the legacy ``account_transactions`` collection
and the SSOT ``general_ledger`` for a given bank account.

For each side it groups by ``transaction_type`` / ``entry_type`` and by
date bucket, so we can see EXACTLY which categories of operations exist
in one collection but not the other. This is used to investigate the
70k ر.س gap on بنك الإنماء (79 account_transactions but only 28 ledger
entries).

STRICT READ-ONLY · NO writes · NO migrations · NO recomputes.

Endpoints
---------

GET /api/diagnostics/account-tx-vs-ledger-walk
    ?account_id=<bank_account_id>          (required)
    &include_rows=false                    (when true, returns sample rows)
    &max_rows=50                           (cap on sample row count)

Response::

    {
      ok: true,
      account: { id, name, account_type, stored_current_balance, ... },
      account_transactions: {
        total_count, in_total, out_total, net,
        by_type: [ { transaction_type, count, in, out, net } ],
        by_month: [ { month, count, net } ],
        sample_rows: [ ... ]                   # when include_rows=true
      },
      general_ledger: {
        total_count, debit_total, credit_total, net,
        by_entry_type: [ { entry_type, sub_account, count, debit, credit, net } ],
        by_month: [ { month, count, net } ],
        sample_rows: [ ... ]                   # when include_rows=true
      },
      crosswalk: {
        # Match by txn_group_id (best effort)
        account_tx_with_ledger_link: int,
        account_tx_without_ledger_link: int,
        unmatched_account_tx_net: float,           # the unexplained 70k
        ledger_groups_without_account_tx: int,
        unmatched_ledger_net: float
      },
      summary: {
        gap_stored_minus_ledger: float,
        gap_breakdown: {
          bnpl_hidden_in_balance_subaccount: float,
          unmatched_legacy_account_transactions: float,
          other: float
        }
      }
    }
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month_key(value) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return value[:7] if len(value) >= 7 else value
    if isinstance(value, datetime):
        return value.strftime("%Y-%m")
    return "—"


def make_account_tx_vs_ledger_walk_router(db, current_user):
    router = APIRouter(tags=["diagnostics", "account-tx-vs-ledger-walk"])

    @router.get("/diagnostics/account-tx-vs-ledger-walk")
    async def walk(
        account_id: str = Query(...),
        include_rows: bool = Query(False),
        max_rows: int = Query(50, ge=1, le=500),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # ── 1. Resolve account ─────────────────────────────────────
        acc = await db.accounts.find_one(
            {"user_id": uid, "id": account_id}, {"_id": 0})
        if not acc:
            raise HTTPException(404, "Account not found")

        if acc.get("account_type") not in {"bank", "cash"}:
            raise HTTPException(
                400,
                "هذا التشخيص مُتاح فقط لـ bank/cash. "
                f"النوع الحالي: {acc.get('account_type')}",
            )

        # ── 2. Walk account_transactions ───────────────────────────
        at_total = 0
        at_in = 0.0
        at_out = 0.0
        at_by_type: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "in": 0.0, "out": 0.0})
        at_by_month: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "net": 0.0})
        at_group_ids: set = set()
        at_orphans_count = 0
        at_orphans_net = 0.0
        at_sample: List[Dict[str, Any]] = []

        async for tx in db.account_transactions.find(
            {"user_id": uid, "account_id": account_id},
            {"_id": 0, "id": 1, "transaction_type": 1, "amount": 1,
             "direction": 1, "description": 1, "transaction_date": 1,
             "created_at": 1, "txn_group_id": 1, "metadata": 1},
        ).sort([("transaction_date", 1)]):
            at_total += 1
            amt = float(tx.get("amount") or 0)
            direction = tx.get("direction") or ""
            ttype = tx.get("transaction_type") or "(unknown)"
            month = _month_key(
                tx.get("transaction_date") or tx.get("created_at"))

            if direction == "in":
                at_in += amt
                at_by_type[ttype]["in"] += amt
                at_by_month[month]["net"] += amt
            else:
                at_out += amt
                at_by_type[ttype]["out"] += amt
                at_by_month[month]["net"] -= amt
            at_by_type[ttype]["count"] += 1
            at_by_month[month]["count"] += 1

            gid = tx.get("txn_group_id")
            if gid:
                at_group_ids.add(gid)
            else:
                at_orphans_count += 1
                at_orphans_net += amt if direction == "in" else -amt

            if include_rows and len(at_sample) < max_rows:
                at_sample.append({
                    "id": tx.get("id"),
                    "transaction_type": ttype,
                    "amount": _r(amt),
                    "direction": direction,
                    "description": (tx.get("description") or "")[:80],
                    "date": tx.get("transaction_date"),
                    "txn_group_id": gid,
                    "has_ledger_link": bool(gid),
                })

        at_by_type_list = [
            {"transaction_type": k,
             "count": v["count"],
             "in": _r(v["in"]),
             "out": _r(v["out"]),
             "net": _r(v["in"] - v["out"])}
            for k, v in sorted(at_by_type.items(),
                               key=lambda x: -x[1]["count"])
        ]
        at_by_month_list = [
            {"month": k, "count": v["count"], "net": _r(v["net"])}
            for k, v in sorted(at_by_month.items())
        ]

        # ── 3. Walk general_ledger ─────────────────────────────────
        gl_total = 0
        gl_debit = 0.0
        gl_credit = 0.0
        gl_by_type: Dict[tuple, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "debit": 0.0, "credit": 0.0})
        gl_by_month: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "net": 0.0})
        gl_group_ids: set = set()
        gl_sample: List[Dict[str, Any]] = []

        async for r in db.general_ledger.find(
            {"user_id": uid,
             "entity_type": "bank",
             "entity_id": account_id,
             "status": "posted",
             "entry_type": {"$ne": "reversal"},
             "metadata.legacy_orphan": {"$ne": True}},
            {"_id": 0, "id": 1, "entry_type": 1, "sub_account": 1,
             "side": 1, "amount": 1, "notes": 1, "posted_at": 1,
             "created_at": 1, "txn_group_id": 1, "metadata": 1},
        ).sort([("posted_at", 1)]):
            gl_total += 1
            amt = float(r.get("amount") or 0)
            side = r.get("side") or ""
            etype = r.get("entry_type") or "(unknown)"
            sub = r.get("sub_account") or "main"
            month = _month_key(
                r.get("posted_at") or r.get("created_at"))

            key = (etype, sub)
            if side == "debit":
                gl_debit += amt
                gl_by_type[key]["debit"] += amt
                gl_by_month[month]["net"] += amt
            else:
                gl_credit += amt
                gl_by_type[key]["credit"] += amt
                gl_by_month[month]["net"] -= amt
            gl_by_type[key]["count"] += 1
            gl_by_month[month]["count"] += 1

            gid = r.get("txn_group_id")
            if gid:
                gl_group_ids.add(gid)

            if include_rows and len(gl_sample) < max_rows:
                md = r.get("metadata") or {}
                gl_sample.append({
                    "id": r.get("id"),
                    "entry_type": etype,
                    "sub_account": sub,
                    "side": side,
                    "amount": _r(amt),
                    "notes": (
                        r.get("notes")
                        or md.get("description") or "")[:80],
                    "date": r.get("posted_at") or r.get("created_at"),
                    "txn_group_id": gid,
                })

        gl_by_type_list = [
            {"entry_type": k[0],
             "sub_account": k[1],
             "count": v["count"],
             "debit": _r(v["debit"]),
             "credit": _r(v["credit"]),
             "net": _r(v["debit"] - v["credit"])}
            for k, v in sorted(gl_by_type.items(),
                               key=lambda x: -x[1]["count"])
        ]
        gl_by_month_list = [
            {"month": k, "count": v["count"], "net": _r(v["net"])}
            for k, v in sorted(gl_by_month.items())
        ]

        # ── 4. Cross-walk ──────────────────────────────────────────
        # Match account_transactions ↔ general_ledger via txn_group_id.
        matched_groups = at_group_ids & gl_group_ids
        at_only_groups = at_group_ids - gl_group_ids
        gl_only_groups = gl_group_ids - at_group_ids

        # Compute net of account_transactions whose txn_group_id is
        # NOT present in general_ledger ⇒ these are legacy rows that
        # never made it to ledger (a key suspect for the 70k gap).
        unmatched_at_net = 0.0
        unmatched_at_count = 0
        unmatched_at_by_type: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "in": 0.0, "out": 0.0, "net": 0.0})
        unmatched_at_sample: List[Dict[str, Any]] = []
        async for tx in db.account_transactions.find(
            {"user_id": uid, "account_id": account_id,
             "$or": [
                 {"txn_group_id": {"$in": list(at_only_groups)}},
                 {"txn_group_id": {"$exists": False}},
                 {"txn_group_id": None},
             ]},
            {"_id": 0, "id": 1, "transaction_type": 1, "amount": 1,
             "direction": 1, "description": 1, "transaction_date": 1,
             "txn_group_id": 1},
        ).sort([("transaction_date", 1)]):
            amt = float(tx.get("amount") or 0)
            direction = tx.get("direction") or ""
            ttype = tx.get("transaction_type") or "(unknown)"
            signed = amt if direction == "in" else -amt
            unmatched_at_net += signed
            unmatched_at_count += 1
            unmatched_at_by_type[ttype]["count"] += 1
            if direction == "in":
                unmatched_at_by_type[ttype]["in"] += amt
            else:
                unmatched_at_by_type[ttype]["out"] += amt
            unmatched_at_by_type[ttype]["net"] += signed
            if include_rows and len(unmatched_at_sample) < max_rows:
                unmatched_at_sample.append({
                    "id": tx.get("id"),
                    "transaction_type": ttype,
                    "amount": _r(amt),
                    "direction": direction,
                    "description": (tx.get("description") or "")[:80],
                    "date": tx.get("transaction_date"),
                    "txn_group_id": tx.get("txn_group_id"),
                })

        # Same for ledger groups without account_tx link
        unmatched_gl_net = 0.0
        unmatched_gl_count = 0
        unmatched_gl_sample: List[Dict[str, Any]] = []
        if gl_only_groups:
            async for r in db.general_ledger.find(
                {"user_id": uid,
                 "entity_type": "bank",
                 "entity_id": account_id,
                 "status": "posted",
                 "txn_group_id": {"$in": list(gl_only_groups)}},
                {"_id": 0, "id": 1, "entry_type": 1, "sub_account": 1,
                 "side": 1, "amount": 1, "notes": 1, "posted_at": 1,
                 "txn_group_id": 1},
            ).sort([("posted_at", 1)]):
                amt = float(r.get("amount") or 0)
                side = r.get("side")
                signed = amt if side == "debit" else -amt
                unmatched_gl_net += signed
                unmatched_gl_count += 1
                if include_rows and len(unmatched_gl_sample) < max_rows:
                    unmatched_gl_sample.append({
                        "id": r.get("id"),
                        "entry_type": r.get("entry_type"),
                        "sub_account": r.get("sub_account"),
                        "side": side,
                        "amount": _r(amt),
                        "notes": (r.get("notes") or "")[:80],
                        "date": r.get("posted_at"),
                        "txn_group_id": r.get("txn_group_id"),
                    })

        # ── 5. Gap breakdown ───────────────────────────────────────
        stored = _r(acc.get("current_balance"))
        ledger_main_plus_balance = _r(gl_debit - gl_credit)
        gap_stored_minus_ledger = _r(stored - ledger_main_plus_balance)

        # Compute BNPL contribution (sub=balance net) separately
        bnpl_net = 0.0
        for k, v in gl_by_type.items():
            etype, sub = k
            if sub == "balance":
                bnpl_net += v["debit"] - v["credit"]
        bnpl_net = _r(bnpl_net)

        return {
            "ok": True,
            "iter": "iter250b_p1_5_b",
            "generated_at": _now_iso(),
            "account": {
                "id": acc["id"],
                "name": acc.get("name"),
                "account_type": acc.get("account_type"),
                "stored_current_balance": stored,
                "opening_balance": _r(acc.get("opening_balance")),
                "expected_orders_balance":
                    _r(acc.get("expected_orders_balance")),
            },
            "account_transactions": {
                "total_count": at_total,
                "in_total": _r(at_in),
                "out_total": _r(at_out),
                "net": _r(at_in - at_out),
                "orphans_without_txn_group_id": {
                    "count": at_orphans_count,
                    "net": _r(at_orphans_net),
                },
                "by_type": at_by_type_list,
                "by_month": at_by_month_list,
                "sample_rows": at_sample,
            },
            "general_ledger": {
                "total_count": gl_total,
                "debit_total": _r(gl_debit),
                "credit_total": _r(gl_credit),
                "net": _r(gl_debit - gl_credit),
                "by_entry_type": gl_by_type_list,
                "by_month": gl_by_month_list,
                "sample_rows": gl_sample,
            },
            "crosswalk": {
                "shared_txn_group_ids": len(matched_groups),
                "account_tx_only_txn_group_ids": len(at_only_groups),
                "ledger_only_txn_group_ids": len(gl_only_groups),
                "unmatched_account_tx_rows_count": unmatched_at_count,
                "unmatched_account_tx_net": _r(unmatched_at_net),
                "unmatched_account_tx_by_type": [
                    {"transaction_type": k,
                     "count": v["count"],
                     "in": _r(v["in"]),
                     "out": _r(v["out"]),
                     "net": _r(v["net"])}
                    for k, v in sorted(
                        unmatched_at_by_type.items(),
                        key=lambda x: -abs(x[1]["net"]))
                ],
                "unmatched_account_tx_sample": unmatched_at_sample,
                "unmatched_ledger_rows_count": unmatched_gl_count,
                "unmatched_ledger_net": _r(unmatched_gl_net),
                "unmatched_ledger_sample": unmatched_gl_sample,
            },
            "summary": {
                "stored_current_balance": stored,
                "ledger_main_plus_balance": ledger_main_plus_balance,
                "gap_stored_minus_ledger": gap_stored_minus_ledger,
                "gap_breakdown_hypothesis": {
                    "bnpl_in_sub_balance": bnpl_net,
                    "unmatched_legacy_account_tx_net": _r(unmatched_at_net),
                    "remaining_unexplained": _r(
                        gap_stored_minus_ledger
                        - 0  # bnpl_net is ALREADY in ledger
                        - unmatched_at_net
                    ),
                },
            },
            "notes": [
                "READ-ONLY — no DB writes performed.",
                "shared_txn_group_ids = both collections have the same "
                "group (typical for new transactions written via "
                "double-write).",
                "account_tx_only_txn_group_ids = group exists only in "
                "account_transactions (legacy, never made it to ledger).",
                "ledger_only_txn_group_ids = group exists only in "
                "general_ledger (newer write that bypassed legacy, "
                "e.g. BNPL bridge, COD settlements, opening_balance).",
                "unmatched_account_tx_net is THE suspect for the "
                "70k drift on بنك الإنماء.",
            ],
        }

    return router


__all__ = ["make_account_tx_vs_ledger_walk_router"]
