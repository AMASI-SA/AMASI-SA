"""PR1 operational holds. No commercial, provider, inventory or accounting writes.

Holds are overlays, not piece status rewrites. All control mutations and execution
claims serialize on the existing Mongo owner transaction. A persisted owner marker
keeps enforcement enabled after the rollout switch is turned off.
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from uuid import uuid4

from fastapi import HTTPException
from operational_atomic import operational_owner

FLAG = "ORDER_FULFILLMENT_LIFECYCLE_CONTROLS_ENABLED"
WORKFLOWS = "order_review_workflows"
PIECES = "mezan_preparation_pieces_v1"
HOLDS = "mezan_fulfillment_holds_v1"
AUDIT = "mezan_fulfillment_control_events_v1"
REQUESTS = "mezan_fulfillment_control_requests_v1"
EXECUTIONS = "mezan_fulfillment_execution_claims_v1"
CONTROL_OWNERS = "mezan_fulfillment_control_owners_v1"
INSTRUCTIONS = "mezan_order_tracking_instructions_v1"
MANAGE = "fulfillment.stop.manage"
SELF_STOP = "preparation.assigned.stop"
HOLD_STAGES = frozenset({"reviewed", "in_progress", "preparation", "assembly", "ready_to_ship"})
STAGES = (*sorted(HOLD_STAGES), "completed", "courier_dispatch", "delivering", "delivered")
_EXECUTION = ContextVar("fulfillment_lifecycle_execution", default=None)


def enabled():
    return os.environ.get(FLAG, "") == "true"


def fail(code, status=409, **details):
    raise HTTPException(status, detail={"code": code, **details})


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _identity(owner, value):
    return _digest([owner, value])


def _public(row):
    return {k: v for k, v in row.items() if k != "_id"}


async def guarded_owner(db, user_id):
    return enabled() or bool(await db[CONTROL_OWNERS].find_one({"_id": user_id, "user_id": user_id}))


async def _snapshot(db, owner, number):
    workflow = await db[WORKFLOWS].find_one({"user_id": owner, "order_number": number})
    if not workflow:
        fail("fulfillment_workflow_not_found", 404)
    component = await db["mezan_component_order_lifecycle_v1"].find_one({"user_id": owner, "order_number": number}) or {}
    source = await db["unified_orders"].find_one({"user_id": owner, "order_number": number}) or {}
    watermark = source.get("g47_salla_snapshot") or {}
    generation = _digest({
        "component_generation": component.get("generation", 0),
        "component_fingerprint": component.get("source_fingerprint"),
        "component_cancelled": component.get("cancelled", False),
        "source_revision": watermark.get("revision", 0),
        "source_updated_at": watermark.get("source_updated_at"),
        "source_cancelled": watermark.get("cancelled", False),
        "source_status": source.get("order_status_slug"),
        "experiment_generation": workflow.get("experiment_generation", 0),
    })
    return workflow, generation


def _authorized(context, owner):
    if context.get("merchant_id") != owner or not context.get("actor_id"):
        fail("fulfillment_actor_scope_invalid", 403)
    return bool(context.get("is_owner") or MANAGE in context.get("permissions", set()))


def policy(stage):
    """Commercial actions remain denied in every PR1 state, including flag-on."""
    return {
        "cancel_product": {"allowed": False, "reason": "commercial_cancellation_not_enabled"},
        "edit_product": {"allowed": False, "reason": "commercial_edit_not_enabled"},
        "add_product": {"allowed": False, "reason": "commercial_add_not_enabled"},
        "hold_order": {"allowed": stage in HOLD_STAGES,
                       "reason": None if stage in HOLD_STAGES else "fulfillment_stage_locked"},
    }


async def capabilities(db, *, user_id, order_number, context):
    manage = _authorized(context, user_id)
    workflow, generation = await _snapshot(db, user_id, order_number)
    stage = workflow.get("stage", "")
    actions = policy(stage)
    for action in ("hold_order", "hold_item", "hold_piece"):
        actions[action] = dict(actions["hold_order"])
        if not enabled():
            actions[action] = {"allowed": False, "reason": "fulfillment_controls_disabled"}
        elif not manage:
            actions[action] = {"allowed": False, "reason": "fulfillment_stop_permission_required"}
    actions["self_hold_piece"] = {
        "allowed": bool(enabled() and stage in HOLD_STAGES and
                        (context.get("is_owner") or SELF_STOP in context.get("permissions", set()))),
        "reason": "assigned_piece_only",
    }
    holds = await db[HOLDS].find({"user_id": user_id, "order_number": order_number, "status": "active"}).to_list(10001)
    claim = await db[EXECUTIONS].find_one({"_id": _identity(user_id, order_number), "user_id": user_id,
                                          "state": {"$in": ["active", "uncertain"]}})
    if claim:
        for action in ("hold_order", "hold_item", "hold_piece", "self_hold_piece"):
            actions[action] = {"allowed": False, "reason": "fulfillment_execution_in_flight"}
    for hold in holds:
        hold["can_resume"] = bool(hold.get("contract_version") == 2 and (manage or (
            SELF_STOP in context.get("permissions", set()) and hold.get("stop_type") == "employee"
            and hold.get("created_by") == context["actor_id"])))
    return {"enabled": enabled(), "stage": stage, "revision": int(workflow.get("revision") or 0),
            "generation": generation, "actions": actions, "active_holds": [_public(row) for row in holds],
            "execution_in_flight": bool(claim), "commercial_mutations_enabled": False}


def _request(payload):
    reason = str(payload.get("reason") or "").strip()
    key = str(payload.get("idempotency_key") or "").strip()
    revision, generation = payload.get("expected_revision"), payload.get("expected_generation")
    if not 3 <= len(reason) <= 1000 or not 8 <= len(key) <= 180:
        fail("fulfillment_control_reason_and_key_required", 422)
    if type(revision) is not int or revision < 0 or not isinstance(generation, str) or len(generation) != 64:
        fail("fulfillment_control_fences_required", 422)
    return reason, key


async def _replay(db, owner, key, fingerprint):
    old = await db[REQUESTS].find_one({"_id": _identity(owner, key), "user_id": owner})
    if old:
        if old["fingerprint"] != fingerprint:
            fail("fulfillment_idempotency_conflict")
        return {**old["result"], "idempotent_replay": True}


async def _fences(db, owner, number, payload):
    workflow, generation = await _snapshot(db, owner, number)
    if generation != payload["expected_generation"]:
        fail("fulfillment_generation_conflict")
    if int(workflow.get("revision") or 0) != payload["expected_revision"]:
        fail("fulfillment_revision_conflict")
    claim = await db[EXECUTIONS].find_one({"_id": _identity(owner, number), "user_id": owner,
                                          "state": {"$in": ["active", "uncertain"]}})
    if claim:
        fail("fulfillment_execution_in_flight")
    return workflow, generation


async def _save(db, owner, number, key, fingerprint, result, event, workflow):
    from order_change_event import ChangeActor, UnitSnapshot, OptionSnapshot, build_control_event
    hold = result["hold"]
    units = tuple(UnitSnapshot(order_item_id=row["order_item_id"], unit_index=row.get("unit_index") or 1,
                               generation=int(row.get("generation") or 0))
                  for row in hold.get("before_states", []) if row.get("order_item_id"))
    options = tuple(OptionSnapshot(key=str(row["key"]), value=str(row["value"]))
                    for row in hold.get("options_snapshot", []))
    change = build_control_event(order_number=number, change_id=event["hold_id"] + ":" + event["event_type"],
        idempotency_key=key, change_type="hold" if event["event_type"] == "fulfillment_hold_created" else "resume",
        actor=ChangeActor(actor_id=event["actor_id"], name=event.get("actor_name") or None),
        reason=event["reason"], timestamp=event["occurred_at"], old_fulfillment_stage=workflow["stage"],
        revision=result["revision"], generation=result["generation"], old_units=units, new_units=units,
        old_options=options, new_options=options, affected_employees=tuple(hold.get("employee_ids", [])))
    event["order_change_event"] = change.model_dump(mode="json")
    result["change_event"] = event["order_change_event"]
    revision = int(workflow.get("revision") or 0)
    changed = await db[WORKFLOWS].update_one(
        {"user_id": owner, "order_number": number, "revision": revision},
        {"$inc": {"revision": 1}, "$set": {"lifecycle_controls_version": 2}})
    if changed.matched_count != 1:
        fail("fulfillment_revision_conflict")
    await db[AUDIT].insert_one({"_id": _identity(owner, key), "user_id": owner, **event})
    await db[REQUESTS].insert_one({"_id": _identity(owner, key), "user_id": owner,
                                 "fingerprint": fingerprint, "result": result})
    return result


async def create_hold(db, *, user_id, order_number, context, payload):
    manage = _authorized(context, user_id)
    reason, key = _request(payload)
    scope, kind = payload.get("scope", "order"), payload.get("stop_type", "note")
    target = str(payload.get("target_id") or "").strip()
    if scope not in {"order", "item", "piece"} or kind not in {"cancel", "edit", "note", "employee"}:
        fail("fulfillment_hold_scope_invalid", 422)
    if scope != "order" and not target:
        fail("fulfillment_hold_target_required", 422)
    if scope == "order":
        target = order_number
    if kind == "employee":
        if scope != "piece" or not (context.get("is_owner") or SELF_STOP in context.get("permissions", set())):
            fail("fulfillment_self_stop_piece_only", 403)
    elif not manage:
        fail("fulfillment_stop_manage_permission_required", 403)
    fingerprint = _digest({"operation": "hold", "order": order_number, "actor": context["actor_id"], "payload": payload})

    async def apply(scoped):
        replay = await _replay(scoped, user_id, key, fingerprint)
        if replay:
            return replay
        if not enabled():
            fail("fulfillment_controls_disabled")
        workflow, generation = await _fences(scoped, user_id, order_number, payload)
        if workflow.get("stage") not in HOLD_STAGES:
            fail("fulfillment_stage_locked")
        query = {"user_id": user_id, "order_number": order_number,
                 "experiment_archived_at": {"$in": [None, ""]}}
        if scope == "piece":
            query["piece_id"] = target
        elif scope == "item":
            query["order_item_id"] = target
        pieces = await scoped[PIECES].find(query).to_list(10001)
        if len(pieces) > 10000:
            fail("fulfillment_hold_target_limit")
        if scope == "piece" and not pieces:
            fail("fulfillment_stop_target_not_available", 404)
        if scope == "item" and not pieces and not any(row.get("order_item_id") == target for row in workflow.get("items", [])):
            fail("fulfillment_stop_target_not_available", 404)
        if kind == "employee" and any(row.get("responsible_employee_id") != context["actor_id"] for row in pieces):
            fail("fulfillment_self_stop_assignment_required", 403)
        if scope == "piece" and pieces[0].get("status") == "cancelled":
            fail("fulfillment_piece_cancelled")
        existing = await scoped[HOLDS].find_one({"user_id": user_id, "order_number": order_number,
                                                "status": "active", "scope": scope, "target_id": target})
        if existing:
            fail("fulfillment_stop_already_active")
        now, hold_id = datetime.now(timezone.utc), "fulfillment-hold-" + uuid4().hex
        before = [{"piece_id": row["piece_id"], "status": row.get("status"),
                   "order_item_id": row.get("order_item_id"), "unit_index": row.get("unit_index"),
                   "generation": row.get("generation", 0),
                   "assembly_status": row.get("assembly_status"), "execution_status": row.get("execution_status")}
                  for row in pieces]
        hold = {"_id": hold_id, "id": hold_id, "user_id": user_id, "order_number": order_number,
                "contract_version": 2, "scope": scope, "target_id": target, "stop_type": kind,
                "status": "active", "note": reason, "reason": reason,
                "piece_ids": [row["piece_id"] for row in pieces], "before_states": before,
                "employee_ids": sorted({row["responsible_employee_id"] for row in pieces if row.get("responsible_employee_id")}),
                "options_snapshot": [{"key": row["piece_id"] + ":" + str(k), "value": v}
                                     for row in pieces for k, v in (row.get("product_options_snapshot") or {}).items()],
                "created_at": now, "created_by": context["actor_id"], "created_by_name": context.get("actor_name", ""),
                "generation": generation, "created_revision": payload["expected_revision"] + 1,
                "mezan_only": True, "salla_updated": False, "qoyod_updated": False}
        await scoped[HOLDS].insert_one(hold)
        instruction_payload = payload.get("instruction")
        instruction = None
        if instruction_payload:
            instruction = {**instruction_payload, "id": "tracking-" + hold_id, "user_id": user_id,
                "order_number": order_number, "scope": scope, "target_id": target, "target_ids": [target],
                "target_piece_ids": hold["piece_ids"], "hold_id": hold_id, "contract_version": 2,
                "status": "active", "operational_hold": True, "created_at": now, "updated_at": now,
                "created_by": context["actor_id"], "created_by_name": context.get("actor_name", ""),
                "acknowledged_by_ids": [], "acknowledgment_history": [],
                "mezan_only": True, "salla_updated": False, "qoyod_updated": False}
            await scoped[INSTRUCTIONS].insert_one(instruction)
            await scoped[HOLDS].update_one({"user_id": user_id, "id": hold_id},
                                           {"$set": {"instruction_id": instruction["id"]}})
            hold["instruction_id"] = instruction["id"]
        await scoped[CONTROL_OWNERS].update_one({"_id": user_id, "user_id": user_id},
                                               {"$setOnInsert": {"user_id": user_id, "contract_version": 2}}, upsert=True)
        result = {"ok": True, "hold": _public(hold), "revision": payload["expected_revision"] + 1,
                  "generation": generation, "idempotent_replay": False, "salla_updated": False, "qoyod_updated": False}
        if instruction:
            result["instruction"] = _public(instruction)
        event = {"event_type": "fulfillment_hold_created", "order_number": order_number, "hold_id": hold_id,
                 "actor_id": context["actor_id"], "actor_name": context.get("actor_name", ""), "reason": reason,
                 "occurred_at": now, "generation": generation, "before": {"hold": None, "revision": payload["expected_revision"], "pieces": before},
                 "after": {"hold": _public(hold), "revision": result["revision"]}, "idempotency_key": key}
        return await _save(scoped, user_id, order_number, key, fingerprint, result, event, workflow)
    return await operational_owner(db, user_id, apply)


async def resume_hold(db, *, user_id, hold_id, context, payload):
    manage = _authorized(context, user_id)
    reason, key = _request(payload)
    fingerprint = _digest({"operation": "resume", "hold": hold_id, "actor": context["actor_id"], "payload": payload})

    async def apply(scoped):
        hold = await scoped[HOLDS].find_one({"user_id": user_id, "id": hold_id})
        if not hold:
            fail("fulfillment_hold_not_found", 404)
        if not (manage or (SELF_STOP in context.get("permissions", set()) and hold.get("created_by") == context["actor_id"]
                           and hold.get("stop_type") == "employee")):
            fail("fulfillment_hold_release_permission_required", 403)
        replay = await _replay(scoped, user_id, key, fingerprint)
        if replay:
            return replay
        if hold.get("contract_version") != 2:
            fail("fulfillment_legacy_hold_requires_existing_contract")
        workflow, generation = await _fences(scoped, user_id, hold["order_number"], payload)
        if hold.get("status") != "active":
            fail("fulfillment_hold_not_active")
        now = datetime.now(timezone.utc)
        after = {**hold, "status": "released", "released_at": now, "released_by": context["actor_id"],
                 "release_note": reason, "released_generation": generation}
        await scoped[HOLDS].update_one({"user_id": user_id, "id": hold_id, "status": "active"},
                                       {"$set": {k: v for k, v in after.items() if k != "_id"}})
        instruction = None
        if hold.get("instruction_id"):
            instruction = await scoped[INSTRUCTIONS].find_one({"user_id": user_id, "id": hold["instruction_id"]})
            if not instruction:
                fail("fulfillment_hold_instruction_missing")
            # Evidence/approval requirements remain authoritative even when the
            # caller uses the generic resume endpoint instead of the UI adapter.
            required = instruction.get("required_action", "none")
            if required in {"upload_photos", "upload_photos_and_result"}:
                count = await scoped["mezan_order_tracking_instruction_evidence_v1"].count_documents(
                    {"user_id": user_id, "instruction_id": instruction["id"], "status": "submitted"})
                if not count:
                    fail("customer_service_instruction_photo_evidence_required")
            if not manage:
                fail("fulfillment_hold_release_permission_required", 403)
            instruction = {**instruction, "status": "completed", "completed_at": now,
                "completed_by": context["actor_id"], "completion_note": reason, "updated_at": now}
            await scoped[INSTRUCTIONS].update_one({"user_id": user_id, "id": instruction["id"]},
                {"$set": {k: v for k, v in instruction.items() if k != "_id"}})
        result = {"ok": True, "hold": _public(after), "revision": payload["expected_revision"] + 1,
                  "generation": generation, "idempotent_replay": False, "salla_updated": False, "qoyod_updated": False}
        if instruction:
            result["instruction"] = _public(instruction)
        event = {"event_type": "fulfillment_hold_resumed", "order_number": hold["order_number"], "hold_id": hold_id,
                 "actor_id": context["actor_id"], "actor_name": context.get("actor_name", ""), "reason": reason,
                 "occurred_at": now, "generation": generation,
                 "before": {"hold": _public(hold), "revision": payload["expected_revision"]},
                 "after": {"hold": _public(after), "revision": result["revision"]}, "idempotency_key": key}
        return await _save(scoped, user_id, hold["order_number"], key, fingerprint, result, event, workflow)
    return await operational_owner(db, user_id, apply)


async def assert_not_held(db, *, user_id, order_number, order_item_id="", piece_id="", order_wide=False):
    if not await guarded_owner(db, user_id):
        return
    rows = await db[HOLDS].find({"user_id": user_id, "order_number": order_number, "status": "active"}).to_list(10001)
    if len(rows) > 10000:
        fail("fulfillment_hold_target_limit")
    for row in rows:
        scope = row.get("scope", "order")
        matches = (order_wide or scope == "order" or
                   (scope == "item" and order_item_id and row.get("target_id") == order_item_id) or
                   (scope == "piece" and piece_id and row.get("target_id") == piece_id))
        if matches:
            fail("fulfillment_lifecycle_held", hold_id=row.get("id"), order_number=order_number, scope=scope)


def piece_generation(piece):
    return _digest({key: piece.get(key) for key in (
        "order_number", "order_item_id", "unit_index", "generation", "lifecycle_generation",
        "experiment_generation", "component_generation", "source_revision", "batch_id")})


async def assert_piece_current(db, *, user_id, piece_id, expected_revision=None, expected_generation=None):
    piece = await db[PIECES].find_one({"user_id": user_id, "piece_id": piece_id})
    if not piece:
        # Internal/direct-assembly units are materialized from the workflow.
        from preparation_piece_operations import _workflow_assembly_pieces
        workflows = await db[WORKFLOWS].find({"user_id": user_id, "$or": [
            {"operational_items.operational_item_id": piece_id}, {"items.direct_assembly_piece_ids": piece_id}]}).to_list(2)
        for workflow in workflows:
            piece = next((row for row in _workflow_assembly_pieces(workflow, order_number=workflow["order_number"])
                          if row.get("piece_id") == piece_id), None)
            if piece:
                break
    if not piece:
        fail("fulfillment_piece_not_found", 404)
    inactive = {"cancelled", "canceled", "replaced", "obsolete", "superseded", "archived"}
    state = str(piece.get("status") or "").lower()
    if (state in inactive or piece.get("cancelled") or piece.get("replaced_by") or piece.get("obsolete")
            or piece.get("current") is False or piece.get("active") is False or piece.get("is_current") is False
            or piece.get("experiment_archived_at")):
        fail("fulfillment_piece_not_current", piece_id=piece_id,
             message="هذه القطعة ملغاة أو مستبدلة أو لم تعد النسخة الحالية. حدّث الطلب ولا تكمل القطعة القديمة.")
    revision = int(piece.get("revision") or 0)
    if expected_revision is not None and expected_revision != revision:
        fail("fulfillment_piece_revision_conflict", piece_id=piece_id)
    if expected_generation is not None and expected_generation != piece_generation(piece):
        fail("fulfillment_piece_generation_conflict", piece_id=piece_id)
    if piece.get("order_item_id") and piece.get("unit_index") is not None:
        newer = await db[PIECES].find_one({"user_id": user_id, "order_number": piece.get("order_number"),
            "order_item_id": piece["order_item_id"], "unit_index": piece["unit_index"],
            "generation": {"$gt": int(piece.get("generation") or 0)},
            "current": {"$ne": False}, "active": {"$ne": False},
            "status": {"$nin": list(inactive)}, "experiment_archived_at": None})
        if newer:
            fail("fulfillment_piece_generation_conflict", piece_id=piece_id)
    return piece


@asynccontextmanager
async def execution_scope(db, *, user_id, targets, operation):
    """Durable exclusion across local transactions AND existing external IO.

