"""Confirm fully routed local preparation after the review transaction commits.

Review approval remains a local immutable operation. This separate preparation
transition reserves one permanent dispatch before a provider write and only
publishes current status aliases after an authoritative provider readback.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import logging
from typing import Any

from fastapi import HTTPException

from operational_atomic import operational_owner
from order_engine.repository import MongoOrderRepository
from order_engine.service import get_order
from order_review_completion import source_fingerprint
from review_local_policy import (
    LOCAL_COMPLETION_MODE, load_local_assignment_workflows,
    local_review_stage_eligible, execution_status_allowed,
)
from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS, order_items_with_review_snapshot, _unit_quantity

WORKFLOWS = "order_review_workflows"
OPERATIONS = "order_review_completion_operations"
EVENTS = "order_review_events"
LOGGER = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _direct_identity(number: str, item_id: str, index: int) -> str:
    return "direct_" + hashlib.sha256(f"{number}:{item_id}:{index}".encode("utf-8")).hexdigest()[:32]


def has_direct_route(workflow: dict) -> bool:
    return any(isinstance(row, dict) and row.get("preparation_route") == "direct_assembly"
               for row in workflow.get("items") or [])


async def preparation_routing_coverage(db, *, user_id: str, order, workflow: dict) -> dict | None:
    """Positive physical identities, completed approval, current source/stock.

    A missing/zero/fractional quantity, new live line, changed approved route or
    missing direct identity cannot be interpreted as an empty required set.
    """
    number = _text(getattr(order, "order_number", None))
    if (not number or workflow.get("user_id") != user_id or workflow.get("order_number") != number
            or workflow.get("completion_mode") != LOCAL_COMPLETION_MODE
            or not has_direct_route(workflow)):
        return None
    approved = await load_local_assignment_workflows(db, user_id=user_id, workflows=[workflow])
    if not local_review_stage_eligible(order, approved.get(number), {"reviewed", "in_progress"}):
        return None
    operation = await db[OPERATIONS].find_one({
        "_id": workflow.get("review_completion_operation_id"), "user_id": user_id,
        "order_number": number, "completion_mode": LOCAL_COMPLETION_MODE,
        "state": "completed", "superseded_by": {"$exists": False},
    }, {"_id": 0, "items": 1})
    if not operation:
        return None
    state_rows = workflow.get("items") or []
    frozen_rows = operation.get("items") or []
    states = {_text(row.get("order_item_id")): row for row in state_rows if isinstance(row, dict)}
    frozen = {_text(row.get("order_item_id")): row for row in frozen_rows if isinstance(row, dict)}
    if (not states or "" in states or set(states) != set(frozen)
            or len(states) != len(state_rows) or len(frozen) != len(frozen_rows)):
        return None
    fields = ("quantity", "preparation_route", "supplier_export", "direct_assembly_piece_ids")
    if any(any(state.get(key) != frozen[item_id].get(key) for key in fields) for item_id, state in states.items()):
        return None
    data = order.model_dump(mode="json")
    items = order_items_with_review_snapshot(data, workflow)
    if not items or any(_text(item.get("order_item_id")) not in states for item in items):
        return None
    allocations = await db[PREPARATION_UNIT_ALLOCATIONS].find({
        "user_id": user_id, "order_number": number, "status": "committed",
    }, {"_id": 0, "order_item_id": 1, "unit_index": 1}).to_list(None)
    by_item: dict[str, set[int]] = {}
    for row in allocations:
        index = row.get("unit_index")
        if type(index) is int and index > 0:
            by_item.setdefault(_text(row.get("order_item_id")), set()).add(index)
    required = allocated = direct = 0
    seen = set()
    for item in items:
        item_id = _text(item.get("order_item_id"))
        quantity = _unit_quantity(item.get("quantity"))
        state = states[item_id]
        if item_id in seen or not quantity or quantity != _unit_quantity(state.get("quantity")):
            return None
        seen.add(item_id)
        if state.get("preparation_route") == "direct_assembly":
            expected = [_direct_identity(number, item_id, index) for index in range(1, quantity + 1)]
            if state.get("supplier_export") is True or state.get("direct_assembly_piece_ids") != expected:
                return None
            direct += quantity
        elif state.get("supplier_export") is False or state.get("preparation_route") not in (None, "", "supplier_file"):
            return None
        else:
            required += quantity
            allocated += len(by_item.get(item_id, set()).intersection(range(1, quantity + 1)))
    if seen != set(states) or not direct:
        return None
    return {"fully_routed": required == allocated, "direct_quantity": direct,
            "required_quantity": required, "allocated_quantity": allocated,
            "remaining_quantity": max(0, required - allocated)}


def _provider_allowed(current: Any, *, internal_id: str, number: str) -> bool:
    if not isinstance(current, dict) or _text(current.get("id")) != internal_id:
        return False
    if _text(current.get("reference_id")) and _text(current["reference_id"]) != number:
        return False
    status = current.get("status")
    values = [current.get("status_slug")]
    if isinstance(status, dict):
        values.extend([status.get("slug"), status.get("name")])
        customized = status.get("customized")
        if isinstance(customized, dict):
            values.extend([customized.get("slug"), customized.get("name")])
        elif customized:
            values.append(customized)
    else:
        values.append(status)
    values = [value for value in values if _text(value)]
    return bool(values) and all(execution_status_allowed(value) for value in values)


async def reconcile_direct_preparation_after_review(db, *, user_id: str, order_number: str,
        actor: dict, result: dict, allow_dispatch: bool = True, batch_id: str = "") -> dict:
    """Return immutable review receipt plus separate preparation sync evidence.

    ``allow_dispatch=False`` may only read/confirm a previous outcome. A timeout
    never clears or re-arms the durable marker, even when this function is called
    again with dispatch enabled.
    """
    import preparation_piece_operations as pieces
    selector = {"user_id": user_id, "order_number": order_number}
    workflow = await db[WORKFLOWS].find_one(selector, {"_id": 0}) or {}
    if not has_direct_route(workflow) or workflow.get("completion_mode") != LOCAL_COMPLETION_MODE:
        return result
    evidence = {"state": "pending", "retry_post": False, "reconcile": "get_only"}
    try:
        order = await get_order(MongoOrderRepository(db), user_id=user_id, order_number=order_number)
        coverage = await preparation_routing_coverage(db, user_id=user_id, order=order, workflow=workflow)
        if not coverage or not coverage["fully_routed"]:
            return {**result, "preparation_status_sync": {**evidence, "reason": "preparation_routing_incomplete"}}
        if workflow.get("experiment_mode") is True and workflow.get("salla_status_writes_allowed") is not True:
            return {**result, "preparation_status_sync": {**evidence, "reason": "salla_status_writes_disabled"}}
        source = await db.unified_orders.find_one(selector) or {}
        material = source_fingerprint(source)
        revision = workflow.get("revision", 0)
        operation_id = workflow.get("review_completion_operation_id")
        internal_id = _text(getattr(getattr(order, "source", None), "source_order_id", None)) or _text(order.order_id)
        confirmed: dict = {}

        async def current_scope(scoped):
            latest = await scoped[WORKFLOWS].find_one(selector, {"_id": 0}) or {}
            current_source = await scoped.unified_orders.find_one(selector) or {}
            current_order = await get_order(MongoOrderRepository(scoped), user_id=user_id, order_number=order_number)
            current_coverage = await preparation_routing_coverage(scoped, user_id=user_id, order=current_order, workflow=latest)
            if (latest.get("review_completion_operation_id") != operation_id
                    or latest.get("revision", 0) != revision or source_fingerprint(current_source) != material
                    or not current_coverage or not current_coverage["fully_routed"]):
                raise HTTPException(409, detail={"code": "preparation_status_scope_changed"})
            return latest

        async def reserve():
            if not allow_dispatch:
                return False
            async def owned(scoped):
                await current_scope(scoped)
                reserved = await scoped[WORKFLOWS].update_one({**selector,
                    "revision": revision, "review_completion_operation_id": operation_id,
                    "stage": {"$in": ["reviewed", "in_progress"]},
                    "salla_status_sync_state": {"$exists": False}}, {"$set": {
                        "salla_status_sync_state": "dispatch_started"}})
                return reserved.modified_count == 1
            # operational_owner commits with majority+journaled write concern.
            return await operational_owner(db, user_id, owned)

        state, reason = await pieces._sync_salla_in_progress(db, user_id=user_id, order=order,
            reserve_dispatch=reserve, confirmed_order=confirmed,
            validate_current=lambda current: _provider_allowed(current, internal_id=internal_id, number=order_number))
        if state != "sent":
            return {**result, "preparation_status_sync": {**evidence, "reason": reason}}
        if (workflow.get("stage") == "in_progress" and workflow.get("salla_status_sync_state") == "sent"
                and source.get("order_status_slug") == "in_progress"
                and source.get("order_status") == "قيد التنفيذ"
                and ((source.get("raw_by_source") or {}).get("salla_direct") or {}).get("status") == confirmed.get("status")):
            return {**result, "preparation_status_sync": {"state": "sent", "retry_post": False}, "current_stage": "in_progress"}

        async def finish(scoped):
            latest = await current_scope(scoped)
            now = pieces._now()
            status = deepcopy(confirmed.get("status"))
            if not status:
                raise HTTPException(409, detail={"code": "preparation_status_readback_missing"})
            await scoped.unified_orders.update_one(selector, {"$set": {
                "order_status": "قيد التنفيذ", "order_status_slug": "in_progress",
                "raw_by_source.salla_direct.status": status,
                "raw_by_source.salla_direct.status_slug": "in_progress",
                "preparation_status_verified_at": now}})
            patch = {"stage": "in_progress", "salla_status_sync_state": "sent",
                "salla_status_name": "قيد التنفيذ", "salla_status_slug": "in_progress",
                "salla_status_synced_at": now, "preparation_assignment_status": "assigned",
                "preparation_fully_allocated_at": now,
                "preparation_progress": {**coverage, "updated_at": now}, "updated_at": now,
                "updated_by": _text(actor.get("id"))}
            if latest.get("stage") == "reviewed":
                patch.update(in_progress_at=now, in_progress_by=_text(actor.get("id")),
                    in_progress_by_name=_text(actor.get("name") or actor.get("email")))
            # This is a status-only continuation of the already committed
            # review, not a new approval/edit. Advancing review revision here
            # would invalidate exact-attempt V2 readback after a lost response.
            update = {"$set": patch, "$inc": {"preparation_status_revision": 1}}
            if batch_id:
                patch["preparation_progress"]["last_batch_id"] = batch_id
                update["$addToSet"] = {"preparation_batch_ids": batch_id}
            changed = await scoped[WORKFLOWS].update_one({**selector, "revision": revision,
                "review_completion_operation_id": operation_id, "stage": {"$in": ["reviewed", "in_progress"]}},
                update)
            if changed.modified_count != 1:
                raise HTTPException(409, detail={"code": "preparation_status_scope_changed"})
            if latest.get("stage") == "reviewed":
                await scoped[EVENTS].insert_one({**selector, "operation_id": operation_id,
                    "event_type": "order_moved_to_in_progress", "occurred_at": now,
                    "actor_id": _text(actor.get("id")), "mezan_only": False,
                    "salla_updated": True, "qoyod_updated": False, "direct_preparation": True})
        await operational_owner(db, user_id, finish)
        return {**result, "preparation_status_sync": {"state": "sent", "retry_post": False}, "current_stage": "in_progress"}
    except Exception as failure:
        reason = (failure.detail.get("code") if isinstance(failure, HTTPException) and isinstance(failure.detail, dict)
                  else type(failure).__name__)
        LOGGER.warning("direct_preparation_sync_pending operation_id=%s reason=%s",
            workflow.get("review_completion_operation_id"), reason)
        return {**result, "preparation_status_sync": {**evidence, "reason": reason}}
