"""Iter-248 — BNPL settlement bank-txn backfill & health.

Surfaces and fixes the gap where `bnpl_settlement` ledger entries
have a balanced bank leg but no row in `account_transactions` (so
the bank statement UI does not list the inflow).

  GET  /api/audit/bnpl-settlement-banktx-backfill-dry-run
  POST /api/admin/bnpl-settlement-banktx-backfill-apply  (X-Apply-Token)
  GET  /api/audit/bnpl-settlement-banktx-health
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException


def _r(n) -> float: return round(float(n or 0), 2)


def _token(uid: str, count: int, total: float) -> str:
    return hashlib.sha256(
        f"iter248|{uid}|{count}|{total:.2f}".encode()
    ).hexdigest()[:16]


def make_bnpl_settlement_banktx_router(db, current_user):
    router = APIRouter(tags=["audit", "bnpl"])

    async def _scan(uid: str) -> List[Dict[str, Any]]:
        """List every bnpl_settlement txn_group whose bank leg has no
        matching `account_transactions` row."""
        # Find every distinct bnpl_settlement group with a bank leg.
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        async for e in db.general_ledger.find(
            {"user_id": uid,
             "entry_type": "bnpl_settlement",
             "entity_type": "bank",
             "side": "debit",
             "status": "posted"},
            {"_id": 0, "txn_group_id": 1, "amount": 1, "entity_id": 1,
             "metadata": 1, "transaction_date": 1, "created_at": 1},
        ):
            grp = e.get("txn_group_id")
            if not grp or grp in seen:
                continue
            seen.add(grp)
            md = e.get("metadata") or {}
            ref = md.get("settlement_reference")
            provider = md.get("provider")
            bank_id = md.get("bank_account_id") or e.get("entity_id")
            bank_name = md.get("bank_account_name") or ""
            transferred = _r(md.get("transferred_amount") or e.get("amount"))

            # Is there already an account_transactions row?
            idem = f"bnpl_settlement_bank_txn:{provider}:{ref}"
            existing = await db.account_transactions.find_one(
                {"user_id": uid,
                 "$or": [
                     {"idempotency_key": idem},
                     {"txn_group_id": grp},
                 ]},
                {"_id": 0, "id": 1},
            )
            has_txn = existing is not None

            out.append({
                "settlement_reference": ref,
                "provider": provider,
                "txn_group_id": grp,
                "bank_account_id": bank_id,
                "bank_account_name": bank_name,
                "transferred_amount": transferred,
                "settlement_date": md.get("settlement_date") or "",
                "has_bank_leg": True,
                "has_account_transaction": has_txn,
                "idempotency_key": idem,
                "_created_at": e.get("created_at"),
            })
        return out

    # ── Dry-Run ───────────────────────────────────────────────
    @router.get(
        "/audit/bnpl-settlement-banktx-backfill-dry-run")
    async def dry_run(user: dict = Depends(current_user)):
        uid = user["id"]
        rows = await _scan(uid)
        missing = [r for r in rows if not r["has_account_transaction"]]
        total = _r(sum(r["transferred_amount"] for r in missing))
        return {
            "ok": True, "iter": "iter248", "read_only": True,
            "settlements_in_ledger_with_bank_leg": len(rows),
            "missing_bank_transaction_count": len(missing),
            "missing_bank_transaction_sum": total,
            "missing": missing,
            "apply_token": _token(uid, len(missing), total),
            "apply_endpoint":
                "POST /api/admin/bnpl-settlement-banktx-backfill-apply",
        }

    # ── Gated Apply ───────────────────────────────────────────
    @router.post(
        "/admin/bnpl-settlement-banktx-backfill-apply")
    async def apply(
        user: dict = Depends(current_user),
        x_apply_token: Optional[str] = Header(
            None, alias="X-Apply-Token"),
    ):
        uid = user["id"]
        rows = await _scan(uid)
        missing = [r for r in rows if not r["has_account_transaction"]]
        total = _r(sum(r["transferred_amount"] for r in missing))
        expected = _token(uid, len(missing), total)
        if not x_apply_token or x_apply_token != expected:
            raise HTTPException(
                401,
                "X-Apply-Token مفقود أو منتهي.  أعد تشغيل الـ dry-run "
                "ونسخ apply_token الجديد.",
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        applied: List[Dict[str, Any]] = []
        for r in missing:
            # Re-check idempotency right before insert (race safety).
            already = await db.account_transactions.find_one(
                {"user_id": uid, "idempotency_key": r["idempotency_key"]},
                {"_id": 0, "id": 1},
            )
            if already:
                applied.append({**r, "result": "already_present"})
                continue
            doc = {
                "id": str(uuid.uuid4()),
                "user_id": uid,
                "account_id": r["bank_account_id"],
                "account_name": r["bank_account_name"],
                "direction": "in",
                "transaction_type": "bnpl_settlement",
                "amount": r["transferred_amount"],
                "transaction_date": r["settlement_date"] or "",
                "reference": r["settlement_reference"],
                "txn_group_id": r["txn_group_id"],
                "provider": r["provider"],
                "description":
                    f"تسوية {r['provider']} - "
                    f"{r['settlement_reference']}",
                "idempotency_key": r["idempotency_key"],
                "status": "posted",
                "balance_after": 0.0,
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            await db.account_transactions.insert_one(doc)
            applied.append({**r, "result": "inserted",
                            "new_account_transaction_id": doc["id"]})

        return {
            "ok": True, "iter": "iter248",
            "applied_count": sum(1 for x in applied
                                 if x["result"] == "inserted"),
            "applied_sum": total,
            "skipped_already_present": sum(
                1 for x in applied if x["result"] == "already_present"),
            "results": applied,
            "note": (
                "Inserted into account_transactions ONLY.  Did NOT "
                "touch general_ledger or current_balance.  Tabby + "
                "Tamara use the identical schema."
            ),
        }

    # ── Health ────────────────────────────────────────────────
    @router.get("/audit/bnpl-settlement-banktx-health")
    async def health(user: dict = Depends(current_user)):
        uid = user["id"]
        rows = await _scan(uid)
        with_txn = sum(1 for r in rows if r["has_account_transaction"])
        without = len(rows) - with_txn
        return {
            "ok": True, "iter": "iter248", "read_only": True,
            "settlements_in_ledger": len(rows),
            "settlements_with_bank_transaction": with_txn,
            "settlements_missing_bank_transaction": without,
            "details": rows,
        }

    return router
