"""Safe reassignment and reporting for store-driver shipments."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from store_delivery_customer_instruction_routes import STORE_DELIVERY_INSTRUCTIONS
from store_courier_domain import ASSIGNED_WAITING_PICKUP, WORKFLOWS
from store_delivery_domain import StoreDeliveryRuleError, carrier_assignment_snapshot, normalize_text
from store_delivery_driver_routes import STORE_DRIVERS
from store_delivery_handover_routes import ASSIGNMENTS, EVENTS, ORDERS, _order_city, _order_shipping_company
from store_delivery_payment_evidence_routes import authoritative_outstanding_amount
from payment_methods import normalize_payment_method

HANDOVER_PERMISSION = "store_delivery.handover"
CURRENT_DELIVERY_STATUSES = ("assigned", "out_for_delivery")


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


def _assignment_status_filter(value: str | None) -> str | dict[str, list[str]] | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    if normalized == "current":
        return {"$in": list(CURRENT_DELIVERY_STATUSES)}
    return normalized


def _is_cash_on_delivery(order: dict[str, Any]) -> bool:
    raw = normalize_text(
        order.get("actual_payment_method")
        or order.get("payment_method")
        or order.get("payment_method_normalized")
    )
    key, _display, _parent = normalize_payment_method(raw)
    return key == "cash_on_delivery"


def _assignment_totals(assignments: list[dict[str, Any]], orders_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    delivery_fee_total = 0.0
    cod_total = 0.0
    cod_unavailable_count = 0
    for assignment in assignments:
        try:
            delivery_fee_total += float(assignment.get("delivery_fee_snapshot") or 0)
        except (TypeError, ValueError):
            pass
        order = (
            orders_by_key.get(normalize_text(assignment.get("order_id")))
            or orders_by_key.get(normalize_text(assignment.get("order_number")))
        )
        if not order or not _is_cash_on_delivery(order):
            continue
        try:
            cod_total += authoritative_outstanding_amount(order)
        except StoreDeliveryRuleError:
            cod_unavailable_count += 1
    return {
        "cod_total": round(cod_total, 2),
        "delivery_fee_total": round(delivery_fee_total, 2),
        "cod_unavailable_count": cod_unavailable_count,
    }


def _parse_timestamp(value: Any) -> datetime | None:
    text = normalize_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _delivery_duration_report(assignments: list[dict[str, Any]]) -> dict[str, Any]:
    durations: list[float] = []
    for assignment in assignments:
        start = _parse_timestamp(assignment.get("assigned_at"))
        end = _parse_timestamp(assignment.get("delivered_at"))
        if not start or not end or end < start:
            continue
        durations.append((end - start).total_seconds())
    if not durations:
        return {
            "measured_count": 0,
            "average_delivery_seconds": None,
            "fastest_delivery_seconds": None,
            "longest_delivery_seconds": None,
        }
    return {
        "measured_count": len(durations),
        "average_delivery_seconds": round(sum(durations) / len(durations), 2),
        "fastest_delivery_seconds": round(min(durations), 2),
        "longest_delivery_seconds": round(max(durations), 2),
    }


async def _orders_for_assignments(db: Any, user_id: str, assignments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ids = sorted({normalize_text(row.get("order_id")) for row in assignments if normalize_text(row.get("order_id"))})
    numbers = sorted({normalize_text(row.get("order_number")) for row in assignments if normalize_text(row.get("order_number"))})
    if not ids and not numbers:
        return {}
    rows = await db[ORDERS].find(
        {
            "user_id": user_id,
            "$or": [
                {"order_id": {"$in": ids + numbers}},
                {"order_number": {"$in": numbers + ids}},
            ],
        },
        {
            "_id": 0,
            "order_id": 1,
            "order_number": 1,
            "reference_id": 1,
            "customer_name": 1,
            "customer_mobile": 1,
            "shipping_city": 1,
            "shipping_district": 1,
            "shipping_street": 1,
            "remaining_amount": 1,
            "paid_amount": 1,
            "total_amount": 1,
            "has_remaining_amount": 1,
            "payment_status": 1,
            "actual_payment_method": 1,
            "payment_method": 1,
            "payment_method_normalized": 1,
        },
    ).to_list(length=max(len(ids) + len(numbers), 1) * 2)
    by_key: dict[str, dict[str, Any]] = {}
    for order in rows:
        for key in (normalize_text(order.get("order_id")), normalize_text(order.get("order_number"))):
            if key:
                by_key[key] = order
    return by_key


def _enrich_assignment(assignment: dict[str, Any], order: dict[str, Any] | None) -> dict[str, Any]:
    row = dict(assignment)
    if not order:
        row["order_found"] = False
        row["cod_outstanding"] = None
        return row
    row.update({
        "order_found": True,
        "customer_name": order.get("customer_name"),
        "customer_mobile": order.get("customer_mobile"),
        "shipping_city": order.get("shipping_city"),
        "shipping_district": order.get("shipping_district"),
        "shipping_street": order.get("shipping_street"),
        "payment_method": order.get("actual_payment_method") or order.get("payment_method"),
        "is_cash_on_delivery": _is_cash_on_delivery(order),
    })
    if row["is_cash_on_delivery"]:
        try:
            row["cod_outstanding"] = authoritative_outstanding_amount(order)
        except StoreDeliveryRuleError:
            row["cod_outstanding"] = None
    else:
        row["cod_outstanding"] = 0.0
    return row



class UnassignPayload(BaseModel):
    """Explicit, fenced operator cancellation of a not-yet-received assignment."""
    model_config = ConfigDict(extra="forbid")
    expected_driver_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(default="إلغاء الإسناد من تطبيق الموظفين", min_length=3, max_length=500)


# Fields are a *projection* of the current assignment, not an order cancellation.
# Keep printed carrier labels, customer order status and all fee/history evidence.
_UNASSIGN_ORDER_FIELDS = (
    "store_delivery_assignment_id", "store_delivery_driver_id",
    "store_delivery_driver_name", "store_delivery_fee_snapshot",
    "store_delivery_assigned_at",
)
_UNASSIGN_WORKFLOW_FIELDS = (
    "store_delivery_assignment_id", "store_courier_driver_profile_id",
    "store_courier_assignee_id", "store_courier_assignee_name",
    "store_courier_assignment_state", "store_courier_assigned_at",
    "store_courier_assigned_by_id",
)
_UNASSIGN_EVIDENCE_COLLECTIONS = (
    "store_delivery_collections",
    "store_delivery_driver_earnings",
    "store_delivery_payment_reviews",
    "store_delivery_driver_settlements",
    "store_delivery_receipts",
    "store_delivery_delivery_proofs",
    "store_delivery_customer_conversation_evidence",
)


def _assert_unassignment_safe(
    assignment: dict[str, Any],
    order: dict[str, Any],
    workflow: dict[str, Any],
    *,
    expected_driver_id: str,
) -> None:
    """Only a pre-pickup handover may be undone; custody and finance are immutable."""
    if normalize_text(assignment.get("driver_id")) != expected_driver_id:
        raise HTTPException(status_code=409, detail={"code": "assignment_driver_changed"})
    if normalize_text(assignment.get("status")) != "assigned" or assignment.get("active") is not True:
        raise HTTPException(status_code=409, detail={"code": "assignment_in_custody_cannot_unassign"})
    if any(normalize_text(assignment.get(field)) for field in (
        "out_for_delivery_at", "delivered_at", "received_at",
        "delivery_resumed_at", "collection_method", "collection_amount",
    )):
        raise HTTPException(status_code=409, detail={"code": "assignment_in_custody_cannot_unassign"})
    if (
        normalize_text(order.get("store_delivery_assignment_id")) != normalize_text(assignment.get("id"))
        or normalize_text(order.get("store_delivery_driver_id")) != expected_driver_id
        or normalize_text(order.get("store_delivery_status")) != "assigned"
        or normalize_text(workflow.get("store_delivery_assignment_id")) != normalize_text(assignment.get("id"))
        or normalize_text(workflow.get("store_courier_driver_profile_id")) != expected_driver_id
    ):
        raise HTTPException(status_code=409, detail={"code": "assignment_projection_changed"})
    if (
        normalize_text(workflow.get("store_courier_assignment_state")) != ASSIGNED_WAITING_PICKUP
        or normalize_text(workflow.get("stage")) != "completed"
        or normalize_text(workflow.get("assembly_status")) != "completed"
        or any(normalize_text(workflow.get(field)) for field in (
            "store_courier_picked_up_at", "store_courier_picked_up_by_id",
            "store_courier_delivered_at", "store_courier_delivered_by_id",
        ))
    ):
        raise HTTPException(status_code=409, detail={"code": "assignment_in_custody_cannot_unassign"})


def make_store_delivery_reassignment_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/assignments", tags=["Store Delivery Reassignment"])

    @router.get("")
    async def active_assignments(
        driver_id: str | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=250, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        include_summary: bool = Query(default=True),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_operator(user)
        user_id = _merchant_user_id(actor)
        query: dict[str, Any] = {"user_id": user_id, "active": True}
        if driver_id:
            query["driver_id"] = normalize_text(driver_id)
        normalized_status = _assignment_status_filter(status_filter)
        if normalized_status is not None:
            query["status"] = normalized_status

        total = await db[ASSIGNMENTS].count_documents(query)
        items = await db[ASSIGNMENTS].find(query, {"_id": 0, "user_id": 0}).sort(
            "assigned_at", -1
        ).skip(offset).limit(limit).to_list(length=limit)
        page_orders = await _orders_for_assignments(db, user_id, items)
        enriched = []
        for item in items:
            order = (
                page_orders.get(normalize_text(item.get("order_id")))
                or page_orders.get(normalize_text(item.get("order_number")))
            )
            enriched.append(_enrich_assignment(item, order))

        summary = None
        if include_summary:
            summary_rows = await db[ASSIGNMENTS].find(
                query,
                {
                    "_id": 0,
                    "order_id": 1,
                    "order_number": 1,
                    "delivery_fee_snapshot": 1,
                },
            ).to_list(length=max(total, 1))
            summary_orders = await _orders_for_assignments(db, user_id, summary_rows)
            summary = _assignment_totals(summary_rows, summary_orders)

        return {
            "items": enriched,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(enriched) < total,
            "summary": summary,
        }

    @router.get("/report")
    async def driver_report(
        driver_id: str = Query(min_length=1),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_operator(user)
        user_id = _merchant_user_id(actor)
        query = {
            "user_id": user_id,
            "driver_id": normalize_text(driver_id),
            "active": True,
            "status": "delivered",
        }
        rows = await db[ASSIGNMENTS].find(
            query,
            {
                "_id": 0,
                "order_id": 1,
                "order_number": 1,
                "delivery_fee_snapshot": 1,
                "assigned_at": 1,
                "delivered_at": 1,
            },
        ).sort("delivered_at", -1).to_list(length=100000)
        orders_by_key = await _orders_for_assignments(db, user_id, rows)
        return {
            "driver_id": normalize_text(driver_id),
            "total_delivered": len(rows),
            "summary": _assignment_totals(rows, orders_by_key),
            **_delivery_duration_report(rows),
        }

    @router.post("/{assignment_id}/unassign")
    async def unassign(
        assignment_id: str, payload: UnassignPayload,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        """Deactivate only a not-yet-collected assignment, using an atomic transaction.

        Reject in-progress, delivered, settled, or inconsistent orders instead
        of removing courier custody/financial history. The receiving app's own
        assignments endpoint filters active=true, so the revoked row disappears.
        """
        actor = _require_operator(user)
        user_id = _merchant_user_id(actor)
        expected_driver = normalize_text(payload.expected_driver_id)
        identifier = normalize_text(assignment_id)
        if not identifier:
            raise HTTPException(status_code=404, detail={"code": "store_delivery_assignment_not_found"})
        now = _now()
        try:
            async with await db.client.start_session() as tx:
                async with tx.start_transaction():
                    assignment = await db[ASSIGNMENTS].find_one(
                        {"user_id": user_id, "id": identifier, "active": True},
                        {"_id": 0}, session=tx,
                    )
                    if not assignment:
                        raise HTTPException(status_code=404, detail={"code": "store_delivery_assignment_not_found"})
                    order = await db[ORDERS].find_one(
                        {"user_id": user_id, "store_delivery_assignment_id": identifier,
                         "$or": [{"order_id": assignment.get("order_id")},
                                 {"order_number": assignment.get("order_number")}]},
                        {"_id": 0}, session=tx,
                    )
                    workflow = await db[WORKFLOWS].find_one(
                        {"user_id": user_id, "order_number": assignment.get("order_number"),
                         "store_delivery_assignment_id": identifier},
                        {"_id": 0}, session=tx,
                    )
                    if not order or not workflow:
                        raise HTTPException(status_code=409, detail={"code": "assignment_projection_changed"})
                    _assert_unassignment_safe(
                        assignment, order, workflow, expected_driver_id=expected_driver,
                    )
                    for collection in _UNASSIGN_EVIDENCE_COLLECTIONS:
                        evidence = await db[collection].find_one(
                            {"user_id": user_id, "assignment_id": identifier},
                            {"_id": 1}, session=tx,
                        )
                        if evidence:
                            raise HTTPException(status_code=409, detail={"code": "assignment_financial_evidence_exists"})
                    session_receipt = await db["store_delivery_driver_receive_sessions"].find_one(
                        {"user_id": user_id, "accepted.assignment_id": identifier},
                        {"_id": 1}, session=tx,
                    )
                    if session_receipt:
                        raise HTTPException(status_code=409, detail={"code": "assignment_in_custody_cannot_unassign"})
                    acknowledged = await db[STORE_DELIVERY_INSTRUCTIONS].find_one(
                        {"user_id": user_id, "order_id": assignment.get("order_id"),
                         "driver_id": expected_driver, "status": "active",
                         "$or": [{"acknowledged_at": {"$nin": [None, ""]}},
                                 {"acknowledged_by_driver_id": {"$nin": [None, ""]}}]},
                        {"_id": 1}, session=tx,
                    )
                    if acknowledged:
                        raise HTTPException(status_code=409, detail={"code": "assignment_in_custody_cannot_unassign"})

                    updated = await db[ASSIGNMENTS].update_one(
                        {"user_id": user_id, "id": identifier,
                         "driver_id": expected_driver, "active": True,
                         "status": "assigned", "out_for_delivery_at": {"$in": [None, ""]},
                         "delivered_at": {"$in": [None, ""]}},
                        {"$set": {"active": False, "status": "unassigned",
                                  "unassigned_at": now, "unassigned_by": normalize_text(actor.get("id")),
                                  "unassignment_reason": normalize_text(payload.reason), "updated_at": now}},
                        session=tx,
                    )
                    if updated.modified_count != 1:
                        raise HTTPException(status_code=409, detail={"code": "assignment_unassign_conflict"})

                    updated_order = await db[ORDERS].update_one(
                        {"user_id": user_id, "store_delivery_assignment_id": identifier,
                         "store_delivery_driver_id": expected_driver,
                         "store_delivery_status": "assigned",
                         "$or": [{"order_id": assignment.get("order_id")},
                                 {"order_number": assignment.get("order_number")}]},
                        {"$unset": {field: "" for field in _UNASSIGN_ORDER_FIELDS},
                         "$set": {"store_delivery_status": "unassigned",
                                  "store_delivery_updated_at": now}},
                        session=tx,
                    )
                    if updated_order.modified_count != 1:
                        raise HTTPException(status_code=409, detail={"code": "assignment_unassign_conflict"})

                    updated_workflow = await db[WORKFLOWS].update_one(
                        {"user_id": user_id, "order_number": assignment.get("order_number"),
                         "store_delivery_assignment_id": identifier,
                         "store_courier_driver_profile_id": expected_driver,
                         "store_courier_assignment_state": ASSIGNED_WAITING_PICKUP,
                         "stage": "completed", "assembly_status": "completed",
                         "store_courier_picked_up_at": {"$in": [None, ""]}},
                        {"$unset": {field: "" for field in _UNASSIGN_WORKFLOW_FIELDS},
                         "$set": {"updated_at": now}},
                        session=tx,
                    )
                    if updated_workflow.modified_count != 1:
                        raise HTTPException(status_code=409, detail={"code": "assignment_unassign_conflict"})

                    await db[STORE_DELIVERY_INSTRUCTIONS].update_many(
                        {"user_id": user_id, "order_id": assignment.get("order_id"),
                         "driver_id": expected_driver, "status": "active"},
                        {"$set": {"driver_id": None, "driver_name_snapshot": None,
                                  "acknowledged_at": None, "acknowledged_by_driver_id": None,
                                  "updated_at": now, "updated_by": normalize_text(actor.get("id"))},
                         "$unset": {"last_reminder_code": "", "last_reminder_at": ""}},
                        session=tx,
                    )
                    await db[EVENTS].insert_one({
                        "id": str(uuid.uuid4()), "user_id": user_id,
                        "event_type": "store_delivery_unassigned", "assignment_id": identifier,
                        "order_id": assignment.get("order_id"),
                        "order_number": assignment.get("order_number"),
                        "driver_id": expected_driver,
                        "reason": normalize_text(payload.reason),
                        "actor_id": normalize_text(actor.get("id")), "occurred_at": now,
                    }, session=tx)
        except HTTPException:
            raise
        except Exception as exc:
            # Never attempt to continue by issuing unprotected partial writes.
            raise HTTPException(
                status_code=503, detail={"code": "assignment_unassign_atomicity_unavailable"},
            ) from exc
        return {"ok": True, "assignment_id": identifier,
                "driver_id": expected_driver, "order_number": assignment.get("order_number")}

    @router.post("/{assignment_id}/reassign")
    async def reassign(assignment_id: str, payload: ReassignPayload,
                       user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_operator(user)
        user_id = _merchant_user_id(actor)
        old = await db[ASSIGNMENTS].find_one({"user_id": user_id, "id": assignment_id, "active": True}, {"_id": 0})
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
            {"user_id": user_id, "$or": [{"order_id": old.get("order_id")}, {"order_number": old.get("order_number")}]}, {"_id": 0}
        )
        if not order:
            raise HTTPException(status_code=409, detail={"code": "canonical_order_not_found"})
        try:
            snapshot = carrier_assignment_snapshot(driver=driver, shipping_company=_order_shipping_company(order), shipping_city=_order_city(order))
        except StoreDeliveryRuleError as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc

        now = _now(); new_id = str(uuid.uuid4())
        new_row = {
            "id": new_id, "user_id": user_id, "session_id": None,
            "order_id": old.get("order_id"), "order_number": old.get("order_number"),
            "barcode": old.get("barcode"), **snapshot,
            "status": "assigned", "active": True, "assigned_at": now,
            "assigned_by": normalize_text(actor.get("id")), "delivered_at": None,
            "reassigned_from_assignment_id": old["id"], "reassignment_reason": normalize_text(payload.reason),
        }
        await db[ASSIGNMENTS].insert_one(new_row)
        old_update = await db[ASSIGNMENTS].update_one(
            {"user_id": user_id, "id": old["id"], "active": True, "status": old.get("status")},
            {"$set": {
                "active": False, "status": "reassigned", "reassigned_at": now,
                "reassigned_by": normalize_text(actor.get("id")), "reassigned_to_assignment_id": new_id,
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
                "last_reminder_code": None, "last_reminder_at": None,
                "updated_at": now, "updated_by": normalize_text(actor.get("id")),
            }},
        )
        await db[ORDERS].update_one(
            {"user_id": user_id, "$or": [{"order_id": old.get("order_id")}, {"order_number": old.get("order_number")}]},
            {"$set": {
                "store_delivery_assignment_id": new_id,
                "store_delivery_driver_id": driver["id"],
                "store_delivery_driver_name": driver.get("name"),
                "store_delivery_fee_snapshot": new_row.get("delivery_fee_snapshot"),
                "store_delivery_status": "assigned",
                "store_delivery_updated_at": now,
            }},
        )
        await db[WORKFLOWS].update_one(
            {
                "user_id": user_id,
                "order_number": old.get("order_number"),
            },
            {
                "$set": {
                    "store_delivery_assignment_id": new_id,
                    "store_courier_driver_profile_id": driver["id"],
                    "store_courier_assignee_id": (
                        normalize_text(driver.get("account_user_id"))
                        or f"store-driver:{driver['id']}"
                    ),
                    "store_courier_assignee_name": driver.get("name"),
                    "store_courier_assignment_state": ASSIGNED_WAITING_PICKUP,
                    "store_courier_assigned_at": now,
                    "store_courier_assigned_by_id": normalize_text(actor.get("id")),
                    "updated_at": now,
                },
                "$unset": {
                    "store_courier_picked_up_at": "",
                    "store_courier_picked_up_by_id": "",
                    "store_courier_delivered_at": "",
                    "store_courier_delivered_by_id": "",
                },
            },
        )
        await db[EVENTS].insert_one({
            "id": str(uuid.uuid4()), "user_id": user_id, "event_type": "store_delivery_reassigned",
            "order_id": old.get("order_id"), "old_assignment_id": old["id"], "new_assignment_id": new_id,
            "old_driver_id": old.get("driver_id"), "new_driver_id": driver["id"],
            "reason": normalize_text(payload.reason), "actor_id": normalize_text(actor.get("id")), "occurred_at": now,
        })
        new_row.pop("_id", None); new_row.pop("user_id", None)
        return {"ok": True, "old_assignment_id": old["id"], "assignment": new_row}

    return router


__all__ = [
    "_assignment_status_filter",
    "_delivery_duration_report",
    "make_store_delivery_reassignment_router",
]
