"""Ready for received supplier work, independent of component approval.

The owner chose actual supplier custody and current Salla status as the Ready
basis for this path. These helpers never clear source flags, accept a component
plan, consume stock or manufacture a receipt. Delivery uses the same saved
Ready facts and complete current product coverage before trusting this basis.
"""
from decimal import Decimal, InvalidOperation

from assembly_status_policy import canonical_status


POLICY = "supplier_receipt_v1"


def supplier_current_units(source, workflow):
    """Current line membership, without any accepted-source/component snapshot.

    Search prepares this once per order. A received unit removed from the
    current order must not become Ready just because its older file survives.
    """
    from order_engine.mapper import map_salla_order
    source, workflow = source or {}, workflow or {}
    owner, number = workflow.get("user_id"), workflow.get("order_number")
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    items = raw.get("items")
    if (not owner or not number or source.get("user_id") != owner
            or source.get("order_number") != number or not raw.get("id")
            or str(raw.get("reference_id") or "").strip() != number
            or not isinstance(items, list) or not items or len(items) > 10000):
        return {}
    try:
        mapped = map_salla_order(raw)
        if len(mapped.items) != len(items):
            return {}
        units = {}
        for item, canonical in zip(items, mapped.items):
            identity = str(canonical.order_item_id or "").strip()
            quantity = Decimal(str(item.get("quantity")))
            if (not identity or identity in units or not quantity.is_finite()
                    or quantity <= 0 or quantity > 10000 or quantity != quantity.to_integral_value()):
                return {}
            units[identity] = int(quantity)
        return units if sum(units.values()) <= 10000 else {}
    except (InvalidOperation, ValueError, TypeError, KeyError, AttributeError):
        return {}


def supplier_current_piece_blocker(source, workflow, piece, *, current_units=None):
    if piece.get("virtual_kind") or piece.get("supplier_dispatch_status") != "received":
        return None
    workflow = workflow or {}
    if (piece.get("user_id") != workflow.get("user_id")
            or piece.get("order_number") != workflow.get("order_number")):
        return "assembly_order_not_ready"
    if current_units is None:
        current_units = supplier_current_units(source, workflow)
    try:
        unit = Decimal(str(piece.get("unit_index")))
        if (not unit.is_finite() or unit != unit.to_integral_value() or unit < 1
                or unit > current_units.get(str(piece.get("order_item_id") or "").strip(), 0)):
            return "assembly_order_not_ready"
    except (InvalidOperation, ValueError, TypeError):
        return "assembly_order_not_ready"
    return None


def supplier_status(source):
    """Read coherent provider/root status without local component barriers."""
    source = source or {}
    snapshot = source.get("g47_salla_snapshot") or {}
    return canonical_status({**source, "g47_salla_snapshot": {
        **snapshot, "component_pending": False, "requires_authoritative_refresh": False,
    }})


def supplier_piece_received(piece, *, preparation_optional=False):
    """Actual supplier custody, complete services and configured handoff."""
    from preparation_piece_operations import (
        _preparation_incomplete_services, _verified_supplier_employee_custody,
    )
    if (not isinstance(piece, dict) or piece.get("virtual_kind")
            or piece.get("experiment_archived_at") or piece.get("active_hold_id")
            or piece.get("status") not in {"received", "ready_for_assembly", "ready_for_employee_receipt"}
            or piece.get("supplier_dispatch_status") != "received"
            or not piece.get("received_at") or _preparation_incomplete_services(piece)):
        return False
    return bool(preparation_optional or _verified_supplier_employee_custody(piece))


def supplier_receipt_blocker(piece, *, preparation_optional=False):
    """A normal legacy path must not hide incomplete supplier receipt facts."""
    if piece.get("virtual_kind") or piece.get("supplier_dispatch_status") != "received":
        return None
    from preparation_piece_operations import _preparation_incomplete_services, _verified_supplier_employee_custody
    if not piece.get("received_at"):
        return "assembly_piece_supplier_receipt_required"
    if _preparation_incomplete_services(piece):
        return "preparation_piece_services_incomplete"
    if (piece.get("status") == "ready_for_assembly" and not preparation_optional
            and not _verified_supplier_employee_custody(piece)):
        return "assembly_piece_preparation_receipt_required"
    return None


