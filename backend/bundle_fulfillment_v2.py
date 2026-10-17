"""Bundle requirements on the existing component plan/unit and warehouse authority.

No parallel balances, suppliers, purchase invoices or journals. Supplier needs
remain pending until an existing posted purchase receipt proves fulfillment.
All mutations are called within the existing owner-serialized transaction.
"""
from copy import deepcopy
import json
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from operational_atomic import operational_owner
from stock_component_consumption_service import (
    PLANS, UNITS, LOCATIONS, RECEIPTS, _rows, _text, _id, _now,
    quantity_decimal, _digest,
)

MAX_ORDER_LEAVES = 10000


def option_answers(line):
    """Keep stable-ID text answers in the order snapshot, never in stock identity.

    Existing selected_context hashes already fence edits and idempotency. Raw
    text is needed only for per-piece supplier instructions, not catalog matching.
    """
    answers = {}
    rows = line.get("options_raw") or line.get("selected_options") or line.get("options") or []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        option = row.get("option") if isinstance(row.get("option"), dict) else {}
        oid = row.get("option_id") or row.get("id") or option.get("id")
        value = row.get("values") if "values" in row else row.get("value", row.get("text", row.get("answer")))
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if isinstance(value, dict):
            value = value.get("value", value.get("text")) if not value.get("id") else None
        if oid is not None and not row.get("value_id") and isinstance(value, str) and value.strip():
            if str(oid) in answers and answers[str(oid)] != value.strip():
                fail("conflicting_option_answers")
            answers[str(oid)] = value.strip()
    return answers


def selected_ids(line):
    from stock_component_consumption_service import selection_tokens
    tokens = line.get("selected_context")
    if tokens is None:
        tokens = selection_tokens(line)
    return [{"option_id": k[3:], "value_id": v[3:]} for k, v in tokens
            if k.startswith("id:") and v.startswith("id:") and v != "id:filled"]


def present_options(line):
    from stock_component_consumption_service import selection_tokens
    tokens = line.get("selected_context")
    if tokens is None:
        tokens = selection_tokens(line)
    return sorted({v for k, v in tokens if k == "option"})


def filled_options(line):
    from stock_component_consumption_service import selection_tokens
    tokens = line.get("selected_context")
    if tokens is None:
        tokens = selection_tokens(line)
    present = {v for k, v in tokens if k == "option"}
    return sorted({k[3:] for k, v in tokens if k.startswith("id:") and v == "id:filled" and k[3:] in present})


async def capture_intake(db, owner, lines, previous):
    """Freeze at first ingestion, even while payment/review is pending."""
    from bundle_catalog_v2 import BUNDLES, expand_bundle
    from product_fulfillment_routes import _product_key
    snapshots = deepcopy(previous or {})
    for line in lines:
        line_id = line["order_line_id"]
        if line_id in snapshots:
            continue
        product = await db["mezan_products_v2"].find_one({"user_id": owner, "$or": [
            {key: line["product_id"]} for key in ("id", "mezan_product_id", "salla_product_id")]})
        if not product:
            continue
        definition = await db[BUNDLES].find_one({"user_id": owner, "product_id": _product_key(product), "product_type": "composite"})
        snapshots[line_id] = await expand_bundle(db, owner, line["product_id"],
            variant_id=line.get("variant_id"), selected_options=selected_ids(line),
            option_answers=option_answers(line),
            present_option_ids=present_options(line), filled_option_ids=filled_options(line)) if definition else None
    if len(json.dumps(snapshots, default=str).encode()) > 8_000_000:
        fail("order_snapshot_size_limit")
    return snapshots


def fail(code, **detail):
    raise HTTPException(409, detail={"code": "bundle_" + code, **detail})


def identity(leaf):
    if leaf.get("kind") == "resource":
        return {"item_type": "stock_component", "resource_id": leaf["resource_id"]}
    return {"item_type": "product", "product_id": leaf.get("mezan_product_id") or leaf["product_id"],
            "salla_product_id": leaf.get("salla_product_id") or leaf["product_id"],
            "variant_id": leaf.get("variant_id") or None}


def stock_key(leaf):
    return leaf["resource_id"] if leaf.get("kind") == "resource" else "bundle-product:" + _digest(identity(leaf))


def leaf_key(leaf):
    return leaf.get("leaf_id") or _digest({k: leaf.get(k) for k in (
        "path", "kind", "product_id", "resource_id", "variant_id", "sourcing_mode", "supplier_id")})


