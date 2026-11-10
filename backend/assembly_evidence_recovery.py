"""Revalidate an enrolled completion against its immutable review approval.

No new approval, inventory operation or provider write is performed here.
An opaque old fingerprint alone can never authorize resealing changed material.
"""
from copy import deepcopy

from fastapi import HTTPException

from operational_atomic import operational_owner
from order_engine import shipping_label_service as shipping


def blocked():
    return shipping.ShippingLabelError(
        "assembly_evidence_reapproval_required", "تغيرت بيانات التجهيز؛ يلزم مراجعتها قبل الاستكمال.")


def same_evidence(before, current):
    from assembly_manual_label_refresh import _same_material_snapshot
    from assembly_status_policy import canonical_status
    def status(snapshot):
        ready = snapshot["ready"]
        return canonical_status({**ready["source"], "raw_by_source": {"salla_direct": ready["raw"]}})
    if status(before) == "completed" and status(current) != "completed":
        return False
    # Revision is used for CAS, not business identity across provider awaits.
    return (all(before[key] == current[key] for key in ("source_hash", "approval", "plan", "instructions"))
            and _same_material_snapshot(before["ready"], current["ready"]))


async def _capture(db, operation):
    from assembly_completion_delivery import FIELD, now, source_fingerprint, workflow_fingerprint
    from assembly_print_completion import _capture as capture_ready
    from assembly_status_policy import canonical_status, component_refresh_pending
    from preparation_piece_operations import (
        _current_assembly_order, _local_preparation_coverage_complete,
        _source_addition_assembly_coverage_complete, shipping_address_is_complete,
        _preparation_incomplete_services,
    )
    from order_review_completion import (
        OPERATIONS, approval_identity, uses_business_snapshot,
        source_fingerprint as review_source_fingerprint, order_fingerprint,
    )
    from order_review_business_snapshot import build_snapshot, compare_snapshots
    from fulfillment_v2_routes import assert_component_execution, allow_legacy_component_execution
    from stock_component_consumption_service import PLANS
    from order_tracking_notes import active_stage_instructions, enforce_instruction_rows

    owner, number = operation["user_id"], operation["order_number"]
    key = {"user_id": owner, "order_number": number}
    workflow = await db.order_review_workflows.find_one(key) or {}
    outbox = workflow.get(FIELD) or {}
    if (outbox.get("version") != 2 or outbox.get("legacy_readback_only")
            or outbox.get("claim") != operation[FIELD].get("claim")
            or not outbox.get("claim") or outbox.get("lease_until", "") <= now().isoformat()
            or workflow.get("stage") != "completed"
            or workflow_fingerprint(workflow) != outbox.get("workflow_fingerprint")
            or outbox.get("workflow_fingerprint") != operation[FIELD].get("workflow_fingerprint")
            or outbox.get("source_fingerprint") != operation[FIELD].get("source_fingerprint")
            or (workflow.get("manual_label_refresh") or {}).get("until", "") > now().isoformat()):
        raise blocked()
    source = await db.unified_orders.find_one(key)
    if (not source or component_refresh_pending(source)
            or canonical_status(source) not in {"in_progress", "completed"}):
        raise blocked()
    ready = await capture_ready(db, owner, number)
    order = await _current_assembly_order(db, **key)
    physical = [p for p in (ready or {}).get("pieces", []) if not p.get("virtual_kind")]
    if (not ready or ready["cancelled"] or not order
            or not shipping_address_is_complete(order.shipping)
            or not _local_preparation_coverage_complete(order, workflow, physical)
            or not _source_addition_assembly_coverage_complete(order, workflow, ready["pieces"])
            or any(_preparation_incomplete_services(p) for p in physical)):
        raise blocked()
    approval = await db[OPERATIONS].find_one({**key,
        "_id": workflow.get("review_completion_operation_id"), "state": "completed"})
    if (not approval or approval.get("superseded_by")
            or approval.get("completion_mode") != workflow.get("completion_mode")):
        raise blocked()
    if uses_business_snapshot(approval):
        candidate = build_snapshot(source, order, approval["acceptance_snapshot"],
            identity=approval_identity(approval), schema_version=approval["approval_contract_version"],
            normalization_version=approval["business_snapshot"]["normalization_version"])
        if not compare_snapshots(approval["business_snapshot"], candidate)["equal"]:
            raise blocked()
    elif (review_source_fingerprint(source) != approval.get("source_fingerprint")
            or order_fingerprint(order) != approval.get("order_fingerprint")):
        raise blocked()
    plan = await db[PLANS].find_one({"user_id": owner, "order_id": number})
    if plan:
        await assert_component_execution(db, user_id=owner, order_number=number, plan=plan)
    elif not await allow_legacy_component_execution(db, **key):
        raise blocked()
    instructions = await active_stage_instructions(db, **key, stage="assembly_labeling", order_wide=True)
    enforce_instruction_rows(instructions, stage="assembly_labeling", order_wide=True,
                             actor_id=outbox.get("actor_id", ""))
    return {"ready": ready, "source_hash": source_fingerprint(source),
            "approval": approval, "plan": plan, "instructions": instructions,
            "revision": workflow.get("revision")}


async def recover_evidence(db, operation):
    """One read-first recovery under the existing delivery claim and budget."""
    from assembly_completion_delivery import FIELD
    from assembly_completion_legacy_readback import _assert_provider_material, _assert_provider_shipping
    from assembly_status_policy import status_values
    from salla_shipping import extract_shipping

    owner, number = operation["user_id"], operation["order_number"]
    before = await operational_owner(db, owner, lambda scoped: _capture(scoped, operation))
    internal_id, order = await shipping._resolve_order(db, owner, number)
    raw = before["ready"]["raw"]
    if (str(order.get("id")) != internal_id or internal_id != before["ready"]["internal_id"]
            or str(order.get("reference_id")) != number
            or status_values(order.get("status")) | status_values(order.get("status_slug"))
                not in ({"in_progress"}, {"completed"})
            or not isinstance(order.get("items"), list) or not order["items"]
            or any(key in raw and key not in order for key in
                   ("shipping_address", "payment_method", "payment_status"))):
        raise blocked()
    saved_shipping = raw.get("shipping") or {}
    observed_shipping = order.get("shipping") or {}
    if any(key in saved_shipping and key not in observed_shipping for key in ("address", "recipient", "receiver")):
        raise blocked()
    observed = extract_shipping(order) or {}
    current_shipping = before["ready"]["shipping"]
    if (not (observed.get("company_code") or observed.get("company_name"))
            or (current_shipping.get("shipment_id")
                and str(observed.get("shipment_id") or "") != str(current_shipping["shipment_id"]))):
        raise blocked()
    _assert_provider_material(order, raw)
    _assert_provider_shipping(order, before["ready"]["shipping"])

    async def commit(scoped):
        current = await _capture(scoped, operation)
        if not same_evidence(before, current):
            raise blocked()
        patch = {f"{FIELD}.source_fingerprint": current["source_hash"]}
        changed = await scoped.order_review_workflows.update_one({
            "user_id": owner, "order_number": number, "revision": current["revision"],
            f"{FIELD}.claim": operation[FIELD]["claim"],
            f"{FIELD}.source_fingerprint": operation[FIELD].get("source_fingerprint"),
        }, {"$set": patch})
        if changed.matched_count != 1:
            raise blocked()
    try:
        await operational_owner(db, owner, commit)
    except HTTPException as exc:
        raise blocked() from exc
    result = deepcopy(operation)
    result[FIELD]["source_fingerprint"] = before["source_hash"]
    result["_recovery_evidence"] = before
    return result
