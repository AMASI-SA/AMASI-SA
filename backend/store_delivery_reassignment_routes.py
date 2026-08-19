"""Safe reassignment of an undelivered store-driver shipment."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from store_delivery_customer_instruction_routes import STORE_DELIVERY_INSTRUCTIONS
from store_delivery_domain import StoreDeliveryRuleError, assignment_snapshot, normalize_text
from store_delivery_driver_routes import STORE_DRIVERS
from store_delivery_handover_routes import ASSIGNMENTS, EVENTS, ORDERS, _order_city

HANDOVER_PERMISSION = "store_delivery.handover"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merchant_user_id(user: dict[str, Any]) -> str:
    if normalize_text(user.get("role")).casefold() == "owner" or user.get("is_owner") is True:
        return normalize_text(user.get("id"))
    owner_id = normalize_text(user.get("created_by"))
    if not owner_id:
        raise HTTPException(status_code=409, detail={"code": "employee_store_not_linked"})
    return owner_id


def _require_operator(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict):
        raise HTTPException(status_code=403, detail={"code": "store_delivery_handover_permission_required"})
    role = normalize_text(user.get("role")).casefold()
    extra = set(user.get("extra_permissions") or [])
    denied = set(user.get("denied_permissions") or [])
    allowed = role in {"owner", "admin", "operations", "shipping"} or user.get("is_owner") is True or HANDOVER_PERMISSION in extra
    if not allowed or HANDOVER_PERMISSION in denied:
        raise HTTPException(status_code=403, detail={"code": "store_delivery_handover_permission_required"})
    return user


class ReassignPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    driver_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(default="", max_length=500)


def make_store_delivery_reassignment_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/assignments", tags=["Store Delivery Reassignment"])

    @router.post("/{assignment_id}/reassign")
    async def reassign(assignment_id: str, payload: ReassignPayload,
                       user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_operator(user)
        user_id = _merchant_user_id(actor)
        old = await db[ASSIGNMENTS].find_one(
            {"user_id": user_id, "id": assignment_id, "active": True}, {"_id": 0}
        )
        if not old:
            raise HTTPException(status_code=404, detail={"code": "store_delivery_assignment_not_found"})
        if old.get("status") == "delivered":
            raise HTTPException(status_code=409, detail={"code": "delivered_assignment_cannot_be_reassigned"})
        if old.get("driver_id") == payload.driver_id:
            raise HTTPException(status_code=409, detail={"code": "assignment_already_with_driver"})

        driver = await db[STORE_DRIVERS].find_one(
            {"user_id": user_id, "id": payload.driver_id, "status": "active"}, {"_id": 0}
        )
        if not driver:
            raise HTTPException(status_code=404, detail={"code": "store_driver_not_found_or_inactive"})
        order = await db[ORDERS].find_one(
            {"user_id": user_id, "$or": [{"order_id": old.get("order_id")}, {"order_number": old.get("order_number")}]},
            {"_id": 0},
        )
        if not order:
            raise HTTPException(status_code=409, detail={"code": "canonical_order_not_found"})
        try:
            snapshot = assignment_snapshot(driver=driver, shipping_city=_order_city(order))
        except StoreDeliveryRuleError as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc

        now = _now()
        new_id = str(uuid.uuid4())
        new_row = {
            "id": new_id, "user_id": user_id, "session_id": None,
            "order_id": old.get("order_id"), "order_number": old.get("order_number"),
            "barcode": old.get("barcode"), **snapshot,
            "status": "assigned", "active": True, "assigned_at": now,
            "assigned_by": normalize_text(actor.get("id")), "delivered_at": None,
            "reassigned_from_assignment_id": old["id"],
            "reassignment_reason": normalize_text(payload.reason),
        }
        await db[ASSIGNMENTS].insert_one(new_row)
        old_update = await db[ASSIGNMENTS].update_one(
            {"user_id": user_id, "id": old["id"], "active": True, "status": old.get("status")},
            {"$set": {
                "active": False, "status": "reassigned", "reassigned_at": now,
                "reassigned_by": normalize_text(actor.get("id")),
                "reassigned_to_assignment_id": new_id,
                "reassignment_reason": normalize_text(payload.reason), "updated_at": now,
            }},
        )
        if old_update.modified_count != 1:
            await db[ASSIGNMENTS].delete_one({"user_id": user_id, "id": new_id})
            raise HTTPException(status_code=409, detail={"code": "assignment_reassign_conflict"})

        await db[STORE_DELIVERY_INSTRUCTIONS].update_many(
            {"user_id": user_id, "order_id": old.get("order_id"), "status": "active"},
            {"$set": {
                "driver_id": driver["id"], "driver_name_snapshot": driver.get("name"),
                "acknowledged_at": None, "acknowledged_by_driver_id": None,
                "updated_at": now, "updated_by": normalize_text(actor.get("id")),
            }},
        )
        await db[ORDERS].update_one(
            {"user_id": user_id, "$or": [{"order_id": old.get("order_id")}, {"order_number": old.get("order_number")}]},
            {"$set": {
                "store_delivery_assignment_id": new_id,
                "store_delivery_driver_id": driver["id"],
                "store_delivery_status": "assigned",
                "store_delivery_updated_at": now,
            }},
        )
        await db[EVENTS].insert_one({
            "id": str(uuid.uuid4()), "user_id": user_id,
            "event_type": "store_delivery_reassigned", "order_id": old.get("order_id"),
            "old_assignment_id": old["id"], "new_assignment_id": new_id,
            "old_driver_id": old.get("driver_id"), "new_driver_id": driver["id"],
            "reason": normalize_text(payload.reason),
            "actor_id": normalize_text(actor.get("id")), "occurred_at": now,
        })
        new_row.pop("_id", None); new_row.pop("user_id", None)
        return {"ok": True, "old_assignment_id": old["id"], "assignment": new_row}

    return router


__all__ = ["make_store_delivery_reassignment_router"]
