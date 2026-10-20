"""Prove a fixed catalogue choice rename against immutable review evidence."""
from copy import deepcopy

from order_review_business_snapshot import (
    KNOWN_NESTED, _business_tree, _tree, verify_snapshot,
)


def _object(node):
    return node[1] if isinstance(node, list) and len(node) == 2 and node[0] == "object" else {}


def _array(node):
    return node[1] if isinstance(node, list) and len(node) == 2 and node[0] == "array" else []


def _string(node):
    return node[1] if isinstance(node, list) and len(node) == 2 and node[0] == "string" else None


def restored_catalog_labels(raw, operation):
    """Return old-label raw input only when every changed choice is proven.

    No fuzzy text matching, positional-only choice matching, or custom-input
    normalization. All option siblings (including price and unknown fields)
    must exactly match the approved business tree after restoring the label.
    The caller must still match the entire component fingerprint and fence all
    lifecycle, completion, cancellation and inventory-cohort checks.
    """
    snapshot = operation.get("business_snapshot")
    if not verify_snapshot(snapshot):
        return None
    identity = {key: operation.get(key) for key in (
        "_id", "user_id", "order_number", "revision", "workflow_fingerprint")}
    if snapshot["facts"]["identity"] != _tree(identity):
        return None
    approved_items = _array(_object(snapshot["facts"]["source"]).get("items"))
    current_items = raw.get("items")
    if not isinstance(current_items, list) or len(current_items) != len(approved_items):
        return None
    restored = deepcopy(raw)
    changes = 0
    for current, old_node in zip(restored["items"], approved_items):
        old = _object(old_node)
        if not isinstance(current, dict) or not current.get("id") or _tree(current["id"]) != old.get("id"):
            return None
        options = current.get("options")
        previous = _array(old.get("options"))
        if options is None and not previous:
            continue
        if not isinstance(options, list) or len(options) != len(previous):
            return None
        for option, previous_node in zip(options, previous):
            if not isinstance(option, dict):
                return None
            if _business_tree(option, KNOWN_NESTED) == previous_node:
                continue
            prior = _object(previous_node)
            value = option.get("value") if isinstance(option, dict) else None
            old_value = _object(prior.get("value"))
            old_name = _string(old_value.get("name"))
            old_field_name = _string(prior.get("name"))
            if (option.get("type") not in {"radio", "select"}
                    or not option.get("id") or _tree(option["id"]) != prior.get("id")
                    or not isinstance(value, dict) or not value.get("id")
                    or _tree(value["id"]) != old_value.get("id")
                    or not isinstance(value.get("name"), str) or not value["name"]
                    or not old_name or not old_field_name
                    or not isinstance(option.get("name"), str) or not option["name"]
                    or (old_name == value["name"] and old_field_name == option["name"])):
                return None
            # salla_refresh may add an exact display alias. Conflicting aliases
            # are not evidence of a rename.
            if "option_value" in value:
                if value["option_value"] != value["name"]:
                    return None
                value["option_value"] = old_name
            value["name"] = old_name
            option["name"] = old_field_name
            if _business_tree(option, KNOWN_NESTED) != previous_node:
                return None
            changes += 1
    return restored if changes else None


async def legacy_catalog_rename(db, *, user_id, order_number, raw, order, previous):
    """Read-only proof; caller holds the owner fence and writes acceptance."""
    from fulfillment_v2_routes import component_order_fingerprint
    from order_engine.mapper import map_salla_order
    from review_local_policy import load_local_review_workflows

    workflows = await load_local_review_workflows(db, user_id=user_id, order_numbers=[order_number])
    workflow = workflows.get(order_number) or {}
    if workflow.get("assembly_status") != "completed":
        return None
    operation = await db.order_review_completion_operations.find_one({
        "_id": workflow.get("review_completion_operation_id"),
        "user_id": user_id, "order_number": order_number,
        "state": "completed", "completion_mode": "mezan_local_v1",
    }) or {}
    if not operation or operation.get("superseded_by"):
        return None
    restored = restored_catalog_labels(raw, operation)
    if restored is None:
        return None
    # Keep the canonical payment/status and all current business fields. Only
    # mapped item labels may differ; the full historical fingerprint must match.
    restored_order = order.model_copy(update={"items": map_salla_order(restored).items})
    approved_fingerprint = component_order_fingerprint(restored_order)
    baseline = previous["source_fingerprint"]
    audit = previous.get("catalog_label_revalidation") or {}
    integrity = operation["business_snapshot"]["integrity_hash"]
    # Subsequent catalogue renames still anchor to the same immutable review,
    # not to the immediately preceding display name. Reject broken lineage.
    if audit:
        if (audit.get("operation_id") != operation["_id"]
                or audit.get("approval_integrity_hash") != integrity
                or audit.get("current_fingerprint") != baseline
                or audit.get("reason") != "fixed_catalog_choice_label_only"):
            return None
        baseline = audit.get("approved_fingerprint")
    if approved_fingerprint != baseline:
        return None
    return {"operation_id": operation["_id"], "previous_fingerprint": previous["source_fingerprint"],
            "approved_fingerprint": approved_fingerprint, "approval_integrity_hash": integrity,
            "reason": "fixed_catalog_choice_label_only"}
