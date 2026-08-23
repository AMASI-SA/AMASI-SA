"""Store-driver management API for Amasi Delivery V1.

This router owns driver master data and the deliberately restricted login
identity used by the standalone Amasi Delivery app. A driver login is stored
with role ``store_driver``. The legacy RBAC resolver treats unknown roles as
zero-permission, so the account receives no Mezan dashboard permissions; only
store-delivery app endpoints explicitly accepting ``store_driver`` are usable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError

from auth import hash_password, validate_bcrypt_secret
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
STORE_DELIVERY_ASSIGNMENTS = "store_delivery_assignments"
MANAGE_PERMISSION = "store_delivery.manage"
DRIVER_ACCOUNT_ROLE = "store_driver"
DRIVER_PIN_LENGTH = 6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_driver_pin(value: str) -> str:
    validate_bcrypt_secret(value)
    if len(value) != DRIVER_PIN_LENGTH or not value.isascii() or not value.isdigit():
        raise ValueError("driver PIN must contain exactly 6 ASCII digits")
    return value


def _merchant_user_id(user: dict[str, Any]) -> str:
    role = normalize_text(user.get("role")).casefold()
    if role == "owner" or user.get("is_owner") is True:
        return normalize_text(user.get("id"))
    owner_id = normalize_text(user.get("created_by"))
    if not owner_id:
        raise HTTPException(status_code=409, detail={"code": "employee_store_not_linked"})
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
            detail={"code": "store_delivery_permission_required", "message": "تحتاج صلاحية إدارة موصلي المتجر."},
        )
    return user


def _require_account_manager(user: Any) -> dict[str, Any]:
    actor = _require_manager(user)
    role = normalize_text(actor.get("role")).casefold()
    if role not in {"owner", "admin"} and actor.get("is_owner") is not True:
        raise HTTPException(status_code=403, detail={"code": "store_driver_account_management_owner_required"})
    return actor


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
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=DRIVER_PIN_LENGTH, max_length=DRIVER_PIN_LENGTH)

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: str | None) -> str | None:
        return None if value is None else _validate_driver_pin(value)

    @model_validator(mode="after")
    def login_fields_together(self) -> "DriverCreate":
        if (self.email is None) != (self.password is None):
            raise ValueError("email and password must be provided together")
        return self

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


class DriverAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=DRIVER_PIN_LENGTH, max_length=DRIVER_PIN_LENGTH)
    _valid_pin = field_validator("password")(_validate_driver_pin)


class DriverPasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: str = Field(min_length=DRIVER_PIN_LENGTH, max_length=DRIVER_PIN_LENGTH)
    _valid_pin = field_validator("new_password")(_validate_driver_pin)


async def ensure_store_driver_indexes(db: Any) -> None:
    await db[STORE_DRIVERS].create_index([("user_id", 1), ("id", 1)], unique=True)
    await db[STORE_DRIVERS].create_index([("user_id", 1), ("phone", 1)], unique=True)
    await db[STORE_DRIVERS].create_index([("user_id", 1), ("city_key", 1), ("status", 1)])
    await db[STORE_DRIVERS].create_index(
        [("account_user_id", 1)], unique=True,
        partialFilterExpression={"account_user_id": {"$type": "string"}},
    )
    await db[STORE_DRIVER_EVENTS].create_index([("user_id", 1), ("driver_id", 1), ("occurred_at", -1)])


async def _event(db: Any, *, user_id: str, driver_id: str, event_type: str, actor_id: str, payload: dict[str, Any] | None = None) -> None:
    await db[STORE_DRIVER_EVENTS].insert_one({
        "id": str(uuid.uuid4()), "user_id": user_id, "driver_id": driver_id,
        "event_type": event_type, "actor_id": actor_id, "payload": payload or {}, "occurred_at": _now(),
    })


def _public_driver(doc: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in doc.items() if key not in {"_id", "user_id", "city_key"}}


def _driver_delivery_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Build the mobile-list counters from the assignment source of truth."""
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        driver_id = normalize_text(row.get("driver_id"))
        if not driver_id:
            continue
        bucket = counts.setdefault(driver_id, {
            "assigned_count": 0,
            "out_for_delivery_count": 0,
            "current_delivery_count": 0,
            "delivered_count": 0,
        })
        assignment_status = normalize_text(row.get("status"))
        key = f"{assignment_status}_count"
        if key in bucket:
            bucket[key] += 1
        if assignment_status in {"assigned", "out_for_delivery"}:
            bucket["current_delivery_count"] += 1
    return counts


