"""Bulk handover sessions for Amasi store drivers.

The operator selects one driver, scans shipment barcodes, and the backend validates
city coverage before any assignment is created. Accepted rows are committed in one
explicit confirmation step with immutable driver/fee snapshots.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from store_delivery_domain import StoreDeliveryRuleError, assignment_snapshot, normalize_text
from store_delivery_driver_routes import STORE_DRIVERS

SESSIONS = "store_delivery_handover_sessions"
ASSIGNMENTS = "store_delivery_assignments"
EVENTS = "store_delivery_events"
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


def _order_city(order: dict[str, Any]) -> str:
    candidates = [
        order.get("shipping_city"),
        order.get("city"),
        (order.get("shipping_address") or {}).get("city"),
        (order.get("customer") or {}).get("city"),
        (order.get("receiver") or {}).get("city"),
    ]
    for value in candidates:
        text = normalize_text(value)
        if text:
            return text
    return ""


def _order_number(order: dict[str, Any]) -> str:
    return normalize_text(order.get("order_number") or order.get("reference_id") or order.get("id"))


def _barcode_candidates(barcode: str) -> list[dict[str, Any]]:
    value = normalize_text(barcode)
    return [
        {"shipping_barcode": value},
        {"tracking_number": value},
        {"barcode": value},
        {"order_number": value},
        {"reference_id": value},
        {"id": value},
    ]


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    driver_id: str = Field(min_length=1, max_length=80)


class ScanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    barcode: str = Field(min_length=1, max_length=180)


async def ensure_store_delivery_handover_indexes(db: Any) -> None:
    await db[SESSIONS].create_index([("user_id", 1), ("id", 1)], unique=True)
    await db[SESSIONS].create_index([("user_id", 1), ("driver_id", 1), ("status", 1)])
    await db[ASSIGNMENTS].create_index([("user_id", 1), ("id", 1)], unique=True)
    await db[ASSIGNMENTS].create_index([("user_id", 1), ("order_id", 1), ("active", 1)])
    await db[EVENTS].create_index([("user_id", 1), ("occurred_at", -1)])


def make_store_delivery_handover_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/handover", tags=["Store Delivery Handover"])

    @router.post("/sessions", status_code=201)
    async def create_session(payload: SessionCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_operator(user)
        user_id = _merchant_user_id(actor)
        await ensure_store_delivery_handover_indexes(db)
        driver = await db[STORE_DRIVERS].find_one({"user_id": user_id, "id": payload.driver_id}, {"_id": 0})
        if not driver:
            raise HTTPException(status_code=404, detail={"code": "store_driver_not_found"})
        if driver.get("status") != "active":
            raise HTTPException(status_code=409, detail={"code": "driver_inactive"})
        now = _now()
        session = {
            "id": str(uuid.uuid4()), "user_id": user_id, "driver_id": driver["id"],
            "driver_name_snapshot": driver.get("name"), "driver_city_snapshot": driver.get("city"),
            "delivery_fee_snapshot": driver.get("delivery_fee"), "status": "open",
            "accepted": [], "rejected": [], "started_at": now,
            "started_by": normalize_text(actor.get("id")), "confirmed_at": None,
        }
        await db[SESSIONS].insert_one(session)
        session.pop("_id", None); session.pop("user_id", None)
        return session

    @router.post("/sessions/{session_id}/scan")
    async def scan_shipment(session_id: str, payload: ScanIn, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_operator(user)
        user_id = _merchant_user_id(actor)
        session = await db[SESSIONS].find_one({"user_id": user_id, "id": session_id}, {"_id": 0})
        if not session:
            raise HTTPException(status_code=404, detail={"code": "handover_session_not_found"})
        if session.get("status") != "open":
            raise HTTPException(status_code=409, detail={"code": "handover_session_closed"})
        driver = await db[STORE_DRIVERS].find_one({"user_id": user_id, "id": session["driver_id"]}, {"_id": 0})
        barcode = normalize_text(payload.barcode)
        order = await db.orders.find_one({"user_id": user_id, "$or": _barcode_candidates(barcode)}, {"_id": 0})
        if not order:
            rejected = {"barcode": barcode, "code": "shipment_not_found", "scanned_at": _now()}
            await db[SESSIONS].update_one({"user_id": user_id, "id": session_id}, {"$push": {"rejected": rejected}})
            return {"accepted": False, **rejected}
        if any(normalize_text(row.get("order_id")) == normalize_text(order.get("id")) for row in session.get("accepted") or []):
            return {"accepted": False, "barcode": barcode, "code": "shipment_already_scanned"}
        active = await db[ASSIGNMENTS].find_one({"user_id": user_id, "order_id": order.get("id"), "active": True}, {"_id": 1})
        if active:
            return {"accepted": False, "barcode": barcode, "code": "shipment_already_assigned"}
        city = _order_city(order)
        try:
            snapshot = assignment_snapshot(driver=driver, shipping_city=city)
        except StoreDeliveryRuleError as exc:
            rejected = {"barcode": barcode, "order_id": order.get("id"), "order_number": _order_number(order), "shipping_city": city, "code": str(exc), "scanned_at": _now()}
            await db[SESSIONS].update_one({"user_id": user_id, "id": session_id}, {"$push": {"rejected": rejected}})
            return {"accepted": False, **rejected}
        accepted = {
            "barcode": barcode, "order_id": order.get("id"), "order_number": _order_number(order),
            "shipping_city": city, **snapshot, "scanned_at": _now(),
        }
        await db[SESSIONS].update_one({"user_id": user_id, "id": session_id}, {"$push": {"accepted": accepted}})
        return {"accepted": True, "shipment": accepted}

    @router.post("/sessions/{session_id}/confirm")
    async def confirm_session(session_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_operator(user)
        user_id = _merchant_user_id(actor)
        session = await db[SESSIONS].find_one({"user_id": user_id, "id": session_id}, {"_id": 0})
        if not session:
            raise HTTPException(status_code=404, detail={"code": "handover_session_not_found"})
        if session.get("status") != "open":
            raise HTTPException(status_code=409, detail={"code": "handover_session_closed"})
        accepted = list(session.get("accepted") or [])
        if not accepted:
            raise HTTPException(status_code=409, detail={"code": "handover_session_empty"})
        now = _now(); created = []
        for item in accepted:
            existing = await db[ASSIGNMENTS].find_one({"user_id": user_id, "order_id": item["order_id"], "active": True}, {"_id": 1})
            if existing:
                raise HTTPException(status_code=409, detail={"code": "shipment_already_assigned", "order_id": item["order_id"]})
            row = {
                "id": str(uuid.uuid4()), "user_id": user_id, "session_id": session_id,
                "order_id": item["order_id"], "order_number": item.get("order_number"),
                "driver_id": item["driver_id"], "driver_name_snapshot": item["driver_name_snapshot"],
                "driver_city_snapshot": item["driver_city_snapshot"], "shipping_city_snapshot": item["shipping_city_snapshot"],
                "delivery_fee_snapshot": item["delivery_fee_snapshot"], "coverage_mode_snapshot": item["coverage_mode_snapshot"],
                "status": "assigned", "active": True, "assigned_at": now,
                "assigned_by": normalize_text(actor.get("id")), "delivered_at": None,
            }
            await db[ASSIGNMENTS].insert_one(row); row.pop("_id", None); row.pop("user_id", None); created.append(row)
        await db[SESSIONS].update_one({"user_id": user_id, "id": session_id, "status": "open"}, {"$set": {"status": "confirmed", "confirmed_at": now, "confirmed_by": normalize_text(actor.get("id")), "assigned_count": len(created)}})
        return {"confirmed": True, "session_id": session_id, "assigned_count": len(created), "assignments": created}

    return router


__all__ = ["make_store_delivery_handover_router", "ensure_store_delivery_handover_indexes", "ASSIGNMENTS", "SESSIONS"]