No TTL: a crashed/uncertain execution must never silently unlock a shipment.
Nested calls reuse the claim but always validate their possibly broader scope.
"""
    if not await guarded_owner(db, user_id):
        yield
        return
    if not targets or any(not row.get("order_number") for row in targets):
        fail("fulfillment_execution_target_required")
    outer = _EXECUTION.get() or {}
    inherited = outer.get("claims", {}) if outer.get("owner") == user_id else {}
    numbers = sorted({row["order_number"] for row in targets})
    new_numbers = [number for number in numbers if number not in inherited]
    token = uuid4().hex

    async def acquire(scoped):
        for target in targets:
            if target.get("piece_id"):
                await assert_piece_current(scoped, user_id=user_id, piece_id=target["piece_id"],
                    expected_revision=target.get("expected_revision"), expected_generation=target.get("expected_generation"))
            await assert_not_held(scoped, user_id=user_id, order_number=target["order_number"],
                                  order_item_id=target.get("order_item_id", ""), piece_id=target.get("piece_id", ""),
                                  order_wide=not (target.get("order_item_id") or target.get("piece_id")))
        for number in new_numbers:
            identity = {"_id": _identity(user_id, number), "user_id": user_id}
            claim = await scoped[EXECUTIONS].find_one(identity)
            if claim and claim.get("state") in {"active", "uncertain"}:
                fail("fulfillment_execution_in_flight", order_number=number)
            await scoped[EXECUTIONS].update_one(identity, {"$set": {"user_id": user_id, "order_number": number,
                "token": token, "state": "active", "operation": operation, "started_at": datetime.now(timezone.utc)}}, upsert=True)
    await operational_owner(db, user_id, acquire)
    marker = _EXECUTION.set({"owner": user_id, "claims": {**inherited, **{number: token for number in new_numbers}}})
    state = "uncertain"
    try:
        yield
        state = "completed"
    except HTTPException:
        # A business rejection is a completed attempt, not an abandoned worker.
        state = "rejected"
        raise
    finally:
        _EXECUTION.reset(marker)
        if new_numbers:
            async def finish(scoped):
                for number in new_numbers:
                    await scoped[EXECUTIONS].update_one({"_id": _identity(user_id, number), "user_id": user_id, "token": token},
                        {"$set": {"state": state, "finished_at": datetime.now(timezone.utc)}})
            await operational_owner(db, user_id, finish)
