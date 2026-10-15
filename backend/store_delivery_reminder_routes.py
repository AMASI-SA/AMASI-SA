"""Reminder API for Amasi Delivery commitments.

The standalone driver app polls this endpoint. The backend computes reminder
windows in Riyadh time and persists the last emitted code per instruction so the
same reminder is not emitted repeatedly on every poll.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from store_delivery_customer_instruction_routes import STORE_DELIVERY_INSTRUCTIONS
from store_delivery_domain import normalize_text
from store_delivery_driver_routes import STORE_DRIVERS
from store_delivery_handover_routes import ASSIGNMENTS
from store_delivery_reminders import reminder_decision

RIYADH_TZ = ZoneInfo("Asia/Riyadh")


def _require_store_driver(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict) or normalize_text(user.get("role")).casefold() != "store_driver":
        raise HTTPException(status_code=403, detail={"code": "store_driver_account_required"})
    return user


async def _driver_for_user(db: Any, user: dict[str, Any]) -> dict[str, Any]:
    owner_id = normalize_text(user.get("created_by"))
    row = await db[STORE_DRIVERS].find_one(
        {"user_id": owner_id, "account_user_id": normalize_text(user.get("id")), "status": "active"},
        {"_id": 0},
    )
    if not row:
        raise HTTPException(status_code=403, detail={"code": "store_driver_profile_not_linked"})
    return row


def make_store_delivery_reminder_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/app/reminders", tags=["Amasi Delivery Reminders"])

    @router.get("")
    async def reminders(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        merchant_id = normalize_text(driver.get("user_id"))
        instructions = await db[STORE_DELIVERY_INSTRUCTIONS].find(
            {"user_id": merchant_id, "driver_id": driver["id"], "status": "active"},
            {"_id": 0},
        ).to_list(length=1000)
        if not instructions:
            return {"items": [], "total": 0, "generated_at": datetime.now(RIYADH_TZ).isoformat()}

        order_ids = sorted({normalize_text(row.get("order_id")) for row in instructions if normalize_text(row.get("order_id"))})
        assignments = await db[ASSIGNMENTS].find(
            {"user_id": merchant_id, "driver_id": driver["id"], "order_id": {"$in": order_ids}, "active": True},
            {"_id": 0, "order_id": 1, "order_number": 1, "status": 1},
        ).to_list(length=2000)
        assignment_by_order = {normalize_text(row.get("order_id")): row for row in assignments}

        now = datetime.now(RIYADH_TZ)
        emitted: list[dict[str, Any]] = []
        for instruction in instructions:
            order_id = normalize_text(instruction.get("order_id"))
            assignment = assignment_by_order.get(order_id) or {}
            decision = reminder_decision(
                instruction,
                now=now,
                delivery_status=normalize_text(assignment.get("status")),
                last_reminder_code=normalize_text(instruction.get("last_reminder_code")) or None,
            )
            if not decision.due:
                continue
            emitted.append({
                "instruction_id": instruction.get("id"),
                "order_id": order_id,
                "order_number": assignment.get("order_number") or order_id,
                "code": decision.code,
                "message": decision.message,
                "overdue": decision.overdue,
                "minutes_to_deadline": decision.minutes_to_deadline,
                "priority": instruction.get("priority"),
                "delivery_date": instruction.get("delivery_date"),
                "delivery_time": instruction.get("delivery_time"),
            })
            await db[STORE_DELIVERY_INSTRUCTIONS].update_one(
                {"user_id": merchant_id, "id": instruction.get("id"), "status": "active"},
                {"$set": {
                    "last_reminder_code": decision.code,
                    "last_reminder_at": now.isoformat(),
                }},
            )
        return {"items": emitted, "total": len(emitted), "generated_at": now.isoformat()}

    return router


__all__ = ["make_store_delivery_reminder_router"]
