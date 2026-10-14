"""Durable explicit review completion. Never infer approval from provider status.

New local approvals commit all effects in one owner transaction without provider
I/O. The retained provider contract keeps its fenced lease and immutable approval
evidence; it is not selected by the public completion route or automatic worker.
"""
from copy import deepcopy
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import uuid

from fastapi import HTTPException

from operational_atomic import operational_owner
from review_local_policy import LOCAL_COMPLETION_MODE
from product_fulfillment_rules import order_is_active, payment_is_eligible
from order_review_acceptance_snapshot import acceptance_snapshot, fingerprint
from order_review_business_snapshot import build_snapshot, compare_snapshots, diagnostic, raw_source_hash, verify_snapshot, REVIEW_SCOPE_VERSION

OPERATIONS = "order_review_completion_operations"
WORKFLOWS = "order_review_workflows"
EVENTS = "order_review_events"
LEASE_SECONDS = 120
# Bound token refresh, transport retries and probes together, not each request.
PROVIDER_CALL_TIMEOUT_SECONDS = 45
PROVIDER_GUARD = ContextVar("review_provider_guard", default=None)
PROVIDER_DISPATCH = ContextVar("review_provider_dispatch", default=None)
PROVIDER_DELIVERY_VERSION = 1
REVIEW_STAGE = ContextVar("review_completion_stage", default="entry")
LOGGER = logging.getLogger(__name__)


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


def approval_identity(op):
    return {key: op.get(key) for key in ("_id", "user_id", "order_number", "revision", "workflow_fingerprint")}


def uses_business_snapshot(op):
    version = op.get("approval_contract_version")
    if version is None and "business_snapshot" not in op:
        return False  # Unversioned Production operations retain their old guard.
    if (version not in (1, REVIEW_SCOPE_VERSION) or not verify_snapshot(op.get("business_snapshot"))
            or op["business_snapshot"]["schema_version"] != version):
        _conflict("review_completion_approval_evidence_missing")
    return True


def _review_409_log_detail(exc, *, stage, user_id, order_number, revision):
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    if exc.status_code != 409:
        return None
    allowed = {
        "code", "operation_id", "approval_integrity_hash", "current_acceptance_fingerprint",
        "current_source_hash", "approved_source_hash", "current_canonical_hash",
        "approved_canonical_hash", "differing_fields", "classification", "category",
        "schema_version", "normalization_version", "observed_at",
    }
    safe = {key: detail[key] for key in allowed if key in detail}
    safe["stage"] = stage
    safe["user_id"] = user_id
    safe["order_number"] = order_number
    safe["revision"] = revision
    return safe


async def complete_review_operation(db, **kwargs):
    """Retain sanitized conflict evidence even when the approval transaction aborts."""
    try:
        return await _complete_review_operation(db, **kwargs)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        evidence = detail.get("source_diagnostic")
        stage = REVIEW_STAGE.get()
        if exc.status_code == 409:
            LOGGER.warning("review_completion_409 %s", _review_409_log_detail(
                exc,
                stage=stage,
                user_id=kwargs.get("user_id"),
                order_number=getattr(kwargs.get("order"), "order_number", None),
                revision=kwargs.get("revision"),
            ))
        if evidence and detail.get("operation_id") and kwargs.get("completion_mode") != LOCAL_COMPLETION_MODE:
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


