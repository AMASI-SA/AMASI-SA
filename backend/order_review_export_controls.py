"""Per-item export controls for the stage-one review workflow.

These controls never delete Salla data. They only decide which reviewed
fields/items are exported to a supplier preparation file and which items are
routed directly to Mezan's internal preparation queue.
"""
from __future__ import annotations

from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from order_item_engine.mapper import map_order_item_identities
from order_review_routes import (
    EVENTS,
    REVIEW_COMPLETED_STAGES,
    WORKFLOWS,
    _merchant_user_id,
    _normalized,
    _now,
    _require_reviewer,
    _state_map,
    _text,
)


SUPPLIER_FILE_ROUTE = "supplier_file"
INTERNAL_PREPARATION_ROUTE = "internal_preparation"


class ReviewExportControlPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manual_hidden_spec_keys: Optional[list[str]] = Field(
        default=None,
        max_length=60,
    )
    preparation_route: Optional[
        Literal["supplier_file", "internal_preparation"]
    ] = None
    assigned_employee_id: Optional[str] = Field(default=None, max_length=120)


def normalize_spec_keys(values: Any) -> list[str]:
    return sorted({
        _normalized(value)
        for value in (values or [])
        if _text(value)
    })


def operational_excluded_keys(
    operational_items: list[dict[str, Any]],
    order_item_id: str,
) -> list[str]:
    return sorted({
        _normalized(spec.get("key") or spec.get("name"))
        for row in operational_items
        if _text(row.get("source_order_item_id")) == order_item_id
        for spec in (row.get("linked_specs") or [])
        if isinstance(spec, dict) and _text(spec.get("key") or spec.get("name"))
    })


def item_export_control_view(
    state: Optional[dict[str, Any]],
    *,
    operational_items: list[dict[str, Any]],
    order_item_id: str,
) -> dict[str, Any]:
    state = state or {}
    manual = normalize_spec_keys(
        state.get("manual_supplier_export_excluded_spec_keys")
    )
    operational = operational_excluded_keys(operational_items, order_item_id)
    hidden = sorted(set(manual) | set(operational))
    route = _text(state.get("preparation_route")) or SUPPLIER_FILE_ROUTE
    if route not in {SUPPLIER_FILE_ROUTE, INTERNAL_PREPARATION_ROUTE}:
        route = SUPPLIER_FILE_ROUTE
    supplier_export = (
        bool(state.get("supplier_export"))
        if "supplier_export" in state
        else route != INTERNAL_PREPARATION_ROUTE
    )
    return {
        "order_item_id": order_item_id,
        "manual_hidden_spec_keys": manual,
        "operational_hidden_spec_keys": operational,
        "hidden_spec_keys": hidden,
        "preparation_route": route,
        "supplier_export": supplier_export,
        "preparation_status": (
            _text(state.get("preparation_status"))
            or (
                "in_progress"
                if route == INTERNAL_PREPARATION_ROUTE
                else "pending_file"
            )
        ),
        "assigned_employee_id": _text(state.get("assigned_employee_id")) or None,
    }


def apply_export_control_patch(
    current: dict[str, Any],
    payload: ReviewExportControlPatch,
    *,
    operational_items: list[dict[str, Any]],
    order_item_id: str,
    actor_id: str,
) -> dict[str, Any]:
    state = dict(current)
    fields = payload.model_fields_set

    if "manual_hidden_spec_keys" in fields:
        manual = normalize_spec_keys(payload.manual_hidden_spec_keys)
        operational = operational_excluded_keys(operational_items, order_item_id)
        state["manual_supplier_export_excluded_spec_keys"] = manual
        state["supplier_export_excluded_spec_keys"] = sorted(
            set(manual) | set(operational)
        )

    if "preparation_route" in fields and payload.preparation_route:
        route = payload.preparation_route
        state["preparation_route"] = route
        if route == INTERNAL_PREPARATION_ROUTE:
            state["supplier_export"] = False
            state["preparation_status"] = "in_progress"
        else:
            state["supplier_export"] = True
            state["preparation_status"] = "pending_file"

    if "assigned_employee_id" in fields:
        state["assigned_employee_id"] = _text(payload.assigned_employee_id) or None

    state.update({
        "order_item_id": order_item_id,
        "review_status": state.get("review_status") or "pending_review",
        "control_revision": int(state.get("control_revision") or 0) + 1,
        "export_controls_updated_at": _now(),
        "export_controls_updated_by": actor_id,
    })
    return state


