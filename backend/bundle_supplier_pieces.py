"""Frozen bundle leaves received through the existing supplier invoice.

Commercial units keep their original index. A physical leaf has an additional
immutable identity, and can never turn a catalog estimate into a receipt. All
writes here run in the caller's existing owner transaction; this module does
not create purchases, stock, payables or journals.
"""
from copy import deepcopy
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json

from fastapi import HTTPException
from pymongo.errors import OperationFailure

REFERENCE_FIELD = "bundle_supplier_reference"
CHANNEL = "supplier_receiving_v1"
PIECES = "mezan_preparation_pieces_v1"
EVENTS = "mezan_supplier_receiving_events_v1"
INVOICES = "mezan_supplier_invoices_v2"
PLANS = "mezan_component_consumption_plans_v1"
UNITS = "mezan_component_consumption_units_v1"
_FROZEN_FIELDS = ("leaf_id", "kind", "current_product_reference", "product_id",
    "mezan_product_id", "salla_product_id", "variant_id", "slot_id", "path",
    "quantity", "customization", "selected_options", "sourcing_mode", "supplier_id",
    "unit_cost", "direct_unit_cost", "required", "cost_source", "customization_labels")


def fail(reason):
    raise HTTPException(409, detail={"code": "bundle_supplier_" + reason})


def _text(value):
    return str(value or "").strip()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str).encode()).hexdigest()


def _number(value):
    try:
        number = Decimal(str(value))
        if isinstance(value, bool) or not number.is_finite() or number < 0:
            fail("invalid_quantity_or_cost")
        return number
    except (InvalidOperation, ValueError, TypeError):
        fail("invalid_quantity_or_cost")


def leaf_fingerprint(leaf):
    return _digest({key: leaf.get(key) for key in _FROZEN_FIELDS})


def supported_supplier_leaves(unit):
    """The first bridge supports only finished same-product supplier pieces."""
    leaves = unit.get("bundle_leaves")
    if not isinstance(leaves, list) or not leaves or len(leaves) > 2:
        fail("unsupported_recipe")
    keys, total = set(), 0
    for leaf in leaves:
        if not isinstance(leaf, dict):
            fail("unsupported_recipe")
        key = _text(leaf.get("leaf_id"))
        quantity = _number(leaf.get("quantity"))
        if (not key or key in keys or quantity not in {Decimal(1), Decimal(2)}
                or leaf.get("kind") != "product" or leaf.get("current_product_reference") is not True
                or leaf.get("sourcing_mode") != "supplier" or not _text(leaf.get("supplier_id"))
                or leaf.get("product_id") != unit.get("product_id")
                or leaf.get("cost_only") or leaf.get("source_override")
                or leaf.get("cost_source") not in {"mezan_v2_base", "mezan_v2_variant"}
                or _number(leaf.get("direct_unit_cost", "0")) != 0):
            fail("unsupported_recipe")
        _number(leaf.get("unit_cost"))
        if not isinstance(leaf.get("customization") or {}, dict):
            fail("invalid_specifications")
        total += int(quantity)
        keys.add(key)
    if total > 2:
        fail("unsupported_recipe")
    return leaves


def prepare_supplier_leaf_unit(unit):
    """Opt into a physical supplier channel before any stock/receipt is used."""
    result = deepcopy(unit)
    leaves = supported_supplier_leaves(result)
    if (result.get("state") != "reserved" or result.get("allocations")
            or result.get("prebuilt") or result.get("resource_demands")):
        fail("unit_not_unreceived")
    for leaf in leaves:
        if (leaf.get("receipt_ids") or leaf.get("supplier_receipt_proofs") or leaf.get("actual_cost") is not None
                or _number(leaf.get("consumed_quantity", "0"))
                or _number(leaf.get("received_quantity", "0"))
                or _number(leaf.get("reserved_quantity", "0"))):
            fail("unit_not_unreceived")
        leaf.update(supplier_receiving_channel=CHANNEL, state="awaiting_supplier",
                    received_quantity="0", reserved_quantity="0", consumed_quantity="0",
                    actual_cost=None)
    result["bundle_supplier_bridge_version"] = 1
    return result


def supplier_materialization_query(unit):
    return {key: deepcopy(unit.get(key)) for key in (
        "user_id", "_id", "plan_id", "order_id", "order_line_id", "unit_index", "product_id",
        "variant_id", "state", "allocations", "prebuilt", "resource_demands", "bundle_leaves")} | {
        "bundle_supplier_bridge_version": {"$exists": False}}