async def inventory_lots(db, owner, warehouses, *, pin=True):
    from fulfillment_v2_routes import _load_inventory_evidence, _inventory_eligibility, _pin_inventory_locations
    locations = await _rows(db[LOCATIONS], {"user_id": owner, "state": {"$ne": "disabled"}})
    if warehouses:
        locations = [r for r in locations if r.get("warehouse_id") in warehouses]
    evidence = await _load_inventory_evidence(db, owner, locations)
    if pin:
        await _pin_inventory_locations(db, owner, locations)
    held = {}
    for unit in await _rows(db[UNITS], {"user_id": owner, "state": "reserved"}):
        for allocation in unit.get("allocations") or []:
            key = (allocation["location_id"], allocation["lot_id"])
            held[key] = held.get(key, Decimal(0)) + quantity_decimal(allocation["quantity"])
    for reservation in await _rows(db["mezan_inventory_reservations_v2"], {"user_id": owner, "status": "active"}):
        for allocation in reservation.get("allocations") or []:
            receipt = allocation.get("receipt_id") or str(allocation.get("inventory_row_key", "")).removeprefix("receipt:")
            key = (allocation.get("location_id"), receipt)
            held[key] = held.get(key, Decimal(0)) + quantity_decimal(allocation["quantity"])
    lots, seen = [], set()
    for location in locations:
        for item in (location.get("occupancy") or {}).get("items") or []:
            lot = _text(item.get("receipt_id") or item.get("lot_id"))
            if not lot:
                continue
            key = (location["id"], lot)
            if key in seen:
                fail("ambiguous_inventory_lot")
            seen.add(key)
            amount = quantity_decimal(item.get("quantity")) - held.get(key, Decimal(0))
            if amount < 0:
                fail("reservation_exceeds_stock")
            if _inventory_eligibility(location, item, evidence):
                amount = Decimal(0)
            lots.append({"location_id": location["id"], "lot_id": lot, "item": item, "available": amount})
    return sorted(lots, key=lambda row: (row["location_id"], row["lot_id"]))


async def allocate_leaves(db, owner, leaves, lots, *, unit_id, previous=None):
    from purchase_receiving_service import same_identity
    from product_inventory_rules import specifications_are_exact
    prior = {leaf_key(r): r for r in previous or []}
    result, allocations = [], []
    for source in leaves:
        leaf = deepcopy(source)
        key = leaf_key(leaf)
        leaf["leaf_id"] = key
        leaf["default_sourcing_mode"] = leaf.get("default_sourcing_mode", leaf["sourcing_mode"])
        old = prior.get(key, {})
        if old.get("source_override"):
            leaf.update(deepcopy(old["source_override"]))
            leaf["source_override"] = deepcopy(old["source_override"])
        leaf["receipt_ids"] = old.get("receipt_ids", [])
        leaf["actual_cost"] = None
        if leaf["sourcing_mode"] == "auto":
            resolved, parts = await allocate_automatic(db, owner, leaf, lots, unit_id=unit_id, old=old)
            result.append(resolved)
            allocations.extend(parts)
            continue
        # Non-stock services are explicitly cost-only; text fields never arrive here.
        if leaf.get("cost_only") or (leaf.get("kind") == "resource" and leaf.get("track_inventory") is False and leaf["sourcing_mode"] == "stock"):
            leaf.update(state="ready", reserved_quantity="0", received_quantity="0", consumed_quantity="0")
            result.append(leaf)
            continue
        remaining = quantity_decimal(leaf["quantity"], positive=True)
        wanted = identity(leaf)
        for lot in lots:
            if lot["available"] <= 0 or not same_identity(lot["item"], wanted):
                continue
            if leaf.get("customization") and not specifications_are_exact(lot["item"].get("specifications"), leaf["customization"]):
                continue
            if leaf.get("location_id") and leaf["location_id"] != lot["location_id"]:
                continue
            if leaf["sourcing_mode"] == "supplier" and lot["lot_id"] not in leaf["receipt_ids"]:
                continue
            taken = min(remaining, lot["available"])
            allocations.append({"id": _id("bundle_allocation", unit_id, key, lot["location_id"], lot["lot_id"]),
                "binding_id": "bundle:" + key, "resource_id": stock_key(leaf), "quantity": str(taken),
                "inventory_identity": wanted, "location_id": lot["location_id"], "lot_id": lot["lot_id"],
                "bundle_leaf_id": key})
            lot["available"] -= taken
            remaining -= taken
            if not remaining:
                break
        reserved = quantity_decimal(leaf["quantity"]) - remaining
        leaf.update(reserved_quantity=str(reserved), consumed_quantity="0",
                    received_quantity=str(reserved) if leaf["sourcing_mode"] == "supplier" else "0",
                    state=("reserved" if not remaining else "awaiting_supplier" if leaf["sourcing_mode"] == "supplier" else "shortage"))
        result.append(leaf)
    return result, allocations


