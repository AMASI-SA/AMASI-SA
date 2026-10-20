"""Read-only display differences from the current immutable review approval."""
from copy import deepcopy

from component_catalog_label_recovery import _array, _object, _string
from order_review_business_snapshot import _tree, verify_snapshot


def option_changes_by_item(order, operation):
    snapshot = operation.get("business_snapshot")
    if not verify_snapshot(snapshot):
        return {}
    identity = {key: operation.get(key) for key in (
        "_id", "user_id", "order_number", "revision", "workflow_fingerprint")}
    if snapshot["facts"]["identity"] != _tree(identity):
        return {}
    current = {item.order_item_id: item for item in order.items}
    result = {}
    for item_node in _array(_object(snapshot["facts"]["order"]).get("items")):
        old = _object(item_node)
        line_id = _string(old.get("order_item_id"))
        item = current.get(line_id)
        if item is None:
            continue
        prior_options = _array(old.get("options_raw"))
        changes = []
        for option in item.options_raw or []:
            if not isinstance(option, dict) or not option.get("id"):
                continue
            matches = [_object(node) for node in prior_options
                       if _object(node).get("id") == _tree(option["id"])]
            # Missing/ambiguous identities must not invent a previous value.
            if len(matches) != 1:
                continue
            previous = matches[0]
            old_name, old_value = _string(previous.get("name")), _string(previous.get("value"))
            name, value = option.get("name"), option.get("value")
            if (old_name is None or old_value is None or not isinstance(name, str)
                    or not isinstance(value, str) or (name, value) == (old_name, old_value)):
                continue
            changes.append({"previous_name": old_name, "previous_value": old_value,
                            "current_name": name, "current_value": value})
        if changes:
            result[line_id] = changes
    return result


def annotate_piece_options(rows, changes_by_item, current_specs_by_item=None):
    """Attach display-only notices, replacing duplicate historical display rows."""
    result = deepcopy(rows)
    for piece in result:
        if piece.get("is_operational_item"):
            continue
        changes = changes_by_item.get(piece.get("order_item_id"), [])
        specs = piece.get("specifications") or []
        current_pairs = {(spec.get("name"), spec.get("value")) for spec in specs}
        applicable = [change for change in changes
                      if (change["current_name"], change["current_value"]) in current_pairs]
        old_pairs = {(change["previous_name"], change["previous_value"]) for change in applicable}
        new_pairs = {(change["current_name"], change["current_value"]) for change in applicable}
        canonical_pairs = (None if current_specs_by_item is None else {
            (spec.get("name"), spec.get("value"))
            for spec in current_specs_by_item.get(piece.get("order_item_id"), [])})
        annotated = []
        for spec in specs:
            pair = (spec.get("name"), spec.get("value"))
            if (pair in old_pairs and pair not in new_pairs
                    and canonical_pairs is not None and pair not in canonical_pairs):
                continue
            match = [change for change in applicable
                     if pair == (change["current_name"], change["current_value"])]
            annotated.append({**spec, **({"change": match[0]} if len(match) == 1 else {})})
        piece["specifications"] = annotated
    return result


async def assembly_option_notices(db, *, user_id, workflow, order, rows, current_specs_by_item=None):
    if order is None or workflow.get("completion_mode") != "mezan_local_v1":
        return rows
    operation_id = workflow.get("review_completion_operation_id")
    if not operation_id:
        return rows
    operation = await db.order_review_completion_operations.find_one({
        "_id": operation_id, "user_id": user_id, "order_number": order.order_number,
        "state": "completed", "completion_mode": "mezan_local_v1",
    }) or {}
    if not operation or operation.get("superseded_by"):
        return rows
    return annotate_piece_options(rows, option_changes_by_item(order, operation), current_specs_by_item)
