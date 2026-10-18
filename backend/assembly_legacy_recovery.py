"""Read-only evidence checks for explicit recovery of legacy completion.

The accepted component plan proves original product/unit/option inputs, not the
entire historical order. Existing shipment admission remains downstream. The caller
must freeze source and shipping evidence across subsequent provider I/O; this
helper cannot prove an address was unchanged before recovery was requested.
Run inside the caller's operational_owner transaction. Never reserve or consume.
"""
from fastapi import HTTPException

from assembly_status_policy import canonical_status, component_refresh_pending
from order_engine.shipping_label_service import ShippingLabelError


def _reject(code):
    raise ShippingLabelError(code, "تعذر إثبات اكتمال التجهيز الحالي؛ يلزم التحقق.")


async def validate_recovery(scoped, owner, number, workflow):
    from fulfillment_v2_routes import _component_order_lines, assert_component_execution
    from preparation_piece_operations import (
        PIECES, PIECE_STATUS_CANCELLED, _assert_ready_piece_components,
        _current_assembly_order, _workflow_assembly_pieces,
        _piece_has_completed_preparation_receipt,
    )
    from stock_component_consumption_service import PLANS, UNITS, _digest, _lines, _whole

    if (workflow.get("user_id") != owner or workflow.get("order_number") != number
            or workflow.get("stage") != "completed"
            or workflow.get("assembly_status") != "completed"):
        _reject("assembly_completion_required")
    source = await scoped["unified_orders"].find_one({"user_id": owner, "order_number": number})
    if not source or canonical_status(source) not in {"in_progress", "completed"}:
        _reject("assembly_canonical_status_blocked")
    if component_refresh_pending(source):
        _reject("completion_source_refresh_pending")
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    if not str(raw.get("id") or "").strip() or str(raw.get("reference_id") or "") != number:
        _reject("salla_order_reference_mismatch")
    order = await _current_assembly_order(scoped, user_id=owner, order_number=number)
    # Do not introduce new payment/address admission rules for pieces approved
    # by historical Ready paths. Current shipping/printing keeps its own guards.
    if order is None or str(order.order_number) != number:
        _reject("assembly_order_not_ready")
    plan = await scoped[PLANS].find_one({"user_id": owner, "order_id": number})
    if not plan or not plan.get("input_hash") or not plan.get("lines") or not plan.get("_id"):
        _reject("completion_recovery_evidence_missing")
    if plan.get("state") != "accepted":
        _reject("component_execution_blocked")
    try:
        await assert_component_execution(scoped, user_id=owner, order_number=number, plan=plan)
        frozen = {row["order_line_id"]: row for row in plan["lines"]}
        if len(frozen) != len(plan["lines"]):
            _reject("assembly_completion_evidence_changed")
        rows = _component_order_lines(order, {})
        acceptance = [
            {**row, "prebuilt_receipts": frozen[row["order_line_id"]].get("prebuilt_receipts") or []}
            if row["order_line_id"] in frozen else row for row in rows
        ]
        normalized = _lines(acceptance)
        digest = _digest({"lines": normalized,
                          "warehouse_ids": sorted(set(plan.get("warehouse_ids") or []))})
        if digest != plan["input_hash"] or normalized != plan["lines"]:
            _reject("assembly_completion_evidence_changed")
        expected = {(row["order_line_id"], index) for row in normalized
                    for index in range(1, row["quantity"] + 1)}
        physical = await scoped[PIECES].find({
            "user_id": owner, "order_number": number,
            "status": {"$ne": PIECE_STATUS_CANCELLED},
            "$or": [{"experiment_archived_at": {"$exists": False}}, {"experiment_archived_at": None}],
        }, {"_id": 0, "image_b64": 0}).to_list(10001)
        pieces = physical + _workflow_assembly_pieces(workflow, order_number=number)
        if not expected or len(physical) > 10000 or not pieces:
            _reject("completion_recovery_evidence_missing")
        coverage, identities = set(), set()
        for piece in pieces:
            identity = str(piece.get("piece_id") or piece.get("id") or "")
            if not identity or identity in identities or piece.get("assembly_status") != "ready":
                _reject("assembly_completion_evidence_changed")
            identities.add(identity)
            if piece.get("virtual_kind") == "operational":
                continue
            if not piece.get("virtual_kind") and (
                    not _piece_has_completed_preparation_receipt(piece)
                    or piece.get("active_hold_id")
                    or str(piece.get("supplier_dispatch_status") or "") not in {"", "received"}):
                _reject("assembly_completion_evidence_changed")
            key = (str(piece.get("order_item_id") or ""), _whole(piece.get("unit_index")))
            if key not in expected or key in coverage:
                _reject("assembly_completion_evidence_changed")
            coverage.add(key)
            await _assert_ready_piece_components(scoped, user_id=owner, piece=piece)
        units = await scoped[UNITS].find({"user_id": owner, "plan_id": plan["_id"]}).to_list(10001)
        unit_keys = [(str(unit.get("order_line_id") or ""), _whole(unit.get("unit_index"))) for unit in units]
        if (coverage != expected or len(unit_keys) != len(expected) or set(unit_keys) != expected
                or any(unit.get("state") != "consumed" or unit.get("order_id") != number for unit in units)):
            _reject("component_ready_state_requires_reconciliation")
    except HTTPException:
        _reject("component_execution_blocked")
    except (KeyError, TypeError, ValueError, OverflowError):
        _reject("completion_recovery_evidence_missing")
    return source