async def allocate_automatic(db, owner, leaf, lots, *, unit_id, old):
    """Keep one requirement with stable stock/supplier segments, using native lots."""
    from bundle_sourcing_v2 import resolve_policy
    from purchase_receiving_service import same_identity
    from product_inventory_rules import specifications_are_exact
    from supplier_identity_service import require_supplier_v2
    def context(row):
        return _digest({"identity": identity(row), "customization": row.get("customization") or {},
            "selected_options": sorted((str(r.get("option_id")), str(r.get("value_id")))
                for r in row.get("selected_options", []) if isinstance(r, dict)),
            "sourcing_policy": row.get("sourcing_policy") or {}})
    current_context = context(leaf)
    if old and old.get("sourcing_context", context(old)) != current_context:
        # The amendment history retains the old evidence; a new physical
        # configuration must never inherit its supplier or receipt claims.
        old = {}
        leaf["receipt_ids"] = []
    leaf["sourcing_context"] = current_context
    policy = deepcopy(old.get("resolved_sourcing") or resolve_policy(leaf))
    supplier = policy.get("supplier_id")
    if supplier:
        try:
            await require_supplier_v2(db, owner, supplier)
        except HTTPException:
            supplier = None
            policy.update(supplier_id=None, reason="supplier_unavailable")
    wanted = quantity_decimal(leaf["quantity"], positive=True)
    available = sum((lot["available"] for lot in lots
        if same_identity(lot["item"], identity(leaf))
        and (not leaf.get("location_id") or leaf["location_id"] == lot["location_id"])
        and (not leaf.get("customization") or specifications_are_exact(lot["item"].get("specifications"), leaf["customization"]))
        and lot["lot_id"] not in leaf["receipt_ids"]), Decimal(0))
    previous_segments = old.get("sourcing_segments") or []
    # Once procurement has been decided, refresh cannot silently buy/hold twice.
    previous_supplier = next((s for s in previous_segments if s["sourcing_mode"] == "supplier"), None)
    if previous_supplier:
        supplier_quantity = min(wanted, quantity_decimal(previous_supplier["quantity"]))
        stock_quantity = wanted - supplier_quantity
    else:
        stock_quantity = min(wanted, available)
        if stock_quantity < wanted and supplier and not policy.get("allow_split"):
            stock_quantity = Decimal(0)
        supplier_quantity = wanted - stock_quantity
    segments, allocations = [], []
    for mode, amount in (("stock", stock_quantity), ("supplier", supplier_quantity)):
        if not amount or (mode == "supplier" and not supplier):
            continue
        segment_id = _id("bundle_source_segment", leaf_key(leaf), mode)
        segment = {**deepcopy(leaf), "leaf_id": segment_id, "quantity": str(amount),
                   "sourcing_mode": mode, "supplier_id": supplier if mode == "supplier" else None}
        segment.pop("source_override", None)
        segment.pop("sourcing_segments", None)
        previous_segment = {**segment, "receipt_ids": leaf["receipt_ids"] if mode == "supplier" else []}
        # A linked procurement receipt is never consumed as ordinary stock.
        eligible = lots if mode == "supplier" else [r for r in lots if r["lot_id"] not in leaf["receipt_ids"]]
        resolved, parts = await allocate_leaves(db, owner, [segment], eligible,
            unit_id=unit_id, previous=[previous_segment])
        segments.extend(resolved)
        allocations.extend({**p, "bundle_leaf_id": leaf_key(leaf), "sourcing_segment_id": segment_id,
                            "sourcing_mode": mode} for p in parts)
    reserved = sum((Decimal(s.get("reserved_quantity", "0")) for s in segments), Decimal(0))
    received = sum((Decimal(s.get("received_quantity", "0")) for s in segments), Decimal(0))
    leaf.update(sourcing_segments=segments, resolved_sourcing=policy, supplier_id=supplier,
        supplier_quantity=str(supplier_quantity if supplier else 0), reserved_quantity=str(reserved),
        received_quantity=str(received), consumed_quantity="0", unresolved_quantity=str(supplier_quantity if not supplier else 0),
        state="reserved" if reserved == wanted else "awaiting_supplier" if supplier else "needs_source")
    return leaf, allocations


