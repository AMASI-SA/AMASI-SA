"""Prove a zero-priced option currency change against immutable review evidence."""
from copy import deepcopy
import re

from order_review_business_snapshot import KNOWN_NESTED, _business_tree, _tree, verify_snapshot
from component_catalog_label_recovery import _array, _object, _string


def restore_zero_price_currencies(raw, operation):
    snapshot = operation.get("business_snapshot")
    if not verify_snapshot(snapshot):
        return None
    identity = {key: operation.get(key) for key in (
        "_id", "user_id", "order_number", "revision", "workflow_fingerprint")}
    if snapshot["facts"]["identity"] != _tree(identity):
        return None
    approved = _array(_object(snapshot["facts"]["source"]).get("items"))
    current = raw.get("items")
    if not isinstance(current, list) or len(approved) != len(current):
        return None
    restored = deepcopy(raw)
    for item, prior_node in zip(restored["items"], approved):
        prior = _object(prior_node)
        if not isinstance(item, dict) or not item.get("id") or _tree(item["id"]) != prior.get("id"):
            return None
        options = item.get("options")
        old_options = _array(prior.get("options"))
        if options is None and not old_options:
            continue
        if not isinstance(options, list) or len(options) != len(old_options):
            return None
        for option, old_node in zip(options, old_options):
            if not isinstance(option, dict):
                return None
            if _business_tree(option, KNOWN_NESTED) == old_node:
                continue
            old = _object(old_node)
            value = option.get("value")
            price = value.get("price") if isinstance(value, dict) else None
            old_price = _object(_object(old.get("value")).get("price"))
            currency = _string(old_price.get("currency"))
            if (not option.get("id") or _tree(option["id"]) != old.get("id")
                    or not isinstance(price, dict)
                    or _tree(price.get("amount")) != ["number", "0"]
                    or old_price.get("amount") != ["number", "0"]
                    or not isinstance(price.get("currency"), str)
                    or not re.fullmatch(r"[A-Z]{3}", price["currency"])
                    or not currency or not re.fullmatch(r"[A-Z]{3}", currency)):
                return None
            price["currency"] = currency
            # Every other field, including unknown option metadata, must match.
            if _business_tree(option, KNOWN_NESTED) != old_node:
                return None
    return restored


async def prove_zero_price_currency_change(db, *, user_id, order_number, raw, order, previous):
    from fulfillment_v2_routes import component_order_fingerprint
    from order_engine.mapper import map_salla_order
    from review_local_policy import load_local_review_workflows

    workflows = await load_local_review_workflows(db, user_id=user_id, order_numbers=[order_number])
    workflow = workflows.get(order_number)
    if not workflow:
        return None
    operation = await db.order_review_completion_operations.find_one({
        "_id": workflow["review_completion_operation_id"], "user_id": user_id,
        "order_number": order_number, "state": "completed", "completion_mode": "mezan_local_v1",
    }) or {}
    if not operation or operation.get("superseded_by"):
        return None
    restored = restore_zero_price_currencies(raw, operation)
    if restored is None:
        return None
    restored_order = order.model_copy(update={"items": map_salla_order(restored).items})
    approved_fingerprint = component_order_fingerprint(restored_order)
    baseline = previous["source_fingerprint"]
    audit = previous.get("zero_price_currency_revalidation") or {}
    integrity = operation["business_snapshot"]["integrity_hash"]
    if audit:
        if (audit.get("operation_id") != operation["_id"]
                or audit.get("approval_integrity_hash") != integrity
                or audit.get("current_fingerprint") != baseline
                or audit.get("reason") != "zero_option_price_currency_only"):
            return None
        baseline = audit.get("approved_fingerprint")
    if approved_fingerprint != baseline:
        return None
    return {"operation_id": operation["_id"], "approval_integrity_hash": integrity,
            "approved_fingerprint": approved_fingerprint,
            "previous_fingerprint": previous["source_fingerprint"],
            "reason": "zero_option_price_currency_only"}