def supplier_ready_eligible(source, workflow, piece, *, preparation_optional=False, current_order=None,
                            current_units=None):
    """The same supplier Ready decision for the card and owner transaction."""
    from preparation_piece_operations import _assembly_in_progress
    from review_local_policy import LOCAL_COMPLETION_MODE, is_known_review_mode
    from product_fulfillment_rules import payment_is_eligible
    workflow = workflow or {}
    if (workflow.get("stage") not in {"reviewed", "in_progress", "ready_to_ship", "completed"}
            or not is_known_review_mode(workflow.get("completion_mode"))
            or supplier_status(source) != "in_progress"
            or (current_order is not None and not _assembly_in_progress(current_order))):
        return False
    if (workflow.get("completion_mode") == LOCAL_COMPLETION_MODE
            and (current_order is None or not payment_is_eligible(getattr(current_order, "payment", None)))):
        return False
    return (supplier_piece_received(piece, preparation_optional=preparation_optional)
            and supplier_current_piece_blocker(source, workflow, piece, current_units=current_units) is None)


def supplier_completion_covered(source, pieces, *, owner, number, proven_bundle_units=()):
    """All current products and operational work are actually Ready.

    Coverage is proved against the current order, not an old component plan or
    approved raw-source hash. Mixed direct pieces retain their ordinary Ready
    path and must already have completed it before this proof can succeed.
    Only the read-only bundle wrapper supplies separately proven parent units;
    they contribute coverage without creating a physical or Ready piece.
    """
    from assembly_print_completion import _covers_current_products
    raw = ((source or {}).get("raw_by_source") or {}).get("salla_direct") or {}
    if (not raw.get("id") or str(raw.get("reference_id") or "").strip() != number
            or not pieces or len(pieces) + len(proven_bundle_units) > 10000
            or not any(piece.get("assembly_ready_basis") == POLICY for piece in pieces)
            or not _covers_current_products(raw, [*pieces, *(
                {"order_item_id": line, "unit_index": unit}
                for line, unit in proven_bundle_units
            )])):
        return False
    for piece in pieces:
        if (piece.get("assembly_status") != "ready" or piece.get("active_hold_id")
                or piece.get("experiment_archived_at")
                or str(piece.get("order_number") or "").strip() != number):
            return False
        if piece.get("virtual_kind") in {"direct_assembly", "operational"}:
            continue
        if (piece.get("user_id") != owner
                or not supplier_piece_received(piece, preparation_optional=(
                    piece.get("assembly_preparation_receipt_optional") is True))):
            return False
    return True


def _bundle_leaves_match(snapshot, unit):
    from bundle_fulfillment_v2 import leaf_key, ready
    from stock_component_consumption_service import quantity_decimal
    expected, actual = snapshot.get("leaves"), unit.get("bundle_leaves")
    if (not isinstance(expected, list) or not expected or not isinstance(actual, list)
            or not actual or len(expected) != len(actual)
            or any(not isinstance(leaf, dict) for leaf in [*expected, *actual])):
        return False
    frozen = {leaf_key(leaf): leaf for leaf in expected}
    keys = [leaf_key(leaf) for leaf in actual]
    if len(frozen) != len(expected) or len(set(keys)) != len(keys) or set(keys) != set(frozen):
        return False
    for leaf, key in zip(actual, keys):
        original = frozen[key]
        if (any(leaf.get(field) != original.get(field) for field in (
                "kind", "product_id", "resource_id", "variant_id", "customization"))
                or leaf.get("required", True) != original.get("required", True)
                or quantity_decimal(leaf.get("quantity"), positive=True)
                != quantity_decimal(original.get("quantity"), positive=True)):
            return False
    return ready(actual)


