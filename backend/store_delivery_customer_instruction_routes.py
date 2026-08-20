"""Customer-service delivery instructions for Amasi Delivery V1.

Instructions are structured commitments, not free-form notes. Drivers may only
acknowledge them; customer service/operations may create or revise them.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from store_delivery_domain import normalize_text

STORE_DELIVERY_INSTRUCTIONS = "store_delivery_instructions"
STORE_DELIVERY_INSTRUCTION_EVENTS = "store_delivery_instruction_events"

InstructionType = Literal["urgent", "scheduled", "do_not_deliver_today", "call_before_arrival", "general"]
Priority = Literal["normal", "high", "urgent"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merchant_user_id(user: dict[str, Any]) -> str:
    if normalize_text(user.get("role")).casefold() == "owner" or user.get("is_owner") is True:
        return normalize_text(user.get("id"))
    owner_id = normalize_text(user.get("created_by"))
    if not owner_id:
        raise HTTPException(status_code=409, detail={"code": "employee_store_not_linked"})
    return owner_id


def _require_customer_service(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict):
        raise HTTPException(status_code=403, detail={"code": "delivery_instruction_permission_required"})
    role = normalize_text(user.get("role")).casefold()
    granted = set(user.get("extra_permissions") or []) | set(user.get("permissions") or []) | set(user.get("effective_permissions") or [])
    denied = set(user.get("denied_permissions") or [])
    delivery_permission = "store_delivery.instructions.manage"
    customer_service_permission = "customer_intelligence.inbox.read"
    role_allowed = role in {"owner", "admin", "operations", "customer_service"} or user.get("is_owner") is True
    permission_allowed = (
        delivery_permission in granted or customer_service_permission in granted
    )
    explicitly_denied = (
        delivery_permission in denied
        or (customer_service_permission in granted and customer_service_permission in denied)
    )
    if not (role_allowed or permission_allowed) or explicitly_denied:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "delivery_instruction_permission_required"})
    return user


def _require_store_driver(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict) or normalize_text(user.get("role")).casefold() != "store_driver":
        raise HTTPException(status_code=403, detail={"code": "store_driver_only"})
    return user


class DeliveryInstructionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=120)
    instruction_type: InstructionType
    priority: Priority = "normal"
    note: str = Field(default="", max_length=1200)
    delivery_date: str | None = Field(default=None, max_length=10)
    delivery_time: str | None = Field(default=None, max_length=5)


class DeliveryInstructionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction_type: InstructionType | None = None
    priority: Priority | None = None
    note: str | None = Field(default=None, max_length=1200)
    delivery_date: str | None = Field(default=None, max_length=10)
    delivery_time: str | None = Field(default=None, max_length=5)
    expected_version: int = Field(ge=1)


def _validate_schedule(payload: Any) -> None:
    if payload.instruction_type == "scheduled" and not payload.delivery_date:
        raise HTTPException(status_code=422, detail={"code": "delivery_date_required"})
    if payload.delivery_date:
        try:
            datetime.strptime(payload.delivery_date, "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "delivery_date_invalid"}) from exc
    if payload.delivery_time:
        try:
            datetime.strptime(payload.delivery_time, "%H:%M")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "delivery_time_invalid"}) from exc


async def ensure_store_delivery_instruction_indexes(db: Any) -> None:
    await db[STORE_DELIVERY_INSTRUCTIONS].create_index([("user_id", 1), ("id", 1)], unique=True)
    await db[STORE_DELIVERY_INSTRUCTIONS].create_index([("user_id", 1), ("order_id", 1), ("status", 1)])
    await db[STORE_DELIVERY_INSTRUCTIONS].create_index([("user_id", 1), ("driver_id", 1), ("delivery_date", 1)])
    await db[STORE_DELIVERY_INSTRUCTION_EVENTS].create_index([("user_id", 1), ("instruction_id", 1), ("occurred_at", -1)])


async def _event(db: Any, *, user_id: str, instruction_id: str, event_type: str, actor_id: str, payload: dict[str, Any] | None = None) -> None:
    await db[STORE_DELIVERY_INSTRUCTION_EVENTS].insert_one({
        "id": str(uuid.uuid4()), "user_id": user_id, "instruction_id": instruction_id,
        "event_type": event_type, "actor_id": actor_id, "payload": payload or {}, "occurred_at": _now(),
    })


def make_store_delivery_customer_instruction_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/instructions", tags=["Store Delivery Instructions"])

    @router.post("", status_code=201)
    async def create_instruction(payload: DeliveryInstructionCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_customer_service(user); user_id = _merchant_user_id(actor)
        _validate_schedule(payload); await ensure_store_delivery_instruction_indexes(db)
        assignment = await db.store_delivery_assignments.find_one(
            {
                "user_id": user_id,
                "order_id": normalize_text(payload.order_id),
                "active": True,
                "status": {"$in": ["assigned", "out_for_delivery"]},
            },
            {"_id": 0, "driver_id": 1, "driver_name_snapshot": 1},
        )
        if not assignment:
            raise HTTPException(status_code=409, detail={"code": "order_has_no_active_store_driver"})
        now = _now(); instruction_id = str(uuid.uuid4())
        doc = {
            "id": instruction_id, "user_id": user_id, "order_id": normalize_text(payload.order_id),
            "driver_id": assignment.get("driver_id"), "driver_name_snapshot": assignment.get("driver_name_snapshot"),
            "instruction_type": payload.instruction_type, "priority": payload.priority,
            "note": normalize_text(payload.note), "delivery_date": payload.delivery_date,
            "delivery_time": payload.delivery_time, "status": "active", "acknowledged_at": None,
            "acknowledged_by_driver_id": None, "version": 1, "created_at": now,
            "created_by": normalize_text(actor.get("id")), "updated_at": now,
        }
        await db[STORE_DELIVERY_INSTRUCTIONS].insert_one(doc)
        await _event(db, user_id=user_id, instruction_id=instruction_id, event_type="delivery_instruction_created",
                     actor_id=normalize_text(actor.get("id")), payload={"order_id": doc["order_id"], "driver_id": doc["driver_id"]})
        doc.pop("_id", None); doc.pop("user_id", None)
        return doc

    @router.patch("/{instruction_id}")
    async def update_instruction(instruction_id: str, payload: DeliveryInstructionUpdate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_customer_service(user); user_id = _merchant_user_id(actor)
        current = await db[STORE_DELIVERY_INSTRUCTIONS].find_one({"user_id": user_id, "id": instruction_id, "status": "active"}, {"_id": 0})
        if not current:
            raise HTTPException(status_code=404, detail={"code": "delivery_instruction_not_found"})
        if int(current.get("version") or 1) != payload.expected_version:
            raise HTTPException(status_code=409, detail={"code": "delivery_instruction_version_conflict"})
        merged = DeliveryInstructionCreate(
            order_id=current["order_id"], instruction_type=payload.instruction_type or current["instruction_type"],
            priority=payload.priority or current["priority"], note=current.get("note", "") if payload.note is None else payload.note,
            delivery_date=current.get("delivery_date") if payload.delivery_date is None else payload.delivery_date,
            delivery_time=current.get("delivery_time") if payload.delivery_time is None else payload.delivery_time,
        )
        _validate_schedule(merged); now = _now()
        patch = {
            "instruction_type": merged.instruction_type, "priority": merged.priority,
            "note": normalize_text(merged.note), "delivery_date": merged.delivery_date,
            "delivery_time": merged.delivery_time, "acknowledged_at": None,
            "acknowledged_by_driver_id": None, "version": payload.expected_version + 1,
            "updated_at": now, "updated_by": normalize_text(actor.get("id")),
            "last_reminder_code": None, "last_reminder_at": None,
        }
        result = await db[STORE_DELIVERY_INSTRUCTIONS].find_one_and_update(
            {"user_id": user_id, "id": instruction_id, "version": payload.expected_version}, {"$set": patch},
            return_document=True, projection={"_id": 0, "user_id": 0})
        if not result:
            raise HTTPException(status_code=409, detail={"code": "delivery_instruction_version_conflict"})
        await _event(db, user_id=user_id, instruction_id=instruction_id, event_type="delivery_instruction_updated",
                     actor_id=normalize_text(actor.get("id")), payload={"previous_version": payload.expected_version})
        return result

    @router.get("/order/{order_id}")
    async def order_instructions(order_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_customer_service(user); user_id = _merchant_user_id(actor)
        items = await db[STORE_DELIVERY_INSTRUCTIONS].find(
            {"user_id": user_id, "order_id": normalize_text(order_id)}, {"_id": 0, "user_id": 0}
        ).sort("created_at", -1).to_list(length=200)
        return {"items": items, "total": len(items)}

    @router.get("/driver/me")
    async def my_instructions(include_acknowledged: bool = Query(default=True), user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user); owner_id = normalize_text(actor.get("created_by"))
        driver = await db.store_drivers.find_one(
            {"user_id": owner_id, "account_user_id": normalize_text(actor.get("id")), "status": "active"}, {"_id": 0, "id": 1})
        if not driver:
            raise HTTPException(status_code=403, detail={"code": "store_driver_account_not_linked"})
        query: dict[str, Any] = {"user_id": owner_id, "driver_id": driver["id"], "status": "active"}
        if not include_acknowledged: query["acknowledged_at"] = None
        items = await db[STORE_DELIVERY_INSTRUCTIONS].find(query, {"_id": 0, "user_id": 0}).sort(
            [("delivery_date", 1), ("delivery_time", 1), ("created_at", 1)]).to_list(length=500)
        return {"items": items, "total": len(items)}

    @router.post("/{instruction_id}/acknowledge")
    async def acknowledge_instruction(instruction_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user); owner_id = normalize_text(actor.get("created_by"))
        driver = await db.store_drivers.find_one(
            {"user_id": owner_id, "account_user_id": normalize_text(actor.get("id")), "status": "active"}, {"_id": 0, "id": 1})
        if not driver:
            raise HTTPException(status_code=403, detail={"code": "store_driver_account_not_linked"})
        now = _now()
        result = await db[STORE_DELIVERY_INSTRUCTIONS].find_one_and_update(
            {"user_id": owner_id, "id": instruction_id, "driver_id": driver["id"], "status": "active"},
            {"$set": {"acknowledged_at": now, "acknowledged_by_driver_id": driver["id"]}},
            return_document=True, projection={"_id": 0, "user_id": 0})
        if not result:
            raise HTTPException(status_code=404, detail={"code": "delivery_instruction_not_found"})
        await _event(db, user_id=owner_id, instruction_id=instruction_id, event_type="delivery_instruction_acknowledged",
                     actor_id=normalize_text(actor.get("id")), payload={"driver_id": driver["id"]})
        return result

    return router


__all__ = ["DeliveryInstructionCreate", "DeliveryInstructionUpdate", "ensure_store_delivery_instruction_indexes", "make_store_delivery_customer_instruction_router", "_require_customer_service"]