def validate_supplier_materialization_update(query, update):
    """Sealed preparation capability: only opt an untouched unit into leaves."""
    keys = {"user_id", "_id", "plan_id", "order_id", "order_line_id", "unit_index", "product_id",
        "variant_id", "state", "allocations", "prebuilt", "resource_demands", "bundle_leaves",
        "bundle_supplier_bridge_version"}
    try:
        if (not isinstance(query, dict) or set(query) != keys or not isinstance(update, dict)
                or set(update) != {"$set"} or not isinstance(update["$set"], dict)
                or set(update["$set"]) != {"bundle_leaves", "bundle_supplier_bridge_version"}
                or type(update["$set"]["bundle_supplier_bridge_version"]) is not int
                or update["$set"]["bundle_supplier_bridge_version"] != 1
                or query["bundle_supplier_bridge_version"] != {"$exists": False}
                or type(query["bundle_supplier_bridge_version"]["$exists"]) is not bool
                or any(not isinstance(query[key], str) or not _text(query[key]) for key in (
                    "user_id", "_id", "plan_id", "order_id", "order_line_id", "product_id"))
                or type(query["unit_index"]) is not int or query["unit_index"] < 1
                or query["state"] != "reserved" or query["allocations"] != []
                or query["resource_demands"] != [] or query["prebuilt"] is not None):
            return False
        before = {key: value for key, value in query.items() if key != "bundle_supplier_bridge_version"}
        prepared = prepare_supplier_leaf_unit(before)
        return update["$set"]["bundle_leaves"] == prepared["bundle_leaves"]
    except (HTTPException, ValueError, TypeError, KeyError):
        return False


def supplier_leaf_reference(unit, leaf, leaf_unit_index):
    quantity = _number(leaf.get("quantity"))
    if (not isinstance(leaf_unit_index, int) or isinstance(leaf_unit_index, bool)
            or leaf_unit_index < 1 or leaf_unit_index > quantity):
        fail("invalid_leaf_unit")
    return {"version": 1, "plan_id": unit["plan_id"], "unit_id": unit["_id"],
        "leaf_id": leaf["leaf_id"], "leaf_unit_index": leaf_unit_index,
        "leaf_fingerprint": leaf_fingerprint(leaf)}


def supplier_leaf_piece_id(owner, unit, reference):
    from preparation_piece_barcode import preparation_piece_id
    return preparation_piece_id(user_id=owner, order_number=unit["order_id"],
        order_item_id=unit["order_line_id"], unit_index=unit["unit_index"], bundle_supplier_reference=reference)


def build_supplier_leaf_pieces(*, owner, unit, template):
    """Build unsaved pieces; the caller explicitly owns custody reconciliation."""
    leaves = supported_supplier_leaves(unit)
    if (unit.get("bundle_supplier_bridge_version") != 1 or template.get("services")
            or unit.get("user_id") != owner or not unit.get("_id") or not unit.get("plan_id")
            or not unit.get("order_id") or not unit.get("order_line_id")
            or type(unit.get("unit_index")) is not int or unit["unit_index"] < 1):
        fail("materialization_context_invalid")
    result = []
    for leaf in leaves:
        if leaf.get("supplier_receiving_channel") != CHANNEL:
            fail("channel_required")
        if _text(template.get("supplier_id")) not in {"", leaf["supplier_id"]}:
            fail("supplier_mismatch")
        for index in range(1, int(_number(leaf["quantity"])) + 1):
            reference = supplier_leaf_reference(unit, leaf, index)
            pid = supplier_leaf_piece_id(owner, unit, reference)
            piece = deepcopy(template)
            # A copied ordinary execution record is not a physical receipt.
            for key in list(piece):
                if (key == "_id" or key.startswith(("receipt_", "supplier_receiving_", "assembly_", "received_",
                        "preparation_received_", "preparation_receipt_", "experiment_"))
                        or key in {"completed_at", "completed_by", "completed_by_name", "bundle_supplier_retired",
                                   "cancellation_reason", "cancelled_at", "options_normalized", "option_values",
                                   "metadata", "size", "color", "customer_name", "service_specifications_snapshot",
                                   "product_options_snapshot", "specifications_snapshot"}):
                    piece.pop(key, None)
            specifications = deepcopy(leaf.get("customization") or {})
            labels = leaf.get("customization_labels") or {}
            selected = [{"option_id": key, "name": labels.get(key) or "اختيار القطعة", "value": value}
                        for key, value in sorted(specifications.items())]
            piece.update(id=pid, piece_id=pid, barcode="MEZAN-PIECE:" + pid,
                user_id=owner, order_number=unit["order_id"], order_item_id=unit["order_line_id"],
                unit_index=unit["unit_index"], quantity=1, product_id=leaf["product_id"],
                variant_id=leaf.get("variant_id"), salla_variant_id=leaf.get("variant_id"),
                supplier_id=leaf["supplier_id"], services=[], service_count=0,
                completed_service_count=0, remaining_service_count=0,
                service_plan_status="no_external_services", assembly_status="pending",
                options_raw=selected, selected_options=selected, options=selected,
                custom_fields=[], service_specifications_snapshot=deepcopy(selected),
                specifications_snapshot=deepcopy(selected), product_options_snapshot={},
                bundle_supplier_specifications=specifications,
                bundle_parent_selected_options=deepcopy(leaf.get("selected_options") or []),
                **{REFERENCE_FIELD: reference})
            result.append(piece)
    return result