async def supplier_completion_covered_current(db, source, workflow, pieces, *, owner, number):
    """Retain ordinary bundle proof only for known non-piece bundle parents.

    Exact supplier/direct coverage never reads components. A missing supplier
    unit cannot use the bundle fallback merely because another bundle is ready.
    This function and all collaborators below are read-only.
    """
    if supplier_completion_covered(source, pieces, owner=owner, number=number):
        return True
    declared = {row.get("order_item_id") for row in
                ((workflow or {}).get("fulfillment_decision") or {}).get("lines") or []
                if isinstance(row, dict) and row.get("bundle") is True
                and isinstance(row.get("order_item_id"), str)}
    if not declared:
        return False
    quantities = supplier_current_units(source, workflow)
    represented = {row.get("order_item_id") for row in pieces if row.get("virtual_kind") != "operational"}
    bundle_ids = declared.intersection(quantities) - represented
    proven = {(line, index) for line in bundle_ids for index in range(1, quantities[line] + 1)}
    if not proven or not supplier_completion_covered(
        source, pieces, owner=owner, number=number, proven_bundle_units=proven,
    ):
        return False

    from fastapi import HTTPException
    from bundle_fulfillment_v2 import routing_lines
    from fulfillment_v2_routes import assert_component_execution, _component_order_lines
    from order_engine.mapper import map_salla_order
    from stock_component_consumption_service import PLANS, UNITS, _lines, _whole
    try:
        plan = await db[PLANS].find_one({"user_id": owner, "order_id": number})
        if (not plan or not plan.get("_id") or plan.get("state") != "accepted"
                or plan.get("reconciliation_required")):
            return False
        # Supplier receipt does not authorize unrelated bundle stock or leaves.
        # Keep the ordinary accepted source/component barrier for these units.
        await assert_component_execution(db, user_id=owner, order_number=number, plan=plan)
        order = map_salla_order(source["raw_by_source"]["salla_direct"])
        current = {line["order_line_id"]: line for line in _lines([
            line for line in _component_order_lines(order, {}) if line["order_line_id"] in bundle_ids
        ])}
        snapshots = plan.get("bundle_snapshots") or {}
        for line in bundle_ids:
            frozen = [row for row in plan.get("lines") or [] if row.get("order_line_id") == line]
            snapshot = snapshots.get(line) or {}
            if (len(frozen) != 1 or frozen[0] != current.get(line)
                    or not snapshot.get("product_id") or snapshot.get("revision") is None
                    or snapshot.get("operational_blocked") or snapshot.get("needs_selection")
                    or snapshot.get("unresolved_components")):
                return False
        units = await db[UNITS].find({
            "user_id": owner, "plan_id": plan["_id"], "order_line_id": {"$in": sorted(bundle_ids)},
            "state": {"$ne": "released"},
        }).to_list(10001)
        if (len(units) > 10000 or any(not isinstance(row.get("bundle_leaves"), list) for row in units)
                or sum(len(row["bundle_leaves"]) for row in units) > 10000):
            return False
        seen = set()
        for unit in units:
            line = unit.get("order_line_id")
            identity = (line, _whole(unit.get("unit_index")))
            snapshot, current_line = snapshots[line], current[line]
            if (identity not in proven or identity in seen
                    or unit.get("order_id") != number or unit.get("state") not in {"reserved", "consumed"}
                    or unit.get("product_id") != snapshot["product_id"]
                    or unit.get("variant_id") != current_line["variant_id"]
                    or unit.get("selected_context") != current_line["selected_context"]
                    or unit.get("bundle_revision") != snapshot["revision"]
                    or not _bundle_leaves_match(snapshot, unit)):
                return False
            seen.add(identity)
        if seen != proven:
            return False
        routed = await routing_lines(db, owner, order)
        return all((routed.get(line) or {}).get("ready") is True
                   and routed[line].get("quantity") == quantities[line]
                   and routed[line].get("product_id") == snapshots[line]["product_id"]
                   for line in bundle_ids)
    except HTTPException as exc:
        reason = exc.detail.get("code") if isinstance(exc.detail, dict) else None
        if exc.status_code == 409 and reason in {
            "component_configuration_required", "component_canonical_order_required",
            "component_source_created_at_required", "component_execution_blocked",
            "component_order_line_identity_required", "component_line_identity_invalid",
            "component_selected_context_invalid", "component_identity_payload_invalid",
            "component_quantity_invalid", "component_unit_quantity_invalid",
            "component_unit_limit_exceeded", "component_provenance_invalid",
            "component_safe_scan_limit_exceeded", "bundle_components_incomplete",
        }:
            return False
        raise


async def completed_supplier_policy(db, owner, number, *, workflow=None, source=None):
    """Revalidate a server-recorded completion before shipping or its display."""
    from preparation_piece_operations import PIECES, _workflow_assembly_pieces
    if workflow is None:
        workflow = await db["order_review_workflows"].find_one({"user_id": owner, "order_number": number})
    if (not workflow or workflow.get("user_id") != owner or workflow.get("order_number") != number
            or workflow.get("assembly_completion_basis") != POLICY
            or workflow.get("assembly_status") != "completed"
            or workflow.get("stage") not in {"completed", "delivering", "delivered"}):
        return False
    if source is None:
        source = await db["unified_orders"].find_one({"user_id": owner, "order_number": number})
    if (not source or source.get("user_id") != owner or source.get("order_number") != number
            or supplier_status(source) not in {"in_progress", "completed"}):
        return False
    pieces = await db[PIECES].find({
        "user_id": owner, "order_number": number, "status": {"$ne": "cancelled"},
        "$or": [{"experiment_archived_at": {"$exists": False}}, {"experiment_archived_at": None}],
    }, {"image_b64": 0}).to_list(10001)
    pieces.extend(_workflow_assembly_pieces(workflow, order_number=number))
    return await supplier_completion_covered_current(db, source, workflow, pieces, owner=owner, number=number)
