"""Safe fulfillment replay and operational stop controls for Mezan 2.

The replay facility never reverses an approved supplier invoice or its ledger
entries.  It archives the old operational allocations/pieces, returns only the
Mezan review workflow to ``reviewed``, and marks the next materialised pieces as
an experiment.  Supplier receiving can therefore exercise the real employee
and permission flow without creating another payable or changing product cost
defaults.  The replay permits only the order-status transition to Salla so
operators can verify the real reviewed-to-in-progress contract; all other
Salla writes remain disabled.

Stops are operational and fail closed.  They can target a complete order, one
order item, or one physical preparation piece.  Every affected employee gets
an in-app alert, and supplier invoice approval re-checks the current piece
state so a stop created after scanning still blocks the invoice.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from alerts_routes import _fp, _upsert_alert
from fulfillment_v2_routes import _actor_context
from order_review_routes import EVENTS, WORKFLOWS
from preparation_piece_operations import (
    PIECES,
    PIECE_EVENTS,
    PIECE_STATUS_ASSIGNED,
    PIECE_STATUS_BLOCKED,
    PIECE_STATUS_CANCELLED,
    PIECE_STATUS_IN_PROGRESS,
    PIECE_STATUS_READY_FOR_RECEIPT,
)
from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS
from supplier_receiving_routes import SESSIONS


EXPERIMENT_RUNS = "mezan_fulfillment_experiment_runs_v1"
FULFILLMENT_HOLDS = "mezan_fulfillment_holds_v1"

MANAGE_STOPS_PERMISSION = "fulfillment.stop.manage"
SELF_STOP_PERMISSION = "preparation.assigned.stop"
EXPERIMENT_RESET_PERMISSION = "fulfillment.experiment.reset"

STOP_TYPES = {"cancel", "edit", "note", "employee"}
STOP_TYPE_LABELS = {
    "cancel": "إيقاف إلغاء",
    "edit": "إيقاف تعديل",
    "note": "إيقاف ملاحظة",
    "employee": "إيقاف من موظف التجهيز",
}
ACTIVE_PIECE_STATUSES = {
    PIECE_STATUS_ASSIGNED,
    PIECE_STATUS_IN_PROGRESS,
    PIECE_STATUS_READY_FOR_RECEIPT,
    PIECE_STATUS_BLOCKED,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


class FulfillmentExperimentResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: str = Field(min_length=8, max_length=180)
    note: str | None = Field(default=None, max_length=1000)
    delivery_flow: Literal["salla", "store_courier"] = "salla"


class FulfillmentHoldCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["order", "item", "piece"] = "order"
    target_id: str | None = Field(default=None, max_length=180)
    stop_type: Literal["cancel", "edit", "note", "employee"]
    note: str = Field(min_length=3, max_length=1000)


class FulfillmentHoldReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


def hold_piece_patch(
    *,
    hold_id: str,
    stop_type: str,
    note: str,
    actor: dict[str, Any],
    stopped_at: datetime,
) -> dict[str, Any]:
    """Return the fail-closed piece fields shared by route and unit tests."""
    cancelled = stop_type == "cancel"
    actor_id = _text(actor.get("id"))
    actor_name = _text(actor.get("name") or actor.get("email"))
    return {
        "status": PIECE_STATUS_CANCELLED if cancelled else PIECE_STATUS_BLOCKED,
        "execution_status": (
            "cancelled_by_fulfillment_stop"
            if cancelled
            else "blocked_by_fulfillment_stop"
        ),
        "active_hold_id": hold_id,
        "hold_stop_type": stop_type,
        "hold_stop_label": STOP_TYPE_LABELS.get(stop_type, stop_type),
        "hold_note": note,
        "hold_actor_id": actor_id,
        "hold_actor_name": actor_name,
        "hold_started_at": stopped_at,
        "block_reason": note,
        "updated_at": stopped_at,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }


def release_piece_update(before: dict[str, Any], released_at: datetime) -> dict[str, Any]:
    """Restore the exact operational status that existed before the stop."""
    return {
        "$set": {
            "status": _text(before.get("status")) or PIECE_STATUS_ASSIGNED,
            "execution_status": (
                _text(before.get("execution_status")) or "assigned"
            ),
            "updated_at": released_at,
        },
        "$unset": {
            "active_hold_id": "",
            "hold_stop_type": "",
            "hold_stop_label": "",
            "hold_note": "",
            "hold_actor_id": "",
            "hold_actor_name": "",
            "hold_started_at": "",
            "block_reason": "",
        },
    }


def _public_piece(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "piece_id",
            "order_number",
            "order_item_id",
            "unit_index",
            "product_id",
            "product_name",
            "sku",
            "status",
            "execution_status",
            "file_number",
            "batch_id",
            "responsible_employee_id",
            "responsible_employee_name",
            "active_hold_id",
            "hold_stop_type",
            "hold_stop_label",
            "hold_note",
            "experiment_run_id",
            "experiment_generation",
            "experiment_mode",
        )
        if row.get(key) is not None
    }


def _public_hold(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "id",
            "order_number",
            "scope",
            "target_id",
            "stop_type",
            "stop_label",
            "note",
            "status",
            "piece_ids",
            "employee_ids",
            "created_at",
            "created_by",
            "created_by_name",
            "released_at",
            "released_by",
            "released_by_name",
            "release_note",
        )
        if row.get(key) is not None
    }


async def ensure_fulfillment_experiment_indexes(db: Any) -> None:
    await db[EXPERIMENT_RUNS].create_index(
        [("user_id", ASCENDING), ("order_number", ASCENDING), ("generation", DESCENDING)],
        unique=True,
        name="uq_fulfillment_experiment_generation_v1",
    )
    await db[EXPERIMENT_RUNS].create_index(
        [("user_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)],
        name="ix_fulfillment_experiment_status_v1",
    )
    await db[FULFILLMENT_HOLDS].create_index(
        [("user_id", ASCENDING), ("order_number", ASCENDING), ("status", ASCENDING)],
        name="ix_fulfillment_hold_order_v1",
    )
    await db[FULFILLMENT_HOLDS].create_index(
        [("user_id", ASCENDING), ("piece_ids", ASCENDING), ("status", ASCENDING)],
        name="ix_fulfillment_hold_piece_v1",
    )


def _can_manage_stops(context: dict[str, Any]) -> bool:
    return bool(
        context.get("is_owner")
        or MANAGE_STOPS_PERMISSION in context.get("permissions", set())
    )


def _can_self_stop(context: dict[str, Any]) -> bool:
    return bool(
        context.get("is_owner")
        or SELF_STOP_PERMISSION in context.get("permissions", set())
    )


async def _active_pieces(
    db: Any,
    *,
    merchant_id: str,
    order_number: str,
    mongo_session: Any = None,
) -> list[dict[str, Any]]:
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    return await db[PIECES].find(
        {
            "user_id": merchant_id,
            "order_number": order_number,
            "$or": [
                {"experiment_archived_at": {"$exists": False}},
                {"experiment_archived_at": None},
            ],
        },
        {"_id": 0},
        **kwargs,
    ).sort([("order_item_id", 1), ("unit_index", 1), ("piece_id", 1)]).to_list(10000)


async def _notify_employees(
    db: Any,
    *,
    hold: dict[str, Any],
    pieces: list[dict[str, Any]],
) -> int:
    by_employee: dict[str, dict[str, Any]] = {}
    for piece in pieces:
        employee_id = _text(piece.get("responsible_employee_id"))
        if employee_id:
            by_employee[employee_id] = piece
    notified = 0
    for employee_id, piece in by_employee.items():
        try:
            await _upsert_alert(db, employee_id, {
                "alert_type": "fulfillment_stop",
                "severity": "critical" if hold["stop_type"] == "cancel" else "warning",
                "title": f"{hold['stop_label']} للطلب #{hold['order_number']}",
                "message": (
                    f"توقف تجهيز {len(hold['piece_ids'])} قطعة. السبب: {hold['note']}"
                ),
                "related_entity_type": "order",
                "related_entity_id": hold["order_number"],
                "related_entity_url": "/fulfillment-v2?workspace=my-products",
                "fingerprint": _fp("fulfillment_stop", "hold", hold["id"]),
                "metadata": {
                    "hold_id": hold["id"],
                    "order_number": hold["order_number"],
                    "piece_ids": hold["piece_ids"],
                    "stop_type": hold["stop_type"],
                    "file_number": piece.get("file_number"),
                },
            })
            notified += 1
        except Exception:
            # The stop itself remains fail-closed even if alert delivery is
            # temporarily unavailable. The dashboard still shows the reason.
            continue
    return notified


def make_fulfillment_experiment_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/fulfillment-experiments-v1",
        tags=["Fulfillment Experiments and Stops"],
    )

    @router.get("/orders/{order_number}")
    async def order_state(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        if not (_can_manage_stops(context) or _can_self_stop(context)):
            raise HTTPException(
                status_code=403,
                detail={"code": "fulfillment_stop_permission_required"},
            )
        await ensure_fulfillment_experiment_indexes(db)
        merchant_id = context["merchant_id"]
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": merchant_id, "order_number": _text(order_number)},
            {"_id": 0},
        )
        pieces = await _active_pieces(
            db,
            merchant_id=merchant_id,
            order_number=_text(order_number),
        )
        if not context["is_owner"] and not _can_manage_stops(context):
            pieces = [
                row for row in pieces
                if _text(row.get("responsible_employee_id")) == context["actor_id"]
            ]
        runs = await db[EXPERIMENT_RUNS].find(
            {"user_id": merchant_id, "order_number": _text(order_number)},
            {"_id": 0},
        ).sort("generation", -1).limit(10).to_list(10)
        holds = await db[FULFILLMENT_HOLDS].find(
            {"user_id": merchant_id, "order_number": _text(order_number)},
            {"_id": 0, "before_states": 0},
        ).sort("created_at", -1).limit(50).to_list(50)
        return {
            "ok": True,
            "order_number": _text(order_number),
            "workflow_stage": _text((workflow or {}).get("stage")) or None,
            "workflow_revision": int((workflow or {}).get("revision") or 0),
            "pieces": [_public_piece(row) for row in pieces],
            "holds": [_public_hold(row) for row in holds],
            "latest_run": runs[0] if runs else None,
            "capabilities": {
                "can_reset_experiment": bool(context["is_owner"]),
                "can_manage_stops": _can_manage_stops(context),
                "can_self_stop": _can_self_stop(context),
            },
        }

    @router.post("/orders/{order_number}/reset")
    async def reset_order_experiment(
        order_number: str,
        payload: FulfillmentExperimentResetRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        if not context["is_owner"] or EXPERIMENT_RESET_PERMISSION not in context["permissions"]:
            raise HTTPException(
                status_code=403,
                detail={"code": "fulfillment_experiment_owner_required"},
            )
        order_number = _text(order_number)
        if _text(payload.confirmation) != f"RESET {order_number}":
            raise HTTPException(
                status_code=422,
                detail={"code": "fulfillment_experiment_confirmation_required"},
            )
        await ensure_fulfillment_experiment_indexes(db)
        merchant_id = context["merchant_id"]
        mongo_client = getattr(db, "client", None)
        if mongo_client is None or not hasattr(mongo_client, "start_session"):
            raise HTTPException(
                status_code=503,
                detail={"code": "fulfillment_experiment_atomic_transaction_required"},
            )
        run_id = f"fulfillment-exp-{uuid.uuid4().hex}"
        now = _now()

        async def finalize(mongo_session: Any) -> dict[str, Any]:
            workflow = await db[WORKFLOWS].find_one(
                {"user_id": merchant_id, "order_number": order_number},
                {"_id": 0},
                session=mongo_session,
            )
            if not workflow:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "fulfillment_experiment_order_not_found"},
                )
            pieces = await _active_pieces(
                db,
                merchant_id=merchant_id,
                order_number=order_number,
                mongo_session=mongo_session,
            )
            session_ids = sorted({
                _text(row.get("supplier_receiving_session_id"))
                for row in pieces
                if _text(row.get("supplier_receiving_session_id"))
            })
            if session_ids:
                open_session = await db[SESSIONS].find_one(
                    {
                        "user_id": merchant_id,
                        "id": {"$in": session_ids},
                        "status": {"$in": ["open", "cancelling"]},
                    },
                    {"_id": 0, "id": 1, "reference": 1},
                    session=mongo_session,
                )
                if open_session:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "fulfillment_experiment_open_receiving_session",
                            "session_id": open_session.get("id"),
                            "reference": open_session.get("reference"),
                        },
                    )
            latest = await db[EXPERIMENT_RUNS].find_one(
                {"user_id": merchant_id, "order_number": order_number},
                {"_id": 0},
                sort=[("generation", -1)],
                session=mongo_session,
            )
            generation = int((latest or {}).get("generation") or 0) + 1
            active_holds = await db[FULFILLMENT_HOLDS].count_documents(
                {
                    "user_id": merchant_id,
                    "order_number": order_number,
                    "status": "active",
                },
                session=mongo_session,
            )
            if active_holds:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "fulfillment_experiment_active_hold_must_release"},
                )
            if latest and _text(latest.get("status")) == "active":
                await db[EXPERIMENT_RUNS].update_one(
                    {"user_id": merchant_id, "id": latest.get("id"), "status": "active"},
                    {"$set": {"status": "superseded", "superseded_at": now, "updated_at": now}},
                    session=mongo_session,
                )
            archived_piece_count = 0
            if pieces:
                piece_ids = [
                    _text(row.get("piece_id"))
                    for row in pieces
                    if _text(row.get("piece_id"))
                ]
                result = await db[PIECES].update_many(
                    {
                        "user_id": merchant_id,
                        "piece_id": {"$in": piece_ids},
                        "$or": [
                            {"experiment_archived_at": {"$exists": False}},
                            {"experiment_archived_at": None},
                        ],
                    },
                    {"$set": {
                        "experiment_archived_at": now,
                        "experiment_archived_by_run_id": run_id,
                        "updated_at": now,
                    }},
                    session=mongo_session,
                )
                archived_piece_count = int(result.modified_count)
                if archived_piece_count != len(piece_ids):
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "fulfillment_experiment_piece_conflict"},
                    )
            allocation_rows = await db[PREPARATION_UNIT_ALLOCATIONS].find(
                {
                    "user_id": merchant_id,
                    "order_number": order_number,
                    "status": {"$in": ["reserved", "committed"]},
                },
                {"_id": 0},
                session=mongo_session,
            ).to_list(10000)
            # The allocation collection has a unique key per physical order
            # unit. Preserve its full snapshot, then free only this order.
            allocation_delete_result = await db[PREPARATION_UNIT_ALLOCATIONS].delete_many(
                {
                    "user_id": merchant_id,
                    "order_number": order_number,
                    "status": {"$in": ["reserved", "committed"]},
                },
                session=mongo_session,
            )
            if int(allocation_delete_result.deleted_count) != len(allocation_rows):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "fulfillment_experiment_allocation_conflict"},
                )
            workflow_result = await db[WORKFLOWS].update_one(
                {
                    "user_id": merchant_id,
                    "order_number": order_number,
                    "revision": workflow.get("revision"),
                },
                {
                    "$set": {
                        "stage": "reviewed",
                        "experiment_mode": True,
                        "experiment_run_id": run_id,
                        "experiment_generation": generation,
                        "experiment_reset_at": now,
                        "experiment_reset_by": context["actor_id"],
                        "experiment_delivery_flow": payload.delivery_flow,
                        "salla_status_writes_allowed": True,
                        "preparation_assignment_status": "unassigned",
                        "preparation_batch_ids": [],
                        "updated_at": now,
                    },
                    "$unset": {
                        "in_progress_at": "",
                        "in_progress_by": "",
                        "in_progress_by_name": "",
                        "preparation_fully_allocated_at": "",
                        "preparation_progress": "",
                        "completed_at": "",
                        "completed_by": "",
                        "delivery_flow": "",
                        "carrier_label_status": "",
                        "carrier_label_ready": "",
                        "carrier_label_url": "",
                        "carrier_label_type": "",
                        "carrier_name": "",
                        "carrier_tracking_number": "",
                        "carrier_shipment_id": "",
                        "carrier_label_message": "",
                        "carrier_label_error_code": "",
                        "carrier_label_error_message": "",
                        "carrier_label_verified_at": "",
                        "carrier_label_print_confirmed": "",
                        "carrier_label_print_confirmed_at": "",
                        "carrier_label_print_confirmed_by_id": "",
                        "carrier_label_print_confirmed_by_name": "",
                        "carrier_label_barcode": "",
                        "carrier_label_print_data": "",
                        "carrier_handoff_state": "",
                        "carrier_handoff_employee_id": "",
                        "carrier_handoff_employee_name": "",
                        "carrier_handoff_scanned_at": "",
                        "carrier_handoff_released_at": "",
                        "carrier_handoff_release_source": "",
                        "store_courier_assignment_state": "",
                        "store_courier_assignee_id": "",
                        "store_courier_assignee_name": "",
                        "store_courier_assigned_at": "",
                        "store_courier_assigned_by_id": "",
                        "store_courier_assigned_by_name": "",
                        "store_courier_assignment_barcode": "",
                        "store_courier_label_verified_at": "",
                        "store_courier_label_verified_by_id": "",
                        "delivering_at": "",
                        "delivered_at": "",
                    },
                    "$inc": {"revision": 1},
                },
                session=mongo_session,
            )
            if not workflow_result.modified_count:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "fulfillment_experiment_workflow_conflict"},
                )
            run = {
                "id": run_id,
                "user_id": merchant_id,
                "order_number": order_number,
                "generation": generation,
                "status": "active",
                "mode": "financially_isolated",
                "delivery_flow": payload.delivery_flow,
                "source_workflow_stage": _text(workflow.get("stage")),
                "source_workflow_revision": int(workflow.get("revision") or 0),
                "archived_piece_count": archived_piece_count,
                "archived_allocation_count": int(allocation_delete_result.deleted_count),
                "archived_allocation_snapshot": allocation_rows,
                "financial_writes_allowed": False,
                "supplier_payable_allowed": False,
                "salla_writes_allowed": False,
                "salla_status_writes_allowed": True,
                "qoyod_writes_allowed": False,
                "note": _text(payload.note) or None,
                "created_at": now,
                "created_by": context["actor_id"],
                "created_by_name": _text(user.get("name") or user.get("email")),
                "updated_at": now,
            }
            await db[EXPERIMENT_RUNS].insert_one(dict(run), session=mongo_session)
            await db[EVENTS].insert_one({
                "id": uuid.uuid4().hex,
                "user_id": merchant_id,
                "order_number": order_number,
                "event_type": "fulfillment_experiment_reset",
                "experiment_run_id": run_id,
                "experiment_generation": generation,
                "experiment_delivery_flow": payload.delivery_flow,
                "archived_piece_count": archived_piece_count,
                "archived_allocation_count": int(allocation_delete_result.deleted_count),
                "actor_id": context["actor_id"],
                "actor_name": _text(user.get("name") or user.get("email")),
                "occurred_at": now,
                "mezan_only": True,
                "salla_updated": False,
                "qoyod_updated": False,
            }, session=mongo_session)
            return {
                "ok": True,
                "run": {key: value for key, value in run.items() if key != "user_id"},
                "workflow_stage": "reviewed",
                "experiment_delivery_flow": payload.delivery_flow,
                "archived_piece_count": archived_piece_count,
                "archived_allocation_count": int(allocation_delete_result.deleted_count),
                "financial_writes": 0,
                "salla_status_writes_allowed": True,
                "salla_updated": False,
                "qoyod_updated": False,
            }

        try:
            async with await mongo_client.start_session() as mongo_session:
                return await mongo_session.with_transaction(finalize)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "fulfillment_experiment_reset_failed",
                    "message": "تعذّرت إعادة الطلب ذريًا؛ لم تتغير أي مرحلة.",
                },
            ) from exc

    @router.post("/orders/{order_number}/holds")
    async def create_hold(
        order_number: str,
        payload: FulfillmentHoldCreateRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        manage = _can_manage_stops(context)
        self_stop = _can_self_stop(context)
        if not (manage or self_stop):
            raise HTTPException(
                status_code=403,
                detail={"code": "fulfillment_stop_permission_required"},
            )
        employee_self_stop = payload.stop_type == "employee"
        if employee_self_stop and (not self_stop or payload.scope != "piece"):
            raise HTTPException(
                status_code=403,
                detail={"code": "fulfillment_self_stop_piece_only"},
            )
        if not employee_self_stop and not manage:
            raise HTTPException(
                status_code=403,
                detail={"code": "fulfillment_stop_manage_permission_required"},
            )
        order_number = _text(order_number)
        pieces = await _active_pieces(
            db,
            merchant_id=context["merchant_id"],
            order_number=order_number,
        )
        if payload.scope == "piece":
            pieces = [row for row in pieces if _text(row.get("piece_id")) == _text(payload.target_id)]
        elif payload.scope == "item":
            pieces = [row for row in pieces if _text(row.get("order_item_id")) == _text(payload.target_id)]
        if employee_self_stop:
            pieces = [
                row for row in pieces
                if _text(row.get("responsible_employee_id")) == context["actor_id"]
            ]
        pieces = [row for row in pieces if _text(row.get("status")) in ACTIVE_PIECE_STATUSES]
        if not pieces:
            raise HTTPException(
                status_code=404,
                detail={"code": "fulfillment_stop_target_not_available"},
            )
        conflicting = [
            _text(row.get("piece_id")) for row in pieces
            if _text(row.get("active_hold_id"))
        ]
        if conflicting:
            raise HTTPException(
                status_code=409,
                detail={"code": "fulfillment_stop_already_active", "piece_ids": conflicting},
            )
        now = _now()
        hold_id = f"fulfillment-hold-{uuid.uuid4().hex}"
        note = _text(payload.note)
        before_states = [{
            "piece_id": _text(row.get("piece_id")),
            "status": _text(row.get("status")) or PIECE_STATUS_ASSIGNED,
            "execution_status": _text(row.get("execution_status")) or "assigned",
        } for row in pieces]
        piece_ids = [row["piece_id"] for row in before_states]
        patch = hold_piece_patch(
            hold_id=hold_id,
            stop_type=payload.stop_type,
            note=note,
            actor=user,
            stopped_at=now,
        )
        result = await db[PIECES].update_many(
            {
                "user_id": context["merchant_id"],
                "piece_id": {"$in": piece_ids},
                "$or": [
                    {"active_hold_id": {"$exists": False}},
                    {"active_hold_id": None},
                    {"active_hold_id": ""},
                ],
            },
            {"$set": patch},
        )
        if int(result.modified_count) != len(piece_ids):
            before_by_id = {row["piece_id"]: row for row in before_states}
            for piece_id in piece_ids:
                await db[PIECES].update_one(
                    {
                        "user_id": context["merchant_id"],
                        "piece_id": piece_id,
                        "active_hold_id": hold_id,
                    },
                    release_piece_update(before_by_id[piece_id], now),
                )
            raise HTTPException(
                status_code=409,
                detail={"code": "fulfillment_stop_piece_conflict"},
            )
        employee_ids = sorted({
            _text(row.get("responsible_employee_id"))
            for row in pieces if _text(row.get("responsible_employee_id"))
        })
        hold = {
            "id": hold_id,
            "user_id": context["merchant_id"],
            "order_number": order_number,
            "scope": payload.scope,
            "target_id": _text(payload.target_id) or order_number,
            "stop_type": payload.stop_type,
            "stop_label": STOP_TYPE_LABELS[payload.stop_type],
            "note": note,
            "status": "active",
            "piece_ids": piece_ids,
            "employee_ids": employee_ids,
            "before_states": before_states,
            "created_at": now,
            "created_by": context["actor_id"],
            "created_by_name": _text(user.get("name") or user.get("email")),
            "source": "preparation_employee" if payload.stop_type == "employee" else "customer_service",
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        event = {
            "id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "order_number": order_number,
            "piece_ids": piece_ids,
            "hold_id": hold_id,
            "event_type": "fulfillment_stop_created",
            "stop_type": payload.stop_type,
            "note": note,
            "actor_id": context["actor_id"],
            "actor_name": _text(user.get("name") or user.get("email")),
            "occurred_at": now,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        try:
            await db[FULFILLMENT_HOLDS].insert_one(dict(hold))
            await db[PIECE_EVENTS].insert_one(dict(event))
            await db[EVENTS].insert_one(dict(event))
        except Exception as exc:
            await db[FULFILLMENT_HOLDS].delete_one({
                "user_id": context["merchant_id"],
                "id": hold_id,
            })
            await db[PIECE_EVENTS].delete_one({"id": event["id"]})
            await db[EVENTS].delete_one({"id": event["id"]})
            before_by_id = {row["piece_id"]: row for row in before_states}
            for piece_id in piece_ids:
                await db[PIECES].update_one(
                    {
                        "user_id": context["merchant_id"],
                        "piece_id": piece_id,
                        "active_hold_id": hold_id,
                    },
                    release_piece_update(before_by_id[piece_id], now),
                )
            raise HTTPException(
                status_code=503,
                detail={"code": "fulfillment_stop_persist_failed"},
            ) from exc
        notified_count = await _notify_employees(db, hold=hold, pieces=pieces)
        return {
            "ok": True,
            "hold": _public_hold(hold),
            "affected_piece_count": len(piece_ids),
            "notified_employee_count": notified_count,
            "notification_delivery_incomplete": notified_count < len(employee_ids),
            "supplier_invoice_blocked": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    @router.post("/holds/{hold_id}/release")
    async def release_hold(
        hold_id: str,
        payload: FulfillmentHoldReleaseRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        hold = await db[FULFILLMENT_HOLDS].find_one(
            {"user_id": context["merchant_id"], "id": _text(hold_id)},
            {"_id": 0},
        )
        if not hold:
            raise HTTPException(status_code=404, detail={"code": "fulfillment_hold_not_found"})
        can_release = _can_manage_stops(context) or (
            _can_self_stop(context)
            and _text(hold.get("created_by")) == context["actor_id"]
            and _text(hold.get("stop_type")) == "employee"
        )
        if not can_release:
            raise HTTPException(status_code=403, detail={"code": "fulfillment_hold_release_permission_required"})
        if _text(hold.get("status")) == "released":
            return {"ok": True, "hold": _public_hold(hold), "idempotent_replay": True}
        if _text(hold.get("status")) != "active":
            raise HTTPException(status_code=409, detail={"code": "fulfillment_hold_not_active"})
        now = _now()
        restored_piece_ids: list[str] = []
        hold_patch = hold_piece_patch(
            hold_id=hold["id"],
            stop_type=_text(hold.get("stop_type")),
            note=_text(hold.get("note")),
            actor={
                "id": hold.get("created_by"),
                "name": hold.get("created_by_name"),
            },
            stopped_at=(
                hold.get("created_at")
                if isinstance(hold.get("created_at"), datetime)
                else now
            ),
        )

        async def restore_hold_on_pieces(piece_ids: list[str]) -> None:
            for piece_id in piece_ids:
                await db[PIECES].update_one(
                    {
                        "user_id": context["merchant_id"],
                        "piece_id": piece_id,
                        "$or": [
                            {"active_hold_id": {"$exists": False}},
                            {"active_hold_id": None},
                            {"active_hold_id": ""},
                        ],
                    },
                    {"$set": hold_patch},
                )

        for before in hold.get("before_states") or []:
            piece_id = _text(before.get("piece_id"))
            result = await db[PIECES].update_one(
                {
                    "user_id": context["merchant_id"],
                    "piece_id": piece_id,
                    "active_hold_id": hold["id"],
                },
                release_piece_update(before, now),
            )
            if result.modified_count:
                restored_piece_ids.append(piece_id)
        if len(restored_piece_ids) != len(hold.get("piece_ids") or []):
            await restore_hold_on_pieces(restored_piece_ids)
            raise HTTPException(status_code=409, detail={"code": "fulfillment_hold_release_piece_conflict"})
        updated = await db[FULFILLMENT_HOLDS].find_one_and_update(
            {"user_id": context["merchant_id"], "id": hold["id"], "status": "active"},
            {"$set": {
                "status": "released",
                "released_at": now,
                "released_by": context["actor_id"],
                "released_by_name": _text(user.get("name") or user.get("email")),
                "release_note": _text(payload.note) or None,
                "updated_at": now,
            }},
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            await restore_hold_on_pieces(restored_piece_ids)
            raise HTTPException(
                status_code=409,
                detail={"code": "fulfillment_hold_release_conflict"},
            )
        alert_cleanup_incomplete = False
        try:
            await db.settlement_alerts.update_many(
                {
                    "user_id": {"$in": list(hold.get("employee_ids") or [])},
                    "fingerprint": _fp("fulfillment_stop", "hold", hold["id"]),
                    "status": {"$in": ["new", "snoozed"]},
                },
                {"$set": {"status": "read", "updated_at": now.isoformat()}},
            )
        except Exception:
            alert_cleanup_incomplete = True
        event = {
            "id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "order_number": hold["order_number"],
            "piece_ids": list(hold.get("piece_ids") or []),
            "hold_id": hold["id"],
            "event_type": "fulfillment_stop_released",
            "note": _text(payload.note) or None,
            "actor_id": context["actor_id"],
            "actor_name": _text(user.get("name") or user.get("email")),
            "occurred_at": now,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        audit_delivery_incomplete = False
        try:
            await db[PIECE_EVENTS].insert_one(dict(event))
            await db[EVENTS].insert_one(dict(event))
        except Exception:
            audit_delivery_incomplete = True
        return {
            "ok": True,
            "hold": _public_hold(updated or {**hold, "status": "released", "released_at": now}),
            "restored_piece_count": len(restored_piece_ids),
            "alert_cleanup_incomplete": alert_cleanup_incomplete,
            "audit_delivery_incomplete": audit_delivery_incomplete,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    return router


__all__ = [
    "EXPERIMENT_RESET_PERMISSION",
    "EXPERIMENT_RUNS",
    "FULFILLMENT_HOLDS",
    "MANAGE_STOPS_PERMISSION",
    "SELF_STOP_PERMISSION",
    "FulfillmentExperimentResetRequest",
    "FulfillmentHoldCreateRequest",
    "FulfillmentHoldReleaseRequest",
    "ensure_fulfillment_experiment_indexes",
    "hold_piece_patch",
    "make_fulfillment_experiment_router",
    "release_piece_update",
]