async def ensure_supplier_piece_identity_index(db):
    """Install the leaf dimension before retiring the exact legacy constraint."""
    old_name, new_name = "uq_preparation_piece_physical_unit_v1", "uq_preparation_piece_physical_unit_v2"
    old_keys = [(key, 1) for key in ("user_id", "batch_id", "order_number", "order_item_id", "unit_index")]
    new_keys = [*old_keys, (REFERENCE_FIELD + ".leaf_id", 1),
                (REFERENCE_FIELD + ".leaf_unit_index", 1)]
    collection = db[PIECES]
    indexes = await collection.index_information()
    old = indexes.get(old_name)
    if old and (list(old.get("key", [])) != old_keys or old.get("unique") is not True
                or any(old.get(key) for key in ("partialFilterExpression", "sparse", "collation", "expireAfterSeconds"))):
        fail("legacy_identity_index_unexpected")
    await collection.create_index(new_keys, unique=True, name=new_name)
    installed = (await collection.index_information()).get(new_name) or {}
    if (list(installed.get("key", [])) != new_keys or installed.get("unique") is not True
            or any(installed.get(key) for key in ("partialFilterExpression", "sparse", "collation", "expireAfterSeconds"))):
        fail("identity_index_unverified")
    if old:
        try:
            await collection.drop_index(old_name)
        except OperationFailure as exc:
            # Another startup may have verified the same replacement first.
            if exc.code != 27:
                raise


def _validate_loaded_piece(owner, piece, unit, plan):
    if piece.get("bundle_supplier_retired"):
        fail("parent_retired")
    ref = piece.get(REFERENCE_FIELD)
    if ref is None:
        return None
    if (not isinstance(ref, dict) or type(ref.get("version")) is not int or ref.get("version") != 1
            or any(not isinstance(ref.get(key), str) or not _text(ref[key]) for key in ("plan_id", "unit_id", "leaf_id", "leaf_fingerprint"))
            or type(piece.get("unit_index")) is not int or piece["unit_index"] < 1
            or piece.get("user_id") != owner):
        fail("identity_invalid")
    if (not unit or not plan or plan.get("state") != "accepted" or plan.get("reconciliation_required")
            or unit.get("user_id") != owner or plan.get("user_id") != owner
            or unit.get("_id") != ref["unit_id"] or plan.get("_id") != ref["plan_id"]
            or unit.get("state") not in {"reserved", "consumed"}
            or type(unit.get("bundle_supplier_bridge_version")) is not int or unit.get("bundle_supplier_bridge_version") != 1
            or type(unit.get("unit_index")) is not int or unit["unit_index"] < 1
            or unit.get("plan_id") != plan.get("_id") or unit.get("order_id") != plan.get("order_id")
            or piece.get("order_number") != unit.get("order_id")
            or piece.get("order_item_id") != unit.get("order_line_id")
            or piece.get("unit_index") != unit.get("unit_index")):
        fail("frozen_authority_mismatch")
    leaves = supported_supplier_leaves(unit)
    found = [leaf for leaf in leaves if leaf.get("leaf_id") == ref.get("leaf_id")]
    snapshot = (plan.get("bundle_snapshots") or {}).get(unit["order_line_id"]) or {}
    frozen = [leaf for leaf in snapshot.get("leaves", []) if leaf.get("leaf_id") == ref.get("leaf_id")]
    if (len(found) != 1 or len(frozen) != 1 or snapshot.get("revision") != unit.get("bundle_revision")
            or leaf_fingerprint(found[0]) != leaf_fingerprint(frozen[0])):
        fail("frozen_leaf_mismatch")
    leaf = found[0]
    if (leaf.get("supplier_receiving_channel") != CHANNEL
            or ref != supplier_leaf_reference(unit, leaf, ref.get("leaf_unit_index"))
            or piece.get("piece_id") != supplier_leaf_piece_id(owner, unit, ref)
            or piece.get("product_id") != leaf.get("product_id")
            or (piece.get("variant_id") or None) != (leaf.get("variant_id") or None)
            or piece.get("supplier_id") != leaf.get("supplier_id")
            or piece.get("bundle_supplier_specifications") != (leaf.get("customization") or {})
            or piece.get("services")):
        fail("piece_evidence_mismatch")
    return unit, leaf


