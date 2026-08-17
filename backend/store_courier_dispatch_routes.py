"""Store-courier dispatch and assignment workflow.

The dispatcher selects one eligible courier, then scans the QR printed on a
store-courier label.  The QR value is the order number only.  A successful scan
atomically assigns the shipment to the selected courier; it never reuses the
external-carrier handoff state.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING

from ai_store_access_contract import (
    PERMISSIONS,
    ROLE_ASSIGNMENTS,
    effective_permissions,
)
from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order


WORKFLOWS = "order_review_workflows"
EVENTS = "mezan_fulfillment_events_v2"
ASSIGN_PERMISSION = "fulfillment.store_courier.assign"
DELIVER_PERMISSION = "fulfillment.store_courier.deliver"
ASSIGNED_WAITING_PICKUP = "assigned_waiting_pickup"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


class AssignStoreCourierShipmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    courier_user_id: str = Field(min_length=1, max_length=128)
    barcode: str = Field(min_length=1, max_length=256)


async def ensure_store_courier_dispatch_indexes(db: Any) -> None:
    await db[WORKFLOWS].create_index(
        [
            ("user_id", ASCENDING),
            ("carrier_label_type", ASCENDING),
            ("store_courier_assignee_id", ASCENDING),
            ("store_courier_assignment_state", ASCENDING),
            ("store_courier_assigned_at", DESCENDING),
        ],
        name="ix_store_courier_assignment_v2",
    )


async def _actor_context(db: Any, user: dict[str, Any]) -> dict[str, Any]:
    actor_id = _text(user.get("id"))
    role = _text(user.get("role")).casefold()
    if role == "owner" or user.get("is_owner") is True:
        return {
            "actor_id": actor_id,
            "merchant_id": actor_id,
            "is_owner": True,
            "permissions": set(PERMISSIONS),
        }

    merchant_id = _text(user.get("created_by") or user.get("merchant_id"))
    if not merchant_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "employee_store_not_linked"},
        )
    assignment = await db[ROLE_ASSIGNMENTS].find_one(
        {"user_id": actor_id},
        {"_id": 0},
    )
    return {
        "actor_id": actor_id,
        "merchant_id": merchant_id,
        "is_owner": False,
        "permissions": set(effective_permissions(assignment)),
    }


def _require_permission(context: dict[str, Any], permission: str) -> None:
    if permission not in context["permissions"]:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "fulfillment_permission_required",
                "permission": permission,
            },
        )


def _member_is_active(member: dict[str, Any]) -> bool:
    return bool(
        member
        and member.get("disabled") is not True
        and member.get("is_active") is not False
        and not member.get("deleted_at")
    )


async def _eligible_courier(
    db: Any,
    *,
    merchant_id: str,
    courier_user_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    courier_id = _text(courier_user_id)
    member = await db.users.find_one(
        {"id": courier_id},
        {
            "_id": 0,
            "id": 1,
            "name": 1,
            "email": 1,
            "phone": 1,
            "role": 1,
            "created_by": 1,
            "merchant_id": 1,
            "disabled": 1,
            "is_active": 1,
            "deleted_at": 1,
        },
    )
    belongs_to_store = bool(
        member
        and merchant_id
        in {
            _text(member.get("created_by")),
            _text(member.get("merchant_id")),
        }
    )
    if not member or not belongs_to_store:
        raise HTTPException(
            status_code=404,
            detail={"code": "store_courier_not_found"},
        )
    if not _member_is_active(member):
        raise HTTPException(
            status_code=409,
            detail={"code": "store_courier_account_inactive"},
        )

    assignment = await db[ROLE_ASSIGNMENTS].find_one(
        {"user_id": courier_id},
        {"_id": 0},
    ) or {}
    if DELIVER_PERMISSION not in set(effective_permissions(assignment)):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "store_courier_not_eligible",
                "courier_user_id": courier_id,
            },
        )
    return member, assignment


async def _courier_rows(db: Any, *, merchant_id: str) -> list[dict[str, Any]]:
    members = await db.users.find(
        {
            "$or": [
                {"created_by": merchant_id},
                {"merchant_id": merchant_id},
            ],
        },
        {
            "_id": 0,
            "id": 1,
            "name": 1,
            "email": 1,
            "phone": 1,
            "role": 1,
            "disabled": 1,
            "is_active": 1,
            "deleted_at": 1,
        },
    ).to_list(length=5000)
    member_ids = [_text(row.get("id")) for row in members if _text(row.get("id"))]
    assignments = await db[ROLE_ASSIGNMENTS].find(
        {"user_id": {"$in": member_ids}},
        {"_id": 0},
    ).to_list(length=max(1, len(member_ids)))
    assignments_by_user = {
        _text(row.get("user_id")): row for row in assignments
    }

    couriers = []
    for member in members:
        courier_id = _text(member.get("id"))
        assignment = assignments_by_user.get(courier_id) or {}
        if (
            not courier_id
            or not _member_is_active(member)
            or DELIVER_PERMISSION not in set(effective_permissions(assignment))
        ):
            continue
        couriers.append({
            "id": courier_id,
            "name": _text(member.get("name")) or _text(member.get("email")),
            "email": _text(member.get("email")) or None,
            "phone": _text(member.get("phone")) or None,
            "role_key": assignment.get("role_key"),
        })

    courier_ids = [row["id"] for row in couriers]
    counts: dict[str, dict[str, int]] = {}
    if courier_ids:
        pipeline = [
            {
                "$match": {
                    "user_id": merchant_id,
                    "carrier_label_type": "store_courier",
                    "store_courier_assignee_id": {"$in": courier_ids},
                }
            },
            {
                "$group": {
                    "_id": "$store_courier_assignee_id",
                    "assigned_count": {
                        "$sum": {
                            "$cond": [
                                {"$ne": ["$stage", "delivered"]},
                                1,
                                0,
                            ]
                        }
                    },
                    "waiting_pickup_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$eq": [
                                        "$store_courier_assignment_state",
                                        ASSIGNED_WAITING_PICKUP,
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "delivering_count": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$stage", "delivering"]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
        ]
        async for row in db[WORKFLOWS].aggregate(pipeline):
            counts[_text(row.get("_id"))] = {
                "assigned_count": int(row.get("assigned_count") or 0),
                "waiting_pickup_count": int(row.get("waiting_pickup_count") or 0),
                "delivering_count": int(row.get("delivering_count") or 0),
            }

    for courier in couriers:
        courier.update(counts.get(courier["id"]) or {
            "assigned_count": 0,
            "waiting_pickup_count": 0,
            "delivering_count": 0,
        })
    couriers.sort(key=lambda row: (row["name"].casefold(), row["id"]))
    return couriers


async def _shipment_view(
    repository: MongoOrderRepository,
    *,
    merchant_id: str,
    workflow: dict[str, Any],
) -> dict[str, Any]:
    order_number = _text(workflow.get("order_number"))
    base = {
        "order_number": order_number,
        "customer_name": None,
        "customer_mobile": None,
        "city": None,
        "carrier_label_type": workflow.get("carrier_label_type"),
        "assignment_state": workflow.get("store_courier_assignment_state"),
        "courier_user_id": workflow.get("store_courier_assignee_id"),
        "courier_name": workflow.get("store_courier_assignee_name"),
        "assigned_at": workflow.get("store_courier_assigned_at"),
        "assigned_by_name": workflow.get("store_courier_assigned_by_name"),
        "stage": workflow.get("stage"),
    }
    try:
        order = await get_order(
            repository,
            user_id=merchant_id,
            order_number=order_number,
        )
    except OrderNotFoundError:
        return base
    base.update({
        "customer_name": order.customer.name,
        "customer_mobile": order.customer.mobile,
        "city": order.shipping.address.city if order.shipping.address else None,
    })
    return base


def make_store_courier_dispatch_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/store-courier-dispatch",
        tags=["Store Courier Dispatch"],
    )
    repository = MongoOrderRepository(db)

    @router.get("/couriers")
    async def list_store_couriers(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, ASSIGN_PERMISSION)
        await ensure_store_courier_dispatch_indexes(db)
        couriers = await _courier_rows(
            db,
            merchant_id=context["merchant_id"],
        )
        return {
            "items": couriers,
            "total": len(couriers),
            "selection_required_before_scan": True,
        }

    @router.get("/assignments")
    async def list_store_courier_assignments(
        courier_user_id: str | None = Query(default=None, max_length=128),
        limit: int = Query(default=100, ge=1, le=300),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, ASSIGN_PERMISSION)
        await ensure_store_courier_dispatch_indexes(db)
        courier_id = _text(courier_user_id)
        if courier_id:
            await _eligible_courier(
                db,
                merchant_id=context["merchant_id"],
                courier_user_id=courier_id,
            )
        query: dict[str, Any] = {
            "user_id": context["merchant_id"],
            "carrier_label_type": "store_courier",
            "store_courier_assignee_id": {"$nin": [None, ""]},
        }
        if courier_id:
            query["store_courier_assignee_id"] = courier_id
        workflows = await db[WORKFLOWS].find(
            query,
            {"_id": 0},
        ).sort("store_courier_assigned_at", -1).limit(limit).to_list(limit)
        items = [
            await _shipment_view(
                repository,
                merchant_id=context["merchant_id"],
                workflow=workflow,
            )
            for workflow in workflows
        ]
        return {
            "items": items,
            "total": len(items),
            "courier_user_id": courier_id or None,
        }

    @router.post("/assign")
    async def assign_store_courier_shipment(
        payload: AssignStoreCourierShipmentRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, ASSIGN_PERMISSION)
        await ensure_store_courier_dispatch_indexes(db)
        courier, _assignment = await _eligible_courier(
            db,
            merchant_id=context["merchant_id"],
            courier_user_id=payload.courier_user_id,
        )
        order_number = _text(payload.barcode)
        workflow = await db[WORKFLOWS].find_one(
            {
                "user_id": context["merchant_id"],
                "order_number": order_number,
            },
            {"_id": 0},
        )
        if not workflow:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "store_courier_shipment_not_found",
                    "order_number": order_number,
                },
            )
        if _text(workflow.get("carrier_label_type")) != "store_courier":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_courier_label_required",
                    "order_number": order_number,
                },
            )
        if workflow.get("carrier_label_ready") is not True:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_courier_label_not_ready",
                    "order_number": order_number,
                },
            )
        if (
            _text(workflow.get("stage")) != "completed"
            or _text(workflow.get("assembly_status")) != "completed"
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_courier_order_not_completed",
                    "order_number": order_number,
                },
            )

        selected_courier_id = _text(courier.get("id"))
        selected_courier_name = (
            _text(courier.get("name"))
            or _text(courier.get("email"))
            or "مندوب التوصيل"
        )
        existing_courier_id = _text(
            workflow.get("store_courier_assignee_id")
        )
        if existing_courier_id:
            if existing_courier_id == selected_courier_id:
                return {
                    "ok": True,
                    "already_assigned": True,
                    "courier": {
                        "id": selected_courier_id,
                        "name": selected_courier_name,
                    },
                    "shipment": await _shipment_view(
                        repository,
                        merchant_id=context["merchant_id"],
                        workflow=workflow,
                    ),
                }
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_courier_already_assigned",
                    "order_number": order_number,
                    "courier_user_id": existing_courier_id,
                    "courier_name": workflow.get(
                        "store_courier_assignee_name"
                    ),
                },
            )

        now = _now()
        actor_name = (
            _text(user.get("name") or user.get("email"))
            or "مسؤول إدارة الموصلين"
        )
        result = await db[WORKFLOWS].update_one(
            {
                "user_id": context["merchant_id"],
                "order_number": order_number,
                "carrier_label_type": "store_courier",
                "carrier_label_ready": True,
                "stage": "completed",
                "assembly_status": "completed",
                "$or": [
                    {"store_courier_assignee_id": {"$exists": False}},
                    {"store_courier_assignee_id": None},
                    {"store_courier_assignee_id": ""},
                ],
            },
            {
                "$set": {
                    "delivery_flow": "store_courier",
                    "store_courier_assignment_state": ASSIGNED_WAITING_PICKUP,
                    "store_courier_assignee_id": selected_courier_id,
                    "store_courier_assignee_name": selected_courier_name,
                    "store_courier_assigned_at": now,
                    "store_courier_assigned_by_id": context["actor_id"],
                    "store_courier_assigned_by_name": actor_name,
                    "store_courier_assignment_barcode": order_number,
                    "store_courier_label_verified_at": now,
                    "store_courier_label_verified_by_id": context["actor_id"],
                    "updated_at": now,
                }
            },
        )
        if not result.modified_count:
            latest = await db[WORKFLOWS].find_one(
                {
                    "user_id": context["merchant_id"],
                    "order_number": order_number,
                },
                {"_id": 0},
            ) or {}
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_courier_assignment_conflict",
                    "order_number": order_number,
                    "courier_name": latest.get(
                        "store_courier_assignee_name"
                    ),
                },
            )

        await db[EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "order_number": order_number,
            "event_type": "store_courier_shipment_assigned",
            "courier_user_id": selected_courier_id,
            "courier_name": selected_courier_name,
            "actor_id": context["actor_id"],
            "actor_name": actor_name,
            "occurred_at": now,
        })
        updated = await db[WORKFLOWS].find_one(
            {
                "user_id": context["merchant_id"],
                "order_number": order_number,
            },
            {"_id": 0},
        ) or {}
        return {
            "ok": True,
            "already_assigned": False,
            "courier": {
                "id": selected_courier_id,
                "name": selected_courier_name,
            },
            "shipment": await _shipment_view(
                repository,
                merchant_id=context["merchant_id"],
                workflow=updated,
            ),
        }

    @router.get("/my-shipments")
    async def list_my_store_courier_shipments(
        limit: int = Query(default=100, ge=1, le=300),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, DELIVER_PERMISSION)
        await ensure_store_courier_dispatch_indexes(db)
        workflows = await db[WORKFLOWS].find(
            {
                "user_id": context["merchant_id"],
                "carrier_label_type": "store_courier",
                "store_courier_assignee_id": context["actor_id"],
                "store_courier_assignment_state": {
                    "$nin": [None, "", "cancelled"]
                },
            },
            {"_id": 0},
        ).sort("store_courier_assigned_at", -1).limit(limit).to_list(limit)
        items = [
            await _shipment_view(
                repository,
                merchant_id=context["merchant_id"],
                workflow=workflow,
            )
            for workflow in workflows
        ]
        return {
            "items": items,
            "total": len(items),
            "courier_user_id": context["actor_id"],
            "poll_seconds": 15,
        }

    return router


__all__ = [
    "ASSIGN_PERMISSION",
    "DELIVER_PERMISSION",
    "ensure_store_courier_dispatch_indexes",
    "make_store_courier_dispatch_router",
]
