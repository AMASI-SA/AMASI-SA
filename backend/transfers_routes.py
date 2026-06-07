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
from shipping_companies import scrub_shipping_company


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
    # Iter-96 — for COD-source transfers: which courier remitted the cash.
    # Normalised via shipping_companies.scrub_shipping_company() so
    # SMSA/سمسا/smsa all collapse to the canonical display name.
    shipping_company: Optional[str] = Field(None, max_length=120)
    # Iter-98 — Net-COD method:
    #   cod_gross_collected = total cash the courier physically collected
    #   shipping_fee_deducted = courier fees withheld before remittance
    #   amount               = the net actually deposited to the bank
    #   (Constraint: amount = cod_gross_collected − shipping_fee_deducted)
    # When shipping_fee_deducted > 0, an additional bookkeeping entry is
    # generated against the courier's shipping_payable OR as a courier
    # fee expense, depending on `shipping_fee_settles_against`.
    cod_gross_collected: Optional[float] = Field(None, ge=0)
    shipping_fee_deducted: Optional[float] = Field(None, ge=0)
    shipping_fee_settles_against: Optional[str] = Field(
        "shipping_payable", pattern=r"^(shipping_payable|expense)$"
    )


def attach_transfers_routes(parent_router: APIRouter, db) -> None:
    from accounts_routes import _recompute_balance, _strip

    async def _post_shipping_fee_leg(
        db, uid: str, *,
        transfer_id: str, shipping_company: str,
        fee_amount: float, transfer_date: str,
        settle_mode: str,
    ) -> None:
        """Iter-98 — record the courier fee leg of a Net-COD transfer.

        Two settle modes are supported:
          • shipping_payable: the fee reduces the courier's payable in
            `shipping_payments` (treated as a partial payment paid from
            "withheld COD" — no bank movement). This is the default and
            is correct when a prior shipping invoice exists.
          • expense: the fee is booked as an operating_daily_expenses row
            (no bank link — the cash never reached the bank). Use when
            there is no prior shipping invoice to settle.
        """
        now = _now()
        if settle_mode == "shipping_payable":
            await db.shipping_payments.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": uid,
                "company_name": shipping_company,
                "amount": round(fee_amount, 2),
                "payment_date": transfer_date,
                "invoice_number": f"COD-NET-{transfer_id[:8]}",
                "note": (
                    f"رسوم شحن مخصومة من COD (Iter-98) — "
                    f"transfer_id={transfer_id}"
                ),
                "paid_from_account_id": None,     # withheld, not from bank
                "linked_transaction_id": None,
                "linked_transfer_id": transfer_id,
                "settled_via_cod_withholding": True,
                "created_at": now,
            })
        else:    # expense
            await db.operating_daily_expenses.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": uid,
                "date": transfer_date,
                "expense_type": "رسوم شحن",
                "description": f"رسوم {shipping_company} مخصومة من COD",
                "amount": round(fee_amount, 2),
                "payment_method": "خصم من COD",
                "notes": f"transfer_id={transfer_id}",
                "paid_from_account_id": None,
                "linked_transaction_id": None,
                "linked_transfer_id": transfer_id,
                "created_at": now,
                "updated_at": now,
            })

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
            {"_id": 0, "id": 1, "name": 1, "currency": 1, "status": 1,
             "current_balance": 1, "normalized_payment_method": 1},
        )
        to_acc = await db.accounts.find_one(
            {"id": payload.to_account_id, "user_id": uid},
            {"_id": 0, "id": 1, "name": 1, "currency": 1, "status": 1,
             "current_balance": 1, "normalized_payment_method": 1},
        )
        if not from_acc:
            raise HTTPException(404, "حساب المصدر غير موجود.")
        if not to_acc:
            raise HTTPException(404, "حساب الوجهة غير موجود.")
        for a, label in ((from_acc, "المصدر"), (to_acc, "الوجهة")):
            if a.get("status") == "inactive":
                raise HTTPException(400, f"حساب {label} موقوف ولا يمكن استخدامه.")

        # Guard: refuse to overdraw the source account. Compare against the
        # current_balance (which already accounts for prior transfers).
        amount = round(float(payload.amount), 2)
        from_bal = round(float(from_acc.get("current_balance") or 0), 2)
        if amount > from_bal + 0.001:  # tiny epsilon for float noise
            raise HTTPException(
                400,
                f"المبلغ ({amount:,.2f}) أكبر من الرصيد المتاح في {from_acc['name']} ({from_bal:,.2f}).",
            )

        now = _now()
        transfer_id = str(uuid.uuid4())

        # Iter-96 — capture shipping company only for COD-source transfers.
        # Iter-98 — normalise to canonical display name (SMSA/سمسا/smsa → سمسا).
        is_cod_source = (from_acc.get("normalized_payment_method") == "cash_on_delivery")
        raw_company = (payload.shipping_company or "").strip() if is_cod_source else ""
        shipping_company = scrub_shipping_company(raw_company) if raw_company else ""

        # Iter-98 — Net COD method: validate the gross/fee math when provided
        cod_gross = float(payload.cod_gross_collected or 0)
        ship_fee = float(payload.shipping_fee_deducted or 0)
        use_net_cod = is_cod_source and (cod_gross > 0 or ship_fee > 0)
        if use_net_cod:
            expected_net = round(cod_gross - ship_fee, 2)
            if expected_net < 0:
                raise HTTPException(400, "رسوم الشحن أكبر من إجمالي COD المحصَّل")
            if abs(expected_net - amount) > 0.01:
                raise HTTPException(
                    400,
                    f"الصافي ({amount}) لا يطابق الإجمالي ({cod_gross}) "
                    f"− الرسوم ({ship_fee}) = {expected_net}",
                )
            if not shipping_company:
                raise HTTPException(400, "شركة الشحن مطلوبة عند خصم الرسوم")

        description = (
            f"تحويل من {from_acc['name']} إلى {to_acc['name']}"
            + (f" — {payload.reference}" if payload.reference else "")
            + (f" — شركة الشحن: {shipping_company}" if shipping_company else "")
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
            "shipping_company": shipping_company or None,
            "cod_gross_collected": cod_gross if use_net_cod else None,
            "shipping_fee_deducted": ship_fee if use_net_cod else None,
            "shipping_fee_settles_against":
                payload.shipping_fee_settles_against if use_net_cod else None,
            "created_by_user_id": uid,
            "created_by_name": user.get("name") or user.get("email") or "",
            "created_at": now,
            "updated_at": now,
        }
        await db.transfers.insert_one(transfer_doc)

        # 2. Two paired ledger rows — same transfer_id links them.
        # Iter-98 — for Net-COD method, the OUT row reflects the GROSS that
        # left the COD bucket (the courier physically collected the full
        # amount); the IN row reflects the NET that actually arrived in the
        # bank. The 2-leg integrity is preserved via a third entry below.
        out_amount = round(cod_gross, 2) if use_net_cod else amount
        tx_out = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "account_id": payload.from_account_id,
            "transaction_type": "internal_transfer",
            "amount": out_amount,
            "direction": "out",
            "description": description
                + (f" (إجمالي {cod_gross} − رسوم {ship_fee} = صافي {amount})"
                   if use_net_cod else ""),
            "transaction_date": payload.transfer_date,
            "balance_after": 0.0,
            "status": "posted",
            "attachment_url": payload.attachment_url,
            "transfer_id": transfer_id,
            "peer_account_id": payload.to_account_id,
            "peer_account_name": to_acc["name"],
            "reference": (payload.reference or "").strip(),
            "shipping_company": shipping_company or None,
            "created_at": now,
            "updated_at": now,
        }
        tx_in = {
            **tx_out,
            "id": str(uuid.uuid4()),
            "account_id": payload.to_account_id,
            "amount": amount,            # net to bank
            "direction": "in",
            "peer_account_id": payload.from_account_id,
            "peer_account_name": from_acc["name"],
        }
        await db.account_transactions.insert_many([tx_out, tx_in])

        # 3. Recompute both account balances.
        await _recompute_balance(db, uid, payload.from_account_id)
        await _recompute_balance(db, uid, payload.to_account_id)

        # 4. Iter-98 — handle the shipping-fee leg (only when ship_fee > 0).
        if use_net_cod and ship_fee > 0:
            await _post_shipping_fee_leg(
                db, uid,
                transfer_id=transfer_id,
                shipping_company=shipping_company,
                fee_amount=ship_fee,
                transfer_date=payload.transfer_date,
                settle_mode=payload.shipping_fee_settles_against,
            )

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