def ready(leaves):
    return all(not r.get("required", True) or r.get("state") in {"reserved", "ready", "consumed"} for r in leaves)


async def amend_order(db, owner, plan, lines, version, fingerprint, actor_id, *, answers_by_line=None):
    """Amend unconsumed lines under the existing source-version fence.

    A consumed affected line requires explicit reconciliation; it is never
    restored or silently rewritten. Unchanged/partially shipped other lines
    retain their unit records and original cost evidence.
    """
    from bundle_catalog_v2 import expand_bundle
    old_lines = {r["order_line_id"]: r for r in plan["lines"]}
    new_lines = {r["order_line_id"]: r for r in lines}
    changed = {key for key in old_lines.keys() | new_lines.keys() if old_lines.get(key) != new_lines.get(key)}
    if not changed <= (plan.get("bundle_snapshots") or {}).keys():
        fail("amendment_requires_review")
    units = await _rows(db[UNITS], {"user_id": owner, "plan_id": plan["_id"]})
    affected = [r for r in units if r["order_line_id"] in changed]
    if any(r["state"] == "consumed" for r in affected):
        fail("consumed_amendment_requires_reconciliation")
    history = [*(plan.get("bundle_amendments") or []), {
        "source_version": plan["source_version"], "lines": plan["lines"],
        "snapshots": plan["bundle_snapshots"], "units": affected, "at": _now(), "actor_id": actor_id}]
    if len(history) > 100 or len(json.dumps(history, default=str).encode()) > 8_000_000:
        fail("amendment_history_limit")
    for row in affected:
        await db[UNITS].update_one({"_id": row["_id"], "user_id": owner}, {"$set": {
            "state": "released", "allocations": [], "released_at": _now()}})
    lots = await inventory_lots(db, owner, plan.get("warehouse_ids") or [])
    snapshots = deepcopy(plan["bundle_snapshots"])
    for key in changed:
        line = new_lines.get(key)
        if not line:
            snapshots.pop(key, None)
            continue
        if line["product_id"] != old_lines[key]["product_id"] or line.get("prebuilt_receipts"):
            fail("product_amendment_requires_review")
        selected = [{"option_id": k[3:], "value_id": v[3:]} for k, v in line["selected_context"]
                    if k.startswith("id:") and v.startswith("id:") and v != "id:filled"]
        frozen = snapshots[key].get("frozen_definitions")
        if not frozen:
            fail("frozen_definition_missing")
        expansion = await expand_bundle(db, owner, line["product_id"], variant_id=line["variant_id"],
                                        option_answers=(answers_by_line or {}).get(key, {}),
                                        selected_options=selected, frozen_definitions=frozen,
                                        present_option_ids=present_options(line), filled_option_ids=filled_options(line))
        if not expansion:
            fail("frozen_definition_missing")
        snapshots[key] = expansion
        for index in range(1, line["quantity"] + 1):
            unit_id = _id("component_unit", owner, plan["order_id"], key, index)
            prior = next((r for r in affected if r["_id"] == unit_id), {})
            previous_leaves = prior.get("bundle_leaves")
            if previous_leaves is None:
                # Source choice belongs to the order line, including units
                # added by a quantity amendment. Do not reuse receipt claims.
                previous_leaves = [{"leaf_id": change["leaf_id"], "source_override": {
                    "sourcing_mode": change["after"]["sourcing_mode"],
                    "supplier_id": change["after"].get("supplier_id")}}
                    for change in plan.get("bundle_source_changes", []) if change["order_line_id"] == key]
            leaves, allocations = await allocate_leaves(db, owner, expansion["leaves"], lots,
                unit_id=unit_id, previous=previous_leaves)
            row = {"_id": unit_id, "user_id": owner, "plan_id": plan["_id"], "order_id": plan["order_id"],
                "order_line_id": key, "unit_index": index, "product_id": expansion["product_id"],
                "variant_id": line["variant_id"], "selected_context": line["selected_context"],
                "bundle_revision": expansion["revision"], "bundle_leaves": leaves,
                "resource_demands": [], "allocations": allocations, "prebuilt": None, "state": "reserved",
                "consumption_id": _id("component_consumption", unit_id, version, leaves), "created_at": _now()}
            await db[UNITS].replace_one({"_id": unit_id, "user_id": owner}, row, upsert=True)
    if sum(len(s["leaves"]) * new_lines[key]["quantity"] for key, s in snapshots.items()) > MAX_ORDER_LEAVES:
        fail("order_leaf_limit")
    patch = {"lines": lines, "source_version": version, "input_hash": fingerprint,
             "bundle_snapshots": snapshots, "bundle_amendments": history, "updated_at": _now()}
    await db[PLANS].update_one({"_id": plan["_id"], "user_id": owner}, {"$set": patch})
    return {**plan, **patch}


