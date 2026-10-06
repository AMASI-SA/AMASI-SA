"""Explicit, fail-closed recovery assessment. No discovery or provider transport.

This module exposes no HTTP endpoint, scheduler or production command. The
operator must identify an existing operation; approval is never reconstructed
from hashes or today's source. The nine-case scope is intentionally fixed.
"""
from copy import deepcopy

from fastapi import HTTPException

from order_review_business_snapshot import build_snapshot, compare_snapshots, verify_snapshot
from product_fulfillment_rules import order_is_active, payment_is_eligible

RECOVERY_ORDERS = frozenset({
    "291715477", "291703306", "291967952", "291717149", "292127659",
    "291717456", "291702542", "291914588", "291507628",
})


def assess_recovery(op, *, request, source, order, acceptance, workflow, component):
    """Pure assessment; SAFE is conditional evidence, never an execution token.

    Old hash-only operations remain REQUIRES_REVIEW. A complete immutable
    approval snapshot must already be bound to the original operation. A caller
    cannot attach a current snapshot and label it an original approval.
    """
    def reject(reason, insufficient=False):
        return {"decision": "REQUIRES_REVIEW", "reason": reason,
                "evidence_status": "INSUFFICIENT_EVIDENCE" if insufficient else "COMPLETE"}

    if not isinstance(op, dict) or not isinstance(request, dict):
        return reject("review_recovery_evidence_missing", True)
    number = str(op.get("order_number", ""))
    if number not in RECOVERY_ORDERS:
        return reject("review_recovery_out_of_scope")
    required = ("_id", "user_id", "actor_id", "items", "source_fingerprint",
                "order_fingerprint", "workflow_fingerprint", "acceptance_snapshot",
                "provider_confirmed_at", "business_snapshot", "approved_component_source")
    if any(not op.get(key) for key in required) or not isinstance(op.get("revision"), int):
        return reject("review_recovery_evidence_missing", True)
    if (request.get("operation_id") != op["_id"] or not request.get("request_id")
            or request.get("user_id") != op["user_id"] or request.get("order_number") != number):
        return reject("review_recovery_identity_mismatch")
    approved = op["business_snapshot"]
    if not verify_snapshot(approved) or request.get("approval_hash") != approved.get("integrity_hash"):
        return reject("review_recovery_evidence_missing", True)
    if op.get("superseded_by"):
        return reject("review_approval_superseded")
    if op.get("state") not in {"requires_review", "provider_confirmed"}:
        return reject("review_recovery_state_invalid")
    last_error = op.get("last_error")
    error = op.get("resume_block_reason") or (last_error.get("code") if isinstance(last_error, dict) else None)
    if error != "review_completion_source_changed":
        return reject("review_recovery_not_source_mismatch")
    if not isinstance(source, dict) or not source or not order or not isinstance(component, dict) or not component:
        return reject("review_recovery_evidence_missing", True)
    if isinstance(order, dict):
        from order_engine.models import OrderDTO
        try:
            order = OrderDTO.model_validate(order)
        except ValueError:
            return reject("review_recovery_evidence_invalid", True)
    if source.get("user_id") != op["user_id"] or str(source.get("order_number")) != number:
        return reject("review_recovery_identity_mismatch")
    watermark = source.get("g47_salla_snapshot") or {}
    if (watermark.get("requires_authoritative_refresh") or watermark.get("component_pending")
            or watermark.get("revision") is None or not watermark.get("source_updated_at")):
        return reject("component_authoritative_refresh_required", True)
    if not order_is_active(order) or not payment_is_eligible(order.payment) or watermark.get("cancelled"):
        return reject("review_completion_order_ineligible")
    if acceptance != op["acceptance_snapshot"]:
        return reject("component_acceptance_changed")
    expected_component = op["approved_component_source"]
    if not expected_component.get("source_fingerprint") or not isinstance(expected_component.get("generation_at_approval"), int):
        return reject("review_recovery_evidence_missing", True)
    # Do not freeze transport revision as semantic equality. Live generation and
    # source revision are also asserted under the transaction by the existing
    # component acceptance ticket, before/after reconciliation and commit.
    if (component.get("cancelled") or not component.get("eligible")
            or not isinstance(component.get("generation"), int)
            or component["generation"] < expected_component["generation_at_approval"]
            or (component["generation"] != expected_component["generation_at_approval"]
                and component.get("source_updated_at") == expected_component.get("source_updated_at"))
            or any(component.get(key) != expected_component.get(key) for key in
                   ("source_fingerprint", "source_created_at", "cancelled", "eligible"))):
        return reject("component_acceptance_changed")
    from order_review_completion import approval_identity, workflow_fingerprint
    if workflow_fingerprint(workflow) != op["workflow_fingerprint"]:
        return reject("review_revision_conflict")
    try:
        current = build_snapshot(source, order, acceptance, identity=approval_identity(op))
        comparison = compare_snapshots(approved, current)
    except (HTTPException, ValueError, TypeError, KeyError):
        return reject("review_recovery_evidence_invalid", True)
    if not comparison["equal"]:
        return reject(comparison["code"])
    return {"decision": "SAFE_TO_RESUME", "reason": "business_facts_identical",
            "evidence_status": "COMPLETE", "operation_id": op["_id"],
            "approval_hash": approved["integrity_hash"], "comparison": comparison}


