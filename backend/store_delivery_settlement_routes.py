"""Audited settlements and accounting-ready courier cash movements.

Existing settlement routes keep their current general-ledger behavior.  The
``pending-account`` routes are intentionally separate: they record operational
cash/fee movements for the AMASI mobile app without posting any journal entry.
Those rows are marked eligible for a future Mezan 2 finance cutover.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from store_delivery_domain import money, normalize_text
from store_delivery_accounting import (
    post_settlement_journal,
    store_driver_ledger_balances,
)
from store_delivery_driver_app_routes import DRIVER_COLLECTIONS, DRIVER_EARNINGS
from store_delivery_driver_routes import STORE_DRIVERS

SETTLEMENTS = "store_delivery_driver_settlements"
PENDING_FINANCIAL_TRANSACTIONS = "store_driver_financial_transactions"
SettlementType = Literal["cod_remittance", "earning_payment", "net_settlement"]
PendingTransactionType = Literal["cod_receipt", "delivery_payout", "offset"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merchant_user_id(user: dict[str, Any]) -> str:
    if normalize_text(user.get("role")).casefold() == "owner" or user.get("is_owner") is True:
        return normalize_text(user.get("id"))
    owner_id = normalize_text(user.get("created_by"))
    if not owner_id:
        raise HTTPException(status_code=409, detail={"code": "employee_store_not_linked"})
    return owner_id


def _require_accountant(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict):
        raise HTTPException(status_code=403, detail={"code": "store_delivery_settlement_permission_required"})
    role = normalize_text(user.get("role")).casefold()
    permission = "store_delivery.settlements.manage"
    allowed = (
        role in {"owner", "admin", "accountant"}
        or user.get("is_owner") is True
        or permission in set(user.get("extra_permissions") or [])
    ) and permission not in set(user.get("denied_permissions") or [])
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "store_delivery_settlement_permission_required"})
    return user


def _require_pending_owner(user: Any) -> dict[str, Any]:
    """The new mobile ledger is owner-only and never resolves legacy employees."""
    if not isinstance(user, dict):
        raise HTTPException(status_code=403, detail={"code": "store_driver_financial_owner_required"})
    role = normalize_text(user.get("role")).casefold()
    if role != "owner" and user.get("is_owner") is not True:
        raise HTTPException(status_code=403, detail={"code": "store_driver_financial_owner_required"})
    if not normalize_text(user.get("id")):
        raise HTTPException(status_code=403, detail={"code": "store_driver_financial_owner_required"})
    return user


class SettlementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float = Field(ge=0, le=10_000_000)
    earning_offset: float = Field(default=0, ge=0, le=10_000_000)
    account_id: str | None = Field(default=None, max_length=120)
    reference: str = Field(default="", max_length=240)
    note: str = Field(default="", max_length=1000)


class PendingTransactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=8, max_length=160)
    transaction_type: PendingTransactionType
    amount_halalas: int = Field(gt=0, le=1_000_000_000)
    payment_method: str = Field(default="cash", max_length=80)
    note: str = Field(default="", max_length=1000)
    occurred_at: str | None = Field(default=None, max_length=80)


class PendingTransactionReverse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=1000)


async def ensure_store_delivery_settlement_indexes(db: Any) -> None:
    await db[SETTLEMENTS].create_index([("user_id", 1), ("id", 1)], unique=True)
    await db[SETTLEMENTS].create_index([("user_id", 1), ("driver_id", 1), ("settlement_type", 1), ("created_at", -1)])


async def ensure_pending_financial_indexes(db: Any) -> None:
    await db[PENDING_FINANCIAL_TRANSACTIONS].create_index(
        [("user_id", 1), ("id", 1)], unique=True,
        name="uq_store_driver_financial_transaction",
    )
    await db[PENDING_FINANCIAL_TRANSACTIONS].create_index(
        [("user_id", 1), ("client_request_id", 1)], unique=True,
        name="uq_store_driver_financial_request",
    )
    await db[PENDING_FINANCIAL_TRANSACTIONS].create_index(
        [("user_id", 1), ("driver_id", 1), ("created_at", -1)],
        name="ix_store_driver_financial_history",
    )
    await db[PENDING_FINANCIAL_TRANSACTIONS].create_index(
        [("user_id", 1), ("accounting_eligible", 1), ("accounting_sent", 1)],
        name="ix_store_driver_financial_accounting_queue",
    )


async def _driver_or_404(db: Any, user_id: str, driver_id: str) -> dict[str, Any]:
    row = await db[STORE_DRIVERS].find_one({"user_id": user_id, "id": driver_id}, {"_id": 0})
    if not row:
        raise HTTPException(status_code=404, detail={"code": "store_driver_not_found"})
    return row


async def _totals(db: Any, user_id: str, driver_id: str) -> dict[str, float]:
    earnings = await db[DRIVER_EARNINGS].find(
        {"user_id": user_id, "driver_id": driver_id}, {"_id": 0, "amount": 1}
    ).to_list(length=100000)
    collections = await db[DRIVER_COLLECTIONS].find(
        {"user_id": user_id, "driver_id": driver_id}, {"_id": 0, "cod_custody_amount": 1}
    ).to_list(length=100000)
    settlements = await db[SETTLEMENTS].find(
        {"user_id": user_id, "driver_id": driver_id, "status": "posted"},
        {"_id": 0, "amount": 1, "settlement_type": 1, "cod_settled_amount": 1, "delivery_fee_settled_amount": 1},
    ).to_list(length=100000)

    earned = round(sum(float(row.get("amount") or 0) for row in earnings), 2)
    cash_collected = round(sum(float(row.get("cod_custody_amount") or 0) for row in collections), 2)
    cod_remitted = round(sum(
        float(row.get("cod_settled_amount") if row.get("cod_settled_amount") is not None else row.get("amount") or 0)
        for row in settlements
        if row.get("settlement_type") in {"cod_remittance", "net_settlement"}
    ), 2)
    earnings_paid = round(sum(
        float(row.get("delivery_fee_settled_amount") if row.get("delivery_fee_settled_amount") is not None else row.get("amount") or 0)
        for row in settlements
        if row.get("settlement_type") in {"earning_payment", "net_settlement"}
    ), 2)
    operational_cod = round(max(cash_collected - cod_remitted, 0), 2)
    operational_fee = round(max(earned - earnings_paid, 0), 2)
    ledger = await store_driver_ledger_balances(db, user_id=user_id, driver_id=driver_id)
    return {
        "delivery_earnings_total": earned,
        "delivery_earnings_paid": earnings_paid,
        "delivery_earnings_due": operational_fee,
        "cod_cash_collected": cash_collected,
        "cod_cash_remitted": cod_remitted,
        "cod_cash_custody": operational_cod,
        "net_due_from_driver": round(max(operational_cod - operational_fee, 0), 2),
        "net_due_to_driver": round(max(operational_fee - operational_cod, 0), 2),
        "ledger_cod_receivable": ledger["cod_receivable"],
        "ledger_delivery_fee_payable": ledger["delivery_fee_payable"],
        "ledger_net_balance": ledger["net_balance"],
    }


async def _pending_account(db: Any, user_id: str, driver_id: str) -> dict[str, Any]:
    base = await _totals(db, user_id, driver_id)
    rows = await db[PENDING_FINANCIAL_TRANSACTIONS].find(
        {"user_id": user_id, "driver_id": driver_id, "status": "active"},
        {"_id": 0, "transaction_type": 1, "amount_halalas": 1},
    ).to_list(length=100000)
    received = sum(int(row.get("amount_halalas") or 0) for row in rows if row.get("transaction_type") == "cod_receipt")
    paid = sum(int(row.get("amount_halalas") or 0) for row in rows if row.get("transaction_type") == "delivery_payout")
    offset = sum(int(row.get("amount_halalas") or 0) for row in rows if row.get("transaction_type") == "offset")
    cod_generated = int(round(float(base["cod_cash_custody"]) * 100))
    fee_generated = int(round(float(base["delivery_earnings_due"]) * 100))
    cod_due = max(0, cod_generated - received - offset)
    fee_due = max(0, fee_generated - paid - offset)
    return {
        "cod_generated_halalas": cod_generated,
        "delivery_fees_generated_halalas": fee_generated,
        "cod_received_halalas": received,
        "delivery_fees_paid_halalas": paid,
        "offset_halalas": offset,
        "cod_due_halalas": cod_due,
        "delivery_fee_due_halalas": fee_due,
        "net_balance_halalas": cod_due - fee_due,
        "max_offset_halalas": min(cod_due, fee_due),
    }


def _public_pending_row(row: dict[str, Any]) -> dict[str, Any]:
    clean = dict(row)
    clean.pop("_id", None)
    clean.pop("user_id", None)
    return clean


def make_store_delivery_settlement_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/settlements", tags=["Store Delivery Settlements"])

    @router.get("/drivers")
    async def settlement_drivers(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_accountant(user)
        user_id = _merchant_user_id(actor)
        drivers = await db[STORE_DRIVERS].find(
            {"user_id": user_id}, {"_id": 0, "user_id": 0, "city_key": 0, "notes": 0},
        ).sort([("status", 1), ("name", 1)]).to_list(length=1000)
        items = []
        for driver in drivers:
            items.append({"id": driver.get("id"), "name": driver.get("name"), "phone": driver.get("phone"), "city": driver.get("city"), "status": driver.get("status"), **await _totals(db, user_id, driver.get("id"))})
        return {"items": items, "total": len(items)}

    @router.get("/driver/{driver_id}/summary")
    async def driver_summary(driver_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_accountant(user)
        user_id = _merchant_user_id(actor)
        driver = await _driver_or_404(db, user_id, driver_id)
        return {"driver": {"id": driver["id"], "name": driver.get("name")}, **await _totals(db, user_id, driver_id)}

    @router.get("/driver/{driver_id}")
    async def list_settlements(driver_id: str, limit: int = Query(default=250, ge=1, le=1000), user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_accountant(user)
        user_id = _merchant_user_id(actor)
        await _driver_or_404(db, user_id, driver_id)
        items = await db[SETTLEMENTS].find({"user_id": user_id, "driver_id": driver_id}, {"_id": 0, "user_id": 0}).sort("created_at", -1).to_list(length=limit)
        return {"items": items, "total": len(items), "summary": await _totals(db, user_id, driver_id)}

    async def _post(driver_id: str, settlement_type: SettlementType, payload: SettlementCreate, actor: dict[str, Any]) -> dict[str, Any]:
        user_id = _merchant_user_id(actor)
        driver = await _driver_or_404(db, user_id, driver_id)
        await ensure_store_delivery_settlement_indexes(db)
        totals = await _totals(db, user_id, driver_id)
        amount = float(money(payload.amount))
        earning_offset = float(money(payload.earning_offset))
        if settlement_type in {"cod_remittance", "earning_payment"} and amount <= 0:
            raise HTTPException(status_code=422, detail={"code": "store_delivery_settlement_amount_required"})
        cod_settled = amount + earning_offset if settlement_type == "net_settlement" else amount if settlement_type == "cod_remittance" else 0.0
        fee_settled = earning_offset if settlement_type == "net_settlement" else amount if settlement_type == "earning_payment" else 0.0
        if settlement_type == "net_settlement" and earning_offset <= 0:
            raise HTTPException(status_code=422, detail={"code": "store_driver_net_offset_required"})
        if cod_settled > totals["cod_cash_custody"] + 0.0001:
            raise HTTPException(status_code=409, detail={"code": "store_delivery_settlement_exceeds_balance", "available": totals["cod_cash_custody"]})
        if fee_settled > totals["delivery_earnings_due"] + 0.0001:
            raise HTTPException(status_code=409, detail={"code": "store_delivery_settlement_exceeds_balance", "available": totals["delivery_earnings_due"]})
        account = None
        if amount > 0 and not payload.account_id:
            raise HTTPException(status_code=422, detail={"code": "settlement_account_required"})
        if payload.account_id:
            account = await db.accounts.find_one({"user_id": user_id, "id": normalize_text(payload.account_id), "status": "active"}, {"_id": 0, "id": 1, "name": 1, "provider": 1, "account_type": 1})
            if not account:
                raise HTTPException(status_code=422, detail={"code": "settlement_account_invalid"})
            if account.get("account_type") not in {"bank", "cash"}:
                raise HTTPException(status_code=422, detail={"code": "settlement_account_must_be_bank_or_cash"})
        now = _now()
        settlement_id = str(uuid.uuid4())
        accounting = await post_settlement_journal(
            db, user_id=user_id, actor_id=normalize_text(actor.get("id")), actor_name=normalize_text(actor.get("name")) or "accountant",
            settlement_id=settlement_id, driver=driver, account=account or {"id": "", "name": ""}, settlement_type=settlement_type,
            bank_amount=amount, earning_offset=earning_offset, reference=payload.reference, note=payload.note,
        )
        row = {
            "id": settlement_id, "user_id": user_id, "driver_id": driver_id, "driver_name_snapshot": driver.get("name"),
            "settlement_type": settlement_type, "amount": amount, "account_id": normalize_text(payload.account_id),
            "account_name_snapshot": (account or {}).get("name") or (account or {}).get("provider"),
            "cod_settled_amount": round(cod_settled, 2), "delivery_fee_settled_amount": round(fee_settled, 2), "earning_offset": earning_offset,
            "reference": normalize_text(payload.reference), "note": normalize_text(payload.note), "status": "posted", "accounting_status": "posted",
            "ledger_txn_group_id": accounting.get("txn_group_id"), "accounting_operation_id": "MZ2-FIN-CUTOVER-001",
            "created_at": now, "created_by": normalize_text(actor.get("id")),
        }
        await db[SETTLEMENTS].insert_one(row)
        row.pop("_id", None); row.pop("user_id", None)
        return {"settlement": row, "summary": await _totals(db, user_id, driver_id)}

    @router.post("/driver/{driver_id}/cod-remittance", status_code=201)
    async def cod_remittance(driver_id: str, payload: SettlementCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
        return await _post(driver_id, "cod_remittance", payload, _require_accountant(user))

    @router.post("/driver/{driver_id}/earning-payment", status_code=201)
    async def earning_payment(driver_id: str, payload: SettlementCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
        return await _post(driver_id, "earning_payment", payload, _require_accountant(user))

    @router.post("/driver/{driver_id}/net-settlement", status_code=201)
    async def net_settlement(driver_id: str, payload: SettlementCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
        return await _post(driver_id, "net_settlement", payload, _require_accountant(user))

    @router.get("/driver/{driver_id}/pending-account")
    async def pending_account(driver_id: str, limit: int = Query(default=100, ge=1, le=500), user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_pending_owner(user)
        user_id = normalize_text(actor.get("id"))
        driver = await _driver_or_404(db, user_id, driver_id)
        await ensure_pending_financial_indexes(db)
        transactions = await db[PENDING_FINANCIAL_TRANSACTIONS].find(
            {"user_id": user_id, "driver_id": driver_id}, {"_id": 0, "user_id": 0}
        ).sort("created_at", -1).to_list(length=limit)
        pending_count = await db[PENDING_FINANCIAL_TRANSACTIONS].count_documents({
            "user_id": user_id, "driver_id": driver_id, "status": "active", "accounting_eligible": True, "accounting_sent": False,
        })
        return {
            "driver": {"id": driver.get("id"), "name": driver.get("name"), "phone": driver.get("phone")},
            "totals": await _pending_account(db, user_id, driver_id),
            "transactions": transactions,
            "accounting": {"pending_eligible_count": pending_count, "destination": "mezan2_finance", "sending_enabled": False},
        }

    @router.post("/driver/{driver_id}/pending-transactions", status_code=201)
    async def create_pending_transaction(driver_id: str, payload: PendingTransactionCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_pending_owner(user)
        user_id = normalize_text(actor.get("id"))
        driver = await _driver_or_404(db, user_id, driver_id)
        await ensure_pending_financial_indexes(db)
        existing = await db[PENDING_FINANCIAL_TRANSACTIONS].find_one({"user_id": user_id, "client_request_id": payload.client_request_id}, {"_id": 0, "user_id": 0})
        if existing:
            if existing.get("driver_id") != driver_id or existing.get("transaction_type") != payload.transaction_type or int(existing.get("amount_halalas") or 0) != payload.amount_halalas:
                raise HTTPException(status_code=409, detail={"code": "store_driver_financial_request_conflict"})
            return {"transaction": existing, "totals": await _pending_account(db, user_id, driver_id), "idempotent_replay": True}
        totals = await _pending_account(db, user_id, driver_id)
        if payload.transaction_type == "cod_receipt" and payload.amount_halalas > totals["cod_due_halalas"]:
            raise HTTPException(status_code=409, detail={"code": "store_driver_cod_receipt_exceeds_due", "available_halalas": totals["cod_due_halalas"]})
        if payload.transaction_type == "delivery_payout" and payload.amount_halalas > totals["delivery_fee_due_halalas"]:
            raise HTTPException(status_code=409, detail={"code": "store_driver_delivery_payout_exceeds_due", "available_halalas": totals["delivery_fee_due_halalas"]})
        if payload.transaction_type == "offset" and payload.amount_halalas > totals["max_offset_halalas"]:
            raise HTTPException(status_code=409, detail={"code": "store_driver_offset_exceeds_due", "available_halalas": totals["max_offset_halalas"]})
        now = _now()
        transaction_id = str(uuid.uuid4())
        reference = f"DRVFIN-{transaction_id.split('-')[0].upper()}"
        row = {
            "id": transaction_id, "user_id": user_id, "driver_id": driver_id, "driver_name_snapshot": driver.get("name"),
            "transaction_type": payload.transaction_type, "amount_halalas": payload.amount_halalas,
            "payment_method": "offset" if payload.transaction_type == "offset" else normalize_text(payload.payment_method) or "cash",
            "note": normalize_text(payload.note), "occurred_at": normalize_text(payload.occurred_at) or now,
            "reference": reference, "client_request_id": payload.client_request_id, "status": "active",
            "accounting_eligible": True, "accounting_sent": False, "accounting_reference": None,
            "accounting_destination": "mezan2_finance", "created_at": now,
            "created_by_id": normalize_text(actor.get("id")), "created_by_name": normalize_text(actor.get("name")),
        }
        try:
            await db[PENDING_FINANCIAL_TRANSACTIONS].insert_one(row)
        except Exception:
            replay = await db[PENDING_FINANCIAL_TRANSACTIONS].find_one({"user_id": user_id, "client_request_id": payload.client_request_id}, {"_id": 0, "user_id": 0})
            if replay:
                return {"transaction": replay, "totals": await _pending_account(db, user_id, driver_id), "idempotent_replay": True}
            raise
        return {"transaction": _public_pending_row(row), "totals": await _pending_account(db, user_id, driver_id), "idempotent_replay": False}

    @router.post("/driver/{driver_id}/pending-transactions/{transaction_id}/reverse")
    async def reverse_pending_transaction(driver_id: str, transaction_id: str, payload: PendingTransactionReverse, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_pending_owner(user)
        user_id = normalize_text(actor.get("id"))
        await _driver_or_404(db, user_id, driver_id)
        row = await db[PENDING_FINANCIAL_TRANSACTIONS].find_one({"user_id": user_id, "driver_id": driver_id, "id": transaction_id})
        if not row:
            raise HTTPException(status_code=404, detail={"code": "store_driver_financial_transaction_not_found"})
        if row.get("accounting_sent") is True:
            raise HTTPException(status_code=409, detail={"code": "store_driver_financial_transaction_already_sent"})
        if row.get("status") == "reversed":
            return {"transaction": _public_pending_row(row), "totals": await _pending_account(db, user_id, driver_id), "idempotent_replay": True}
        now = _now()
        await db[PENDING_FINANCIAL_TRANSACTIONS].update_one(
            {"user_id": user_id, "driver_id": driver_id, "id": transaction_id, "status": "active"},
            {"$set": {"status": "reversed", "accounting_eligible": False, "reversed_at": now, "reversed_by_id": normalize_text(actor.get("id")), "reversed_by_name": normalize_text(actor.get("name")), "reversal_reason": normalize_text(payload.reason)}},
        )
        updated = await db[PENDING_FINANCIAL_TRANSACTIONS].find_one({"user_id": user_id, "driver_id": driver_id, "id": transaction_id}, {"_id": 0, "user_id": 0})
        return {"transaction": updated, "totals": await _pending_account(db, user_id, driver_id), "idempotent_replay": False}

    return router


__all__ = ["SETTLEMENTS", "PENDING_FINANCIAL_TRANSACTIONS", "make_store_delivery_settlement_router"]
