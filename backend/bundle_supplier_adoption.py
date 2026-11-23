"""Explicit, pre-receipt adoption of an ordinary line into supplier bundle leaves.

The original sale, ordinary intake and dispatch remain historical evidence.
Only an owner-reviewed preview can replace the unreceived execution identity.
No invoice, receipt, inventory or accounting record is written by this module.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from operational_atomic import operational_owner

ADOPTIONS = "mezan_bundle_supplier_adoptions_v1"
PROFILE = "bundle_supplier_adoption"
PLANS = "mezan_component_consumption_plans_v1"
UNITS = "mezan_component_consumption_units_v1"
LIFECYCLES = "mezan_component_order_lifecycle_v1"
PIECES = "mezan_preparation_pieces_v1"
DISPATCHES = "mezan_supplier_dispatches_v1"
PIECE_EVENTS = "mezan_preparation_piece_events_v1"
DISPATCH_EVENTS = "mezan_supplier_dispatch_events_v1"
ORDER_EVENTS = "order_review_events"
PRODUCTS = "mezan_products_v2"
COSTS = "mezan_product_cost_profiles_v2"
DEFINITIONS = "mezan_bundle_definitions_v2"
COST_REVISIONS = "mezan_product_cost_revisions_v2"
OPERATION_PROFILES = "mezan_product_operation_profiles_v2"
RESOURCE_BINDINGS = "mezan_product_resource_bindings_v2"
OPTION_BINDINGS = "mezan_product_option_cost_bindings_v2"
RESOURCES = "mezan_cost_resources_v2"
SUPPLIERS = "mezan_suppliers_v2"
WORKFLOWS = "order_review_workflows"
SOURCES = "unified_orders"
FENCE_FIELD = "bundle_adoption_fence"
REFERENCE = "bundle_supplier_reference"
ADOPTION_WRITE_COLLECTIONS = frozenset({ADOPTIONS, PLANS, UNITS, PIECES,
    DISPATCHES, PIECE_EVENTS, DISPATCH_EVENTS, ORDER_EVENTS, PRODUCTS, COSTS,
    DEFINITIONS, COST_REVISIONS, OPERATION_PROFILES, RESOURCE_BINDINGS, OPTION_BINDINGS,
    RESOURCES, SUPPLIERS, WORKFLOWS, SOURCES, LIFECYCLES, "users"})
FENCE_COLLECTIONS = frozenset({PRODUCTS, COSTS, DEFINITIONS, COST_REVISIONS,
    OPERATION_PROFILES, RESOURCE_BINDINGS, OPTION_BINDINGS, RESOURCES,
    SUPPLIERS, WORKFLOWS, SOURCES, LIFECYCLES, DISPATCHES})
MAX_UNITS = 100

_TEMPLATE_FIELDS = frozenset({"batch_id", "file_number", "file_title", "product_name", "sku",
    "image_url", "selected_image_url", "resolved_image_url", "shipping_company",
    "preparation_note", "responsible_employee_id", "responsible_employee_name",
    "supplier_name", "assigned_at", "assignment_date", "started_at", "schedule_mode",
    "estimated_duration_minutes", "estimated_due_at", "required_due_at"})
_PIECE_FIELDS = _TEMPLATE_FIELDS | frozenset({"_id", "id", "piece_id", "barcode", "user_id",
    "order_number", "order_item_id", "unit_index", "quantity", "product_id", "variant_id",
    "salla_variant_id", "supplier_id", "services", "service_count", "completed_service_count",
    "remaining_service_count", "service_plan_status", "assembly_status", "options_raw",
    "selected_options", "options", "custom_fields", "service_specifications_snapshot",
    "specifications_snapshot", "product_options_snapshot", "bundle_supplier_specifications",
    "bundle_parent_selected_options", REFERENCE, "bundle_supplier_adoption_id", "original_piece_id",
    "supplier_dispatch_id", "supplier_dispatch_status", "supplier_file_number", "sent_to_supplier_at",
    "sent_to_supplier_by_id", "sent_to_supplier_by_name", "status", "execution_status", "created_at",
    "updated_at", "total_products_in_order", "mezan_only", "salla_updated", "qoyod_updated"})
_EVENT_FIELDS = frozenset({"_id", "id", "user_id", "order_number", "order_item_id", "piece_id",
    "event_type", "adoption_id", "actor_id", "reason", "occurred_at", "previous_piece_ids",
    "piece_ids", "previous_dispatch_ids", "dispatch_ids", "mezan_only", "salla_updated", "qoyod_updated"})
_DISPATCH_FIELDS = frozenset({"_id", "id", "user_id", "client_request_id", "status", "file_number",
    "supplier_file_number", "source_file_numbers", "source_files", "batch_id", "supplier_id",
    "supplier_name", "sent_by_id", "sent_by_name", "responsible_employee_id", "responsible_employee_name",
    "performed_by_id", "piece_ids", "piece_count", "lines", "note", "created_at", "sent_at",
    "mezan_only", "salla_updated", "qoyod_updated", "bundle_supplier_adoption_id",
    "reconciles_dispatch_id", "reconciles_piece_ids", "reconciliation_kind"})
_ADOPTION_FIELDS = frozenset({"_id", "id", "user_id", "order_number", "order_line_id", "actor_id",
    "client_request_id", "command_hash", "preview_hash", "reason", "state", "created_at",
    "schema", "financial_effect", "preview", "before", "after", "seal"})
_SET_FIELDS = {
    PLANS: frozenset({"bundle_snapshots", "bundle_supplier_adoptions", "updated_at"}),
    UNITS: frozenset({"bundle_leaves", "bundle_revision", "bundle_supplier_adoption_id",
                      "bundle_supplier_bridge_version", "updated_at"}),
    PIECES: frozenset({"status", "execution_status", "supplier_dispatch_status", "bundle_retired_at",
                      "bundle_supplier_retired", "bundle_supplier_adoption_id", "updated_at"}),
}


class AdoptionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    order_line_id: str = Field(min_length=1, max_length=300)


class AdoptionApply(AdoptionPreview):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    client_request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    reason: str = Field(min_length=3, max_length=500)


def _fail(code, status=409):
    raise HTTPException(status, detail={"code": "bundle_adoption_" + code})


def _text(value):
    return str(value or "").strip()


def _digest(value):
    # Mongo stores dates at millisecond precision and may return naive UTC.
    # A committed audit must retain the same seal after that round trip.
    def canonical(item):
        if isinstance(item, datetime):
            return item.replace(tzinfo=item.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat(timespec="milliseconds")
        if isinstance(item, dict):
            return {key: canonical(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [canonical(child) for child in item]
        return item
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode()).hexdigest()


def _without_fence(value):
    if isinstance(value, dict):
        return {key: _without_fence(item) for key, item in value.items() if key != FENCE_FIELD}
    if isinstance(value, list):
        return [_without_fence(item) for item in value]
    return value


def _aid(owner, request):
    return "bsa_" + uuid.uuid5(uuid.NAMESPACE_URL, "bundle-adoption:" + owner + ":" + request).hex


async def _bounded(db, collection, query, limit=MAX_UNITS):
    rows = await db[collection].find(query).to_list(limit + 1)
    if len(rows) > limit:
        _fail("evidence_limit")
    return rows


def _receipt_evidence(piece):
    evidence = ("received_at", "completed_at", "supplier_receiving_session_id", "supplier_receiving_history",
        "supplier_invoice_id", "supplier_receiving_reference", "preparation_received_at", "assembly_ready_at",
        "assembly_completed_at", "supplier_receipt_id", "inventory_receipt_id", "scan_lock_token")
    return (any(piece.get(key) for key in evidence)
        or piece.get("assembly_status") not in {None, "", "pending"}
        or any(service.get("status") == "completed" for service in piece.get("services") or [] if isinstance(service, dict)))


def _unreceived_piece(piece):
    if (piece.get(REFERENCE) or piece.get("bundle_supplier_retired") or piece.get("virtual_kind")
            or piece.get("status") not in {"assigned", "in_progress"}
            or piece.get("supplier_dispatch_status") not in {"sent", "ready"}
            or piece.get("services") or piece.get("active_hold_id") or piece.get("customer_service_hold_active")
            or piece.get("experiment_mode") or piece.get("experiment_run_id") or piece.get("experiment_archived_at")
            or piece.get("assembly_status") not in {None, "", "pending"}):
        return False
    return not _receipt_evidence(piece)


async def _authority(db, owner):
    from security_sensitive_routes import require_security_owner
    actor = await require_security_owner(db, {"id": owner})
    current = await db.users.find_one({"id": owner, "role": "owner"})
    if (actor["id"] != owner or not current or current.get("is_active") is False
            or current.get("disabled") is True or current.get("deleted_at")):
        _fail("owner_required", 403)
    return current


async def _read_preview(db, owner, order_number, line_id):
    """Read one bounded line. No writes, provider calls or invented intake."""
    from bundle_catalog_v2 import expand_bundle
    from bundle_fulfillment_v2 import selected_ids, option_answers, present_options, filled_options
    from bundle_supplier_pieces import prepare_supplier_leaf_unit, build_supplier_leaf_pieces
    from fulfillment_v2_routes import component_order_fingerprint, _component_order_lines
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import get_order, OrderNotFoundError
    from product_fulfillment_routes import _product_key
    from product_fulfillment_rules import order_is_active, payment_is_eligible
    from review_local_policy import is_known_review_mode
    from stock_component_consumption_service import _lines
    from supplier_identity_service import require_supplier_v2
    from supplier_receipt_ready_policy import supplier_status

    await _authority(db, owner)
    selector = {"user_id": owner, "order_number": order_number}
    source = await db[SOURCES].find_one(selector)
    workflow = await db[WORKFLOWS].find_one(selector)
    lifecycle = await db[LIFECYCLES].find_one(selector)
    if not source or not workflow or not lifecycle:
        _fail("source_evidence_required")
    if (supplier_status(source) != "in_progress" or workflow.get("stage") != "in_progress"
            or not is_known_review_mode(workflow.get("completion_mode"))
            or workflow.get("customer_service_hold_active") or workflow.get("experiment_mode")
            or lifecycle.get("accepted") is not True or lifecycle.get("retry_required")
            or lifecycle.get("state") != "reserved" or lifecycle.get("cancelled")
            or (source.get("g47_salla_snapshot") or {}).get("component_pending")
            or (source.get("g47_salla_snapshot") or {}).get("requires_authoritative_refresh")):
        _fail("source_not_eligible")
    accepted_revision = lifecycle.get("snapshot_revision")
    current_revision = (source.get("g47_salla_snapshot") or {}).get("revision")
    if type(accepted_revision) is not int or type(current_revision) is not int or accepted_revision != current_revision:
        _fail("source_acceptance_stale")
    intake = lifecycle.get("bundle_intake_snapshots") or {}
    if line_id not in intake or intake[line_id] is not None:
        _fail("ordinary_intake_required")
    try:
        order = await get_order(MongoOrderRepository(db), user_id=owner, order_number=order_number)
    except OrderNotFoundError:
        _fail("source_evidence_required")
    if (not order_is_active(order) or not payment_is_eligible(order.payment)
            or component_order_fingerprint(order) != lifecycle.get("source_fingerprint")):
        _fail("source_changed")
    lines = _component_order_lines(order, {})
    selected = [line for line in lines if line["order_line_id"] == line_id]
    if len(selected) != 1:
        _fail("line_not_found", 404)
    line = selected[0]
    normalized = _lines([line])[0]
    qty = normalized["quantity"]
    if qty < 1 or qty > MAX_UNITS:
        _fail("quantity_limit")
    review = [row for row in workflow.get("items") or [] if row.get("order_item_id") == line_id]
    if len(review) != 1 or review[0].get("review_status") != "reviewed" or review[0].get("supplier_export") is False:
        _fail("review_evidence_required")
    plan = await db[PLANS].find_one({"user_id": owner, "order_id": order_number})
    if (not plan or plan.get("state") != "accepted" or plan.get("reconciliation_required")
            or (plan.get("source_version") or {}).get("value") != lifecycle.get("generation")
            or [row for row in plan.get("lines", []) if row.get("order_line_id") == line_id] != [normalized]
            or (plan.get("bundle_snapshots") or {}).get(line_id)
            or (plan.get("bundle_supplier_adoptions") or {}).get(line_id)):
        _fail("ordinary_plan_required")
    units = await _bounded(db, UNITS, {"user_id": owner, "plan_id": plan["_id"], "order_line_id": line_id})
    units.sort(key=lambda row: row.get("unit_index", 0))
    if (len(units) != qty or [row.get("unit_index") for row in units] != list(range(1, qty + 1))
            or any(row.get("state") != "reserved" or row.get("bundle_leaves") or row.get("allocations")
                or row.get("resource_demands") or row.get("prebuilt") or row.get("consumed_at")
                or row.get("actual_cost") is not None for row in units)):
        _fail("unconsumed_empty_units_required")
    all_pieces = await _bounded(db, PIECES, {**selector, "order_item_id": line_id})
    pieces = [piece for piece in all_pieces if piece.get("status") != "cancelled" and not piece.get("experiment_archived_at")]
    pieces.sort(key=lambda row: row.get("unit_index", 0))
    if (len(pieces) != qty or [row.get("unit_index") for row in pieces] != list(range(1, qty + 1))
            or not all(_unreceived_piece(piece) for piece in pieces)):
        _fail("unreceived_dispatched_pieces_required")
    # Even retired aliases cannot conceal a previous invoice or draft scan.
    ids = [_text(piece.get("piece_id")) for piece in all_pieces]
    if not all(ids) or len(set(ids)) != len(ids):
        _fail("piece_identity_ambiguous")
    if any(_receipt_evidence(piece) for piece in all_pieces):
        _fail("receipt_or_scan_exists")
    if await db["mezan_supplier_receiving_events_v1"].find_one({"user_id": owner, "$or": [
            {"piece_id": {"$in": ids}}, {"order_number": order_number, "order_item_id": line_id}],
            "event_type": {"$in": ["supplier_piece_scanned", "supplier_piece_service_recorded"]}}):
        _fail("receipt_or_scan_exists")
    if await db["mezan_supplier_invoices_v2"].find_one({"user_id": owner, "lines.piece_ids": {"$in": ids}}):
        _fail("invoice_exists")
    if await db["mezan_inventory_receipts_v2"].find_one({"user_id": owner, "$or": [
            {"order_number": order_number, "order_item_id": line_id},
            {"order_id": order_number, "order_line_id": line_id}]}):
        _fail("receipt_or_scan_exists")
    suppliers = {_text(piece.get("supplier_id")) for piece in pieces}
    if len(suppliers) != 1 or "" in suppliers:
        _fail("supplier_evidence_required")
    supplier_id = next(iter(suppliers))
    supplier = await require_supplier_v2(db, owner, supplier_id)
    supplier_doc = await db[SUPPLIERS].find_one({"user_id": owner, "id": supplier_id})
    dispatches, accounts = {}, {}
    for piece in pieces:
        did = _text(piece.get("supplier_dispatch_id"))
        dispatch = await db[DISPATCHES].find_one({"user_id": owner, "id": did})
        employee = _text(piece.get("responsible_employee_id"))
        account = await db.users.find_one({"id": employee})
        if (not did or not dispatch or dispatch.get("status") not in {"sent", "ready"}
                or dispatch.get("supplier_id") != supplier_id or dispatch.get("sent_by_id") != employee
                or piece["piece_id"] not in (dispatch.get("piece_ids") or [])
                or not account or account.get("is_active") is False or account.get("disabled") is True
                or account.get("deleted_at")
                or not (employee == owner or account.get("created_by") == owner)):
            _fail("dispatch_custody_required")
        dispatches[did], accounts[employee] = dispatch, account
    product = await db[PRODUCTS].find_one({"user_id": owner, "$or": [
        {key: normalized["product_id"]} for key in ("id", "mezan_product_id", "salla_product_id")]})
    if not product:
        _fail("catalog_evidence_required")
    key = _product_key(product)
    profile = await db[COSTS].find_one({"user_id": owner, "salla_product_id": key})
    definition = await db[DEFINITIONS].find_one({"user_id": owner, "product_id": key})
    cost_revision = await db[COST_REVISIONS].find_one({"_id": "product-cost:" + owner, "user_id": owner})
    if not profile or not definition or not cost_revision or definition.get("product_type") != "composite":
        _fail("catalog_evidence_required")
    snapshot = await expand_bundle(db, owner, key, variant_id=normalized["variant_id"],
        selected_options=selected_ids(line), option_answers=option_answers(line),
        present_option_ids=present_options(line), filled_option_ids=filled_options(line))
    if not snapshot or not snapshot.get("cost_complete") or snapshot.get("missing_options"):
        _fail("bundle_incomplete")
    if (set(snapshot.get("frozen_definitions") or {}) != {key}
            or snapshot.get("component_decisions") or snapshot.get("unresolved_components")):
        _fail("finished_same_product_required")
    # Every existing metadata document consumed by expansion gets a write
    # fence. The required global cost revision binds insertion/removal of cost
    # metadata; bundle/profile policy writers also use the owner transaction.
    related = {}
    for collection in (OPERATION_PROFILES, RESOURCE_BINDINGS, OPTION_BINDINGS):
        related[collection] = await _bounded(db, collection, {"user_id": owner, "salla_product_id": key})
    metadata = snapshot["frozen_definitions"][key].get("catalog_metadata") or {}
    resource_ids = sorted((metadata.get("resources") or {}).keys())
    related[RESOURCES] = await _bounded(db, RESOURCES, {"user_id": owner, "id": {"$in": resource_ids}}) if resource_ids else []
    if len(related[OPERATION_PROFILES]) > 1 or len(related[RESOURCES]) != len(resource_ids):
        _fail("catalog_evidence_ambiguous")
    new_units, materialized = [], []
    for unit, piece in zip(units, pieces):
        prepared = prepare_supplier_leaf_unit({**unit, "bundle_leaves": deepcopy(snapshot["leaves"]),
            "bundle_revision": snapshot["revision"], "product_id": snapshot["product_id"]})
        built = build_supplier_leaf_pieces(owner=owner, unit=prepared, template={
            **{field: deepcopy(piece[field]) for field in _TEMPLATE_FIELDS if field in piece},
            "supplier_id": supplier_id, "status": "in_progress", "supplier_dispatch_status": "sent"})
        new_units.append(prepared)
        materialized.extend(built)
    # No side effects from checking for a prior failed/foreign materialization.
    if await db[PIECES].find_one({"user_id": owner, "piece_id": {"$in": [row["piece_id"] for row in materialized]}}):
        _fail("leaf_piece_exists")
    context = {"source": source, "workflow": workflow, "lifecycle": lifecycle, "plan": plan,
        "units": units, "pieces": pieces, "dispatches": dispatches, "accounts": accounts,
        "product": product, "profile": profile, "definition": definition, "cost_revision": cost_revision,
        "supplier": supplier_doc, "snapshot": snapshot, "catalog_related": related}
    preview_hash = _digest(_without_fence(context))
    leaves = {leaf["leaf_id"]: leaf for leaf in snapshot["leaves"]}
    requirements = [{"piece_id": row["piece_id"], "leaf_id": row[REFERENCE]["leaf_id"],
        "leaf_unit_index": row[REFERENCE]["leaf_unit_index"], "unit_index": row["unit_index"],
        "product_name": row.get("product_name") or product.get("name"),
        "specifications": deepcopy(row["bundle_supplier_specifications"]),
        "unit_cost": str(leaves[row[REFERENCE]["leaf_id"]]["unit_cost"])} for row in materialized]
    preview = {"schema": 1, "state": "preview", "order_number": order_number, "order_line_id": line_id,
        "preview_hash": preview_hash, "commercial_quantity": qty, "physical_quantity": len(materialized),
        "previous_piece_ids": [row["piece_id"] for row in pieces],
        "supplier": {"id": supplier_id, "name": supplier["name"]},
        "expected_total_cost": str(sum((Decimal(row["unit_cost"]) for row in requirements), Decimal(0))),
        "requirements": requirements, "source_revision": (source.get("g47_salla_snapshot") or {}).get("revision"),
        "catalog_revision": snapshot["revision"]}
    return preview, {**context, "prepared_units": new_units}


async def preview_adoption(db, owner, order_number, line_id):
    return (await _read_preview(db, owner, _text(order_number), _text(line_id)))[0]


def _response(row, duplicate=False):
    return {"schema": 1, "state": "applied", "adoption_id": row["id"], "duplicate": duplicate,
        "preview": deepcopy(row["preview"]), "piece_ids": row["after"]["piece_ids"]}


async def _fence(db, name, row, owner):
    if name == "users":
        query = {"id": row["id"], **({"role": "owner"} if row["id"] == owner else {"created_by": owner})}
    else:
        if "_id" not in row:
            _fail("fence_evidence_required")
        query = {"_id": row["_id"], "user_id": owner}
    changed = await db[name].update_one(query, {"$inc": {FENCE_FIELD: 1}})
    if changed.matched_count != 1:
        _fail("source_changed")


async def apply_adoption(db, owner, order_number, command):
    from bundle_supplier_pieces import build_supplier_leaf_pieces, validate_supplier_leaf_piece
    from preparation_supplier_dispatch import supplier_dispatch_cards, supplier_dispatch_lines
    order_number = _text(order_number)
    command = command.model_dump() if isinstance(command, AdoptionApply) else AdoptionApply.model_validate(command).model_dump()
    command_hash = _digest({"order_number": order_number, **command})
    adoption_id = _aid(owner, command["client_request_id"])

    async def apply(tx):
        actor = await _authority(tx, owner)
        previous = await tx[ADOPTIONS].find_one({"_id": adoption_id, "user_id": owner})
        if previous:
            if previous.get("command_hash") != command_hash:
                _fail("request_conflict")
            if previous.get("state") != "applied" or previous.get("seal") != _digest({k:v for k,v in previous.items() if k not in {"_id", "seal"}}):
                _fail("audit_invalid")
            return _response(previous, True)
        preview, evidence = await _read_preview(tx, owner, order_number, command["order_line_id"])
        if preview["preview_hash"] != command["preview_hash"]:
            _fail("preview_changed")
        for collection, key in ((SOURCES,"source"), (WORKFLOWS,"workflow"), (LIFECYCLES,"lifecycle"),
                (PRODUCTS,"product"), (COSTS,"profile"), (DEFINITIONS,"definition"),
                (COST_REVISIONS,"cost_revision"), (SUPPLIERS,"supplier")):
            await _fence(tx, collection, evidence[key], owner)
        for dispatch in evidence["dispatches"].values():
            await _fence(tx, DISPATCHES, dispatch, owner)
        for collection, documents in evidence["catalog_related"].items():
            for document in documents:
                await _fence(tx, collection, document, owner)
        accounts = {**evidence["accounts"], owner: actor}
        for account in accounts.values():
            await _fence(tx, "users", account, owner)
        now = datetime.now(timezone.utc)
        new_pieces, new_dispatches, updated_units = [], {}, []
        qty_delta = preview["physical_quantity"] - preview["commercial_quantity"]
        for unit, old in zip(evidence["prepared_units"], evidence["pieces"]):
            unit = {**unit, "bundle_supplier_adoption_id": adoption_id}
            did = "sdb_" + uuid.uuid5(uuid.NAMESPACE_URL, adoption_id + ":" + old["supplier_dispatch_id"]).hex
            old_dispatch = evidence["dispatches"][old["supplier_dispatch_id"]]
            file_number = _text(old_dispatch.get("supplier_file_number") or old.get("file_number")) + "-B" + adoption_id[-6:]
            template = {field: deepcopy(old[field]) for field in _TEMPLATE_FIELDS if field in old}
            template.update(supplier_id=preview["supplier"]["id"], supplier_name=preview["supplier"]["name"],
                status="in_progress", execution_status="sent_to_supplier", supplier_dispatch_status="sent",
                supplier_dispatch_id=did, supplier_file_number=file_number,
                bundle_supplier_adoption_id=adoption_id, original_piece_id=old["piece_id"],
                total_products_in_order=max(1, int(old.get("total_products_in_order") or preview["commercial_quantity"]) + qty_delta),
                created_at=now, updated_at=now, sent_to_supplier_at=now,
                sent_to_supplier_by_id=old.get("responsible_employee_id"),
                sent_to_supplier_by_name=old.get("responsible_employee_name"),
                mezan_only=True, salla_updated=False, qoyod_updated=False)
            built = build_supplier_leaf_pieces(owner=owner, unit=unit, template=template)
            new_pieces.extend(built)
            updated_units.append(unit)
            dispatch = new_dispatches.setdefault(did, {"id": did, "user_id": owner,
                "client_request_id": adoption_id + ":" + old["supplier_dispatch_id"], "status": "sent",
                "file_number": file_number, "supplier_file_number": file_number,
                "source_file_numbers": [], "source_files": [], "batch_id": old.get("batch_id"),
                "supplier_id": preview["supplier"]["id"], "supplier_name": preview["supplier"]["name"],
                "sent_by_id": old["responsible_employee_id"], "sent_by_name": old.get("responsible_employee_name"),
                "responsible_employee_id": old["responsible_employee_id"],
                "responsible_employee_name": old.get("responsible_employee_name"), "performed_by_id": owner,
                "piece_ids": [], "piece_count": 0, "lines": [], "note": command["reason"],
                "created_at": now, "sent_at": now, "mezan_only": True, "salla_updated": False, "qoyod_updated": False,
                "bundle_supplier_adoption_id": adoption_id, "reconciles_dispatch_id": old["supplier_dispatch_id"],
                "reconciles_piece_ids": [], "reconciliation_kind": "ordinary_to_frozen_bundle"})
            dispatch["reconciles_piece_ids"].append(old["piece_id"])
            dispatch["piece_ids"].extend(row["piece_id"] for row in built)
        for dispatch in new_dispatches.values():
            members = [row for row in new_pieces if row["supplier_dispatch_id"] == dispatch["id"]]
            dispatch["piece_count"] = len(members)
            dispatch["lines"] = supplier_dispatch_lines(members, evidence["supplier"])
            files = sorted({_text(row.get("file_number")) for row in members})
            dispatch["source_file_numbers"] = files
            dispatch["source_files"] = [{"file_number": file, "batch_id": next(row.get("batch_id") for row in members if row.get("file_number") == file),
                "piece_count": len([row for row in members if row.get("file_number") == file]),
                "cards": supplier_dispatch_cards([row for row in members if row.get("file_number") == file]),
                "lines": supplier_dispatch_lines([row for row in members if row.get("file_number") == file], evidence["supplier"])} for file in files]
        plan = evidence["plan"]
        snapshots = {**deepcopy(plan.get("bundle_snapshots") or {}), command["order_line_id"]: deepcopy(evidence["snapshot"])}
        adoptions = {**deepcopy(plan.get("bundle_supplier_adoptions") or {}), command["order_line_id"]: adoption_id}
        row = {"_id": adoption_id, "id": adoption_id, "schema": 1, "user_id": owner,
            "order_number": order_number, "order_line_id": command["order_line_id"], "actor_id": owner,
            "client_request_id": command["client_request_id"], "command_hash": command_hash,
            "preview_hash": command["preview_hash"], "reason": command["reason"], "state": "applied",
            "created_at": now, "financial_effect": "none", "preview": deepcopy(preview),
            "before": {"plan_id": plan["_id"], "source_fingerprint": evidence["lifecycle"]["source_fingerprint"],
                "intake": None, "piece_ids": preview["previous_piece_ids"],
                "dispatch_ids": sorted(evidence["dispatches"]),
                "units": [{k:deepcopy(unit.get(k)) for k in ("_id","unit_index","state","consumption_id","resource_demands","allocations")} for unit in evidence["units"]]},
            "after": {"plan_id": plan["_id"], "unit_ids": [unit["_id"] for unit in updated_units],
                "piece_ids": [piece["piece_id"] for piece in new_pieces], "dispatch_ids": sorted(new_dispatches),
                "bundle_snapshot": deepcopy(evidence["snapshot"])}}
        row["seal"] = _digest({k:v for k,v in row.items() if k != "_id"})
        await tx[ADOPTIONS].insert_one(deepcopy(row))
        await tx[PLANS].update_one({"_id": plan["_id"], "user_id": owner}, {"$set": {
            "bundle_snapshots": snapshots, "bundle_supplier_adoptions": adoptions, "updated_at": now}})
        for unit in updated_units:
            await tx[UNITS].update_one({"_id": unit["_id"], "user_id": owner}, {"$set": {
                "bundle_leaves": unit["bundle_leaves"], "bundle_revision": unit["bundle_revision"],
                "bundle_supplier_bridge_version": 1, "bundle_supplier_adoption_id": adoption_id, "updated_at": now}})
        for old in evidence["pieces"]:
            changed = await tx[PIECES].update_one({"user_id": owner, "piece_id": old["piece_id"]}, {"$set": {
                "status": "cancelled", "execution_status": "bundle_adopted", "supplier_dispatch_status": "superseded",
                "bundle_supplier_retired": True, "bundle_retired_at": now,
                "bundle_supplier_adoption_id": adoption_id, "updated_at": now}})
            if changed.matched_count != 1:
                _fail("source_changed")
        for piece in new_pieces:
            await tx[PIECES].insert_one(piece)
            await validate_supplier_leaf_piece(tx, owner, piece)
        for dispatch in new_dispatches.values():
            await tx[DISPATCHES].insert_one(dispatch)
        event = {"id": adoption_id, "user_id": owner, "order_number": order_number,
            "order_item_id": command["order_line_id"], "event_type": "supplier_bundle_adopted",
            "adoption_id": adoption_id, "actor_id": owner, "reason": command["reason"], "occurred_at": now,
            "previous_piece_ids": preview["previous_piece_ids"], "piece_ids": row["after"]["piece_ids"],
            "previous_dispatch_ids": row["before"]["dispatch_ids"], "dispatch_ids": row["after"]["dispatch_ids"],
            "mezan_only": True, "salla_updated": False, "qoyod_updated": False}
        for collection in (PIECE_EVENTS, DISPATCH_EVENTS, ORDER_EVENTS):
            await tx[collection].insert_one(deepcopy(event))
        return _response(row)
    return await operational_owner(db, owner, apply, profile=PROFILE)


def validate_adoption_insert(name, doc):
    """Capability validation; no general financial or arbitrary document writer."""
    if not isinstance(doc, dict) or not _text(doc.get("user_id")):
        return False
    if name == ADOPTIONS:
        return (set(doc) <= _ADOPTION_FIELDS and doc.get("schema") == 1 and doc.get("state") == "applied"
            and doc.get("financial_effect") == "none" and doc.get("actor_id") == doc.get("user_id")
            and doc.get("id") == doc.get("_id") == _aid(doc["user_id"], doc.get("client_request_id", ""))
            and doc.get("seal") == _digest({k:v for k,v in doc.items() if k not in {"_id", "seal"}}))
    if name == PIECES:
        ref = doc.get(REFERENCE)
        return (set(doc) <= _PIECE_FIELDS and isinstance(ref, dict) and ref.get("version") == 1
            and _text(doc.get("bundle_supplier_adoption_id")) and doc.get("status") == "in_progress"
            and doc.get("supplier_dispatch_status") == "sent" and doc.get("assembly_status") == "pending"
            and doc.get("services") == [] and doc.get("quantity") == 1 and _text(doc.get("original_piece_id")))
    if name == DISPATCHES:
        return (set(doc) <= _DISPATCH_FIELDS and doc.get("status") == "sent"
            and doc.get("reconciliation_kind") == "ordinary_to_frozen_bundle"
            and _text(doc.get("bundle_supplier_adoption_id")) and _text(doc.get("reconciles_dispatch_id"))
            and doc.get("piece_count") == len(set(doc.get("piece_ids") or [])) > 0)
    if name in {PIECE_EVENTS, DISPATCH_EVENTS, ORDER_EVENTS}:
        return (set(doc) <= _EVENT_FIELDS and doc.get("event_type") == "supplier_bundle_adopted"
            and doc.get("actor_id") == doc.get("user_id") and _text(doc.get("adoption_id"))
            and doc.get("mezan_only") is True and doc.get("salla_updated") is False and doc.get("qoyod_updated") is False)
    return False


async def validate_adoption_update(db, owner, name, query, update):
    if not isinstance(query, dict) or not isinstance(update, dict):
        return False
    if name == "users":
        if update != {"$inc": {FENCE_FIELD: 1}}:
            return False
        if (query == {"id": owner, "role": "owner"} or
                set(query) == {"id", "created_by"} and query.get("created_by") == owner and _text(query.get("id"))):
            row = await db.users.find_one(query)
            return bool(row and row.get("is_active") is not False and row.get("disabled") is not True
                and not row.get("deleted_at"))
        return False
    if query.get("user_id") != owner:
        return False
    if name in FENCE_COLLECTIONS:
        return set(query) == {"_id", "user_id"} and query.get("_id") is not None and update == {"$inc": {FENCE_FIELD: 1}}
    if name not in _SET_FIELDS or set(update) != {"$set"} or set(update["$set"]) != _SET_FIELDS[name]:
        return False
    if set(query) != ({"user_id", "piece_id"} if name == PIECES else {"user_id", "_id"}):
        return False
    values = update["$set"]
    before = await db[name].find_one(query)
    if not before:
        return False
    aid = values.get("bundle_supplier_adoption_id")
    if name == PLANS:
        old = before.get("bundle_supplier_adoptions") or {}
        changed = {k:v for k,v in values["bundle_supplier_adoptions"].items() if old.get(k) != v}
        if len(changed) != 1 or any(values["bundle_supplier_adoptions"].get(k) != v for k,v in old.items()):
            return False
        line, aid = next(iter(changed.items()))
        adoption = await db[ADOPTIONS].find_one({"user_id": owner, "id": aid})
        old_snapshots = before.get("bundle_snapshots") or {}
        return bool(adoption and adoption.get("order_line_id") == line and line not in old_snapshots
            and adoption["after"]["plan_id"] == before["_id"] and values["bundle_snapshots"] == {
                **old_snapshots, line: adoption["after"]["bundle_snapshot"]})
    adoption = await db[ADOPTIONS].find_one({"user_id": owner, "id": aid})
    if not adoption or adoption.get("state") != "applied":
        return False
    if name == UNITS:
        from bundle_supplier_pieces import prepare_supplier_leaf_unit
        if before["_id"] not in adoption["after"]["unit_ids"] or before.get("bundle_leaves"):
            return False
        snapshot = adoption["after"]["bundle_snapshot"]
        prepared = prepare_supplier_leaf_unit({**before, "bundle_leaves": snapshot["leaves"], "bundle_revision": snapshot["revision"]})
        return (values["bundle_leaves"] == prepared["bundle_leaves"] and values["bundle_revision"] == snapshot["revision"]
            and values["bundle_supplier_bridge_version"] == 1)
    return (before.get("piece_id") in adoption["before"]["piece_ids"] and _unreceived_piece(before)
        and values["status"] == "cancelled" and values["execution_status"] == "bundle_adopted"
        and values["supplier_dispatch_status"] == "superseded" and values["bundle_supplier_retired"] is True)


def make_bundle_supplier_adoption_router(db, current_user):
    from security_sensitive_routes import require_security_owner
    router = APIRouter(prefix="/fulfillment-v2", tags=["Supplier bundle adoption"])
    indexes_ready, index_lock = False, asyncio.Lock()

    async def indexes():
        nonlocal indexes_ready
        if indexes_ready:
            return
        async with index_lock:
            if indexes_ready:
                return
            from preparation_piece_operations import ensure_piece_operation_indexes
            await ensure_piece_operation_indexes(db)
            await db[ADOPTIONS].create_index([("user_id",1),("order_number",1),("order_line_id",1)],
                unique=True, name="uq_bundle_supplier_adoption_line_v1")
            indexes_ready = True

    @router.post("/orders/{order_number}/bundle-adoption/preview")
    async def preview(order_number: str, payload: AdoptionPreview, user=Depends(current_user)):
        actor = await require_security_owner(db, user)
        await _authority(db, actor["id"])
        return await preview_adoption(db, actor["id"], order_number, payload.order_line_id)

    @router.post("/orders/{order_number}/bundle-adoption/apply")
    async def apply(order_number: str, payload: AdoptionApply, user=Depends(current_user)):
        actor = await require_security_owner(db, user)
        await _authority(db, actor["id"])
        await indexes()
        return await apply_adoption(db, actor["id"], order_number, payload)

    return router
