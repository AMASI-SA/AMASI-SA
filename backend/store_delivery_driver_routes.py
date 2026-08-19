"""Store-driver management API for Amasi Delivery V1.

This first slice manages driver identity, city coverage metadata and current
per-delivery fee. Assignment/payment/app-only actions are intentionally kept out
of this router so management permissions cannot leak into the driver app.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.errors import DuplicateKeyError

from store_delivery_domain import (
    COVERAGE_MODE_CITY,
    DRIVER_STATUS_ACTIVE,
    DRIVER_STATUS_INACTIVE,
    StoreDeliveryRuleError,
    money,
    normalize_text,
)

STORE_DRIVERS = "store_drivers"
STORE_DRIVER_EVENTS = "store_driver_events"
MANAGE_PERMISSION = "store_delivery.manage"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merchant_user_id(user: dict[str, Any]) -> str:
    role = normalize_text(user.get("role")).casefold()
    if role == "owner" or user.get("is_owner") is True:
        return normalize_text(user.get("id"))
    owner_id = normalize_text(user.get("created_by"))
    if not owner_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "employee_store_not_linked"},
        )
    return owner_id


def _require_manager(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict):
        raise HTTPException(status_code=403, detail={"code": "store_delivery_permission_required"})
    role = normalize_text(user.get("role")).casefold()
    denied = set(user.get("denied_permissions") or [])
    extra = set(user.get("extra_permissions") or [])
    allowed = (
        role in {"owner", "admin", "operations", "hr"}
        or user.get("is_owner") is True
        or MANAGE_PERMISSION in extra
    ) and MANAGE_PERMISSION not in denied
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "store_delivery_permission_required",
                "message": "تحتاج صلاحية إدارة موصلي المتجر.",
            },
        )
    return user


class DriverCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=1, max_length=40)
    city: str = Field(min_length=1, max_length=100)
    region: str = Field(default="", max_length=120)
    district: str = Field(default="", max_length=120)
    street: str = Field(default="", max_length=180)
    delivery_fee: float = Field(ge=0, le=10000)
    status: str = DRIVER_STATUS_ACTIVE
    coverage_mode: str = COVERAGE_MODE_CITY
    notes: str = Field(default="", max_length=1000)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        value = normalize_text(value)
        if value not in {DRIVER_STATUS_ACTIVE, DRIVER_STATUS_INACTIVE}:
            raise ValueError("invalid driver status")
        return value

    @field_validator("coverage_mode")
    @classmethod
    def valid_coverage_mode(cls, value: str) -> str:
        value = normalize_text(value) or COVERAGE_MODE_CITY
        if value != COVERAGE_MODE_CITY:
            raise ValueError("only city coverage is enabled in V1")
        return value


class DriverUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, min_length=1, max_length=40)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    region: str | None = Field(default=None, max_length=120)
    district: str | None = Field(default=None, max_length=120)
    street: str | None = Field(default=None, max_length=180)
    delivery_fee: float | None = Field(default=None, ge=0, le=10000)
    status: str | None = None
    notes: str | None = Field(default=None, max_length=1000)
    expected_version: int = Field(ge=1)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = normalize_text(value)
        if value not in {DRIVER_STATUS_ACTIVE, DRIVER_STATUS_INACTIVE}:
            raise ValueError("invalid driver status")
        return value


async def ensure_store_driver_indexes(db: Any) -> None:
    await db[STORE_DRIVERS].create_index([("user_id", 1), ("id", 1)], unique=True)
    await db[STORE_DRIVERS].create_index([("user_id", 1), ("phone", 1)], unique=True)
    await db[STORE_DRIVERS].create_index([("user_id", 1), ("city_key", 1), ("status", 1)])
    await db[STORE_DRIVER_EVENTS].create_index([("user_id", 1), ("driver_id", 1), ("occurred_at", -1)])


async def _event(db: Any, *, user_id: str, driver_id: str, event_type: str, actor_id: str, payload: dict[str, Any] | None = None) -> None:
    await db[STORE_DRIVER_EVENTS].insert_one({
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "driver_id": driver_id,
        "event_type": event_type,
        "actor_id": actor_id,
        "payload": payload or {},
        "occurred_at": _now(),
    })


def _public_driver(doc: dict[str, Any]) -> dict[str, Any]:
    row = {key: value for key, value in doc.items() if key not in {"_id", "user_id", "city_key"}}
    return row


def make_store_delivery_driver_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/drivers", tags=["Store Delivery Drivers"])

    @router.post("", status_code=201)
    async def create_driver(payload: DriverCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        await ensure_store_driver_indexes(db)
        try:
            fee = float(money(payload.delivery_fee))
        except StoreDeliveryRuleError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
        now = _now()
        driver_id = str(uuid.uuid4())
        doc = {
            "id": driver_id,
            "user_id": user_id,
            "name": normalize_text(payload.name),
            "phone": normalize_text(payload.phone),
            "city": normalize_text(payload.city),
            "city_key": normalize_text(payload.city).casefold(),
            "region": normalize_text(payload.region),
            "district": normalize_text(payload.district),
            "street": normalize_text(payload.street),
            "coverage_mode": COVERAGE_MODE_CITY,
            "delivery_fee": fee,
            "status": payload.status,
            "notes": normalize_text(payload.notes),
            "account_user_id": None,
            "account_role": "store_driver",
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "created_by": normalize_text(actor.get("id")),
        }
        try:
            await db[STORE_DRIVERS].insert_one(doc)
        except DuplicateKeyError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "store_driver_phone_exists"},
            ) from exc
        await _event(
            db,
            user_id=user_id,
            driver_id=driver_id,
            event_type="store_driver_created",
            actor_id=normalize_text(actor.get("id")),
            payload={"city": doc["city"], "delivery_fee": fee},
        )
        return _public_driver(doc)

    @router.get("")
    async def list_drivers(
        status_filter: str | None = Query(default=None, alias="status"),
        city: str | None = None,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        query: dict[str, Any] = {"user_id": user_id}
        if status_filter:
            query["status"] = normalize_text(status_filter)
        if city:
            query["city_key"] = normalize_text(city).casefold()
        items = await db[STORE_DRIVERS].find(query, {"_id": 0, "user_id": 0, "city_key": 0}).sort(
            [("status", 1), ("name", 1)]
        ).to_list(length=1000)
        return {"items": items, "total": len(items), "coverage_mode": COVERAGE_MODE_CITY}

    @router.get("/{driver_id}")
    async def get_driver(driver_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        row = await db[STORE_DRIVERS].find_one(
            {"user_id": user_id, "id": driver_id},
            {"_id": 0, "user_id": 0, "city_key": 0},
        )
        if not row:
            raise HTTPException(status_code=404, detail={"code": "store_driver_not_found"})
        return row

    @router.patch("/{driver_id}")
    async def update_driver(driver_id: str, payload: DriverUpdate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        current = await db[STORE_DRIVERS].find_one({"user_id": user_id, "id": driver_id}, {"_id": 0})
        if not current:
            raise HTTPException(status_code=404, detail={"code": "store_driver_not_found"})
        if int(current.get("version") or 1) != payload.expected_version:
            raise HTTPException(status_code=409, detail={"code": "store_driver_version_conflict"})

        changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
        patch: dict[str, Any] = {}
        for key in ("name", "phone", "city", "region", "district", "street", "notes", "status"):
            if key in changes and changes[key] is not None:
                patch[key] = normalize_text(changes[key])
        if "city" in patch:
            patch["city_key"] = patch["city"].casefold()
        if "delivery_fee" in changes and changes["delivery_fee"] is not None:
            patch["delivery_fee"] = float(money(changes["delivery_fee"]))
        patch["updated_at"] = _now()
        patch["updated_by"] = normalize_text(actor.get("id"))
        patch["version"] = payload.expected_version + 1

        try:
            result = await db[STORE_DRIVERS].find_one_and_update(
                {"user_id": user_id, "id": driver_id, "version": payload.expected_version},
                {"$set": patch},
                return_document=True,
                projection={"_id": 0, "user_id": 0, "city_key": 0},
            )
        except DuplicateKeyError as exc:
            raise HTTPException(status_code=409, detail={"code": "store_driver_phone_exists"}) from exc
        if not result:
            raise HTTPException(status_code=409, detail={"code": "store_driver_version_conflict"})
        await _event(
            db,
            user_id=user_id,
            driver_id=driver_id,
            event_type="store_driver_updated",
            actor_id=normalize_text(actor.get("id")),
            payload={"changed_fields": sorted(patch.keys())},
        )
        return result

    @router.get("/{driver_id}/events")
    async def list_driver_events(driver_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        exists = await db[STORE_DRIVERS].find_one({"user_id": user_id, "id": driver_id}, {"_id": 1})
        if not exists:
            raise HTTPException(status_code=404, detail={"code": "store_driver_not_found"})
        items = await db[STORE_DRIVER_EVENTS].find(
            {"user_id": user_id, "driver_id": driver_id}, {"_id": 0, "user_id": 0}
        ).sort("occurred_at", -1).to_list(length=500)
        return {"items": items, "total": len(items)}

    return router


__all__ = [
    "DriverCreate",
    "DriverUpdate",
    "ensure_store_driver_indexes",
    "make_store_delivery_driver_router",
]