async def complete_local_review_operation(db, **kwargs):
    """Public completion policy: local only; callers cannot select a legacy mode."""
    from pymongo.errors import PyMongoError
    try:
        return await complete_review_operation(db, **kwargs, completion_mode=LOCAL_COMPLETION_MODE)
    except PyMongoError as exc:
        # A DB exception is not a terminal verdict for an uncertain user attempt.
        # No callback or POST retry here. Readback must reconcile the exact attempt.
        aborted = (getattr(exc, "code", None) == 112
                   and exc.has_error_label("TransientTransactionError")
                   and not exc.has_error_label("UnknownTransactionCommitResult"))
        code = "review_completion_transaction_conflict" if aborted else "review_completion_outcome_unknown"
        LOGGER.warning("%s mongo_code=%s transient=%s unknown_commit=%s", code,
                       getattr(exc, "code", None), exc.has_error_label("TransientTransactionError"),
                       exc.has_error_label("UnknownTransactionCommitResult"))
        raise HTTPException(503, detail={"code": code, "state": "unknown", "retry_post": False,
            "reconcile": "get_only", "execution_outcome": "aborted" if aborted else "unknown"}) from None


async def _complete_review_operation(db, *, user_id, actor_id, actor_name,
                                    order, workflow, frozen_items, revision,
                                    load_order, sync_salla, enforce_instructions,
                                    source_snapshot, approved_acceptance,
                                    reapprove_operation_id=None,
                                    expected_acceptance_fingerprint=None,
                                    resume_operation_id=None, completion_mode=None, approval_token=None,
                                    approved_identities=None, readback_binding=None, session_context=None):
    from fulfillment_v2_routes import (
        assert_component_acceptance, build_order_fulfillment_decision,
        reconcile_component_order_lifecycle, ensure_fulfillment_indexes,
        COMPONENT_LIFECYCLES, record_component_intake_failure,
    )

    if completion_mode not in (None, LOCAL_COMPLETION_MODE):
        _conflict("review_completion_mode_unknown")
    local = completion_mode == LOCAL_COMPLETION_MODE
    if local and (workflow or {}).get("completion_mode") not in (None, LOCAL_COMPLETION_MODE):
        _conflict("review_completion_mode_unknown")
    if local and (resume_operation_id or reapprove_operation_id):
        _conflict("review_completion_legacy_operation_requires_resolution")
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
        current = await load_order(scoped)
        if not order_is_active(current) or not payment_is_eligible(current.payment):
            _conflict("review_completion_order_ineligible")
        if uses_business_snapshot(op):
            try:
                candidate = build_snapshot(source, current, current_acceptance, identity=approval_identity(op),
                                           schema_version=op["approval_contract_version"])
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
        elif (source_fingerprint(source) != op["source_fingerprint"]
              or order_fingerprint(current) != op["order_fingerprint"]):
            _conflict("review_completion_source_changed")
        component_approval = op.get("approved_component_source")
        if component_approval:
            if (any(lifecycle.get(key) != component_approval.get(key) for key in
                    ("source_fingerprint", "source_created_at", "cancelled", "eligible"))
                    or (lifecycle.get("generation") != component_approval.get("generation_at_approval")
                        and lifecycle.get("source_updated_at") == component_approval.get("source_updated_at"))):
                _conflict("component_acceptance_changed")
        current_workflow = await scoped[WORKFLOWS].find_one(selector)
        if workflow_fingerprint(current_workflow) != op["workflow_fingerprint"]:
            _conflict("review_revision_conflict")
        await enforce_instructions(scoped)
        return current

    async def claim(scoped):
        if session_context is not None:
            from review_session_fence import fence_review_session
            await fence_review_session(scoped, user_id=user_id, **session_context)
        existing = await scoped[OPERATIONS].find_one({"_id": identity})
        if existing and (readback_binding is not None or existing.get("readback_binding") is not None):
            from review_completion_readback import require_binding
            require_binding(existing, readback_binding)
        approved_order = order_fingerprint(order)
        if resume_operation_id and (not existing or existing.get("user_id") != user_id
                                    or existing.get("order_number") != number
                                    or existing.get("revision") != revision):
            _conflict("review_resume_evidence_missing")
        if local:
            # Never adopt or redispatch a historic provider operation, including
            # one from an earlier revision. Resolution requires separate review.
            old = await scoped[OPERATIONS].find_one({
                **selector, "completion_mode": {"$ne": LOCAL_COMPLETION_MODE},
                "state": {"$ne": "completed"}, "superseded_by": {"$exists": False},
            })
            if old:
                raise HTTPException(409, detail={
                    "code": "review_completion_legacy_operation_requires_resolution",
                    "operation_id": old["_id"], "state": old.get("state"),
                })
        if existing and existing.get("completion_mode") not in (None, completion_mode):
            _conflict("review_completion_mode_unknown")
        if existing and existing.get("superseded_by"):
            _conflict("review_approval_superseded")
        if existing and existing["state"] == "completed":
            return existing
        if existing and existing.get("state") == "requires_review":
            _conflict("review_explicit_reapproval_required")
        if existing:
            uses_business_snapshot(existing)
            if existing.get("provider_delivery_version") not in (None, PROVIDER_DELIVERY_VERSION):
                _conflict("review_provider_delivery_contract_unknown")
        now = _now()
        source = await scoped.unified_orders.find_one(selector) or {}
        if local:
            from order_review_approval import verify_token, approval_fingerprint, reject
            from order_review_routes import _review_item_identities
            approved_digest = verify_token(approval_token, user_id=user_id,
                                           order_number=number, revision=revision,
                                           context=({key: value for key, value in readback_binding.items()
                                               if key not in {"client_request_id", "approval_fingerprint", "approval_token_sha256"}}
                                               if readback_binding is not None else None))
            # Frozen execution items were prepared from these route reads. They
            # must match the displayed approval too, including an A/B/A race.
            if approved_identities is None or approval_fingerprint(
                source_snapshot, order, approved_acceptance, workflow,
                approved_identities, user_id=user_id,
            ) != approved_digest:
                reject()
            current_order = await load_order(scoped)
            current_acceptance = await acceptance_snapshot(scoped, user_id=user_id, order=current_order)
            current_workflow = await scoped[WORKFLOWS].find_one(selector)
            current_items = await _review_item_identities(scoped, user_id, current_order, local_only=True)
            current_digest = approval_fingerprint(source, current_order, current_acceptance,
                                                 current_workflow, current_items, user_id=user_id)
            if current_digest != approved_digest:
                reject()
        elif not existing and (source.get("g47_salla_snapshot") or {}).get("revision") != (source_snapshot.get("g47_salla_snapshot") or {}).get("revision"):
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
            if uses_business_snapshot(previous):
                # Compare unchanged business facts under the OLD approval; the
                # explicitly confirmed new acceptance is checked separately.
                current_basis = build_snapshot(source, order, previous["acceptance_snapshot"],
                                               identity=approval_identity(previous),
                                               schema_version=previous["approval_contract_version"])
                same_business = compare_snapshots(previous["business_snapshot"], current_basis)["equal"]
            else:
                same_business = (previous.get("order_fingerprint") == order_fingerprint(order)
                                 and previous.get("source_fingerprint") == source_fingerprint(source))
            if not same_business or previous.get("workflow_fingerprint") != approved_workflow:
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
            "approval_contract_version": REVIEW_SCOPE_VERSION,
            "provider_delivery_version": PROVIDER_DELIVERY_VERSION,
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
        if local and not existing:
            op["completion_mode"] = LOCAL_COMPLETION_MODE
            if readback_binding is not None:
                op["readback_binding"] = deepcopy(readback_binding)
            for key in ("provider_delivery_version", "auto_resume_version", "resume_attempts", "resume_due_at"):
                op.pop(key, None)
        if not existing:
            # Explicit reapproval cannot erase a predecessor's possible external
            # effect. Never infer a fresh send from a new approval identity.
            if previous and (previous.get("provider_dispatch") or previous.get("provider_attempt_started_at")
                             or previous.get("resume_previous_state") in ("syncing", "provider_confirmed")
                             or previous.get("state") in ("syncing", "provider_confirmed")):
                op["provider_dispatch"] = {
                    **deepcopy(previous.get("provider_dispatch") or {"state": "outcome_unknown"}),
                    "origin_operation_id": previous["_id"],
                }
            # Preserve G47 prerequisite errors before freezing approval evidence.
            # This read-only check uses the transaction's authoritative document;
            # its legacy result never exempts an order from Business Snapshot.
            from fulfillment_v2_routes import legacy_component_cohort
            await legacy_component_cohort(scoped, user_id=user_id, order_number=number)
            op["business_snapshot"] = build_snapshot(source_snapshot, order, approved_acceptance,
                                                      identity=approval_identity(op),
                                                      schema_version=op["approval_contract_version"])
        await validate(scoped, op)
        if previous:
            await scoped[OPERATIONS].update_one({"_id": previous["_id"]}, {"$set": {
                "superseded_by": identity, "superseded_at": now.isoformat(),
            }})
        op.update(lease_token=token, lease_until=(now + timedelta(seconds=LEASE_SECONDS)).isoformat())
        await scoped[OPERATIONS].replace_one({"_id": identity}, op, upsert=True)
        return op

    async def persist_completion(scoped, op, latest, current, final_decision):
        now = _now().isoformat()
        stage = "reviewed" if local else ("ready_to_ship" if final_decision.get("ready_to_ship") is True else "reviewed")
        document = {
            **(workflow or {}), **selector, "order_id": current.order_id,
            "stage": stage, "revision": revision + 1, "items": op["items"],
            "operational_items": op["operational_items"], "fulfillment_decision": final_decision,
            "reviewed_at": op["created_at"], "reviewed_by": op["actor_id"],
            "reviewed_by_name": op["actor_name"], "updated_at": now,
            "updated_by": op["actor_id"], "salla_status_sync": "not_requested" if local else "sent",
            "salla_status_sync_error": None, "salla_status_sync_at": latest.get("provider_confirmed_at"),
            "review_completion_operation_id": identity,
        }
        if local:
            document["completion_mode"] = LOCAL_COMPLETION_MODE
            # An explicit approval cannot inherit an earlier auto-route rollback
            # target below reviewed. Never change original provider/source data.
            for key in ("auto_routed_instant", "auto_route_previous_stage", "ready_to_ship_at"):
                document.pop(key, None)
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
        event = {
            "_id": identity + ":completed", **selector,
            "operation_id": identity, "event_type": "order_review_completed",
            "item_count": len(op["items"]), "occurred_at": now, "actor_id": op["actor_id"],
        }
        if readback_binding is not None:
            event.update(readback_binding=deepcopy(readback_binding), committed_revision=revision + 1)
        await scoped[EVENTS].insert_one(event)
        response = {"ok": True, "order_number": number, "stage": stage,
                    "reviewed_item_count": len(op["items"]), "salla_status_sync": "not_requested" if local else "sent",
                    "salla_status_sync_error": None, "fulfillment_decision": final_decision,
                    "operation_id": identity}
        if local:
            response.update(state="completed", completion_mode=LOCAL_COMPLETION_MODE)
        commit_fields = {}
        if readback_binding is not None:
            from review_completion_readback import committed_proof
            commit_fields["committed_revision"] = revision + 1
            proof_op = {**op, "state": "completed", "result": response, "completed_at": now, **commit_fields}
            response.update(operation=committed_proof(proof_op, event), current_revision=revision + 1,
                            current_operation_id=identity)
        await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
            "state": "completed", "result": response, "completed_at": now,
            "lease_until": "", "lease_token": None,
            **commit_fields,
        }})
        return response

    async def finish_local(scoped):
        REVIEW_STAGE.set("claim")
        local_op = await claim(scoped)
        if local_op["state"] == "completed":
            if readback_binding is not None:
                from review_completion_readback import committed_proof
                event = await scoped[EVENTS].find_one({"_id": identity + ":completed", **selector})
                proof = committed_proof(local_op, event)
                current_workflow = await scoped[WORKFLOWS].find_one(selector) or {}
                return {**local_op["result"], "operation": proof, "already_reviewed": True,
                        "current_revision": current_workflow.get("revision"),
                        "current_operation_id": current_workflow.get("review_completion_operation_id")}
            return {**local_op["result"], "already_reviewed": True}
        REVIEW_STAGE.set("evaluate")
        current = await load_order(scoped)
        snapshot = await scoped.unified_orders.find_one(selector) or {}
        watermark = snapshot.get("g47_salla_snapshot") or {}
        decision = await build_order_fulfillment_decision(
            scoped, user_id=user_id, order=current,
            operational_items=local_op["operational_items"], review_items=local_op["items"],
        )
        ticket = await reconcile_component_order_lifecycle(
            scoped, user_id=user_id, order=current, actor_id=local_op["actor_id"],
            decision=decision, strict=True,
            source_revision=int(watermark.get("revision") or 0),
            source_updated_at=watermark.get("source_updated_at"),
        )
        REVIEW_STAGE.set("finalize")
        current = await validate(scoped, local_op)
        await assert_component_acceptance(scoped, ticket=ticket)
        from product_fulfillment_rules import evaluate_order_fulfillment
        final_decision = {**decision, **evaluate_order_fulfillment(order=current, lines=decision["lines"])}
        return await persist_completion(scoped, local_op, local_op, current, final_decision)

    if local:
        # One owner transaction, no separately committed claim/lease or provider
        # stage. An abort leaves no operation, stock, workflow or event effects.
        return await operational_owner(db, user_id, finish_local, **(
            {"review_session": session_context} if session_context is not None else {}))

    REVIEW_STAGE.set("claim")
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
        REVIEW_STAGE.set("evaluate")
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
        REVIEW_STAGE.set("before_provider")
        component_evidence = await operational_owner(db, user_id, before_provider)
        # Copy only after commit; Mongo may retry the transaction callback.
        op["approved_component_source"] = component_evidence
        delivery = op.get("provider_delivery_version") == PROVIDER_DELIVERY_VERSION
        dispatch = op.get("provider_dispatch") or {}
        journal = {
            "possible": (bool(dispatch) and dispatch.get("state") != "rejected") or op["state"] == "provider_confirmed",
            "read_only": (bool(dispatch) and dispatch.get("state") != "rejected") or op["state"] == "provider_confirmed",
            "attempt_id": dispatch.get("attempt_id"),
        }

        async def record_dispatch(state, evidence=None):
            # Do not persist raw provider messages, payloads, URLs or credentials.
            safe = {key: value for key, value in (evidence or {}).items()
                    if key in {"error_type", "reported_status", "phase"}}
            async def record_owned(scoped):
                latest = await fenced(scoped)
                attempt = latest.get("provider_dispatch") or {}
                if attempt.get("attempt_id") != journal["attempt_id"]:
                    _conflict("review_completion_lease_lost")
                await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
                    "provider_dispatch": {**attempt, **safe, "state": state, "observed_at": _now().isoformat()},
                }})
            await operational_owner(db, user_id, record_owned)
            journal["possible"] = state != "rejected"
            journal["read_only"] = state != "rejected"
            LOGGER.info("review_provider_dispatch operation_id=%s state=%s evidence=%s", identity, state, safe)

        journal["record"] = record_dispatch
        async def renew_provider_lease(method):
            async def renew(scoped):
                latest = await fenced(scoped)
                # A readback must still be possible after a concurrent config
                # change, so verified provider success can be retained durably.
                # Only writes require the approval to remain valid here.
                if method != "GET":
                    await validate(scoped, op)
                    if delivery:
                        prior = latest.get("provider_dispatch") or {}
                        if ((prior and prior.get("state") != "rejected") or latest["state"] == "provider_confirmed"):
                            _conflict("review_provider_confirmation_required")
                        if prior:
                            await scoped[OPERATIONS].update_one({"_id": identity}, {"$push": {
                                "provider_dispatch_history": {"$each": [prior], "$slice": -8},
                            }})
                        attempt_id = uuid.uuid4().hex
                        await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
                            "provider_dispatch": {"attempt_id": attempt_id, "state": "dispatch_started",
                                                  "started_at": _now().isoformat()},
                        }})
                    else:
                        attempt_id = None
                else:
                    attempt_id = None
                await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
                    "lease_until": (_now() + timedelta(seconds=LEASE_SECONDS)).isoformat(),
                }})
                return attempt_id
            attempt_id = await operational_owner(db, user_id, renew)
            if attempt_id:
                # Only publish the marker in memory after its transaction commits.
                journal.update(possible=True, read_only=True, attempt_id=attempt_id)
        context = PROVIDER_GUARD.set(renew_provider_lease)
        dispatch_context = PROVIDER_DISPATCH.set(journal if delivery else None)
        try:
            REVIEW_STAGE.set("sync_salla")
            sync_status, sync_error = await sync_salla(current)
        except HTTPException as exc:
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            if delivery and journal["possible"] and code in {
                "review_completion_lease_lost", "review_completion_lease_expired",
            }:
                # The durable pre-send marker survives. A stale worker must not
                # write a receipt; the next lease owner can only read/confirm.
                sync_status, sync_error = "confirmation_pending", code
            else:
                raise
        finally:
            PROVIDER_GUARD.reset(context)
            PROVIDER_DISPATCH.reset(dispatch_context)
        if delivery and sync_status == "confirmation_pending":
            return {"ok": False, "confirmation_pending": True, "operation_id": identity,
                    "order_number": number, "state": "syncing", "salla_status_sync": "pending",
                    "reason": sync_error}
        if sync_status != "sent":
            raise HTTPException(502, detail={"code": "salla_review_status_sync_failed", "reason": sync_error})

        async def confirmed(scoped):
            await fenced(scoped)
            await scoped[OPERATIONS].update_one({"_id": identity}, {"$set": {
                "state": "provider_confirmed", "provider_confirmed_at": _now().isoformat(),
            }})
        REVIEW_STAGE.set("confirmed")
        try:
            await operational_owner(db, user_id, confirmed)
        except HTTPException as exc:
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            if delivery and journal["possible"] and code in {"review_completion_lease_lost", "review_completion_lease_expired"}:
                return {"ok": False, "confirmation_pending": True, "operation_id": identity,
                        "order_number": number, "state": "syncing", "salla_status_sync": "pending", "reason": code}
            raise
        # A status-only webhook may invalidate the old ticket. Re-evaluate
        # against fresh facts, never waive generation/revision/stock guards.
        current, decision, ticket = await evaluate()

        async def finalize(scoped):
            latest = await fenced(scoped)
            if latest["state"] == "completed":
                return latest["result"]
            current = await validate(scoped, op)
            await assert_component_acceptance(scoped, ticket=ticket)
            final_decision = decision
            if op.get("approval_contract_version") == REVIEW_SCOPE_VERSION:
                # Approval excludes delivery metadata, but ready-to-ship still
                # requires the current address under the source transaction fence.
                from product_fulfillment_rules import evaluate_order_fulfillment
                final_decision = {**decision, **evaluate_order_fulfillment(
                    order=current, lines=decision["lines"])}
            return await persist_completion(scoped, op, latest, current, final_decision)
        REVIEW_STAGE.set("finalize")
        return await operational_owner(db, user_id, finalize)
    finally:
        # Real process death leaves the durable lease for expiry/recovery.
        # A stale worker cannot clear the successor's token.
        await db[OPERATIONS].update_one({"_id": identity, "user_id": user_id, "lease_token": token},
                                       {"$set": {"lease_until": "", "lease_token": None}})