def partition_review_items_for_preparation(
    items: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Canonical split used by the future preparation-file generator."""
    supplier_items: list[dict[str, Any]] = []
    internal_items: list[dict[str, Any]] = []
    for row in items or []:
        route = _text(row.get("preparation_route")) or SUPPLIER_FILE_ROUTE
        supplier_export = row.get("supplier_export") is not False
        if route == INTERNAL_PREPARATION_ROUTE or not supplier_export:
            internal_items.append(row)
        else:
            supplier_items.append(row)
    return {
        "supplier_file_items": supplier_items,
        "internal_preparation_items": internal_items,
    }


def make_order_review_export_controls_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(
        prefix="/order-review-export-controls-v1",
        tags=["order-review-export-controls"],
    )
    repository = MongoOrderRepository(db)

    async def _order_and_item_ids(user_id: str, order_number: str):
        try:
            order = await get_order(
                repository,
                user_id=user_id,
                order_number=order_number,
            )
        except OrderNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "order_not_found"},
            ) from exc
        identities = map_order_item_identities(order)
        return order, {
            _text(item.order_item_id)
            for item in identities
            if _text(item.order_item_id)
        }

    @router.get("/{order_number}")
    async def get_export_controls(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        _, item_ids = await _order_and_item_ids(user_id, order_number)
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number},
            {"_id": 0},
        ) or {}
        states = _state_map(workflow)
        operational_items = list(workflow.get("operational_items") or [])
        return {
            "order_number": order_number,
            "stage": workflow.get("stage") or "pending_review",
            "items": [
                item_export_control_view(
                    states.get(order_item_id),
                    operational_items=operational_items,
                    order_item_id=order_item_id,
                )
                for order_item_id in sorted(item_ids)
            ],
        }

    @router.patch("/{order_number}/items/{order_item_id:path}")
    async def patch_export_controls(
        order_number: str,
        order_item_id: str,
        payload: ReviewExportControlPatch,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        actor_id = _text(reviewer.get("id"))
        order, item_ids = await _order_and_item_ids(user_id, order_number)
        if order_item_id not in item_ids:
            raise HTTPException(
                status_code=404,
                detail={"code": "order_item_not_found"},
            )
        if not payload.model_fields_set:
            raise HTTPException(
                status_code=422,
                detail={"code": "export_control_update_required"},
            )

        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number},
            {"_id": 0},
        )
        if (workflow or {}).get("stage") in REVIEW_COMPLETED_STAGES:
            raise HTTPException(
                status_code=409,
                detail={"code": "review_already_completed"},
            )

        states = _state_map(workflow)
        operational_items = list((workflow or {}).get("operational_items") or [])
        current = states.get(order_item_id, {
            "order_item_id": order_item_id,
            "review_status": "pending_review",
        })
        states[order_item_id] = apply_export_control_patch(
            current,
            payload,
            operational_items=operational_items,
            order_item_id=order_item_id,
            actor_id=actor_id,
        )
        now = _now()

        if workflow:
            result = await db[WORKFLOWS].update_one(
                {
                    "user_id": user_id,
                    "order_number": order_number,
                    "revision": int(workflow.get("revision") or 0),
                },
                {"$set": {
                    "items": list(states.values()),
                    "updated_at": now,
                    "updated_by": actor_id,
                }},
            )
            if not result.matched_count:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "review_revision_conflict"},
                )
        else:
            try:
                await db[WORKFLOWS].insert_one({
                    "user_id": user_id,
                    "order_number": order_number,
                    "order_id": order.order_id,
                    "stage": "pending_review",
                    "revision": 0,
                    "items": list(states.values()),
                    "operational_items": [],
                    "created_at": now,
                    "updated_at": now,
                    "updated_by": actor_id,
                })
            except DuplicateKeyError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "review_revision_conflict"},
                ) from exc

        await db[EVENTS].insert_one({
            "user_id": user_id,
            "order_number": order_number,
            "order_item_id": order_item_id,
            "event_type": "review_export_controls_updated",
            "changed_fields": sorted(payload.model_fields_set),
            "occurred_at": now,
            "actor_id": actor_id,
        })
        return item_export_control_view(
            states[order_item_id],
            operational_items=operational_items,
            order_item_id=order_item_id,
        )

    return router
