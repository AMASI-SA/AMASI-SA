"""Store-courier dispatch, pickup and delivery workflow.

The store-courier branch stays inside the governed fulfillment path:

completed -> assigned_waiting_pickup -> delivering -> delivered

A dispatcher selects one eligible courier and scans the QR printed on the
store-courier label. The QR payload is the order number only. Couriers can read
only shipments assigned to their own account, must scan the same QR when taking
custody, and can complete only shipments already in their custody.
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
    effective_permissions,
    find_role_assignment,
    find_role_assignments,
)
from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from order_tracking_notes import enforce_stage_instructions


WORKFLOWS = "order_review_workflows"
EVENTS = "mezan_fulfillment_events_v2"
ASSIGN_PERMISSION = "fulfillment.store_courier.assign"
DELIVER_PERMISSION = "fulfillment.store_courier.deliver"
DISPATCH_RESPONSIBILITY = "store_courier_dispatch"
DELIVERY_RESPONSIBILITY = "store_courier_delivery"
STORE_COURIER_ROLE = "store_courier"

ASSIGNED_WAITING_PICKUP = "assigned_waiting_pickup"
DELIVERING = "delivering"
DELIVERED = "delivered"
CANCELLED = "cancelled"
MY_SHIPMENT_STAGES = {"waiting", DELIVERING, DELIVERED, "all"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    normalized = _text(value)
    return normalized or None


def _model_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict):
        return legacy_dict()
    return {}


def _first_text(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _text(mapping.get(key))
        if value:
            return value
    return None


class AssignStoreCourierShipmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    courier_user_id: str = Field(min_length=1, max_length=128)
    barcode: str = Field(min_length=1, max_length=256)


class PickupStoreCourierShipmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=1, max_length=256)


class CompleteStoreCourierShipmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


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
    await db[WORKFLOWS].create_index(
        [
            ("user_id", ASCENDING),
            ("store_courier_assignee_id", ASCENDING),
            ("stage", ASCENDING),
            ("updated_at", DESCENDING),
        ],
        name="ix_store_courier_my_shipments_v2",
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
            "role_key": "owner",
            "responsibilities": {
                DISPATCH_RESPONSIBILITY,
                DELIVERY_RESPONSIBILITY,
            },
        }

    merchant_id = _text(user.get("created_by") or user.get("merchant_id"))
    if not merchant_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "employee_store_not_linked"},
        )
    assignment = await find_role_assignment(
        db,
        owner_user_id=merchant_id,
        user_id=actor_id,
    ) or {}
    return {
        "actor_id": actor_id,
        "merchant_id": merchant_id,
        "is_owner": False,
        "permissions": set(effective_permissions(assignment)),
        "role_key": _text(assignment.get("role_key")),
        "responsibilities": {
            _text(value)
            for value in assignment.get("fulfillment_responsibilities") or []
            if _text(value)
        },
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


def _assignment_is_store_courier(assignment: dict[str, Any] | None) -> bool:
    assignment = assignment or {}
    permissions = set(effective_permissions(assignment))
    if DELIVER_PERMISSION not in permissions:
        return False
    role_key = _text(assignment.get("role_key"))
    responsibilities = {
        _text(value)
        for value in assignment.get("fulfillment_responsibilities") or []
        if _text(value)
    }
    return bool(
        role_key == STORE_COURIER_ROLE
        or DELIVERY_RESPONSIBILITY in responsibilities
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

    assignment = await find_role_assignment(
        db,
        owner_user_id=merchant_id,
        user_id=courier_id,
    ) or {}
    if not _assignment_is_store_courier(assignment):
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
    member_ids = [
        _text(row.get("id"))
        for row in members
        if _text(row.get("id"))
    ]
    assignments = await find_role_assignments(
        db,
        owner_user_id=merchant_id,
        user_ids=member_ids,
        limit=max(1, len(member_ids)),
    )
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
            or not _assignment_is_store_courier(assignment)
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
                                {"$ne": ["$stage", DELIVERED]},
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
                                {"$eq": ["$stage", DELIVERING]},
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
                "waiting_pickup_count": int(
                    row.get("waiting_pickup_count") or 0
                ),
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


def _address_text(address: dict[str, Any]) -> str | None:
    formatted = _text(address.get("formatted"))
    if formatted:
        return formatted
    parts = []
    for key in (
        "short_address",
        "district",
        "street",
        "building_number",
        "city",
        "postal_code",
        "country",
    ):
        value = _text(address.get(key))
        if value and value not in parts:
            parts.append(value)
    return "، ".join(parts) or None


async def _shipment_view(
    repository: MongoOrderRepository,
    *,
    merchant_id: str,
    workflow: dict[str, Any],
) -> dict[str, Any]:
    order_number = _text(workflow.get("order_number"))
    base: dict[str, Any] = {
        "order_number": order_number,
        "customer_name": None,
        "customer_mobile": None,
        "recipient_name": None,
        "recipient_mobile": None,
        "city": None,
        "address": {},
        "address_text": None,
        "remaining_amount": 0.0,
        "order_total": 0.0,
        "currency": "SAR",
        "payment_method": None,
        "items": [],
        "order_created_at": None,
        "carrier_label_type": workflow.get("carrier_label_type"),
        "assignment_state": workflow.get("store_courier_assignment_state"),
        "courier_user_id": workflow.get("store_courier_assignee_id"),
        "courier_name": workflow.get("store_courier_assignee_name"),
        "assigned_at": workflow.get("store_courier_assigned_at"),
        "assigned_by_name": workflow.get("store_courier_assigned_by_name"),
        "picked_up_at": workflow.get("store_courier_picked_up_at"),
        "delivered_at": (
            workflow.get("store_courier_delivered_at")
            or workflow.get("delivered_at")
        ),
        "delivery_note": workflow.get("store_courier_delivery_note"),
        "customer_service_instructions": list(
            workflow.get("customer_service_instructions") or []
        ),
        "customer_service_hold_active": bool(
            workflow.get("customer_service_hold_active")
        ),
        "stage": workflow.get("stage"),
        "can_pickup": bool(
            _text(workflow.get("stage")) == "completed"
            and _text(workflow.get("store_courier_assignment_state"))
            == ASSIGNED_WAITING_PICKUP
        ),
        "can_mark_delivered": bool(
            _text(workflow.get("stage")) == DELIVERING
            and _text(workflow.get("store_courier_assignment_state"))
            == DELIVERING
        ),
    }
    try:
        order = await get_order(
            repository,
            user_id=merchant_id,
            order_number=order_number,
        )
    except OrderNotFoundError:
        return base

    address = _model_dict(order.shipping.address)
    recipient = (
        dict(order.shipping.recipient)
        if isinstance(order.shipping.recipient, dict)
        else {}
    )
    recipient_name = _first_text(
        recipient,
        "name",
        "full_name",
        "recipient_name",
    )
    recipient_mobile = _first_text(
        recipient,
        "mobile",
        "phone",
        "phone_number",
    )
    base.update({
        "customer_name": order.customer.name,
        "customer_mobile": order.customer.mobile,
        "recipient_name": recipient_name or order.customer.name,
        "recipient_mobile": recipient_mobile or order.customer.mobile,
        "city": address.get("city"),
        "address": address,
        "address_text": _address_text(address),
        "remaining_amount": round(float(order.payment.remaining_amount or 0), 2),
        "order_total": round(float(order.totals.total or 0), 2),
        "currency": _text(order.totals.currency) or "SAR",
        "payment_method": order.payment.method,
        "items": [
            {
                "order_item_id": item.order_item_id,
                "name": item.name,
                "sku": item.sku,
                "quantity": item.quantity,
            }
            for item in order.items
        ],
        "order_created_at": _iso(order.created_at),
    })
    return base


def _my_stage_filter(stage: str) -> dict[str, Any]:
    normalized = _text(stage).casefold() or "waiting"
    if normalized not in MY_SHIPMENT_STAGES:
        raise HTTPException(
            status_code=422,
            detail={"code": "store_courier_stage_invalid"},
        )
    if normalized == "waiting":
        return {
            "stage": "completed",
            "store_courier_assignment_state": ASSIGNED_WAITING_PICKUP,
        }
    if normalized == DELIVERING:
        return {
            "stage": DELIVERING,
            "store_courier_assignment_state": DELIVERING,
        }
    if normalized == DELIVERED:
        return {
            "stage": DELIVERED,
            "store_courier_assignment_state": DELIVERED,
        }
    return {
        "store_courier_assignment_state": {
            "$nin": [None, "", CANCELLED]
        }
    }


def _actor_name(user: dict[str, Any], fallback: str) -> str:
    return _text(user.get("name") or user.get("email")) or fallback


def _store_courier_assignment_blocker(workflow: dict[str, Any]) -> str | None:
    if _text(workflow.get("carrier_label_type")) != "store_courier":
        return "store_courier_label_required"
    if workflow.get("carrier_label_ready") is not True:
        return "store_courier_label_not_ready"
    if workflow.get("carrier_label_print_confirmed") is not True:
        return "store_courier_label_not_confirmed"
    if (
        _text(workflow.get("stage")) != "completed"
        or _text(workflow.get("assembly_status")) != "completed"
    ):
        return "store_courier_order_not_completed"
    return None


async def _assigned_workflow(
    db: Any,
    *,
    merchant_id: str,
    actor_id: str,
    order_number: str,
) -> dict[str, Any]:
    workflow = await db[WORKFLOWS].find_one(
        {
            "user_id": merchant_id,
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
    assigned_to = _text(workflow.get("store_courier_assignee_id"))
    if not assigned_to:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "store_courier_shipment_not_assigned",
                "order_number": order_number,
            },
        )
    if assigned_to != actor_id:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "store_courier_shipment_assigned_to_another",
                "order_number": order_number,
                "courier_name": workflow.get("store_courier_assignee_name"),
            },
        )
    return workflow


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
        assignment_blocker = _store_courier_assignment_blocker(workflow)
        if assignment_blocker:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": assignment_blocker,
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

        await enforce_stage_instructions(
            db,
            user_id=context["merchant_id"],
            order_number=order_number,
            stage="store_courier",
            actor_id=context["actor_id"],
            order_wide=True,
        )

        now = _now()
        actor_name = _actor_name(user, "مسؤول إدارة الموصلين")
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
        stage: str = Query(default="waiting", max_length=16),
        limit: int = Query(default=100, ge=1, le=300),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, DELIVER_PERMISSION)
        await ensure_store_courier_dispatch_indexes(db)
        normalized_stage = _text(stage).casefold() or "waiting"
        stage_filter = _my_stage_filter(normalized_stage)
        workflows = await db[WORKFLOWS].find(
            {
                "user_id": context["merchant_id"],
                "carrier_label_type": "store_courier",
                "store_courier_assignee_id": context["actor_id"],
                **stage_filter,
            },
            {"_id": 0},
        ).sort("updated_at", -1).limit(limit).to_list(limit)
        items = [
            await _shipment_view(
                repository,
                merchant_id=context["merchant_id"],
                workflow=workflow,
            )
            for workflow in workflows
        ]
        return {
            "stage": normalized_stage,
            "items": items,
            "total": len(items),
            "courier_user_id": context["actor_id"],
            "poll_seconds": 15,
        }

    @router.post("/my-shipments/{order_number}/pickup")
    async def pickup_store_courier_shipment(
        order_number: str,
        payload: PickupStoreCourierShipmentRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, DELIVER_PERMISSION)
        await ensure_store_courier_dispatch_indexes(db)
        normalized_order = _text(order_number)
        scanned_barcode = _text(payload.barcode)
        if scanned_barcode != normalized_order:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_courier_pickup_barcode_mismatch",
                    "order_number": normalized_order,
                },
            )
        workflow = await _assigned_workflow(
            db,
            merchant_id=context["merchant_id"],
            actor_id=context["actor_id"],
            order_number=normalized_order,
        )
        current_stage = _text(workflow.get("stage"))
        current_state = _text(workflow.get("store_courier_assignment_state"))
        if current_stage == DELIVERED or current_state == DELIVERED:
            return {
                "ok": True,
                "already_delivered": True,
                "already_picked_up": True,
                "shipment": await _shipment_view(
                    repository,
                    merchant_id=context["merchant_id"],
                    workflow=workflow,
                ),
            }
        if current_stage == DELIVERING and current_state == DELIVERING:
            return {
                "ok": True,
                "already_picked_up": True,
                "shipment": await _shipment_view(
                    repository,
                    merchant_id=context["merchant_id"],
                    workflow=workflow,
                ),
            }
        if (
            current_stage != "completed"
            or current_state != ASSIGNED_WAITING_PICKUP
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_courier_pickup_state_invalid",
                    "order_number": normalized_order,
                    "stage": current_stage,
                    "assignment_state": current_state,
                },
            )

        await enforce_stage_instructions(
            db,
            user_id=context["merchant_id"],
            order_number=normalized_order,
            stage="store_courier",
            actor_id=context["actor_id"],
            order_wide=True,
        )

        now = _now()
        actor_name = _actor_name(user, "مندوب المتجر")
        result = await db[WORKFLOWS].update_one(
            {
                "user_id": context["merchant_id"],
                "order_number": normalized_order,
                "carrier_label_type": "store_courier",
                "store_courier_assignee_id": context["actor_id"],
                "stage": "completed",
                "store_courier_assignment_state": ASSIGNED_WAITING_PICKUP,
            },
            {
                "$set": {
                    "delivery_flow": "store_courier",
                    "stage": DELIVERING,
                    "store_courier_assignment_state": DELIVERING,
                    "store_courier_picked_up_at": now,
                    "store_courier_picked_up_by_id": context["actor_id"],
                    "store_courier_picked_up_by_name": actor_name,
                    "store_courier_pickup_barcode": scanned_barcode,
                    "delivering_at": now,
                    "delivery_status_source": "store_courier_app",
                    "updated_at": now,
                }
            },
        )
        if not result.modified_count:
            latest = await _assigned_workflow(
                db,
                merchant_id=context["merchant_id"],
                actor_id=context["actor_id"],
                order_number=normalized_order,
            )
            if (
                _text(latest.get("stage")) in {DELIVERING, DELIVERED}
                and _text(latest.get("store_courier_assignment_state"))
                in {DELIVERING, DELIVERED}
            ):
                return {
                    "ok": True,
                    "already_picked_up": True,
                    "shipment": await _shipment_view(
                        repository,
                        merchant_id=context["merchant_id"],
                        workflow=latest,
                    ),
                }
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_courier_pickup_conflict",
                    "order_number": normalized_order,
                },
            )

        await db[EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "order_number": normalized_order,
            "event_type": "store_courier_shipment_picked_up",
            "courier_user_id": context["actor_id"],
            "courier_name": actor_name,
            "barcode": scanned_barcode,
            "occurred_at": now,
        })
        updated = await db[WORKFLOWS].find_one(
            {
                "user_id": context["merchant_id"],
                "order_number": normalized_order,
            },
            {"_id": 0},
        ) or {}
        return {
            "ok": True,
            "already_picked_up": False,
            "shipment": await _shipment_view(
                repository,
                merchant_id=context["merchant_id"],
                workflow=updated,
            ),
        }

    @router.post("/my-shipments/{order_number}/delivered")
    async def complete_store_courier_shipment(
        order_number: str,
        payload: CompleteStoreCourierShipmentRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, DELIVER_PERMISSION)
        await ensure_store_courier_dispatch_indexes(db)
        normalized_order = _text(order_number)
        workflow = await _assigned_workflow(
            db,
            merchant_id=context["merchant_id"],
            actor_id=context["actor_id"],
            order_number=normalized_order,
        )
        current_stage = _text(workflow.get("stage"))
        current_state = _text(workflow.get("store_courier_assignment_state"))
        if current_stage == DELIVERED and current_state == DELIVERED:
            return {
                "ok": True,
                "already_delivered": True,
                "shipment": await _shipment_view(
                    repository,
                    merchant_id=context["merchant_id"],
                    workflow=workflow,
                ),
            }
        if (
            current_stage == "completed"
            and current_state == ASSIGNED_WAITING_PICKUP
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_courier_pickup_required",
                    "order_number": normalized_order,
                },
            )
        if current_stage != DELIVERING or current_state != DELIVERING:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_courier_delivery_state_invalid",
                    "order_number": normalized_order,
                    "stage": current_stage,
                    "assignment_state": current_state,
                },
            )

        await enforce_stage_instructions(
            db,
            user_id=context["merchant_id"],
            order_number=normalized_order,
            stage="store_courier",
            actor_id=context["actor_id"],
            order_wide=True,
        )

        now = _now()
        actor_name = _actor_name(user, "مندوب المتجر")
        note = _text(payload.note) or None
        set_fields: dict[str, Any] = {
            "delivery_flow": "store_courier",
            "stage": DELIVERED,
            "store_courier_assignment_state": DELIVERED,
            "store_courier_delivered_at": now,
            "store_courier_delivered_by_id": context["actor_id"],
            "store_courier_delivered_by_name": actor_name,
            "delivered_at": now,
            "delivery_status_source": "store_courier_app",
            "updated_at": now,
        }
        if note:
            set_fields["store_courier_delivery_note"] = note
        result = await db[WORKFLOWS].update_one(
            {
                "user_id": context["merchant_id"],
                "order_number": normalized_order,
                "carrier_label_type": "store_courier",
                "store_courier_assignee_id": context["actor_id"],
                "stage": DELIVERING,
                "store_courier_assignment_state": DELIVERING,
            },
            {"$set": set_fields},
        )
        if not result.modified_count:
            latest = await _assigned_workflow(
                db,
                merchant_id=context["merchant_id"],
                actor_id=context["actor_id"],
                order_number=normalized_order,
            )
            if (
                _text(latest.get("stage")) == DELIVERED
                and _text(latest.get("store_courier_assignment_state"))
                == DELIVERED
            ):
                return {
                    "ok": True,
                    "already_delivered": True,
                    "shipment": await _shipment_view(
                        repository,
                        merchant_id=context["merchant_id"],
                        workflow=latest,
                    ),
                }
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_courier_delivery_conflict",
                    "order_number": normalized_order,
                },
            )

        await db[EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "order_number": normalized_order,
            "event_type": "store_courier_shipment_delivered",
            "courier_user_id": context["actor_id"],
            "courier_name": actor_name,
            "note": note,
            "occurred_at": now,
        })
        updated = await db[WORKFLOWS].find_one(
            {
                "user_id": context["merchant_id"],
                "order_number": normalized_order,
            },
            {"_id": 0},
        ) or {}
        return {
            "ok": True,
            "already_delivered": False,
            "shipment": await _shipment_view(
                repository,
                merchant_id=context["merchant_id"],
                workflow=updated,
            ),
        }

    return router


__all__ = [
    "ASSIGN_PERMISSION",
    "DELIVER_PERMISSION",
    "ASSIGNED_WAITING_PICKUP",
    "DELIVERING",
    "DELIVERED",
    "_assignment_is_store_courier",
    "_my_stage_filter",
    "ensure_store_courier_dispatch_indexes",
    "make_store_courier_dispatch_router",
]