async def assert_supplier_source_current(db, owner, plan, line_id, *, session=None):
    """A frozen supplier identity cannot execute a changed commercial line."""
    from fulfillment_v2_routes import _component_order_lines
    from order_engine.mapper import map_salla_order
    from stock_component_consumption_service import _lines
    kw = {"session": session} if session is not None else {}
    number = plan.get("order_id")
    source = await db["unified_orders"].find_one({"user_id": owner, "order_number": number}, **kw) or {}
    intent = await db["mezan_component_order_lifecycle_v1"].find_one({"user_id": owner, "order_number": number}, **kw) or {}
    watermark = source.get("g47_salla_snapshot") or {}
    if (not source or intent.get("state") != "reserved" or intent.get("accepted") is not True
            or intent.get("cancelled") or intent.get("retry_required")
            or (plan.get("source_version") or {}).get("value") != intent.get("generation")
            or watermark.get("component_pending") or watermark.get("requires_authoritative_refresh")):
        fail("source_reconciliation_required")
    try:
        raw = source["raw_by_source"]["salla_direct"]
        current = _lines(_component_order_lines(map_salla_order(raw), {}))
        frozen = [row for row in plan.get("lines", []) if row.get("order_line_id") == line_id]
        actual = [row for row in current if row["order_line_id"] == line_id]
        if len(frozen) != 1 or frozen != actual:
            fail("source_reconciliation_required")
    except (KeyError, TypeError, ValueError):
        fail("source_reconciliation_required")


async def validate_supplier_leaf_piece(db, owner, piece, *, session=None):
    """Never trust a reference supplied in a piece without frozen authority."""
    if piece.get("bundle_supplier_retired"):
        fail("parent_retired")
    ref = piece.get(REFERENCE_FIELD)
    if ref is None:
        return None
    if not isinstance(ref, dict) or any(not isinstance(ref.get(key), str) for key in ("unit_id", "plan_id")):
        fail("identity_invalid")
    kw = {"session": session} if session is not None else {}
    unit = await db[UNITS].find_one({"user_id": owner, "_id": ref["unit_id"]}, **kw)
    plan = await db[PLANS].find_one({"user_id": owner, "_id": ref["plan_id"]}, **kw)
    result = _validate_loaded_piece(owner, piece, unit, plan)
    await assert_supplier_source_current(db, owner, plan, unit["order_line_id"], session=session)
    return result


async def supplier_bundle_intake_lines(db, owner, order_numbers):
    """Expose only accepted same-product supplier recipes, retaining sale units."""
    result = defaultdict(dict)
    numbers = sorted(set(order_numbers))
    if not numbers:
        return result
    plans = await db[PLANS].find({"user_id": owner, "order_id": {"$in": numbers},
        "state": "accepted", "bundle_snapshots": {"$exists": True}}).to_list(len(numbers) + 1)
    if len(plans) > len(numbers):
        fail("plan_ambiguous")
    for plan in plans:
        if plan.get("reconciliation_required"):
            continue
        units = await db[UNITS].find({"user_id": owner, "plan_id": plan["_id"],
            "state": {"$ne": "released"}}).to_list(10001)
        if len(units) > 10000:
            fail("unit_limit_exceeded")
        for line_id, snapshot in (plan.get("bundle_snapshots") or {}).items():
            if not snapshot:
                continue
            rows = [unit for unit in units if unit.get("order_line_id") == line_id]
            frozen_lines = [line for line in plan.get("lines") or [] if line.get("order_line_id") == line_id]
            try:
                if (len(frozen_lines) != 1 or not rows or len(rows) != frozen_lines[0]["quantity"]
                        or {unit.get("unit_index") for unit in rows} != set(range(1, len(rows) + 1))
                        or snapshot.get("operational_blocked") or snapshot.get("needs_selection")
                        or snapshot.get("unresolved_components")):
                    continue
                fingerprints = {leaf["leaf_id"]: leaf_fingerprint(leaf) for leaf in snapshot.get("leaves", [])}
                for unit in rows:
                    leaves = supported_supplier_leaves(unit)
                    if (unit.get("order_id") != plan["order_id"] or unit.get("bundle_revision") != snapshot.get("revision")
                            or unit.get("product_id") != snapshot.get("product_id")
                            or unit.get("selected_context") != frozen_lines[0].get("selected_context")
                            or unit.get("variant_id") != frozen_lines[0].get("variant_id")
                            or {leaf["leaf_id"]: leaf_fingerprint(leaf) for leaf in leaves} != fingerprints):
                        fail("frozen_leaf_mismatch")
                    if unit.get("bundle_supplier_bridge_version") != 1:
                        prepare_supplier_leaf_unit(unit)
                await assert_supplier_source_current(db, owner, plan, line_id)
                per_unit = sum(int(_number(leaf["quantity"])) for leaf in rows[0]["bundle_leaves"])
                result[plan["order_id"]][line_id] = {
                    "physical_quantity_per_unit": per_unit, "physical_quantity": per_unit * len(rows),
                    "supplier_ids": sorted({leaf["supplier_id"] for leaf in rows[0]["bundle_leaves"]}),
                    "bundle_supplier_fingerprint": _digest({"revision": snapshot["revision"], "leaves": fingerprints}),
                }
            except (HTTPException, ValueError, KeyError, TypeError):
                continue
    return result


