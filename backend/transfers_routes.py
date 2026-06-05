"""Internal transfers between the user's own accounts — Phase 2.1.

A "transfer" is two LINKED `internal_transfer` rows in `account_transactions`:
  • OUT from the source account (decreases its current_balance)
  • IN  to  the destination account (increases its current_balance)

Both rows carry the same `transfer_id` so the UI can group them and we can
delete the pair atomically. Balances are kept honest via the existing
`_recompute_balance(account_id)` helper from accounts_routes.

Scope (deliberately small for iter-66):
- Create / list / delete transfer
- Inside an account's transaction list, transfer rows already show via the
  shared `internal_transfer` type — we add `peer_account_name` for the UI.

Out of scope (later phases): reconciliation, 14-day delay alerts, debt
tracking, bank-statement import.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user_from_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TransferIn(BaseModel):
    from_account_id: str
    to_account_id: str
    amount: float = Field(..., gt=0)
    transfer_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    reference: Optional[str] = Field("", max_length=120)
    notes: Optional[str] = Field("", max_length=500)
    attachment_url: Optional[str] = None


def attach_transfers_routes(parent_router: APIRouter, db) -> None:
    from accounts_routes import _recompute_balance, _strip

    router = APIRouter(prefix="/transfers", tags=["transfers"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    async def _enrich(doc: dict) -> dict:
        """Add peer account names for nicer rendering."""
        out = _strip(doc)
        # Fetch both accounts' names in parallel-ish (two cheap PK lookups).
        from_doc = await db.accounts.find_one(
            {"id": out["from_account_id"]}, {"_id": 0, "name": 1, "currency": 1}
        ) or {}
        to_doc = await db.accounts.find_one(
            {"id": out["to_account_id"]}, {"_id": 0, "name": 1, "currency": 1}
        ) or {}
        out["from_account_name"] = from_doc.get("name") or "—"
        out["to_account_name"] = to_doc.get("name") or "—"
        out["currency"] = from_doc.get("currency") or "SAR"
        return out

    @router.get("")
    async def list_transfers(
        user: dict = Depends(current_user),
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        account_id: Optional[str] = None,
        limit: int = 200,
    ):
        q: dict = {"user_id": user["id"]}
        if from_date:
            q["transfer_date"] = {"$gte": from_date}
        if to_date:
            q.setdefault("transfer_date", {})["$lte"] = to_date
        if account_id:
            q["$or"] = [
                {"from_account_id": account_id},
                {"to_account_id": account_id},
            ]
        limit = max(1, min(int(limit or 200), 1000))
        docs = await db.transfers.find(q, {"_id": 0}).sort(
            [("transfer_date", -1), ("created_at", -1)]
        ).to_list(limit)
        return [await _enrich(d) for d in docs]

    @router.post("")
    async def create_transfer(payload: TransferIn, user: dict = Depends(current_user)):
        uid = user["id"]
        if payload.from_account_id == payload.to_account_id:
            raise HTTPException(400, "حساب المصدر والوجهة لا يمكن أن يكونا نفس الحساب.")
        # Validate both accounts exist and belong to the user.
        from_acc = await db.accounts.find_one(
            {"id": payload.from_account_id, "user_id": uid},
            {"_id": 0, "id": 1, "name": 1, "currency": 1, "status": 1},
        )
        to_acc = await db.accounts.find_one(
            {"id": payload.to_account_id, "user_id": uid},
            {"_id": 0, "id": 1, "name": 1, "currency": 1, "status": 1},
        )
        if not from_acc:
            raise HTTPException(404, "حساب المصدر غير موجود.")
        if not to_acc:
            raise HTTPException(404, "حساب الوجهة غير موجود.")
        for a, label in ((from_acc, "المصدر"), (to_acc, "الوجهة")):
            if a.get("status") == "inactive":
                raise HTTPException(400, f"حساب {label} موقوف ولا يمكن استخدامه.")

        now = _now()
        transfer_id = str(uuid.uuid4())
        amount = round(float(payload.amount), 2)
        description = (
            f"تحويل من {from_acc['name']} إلى {to_acc['name']}"
            + (f" — {payload.reference}" if payload.reference else "")
        )

        # 1. Persist the transfer envelope (1 doc per transfer).
        transfer_doc = {
            "id": transfer_id,
            "user_id": uid,
            "from_account_id": payload.from_account_id,
            "to_account_id": payload.to_account_id,
            "amount": amount,
            "transfer_date": payload.transfer_date,
            "reference": (payload.reference or "").strip(),
            "notes": (payload.notes or "").strip(),
            "attachment_url": payload.attachment_url,
            "created_by_user_id": uid,
            "created_by_name": user.get("name") or user.get("email") or "",
            "created_at": now,
            "updated_at": now,
        }
        await db.transfers.insert_one(transfer_doc)

        # 2. Two paired ledger rows — same transfer_id links them.
        tx_out = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "account_id": payload.from_account_id,
            "transaction_type": "internal_transfer",
            "amount": amount,
            "direction": "out",
            "description": description,
            "transaction_date": payload.transfer_date,
            "balance_after": 0.0,
            "status": "posted",
            "attachment_url": payload.attachment_url,
            "transfer_id": transfer_id,
            "peer_account_id": payload.to_account_id,
            "peer_account_name": to_acc["name"],
            "reference": (payload.reference or "").strip(),
            "created_at": now,
            "updated_at": now,
        }
        tx_in = {
            **tx_out,
            "id": str(uuid.uuid4()),
            "account_id": payload.to_account_id,
            "direction": "in",
            "peer_account_id": payload.from_account_id,
            "peer_account_name": from_acc["name"],
        }
        await db.account_transactions.insert_many([tx_out, tx_in])

        # 3. Recompute both account balances.
        await _recompute_balance(db, uid, payload.from_account_id)
        await _recompute_balance(db, uid, payload.to_account_id)

        transfer_doc.pop("_id", None)
        return await _enrich(transfer_doc)

    @router.delete("/{transfer_id}")
    async def delete_transfer(transfer_id: str, user: dict = Depends(current_user)):
        uid = user["id"]
        existing = await db.transfers.find_one(
            {"id": transfer_id, "user_id": uid}, {"_id": 0}
        )
        if not existing:
            raise HTTPException(404, "التحويل غير موجود.")
        # Remove the paired ledger rows and the envelope.
        await db.account_transactions.delete_many(
            {"user_id": uid, "transfer_id": transfer_id}
        )
        await db.transfers.delete_one({"id": transfer_id, "user_id": uid})
        # Recompute both sides.
        await _recompute_balance(db, uid, existing["from_account_id"])
        await _recompute_balance(db, uid, existing["to_account_id"])
        return {"ok": True}

    parent_router.include_router(router)


async def ensure_transfers_indexes(db) -> None:
    await db.transfers.create_index([("user_id", 1), ("transfer_date", -1)])
    await db.transfers.create_index("id", unique=True)
    await db.account_transactions.create_index("transfer_id", sparse=True)
