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
from order_review_source import FINGERPRINT_VERSION, canonical_order, canonical_source
from order_review_business_snapshot import build_snapshot, compare_snapshots, diagnostic, raw_source_hash

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


def order_fingerprint(order, version=FINGERPRINT_VERSION):
    if version == FINGERPRINT_VERSION:
        return _digest(canonical_order(order))
    if version != 1:
        _conflict("review_completion_fingerprint_version_unsupported")
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


def source_fingerprint(snapshot, version=FINGERPRINT_VERSION):
    if version == FINGERPRINT_VERSION:
        return _digest(canonical_source(snapshot))
    if version != 1:
        _conflict("review_completion_fingerprint_version_unsupported")
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


def approval_identity(op):
    return {key: op.get(key) for key in ("_id", "user_id", "order_number", "revision", "workflow_fingerprint")}


async def complete_review_operation(db, **kwargs):
    """Retain sanitized conflict evidence even when the approval transaction aborts."""
    try:
        return await _complete_review_operation(db, **kwargs)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        evidence = detail.get("source_diagnostic")
        if evidence and detail.get("operation_id"):
            async def record(scoped):
                await scoped[OPERATIONS].update_one({
                    "_id": detail["operation_id"], "user_id": kwargs["user_id"],
                    "state": {"$ne": "completed"},
                    "lease_token": None,
                    "business_snapshot.integrity_hash": detail.get("approval_integrity_hash"),
                    "$or": [{"source_diagnostic.observed_at": {"$exists": False}},
                            {"source_diagnostic.observed_at": {"$lte": evidence["observed_at"]}}],
                }, {"$set": {"source_diagnostic": evidence}})
            await operational_owner(db, kwargs["user_id"], record)
        raise