async def capture_actual_cost(db, owner, row):
    from purchase_receiving_service import COST_STATES, COST_POLICY, identity_key, number
    leaves = deepcopy(row.get("bundle_leaves") or [])
    if not ready(leaves):
        fail("components_incomplete", order_line_id=row["order_line_id"])
    for leaf in leaves:
        if leaf.get("sourcing_mode") == "auto":
            segment_rows = []
            for segment in leaf.get("sourcing_segments", []):
                parts = [{**a, "bundle_leaf_id": segment["leaf_id"]} for a in row.get("allocations", [])
                         if a.get("sourcing_segment_id") == segment["leaf_id"]]
                segment_rows.extend(await capture_actual_cost(db, owner,
                    {**row, "bundle_leaves": [segment], "allocations": parts}))
            leaf["sourcing_segments"] = segment_rows
            leaf["actual_cost"] = (str(sum((Decimal(s["actual_cost"]) for s in segment_rows), Decimal(0)))
                if all(s.get("actual_cost") is not None for s in segment_rows) else None)
            leaf.update(state="consumed", consumed_quantity=leaf.get("reserved_quantity", "0"), cost_policy="mixed_native_sources")
            continue
        if leaf.get("state") not in {"reserved", "ready"}:
            if not leaf.get("required", True):
                # Optional, unavailable leaves are omitted from this physical
                # unit. Any partial allocation is costed only for its quantity.
                leaf["state"] = "reserved" if Decimal(leaf.get("reserved_quantity", "0")) else "skipped"
                if leaf["state"] == "skipped":
                    leaf["actual_cost"] = "0"
                    continue
            else:
                continue
        amount = None
        if leaf.get("state") == "ready":
            amount = leaf.get("unit_cost")
        elif leaf["sourcing_mode"] == "stock":
            cost = await db[COST_STATES].find_one({"user_id": owner, "_id": identity_key(owner, identity(leaf)),
                "authoritative": True, "cost_policy_version": COST_POLICY})
            amount = (cost or {}).get("average_cost")
        else:
            total, known = Decimal(0), True
            for allocation in row.get("allocations") or []:
                if allocation.get("bundle_leaf_id") != leaf_key(leaf):
                    continue
                receipt = await db[RECEIPTS].find_one({"user_id": owner, "id": allocation["lot_id"], "status": "posted"}) or {}
                unit_cost = receipt.get("unit_purchase_cost")
                if unit_cost is None:
                    known = False
                else:
                    total += number(unit_cost) * quantity_decimal(allocation["quantity"])
            if known:
                leaf["actual_cost"] = str(total)
        if amount is not None:
            consumed = leaf["quantity"] if leaf.get("state") == "ready" else leaf.get("reserved_quantity", "0")
            leaf["actual_cost"] = str(number(amount) * quantity_decimal(consumed))
        if leaf.get("kind") == "product" and leaf.get("actual_cost") is not None:
            # Inventory valuation owns the physical unit; explicit option work
            # remains an additional frozen operational cost, not stock value.
            leaf["actual_cost"] = str(number(leaf["actual_cost"]) +
                number(leaf.get("direct_unit_cost", "0")) * quantity_decimal(leaf.get("reserved_quantity", "0")))
        leaf["state"] = "consumed"
        leaf["consumed_quantity"] = leaf.get("reserved_quantity", "0")
        leaf["cost_policy"] = COST_POLICY if leaf["sourcing_mode"] == "stock" else "posted_purchase_receipt"
    return leaves


