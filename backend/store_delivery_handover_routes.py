"""Bulk handover sessions for Amasi store drivers.

The operator selects one driver, scans shipment barcodes, and the backend validates
city coverage before any assignment is created. Accepted rows are committed in one
explicit confirmation step with immutable driver/fee snapshots.

The canonical order source is ``unified_orders`` (orders_db SSOT). Never use a
parallel ``orders`` collection here: doing so can miss current Salla fields and can
produce null assignment ids.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from store_delivery_domain import StoreDeliveryRuleError, assignment_snapshot, normalize_text
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS, enforce_stage_instructions
from store_courier_domain import (
    ASSIGNED_WAITING_PICKUP,
    WORKFLOWS,
    store_courier_assignment_blocker,
)
from store_delivery_customer_instruction_routes import STORE_DELIVERY_INSTRUCTIONS
from store_delivery_driver_routes import STORE_DRIVERS

SESSIONS = "store_delivery_handover_sessions"
ASSIGNMENTS = "store_delivery_assignments"
EVENTS = "store_delivery_events"
ORDERS = "unified_orders"
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
    allowed = (
        role in {"owner", "admin", "operations", "shipping"}
        or user.get("is_owner") is True
        or HANDOVER_PERMISSION in extra
    )
    if not allowed or HANDOVER_PERMISSION in denied:
        raise HTTPException(status_code=403, detail={"code": "store_delivery_handover_permission_required"})
    return user


def _order_city(order: dict[str, Any]) -> str:
    candidates = [
        order.get("shipping_city"),
        order.get("customer_city"),
        (order.get("shipping_address") or {}).get("city") if isinstance(order.get("shipping_address"), dict) else None,
    ]
    for value in candidates:
        text = normalize_text(value)
        if text:
            return text
    return ""


def _order_number(order: dict[str, Any]) -> str:
    return normalize_text(order.get("order_number") or order.get("reference_id") or order.get("order_id"))


def _order_id(order: dict[str, Any]) -> str:
    return normalize_text(order.get("order_id") or order.get("order_number"))


def _barcode_candidates(barcode: str) -> list[dict[str, Any]]:
    value = normalize_text(barcode)
    return [
        {"tracking_number": value},
        {"shipping_barcode": value},
        {"barcode": value},
        {"order_number": value},
        {"order_id": value},
        {"reference_id": value},
    ]


def _assignment_order_filter(user_id: str, row: dict[str, Any]) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = []
    for field, value in (
        ("order_id", row.get("order_id")),
        ("order_number", row.get("order_id")),
        ("order_number", row.get("order_number")),
    ):
        normalized = normalize_text(value)
        if normalized and {field: normalized} not in clauses:
            clauses.append({field: normalized})
    if not clauses:
        raise RuntimeError("canonical_order_identity_missing_during_handover_confirm")
    return {"user_id": user_id, "$or": clauses}


async def _rollback_confirm_targets(
    db: Any,
    *,
    user_id: str,
    updated_orders: list[tuple[dict[str, Any], dict[str, Any]]],
    updated_workflows: list[tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    for prior, patch in updated_orders:
        identity = normalize_text(prior.get("order_id") or prior.get("order_number"))
        rollback_filter = {
            "user_id": user_id,
            "store_delivery_assignment_id": patch["store_delivery_assignment_id"],
            "$or": [{"order_id": identity}, {"order_number": identity}],
        }
        restore_keys = set(patch)
        restore = {key: prior[key] for key in restore_keys if key in prior}
        unset = {key: "" for key in restore_keys if key not in prior}
        update: dict[str, Any] = {}
        if restore:
            update["$set"] = restore
        if unset:
            update["$unset"] = unset
        if update:
            await db[ORDERS].update_one(rollback_filter, update)
    for prior, patch in updated_workflows:
        order_number = normalize_text(prior.get("order_number"))
        rollback_filter = {
            "user_id": user_id,
            "order_number": order_number,
            "store_delivery_assignment_id": patch["store_delivery_assignment_id"],
        }
        restore_keys = set(patch)
        restore = {key: prior[key] for key in restore_keys if key in prior}
        unset = {key: "" for key in restore_keys if key not in prior}
        update = {}
        if restore:
            update["$set"] = restore
        if unset:
            update["$unset"] = unset
        if update:
            await db[WORKFLOWS].update_one(rollback_filter, update)


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
    await db[ASSIGNMENTS].create_index([("user_id", 1), ("driver_id", 1), ("status", 1), ("active", 1)])
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
        session.pop("_id", None)
        session.pop("user_id", None)
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
        order = await db[ORDERS].find_one({"user_id": user_id, "$or": _barcode_candidates(barcode)}, {"_id": 0})
        if not order:
            rejected = {"barcode": barcode, "code": "shipment_not_found", "scanned_at": _now()}
            await db[SESSIONS].update_one({"user_id": user_id, "id": session_id}, {"$push": {"rejected": rejected}})
            return {"accepted": False, **rejected}

        canonical_order_id = _order_id(order)
        if not canonical_order_id:
            rejected = {"barcode": barcode, "order_number": _order_number(order), "code": "canonical_order_identity_missing", "scanned_at": _now()}
            await db[SESSIONS].update_one({"user_id": user_id, "id": session_id}, {"$push": {"rejected": rejected}})
            return {"accepted": False, **rejected}

        if any(normalize_text(row.get("order_id")) == canonical_order_id for row in session.get("accepted") or []):
            return {"accepted": False, "barcode": barcode, "code": "shipment_already_scanned"}
        active = await db[ASSIGNMENTS].find_one({"user_id": user_id, "order_id": canonical_order_id, "active": True}, {"_id": 1})
        if active:
            return {"accepted": False, "barcode": barcode, "code": "shipment_already_assigned"}

        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": _order_number(order)},
            {"_id": 0},
        )
        if not workflow:
            return {"accepted": False, "barcode": barcode, "code": "store_courier_shipment_not_found"}
        blocker = store_courier_assignment_blocker(workflow)
        if blocker:
            return {"accepted": False, "barcode": barcode, "code": blocker}
        if normalize_text(workflow.get("store_courier_assignee_id")):
            return {"accepted": False, "barcode": barcode, "code": "store_courier_already_assigned"}

        city = _order_city(order)
        try:
            snapshot = assignment_snapshot(driver=driver, shipping_city=city)
        except StoreDeliveryRuleError as exc:
            rejected = {"barcode": barcode, "order_id": canonical_order_id, "order_number": _order_number(order), "shipping_city": city, "code": str(exc), "scanned_at": _now()}
            await db[SESSIONS].update_one({"user_id": user_id, "id": session_id}, {"$push": {"rejected": rejected}})
            return {"accepted": False, **rejected}

        accepted = {"barcode": barcode, "order_id": canonical_order_id, "order_number": _order_number(order), "shipping_city": city, **snapshot, "scanned_at": _now()}
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

        order_ids = [item["order_id"] for item in accepted]
        existing = await db[ASSIGNMENTS].find_one({"user_id": user_id, "order_id": {"$in": order_ids}, "active": True}, {"_id": 0, "order_id": 1})
        if existing:
            raise HTTPException(status_code=409, detail={"code": "shipment_already_assigned", "order_id": existing.get("order_id")})

        driver = await db[STORE_DRIVERS].find_one(
            {"user_id": user_id, "id": session.get("driver_id"), "status": "active"},
            {"_id": 0},
        )
        if not driver:
            raise HTTPException(status_code=409, detail={"code": "driver_inactive"})

        now = _now()
        rows = []
        for item in accepted:
            try:
                current_snapshot = assignment_snapshot(
                    driver=driver,
                    shipping_city=normalize_text(item.get("shipping_city") or item.get("shipping_city_snapshot")),
                )
            except StoreDeliveryRuleError as exc:
                raise HTTPException(status_code=409, detail={"code": str(exc), "order_id": item.get("order_id")}) from exc
            rows.append({
                "id": str(uuid.uuid4()), "user_id": user_id, "session_id": session_id,
                "order_id": item["order_id"], "order_number": item.get("order_number"), "barcode": item.get("barcode"),
                **current_snapshot,
                "status": "assigned", "active": True, "assigned_at": now,
                "assigned_by": normalize_text(actor.get("id")), "delivered_at": None,
            })

        inserted_ids: list[str] = []
        inserted_instruction_ids: list[str] = []
        updated_orders: list[tuple[dict[str, Any], dict[str, Any]]] = []
        updated_workflows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        try:
            for row in rows:
                workflow = await db[WORKFLOWS].find_one(
                    {"user_id": user_id, "order_number": row.get("order_number")},
                    {"_id": 0},
                )
                if not workflow:
                    raise HTTPException(status_code=409, detail={"code": "store_courier_shipment_not_found"})
                blocker = store_courier_assignment_blocker(workflow)
                if blocker:
                    raise HTTPException(status_code=409, detail={"code": blocker, "order_number": row.get("order_number")})
                if normalize_text(workflow.get("store_courier_assignee_id")):
                    raise HTTPException(status_code=409, detail={"code": "store_courier_already_assigned", "order_number": row.get("order_number")})
                await enforce_stage_instructions(
                    db,
                    user_id=user_id,
                    order_number=normalize_text(row.get("order_number")),
                    stage="store_courier",
                    actor_id=normalize_text(actor.get("id")),
                    order_wide=True,
                )
                await db[ASSIGNMENTS].insert_one(row)
                inserted_ids.append(row["id"])
                tracking_rows = await db[ORDER_TRACKING_INSTRUCTIONS].find(
                    {
                        "user_id": user_id,
                        "order_number": row.get("order_number"),
                        "status": {"$in": ["active", "waiting_customer_service_approval"]},
                        "target_stages": "store_courier",
                    },
                    {"_id": 0},
                ).to_list(100)
                for tracking in tracking_rows:
                    instruction_id = f"tracking-{tracking['id']}"
                    instruction_result = await db[STORE_DELIVERY_INSTRUCTIONS].update_one(
                        {"user_id": user_id, "id": instruction_id},
                        {"$setOnInsert": {
                            "id": instruction_id,
                            "user_id": user_id,
                            "order_id": row["order_id"],
                            "driver_id": row["driver_id"],
                            "driver_name_snapshot": row.get("driver_name_snapshot"),
                            "instruction_type": (
                                "scheduled"
                                if tracking.get("delivery_date")
                                else "urgent"
                                if tracking.get("priority") == "urgent"
                                else "general"
                            ),
                            "priority": tracking.get("priority") or "normal",
                            "note": tracking.get("note") or "تعليمات من خدمة العملاء",
                            "delivery_date": tracking.get("delivery_date"),
                            "delivery_time": tracking.get("delivery_time"),
                            "status": "active",
                            "acknowledged_at": None,
                            "acknowledged_by_driver_id": None,
                            "version": 1,
                            "created_at": now,
                            "created_by": tracking.get("created_by"),
                            "updated_at": now,
                            "source_tracking_instruction_id": tracking["id"],
                        }},
                        upsert=True,
                    )
                    if getattr(instruction_result, "upserted_id", None) is not None:
                        inserted_instruction_ids.append(instruction_id)

                order_filter = _assignment_order_filter(user_id, row)
                prior = await db[ORDERS].find_one(
                    order_filter,
                    {
                        "_id": 0,
                        "order_id": 1,
                        "order_number": 1,
                        "store_delivery_assignment_id": 1,
                        "store_delivery_driver_id": 1,
                        "store_delivery_driver_name": 1,
                        "store_delivery_fee_snapshot": 1,
                        "store_delivery_status": 1,
                        "store_delivery_assigned_at": 1,
                        "store_delivery_updated_at": 1,
                    },
                )
                if not prior:
                    raise RuntimeError("canonical_order_not_found_during_handover_confirm")
                order_patch = {
                    "store_delivery_assignment_id": row["id"],
                    "store_delivery_driver_id": row["driver_id"],
                    "store_delivery_driver_name": row.get("driver_name_snapshot"),
                    "store_delivery_fee_snapshot": row.get("delivery_fee_snapshot"),
                    "store_delivery_status": "assigned",
                    "store_delivery_assigned_at": now,
                    "store_delivery_updated_at": now,
                }
                order_result = await db[ORDERS].update_one(order_filter, {"$set": order_patch})
                if order_result.matched_count != 1:
                    raise RuntimeError("canonical_order_update_conflict_during_handover_confirm")
                updated_orders.append((prior, order_patch))

                workflow_patch = {
                    "delivery_flow": "store_courier",
                    "store_courier_assignment_state": ASSIGNED_WAITING_PICKUP,
                    "store_courier_assignee_id": (
                        normalize_text(driver.get("account_user_id"))
                        or f"store-driver:{driver['id']}"
                    ),
                    "store_courier_assignee_name": driver.get("name"),
                    "store_courier_driver_profile_id": driver["id"],
                    "store_delivery_assignment_id": row["id"],
                    "store_courier_assigned_at": now,
                    "store_courier_assigned_by_id": normalize_text(actor.get("id")),
                    "store_courier_assignment_barcode": row.get("barcode"),
                    "store_courier_label_verified_at": now,
                    "store_courier_label_verified_by_id": normalize_text(actor.get("id")),
                    "updated_at": now,
                }
                workflow_result = await db[WORKFLOWS].update_one(
                    {
                        "user_id": user_id,
                        "order_number": row.get("order_number"),
                        "carrier_label_type": "store_courier",
                        "carrier_label_ready": True,
                        "carrier_label_print_confirmed": True,
                        "stage": "completed",
                        "assembly_status": "completed",
                        "$or": [
                            {"store_courier_assignee_id": {"$exists": False}},
                            {"store_courier_assignee_id": None},
                            {"store_courier_assignee_id": ""},
                        ],
                    },
                    {"$set": workflow_patch},
                )
                if workflow_result.modified_count != 1:
                    raise HTTPException(status_code=409, detail={"code": "store_courier_assignment_conflict", "order_number": row.get("order_number")})
                updated_workflows.append((workflow, workflow_patch))
        except Exception:
            if inserted_ids:
                await db[ASSIGNMENTS].delete_many({"user_id": user_id, "id": {"$in": inserted_ids}})
            if inserted_instruction_ids:
                await db[STORE_DELIVERY_INSTRUCTIONS].delete_many({
                    "user_id": user_id,
                    "id": {"$in": inserted_instruction_ids},
                })
            await _rollback_confirm_targets(
                db,
                user_id=user_id,
                updated_orders=updated_orders,
                updated_workflows=updated_workflows,
            )
            raise

        session_update = await db[SESSIONS].update_one(
            {"user_id": user_id, "id": session_id, "status": "open"},
            {"$set": {"status": "confirmed", "confirmed_at": now, "confirmed_by": normalize_text(actor.get("id")), "assigned_count": len(rows)}},
        )
        if session_update.modified_count != 1:
            await db[ASSIGNMENTS].delete_many({"user_id": user_id, "id": {"$in": inserted_ids}})
            if inserted_instruction_ids:
                await db[STORE_DELIVERY_INSTRUCTIONS].delete_many({
                    "user_id": user_id,
                    "id": {"$in": inserted_instruction_ids},
                })
            await _rollback_confirm_targets(
                db,
                user_id=user_id,
                updated_orders=updated_orders,
                updated_workflows=updated_workflows,
            )
            raise HTTPException(status_code=409, detail={"code": "handover_session_confirm_conflict"})

        created = [{k: v for k, v in row.items() if k not in {"_id", "user_id"}} for row in rows]
        await db[EVENTS].insert_one({
            "id": str(uuid.uuid4()), "user_id": user_id, "event_type": "store_delivery_handover_confirmed",
            "session_id": session_id, "driver_id": session.get("driver_id"), "assigned_count": len(created),
            "actor_id": normalize_text(actor.get("id")), "occurred_at": now,
        })
        return {"confirmed": True, "session_id": session_id, "assigned_count": len(created), "assignments": created}

    return router


__all__ = ["make_store_delivery_handover_router", "ensure_store_delivery_handover_indexes", "ASSIGNMENTS", "SESSIONS", "ORDERS"]