async def supplier_leaf_intake_unit(db, owner, order, line, index):
    plan = await db[PLANS].find_one({"user_id": owner, "order_id": order, "state": "accepted"})
    if not plan or plan.get("reconciliation_required"):
        fail("frozen_authority_mismatch")
    rows = await db[UNITS].find({"user_id": owner, "plan_id": plan["_id"],
        "order_line_id": line, "unit_index": index, "state": {"$ne": "released"}}).to_list(2)
    if len(rows) != 1 or "bundle_supplier_bridge_version" in rows[0]:
        fail("materialization_context_invalid")
    unit = rows[0]
    prepared = prepare_supplier_leaf_unit(unit)
    preview = build_supplier_leaf_pieces(owner=owner, unit=prepared, template={"services": []})
    for piece in preview:
        _validate_loaded_piece(owner, piece, prepared, plan)
    await assert_supplier_source_current(db, owner, plan, line)
    identity = {"user_id": owner, "order_number": order, "order_item_id": line, "unit_index": index}
    existing = await db[PIECES].find_one(identity)
    receipt = await db[EVENTS].find_one({**identity,
        "event_type": {"$in": ["supplier_piece_scanned", "supplier_piece_service_recorded"]}})
    if existing or receipt:
        fail("existing_piece_requires_adoption")
    return unit, prepared


async def supplier_bundle_print_preview(db, owner, lines, batch_id):
    """Prepare deterministic leaf cards before the assignment transaction."""
    numbers = [_text(line.get("order_number")) for line in lines]
    eligible = await supplier_bundle_intake_lines(db, owner, numbers)
    cards, references = [], {}
    for line in lines:
        number, line_id = _text(line.get("order_number")), _text(line.get("order_item_id"))
        if line_id not in eligible[number]:
            cards.append(deepcopy(line))
            continue
        for index in line.get("unit_indices") or []:
            _, unit = await supplier_leaf_intake_unit(db, owner, number, line_id, index)
            for piece in build_supplier_leaf_pieces(owner=owner, unit=unit, template={**line,
                    "batch_id": batch_id, "services": []}):
                card = {**deepcopy(line), "unit_index": index, "unit_indices": [index], "quantity": 1,
                    "file_spec_fields": deepcopy(piece["service_specifications_snapshot"]), "product_options": {},
                    "size": None, "color": None, "customer_name": None, "note": None,
                    REFERENCE_FIELD: deepcopy(piece[REFERENCE_FIELD])}
                cards.append(card)
                references[piece["piece_id"]] = piece[REFERENCE_FIELD]
    return cards, references