async def routing_lines(db, owner, order):
    """Frozen sale identity wins over later catalog changes for parent routing."""
    from bundle_catalog_v2 import BUNDLES
    view = await order_view(db, owner, str(order.order_number))
    result = {r["order_line_id"]: r for r in view["bundles"]}
    lifecycle = await db["mezan_component_order_lifecycle_v1"].find_one({
        "user_id": owner, "order_number": str(order.order_number)}) or {}
    if lifecycle and (lifecycle.get("retry_required") or lifecycle.get("state") != "reserved" or lifecycle.get("cancelled")):
        result = {key: {**row, "ready": False} for key, row in result.items()}
    intake = lifecycle.get("bundle_intake_snapshots") or {}
    for item in order.items:
        key = str(getattr(item, "order_item_id", None) or "")
        if key in result:
            continue
        if key in intake:
            if intake[key] is not None:
                result[key] = {"ready": False, "leaves": [], "state": "pending_acceptance"}
            continue
        pid = str(getattr(item, "parent_product_id", None) or getattr(item, "product_id", None) or "")
        if await db[BUNDLES].find_one({"user_id": owner, "product_id": pid, "product_type": "composite"}):
            result[key] = {"ready": False, "leaves": [], "state": "pending_acceptance"}
    return result


async def order_view(db, owner, order):
    plan = await db[PLANS].find_one({"user_id": owner, "order_id": order}) or {}
    units = await _rows(db[UNITS], {"user_id": owner, "plan_id": plan.get("_id", "missing")})
    bundles = []
    for line_id, snapshot in (plan.get("bundle_snapshots") or {}).items():
        selected = [u for u in units if u["order_line_id"] == line_id and u["state"] != "released"]
        leaves = []
        for unit in selected:
            for leaf in unit.get("bundle_leaves") or []:
                leaves.append({**leaf, "unit_index": unit["unit_index"], "unit_state": unit["state"]})
        known = all(r.get("actual_cost") is not None for r in leaves)
        estimated = snapshot.get("estimated_cost")
        bundles.append({"order_line_id": line_id, "product_id": snapshot["product_id"], "revision": snapshot["revision"],
            "product_name": ((snapshot.get("frozen_definitions") or {}).get(snapshot["product_id"], {}).get("catalog_product") or {}).get("name"),
            "quantity": len(selected), "estimated_cost": estimated,
            "sale_quantity": len(selected),
            "units_per_selection": str(sum((Decimal(r["quantity"]) for r in snapshot.get("leaves", []) if r.get("kind") == "product"), Decimal(0))),
            "fulfillment_quantity": str(sum((Decimal(r["quantity"]) for r in leaves if r.get("kind") == "product"), Decimal(0))),
            "current_product_quantity": str(sum((Decimal(r["quantity"]) for r in leaves if r.get("current_product_reference")), Decimal(0))),
            "expected_cost": str(Decimal(str(estimated)) * len(selected)) if estimated is not None else None,
            "actual_cost": str(sum((Decimal(r["actual_cost"]) for r in leaves), Decimal(0))) if leaves and known else None,
            "ready": bool(selected) and ready(leaves), "leaves": leaves, "selected_options": snapshot.get("selected_options", []),
            "state": plan.get("state"), "reconciliation_required": bool(plan.get("reconciliation_required"))})
    return {"order_number": order, "bundles": bundles}


def report_totals(rows):
    """Component quantities are consumption, never additional sales lines."""
    components, suppliers = {}, {}
    for row in rows:
        for leaf in row["leaves"]:
            key = (leaf["kind"], leaf.get("product_id") or leaf.get("resource_id") or leaf.get("binding_id"), leaf.get("variant_id"))
            summary = components.setdefault(key, {"kind": key[0], "reference_id": key[1], "variant_id": key[2],
                "name": leaf.get("name"), "consumed_quantity": Decimal(0), "reserved_quantity": Decimal(0)})
            summary["consumed_quantity"] += Decimal(leaf.get("consumed_quantity", "0"))
            if leaf.get("unit_state") == "reserved":
                summary["reserved_quantity"] += Decimal(leaf.get("reserved_quantity", "0"))
            if row.get("state") != "cancelled" and leaf["sourcing_mode"] in {"supplier", "auto"} and leaf.get("supplier_id") and leaf.get("unit_state") == "reserved":
                missing = Decimal(leaf.get("supplier_quantity", leaf["quantity"])) - Decimal(leaf.get("received_quantity", "0"))
                if missing > 0:
                    supplier = suppliers.setdefault(leaf["supplier_id"], {"supplier_id": leaf["supplier_id"], "requirements": []})
                    supplier["requirements"].append({"order_number": row["order_number"], "order_line_id": row["order_line_id"],
                        "bundle_product_id": row["product_id"], "unit_index": leaf["unit_index"], "leaf_id": leaf["leaf_id"],
                        "kind": key[0], "reference_id": key[1], "variant_id": key[2], "name": leaf.get("name"),
                        "slot_id": leaf.get("slot_id"), "customization": deepcopy(leaf.get("customization") or {}),
                        "quantity": str(missing), "expected_unit_cost": leaf.get("unit_cost")})
    usage = sorted(components.values(), key=lambda r: (-r["consumed_quantity"], str(r["reference_id"])))
    return {"component_usage": [{**r, "consumed_quantity": str(r["consumed_quantity"]), "reserved_quantity": str(r["reserved_quantity"])} for r in usage],
            "supplier_requirements": list(suppliers.values())}