async def _complete_review_operation(db, *, user_id, actor_id, actor_name,
                                    order, workflow, frozen_items, revision,
                                    load_order, sync_salla, enforce_instructions,
                                    source_snapshot, approved_acceptance,
                                    reapprove_operation_id=None,
                                    expected_acceptance_fingerprint=None,
                                    resume_operation_id=None, recovery_request=None):
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
    if resume_operation_id:
        identity = resume_operation_id
    token = uuid.uuid4().hex
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
        return current

    async def validate(scoped, op):
        current_acceptance = await validate_acceptance(scoped, op)
        # A real write on the source document fences even source writers which
        # do not participate in owner serialization. No-op writes do not suffice.
        await scoped.unified_orders.update_one(selector, {"$inc": {"review_completion_source_fence": 1}})
        source = await scoped.unified_orders.find_one(selector) or {}
        lifecycle = await scoped[COMPONENT_LIFECYCLES].find_one(selector) or {}
        if lifecycle.get("cancelled"):
            _conflict("component_acceptance_changed")
        component_approval = op.get("approved_component_source")
        if component_approval:
            if (any(lifecycle.get(key) != component_approval.get(key) for key in
                    ("source_fingerprint", "source_created_at", "cancelled", "eligible"))
                    or (lifecycle.get("generation") != component_approval.get("generation_at_approval")
                        and lifecycle.get("source_updated_at") == component_approval.get("source_updated_at"))):
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
        version = op.get("fingerprint_version", 1)
        current = await load_order(scoped)
        if not order_is_active(current) or not payment_is_eligible(current.payment):
            _conflict("review_completion_order_ineligible")
        if op.get("business_snapshot") is not None:
            try:
                candidate = build_snapshot(source, current, current_acceptance, identity=approval_identity(op))
            except HTTPException as exc:
                approved = op["business_snapshot"]
                raise HTTPException(409, detail={
                    "code": "review_completion_source_changed", "operation_id": op["_id"],
                    "approval_integrity_hash": approved.get("integrity_hash"),
                    "source_diagnostic": {
                        "schema_version": approved.get("schema_version"),
                        "normalization_version": approved.get("normalization_version"),
                        "approved_source_hash": approved.get("source_hash"),
                        "current_source_hash": raw_source_hash(source),
                        "approved_canonical_hash": approved.get("canonical_hash"),
                        "current_canonical_hash": None,
                        "classification": "unknown", "code": "review_completion_source_changed",
                        "category": "conflicting_or_invalid_representation",
                        "observed_at": _now().isoformat(),
                        "differing_fields": (exc.detail or {}).get("differing_fields", ["/source/invalid_representation"]),
                    },
                }) from None
            comparison = compare_snapshots(op["business_snapshot"], candidate)
            if not comparison["equal"]:
                raise HTTPException(409, detail={
                    "code": comparison["code"], "operation_id": op["_id"],
                    "approval_integrity_hash": op["business_snapshot"].get("integrity_hash"),
                    "source_diagnostic": {**diagnostic(op["business_snapshot"], candidate),
                                          "observed_at": _now().isoformat()},
                })
        elif (source_fingerprint(source, version) != op["source_fingerprint"]
              or order_fingerprint(current, version) != op["order_fingerprint"]):
            _conflict("review_completion_source_changed")
        current_workflow = await scoped[WORKFLOWS].find_one(selector)
        if workflow_fingerprint(current_workflow) != op["workflow_fingerprint"]:
            _conflict("review_revision_conflict")
        if recovery_request is not None:
            from order_review_recovery_guard import assess_recovery
            decision = assess_recovery(op, request=recovery_request, source=source, order=current,
                acceptance=current_acceptance, workflow=current_workflow, component=lifecycle)
            if decision["decision"] != "SAFE_TO_RESUME":
                _conflict(decision["reason"])
        await enforce_instructions(scoped)
        return current

    async def claim(scoped):
        existing = await scoped[OPERATIONS].find_one({"_id": identity})
        version = existing.get("fingerprint_version", 1) if existing else FINGERPRINT_VERSION
        approved_order = order_fingerprint(order, version)
        if resume_operation_id and (not existing or existing.get("user_id") != user_id
                                    or existing.get("order_number") != number
                                    or existing.get("revision") != revision):
            _conflict("review_resume_evidence_missing")
        if existing and existing.get("superseded_by"):
            _conflict("review_approval_superseded")
        if existing and existing["state"] == "completed":
            return existing
        if existing and existing.get("state") == "requires_review" and recovery_request is None:
            _conflict("review_explicit_reapproval_required")
        if recovery_request is not None and (not existing or not resume_operation_id or reapprove_operation_id):
            _conflict("review_recovery_evidence_missing")
        now = _now()
        source = await scoped.unified_orders.find_one(selector) or {}
        if not existing and (source.get("g47_salla_snapshot") or {}).get("revision") != (source_snapshot.get("g47_salla_snapshot") or {}).get("revision"):
            _conflict("component_source_event_stale")
        if existing and existing.get("lease_until", "") > now.isoformat():
            _conflict("review_completion_in_progress")
        if existing and ((not existing.get("business_snapshot") and existing["order_fingerprint"] != approved_order)
                         or existing["workflow_fingerprint"] != approved_workflow):
            _conflict("review_completion_snapshot_changed")
        previous = None
        if reapprove_operation_id and not existing:
            previous = await scoped[OPERATIONS].find_one({"_id": reapprove_operation_id, **selector, "revision": revision})
            if (not previous or previous.get("state") == "completed" or previous.get("superseded_by")
                    or previous.get("lease_until", "") > now.isoformat()):
                _conflict("review_reapproval_conflict")
            previous_version = previous.get("fingerprint_version", 1)
            if (previous.get("order_fingerprint") != order_fingerprint(order, previous_version)
                    or previous.get("workflow_fingerprint") != approved_workflow
                    or previous.get("source_fingerprint") != source_fingerprint(source, previous_version)):
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
            "fingerprint_version": FINGERPRINT_VERSION,
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
            # Opt in only operations created by this version. Never migrate
            # historic operations or infer approval from a provider status.
            "auto_resume_version": 1, "resume_attempts": 0,
            "resume_due_at": now.isoformat(),
        }
        if not existing:
            op["business_snapshot"] = build_snapshot(source_snapshot, order, approved_acceptance,
                                                      identity=approval_identity(op))
        await validate(scoped, op)
        if previous:
            await scoped[OPERATIONS].update_one({"_id": previous["_id"]}, {"$set": {
                "superseded_by": identity, "superseded_at": now.isoformat(),
            }})
        op.update(lease_token=token, lease_until=(now + timedelta(seconds=LEASE_SECONDS)).isoformat())
        if recovery_request is not None:
            original_state = (op.get("recovery_audit") or {}).get("original_state", op["state"])
            op["recovery_audit"] = {"request_id": recovery_request["request_id"],
                "operation_id": identity, "approval_hash": op["business_snapshot"]["integrity_hash"],
                "decision": "SAFE_TO_RESUME", "started_at": now.isoformat(),
                "provider_mutation": False, "original_state": original_state}
            # Once explicitly claimed for local-only recovery, a crashed attempt
            # must not fall back into the provider-capable automatic worker.
            op["state"] = "requires_review"
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

    async def validate_for_recovery(ticket):
        async def check(scoped):
            await fenced(scoped)
            await validate(scoped, op)
            await assert_component_acceptance(scoped, ticket=ticket)
        await operational_owner(db, user_id, check)

    try:
        current, _, ticket = await evaluate()

        async def before_provider(scoped):
            await fenced(scoped)
            await validate(scoped, op)
            await assert_component_acceptance(scoped, ticket=ticket)
            lifecycle = await scoped[COMPONENT_LIFECYCLES].find_one(selector) or {}
            component_evidence = op.get("approved_component_source")
            if not op.get("approved_component_source"):
                # Freeze semantic component evidence once, before external I/O.
                # Generation remains fenced by the current ticket; a status-only
                # ingestion may legitimately issue a newer ticket for same facts.
                component_evidence = {key: lifecycle.get(key) for key in
                    ("source_fingerprint", "source_created_at", "source_updated_at", "cancelled", "eligible")}
                component_evidence["generation_at_approval"] = lifecycle.get("generation")
                await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
                    "approved_component_source": component_evidence}})
            await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
                # A failed verification read must not erase an already
                # durably confirmed external success during resumption.
                "state": "provider_confirmed" if op["state"] == "provider_confirmed" else "syncing",
                "provider_attempt_started_at": _now().isoformat(),
            }})
            return component_evidence
        if recovery_request is not None:
            # Recovery is local completion only. The durable confirmation must
            # already exist; do not POST, readback, or recreate confirmation.
            await validate_for_recovery(ticket)
        else:
            component_evidence = await operational_owner(db, user_id, before_provider)
            # Mutate the in-memory copy only after the transaction commits;
            # Mongo may retry its callback after a write conflict.
            op["approved_component_source"] = component_evidence
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
        if recovery_request is None:
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
            if recovery_request is not None:
                await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
                    "recovery_audit.completed_at": now,
                    "recovery_audit.workflow_stage": stage,
                    "recovery_audit.event_id": identity + ":completed",
                }})
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