async def supplier_leaf_receipts(db, owner, unit, leaf, *, session=None):
    """Read verified finalized invoices, without trusting cached leaf amounts."""
    from supplier_invoice_integrity import verify_persisted_supplier_invoice
    from supplier_invoice_reporting_costs import _invoice_amounts
    from supplier_native_invoice_v2 import invoice_digest
    kw = {"session": session} if session is not None else {}
    proofs, total = [], Decimal(0)
    for index in range(1, int(_number(leaf["quantity"])) + 1):
        ref = supplier_leaf_reference(unit, leaf, index)
        pid = supplier_leaf_piece_id(owner, unit, ref)
        piece = await db[PIECES].find_one({"user_id": owner, "piece_id": pid}, **kw)
        if not piece:
            continue
        await validate_supplier_leaf_piece(db, owner, piece, session=session)
        events = await db[EVENTS].find({"user_id": owner, "piece_id": pid,
            "event_type": "supplier_piece_service_recorded"}, **kw).to_list(3)
        if not events:
            continue
        if len(events) != 1:
            fail("receipt_ambiguous")
        event = events[0]
        if (event.get(REFERENCE_FIELD) != ref or event.get("order_number") != unit["order_id"]
                or event.get("order_item_id") != unit["order_line_id"]
                or event.get("unit_index") != unit["unit_index"]
                or event.get("financial_invoice_created") is not True
                or event.get("liability_created") is not True
                or event.get("experiment_mode") or event.get("experiment_run_id")
                or event.get("product_id") != leaf.get("product_id")
                or (event.get("variant_id") or None) != (leaf.get("variant_id") or None)
                or event.get("bundle_supplier_specifications") != (leaf.get("customization") or {})
                or (event.get("supplier_context") or {}).get("id") != leaf["supplier_id"]
                or piece.get("supplier_dispatch_status") != "received"):
            fail("receipt_evidence_mismatch")
        invoice = await verify_persisted_supplier_invoice(db, user_id=owner,
            invoice_id=event.get("supplier_invoice_id"), session_id=event.get("session_id"),
            supplier_id=leaf["supplier_id"], mongo_session=session)
        if (invoice.get("status") != "payable_posted" or not invoice.get("approved_at")
                or invoice.get("financial_invoice_created") is not True
                or invoice.get("liability_created") is not True
                or invoice.get("financial_integrity_verified") is not True
                or invoice.get("experiment_mode") is not False or invoice.get("experiment_run_id")
                or invoice.get("session_id") != event.get("session_id")
                or any(invoice.get(key) for key in ("reversed_at", "reversal_id", "cancelled_at", "voided_at"))
                or event.get("finalized_at") != invoice.get("approved_at")):
            fail("receipt_evidence_mismatch")
        lines = [line for line in invoice.get("lines", []) if pid in (line.get("piece_ids") or [])]
        if len(lines) != 1 or lines[0].get("services") or lines[0].get("product_charge_eligible") is False:
            fail("unsupported_invoice_line")
        aliases = {leaf.get(key) for key in ("product_id", "mezan_product_id", "salla_product_id") if leaf.get(key)}
        line = lines[0]
        if (line.get("product_id") not in aliases
                or line.get("product_v2_id") and line["product_v2_id"] not in aliases
                or (line.get("variant_id") or None) != (leaf.get("variant_id") or None)):
            fail("invoice_product_mismatch")
        try:
            amounts = _invoice_amounts(invoice)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            fail("receipt_cost_invalid")
        amount = amounts.get((pid, "product"), {}).get("net_amount")
        if amount is None:
            fail("receipt_cost_missing")
        amount = _number(amount)
        total += amount
        proofs.append({"piece_id": pid, "invoice_id": invoice["id"], "event_id": event["id"],
            "invoice_digest": invoice_digest(invoice), "actual_cost": str(amount)})
    return proofs, total


async def refresh_supplier_leaf_unit(db, owner, unit, *, session=None):
    kw = {"session": session} if session is not None else {}
    leaves = deepcopy(unit["bundle_leaves"])
    for leaf in leaves:
        if leaf.get("supplier_receiving_channel") != CHANNEL:
            fail("channel_required")
        proofs, amount = await supplier_leaf_receipts(db, owner, unit, leaf, session=session)
        complete = len(proofs) == _number(leaf["quantity"])
        leaf.update(supplier_receipt_proofs=proofs, received_quantity=str(len(proofs)),
            reserved_quantity=str(len(proofs)), state="reserved" if complete else "awaiting_supplier",
            actual_cost=str(amount) if complete else None, cost_policy="posted_supplier_invoice")
    result = await db[UNITS].update_one({"user_id": owner, "_id": unit["_id"],
        "state": "reserved", "bundle_leaves": unit["bundle_leaves"]},
        {"$set": {"bundle_leaves": leaves}}, **kw)
    if result.matched_count != 1:
        fail("unit_changed")
    return {**unit, "bundle_leaves": leaves}


async def finalize_supplier_leaf_receipts(db, owner, pieces, *, session=None):
    units = {}
    for piece in pieces:
        authority = await validate_supplier_leaf_piece(db, owner, piece, session=session)
        if authority:
            unit, _ = authority
            units[unit["_id"]] = unit
    for unit in units.values():
        await refresh_supplier_leaf_unit(db, owner, unit, session=session)


async def collapse_ready_supplier_leaves(db, owner, pieces):
    """One parent coverage record only after every exact physical leaf is Ready."""
    from supplier_receipt_ready_policy import supplier_piece_received
    ordinary, groups = [], {}
    for piece in pieces:
        if piece.get(REFERENCE_FIELD) is None:
            ordinary.append(piece)
            continue
        unit, _ = await validate_supplier_leaf_piece(db, owner, piece)
        groups.setdefault(unit["_id"], (unit, []))[1].append(piece)
    for unit, members in groups.values():
        expected = {supplier_leaf_piece_id(owner, unit, supplier_leaf_reference(unit, leaf, index))
            for leaf in supported_supplier_leaves(unit)
            for index in range(1, int(_number(leaf["quantity"])) + 1)}
        if (len(members) != len(expected) or {piece["piece_id"] for piece in members} != expected
                or any(piece.get("assembly_status") != "ready"
                    or not supplier_piece_received(piece, preparation_optional=(
                        piece.get("assembly_preparation_receipt_optional") is True))
                    for piece in members)):
            return None
        for leaf in unit["bundle_leaves"]:
            proofs, _ = await supplier_leaf_receipts(db, owner, unit, leaf)
            if len(proofs) != _number(leaf["quantity"]):
                return None
        ordinary.append(members[0])
    return ordinary