async def refresh_requirements(db, owner, order, *, actor_id, receipt=None, source_change=None):
    """Attach proven receipts/retry shortages without creating purchases or stock."""
    async def refresh(scoped):
        plan = await scoped[PLANS].find_one({"user_id": owner, "order_id": order})
        if not plan or plan.get("state") == "cancelled":
            fail("active_plan_required")
        rows = await _rows(scoped[UNITS], {"user_id": owner, "plan_id": plan["_id"], "state": "reserved"})
        selected = [row for row in rows if row.get("bundle_leaves")]
        matched = receipt is None
        changed = source_change is None
        history = list(plan.get("bundle_source_changes") or [])
        if source_change:
            from supplier_identity_service import require_supplier_v2
            if source_change["sourcing_mode"] not in {"stock", "supplier"}:
                fail("invalid_sourcing_mode")
            if source_change["sourcing_mode"] == "supplier" or source_change.get("supplier_id"):
                await require_supplier_v2(scoped, owner, source_change.get("supplier_id"))
            if len(history) >= 100:
                fail("source_change_history_limit")
        for row in selected:
            leaves = row["bundle_leaves"]
            if source_change and row["order_line_id"] == source_change["order_line_id"]:
                for leaf in leaves:
                    if leaf_key(leaf) == source_change["leaf_id"]:
                        if leaf.get("cost_only"):
                            fail("cost_only_source_change_forbidden")
                        if leaf.get("kind") == "resource" and leaf.get("track_inventory") is False and source_change["sourcing_mode"] == "supplier":
                            fail("nonstock_supplier_requires_service_receipt")
                        changed = True
                        history.append({"order_line_id": row["order_line_id"], "unit_index": row["unit_index"],
                            "leaf_id": leaf_key(leaf), "before": {k: leaf.get(k) for k in ("sourcing_mode", "supplier_id", "receipt_ids")},
                            "after": source_change, "actor_id": actor_id, "at": _now()})
                        if len(history) > 100:
                            fail("source_change_history_limit")
                        override = {"sourcing_mode": source_change["sourcing_mode"], "supplier_id": source_change.get("supplier_id")}
                        leaf.update(**override, source_override=override, receipt_ids=[])
                        for stale in ("sourcing_segments", "resolved_sourcing", "supplier_quantity", "unresolved_quantity"):
                            leaf.pop(stale, None)
            if receipt and row["order_line_id"] == receipt["order_line_id"]:
                for leaf in leaves:
                    if leaf_key(leaf) != receipt["leaf_id"]:
                        continue
                    if leaf["sourcing_mode"] not in {"supplier", "auto"} or not leaf.get("supplier_id"):
                        fail("supplier_requirement_required")
                    proof = await scoped[RECEIPTS].find_one({"user_id": owner, "id": receipt["receipt_id"], "status": "posted"})
                    from purchase_receiving_service import same_identity
                    if not proof or proof.get("source_type") != "purchase_invoice" or not same_identity(proof, identity(leaf)):
                        fail("purchase_receipt_identity_mismatch")
                    from product_inventory_rules import specifications_are_exact
                    if leaf.get("customization") and not specifications_are_exact(proof.get("specifications"), leaf["customization"]):
                        fail("purchase_receipt_customization_mismatch")
                    invoice = await scoped["purchase_invoices"].find_one({"user_id": owner, "id": proof.get("source_id")}) or {}
                    supplier = proof.get("supplier_id") or invoice.get("supplier_counterparty_id") or invoice.get("supplier_id")
                    if supplier != leaf.get("supplier_id"):
                        fail("purchase_supplier_mismatch")
                    leaf["receipt_ids"] = sorted(set([*(leaf.get("receipt_ids") or []), proof["id"]]))
                    matched = True
            # Release only these unconsumed allocations within this transaction.
            await scoped[UNITS].update_one({"_id": row["_id"], "user_id": owner}, {"$set": {"allocations": []}})
        if not matched:
            fail("requirement_not_found")
        if not changed:
            fail("unconsumed_requirement_not_found")
        if source_change:
            await scoped[PLANS].update_one({"_id": plan["_id"], "user_id": owner}, {"$set": {"bundle_source_changes": history}})
        lots = await inventory_lots(scoped, owner, plan.get("warehouse_ids") or [])
        for row in selected:
            leaves, allocations = await allocate_leaves(scoped, owner, row["bundle_leaves"], lots,
                                                        unit_id=row["_id"], previous=row["bundle_leaves"])
            await scoped[UNITS].update_one({"_id": row["_id"], "user_id": owner}, {"$set": {
                "bundle_leaves": leaves, "allocations": allocations, "updated_at": _now(), "actor_id": actor_id}})
        return await order_view(scoped, owner, order)
    return await operational_owner(db, owner, refresh)


class ReceiptLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_line_id: str = Field(min_length=1, max_length=200)
    leaf_id: str = Field(min_length=1, max_length=200)
    receipt_id: str = Field(min_length=1, max_length=200)


class SourceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_line_id: str = Field(min_length=1, max_length=200)
    leaf_id: str = Field(min_length=1, max_length=200)
    sourcing_mode: str
    supplier_id: str | None = Field(default=None, max_length=200)


def make_bundle_operations_router(db: Any, current_user):
    from security_sensitive_routes import require_product_permission
    router = APIRouter(prefix="/fulfillment-v2", tags=["Bundle operations"])

    async def public(view, user):
        try:
            await require_product_permission(db, user, "products.cost.read")
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
            view = deepcopy(view)
            for row in view.get("bundles", []):
                for key in ("estimated_cost", "expected_cost", "actual_cost"):
                    row.pop(key, None)
                for leaf in row.get("leaves", []):
                    pending = [leaf]
                    while pending:
                        entry = pending.pop()
                        for key in ("unit_cost", "direct_unit_cost", "actual_cost", "cost_source", "cost_policy"):
                            entry.pop(key, None)
                        pending.extend(entry.get("sourcing_segments") or [])
        return view

    @router.get("/orders/{order_number}/bundles")
    async def view(order_number: str, user=Depends(current_user)):
        owner = await require_product_permission(db, user, "fulfillment.ready.read")
        return await public(await order_view(db, owner, order_number), user)

    @router.post("/orders/{order_number}/bundles/refresh")
    async def refresh(order_number: str, user=Depends(current_user)):
        owner = await require_product_permission(db, user, "inventory.preparation.receive")
        return await public(await refresh_requirements(db, owner, order_number, actor_id=user.get("_mobile_actor_id") or user["id"]), user)

    @router.post("/orders/{order_number}/bundles/receipts")
    async def receive(order_number: str, payload: ReceiptLink, user=Depends(current_user)):
        owner = await require_product_permission(db, user, "inventory.preparation.receive")
        return await public(await refresh_requirements(db, owner, order_number, actor_id=user.get("_mobile_actor_id") or user["id"], receipt=payload.model_dump()), user)

    @router.get("/bundles/report")
    async def report(user=Depends(current_user)):
        owner = await require_product_permission(db, user, "products.cost.read")
        plans = await _rows(db[PLANS], {"user_id": owner, "bundle_snapshots": {"$exists": True}}, limit=1000)
        rows = []
        for plan in plans:
            view = await order_view(db, owner, plan["order_id"])
            rows.extend({"order_number": plan["order_id"], **row} for row in view["bundles"])
        return {"items": rows, "count": len(rows), "sales_count": sum(r["quantity"] for r in rows if r["state"] != "cancelled"),
                "waiting_count": sum(not r["ready"] and r["quantity"] > 0 and r["state"] != "cancelled" for r in rows),
                "cost_authority": "frozen_bundle_operational", **report_totals(rows)}

    @router.post("/orders/{order_number}/bundles/source")
    async def source(order_number: str, payload: SourceChange, user=Depends(current_user)):
        owner = await require_product_permission(db, user, "products.cost.write")
        return await public(await refresh_requirements(db, owner, order_number,
            actor_id=user.get("_mobile_actor_id") or user["id"], source_change=payload.model_dump()), user)
    return router
