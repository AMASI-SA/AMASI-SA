"""Durable explicit review completion. Never infer approval from provider status.

Provider I/O is outside transactions. A fenced, expiring lease serializes callers;
the operation, workflow and completion event commit together. Retrying the same
review revision retains the originally approved snapshot and actor.
"""
from copy import deepcopy
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from fastapi import HTTPException

from operational_atomic import operational_owner
from product_fulfillment_rules import order_is_active, payment_is_eligible
from order_review_acceptance_snapshot import acceptance_snapshot, fingerprint

OPERATIONS = "order_review_completion_operations"
WORKFLOWS = "order_review_workflows"
EVENTS = "order_review_events"
LEASE_SECONDS = 120
# Bound token refresh, transport retries and probes together, not each request.
PROVIDER_CALL_TIMEOUT_SECONDS = 45
PROVIDER_GUARD = ContextVar("review_provider_guard", default=None)


async def guard_provider_request(method="POST"):
    guard = PROVIDER_GUARD.get()
    if guard is not None:
        await guard(method)


def _now():
    return datetime.now(timezone.utc)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str).encode()).hexdigest()


def order_fingerprint(order):
    # Status/timeline/provider update time are intentionally excluded: the
    # successful status POST itself can deliver a newer webhook. Eligibility
    # is checked independently on every read and again in the final transaction.
    row = order.model_dump(mode="json")
    return _digest({key: row.get(key) for key in (
        "order_id", "order_number", "items", "payment", "totals", "shipping",
        "customer_notes", "staff_notes", "is_gift",
    )})


def provider_fingerprint(order):
    row = order.model_dump(mode="json")
    return _digest({
        "items": [{key: item.get(key) for key in (
            "order_item_id", "source_item_id", "product_id", "parent_product_id",
            "variant_id", "quantity", "sku", "name", "color", "size", "material",
            "options_raw", "options_normalized", "custom_fields", "unit_price", "total",
        )} for item in row["items"]],
        "payment": {key: row["payment"].get(key) for key in (
            "method", "status", "paid_amount", "remaining_amount", "has_remaining_amount",
        )},
        "total": row["totals"].get("total"),
    })


def workflow_fingerprint(workflow):
    row = workflow or {}
    return _digest({key: row.get(key) for key in (
        "stage", "revision", "items", "operational_items",
    )})


def source_fingerprint(snapshot):
    raw = (snapshot.get("raw_by_source") or {}).get("salla_direct") or {}
    facts = {key: raw.get(key) for key in (
        "id", "reference_id", "items", "amounts", "payment_method", "payment_status",
        "customer", "shipping", "notes", "options",
    )}
    # Refresh copies the same shipping_address into shipping.address. Treat
    # that representation change as equivalent, without ignoring a new address.
    shipping = deepcopy(raw.get("shipping") or {})
    if isinstance(shipping, dict):
        if not shipping.get("address") and raw.get("shipping_address"):
            shipping["address"] = deepcopy(raw["shipping_address"])
        facts["shipping"] = shipping
    return _digest(facts)


def _conflict(code):
    raise HTTPException(409, detail={"code": code})