def _change_history_payload(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    ignored = {"city_key", "updated_at", "updated_by", "version"}
    changed_fields = sorted(key for key in patch if key not in ignored and current.get(key) != patch.get(key))
    return {
        "changed_fields": changed_fields,
        "before": {key: current.get(key) for key in changed_fields},
        "after": {key: patch.get(key) for key in changed_fields},
    }


async def _driver_or_404(db: Any, user_id: str, driver_id: str) -> dict[str, Any]:
    row = await db[STORE_DRIVERS].find_one({"user_id": user_id, "id": driver_id}, {"_id": 0})
    if not row:
        raise HTTPException(status_code=404, detail={"code": "store_driver_not_found"})
    return row


def make_store_delivery_driver_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/drivers", tags=["Store Delivery Drivers"])

    @router.post("", status_code=201)
    async def create_driver(payload: DriverCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        await ensure_store_driver_indexes(db)
        account_email = str(payload.email).strip().lower() if payload.email is not None else ""
        account_id = str(uuid.uuid4()) if account_email else None
        if account_email:
            _require_account_manager(actor)
            if await db.users.find_one({"email": account_email}, {"_id": 1}):
                raise HTTPException(status_code=409, detail={"code": "store_driver_account_email_exists"})
        try:
            fee = float(money(payload.delivery_fee))
        except StoreDeliveryRuleError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
        now = _now(); driver_id = str(uuid.uuid4())
        doc = {
            "id": driver_id, "user_id": user_id, "name": normalize_text(payload.name),
            "phone": normalize_text(payload.phone), "city": normalize_text(payload.city),
            "city_key": normalize_text(payload.city).casefold(), "region": normalize_text(payload.region),
            "district": normalize_text(payload.district), "street": normalize_text(payload.street),
            "coverage_mode": COVERAGE_MODE_CITY, "delivery_fee": fee, "status": payload.status,
            "notes": normalize_text(payload.notes), "account_user_id": account_id,
            "account_email": account_email or None, "account_role": DRIVER_ACCOUNT_ROLE,
            "version": 1, "created_at": now, "updated_at": now,
            "created_by": normalize_text(actor.get("id")),
        }
        if account_id:
            account = {
                "id": account_id, "name": doc["name"] or "موصل المتجر", "email": account_email,
                "password_hash": hash_password(payload.password or ""), "role": DRIVER_ACCOUNT_ROLE,
                "extra_permissions": [], "denied_permissions": [], "created_at": now,
                "created_by": user_id, "linked_driver_id": driver_id, "account_type": DRIVER_ACCOUNT_ROLE,
                "disabled": payload.status != DRIVER_STATUS_ACTIVE,
                "is_active": payload.status == DRIVER_STATUS_ACTIVE,
            }
            try:
                await db.users.insert_one(account)
            except DuplicateKeyError as exc:
                raise HTTPException(status_code=409, detail={"code": "store_driver_account_email_exists"}) from exc
        try:
            await db[STORE_DRIVERS].insert_one(doc)
        except DuplicateKeyError as exc:
            if account_id:
                await db.users.delete_one({"id": account_id, "role": DRIVER_ACCOUNT_ROLE})
            raise HTTPException(status_code=409, detail={"code": "store_driver_phone_exists"}) from exc
        await _event(db, user_id=user_id, driver_id=driver_id, event_type="store_driver_created",
                     actor_id=normalize_text(actor.get("id")), payload={"city": doc["city"], "delivery_fee": fee, "account_created": bool(account_id), "email": account_email or None})
        return _public_driver(doc)

    @router.get("")
    async def list_drivers(status_filter: str | None = Query(default=None, alias="status"), city: str | None = None,
                           user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user); user_id = _merchant_user_id(actor)
        query: dict[str, Any] = {"user_id": user_id}
        if status_filter: query["status"] = normalize_text(status_filter)
        if city: query["city_key"] = normalize_text(city).casefold()
        items = await db[STORE_DRIVERS].find(query, {"_id": 0, "user_id": 0, "city_key": 0}).sort(
            [("status", 1), ("name", 1)]).to_list(length=1000)
        assignment_rows = await db[STORE_DELIVERY_ASSIGNMENTS].find(
            {
                "user_id": user_id,
                "active": True,
                "status": {"$in": ["assigned", "out_for_delivery", "delivered"]},
            },
            {"_id": 0, "driver_id": 1, "status": 1},
        ).to_list(length=100000)
        counts = _driver_delivery_counts(assignment_rows)
        empty_counts = {
            "assigned_count": 0,
            "out_for_delivery_count": 0,
            "current_delivery_count": 0,
            "delivered_count": 0,
        }
        for item in items:
            item["delivery_counts"] = counts.get(normalize_text(item.get("id")), empty_counts.copy())
        return {"items": items, "total": len(items), "coverage_mode": COVERAGE_MODE_CITY}

    @router.get("/{driver_id}")
    async def get_driver(driver_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user); user_id = _merchant_user_id(actor)
        return _public_driver(await _driver_or_404(db, user_id, driver_id))

    @router.patch("/{driver_id}")
    async def update_driver(driver_id: str, payload: DriverUpdate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user); user_id = _merchant_user_id(actor)
        current = await _driver_or_404(db, user_id, driver_id)
        if int(current.get("version") or 1) != payload.expected_version:
            raise HTTPException(status_code=409, detail={"code": "store_driver_version_conflict"})
        changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"}); patch: dict[str, Any] = {}
        for key in ("name", "phone", "city", "region", "district", "street", "notes", "status"):
            if key in changes and changes[key] is not None: patch[key] = normalize_text(changes[key])
        if "city" in patch: patch["city_key"] = patch["city"].casefold()
        if "delivery_fee" in changes and changes["delivery_fee"] is not None:
            patch["delivery_fee"] = float(money(changes["delivery_fee"]))
        patch.update({"updated_at": _now(), "updated_by": normalize_text(actor.get("id")), "version": payload.expected_version + 1})
        try:
            result = await db[STORE_DRIVERS].find_one_and_update(
                {"user_id": user_id, "id": driver_id, "version": payload.expected_version}, {"$set": patch},
                return_document=True, projection={"_id": 0, "user_id": 0, "city_key": 0})
        except DuplicateKeyError as exc:
            raise HTTPException(status_code=409, detail={"code": "store_driver_phone_exists"}) from exc
        if not result: raise HTTPException(status_code=409, detail={"code": "store_driver_version_conflict"})
        if "status" in patch and current.get("account_user_id"):
            active = patch["status"] == DRIVER_STATUS_ACTIVE
            await db.users.update_one(
                {"id": current["account_user_id"], "role": DRIVER_ACCOUNT_ROLE, "linked_driver_id": driver_id},
                {"$set": {"disabled": not active, "is_active": active, "updated_at": _now()}},
            )
        await _event(db, user_id=user_id, driver_id=driver_id, event_type="store_driver_updated",
                     actor_id=normalize_text(actor.get("id")), payload=_change_history_payload(current, patch))
        return result

    @router.post("/{driver_id}/account", status_code=201)
    async def create_driver_account(driver_id: str, payload: DriverAccountCreate,
                                    user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_account_manager(user); user_id = _merchant_user_id(actor)
        await ensure_store_driver_indexes(db)
        driver = await _driver_or_404(db, user_id, driver_id)
        if driver.get("account_user_id"):
            raise HTTPException(status_code=409, detail={"code": "store_driver_account_already_linked"})
        email = str(payload.email).strip().lower()
        if await db.users.find_one({"email": email}, {"_id": 1}):
            raise HTTPException(status_code=409, detail={"code": "store_driver_account_email_exists"})
        now = _now(); account_id = str(uuid.uuid4())
        account = {
            "id": account_id, "name": driver.get("name") or "موصل المتجر", "email": email,
            "password_hash": hash_password(payload.password), "role": DRIVER_ACCOUNT_ROLE,
            "extra_permissions": [], "denied_permissions": [], "created_at": now,
            "created_by": user_id, "linked_driver_id": driver_id, "account_type": DRIVER_ACCOUNT_ROLE,
            "disabled": driver.get("status") != DRIVER_STATUS_ACTIVE,
            "is_active": driver.get("status") == DRIVER_STATUS_ACTIVE,
        }
        await db.users.insert_one(account)
        linked = await db[STORE_DRIVERS].update_one(
            {"user_id": user_id, "id": driver_id, "account_user_id": None},
            {"$set": {"account_user_id": account_id, "account_email": email,
                      "account_role": DRIVER_ACCOUNT_ROLE,
                      "updated_at": now, "updated_by": normalize_text(actor.get("id"))}},
        )
        if linked.modified_count != 1:
            await db.users.delete_one({"id": account_id, "role": DRIVER_ACCOUNT_ROLE})
            raise HTTPException(status_code=409, detail={"code": "store_driver_account_link_conflict"})
        await _event(db, user_id=user_id, driver_id=driver_id, event_type="store_driver_account_created",
                     actor_id=normalize_text(actor.get("id")), payload={"account_user_id": account_id, "email": email})
        return {"id": account_id, "email": email, "role": DRIVER_ACCOUNT_ROLE,
                "driver_id": driver_id, "effective_permissions": [], "is_active": account["is_active"]}

    @router.put("/{driver_id}/account/password")
    async def reset_driver_password(driver_id: str, payload: DriverPasswordReset,
                                    user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_account_manager(user); user_id = _merchant_user_id(actor)
        driver = await _driver_or_404(db, user_id, driver_id); account_id = driver.get("account_user_id")
        if not account_id: raise HTTPException(status_code=409, detail={"code": "store_driver_account_not_linked"})
        result = await db.users.update_one(
            {"id": account_id, "role": DRIVER_ACCOUNT_ROLE, "linked_driver_id": driver_id},
            {"$set": {"password_hash": hash_password(payload.new_password), "password_updated_at": _now()}},
        )
        if result.matched_count != 1: raise HTTPException(status_code=409, detail={"code": "store_driver_account_link_broken"})
        await _event(db, user_id=user_id, driver_id=driver_id, event_type="store_driver_password_reset",
                     actor_id=normalize_text(actor.get("id")))
        return {"ok": True, "driver_id": driver_id}

    @router.delete("/{driver_id}/account")
    async def disable_driver_account(driver_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_account_manager(user); user_id = _merchant_user_id(actor)
        driver = await _driver_or_404(db, user_id, driver_id); account_id = driver.get("account_user_id")
        if not account_id: return {"ok": True, "already_unlinked": True}
        now = _now()
        await db.users.update_one(
            {"id": account_id, "role": DRIVER_ACCOUNT_ROLE, "linked_driver_id": driver_id},
            {"$set": {"disabled": True, "is_active": False, "updated_at": now, "unlinked_at": now}},
        )
        await db[STORE_DRIVERS].update_one(
            {"user_id": user_id, "id": driver_id},
            {"$set": {"account_user_id": None, "account_email": None,
                      "updated_at": now, "updated_by": normalize_text(actor.get("id"))}},
        )
        await _event(db, user_id=user_id, driver_id=driver_id, event_type="store_driver_account_unlinked",
                     actor_id=normalize_text(actor.get("id")), payload={"account_user_id": account_id})
        return {"ok": True, "driver_id": driver_id}

    @router.get("/{driver_id}/events")
    async def list_driver_events(driver_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user); user_id = _merchant_user_id(actor)
        await _driver_or_404(db, user_id, driver_id)
        items = await db[STORE_DRIVER_EVENTS].find(
            {"user_id": user_id, "driver_id": driver_id}, {"_id": 0, "user_id": 0}).sort(
            "occurred_at", -1).to_list(length=500)
        return {"items": items, "total": len(items)}

    return router


__all__ = [
    "DRIVER_ACCOUNT_ROLE", "DriverAccountCreate", "DriverCreate", "DriverPasswordReset", "DriverUpdate",
    "ensure_store_driver_indexes", "make_store_delivery_driver_router",
]