async def verify_reporting_supplier_units(db, owner, units, plans, *, read, budget=None):
    """Revalidate consumed leaf proofs in bounded batches, with no writes.

    Reporting shares the existing invoice arithmetic and native journal-header
    evidence reader. It does not turn cached actual_cost or a saved proof ID
    into authority, and never performs a query inside the per-leaf loop.
    """
    from accounting_ledger_v2 import read_supplier_invoice_journal_evidence_v2
    from supplier_invoice_reporting_costs import INVOICE_FIELDS, _invoice_amounts, _instant, _journal_id
    from supplier_native_invoice_v2 import invoice_digest
    candidates = [unit for unit in units if unit.get("state") == "consumed"
        and any(leaf.get("supplier_receiving_channel") == CHANNEL for leaf in unit.get("bundle_leaves") or [])]
    if not candidates:
        return
    plan_groups = defaultdict(list)
    for plan in plans:
        plan_groups[plan["_id"]].append(plan)
    expected, invalid = {}, set()
    for unit in candidates:
        unit["_supplier_invoice_actual_verified"] = False
        try:
            if len(plan_groups[unit["plan_id"]]) != 1:
                fail("frozen_authority_mismatch")
            plan = plan_groups[unit["plan_id"]][0]
            for leaf in supported_supplier_leaves(unit):
                quantity = int(_number(leaf["quantity"]))
                proofs = leaf.get("supplier_receipt_proofs")
                if (leaf.get("supplier_receiving_channel") != CHANNEL
                        or not isinstance(proofs, list) or len(proofs) != quantity
                        or _number(leaf.get("consumed_quantity")) != quantity):
                    fail("receipt_cost_missing")
                keyed = {proof["piece_id"]: proof for proof in proofs}
                if len(keyed) != quantity:
                    fail("receipt_ambiguous")
                for index in range(1, quantity + 1):
                    ref = supplier_leaf_reference(unit, leaf, index)
                    pid = supplier_leaf_piece_id(owner, unit, ref)
                    proof = keyed[pid]
                    if (pid in expected or not all(isinstance(proof.get(key), str) and proof[key]
                            for key in ("invoice_id", "event_id", "invoice_digest"))):
                        fail("receipt_ambiguous")
                    expected[pid] = (unit, leaf, plan, ref, proof)
        except (HTTPException, ValueError, TypeError, KeyError):
            invalid.add(unit.get("_id"))
    if not expected:
        return
    if len(expected) > 10000:
        raise ValueError("order_cost_authority_read_limit:bundle_supplier_proofs")
    pids = sorted(expected)
    invoice_ids = sorted({value[4]["invoice_id"] for value in expected.values()})
    piece_rows = await read(PIECES, {"user_id": owner, "piece_id": {"$in": pids}}, {"_id": 0}, len(pids) * 2)
    event_rows = await read(EVENTS, {"user_id": owner, "piece_id": {"$in": pids},
        "event_type": "supplier_piece_service_recorded"}, {"_id": 0}, len(pids) * 2)
    invoice_rows = await read(INVOICES, {"user_id": owner, "id": {"$in": invoice_ids}}, INVOICE_FIELDS, len(invoice_ids) * 2)
    session_ids = sorted({row.get("session_id") for row in invoice_rows if isinstance(row.get("session_id"), str)})
    session_rows = await read("mezan_supplier_receiving_sessions_v1", {"user_id": owner, "id": {"$in": session_ids}},
        {"_id": 0, "id": 1, "status": 1, "supplier_id": 1, "supplier_invoice_id": 1}, max(1, len(session_ids) * 2))
    groups = defaultdict(list)
    for kind, rows, key in (("piece", piece_rows, "piece_id"), ("event", event_rows, "piece_id"),
                            ("invoice", invoice_rows, "id"), ("session", session_rows, "id")):
        for row in rows:
            groups[(kind, row.get(key))].append(row)
    journal_ids = sorted({gid for row in invoice_rows if (gid := _journal_id(row.get("mz2_txn_group_id")))})
    journals = (await read_supplier_invoice_journal_evidence_v2(db, user_id=owner, txn_group_ids=journal_ids)) if journal_ids else []
    reversed_ids = set()
    for row in journals:
        groups[("journal", row.get("txn_group_id"))].append(row)
        if row.get("reversal_of_txn_group_id"):
            reversed_ids.add(row["reversal_of_txn_group_id"])
    invoice_cache, amounts_by_leaf = {}, defaultdict(lambda: Decimal(0))
    now = datetime.now(timezone.utc)
    for pid, (unit, leaf, plan, ref, proof) in expected.items():
        if budget is not None:
            await budget.checkpoint()
        try:
            if (len(groups[("piece", pid)]) != 1 or len(groups[("event", pid)]) != 1
                    or len(groups[("invoice", proof["invoice_id"])]) != 1):
                fail("receipt_ambiguous")
            piece, event = groups[("piece", pid)][0], groups[("event", pid)][0]
            _validate_loaded_piece(owner, piece, unit, plan)
            invoice = groups[("invoice", proof["invoice_id"])][0]
            identity = invoice["id"]
            if identity not in invoice_cache:
                sessions = groups[("session", invoice.get("session_id"))]
                if (invoice.get("status") != "payable_posted" or invoice.get("currency", "SAR") != "SAR"
                        or invoice.get("experiment_mode") is not False or invoice.get("experiment_run_id")
                        or any(invoice.get(key) is not True for key in (
                            "financial_invoice_created", "liability_created", "financial_integrity_verified"))
                        or any(invoice.get(key) for key in ("reversed_at", "reversal_id", "cancelled_at", "voided_at"))
                        or _instant(invoice.get("approved_at")) > now or len(sessions) != 1
                        or sessions[0].get("status") != "closed" or sessions[0].get("supplier_invoice_id") != identity
                        or sessions[0].get("supplier_id") != invoice.get("supplier_id")):
                    fail("receipt_evidence_mismatch")
                digest = invoice_digest(invoice)
                gid = _journal_id(invoice.get("mz2_txn_group_id"))
                if invoice.get("mz2_financial_contract") or invoice.get("mz2_txn_group_id"):
                    headers = groups[("journal", gid)]
                    if (not gid or gid in reversed_ids or len(headers) != 1 or headers[0].get("status") != "posted"
                            or (headers[0].get("metadata") or {}).get("invoice_digest") != digest
                            or (headers[0].get("metadata") or {}).get("supplier_invoice_id") != identity):
                        fail("receipt_evidence_mismatch")
                invoice_cache[identity] = (digest, _invoice_amounts(invoice))
            digest, amounts = invoice_cache[identity]
            if (proof["invoice_digest"] != digest or proof["event_id"] != event.get("id")
                    or event.get(REFERENCE_FIELD) != ref or event.get("supplier_invoice_id") != identity
                    or event.get("session_id") != invoice.get("session_id")
                    or event.get("financial_invoice_created") is not True or event.get("liability_created") is not True
                    or event.get("experiment_mode") or event.get("experiment_run_id")
                    or event.get("product_id") != leaf.get("product_id")
                    or (event.get("variant_id") or None) != (leaf.get("variant_id") or None)
                    or event.get("bundle_supplier_specifications") != (leaf.get("customization") or {})
                    or (event.get("supplier_context") or {}).get("id") != leaf["supplier_id"]
                    or invoice.get("supplier_id") != leaf["supplier_id"]
                    or event.get("order_number") != unit["order_id"]
                    or event.get("order_item_id") != unit["order_line_id"]
                    or event.get("unit_index") != unit["unit_index"]
                    or _instant(event.get("finalized_at")) != _instant(invoice.get("approved_at"))
                    or piece.get("supplier_dispatch_status") != "received"):
                fail("receipt_evidence_mismatch")
            lines = [line for line in invoice.get("lines", []) if pid in (line.get("piece_ids") or [])]
            aliases = {leaf.get(key) for key in ("product_id", "salla_product_id", "mezan_product_id") if leaf.get(key)}
            if (len(lines) != 1 or lines[0].get("services") or lines[0].get("product_charge_eligible") is False
                    or lines[0].get("product_id") not in aliases
                    or lines[0].get("product_v2_id") and lines[0]["product_v2_id"] not in aliases
                    or (lines[0].get("variant_id") or None) != (leaf.get("variant_id") or None)):
                fail("invoice_product_mismatch")
            amount = _number(amounts[(pid, "product")]["net_amount"])
            if amount != _number(proof.get("actual_cost")):
                fail("receipt_cost_invalid")
            amounts_by_leaf[(unit["_id"], leaf["leaf_id"])] += amount
        except (HTTPException, ValueError, TypeError, KeyError, ArithmeticError):
            invalid.add(unit.get("_id"))
    for unit in candidates:
        try:
            unit["_supplier_invoice_actual_verified"] = unit["_id"] not in invalid and all(
                amounts_by_leaf[(unit["_id"], leaf["leaf_id"])] == _number(leaf.get("actual_cost"))
                for leaf in unit["bundle_leaves"])
        except (HTTPException, ValueError, TypeError, KeyError):
            unit["_supplier_invoice_actual_verified"] = False
