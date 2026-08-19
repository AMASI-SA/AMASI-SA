"""Audited settlements for Amasi store drivers.

COD cash custody and driver delivery earnings are separate liabilities. This
router records accountant-approved remittances/payouts without silently netting
one against the other. It intentionally does not post to the general ledger yet;
that bridge can be added after accounting mapping is explicitly approved.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from store_delivery_domain import money, normalize_text
from store_delivery_driver_app_routes import DRIVER_COLLECTIONS, DRIVER_EARNINGS
from store_delivery_driver_routes import STORE_DRIVERS

SETTLEMENTS = "store_delivery_driver_settlements"
SettlementType = Literal["cod_remittance", "earning_payment"]


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


class SettlementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float = Field(gt=0, le=10_000_000)
    account_id: str | None = Field(default=None, max_length=120)
    reference: str = Field(default="", max_length=240)
    note: str = Field(default="", max_length=1000)


async def ensure_store_delivery_settlement_indexes(db: Any) -> None:
    await db[SETTLEMENTS].create_index([("user_id", 1), ("id", 1)], unique=True)
    await db[SETTLEMENTS].create_index([("user_id", 1), ("driver_id", 1), ("settlement_type", 1), ("created_at", -1)])


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
        {"_id": 0, "amount": 1, "settlement_type": 1},
    ).to_list(length=100000)

    earned = round(sum(float(row.get("amount") or 0) for row in earnings), 2)
    cash_collected = round(sum(float(row.get("cod_custody_amount") or 0) for row in collections), 2)
    cod_remitted = round(sum(float(row.get("amount") or 0) for row in settlements if row.get("settlement_type") == "cod_remittance"), 2)
    earnings_paid = round(sum(float(row.get("amount") or 0) for row in settlements if row.get("settlement_type") == "earning_payment"), 2)
    return {
        "delivery_earnings_total": earned,
        "delivery_earnings_paid": earnings_paid,
        "delivery_earnings_due": round(max(earned - earnings_paid, 0), 2),
        "cod_cash_collected": cash_collected,
        "cod_cash_remitted": cod_remitted,
        "cod_cash_custody": round(max(cash_collected - cod_remitted, 0), 2),
    }


def make_store_delivery_settlement_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/settlements", tags=["Store Delivery Settlements"])

    @router.get("/drivers")
    async def settlement_drivers(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_accountant(user)
        user_id = _merchant_user_id(actor)
        drivers = await db[STORE_DRIVERS].find(
            {"user_id": user_id},
            {"_id": 0, "user_id": 0, "city_key": 0, "notes": 0},
        ).sort([("status", 1), ("name", 1)]).to_list(length=1000)
        items = []
        for driver in drivers:
            items.append({
                "id": driver.get("id"),
                "name": driver.get("name"),
                "phone": driver.get("phone"),
                "city": driver.get("city"),
                "status": driver.get("status"),
                **await _totals(db, user_id, driver.get("id")),
            })
        return {"items": items, "total": len(items)}

    @router.get("/driver/{driver_id}/summary")
    async def driver_summary(driver_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_accountant(user)
        user_id = _merchant_user_id(actor)
        driver = await _driver_or_404(db, user_id, driver_id)
        return {"driver": {"id": driver["id"], "name": driver.get("name")}, **await _totals(db, user_id, driver_id)}

    @router.get("/driver/{driver_id}")
    async def list_settlements(
        driver_id: str,
        limit: int = Query(default=250, ge=1, le=1000),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_accountant(user)
        user_id = _merchant_user_id(actor)
        await _driver_or_404(db, user_id, driver_id)
        items = await db[SETTLEMENTS].find(
            {"user_id": user_id, "driver_id": driver_id}, {"_id": 0, "user_id": 0}
        ).sort("created_at", -1).to_list(length=limit)
        return {"items": items, "total": len(items), "summary": await _totals(db, user_id, driver_id)}

    async def _post(driver_id: str, settlement_type: SettlementType, payload: SettlementCreate, actor: dict[str, Any]) -> dict[str, Any]:
        user_id = _merchant_user_id(actor)
        driver = await _driver_or_404(db, user_id, driver_id)
        await ensure_store_delivery_settlement_indexes(db)
        totals = await _totals(db, user_id, driver_id)
        amount = float(money(payload.amount))
        available = totals["cod_cash_custody"] if settlement_type == "cod_remittance" else totals["delivery_earnings_due"]
        if amount > available + 0.0001:
            raise HTTPException(status_code=409, detail={"code": "store_delivery_settlement_exceeds_balance", "available": available})
        account = None
        if payload.account_id:
            account = await db.accounts.find_one(
                {"user_id": user_id, "id": normalize_text(payload.account_id), "status": "active"},
                {"_id": 0, "id": 1, "name": 1, "provider": 1, "account_type": 1},
            )
            if not account:
                raise HTTPException(status_code=422, detail={"code": "settlement_account_invalid"})
        now = _now()
        row = {
            "id": str(uuid.uuid4()), "user_id": user_id, "driver_id": driver_id,
            "driver_name_snapshot": driver.get("name"), "settlement_type": settlement_type,
            "amount": amount, "account_id": normalize_text(payload.account_id),
            "account_name_snapshot": (account or {}).get("name") or (account or {}).get("provider"),
            "reference": normalize_text(payload.reference), "note": normalize_text(payload.note),
            "status": "posted", "created_at": now, "created_by": normalize_text(actor.get("id")),
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

    return router


__all__ = ["SETTLEMENTS", "make_store_delivery_settlement_router"]