async def complete_review_operation(db, *, user_id, actor_id, actor_name,
                                    order, workflow, frozen_items, revision,
                                    load_order, sync_salla, enforce_instructions,
                                    source_snapshot, approved_acceptance,
                                    reapprove_operation_id=None,
                                    expected_acceptance_fingerprint=None):
    from fulfillment_v2_routes import (
        assert_component_acceptance, build_order_fulfillment_decision,
        reconcile_component_order_lifecycle, ensure_fulfillment_indexes,
        COMPONENT_LIFECYCLES, record_component_intake_failure,
    )

    number = order.order_number
    selector = {"user_id": user_id, "order_number": number}
    identity = "review_" + _digest([user_id, number, revision])
    if bool(reapprove_operation_id) != bool(expected_acceptance_fingerprint):
        _conflict("review_reapproval_inputs_required")
    if reapprove_operation_id:
        identity = "review_" + _digest([user_id, number, revision,
                                       reapprove_operation_id, expected_acceptance_fingerprint])
    token = uuid.uuid4().hex
    approved_order = order_fingerprint(order)
    approved_workflow = workflow_fingerprint(workflow)
    await ensure_fulfillment_indexes(db)

    async def validate_acceptance(scoped, op):
        from review_acceptance_config_guard import FENCES
        await scoped[FENCES].update_one({"_id": user_id}, {"$inc": {"fence": 1}}, upsert=True)
        current = await acceptance_snapshot(scoped, user_id=user_id, order=order)
        if current != op.get("acceptance_snapshot"):
            raise HTTPException(409, detail={
                "code": "component_acceptance_changed", "operation_id": op["_id"],
                "current_acceptance_fingerprint": fingerprint(current),
                "reapproval_required": True,
            })

    async def validate(scoped, op):
        await validate_acceptance(scoped, op)
        source = await scoped.unified_orders.find_one(selector) or {}
        lifecycle = await scoped[COMPONENT_LIFECYCLES].find_one(selector) or {}
        if lifecycle.get("cancelled"):
            _conflict("component_acceptance_changed")
        raw_status = ((source.get("raw_by_source") or {}).get("salla_direct") or {}).get("status") or {}
        if not isinstance(raw_status, dict):
            raw_status = {"name": raw_status}
        customized = raw_status.get("customized") or {}
        custom_name = customized.get("name") if isinstance(customized, dict) else customized
        if ((source.get("g47_salla_snapshot") or {}).get("cancelled") or not order_is_active(
            order.model_copy(update={
                "status": raw_status.get("slug") or source.get("order_status_slug") or order.status,
                "status_native": custom_name or raw_status.get("name") or source.get("order_status") or order.status_native,
            })
        )):
            _conflict("component_acceptance_changed")
        if source_fingerprint(source) != op["source_fingerprint"]:
            _conflict("review_completion_source_changed")
        current = await load_order(scoped)
        if not order_is_active(current) or not payment_is_eligible(current.payment):
            _conflict("review_completion_order_ineligible")
        if order_fingerprint(current) != op["order_fingerprint"]:
            _conflict("review_completion_source_changed")
        current_workflow = await scoped[WORKFLOWS].find_one(selector)
        if workflow_fingerprint(current_workflow) != op["workflow_fingerprint"]:
            _conflict("review_revision_conflict")
        await enforce_instructions(scoped)
        return current

    async def claim(scoped):
        existing = await scoped[OPERATIONS].find_one({"_id": identity})
        if existing and existing.get("superseded_by"):
            _conflict("review_approval_superseded")
        if existing and existing["state"] == "completed":
            return existing
        now = _now()
        source = await scoped.unified_orders.find_one(selector) or {}
        if not existing and (source.get("g47_salla_snapshot") or {}).get("revision") != (source_snapshot.get("g47_salla_snapshot") or {}).get("revision"):
            _conflict("component_source_event_stale")
        if existing and existing.get("lease_until", "") > now.isoformat():
            _conflict("review_completion_in_progress")
        if existing and (existing["order_fingerprint"] != approved_order
                         or existing["workflow_fingerprint"] != approved_workflow):
            _conflict("review_completion_snapshot_changed")
        previous = None
        if reapprove_operation_id and not existing:
            previous = await scoped[OPERATIONS].find_one({"_id": reapprove_operation_id, **selector, "revision": revision})
            if (not previous or previous.get("state") == "completed" or previous.get("superseded_by")
                    or previous.get("lease_until", "") > now.isoformat()):
                _conflict("review_reapproval_conflict")
            if (previous.get("order_fingerprint") != approved_order
                    or previous.get("workflow_fingerprint") != approved_workflow
                    or previous.get("source_fingerprint") != source_fingerprint(source)):
                _conflict("review_completion_snapshot_changed")
            if (fingerprint(approved_acceptance) != expected_acceptance_fingerprint
                    or previous.get("acceptance_snapshot") == approved_acceptance):
                _conflict("component_acceptance_changed")
            # Component plans freeze their recipe. A new review approval must
            # not describe new recipe inputs while silently reusing old demands.
            # Replanning is a separate operation, never an implicit review retry.
            from stock_component_consumption_service import PLANS
            recipe_keys = ("products", "product_bindings", "option_bindings", "resources")
            if (any((previous.get("acceptance_snapshot") or {}).get(key) != approved_acceptance.get(key)
                    for key in recipe_keys)
                    and await scoped[PLANS].find_one({"user_id": user_id, "order_id": number})):
                raise HTTPException(409, detail={"code": "component_acceptance_changed",
                                                "reason": "component_plan_reapproval_required"})
        op = existing or {
            "_id": identity, **selector, "revision": revision, "state": "prepared",
            "order_fingerprint": approved_order,
            "workflow_fingerprint": approved_workflow,
            "source_fingerprint": source_fingerprint(source_snapshot),
            "acceptance_snapshot": deepcopy(approved_acceptance),
            "acceptance_fingerprint": fingerprint(approved_acceptance),
            "supersedes_operation_id": reapprove_operation_id,
            "items": deepcopy(frozen_items),
            "operational_items": deepcopy((workflow or {}).get("operational_items") or []),
            "actor_id": actor_id, "actor_name": actor_name,
            "created_at": now.isoformat(),
        }
        await validate(scoped, op)
        if previous:
            await scoped[OPERATIONS].update_one({"_id": previous["_id"]}, {"$set": {
                "superseded_by": identity, "superseded_at": now.isoformat(),
            }})
        op.update(lease_token=token, lease_until=(now + timedelta(seconds=LEASE_SECONDS)).isoformat())
        await scoped[OPERATIONS].replace_one({"_id": identity}, op, upsert=True)
        return op

    op = await operational_owner(db, user_id, claim)
    if op["state"] == "completed":
        return {**op["result"], "already_reviewed": True}

    async def fenced(scoped):
        current = await scoped[OPERATIONS].find_one({"_id": identity})
        if not current or current.get("lease_token") != token:
            _conflict("review_completion_lease_lost")
        if current.get("superseded_by"):
            _conflict("review_approval_superseded")
        if current.get("lease_until", "") <= _now().isoformat():
            _conflict("review_completion_lease_expired")
        return current

    async def evaluate():
        async def evaluate_owned(scoped):
            await fenced(scoped)
            current = await validate(scoped, op)
            snapshot = await scoped.unified_orders.find_one(selector) or {}
            watermark = snapshot.get("g47_salla_snapshot") or {}
            decision = await build_order_fulfillment_decision(
                scoped, user_id=user_id, order=current,
                operational_items=op["operational_items"], review_items=op["items"],
            )
            ticket = await reconcile_component_order_lifecycle(
                scoped, user_id=user_id, order=current, actor_id=op["actor_id"],
                decision=decision, strict=True,
                source_revision=int(watermark.get("revision") or 0),
                source_updated_at=watermark.get("source_updated_at"),
            )
            return current, decision, ticket
        try:
            return await operational_owner(db, user_id, evaluate_owned)
        except HTTPException as exc:
            # Preserve failure evidence after rolling back every reservation.
            # Do not overwrite a newer worker/source or cancellation.
            async def record_failure(scoped):
                await fenced(scoped)
                await validate(scoped, op)
                snapshot = await scoped.unified_orders.find_one(selector) or {}
                watermark = snapshot.get("g47_salla_snapshot") or {}
                await record_component_intake_failure(
                    scoped, user_id=user_id, order_number=number,
                    source_updated_at=watermark.get("source_updated_at"),
                    source_revision=watermark.get("revision"),
                )
                await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
                    "last_error": exc.detail, "last_error_at": _now().isoformat(),
                }})
            await operational_owner(db, user_id, record_failure)
            raise

    try:
        current, _, ticket = await evaluate()

        async def before_provider(scoped):
            await fenced(scoped)
            await validate(scoped, op)
            await assert_component_acceptance(scoped, ticket=ticket)
            await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
                "state": "syncing", "provider_attempt_started_at": _now().isoformat(),
            }})
        await operational_owner(db, user_id, before_provider)
        async def renew_provider_lease(method):
            async def renew(scoped):
                await fenced(scoped)
                # A readback must still be possible after a concurrent config
                # change, so verified provider success can be retained durably.
                # Only writes require the approval to remain valid here.
                if method != "GET":
                    await validate(scoped, op)
                await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
                    "lease_until": (_now() + timedelta(seconds=LEASE_SECONDS)).isoformat(),
                }})
            await operational_owner(db, user_id, renew)
        context = PROVIDER_GUARD.set(renew_provider_lease)
        try:
            sync_status, sync_error = await sync_salla(current)
        finally:
            PROVIDER_GUARD.reset(context)
        if sync_status != "sent":
            raise HTTPException(502, detail={"code": "salla_review_status_sync_failed", "reason": sync_error})

        async def confirmed(scoped):
            await fenced(scoped)
            await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
                "state": "provider_confirmed", "provider_confirmed_at": _now().isoformat(),
            }})
        await operational_owner(db, user_id, confirmed)
        # A status-only webhook may invalidate the old ticket. Re-evaluate
        # against fresh facts, never waive generation/revision/stock guards.
        current, decision, ticket = await evaluate()

        async def finalize(scoped):
            latest = await fenced(scoped)
            if latest["state"] == "completed":
                return latest["result"]
            await validate(scoped, op)
            await assert_component_acceptance(scoped, ticket=ticket)
            now = _now().isoformat()
            stage = "ready_to_ship" if decision.get("ready_to_ship") is True else "reviewed"
            document = {
                **(workflow or {}), **selector, "order_id": current.order_id,
                "stage": stage, "revision": revision + 1, "items": op["items"],
                "operational_items": op["operational_items"], "fulfillment_decision": decision,
                "reviewed_at": op["created_at"], "reviewed_by": op["actor_id"],
                "reviewed_by_name": op["actor_name"], "updated_at": now,
                "updated_by": op["actor_id"], "salla_status_sync": "sent",
                "salla_status_sync_error": None, "salla_status_sync_at": latest["provider_confirmed_at"],
                "review_completion_operation_id": identity,
            }
            document.pop("_id", None)
            if stage == "ready_to_ship":
                document["ready_to_ship_at"] = now
            if workflow:
                result = await scoped[WORKFLOWS].replace_one({**selector, "revision": revision}, document)
                if not result.matched_count:
                    _conflict("review_revision_conflict")
            else:
                document["created_at"] = now
                await scoped[WORKFLOWS].insert_one(document)
            await scoped[EVENTS].insert_one({
                "_id": identity + ":completed", **selector,
                "operation_id": identity, "event_type": "order_review_completed",
                "item_count": len(op["items"]), "occurred_at": now, "actor_id": op["actor_id"],
            })
            response = {"ok": True, "order_number": number, "stage": stage,
                        "reviewed_item_count": len(op["items"]), "salla_status_sync": "sent",
                        "salla_status_sync_error": None, "fulfillment_decision": decision,
                        "operation_id": identity}
            await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
                "state": "completed", "result": response, "completed_at": now,
                "lease_until": "", "lease_token": None,
            }})
            return response
        return await operational_owner(db, user_id, finalize)
    finally:
        # Real process death leaves the durable lease for expiry/recovery.
        # A stale worker cannot clear the successor's token.
        await db[OPERATIONS].update_one({"_id": identity, "user_id": user_id, "lease_token": token},
                                       {"$set": {"lease_until": "", "lease_token": None}})
