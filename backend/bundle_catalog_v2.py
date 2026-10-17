"""Versioned bundle definitions over the existing Product/Components V2 catalogs.

Expansion is read-only. Operational consumers freeze its returned value at sale.
All edits serialize on the same merchant fence as fulfillment transactions.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from component_status_policy import require_active_component
from component_workspace_cost_compat_routes import _current_cost
from operational_atomic import operational_owner
from order_option_cost_snapshot_routes import resolve_base_unit_cost
from product_fulfillment_routes import _product, _product_key
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_fulfillment_rules import PRODUCT_OPERATION_PROFILES
from product_option_cost_routes import BINDINGS, RESOURCES
from product_v2_details_routes import COST_PROFILES
from supplier_identity_service import require_supplier_v2
from security_sensitive_routes import require_product_permission
from product_bundle_policy_routes import is_text_option

BUNDLES = "mezan_bundle_definitions_v2"
LIMITS = {"max_items": 100, "max_depth": 5, "max_leaves": 500, "max_revisions": 100, "max_total_quantity": 100000}


def reject(code, status=422):
    raise HTTPException(status, detail={"code": code})


def number(value, *, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        reject("bundle_invalid_number")
    if not result.is_finite() or result < 0 or (positive and result <= 0) or result > Decimal("1000000000"):
        reject("bundle_invalid_number")
    return result


def quantity(value):
    result = number(value, positive=True)
    if result.as_tuple().exponent < -6:
        reject("bundle_quantity_precision")
    return result


def text_id(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        reject("bundle_invalid_reference")
    return value.strip()


async def _definition(db, owner, key):
    return await db[BUNDLES].find_one({"user_id": owner, "product_id": key}, {"_id": 0})


def _public(doc):
    return {k: deepcopy(v) for k, v in doc.items() if k not in {"_id", "history", "user_id"}}


async def expand_bundle(db, owner, product_id, *, variant_id=None, selected_options=None, present_option_ids=None, filled_option_ids=None, option_answers=None, frozen_definitions=None, strict=True, _overrides=None):
    async def catalog_product(reference):
        # Frozen definitions originate from the server-owned sale snapshot.
        # Resolve their aliases before touching a subsequently edited/deleted catalog.
        for frozen in (frozen_definitions or {}).values():
            snapshot = (frozen or {}).get("catalog_product") or {}
            if str(reference) in {str(snapshot.get(field)) for field in ("id", "mezan_product_id", "salla_product_id") if snapshot.get(field) is not None}:
                return deepcopy(snapshot)
        return await _product(db, owner, str(reference))

    root = await catalog_product(product_id)
    root_key = _product_key(root)
    overrides = _overrides or {}
    definitions = {}
    product_snapshots = {}
    metadata = {}
    resource_snapshots = {}
    answers = {}
    graph_visits = 0
    needs_selection = []
    leaves = []
    provenance = []
    explicit_resources = set()
    automatic_resources = set()
    component_decisions = []
    unresolved_components = []
    composition_v2 = False

    async def definition(key):
        if key not in definitions:
            if frozen_definitions is not None:
                if key not in frozen_definitions:
                    reject("bundle_frozen_definition_missing", 409)
                definitions[key] = deepcopy(frozen_definitions[key])
            else:
                definitions[key] = overrides.get(key) or await _definition(db, owner, key)
        return definitions[key]

    def selections_for(product, raw, *, validate_required=True, present_ids=None, filled_ids=None):
        if raw is None:
            raw = []
        if not isinstance(raw, list) or len(raw) > 100:
            reject("bundle_invalid_selection")
        opts = {str(o.get("id")): dict(o) for o in product.get("options", []) if isinstance(o, dict)}
        source_raw = product.get("raw_salla_details") or product.get("raw_salla") or {}
        for opt in source_raw.get("options", []) if isinstance(source_raw, dict) else []:
            if str(opt.get("id")) in opts:
                opts[str(opt["id"])].update({k:v for k,v in opt.items() if k.startswith("visibility")})
        def checked_ids(values):
            if values is None:
                return set()
            if not isinstance(values, list) or len(values) > 100:
                reject("bundle_invalid_option_presence")
            result = {text_id(value) for value in values}
            if None in result or not result.issubset(opts):
                reject("bundle_unknown_option_presence")
            return result
        present = checked_ids(present_ids)
        filled = checked_ids(filled_ids)
        if not filled.issubset(present):
            reject("bundle_filled_option_not_present")
        dependencies = {}
        for oid, opt in opts.items():
            conditions = opt.get("show_if") or opt.get("conditions") or []
            if not isinstance(conditions, list) or any(not isinstance(c, dict) or set(c) != {"option_id", "value_id"} for c in conditions):
                reject("bundle_unsupported_option_condition", 409)
            refs = [(str(c["option_id"]), str(c["value_id"])) for c in conditions]
            if opt.get("visibility") == "on_condition":
                if opt.get("visibility_condition_type") not in {"=", "!="}:
                    reject("bundle_unsupported_option_condition", 409)
                refs.append((str(opt.get("visibility_condition_option")), str(opt.get("visibility_condition_value"))))
            for parent, value in refs:
                if parent not in opts or value not in {str(v.get("id")) for v in opts[parent].get("values", [])}:
                    reject("bundle_unsupported_option_condition", 409)
            dependencies[oid] = [parent for parent, _ in refs]
        visited = set()
        def visit(oid, active):
            if oid in active:
                reject("bundle_option_visibility_cycle", 409)
            if oid in visited:
                return
            if len(active) >= 100:
                reject("bundle_option_visibility_depth", 409)
            for parent in dependencies[oid]:
                visit(parent, active | {oid})
            visited.add(oid)
        for oid in opts:
            visit(oid, set())
        pairs = set()
        for selection in raw:
            if not isinstance(selection, dict) or set(selection) != {"option_id", "value_id"}:
                reject("bundle_invalid_selection")
            pair = (text_id(selection.get("option_id")), text_id(selection.get("value_id")))
            option = opts.get(pair[0])
            if not option or pair[1] not in {str(v.get("id")) for v in option.get("values", []) if isinstance(v, dict)}:
                reject("bundle_option_value_not_found")
            if pair in pairs:
                reject("bundle_duplicate_selection")
            pairs.add(pair)
        for oid, opt in opts.items():
            # Display conditions must be represented as stable ID pairs. Unknown
            # supplier schemas fail closed rather than guessing which rows apply.
            conditions = opt.get("show_if") or opt.get("conditions") or []
            visible = True
            if conditions:
                if not isinstance(conditions, list) or any(not isinstance(c, dict) or set(c) != {"option_id", "value_id"} for c in conditions):
                    reject("bundle_unsupported_option_condition", 409)
                visible = all((str(c["option_id"]), str(c["value_id"])) in pairs for c in conditions)
            if opt.get("visibility") == "on_condition":
                operator = opt.get("visibility_condition_type")
                parent = str(opt.get("visibility_condition_option"))
                value = str(opt.get("visibility_condition_value"))
                if operator not in {"=", "!="} or parent not in opts or value not in {str(v.get("id")) for v in opts[parent].get("values", [])}:
                    reject("bundle_unsupported_option_condition", 409)
                matches = (parent, value) in pairs
                visible = matches if operator == "=" else any(p[0] == parent for p in pairs) and not matches
            chosen = [pair for pair in pairs if pair[0] == oid]
            if validate_required and (chosen or oid in present) and not visible:
                reject("bundle_hidden_option_selected")
            if len(chosen) > 1 and opt.get("type") not in {"checkbox", "multiple", "multi_select"}:
                reject("bundle_multiple_option_values")
            if validate_required and visible and (opt.get("required") or opt.get("is_required")) and not (chosen if opt.get("values") else oid in filled):
                needs_selection.append({"product_id": _product_key(product), "option_id": oid})
        return pairs

    def validate_current(product, row):
        count = quantity(row.get("quantity", 1))
        if count != count.to_integral_value() or count > LIMITS["max_items"]:
            reject("bundle_current_product_quantity_limit")
        slots = row.get("slots")
        variants = {str(v.get("id")) for v in (product.get("variants") or product.get("skus") or [])}
        if row.get("variant_id") and text_id(row["variant_id"]) not in variants:
            reject("bundle_variant_not_found")
        if slots is None:
            return
        if not isinstance(slots, list) or len(slots) != int(count):
            reject("bundle_slot_count_mismatch")
        seen_slots = set()
        options = {str(o.get("id")): o for o in product.get("options", [])}
        for slot in slots:
            if not isinstance(slot, dict) or set(slot) - {"slot_id", "variant_id", "variant_rules", "customization_option_ids"}:
                reject("bundle_invalid_slot")
            slot_id = text_id(slot.get("slot_id"))
            if not slot_id or slot_id in seen_slots:
                reject("bundle_duplicate_slot")
            seen_slots.add(slot_id)
            if slot.get("variant_id") and text_id(slot["variant_id"]) not in variants:
                reject("bundle_variant_not_found")
            rules = slot.get("variant_rules", [])
            if not isinstance(rules, list) or len(rules) > 100:
                reject("bundle_invalid_variant_rules")
            for rule in rules:
                if not isinstance(rule, dict) or set(rule) != {"conditions", "variant_id"} or text_id(rule["variant_id"]) not in variants:
                    reject("bundle_invalid_variant_rule")
                if not isinstance(rule["conditions"], list) or not rule["conditions"]:
                    reject("bundle_invalid_variant_rule")
                selections_for(product, rule["conditions"], validate_required=False)
            custom = slot.get("customization_option_ids", [])
            if not isinstance(custom, list) or len(custom) > 100 or any(not isinstance(v, str) for v in custom) or len(set(custom)) != len(custom):
                reject("bundle_invalid_customization")
            for oid in custom:
                if not isinstance(oid, str) or oid not in options or options[oid].get("values") or composition_v2 and not is_text_option(options[oid]):
                    reject("bundle_invalid_text_option")

    async def gather(product, ancestors):
        nonlocal graph_visits
        graph_visits += 1
        if graph_visits > LIMITS["max_leaves"]:
            reject("bundle_graph_limit")
        key = _product_key(product)
        if key in ancestors:
            reject("bundle_cycle", 409)
        doc = await definition(key)
        if frozen_definitions is not None and doc and doc.get("catalog_product"):
            product = deepcopy(doc["catalog_product"])
        if doc and doc.get("product_type") == "composite" and len(ancestors) >= LIMITS["max_depth"]:
            reject("bundle_depth_limit")
        if len(product_snapshots) >= LIMITS["max_leaves"]:
            reject("bundle_graph_limit")
        product_snapshots[key] = product
        if key not in metadata:
            if frozen_definitions is not None:
                metadata[key] = deepcopy((doc or {}).get("catalog_metadata", {}))
                if not metadata[key]:
                    reject("bundle_frozen_metadata_missing", 409)
            else:
                query = {"user_id": owner, "salla_product_id": key}
                metadata[key] = {"profile": await db[COST_PROFILES].find_one(query, {"_id": 0}),
                    "operation_profile": await db[PRODUCT_OPERATION_PROFILES].find_one(query, {"_id": 0}) or {},
                    "bindings": await db[PRODUCT_RESOURCE_BINDINGS].find(query, {"_id": 0}).to_list(length=501),
                    "options": await db[BINDINGS].find(query, {"_id": 0}).to_list(length=501), "resources": {}}
                links = metadata[key]["bindings"] + metadata[key]["options"] + ((doc or {}).get("items", []))
                if len(links) > 1000:
                    reject("bundle_graph_limit")
                for link in links:
                    rid = link.get("resource_id")
                    if rid and rid not in metadata[key]["resources"]:
                        row = await db[RESOURCES].find_one({"user_id": owner, "id": rid}, {"_id": 0})
                        require_active_component(row)
                        metadata[key]["resources"][rid] = row
            resource_snapshots.update(metadata[key].get("resources", {}))
        if not doc or doc.get("product_type") != "composite":
            return
        if len(ancestors) >= LIMITS["max_depth"]:
            reject("bundle_depth_limit")
        if not 1 <= len(doc.get("items", [])) <= LIMITS["max_items"]:
            reject("bundle_item_limit")
        has_current = any(row.get("kind") == "current_product" for row in doc["items"])
        if has_current:
            scopes = doc.get("component_scopes") or {}
            for binding in metadata[key].get("bindings", []) + metadata[key].get("options", []):
                if binding.get("resource_id") and scopes.get(binding["resource_id"]) not in {"per_bundle", "per_unit"}:
                    reject("bundle_component_scope_required", 409)
        for item in doc["items"]:
            if not isinstance(item.get("component_scope", "per_bundle"), str) or item.get("component_scope", "per_bundle") not in {"per_bundle", "per_unit"}:
                reject("bundle_invalid_component_scope")
            if item.get("kind") == "current_product":
                validate_current(product, item)
            condition = item.get("condition")
            if condition is not None:
                selections_for(product, [condition], validate_required=False)
            if item.get("kind") == "product":
                child_product = await catalog_product(item["product_id"])
                child_definition = await definition(_product_key(child_product))
                if frozen_definitions is not None and child_definition and child_definition.get("catalog_product"):
                    child_product = child_definition["catalog_product"]
                if item.get("variant_id") and not any(str(v.get("id")) == item["variant_id"] for v in (child_product.get("variants") or child_product.get("skus") or [])):
                    reject("bundle_variant_not_found")
                await gather(child_product, ancestors + [key])
                validate_overrides(item, _product_key(child_product))
                validate_mappings(product, child_product, item)
            elif item.get("kind") == "current_product":
                validate_overrides(item, key)
            elif item.get("kind") == "resource":
                component = await db[RESOURCES].find_one({"user_id": owner, "id": item.get("resource_id")})
                require_active_component(component)
                if not component.get("track_inventory") and item.get("sourcing_mode") == "supplier":
                    reject("bundle_nonstock_supplier_requires_service_receipt", 409)
            await source(item)

    def validate_overrides(item, key):
        available = {(kind, str(b.get("id"))) for kind, rows in (("product_resource", metadata[key].get("bindings", [])), ("option_resource", metadata[key].get("options", []))) for b in rows if b.get("resource_id")}
        for override in item.get("component_overrides", []):
            if (override["binding_kind"], override["binding_id"]) not in available:
                reject("bundle_component_override_not_found")

    def validate_mappings(parent, child, item):
        variants = {str(v.get("id")) for v in child.get("variants") or child.get("skus") or []}
        for rule in item.get("variant_rules", []):
            if not isinstance(rule, dict) or set(rule) != {"conditions", "variant_id"} or text_id(rule["variant_id"]) not in variants or not isinstance(rule["conditions"], list) or not rule["conditions"]:
                reject("bundle_invalid_variant_rule")
            selections_for(parent, rule["conditions"], validate_required=False)
        for mapping in item.get("option_mappings", []):
            if not isinstance(mapping, dict) or set(mapping) != {"option_id", "value_id", "target_option_id", "target_value_id"}:
                reject("bundle_invalid_option_mapping")
            selections_for(parent, [{"option_id": mapping["option_id"], "value_id": mapping["value_id"]}], validate_required=False)
            selections_for(child, [{"option_id": mapping["target_option_id"], "value_id": mapping["target_value_id"]}], validate_required=False)
        parent_options = {str(o.get("id")): o for o in parent.get("options", [])}
        child_options = {str(o.get("id")): o for o in child.get("options", [])}
        targets = set()
        for mapping in item.get("customization_mappings", []):
            if not isinstance(mapping, dict) or set(mapping) != {"option_id", "target_option_id"}:
                reject("bundle_invalid_customization_mapping")
            source_id, target = text_id(mapping["option_id"]), text_id(mapping["target_option_id"])
            if source_id not in parent_options or target not in child_options or not is_text_option(parent_options[source_id]) or not is_text_option(child_options[target]) or target in targets:
                reject("bundle_invalid_customization_mapping")
            targets.add(target)

    async def source(item):
        mode = item.get("sourcing_mode", "stock")
        if mode not in {"stock", "supplier", "auto"} or mode == "auto" and item.get("kind") not in {"product", "current_product"}:
            reject("bundle_invalid_sourcing_mode")
        supplier = text_id(item.get("supplier_id"))
        if mode == "supplier" and not supplier:
            reject("bundle_supplier_required")
        if supplier:
            await require_supplier_v2(db, owner, supplier)
        location = text_id(item.get("location_id"))
        if location and not await db["warehouse_locations"].find_one({"user_id": owner, "id": location}):
            reject("bundle_location_not_found")
        return {"sourcing_mode": mode, "supplier_id": supplier, "location_id": location}

    def append(leaf, path):
        if len(leaves) >= LIMITS["max_leaves"]:
            reject("bundle_leaf_limit")
        leaf["leaf_id"] = hashlib.sha256((root_key + "/" + "/".join(path)).encode()).hexdigest()[:24]
        leaf["path"] = list(path)
        leaf["paths"] = [list(path)]
        scope = leaf.pop("automatic_binding_scope", None)
        leaf["dedup_scopes"] = [list(path[:-1] if scope is None else scope)]
        leaf["required"] = leaf.get("required", True)
        leaves.append(leaf)

    async def resource(item, multiplier, path, automatic=False, dedup_scope=None):
        rid = text_id(item.get("resource_id"))
        if not rid:
            reject("bundle_reference_required")
        row = await db[RESOURCES].find_one({"user_id": owner, "id": rid})
        require_active_component(row)
        (automatic_resources if automatic else explicit_resources).add(rid)
        if not composition_v2 and rid in automatic_resources and rid in explicit_resources:
            reject("bundle_resource_cost_overlap", 409)
        snapshot_row = resource_snapshots.get(rid, row)
        cost = _current_cost(snapshot_row)
        if not snapshot_row.get("track_inventory") and item.get("sourcing_mode") == "supplier":
            reject("bundle_nonstock_supplier_requires_service_receipt", 409)
        append({"kind": "resource", "resource_id": rid, "product_id": None,
                "name": row.get("name"), "track_inventory": bool(snapshot_row.get("track_inventory")), "quantity": str(multiplier),
                "unit": row.get("unit") or item.get("unit", "unit"),
                "required": item.get("required", True), "repeat_policy": item.get("repeat_policy", "once"),
                "unit_cost": str(number(cost)) if cost is not None else None,
                "cost_source": "components_v2", "automatic_binding_scope": dedup_scope, **await source(item)}, path)

    async def inherited(product_key, item, binding, binding_kind, multiplier, path, scope):
        """Resolve physical use independently from price inclusion and assembly."""
        bid = str(binding.get("id") or "")
        if not bid:
            reject("bundle_binding_identity_required", 409)
        policy = ((metadata[product_key].get("profile") or {}).get("composition_components") or {}).get(binding_kind + ":" + bid, {})
        use = next((o["use"] for o in item.get("component_overrides", []) if o["binding_kind"] == binding_kind and o["binding_id"] == bid), True)
        cost = policy.get("cost_treatment", "unknown")
        origin = policy.get("assembly_origin", "unknown")
        rid = binding["resource_id"]
        record = metadata[product_key].get("resources", {}).get(rid, {})
        decision = {"product_id": product_key, "path": list(path), "binding_kind": binding_kind, "binding_id": bid,
                    "resource_id": rid, "name": record.get("name"), "quantity": str(multiplier), "use": use,
                    "policy": deepcopy(policy), "cost_treatment": cost, "assembly_origin": origin,
                    "status": "used" if use else "excluded", "unit_cost": _current_cost(record)}
        component_decisions.append(decision)
        reason = None
        if cost not in {"independent", "included_in_base"} or origin not in {"inhouse", "supplier", "purchased_complete"}:
            reason = "component_provenance_required"
        elif origin == "supplier" and not use and not (policy.get("supplier_omission_supported") is True and policy.get("evidence_ref")):
            reason = "supplier_omission_unconfirmed"
        elif origin != "inhouse" and cost == "independent" and use:
            reason = "supplier_component_execution_required"
        elif origin == "inhouse" and cost == "included_in_base" and use:
            reason = "included_component_inventory_cost_basis_required"
        if reason:
            decision.update(status="unresolved", reason=reason)
            unresolved_components.append(deepcopy(decision))
            return
        if not use or origin in {"supplier", "purchased_complete"}:
            return
        await resource({"resource_id": rid, "sourcing_mode": "stock", "required": item.get("required", True), "repeat_policy": "sum"}, multiplier, path + [binding_kind + ":" + bid], True, scope)

    async def current_units(product, row, multiplier, path, pairs, scoped_answers=None):
        key = _product_key(product)
        count = quantity(row.get("quantity", 1))
        if count != count.to_integral_value() or count > LIMITS["max_items"]:
            reject("bundle_current_product_quantity_limit")
        slots = row.get("slots") or [{"slot_id": str(i + 1), "variant_id": row.get("variant_id")} for i in range(int(count))]
        variants = product.get("variants") or product.get("skus") or []
        for slot in slots:
            selected_ids = {rule["variant_id"] for rule in slot.get("variant_rules", []) if all((c["option_id"], c["value_id"]) in pairs for c in rule["conditions"])}
            if len(selected_ids) > 1:
                reject("bundle_slot_variant_conflict", 409)
            vid = next(iter(selected_ids), None) or slot.get("variant_id") or row.get("variant_id")
            if slot.get("variant_rules") and not selected_ids and not vid:
                needs_selection.append({"product_id": key, "slot_id": slot["slot_id"], "variant_required": True})
                continue
            if not vid and (variants or product.get("variants_count", 0)):
                needs_selection.append({"product_id": key, "slot_id": slot["slot_id"], "variant_required": True})
                continue
            selected = next((v for v in variants if str(v.get("id")) == vid), None) if vid else None
            if vid and selected is None:
                reject("bundle_variant_not_found")
            customization = {}
            local_answers = answers if key == root_key else (scoped_answers or {})
            for oid in slot.get("customization_option_ids", []):
                if not local_answers.get(oid):
                    needs_selection.append({"product_id": key, "slot_id": slot["slot_id"], "option_id": oid})
                else:
                    customization[oid] = local_answers[oid]
            sourcing = await source(row)
            if customization and sourcing["sourcing_mode"] == "stock" and not vid:
                reject("bundle_stock_customization_requires_variant", 409)
            cost, cost_source = resolve_base_unit_cost({"variant_id": vid}, metadata[key].get("profile"), product)
            append({"kind": "product", "product_id": key, "mezan_product_id": product.get("mezan_product_id"),
                    "salla_product_id": product.get("salla_product_id"), "variant_id": vid,
                    "name": product.get("name"), "sku": (selected or product).get("sku"), "track_inventory": True,
                    "current_product_reference": True, "slot_id": slot["slot_id"], "customization": customization,
                    "sourcing_policy": deepcopy((metadata[key].get("operation_profile") or {}).get("bundle_sourcing", {})),
                    "selected_options": [{"option_id": a, "value_id": b} for a, b in sorted(pairs)],
                    "quantity": str(multiplier), "unit": row.get("unit", "unit"), "required": row.get("required", True),
                    "repeat_policy": row.get("repeat_policy", "once"), "unit_cost": str(number(cost)) if cost is not None else None,
                    "cost_source": cost_source, "direct_unit_cost": "0", "automatic_binding_scope": path[:-1], **sourcing}, path + ["slot:" + slot["slot_id"]])

    async def walk(product, item, multiplier, path, ancestors):
        key = _product_key(product)
        product = product_snapshots.get(key, product)
        if key in ancestors:
            reject("bundle_cycle", 409)
        doc = await definition(key)
        has_current = any(row.get("kind") == "current_product" for row in (doc or {}).get("items", []))
        vid = text_id(item.get("variant_id"))
        variants = product.get("variants") or product.get("skus") or []
        selected = next((v for v in variants if str(v.get("id")) == vid), None) if vid else None
        if not vid and (variants or product.get("variants_count", 0)) and not has_current:
            if strict or path and not composition_v2:
                reject("bundle_variant_required", 409)
            needs_selection.append({"product_id": key, "variant_required": True})
        if vid and selected is None:
            reject("bundle_variant_not_found")
        doc = await definition(key)
        pairs = selections_for(product, item.get("selected_options"), present_ids=item.get("present_option_ids"), filled_ids=item.get("filled_option_ids"))
        local_answers = answers if key == root_key else item.get("option_answers", {})
        if doc and doc.get("product_type") == "composite":
            if len(ancestors) >= LIMITS["max_depth"]:
                reject("bundle_depth_limit")
            provenance.append({"path": list(path), "definition": _public(doc)})
            items = doc.get("items", [])
            if not 1 <= len(items) <= LIMITS["max_items"]:
                reject("bundle_item_limit")
            active = [(index, child) for index, child in enumerate(items) if not child.get("condition") or (child["condition"]["option_id"], child["condition"]["value_id"]) in pairs]
            current_start = len(leaves)
            for index, child in active:
                if child.get("kind") == "current_product":
                    await current_units(product, {**child, "required": item.get("required", True) and child.get("required", True)}, multiplier, path + [f"{key}@{doc['revision']}:{child.get('rule_id', index)}"], pairs, local_answers)
            current_counts = {}
            for leaf in leaves[current_start:]:
                signature = (leaf.get("slot_id"), leaf.get("variant_id"), leaf.get("sourcing_mode"), leaf.get("supplier_id"), leaf.get("location_id"), json.dumps(leaf.get("customization", {}), sort_keys=True))
                amount = quantity(leaf["quantity"])
                prior = current_counts.get(signature)
                policy = leaf.get("repeat_policy", "once")
                if prior is None:
                    current_counts[signature] = (amount, policy)
                elif policy == "sum" or prior[1] == "sum":
                    current_counts[signature] = (prior[0] + amount, "sum")
                elif prior[0] != amount:
                    reject("bundle_duplicate_quantity_conflict", 409)
            current_count = sum((entry[0] for entry in current_counts.values()), Decimal(0)) / multiplier
            for index, child in active:
                if child.get("kind") == "current_product":
                    continue
                child = {**child, "required": item.get("required", True) and child.get("required", True)}
                count = quantity(multiplier * quantity(child.get("quantity", 1)))
                child_path = path + [f"{key}@{doc['revision']}:{child.get('rule_id', index)}"]
                if child.get("kind") == "resource":
                    if child.get("component_scope", "per_bundle") == "per_unit":
                        if not has_current:
                            reject("bundle_per_unit_requires_current_product")
                        if not current_count:
                            continue
                        count = quantity(count * current_count)
                    await resource(child, count, child_path)
                else:
                    child_product = await catalog_product(child["product_id"])
                    if composition_v2:
                        matching = {rule["variant_id"] for rule in child.get("variant_rules", []) if all((c["option_id"], c["value_id"]) in pairs for c in rule["conditions"])}
                        if len(matching) > 1:
                            needs_selection.append({"product_id": child["product_id"], "rule_id": child.get("rule_id"), "reason": "variant_mapping_conflict"})
                            continue
                        if matching:
                            child["variant_id"] = next(iter(matching))
                        mapped = {(s["option_id"], s["value_id"]) for s in child.get("selected_options", [])}
                        for mapping in child.get("option_mappings", []):
                            if (mapping["option_id"], mapping["value_id"]) in pairs:
                                mapped.add((mapping["target_option_id"], mapping["target_value_id"]))
                        child["selected_options"] = [{"option_id": a, "value_id": b} for a, b in sorted(mapped)]
                        child["option_answers"] = {m["target_option_id"]: local_answers[m["option_id"]] for m in child.get("customization_mappings", []) if local_answers.get(m["option_id"])}
                        child["present_option_ids"] = list(child["option_answers"])
                        child["filled_option_ids"] = list(child["option_answers"])
                        child_options = {str(o.get("id")): o for o in child_product.get("options", [])}
                        conflicting = [oid for oid in child_options if len({v for o, v in mapped if o == oid}) > 1 and child_options[oid].get("type") not in {"checkbox", "multiple", "multi_select"}]
                        if conflicting:
                            needs_selection.append({"product_id": child["product_id"], "reason": "option_mapping_conflict", "option_ids": conflicting})
                            continue
                    await walk(child_product, child, count, child_path, ancestors + [key])
            bindings = metadata[key].get("bindings", [])
            options = [o for o in metadata[key].get("options", []) if (str(o.get("option_id")), str(o.get("value_id"))) in pairs]
            seen_resources = set()
            for binding in bindings + options:
                if binding.get("mode") == "direct":
                    binding_id = text_id(binding.get("id"))
                    if not binding_id:
                        reject("bundle_direct_binding_identity_required", 409)
                    append({"kind": "direct_cost", "binding_id": binding_id,
                            "name": " / ".join(str(v) for v in (binding.get("option_name"), binding.get("value_name")) if v) or binding_id,
                            "track_inventory": False, "cost_only": True, "required": True,
                            "quantity": str(quantity(multiplier * quantity(binding.get("quantity", 1)))),
                            "unit": "unit", "unit_cost": str(number(binding.get("direct_amount"))),
                            "cost_source": "product_option_binding_v2", "sourcing_mode": "stock",
                            "supplier_id": None, "location_id": None,
                            "automatic_binding_scope": path + ["bundle:" + key]},
                           path + [f"{key}@{doc['revision']}:direct:{binding_id}"])
                    continue
                rid = binding.get("resource_id")
                if composition_v2:
                    binding_kind = "product_resource" if binding in bindings else "option_resource"
                    scope = (doc.get("component_scopes") or {}).get(rid)
                    if has_current and scope == "per_unit":
                        for _, current in active:
                            if current.get("kind") == "current_product":
                                await inherited(key, current, binding, binding_kind, quantity(multiplier * quantity(current.get("quantity", 1)) * quantity(binding.get("quantity", 1))), path + ["current:" + str(current.get("rule_id"))], path)
                    else:
                        effective_item = item
                        if has_current and not any(o["binding_kind"] == binding_kind and o["binding_id"] == str(binding.get("id")) for o in item.get("component_overrides", [])):
                            choices = {next((o["use"] for o in current.get("component_overrides", []) if o["binding_kind"] == binding_kind and o["binding_id"] == str(binding.get("id"))), True)
                                       for _, current in active if current.get("kind") == "current_product"}
                            if len(choices) > 1:
                                conflict = {"product_id": key, "path": list(path), "binding_kind": binding_kind,
                                            "binding_id": binding.get("id"), "resource_id": rid,
                                            "status": "unresolved", "reason": "shared_component_override_conflict"}
                                component_decisions.append(conflict)
                                unresolved_components.append(deepcopy(conflict))
                                continue
                            if choices:
                                effective_item = {**item, "component_overrides": [{"binding_kind": binding_kind, "binding_id": str(binding.get("id")), "use": next(iter(choices))}]}
                        await inherited(key, effective_item, binding, binding_kind, quantity(multiplier * quantity(binding.get("quantity", 1))), path, path)
                    continue
                if rid in seen_resources:
                    reject("bundle_resource_cost_overlap", 409)
                seen_resources.add(rid)
                scope = (doc.get("component_scopes") or {}).get(rid)
                if has_current and scope not in {"per_bundle", "per_unit"}:
                    reject("bundle_component_scope_required", 409)
                component_count = current_count if scope == "per_unit" else Decimal(1)
                if not component_count:
                    continue
                await resource({"resource_id": rid, "sourcing_mode": "stock", "required": item.get("required", True)}, quantity(multiplier * component_count * quantity(binding.get("quantity", 1))), path + [f"{key}@{doc['revision']}:binding", f"resource:{rid}"], True, path + ["bundle:" + key])
            return
        if multiplier != multiplier.to_integral_value():
            reject("bundle_product_quantity_must_be_integral")
        profile = metadata[key].get("profile")
        cost, cost_source = resolve_base_unit_cost({"variant_id": vid}, profile, product)
        sourcing = await source(item)
        bindings = metadata[key].get("bindings", [])
        options = metadata[key].get("options", [])
        # Only IDs from the actual variant can authorize an option's charge.
        selections = (selected or {}).get("selections") or (selected or {}).get("options") or []
        selected_pairs = set(pairs)
        for selection in selections if isinstance(selections, list) else []:
            if isinstance(selection, dict):
                oid = selection.get("option_id") or selection.get("id")
                value = selection.get("value_id")
                if oid is not None and value is not None:
                    selected_pairs.add((str(oid), str(value)))
        option_direct = Decimal(0)
        resource_ids = set()
        for binding in bindings + [o for o in options if (str(o.get("option_id")), str(o.get("value_id"))) in selected_pairs]:
            amount = quantity(binding.get("quantity", 1))
            if binding.get("mode") == "direct":
                option_direct += number(binding.get("direct_amount")) * amount
                continue
            rid = binding.get("resource_id")
            if composition_v2:
                await inherited(key, item, binding, "product_resource" if binding in bindings else "option_resource", quantity(multiplier * amount), path, path)
                continue
            if rid in resource_ids:
                reject("bundle_resource_cost_overlap", 409)
            resource_ids.add(rid)
            await resource({**sourcing, "resource_id": rid, "required": item.get("required", True), "repeat_policy": item.get("repeat_policy", "once")}, quantity(multiplier * amount), path + [f"resource:{rid}"], True, path[:-1] + ["product:" + key + ":" + str(vid)])
        append({"kind": "product", "product_id": key, "mezan_product_id": product.get("mezan_product_id"),
                "salla_product_id": product.get("salla_product_id"), "variant_id": vid,
                "name": product.get("name"), "track_inventory": True, "sku": (selected or product).get("sku"),
                "quantity": str(multiplier), "unit": item.get("unit", "unit"),
                "sourcing_policy": deepcopy((metadata[key].get("operation_profile") or {}).get("bundle_sourcing", {})),
                "selected_options": [{"option_id": a, "value_id": b} for a, b in sorted(selected_pairs)],
                "customization": deepcopy(local_answers) if composition_v2 else {},
                "required": item.get("required", True), "repeat_policy": item.get("repeat_policy", "once"),
                "unit_cost": str(number(cost) + option_direct) if cost is not None else None,
                "cost_source": cost_source, "direct_unit_cost": str(option_direct), **sourcing}, path)

    root_doc = await definition(root_key)
    if not root_doc or root_doc.get("product_type") != "composite":
        return None
    composition_v2 = root_doc.get("schema_version", 1) == 2
    if option_answers is not None and (not isinstance(option_answers, dict) or len(option_answers) > 100):
        reject("bundle_invalid_option_answers")
    root_product = (root_doc or {}).get("catalog_product") or root
    root_options = {str(o.get("id")): o for o in root_product.get("options", [])}
    for oid, answer in (option_answers or {}).items():
        if not isinstance(oid, str) or oid not in root_options or root_options[oid].get("values") or composition_v2 and not is_text_option(root_options[oid]):
            reject("bundle_invalid_text_option")
        if not isinstance(answer, str) or len(answer) > 2000:
            reject("bundle_invalid_option_answer")
        answers[oid] = answer.strip()
    for ids in (present_option_ids, filled_option_ids):
        if ids is not None and (not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(value, str) for value in ids)):
            reject("bundle_invalid_option_presence")
    present_option_ids = sorted(set(present_option_ids or []) | set(answers))
    filled_option_ids = sorted(set(filled_option_ids or []) | {oid for oid, answer in answers.items() if answer})
    await gather(root, [])
    needs_selection.clear()  # Validation of alternative conditions is not a sale selection.
    await walk(root, {"variant_id": variant_id, "selected_options": selected_options, "present_option_ids": present_option_ids, "filled_option_ids": filled_option_ids}, Decimal(1), [], [])
    if needs_selection and strict:
        raise HTTPException(409, detail={"code": "bundle_selection_required", "options": needs_selection})
    merged = {}
    for leaf in leaves:
        # Equal physical variants can still require different supplier work.
        # Keep stable customer selections and the frozen sourcing evidence in
        # both merge stages; otherwise the first rule silently wins for all units.
        sourcing_context = (
            tuple(sorted((str(s["option_id"]), str(s["value_id"])) for s in leaf.get("selected_options", []))),
            json.dumps(leaf.get("sourcing_policy") or {}, sort_keys=True, default=str),
        ) if composition_v2 else None
        identity = (leaf["kind"], leaf.get("product_id"), leaf.get("resource_id") or leaf.get("binding_id"), leaf.get("variant_id"), leaf.get("sourcing_mode"), leaf.get("supplier_id"), leaf.get("location_id"), leaf.get("unit"), leaf.get("slot_id"), json.dumps(leaf.get("customization", {}), sort_keys=True), sourcing_context, tuple(leaf["dedup_scopes"][0]))
        previous = merged.get(identity)
        if previous is None:
            merged[identity] = leaf
            continue
        if previous["unit_cost"] != leaf["unit_cost"]:
            reject("bundle_duplicate_cost_conflict", 409)
        same_scope = leaf["dedup_scopes"][0] in previous["dedup_scopes"]
        if not same_scope or leaf.get("repeat_policy") == "sum" or previous.get("repeat_policy") == "sum":
            previous["quantity"] = str(quantity(quantity(previous["quantity"]) + quantity(leaf["quantity"])))
            previous["repeat_policy"] = "sum"
        elif quantity(previous["quantity"]) != quantity(leaf["quantity"]):
            reject("bundle_duplicate_quantity_conflict", 409)
        previous["required"] = previous["required"] or leaf["required"]
        previous["paths"].extend(leaf["paths"])
        previous["dedup_scopes"].extend(leaf["dedup_scopes"])
    combined = {}
    for identity, leaf in merged.items():
        physical = identity[:-1]
        if physical not in combined:
            combined[physical] = leaf
        else:
            previous = combined[physical]
            if previous["unit_cost"] != leaf["unit_cost"]:
                reject("bundle_duplicate_cost_conflict", 409)
            previous["quantity"] = str(quantity(quantity(previous["quantity"]) + quantity(leaf["quantity"])))
            previous["required"] = previous["required"] or leaf["required"]
            previous["paths"].extend(leaf["paths"])
            previous["dedup_scopes"].extend(leaf["dedup_scopes"])
    leaves = list(combined.values())
    if sum((quantity(leaf["quantity"]) for leaf in leaves), Decimal(0)) > LIMITS["max_total_quantity"]:
        reject("bundle_total_quantity_limit")
    if strict and unresolved_components:
        raise HTTPException(409, detail={"code": "bundle_component_policy_unresolved", "components": unresolved_components})
    known = not unresolved_components and all(leaf["unit_cost"] is not None for leaf in leaves)
    total = sum((number(l["quantity"]) * number(l["unit_cost"]) for l in leaves if l["unit_cost"] is not None), Decimal(0))
    result = {"product_id": root_key, "revision": root_doc["revision"], "leaves": leaves,
            "estimated_cost": str(total) if known and not needs_selection else None, "known_cost": str(total),
            "cost_complete": known and not needs_selection, "provenance": provenance, "schema_version": 2 if composition_v2 else 1,
            "component_decisions": component_decisions, "unresolved_components": unresolved_components,
            "needs_selection": bool(needs_selection), "missing_options": needs_selection,
            "selected_options": deepcopy(selected_options or []),
            "present_option_ids": deepcopy(present_option_ids or []),
            "filled_option_ids": deepcopy(filled_option_ids or []),
            "option_answers": deepcopy(answers),
            "units_per_selection": str(sum((quantity(l["quantity"]) for l in leaves if l["kind"] == "product"), Decimal(0))),
            "current_product_units_per_selection": str(sum((quantity(l["quantity"]) for l in leaves if l.get("current_product_reference")), Decimal(0))),
            "frozen_definitions": {k: {**(_public(v) if v else {"product_id": k, "product_type": "normal", "revision": 0, "items": []}), "catalog_metadata": metadata[k], "catalog_product": {field: deepcopy(value) for field, value in product_snapshots[k].items() if field != "_id"}} for k, v in definitions.items()}}
    if len(json.dumps(result, default=str).encode("utf-8")) > 8_000_000:
        reject("bundle_snapshot_size_limit")
    return result


async def save_bundle(db, owner, product_id, payload, actor_id):
    if not isinstance(payload, dict) or set(payload) - {"expected_revision", "product_type", "items", "component_scopes", "schema_version"}:
        reject("bundle_invalid_payload")
    schema_version = payload.get("schema_version", 1)
    if type(schema_version) is not int or schema_version not in {1, 2}:
        reject("bundle_invalid_schema_version")
    scopes = payload.get("component_scopes", {})
    if not isinstance(scopes, dict) or len(scopes) > 500 or any(not isinstance(k, str) or not k or len(k) > 200 or not isinstance(v, str) or v not in {"per_bundle", "per_unit"} for k, v in scopes.items()):
        reject("bundle_invalid_component_scopes")
    expected = payload.get("expected_revision")
    if type(expected) is not int or expected < 0:
        reject("bundle_expected_revision_required")
    kind = payload.get("product_type")
    if kind not in {"normal", "composite"}:
        reject("bundle_invalid_product_type")
    items = payload.get("items", [])
    if not isinstance(items, list) or len(items) > LIMITS["max_items"] or (kind == "composite" and not items) or (kind == "normal" and items):
        reject("bundle_item_limit")
    await db[BUNDLES].create_index([("user_id", 1), ("product_id", 1)], unique=True)

    async def save(scoped):
        product = await _product(scoped, owner, str(product_id))
        key = _product_key(product)
        old = await _definition(scoped, owner, key)
        if (old or {}).get("revision", 0) != expected:
            reject("bundle_revision_conflict", 409)
        if expected >= LIMITS["max_revisions"]:
            reject("bundle_revision_limit", 409)
        cleaned = []
        seen = set()
        for item in items:
            if not isinstance(item, dict) or set(item) - {"kind", "product_id", "resource_id", "variant_id", "quantity", "sourcing_mode", "supplier_id", "location_id", "unit", "required", "condition", "repeat_policy", "selected_options", "slots", "component_scope", "component_overrides", "rule_id", "variant_rules", "option_mappings", "customization_mappings"}:
                reject("bundle_invalid_item")
            row = dict(item)
            for mapping_field in ("variant_rules", "option_mappings", "customization_mappings"):
                if mapping_field in row and (schema_version != 2 or row.get("kind") != "product" or not isinstance(row[mapping_field], list) or len(row[mapping_field]) > 100):
                    reject("bundle_invalid_mapping")
            if schema_version == 1 and ("component_overrides" in row or "rule_id" in row):
                reject("bundle_composition_v2_required")
            if schema_version == 2:
                row["rule_id"] = text_id(row.get("rule_id") or uuid.uuid4().hex)
                row["repeat_policy"] = "sum"
                overrides = row.get("component_overrides", [])
                if not isinstance(overrides, list) or len(overrides) > 500 or row.get("kind") == "resource" and overrides:
                    reject("bundle_invalid_component_overrides")
                override_ids = set()
                for override in overrides:
                    if not isinstance(override, dict) or set(override) != {"binding_kind", "binding_id", "use"} or override["binding_kind"] not in {"product_resource", "option_resource"} or type(override["use"]) is not bool:
                        reject("bundle_invalid_component_overrides")
                    oid = (override["binding_kind"], text_id(override["binding_id"]))
                    if not oid[1] or oid in override_ids:
                        reject("bundle_duplicate_component_override")
                    override_ids.add(oid)
            if row.get("kind") != "current_product" and "slots" in row:
                reject("bundle_slots_require_current_product")
            if row.get("repeat_policy", "once") not in {"once", "sum"}:
                reject("bundle_invalid_repeat_policy")
            if row.get("kind") not in {"product", "resource", "current_product"}:
                reject("bundle_invalid_item_kind")
            if type(row.get("required", True)) is not bool:
                reject("bundle_invalid_required")
            row["quantity"] = str(quantity(row.get("quantity", 1)))
            row["unit"] = text_id(row.get("unit", "unit"))
            row["required"] = row.get("required", True)
            row["sourcing_mode"] = row.get("sourcing_mode", "auto" if schema_version == 2 and row["kind"] in {"product", "current_product"} else "stock")
            if row["kind"] == "product":
                if row.get("resource_id"):
                    reject("bundle_ambiguous_reference")
                reference = text_id(row.get("product_id"))
                if not reference:
                    reject("bundle_reference_required")
                row["product_id"] = _product_key(await _product(scoped, owner, reference))
            elif row["kind"] == "current_product":
                if row.get("product_id") or row.get("resource_id"):
                    reject("bundle_current_reference_must_be_implicit")
                count = quantity(row["quantity"])
                if count != count.to_integral_value() or count > LIMITS["max_items"]:
                    reject("bundle_current_product_quantity_limit")
            else:
                if row.get("product_id") or row.get("variant_id"):
                    reject("bundle_ambiguous_reference")
                row["resource_id"] = text_id(row.get("resource_id"))
                if not row["resource_id"]:
                    reject("bundle_reference_required")
            identity = (row["kind"], row.get("product_id") or row.get("resource_id"), row.get("variant_id"), row.get("supplier_id"), row.get("location_id"), str(row.get("condition")))
            if schema_version == 2:
                identity = row["rule_id"]
            if identity in seen:
                reject("bundle_duplicate_item")
            seen.add(identity)
            cleaned.append(row)
        doc = {"user_id": owner, "product_id": key, "revision": expected + 1,
               "schema_version": schema_version,
               "product_type": kind, "items": cleaned, "component_scopes": deepcopy(payload.get("component_scopes", {})), "updated_by": actor_id,
               "updated_at": datetime.now(timezone.utc).isoformat()}
        expansion = await expand_bundle(scoped, owner, key, _overrides={key: doc}, strict=False)
        history = deepcopy((old or {}).get("history", []))
        if old:
            history.append(_public(old))
        doc["history"] = history
        if len(json.dumps(doc, default=str).encode("utf-8")) > 12_000_000:
            reject("bundle_definition_size_limit")
        if old:
            await scoped[BUNDLES].update_one({"user_id": owner, "product_id": key, "revision": expected}, {"$set": doc})
        else:
            await scoped[BUNDLES].insert_one(doc)
        return {"definition": _public(doc), "expansion": expansion, "limits": LIMITS}
    return await operational_owner(db, owner, save, profile="bundle_catalog")


async def bundle_scope_resources(db, owner, key):
    query = {"user_id": owner, "salla_product_id": key}
    product_links = await db[PRODUCT_RESOURCE_BINDINGS].find(query, {"_id": 0}).to_list(length=501)
    option_links = await db[BINDINGS].find(query, {"_id": 0}).to_list(length=501)
    ids = sorted({row["resource_id"] for row in product_links + option_links if row.get("resource_id")})
    resources = []
    for rid in ids:
        row = await db[RESOURCES].find_one({"user_id": owner, "id": rid}, {"_id": 0, "name": 1}) or {}
        resources.append({"resource_id": rid, "name": row.get("name") or rid})
    return resources


async def annotate_inventory(db, owner, expansion):
    """Decorate editor responses without pinning locations or reserving stock."""
    if expansion is None:
        return None
    from bundle_fulfillment_v2 import inventory_lots, identity, allocate_leaves
    from purchase_receiving_service import same_identity
    from product_inventory_rules import specifications_are_exact
    result = deepcopy(expansion)
    lots = await inventory_lots(db, owner, [], pin=False)
    suppliers = {}
    for leaf in result.get("leaves", []):
        supplier_id = leaf.get("supplier_id")
        if supplier_id and supplier_id not in suppliers:
            suppliers[supplier_id] = await db["mezan_suppliers_v2"].find_one(
                {"user_id": owner, "id": supplier_id},
                {"_id": 0, "name": 1, "company_name": 1}) or {}
        supplier = suppliers.get(supplier_id, {})
        leaf["supplier_name"] = supplier.get("company_name") or supplier.get("name") or None
        if leaf.get("cost_only") or (leaf.get("kind") == "resource" and leaf.get("track_inventory") is False):
            leaf["available_quantity"] = None
            continue
        wanted = identity(leaf)
        amount = sum((lot["available"] for lot in lots
                      if same_identity(lot["item"], wanted)
                      and (not leaf.get("customization") or specifications_are_exact(lot["item"].get("specifications"), leaf["customization"]))
                      and (not leaf.get("location_id") or leaf["location_id"] == lot["location_id"])), Decimal(0))
        leaf["available_quantity"] = str(amount)
    if result.get("schema_version") == 2:
        # The allocator mutates only its detached lot list. Never pin locations,
        # persist units, or invoke purchasing from an editor preview.
        projected, _ = await allocate_leaves(db, owner, result.get("leaves", []), deepcopy(lots),
                                             unit_id="bundle-preview:" + str(result.get("product_id", "")))
        result["leaves"] = projected
        result["sourcing_preview"] = True
        for leaf in projected:
            leaf["preview_only"] = True
            leaf["projected_reserved_quantity"] = leaf.get("reserved_quantity", "0")
            leaf["pending_quantity"] = str(max(Decimal(0), quantity(leaf["quantity"]) - number(leaf.get("reserved_quantity", 0)))) if not leaf.get("cost_only") and leaf.get("track_inventory") is not False else "0"
            supplier_id = leaf.get("supplier_id")
            if supplier_id and supplier_id not in suppliers:
                suppliers[supplier_id] = await db["mezan_suppliers_v2"].find_one(
                    {"user_id": owner, "id": supplier_id}, {"_id": 0, "name": 1, "company_name": 1}) or {}
            supplier = suppliers.get(supplier_id, {})
            leaf["supplier_name"] = supplier.get("company_name") or supplier.get("name") or None
    return result


def make_bundle_router(db, current_user):
    router = APIRouter(prefix="/products-v2", tags=["Product V2 Bundles"])
    from product_bundle_policy_routes import register_bundle_policy_routes
    register_bundle_policy_routes(router, db, current_user)

    @router.get("/{product_id}/bundle-components")
    async def get_bundle_components(product_id: str, user=Depends(current_user)):
        owner = await require_product_permission(db, user, "products.cost.read")
        product = await _product(db, owner, product_id)
        key = _product_key(product)
        query = {"user_id": owner, "salla_product_id": key}
        profile = await db[COST_PROFILES].find_one(query, {"_id": 0}) or {}
        components = []
        for kind, collection in (("product_resource", PRODUCT_RESOURCE_BINDINGS), ("option_resource", BINDINGS)):
            for binding in await db[collection].find(query, {"_id": 0}).to_list(length=501):
                if not binding.get("resource_id"):
                    continue
                resource_row = await db[RESOURCES].find_one({"user_id": owner, "id": binding["resource_id"]}, {"_id": 0}) or {}
                bid = str(binding.get("id") or "")
                policy = (profile.get("composition_components") or {}).get(kind + ":" + bid, {})
                components.append({"binding_kind": kind, "binding_id": bid, "resource_id": binding["resource_id"],
                    "name": resource_row.get("name"), "quantity": binding.get("quantity", 1), "unit": resource_row.get("unit"),
                    "unit_cost": _current_cost(resource_row), "policy": deepcopy(policy),
                    "condition": {"option_id": binding.get("option_id"), "value_id": binding.get("value_id")} if kind == "option_resource" else None})
        return {"product_id": key, "components": components}

    @router.get("/{product_id}/bundle")
    async def get_bundle(product_id: str, user=Depends(current_user)):
        owner = await require_product_permission(db, user, "products.cost.read")
        product = await _product(db, owner, product_id)
        key = _product_key(product)
        doc = await _definition(db, owner, key)
        return {"definition": _public(doc) if doc else {"product_id": key, "revision": 0, "product_type": "normal", "items": []},
                "expansion": await annotate_inventory(db, owner, await expand_bundle(db, owner, key, strict=False)), "scope_resources": await bundle_scope_resources(db, owner, key), "limits": LIMITS}

    @router.post("/{product_id}/bundle/preview")
    async def preview_bundle(product_id: str, payload: dict = Body(...), user=Depends(current_user)):
        owner = await require_product_permission(db, user, "products.cost.read")
        if set(payload) - {"selected_options", "variant_id", "present_option_ids", "filled_option_ids", "option_answers"}:
            reject("bundle_invalid_payload")
        return {"expansion": await annotate_inventory(db, owner, await expand_bundle(db, owner, product_id, selected_options=payload.get("selected_options"), variant_id=payload.get("variant_id"), present_option_ids=payload.get("present_option_ids"), filled_option_ids=payload.get("filled_option_ids"), option_answers=payload.get("option_answers"), strict=False))}

    @router.put("/{product_id}/bundle")
    async def put_bundle(product_id: str, payload: dict = Body(...), user=Depends(current_user)):
        owner = await require_product_permission(db, user, "products.cost.write")
        result = await save_bundle(db, owner, product_id, payload, user.get("_mobile_actor_id") or user["id"])
        result["expansion"] = await annotate_inventory(db, owner, result["expansion"])
        result["scope_resources"] = await bundle_scope_resources(db, owner, result["definition"]["product_id"])
        return result
    return router