async def recover_existing(db, *, request):
    """Explicit local completion, same engine/lease; no provider call permitted.

    Not wired to any route or worker. The request is NOT trusted as approval:
    the engine reloads and assesses source/config/evidence inside each fence.
    """
    import order_review_routes as routes
    from auth import account_is_disabled
    from order_review_completion import OPERATIONS, complete_review_operation

    request = deepcopy(request)
    if str(request.get("order_number", "")) not in RECOVERY_ORDERS:
        raise HTTPException(409, detail={"code": "review_recovery_out_of_scope"})
    op = await db[OPERATIONS].find_one({"_id": request.get("operation_id"),
        "user_id": request.get("user_id"), "order_number": request.get("order_number")})
    if not op or not verify_snapshot(op.get("business_snapshot")):
        raise HTTPException(409, detail={"code": "review_recovery_evidence_missing"})
    if request.get("approval_hash") != op["business_snapshot"].get("integrity_hash") or not request.get("request_id"):
        raise HTTPException(409, detail={"code": "review_recovery_identity_mismatch"})

    async def instructions(scoped):
        actor = await scoped.users.find_one({"id": op["actor_id"]})
        if not actor or account_is_disabled(actor):
            raise HTTPException(403, detail={"code": "review_actor_unavailable"})
        routes._require_reviewer(actor)
        if routes._merchant_user_id(actor) != op["user_id"]:
            raise HTTPException(403, detail={"code": "review_actor_owner_changed"})
        await routes.enforce_stage_instructions(scoped, user_id=op["user_id"],
            order_number=op["order_number"], stage="pending_review", actor_id=op["actor_id"], order_wide=True)

    async def load(scoped):
        return await routes.get_order(routes.MongoOrderRepository(scoped),
            user_id=op["user_id"], order_number=op["order_number"])

    async def no_provider(_):
        raise RuntimeError("Recovery must never call provider transport")

    await instructions(db)
    order = await load(db)
    selector = {"user_id": op["user_id"], "order_number": op["order_number"]}
    return await complete_review_operation(db, user_id=op["user_id"], actor_id=op["actor_id"],
        actor_name=op.get("actor_name", ""), order=order,
        workflow=await db.order_review_workflows.find_one(selector), frozen_items=op["items"],
        revision=op["revision"], load_order=load, sync_salla=no_provider,
        enforce_instructions=instructions, source_snapshot=await db.unified_orders.find_one(selector),
        approved_acceptance=op["acceptance_snapshot"], resume_operation_id=op["_id"], recovery_request=request)
