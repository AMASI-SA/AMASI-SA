"""One read-only cost authority for order profitability, always expressed in SAR.

Catalog costs are estimates for legacy ordinary lines with no historical proof.
Frozen bundle composition replaces (never augments) that catalog estimate.
An explicit bundle-only financial base policy can include a missing parent
cost without creating a physical ``current_product`` or changing fulfillment.
Reads never reserve stock, rewrite orders, create snapshots, or post journals.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any

VERSION = "order-cost-authority-v2"
LIFECYCLES = "mezan_component_order_lifecycle_v1"
SNAPSHOTS = "mezan_order_item_cost_snapshots_v2"
CORRECTIONS = "mezan_order_cost_corrections_v1"
CHUNK_SIZE = 250


async def ensure_order_cost_indexes(db: Any) -> None:
    """Prepare once during startup; report reads never create indexes."""
    await db[LIFECYCLES].create_index([("user_id", 1), ("order_number", 1)],
                                     name="ix_order_cost_lifecycle_owner_order")
    await db[CORRECTIONS].create_index(
        [("user_id", 1), ("order_number", 1), ("order_item_id", 1), ("state", 1)],
        name="ix_order_cost_correction_owner_line")
    from order_option_cost_snapshot_routes import ensure_indexes
    await ensure_indexes(db)


def decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _amount(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def item_dict(item: Any) -> dict:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="python")
    return vars(item)


def line_id(item: Any) -> str:
    row = item_dict(item)
    return str(row.get("canonical_order_item_id") or row.get("order_item_id") or row.get("order_line_id") or
               row.get("source_item_id") or row.get("id") or "").strip()


def line_aliases(item: Any) -> set[str]:
    row = item_dict(item)
    raw = row.get("raw_item") if isinstance(row.get("raw_item"), dict) else {}
    # These aliases are recorded by Salla's mapper/projector. Never match SKU.
    return {str(value).strip() for value in (line_id(row), row.get("order_item_id"),
            row.get("order_line_id"), row.get("source_item_id"), raw.get("id")) if value not in (None, "")}


def economic_item(item: Any) -> dict:
    """Recover provider option IDs retained by the canonical compatibility view."""
    row = deepcopy(item_dict(item))
    raw = row.get("raw_item") if isinstance(row.get("raw_item"), dict) else {}
    # The canonical/projected lists already include every provider source
    # (options, customer_options, product.options, etc.). A single raw alias
    # would silently discard the other selections.
    options = row.get("options_raw") or row.get("options") or row.get("selected_options") or raw.get("options") or []
    row["options_raw"] = [deepcopy(option.get("raw") or option) for option in options if isinstance(option, dict)]
    row["custom_fields"] = [deepcopy(field.get("raw") or field)
                            for field in row.get("custom_fields") or raw.get("custom_fields") or [] if isinstance(field, dict)]
    return row


def _economic_tokens(item: dict) -> list[list[str]]:
    from stock_component_consumption_service import selection_tokens
    tokens = item.get("selected_context")
    if tokens is None:
        tokens = selection_tokens(economic_item(item))
    named_ids = {key[len("option-name:"):] for key, _ in tokens if key.startswith("option-name:")}
    result = []
    for key, value in tokens:
        if key.startswith("option-name:") or key == "option":
            continue
        if key.startswith("name:") and key[len("name:"):] in named_ids:
            continue
        # Filled is implied by a specific value ID, and display labels are not
        # identity when a provider ID exists.
        if key.startswith("id:") and value == "id:filled" and any(k == key and v.startswith("id:") and v != value for k, v in tokens):
            continue
        result.append([key, value])
    return sorted(result)


def order_items(order: dict) -> list[dict]:
    return [item_dict(item) for item in (order.get("products") or order.get("items") or [])
            if isinstance(item, dict) or hasattr(item, "model_dump") or hasattr(item, "__dict__")]


def line_fingerprint(item: Any) -> str:
    row = item_dict(item)
    quantity = decimal(row.get("quantity", 1))
    raw = row.get("raw_item") if isinstance(row.get("raw_item"), dict) else {}
    identity = str(row.get("source_item_id") or raw.get("id") or line_id(row))
    if identity.startswith("salla:") and identity.count(":") == 2:
        identity = identity.split(":", 2)[2]
    payload = {
        "line_id": identity,
        "product_id": str(row.get("parent_product_id") or row.get("product_id") or row.get("salla_product_id") or ""),
        "variant_id": str(row.get("variant_id") or ""),
        "quantity": str(quantity.normalize()) if quantity is not None else None,
        "options": _economic_tokens(row),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _sar(value: Any, evidence: dict | None) -> Decimal | None:
    """Only cost currency matters; a QAR/KWD sale does not revalue SAR costs."""
    amount = decimal(value)
    if amount is None:
        return None
    evidence = evidence or {}
    currency = str(evidence.get("cost_currency") or "SAR").upper()
    if currency == "SAR":
        return amount
    rate = decimal(evidence.get("cost_fx_rate_to_sar"))
    if rate is None or rate == 0:
        return None
    return amount * rate


def _legacy_service_aliases(product, bindings, decisions, selection_evidence, resources, profile):
    """Collapse one proven legacy field alias of a selected service choice.

    Shared resource identity alone is insufficient: independent options can
    legitimately charge the same service twice. The legacy field must be shown
    only for this exact parent choice, with the same service and quantity. Both
    rules must actually apply, and multiple possible legacy fields are ambiguous.
    """
    selected = {}
    for index, binding in enumerate(bindings):
        identity = str(binding.get("id") or "")
        resource_id = str(binding.get("resource_id") or "")
        quantity = decimal(binding.get("quantity", 1))
        policy = ((profile or {}).get("composition_components") or {}).get("option_resource:" + identity) or {}
        if (not identity or identity in selected or decisions[index] is not True
                or binding.get("mode") != "resource"
                or (resources.get(resource_id) or {}).get("kind") != "service"
                or quantity is None or quantity <= 0
                or policy.get("cost_treatment") == "included_in_base"
                or binding.get("cost_treatment") == "included_in_base"):
            continue
        selected[identity] = (binding, resource_id, quantity)
    legacy = {identity: row for identity, row in selected.items()
              if str(row[0].get("value_id") or "") == "__option__"}
    if not legacy:
        return set()
    from report_option_identity import _option_visibility, catalog_options
    catalog = catalog_options(product or {})
    resolved = defaultdict(set)
    for row in selection_evidence:
        resolved[row["option_id"]].add(row["value_id"])
    candidates = defaultdict(set)
    for identity, (binding, resource_id, quantity) in legacy.items():
        option_id = str(binding.get("option_id") or "")
        option = catalog.get(option_id) or {}
        if (option.get("visibility") != "on_condition" or option.get("visibility_condition_type") != "="
                or option.get("type") not in {"text", "textarea", "long_text"} or option.get("values")
                or _option_visibility(option_id, catalog, resolved, set(resolved), set()) is not True):
            continue
        parent_id = str(option.get("visibility_condition_option") or "")
        value_id = str(option.get("visibility_condition_value") or "")
        parents = [key for key, (parent, resource, count) in selected.items()
                   if key != identity and resource == resource_id and count == quantity
                   and str(parent.get("option_id") or "") == parent_id
                   and str(parent.get("value_id") or "") == value_id]
        if len(parents) == 1:
            candidates[parents[0]].add(identity)
    return {next(iter(aliases)) for aliases in candidates.values() if len(aliases) == 1}


def calculate_catalog_line_cost(
    item: dict, *, product: dict | None, profile: dict | None,
    product_bindings: list[dict], option_bindings: list[dict],
    resources: dict[str, dict], current_report: bool = False,
) -> dict:
    """Base + selected, independent resources/options, with one quantity pass."""
    from order_option_cost_snapshot_routes import classify_base_unit_cost
    from report_option_recovery import report_cost_item
    row = economic_item(report_cost_item(item) if current_report else item)
    quantity = decimal(row.get("quantity", 1))
    valid_quantity = quantity is not None and quantity > 0
    quantity = quantity if valid_quantity else Decimal(0)
    status = classify_base_unit_cost(row, profile, product)
    evidence = profile if status["source"].startswith("mezan_") else product
    base = _sar(status["unit_cost"], evidence)
    complete = base is not None and valid_quantity
    missing: list[str] = []
    if base is None:
        missing.append("base_cost_or_fx_missing")
    if not valid_quantity:
        missing.append("invalid_quantity")
    totals = {"product": Decimal(0), "option": Decimal(0)}
    applied = {"product": [], "option": []}
    details = [{"key": "base", "label": (product or {}).get("name") or row.get("name") or "المنتج",
                "kind": "product", "status": "costed" if base is not None else "missing_cost",
                "unit_cost": _amount(base) if base is not None else None}]
    from report_option_identity import resolve_report_choices
    decisions, selection_evidence = resolve_report_choices(row, product, option_bindings)
    legacy_aliases = _legacy_service_aliases(product, option_bindings, decisions, selection_evidence, resources, profile)
    seen: set[tuple[str, str]] = set()
    for kind, bindings in (("product", product_bindings), ("option", option_bindings)):
        for index, binding in enumerate(bindings):
            binding_id = str(binding.get("id") or "")
            if binding_id and (kind, binding_id) in seen:
                continue
            if binding_id:
                seen.add((kind, binding_id))
            if kind == "option" and binding_id in legacy_aliases:
                # Its uniquely matched parent rule provides this same service.
                # Keep one monetary entry and one detail cell for that charge.
                continue
            resource = resources.get(str(binding.get("resource_id") or "")) or {}
            cell = {"key": kind + ":" + (binding_id or str(index)),
                    "label": resource.get("name") or binding.get("name") or binding.get("option_name") or binding.get("resource_id") or "بند تكلفة",
                    "kind": "service" if resource.get("kind") == "service" or kind == "option" and binding.get("mode") != "resource" else "component",
                    # Presentation counts resources, while independent binding
                    # rules and their monetary amounts remain separate.
                    "resource_id": binding.get("resource_id") if kind == "product" or binding.get("mode") == "resource" else None,
                    "status": "costed", "unit_cost": None}
            details.append(cell)
            policy_key = ("product_resource" if kind == "product" else "option_resource") + ":" + binding_id
            policy = ((profile or {}).get("composition_components") or {}).get(policy_key) or {}
            if policy.get("cost_treatment") == "included_in_base" or binding.get("cost_treatment") == "included_in_base":
                cell["status"] = "included_in_base"
                continue
            if kind == "option" and decisions[index] is not True:
                cell["status"] = "unresolved" if decisions[index] is None else "not_required"
                if decisions[index] is None:
                    complete = False
                    missing.append("option_selection_unresolved:" + str(binding.get("id") or binding.get("option_id") or "unknown"))
                continue
            # Separate binding kinds can legitimately reference the same resource.
            # Deduplicate only identical source identities, never a resource SKU.
            multiplier = decimal(binding.get("quantity", 1))
            resource = resources.get(str(binding.get("resource_id") or "")) or {}
            if kind == "product" or binding.get("mode") == "resource":
                unit = _sar(resource.get("unit_cost"), resource)
            else:
                unit = _sar(binding.get("direct_amount"), binding)
            if unit is None or multiplier is None or multiplier <= 0:
                cell["status"] = "missing_cost"
                complete = False
                missing.append("component_cost_or_fx_missing:" + (binding_id or str(binding.get("resource_id") or "unknown")))
                continue
            amount = unit * multiplier
            cell["unit_cost"] = _amount(amount)
            totals[kind] += amount
            applied[kind].append({"binding_id": binding_id, "resource_id": binding.get("resource_id"),
                "quantity": str(multiplier), "unit_cost_sar": str(unit), "resolved_amount": str(amount)})
    base_total = (base or Decimal(0)) * quantity
    product_total, option_total = totals["product"] * quantity, totals["option"] * quantity
    return {
        "quantity": float(quantity), "currency": "SAR", "cost_currency": "SAR",
        "base_cost_source": status["source"], "base_complete": complete,
        "calculation_cost_available": complete,
        "calculation_cost_source": status["source"],
        "mezan_cost_complete": status["mezan_cost_complete"] and complete,
        "mezan_cost_missing": not (status["mezan_cost_complete"] and complete),
        "uses_salla_fallback": status["uses_salla_fallback"],
        "cost_semantics_version": VERSION,
        "base_total": _amount(base_total), "product_components_total": _amount(product_total),
        "selected_options_total": _amount(option_total), "components_total": _amount(product_total + option_total),
        "line_total": _amount(base_total + product_total + option_total),
        "applied_product_resources": applied["product"], "applied_option_costs": applied["option"],
        "resolved_option_selections": selection_evidence,
        "cost_authority": "catalog_estimate", "cost_stage": "expected",
        "historical_cost_verified": False, "missing_cost_reasons": missing,
        "cost_detail_cells": details,
    }


def _unknown(quantity: Any, reason: str) -> dict:
    q = decimal(quantity) or Decimal(0)
    return {
        "quantity": float(q), "currency": "SAR", "cost_currency": "SAR",
        "base_cost_source": "missing", "base_complete": False,
        "calculation_cost_available": False, "calculation_cost_source": "missing",
        "mezan_cost_complete": False, "mezan_cost_missing": True, "uses_salla_fallback": False,
        "cost_semantics_version": VERSION, "base_total": 0.0,
        "product_components_total": 0.0, "selected_options_total": 0.0,
        "components_total": 0.0, "line_total": 0.0,
        "cost_authority": "unresolved_snapshot", "cost_stage": "unknown",
        "historical_cost_verified": False, "missing_cost_reasons": [reason],
    }


def _frozen_cost(cost: dict, *, authority: str, stage: str = "expected") -> dict:
    result = deepcopy(cost)
    result.update(cost_authority=authority, cost_stage=stage,
                  historical_cost_verified=True, cost_semantics_version=VERSION,
                  currency="SAR", cost_currency="SAR")
    result["calculation_cost_source"] = ("bundle_" + stage) if authority == "bundle_snapshot" else authority
    result["base_cost_source"] = authority
    return result


def _bundle_detail_cells(rows: list[dict], expected: Decimal) -> list[dict]:
    """Use frozen SAR evidence, never current prices, for displayed leaf costs."""
    cells = []
    for index, leaf in enumerate(rows):
        amount = decimal(leaf.get("line_cost_sar"))
        if amount is None:
            return []
        identity = str(leaf.get("leaf_id") or leaf.get("binding_id") or leaf.get("resource_id") or leaf.get("product_id") or index)
        kind = "service" if leaf.get("kind") == "direct_cost" else "product" if leaf.get("kind") == "product" else "component"
        cells.append({"key": "frozen:" + identity + ":" + str(index),
            "label": leaf.get("name") or leaf.get("resource_name") or leaf.get("product_name") or identity,
            "kind": kind, "status": "costed", "unit_cost": float(amount),
            "resource_id": leaf.get("resource_id"), "product_id": leaf.get("product_id")})
    if sum((decimal(c["unit_cost"]) for c in cells), Decimal(0)) != expected:
        return []
    return cells


def _bundle_cost(snapshot: dict, quantity: Any, units: list[dict] | None = None) -> dict:
    q = decimal(quantity)
    if q is None or q <= 0:
        return _unknown(quantity, "bundle_quantity_unresolved")
    if snapshot.get("historical_cost_verified") is False:
        return _unknown(quantity, "historical_bundle_cost_unproven")
    if "reporting_cost" in snapshot:
        from bundle_reporting_cost import verified_reporting_cost, policy_applies_to_sale
        report = verified_reporting_cost(snapshot)
        if report is None:
            return _unknown(quantity, "bundle_reporting_cost_incomplete")
        if "order_created_at" in snapshot and not policy_applies_to_sale(report, snapshot["order_created_at"]):
            return _unknown(quantity, "historical_bundle_cost_unproven")
        # The added base is captured expected evidence, not a stock allocation.
        # Consuming the old leaves cannot remove it or prove a full actual cost.
        # Supplier invoices and their posting workflow remain independent.
        if units is not None:
            indexes = [unit.get("unit_index") for unit in units]
            if Decimal(len(units)) != q or len(set(indexes)) != len(indexes):
                return _unknown(quantity, "bundle_unit_quantity_mismatch")
        base, components, total = (decimal(report[key]) * q for key in (
            "base_total_sar", "components_total_sar", "unit_total_sar"))
        fallback = report.get("uses_salla_fallback", False)
        result = _unknown(quantity, "")
        result.update(base_complete=True, calculation_cost_available=True,
            mezan_cost_complete=not fallback, mezan_cost_missing=fallback,
            uses_salla_fallback=fallback, missing_cost_reasons=[],
            base_total=_amount(base), components_total=_amount(components),
            product_components_total=_amount(components), line_total=_amount(total),
            bundle_revision=snapshot.get("revision"),
            bundle_reporting_evidence_hash=report["evidence_hash"])
        result["cost_detail_cells"] = _bundle_detail_cells(
            [{**r, "kind": "product"} for r in report.get("added_bases") or []]
            + list(report.get("components") or []), total / q)
        return _frozen_cost(result, authority="bundle_snapshot", stage="expected")
    estimate = decimal(snapshot.get("estimated_cost"))
    stage, total = "expected", estimate * q if estimate is not None else None
    if units is not None:
        indexes = [unit.get("unit_index") for unit in units]
        if Decimal(len(units)) != q or len(set(indexes)) != len(indexes):
            return _unknown(quantity, "bundle_unit_quantity_mismatch")
        leaves = [leaf for unit in units for leaf in unit.get("bundle_leaves") or []]
        actual = [decimal(leaf.get("actual_cost")) for leaf in leaves]
        # Actual is valid only for a complete consumption of every physical unit.
        coverage = all(_actual_leaves_cover_snapshot(snapshot, unit) for unit in units)
        if leaves and coverage and all(unit.get("state") == "consumed" for unit in units) and all(value is not None for value in actual):
            total = sum(actual, Decimal(0))
            stage = "actual"
    if total is None:
        return _unknown(quantity, "bundle_cost_incomplete")
    fallback = stage != "actual" and any(str(leaf.get("cost_source", "")).startswith("salla_")
                                         for leaf in snapshot.get("leaves", []))
    result = _unknown(quantity, "")
    result.update(base_complete=True, calculation_cost_available=True,
                  mezan_cost_complete=not fallback, mezan_cost_missing=fallback,
                  uses_salla_fallback=fallback, missing_cost_reasons=[],
                  components_total=_amount(total), product_components_total=_amount(total),
                  line_total=_amount(total), bundle_revision=snapshot.get("revision"))
    if stage == "actual":
        # Keep each consumed physical unit's frozen amounts; averaging here
        # would hide differences in inventory consumption costs.
        from bundle_fulfillment_v2 import leaf_key
        detail_units = []
        for unit in units:
            unit_leaves = sorted(unit.get("bundle_leaves") or [], key=leaf_key)
            unit_total = sum((decimal(leaf["actual_cost"]) for leaf in unit_leaves), Decimal(0))
            cells = _bundle_detail_cells(
                [{**leaf, "line_cost_sar": str(leaf["actual_cost"])} for leaf in unit_leaves], unit_total)
            detail_units.append({"quantity": 1, "line_total": str(unit_total), "cost_detail_cells": cells})
        if all(entry["cost_detail_cells"] for entry in detail_units):
            result["cost_detail_units"] = detail_units
    if stage == "expected":
        leaves = []
        for leaf in snapshot.get("leaves") or []:
            unit, count = decimal(leaf.get("unit_cost")), decimal(leaf.get("quantity"))
            leaves.append({**leaf, "line_cost_sar": str(unit * count) if unit is not None and count is not None else None})
        result["cost_detail_cells"] = _bundle_detail_cells(leaves, total / q)
    return _frozen_cost(result, authority="bundle_snapshot", stage=stage)


def _actual_leaves_cover_snapshot(snapshot: dict, unit: dict) -> bool:
    """A consumed unit must cover the frozen recipe, with no duplicate leaves.

    Sourcing segments already roll into their parent actual_cost. They are not
    additional recipe leaves and must never be counted a second time.
    """
    from bundle_fulfillment_v2 import leaf_key
    expected = snapshot.get("leaves") or []
    actual = unit.get("bundle_leaves") or []
    if not expected:
        # Old plans stored only the estimate. Keep their existing consumption
        # authority; new snapshots always retain the expanded recipe.
        return bool(actual)
    expected_keys = [leaf_key(leaf) for leaf in expected]
    actual_keys = [leaf_key(leaf) for leaf in actual]
    if (len(set(expected_keys)) != len(expected_keys)
            or len(set(actual_keys)) != len(actual_keys)
            or set(expected_keys) != set(actual_keys)):
        return False
    actual_by_key = dict(zip(actual_keys, actual))
    identity_fields = ("kind", "product_id", "salla_product_id", "mezan_product_id", "resource_id", "variant_id")
    for expected_leaf, key in zip(expected, expected_keys):
        leaf = actual_by_key[key]
        if any(str(expected_leaf.get(field) or "") != str(leaf.get(field) or "") for field in identity_fields):
            return False
        expected_quantity = decimal(expected_leaf.get("quantity"))
        if expected_quantity is None or decimal(leaf.get("quantity")) != expected_quantity:
            return False
        required = expected_leaf.get("required", True)
        if leaf.get("state") != "consumed":
            if not required and leaf.get("state") == "skipped" and decimal(leaf.get("actual_cost")) == 0:
                continue
            return False
        cost_only = (expected_leaf.get("cost_only")
                     or expected_leaf.get("kind") == "resource" and expected_leaf.get("track_inventory") is False)
        consumed = decimal(leaf.get("consumed_quantity"))
        if required and not cost_only and consumed != expected_quantity:
            return False
        if consumed is not None and consumed > expected_quantity:
            return False
    return True


def _stored_cost(row: dict) -> dict:
    if isinstance(row.get("cost"), dict):
        result = _frozen_cost(row["cost"], authority=row["cost"].get("cost_authority") or "order_snapshot",
                              stage=row["cost"].get("cost_stage", "expected"))
        # Persistence makes an estimate immutable, not retroactively historical.
        # Preserve explicit capture provenance on every subsequent read.
        if row["cost"].get("historical_cost_verified") is False:
            result["historical_cost_verified"] = False
        return result
    q, total = decimal(row.get("quantity")), decimal(row.get("line_cost"))
    if q is None or total is None or str(row.get("currency") or "SAR").upper() != "SAR":
        return _unknown(row.get("quantity"), "stored_cost_invalid")
    # Existing stored values are already whole-line costs; do not multiply again.
    result = _unknown(q, "")
    base = (decimal(row.get("base_unit_cost")) or Decimal(0)) * q
    product = (decimal(row.get("product_resource_unit_cost")) or Decimal(0)) * q
    option = (decimal(row.get("option_unit_cost")) or Decimal(0)) * q
    complete = bool(row.get("calculation_cost_available", row.get("cost_complete", True)))
    fallback = bool(row.get("uses_salla_fallback"))
    result.update(base_total=_amount(base), product_components_total=_amount(product),
        selected_options_total=_amount(option), components_total=_amount(total-base), line_total=_amount(total),
        base_complete=complete, calculation_cost_available=complete,
        mezan_cost_complete=bool(row.get("mezan_cost_complete", complete and not fallback)),
        mezan_cost_missing=bool(row.get("mezan_cost_missing", not complete or fallback)),
        uses_salla_fallback=fallback, missing_cost_reasons=[] if complete else ["stored_cost_incomplete"])
    return _frozen_cost(result, authority="order_snapshot", stage=row.get("cost_stage", "expected"))


def _record(item: dict, cost: dict, **extra: Any) -> dict:
    return {"line_fingerprint": line_fingerprint(item), "quantity": item.get("quantity", 1),
            "product_id": str(item.get("parent_product_id") or item.get("product_id") or item.get("salla_product_id") or ""),
            "cost": deepcopy(cost), **extra}


async def _read(db: Any, collection: str, query: dict, projection: dict, limit: int) -> list[dict]:
    rows = await db[collection].find(query, projection).to_list(length=limit + 1)
    if len(rows) > limit:
        # Truncation cannot silently turn missing components into zero cost.
        raise ValueError("order_cost_authority_read_limit:" + collection)
    return rows


def _compact_snapshot_map(field: str) -> dict:
    # Dynamic line IDs preclude ordinary dotted projections. Remove large
    # frozen catalog definitions inside Mongo, before transferring the cohort.
    # Keep every other snapshot field, including leaf identities and evidence.
    return {"$arrayToObject": {"$map": {
        "input": {"$objectToArray": {"$ifNull": ["$" + field, {}]}},
        "as": "entry", "in": {"k": "$$entry.k", "v": {"$cond": [
            {"$eq": ["$$entry.v", None]}, None,
            {"$arrayToObject": {"$filter": {
                "input": {"$objectToArray": "$$entry.v"}, "as": "part",
                "cond": {"$ne": ["$$part.k", "frozen_definitions"]},
            }}},
        ]}},
    }}}


async def _read_snapshots(db: Any, collection: str, query: dict, projection: dict,
                          field: str, limit: int, *, full: bool) -> list[dict]:
    if full:
        return await _read(db, collection, query, projection, limit)
    rows = await db[collection].aggregate([
        {"$match": query}, {"$limit": limit + 1},
        {"$project": {**projection, field: _compact_snapshot_map(field)}},
    ]).to_list(length=limit + 1)
    if len(rows) > limit:
        raise ValueError("order_cost_authority_read_limit:" + collection)
    return rows


def _request_parallel_reads(db: Any, user_id: str, enabled: bool) -> bool:
    if enabled is not True:
        return False
    from motor.motor_asyncio import AsyncIOMotorDatabase
    from operational_demand_refresh import demand_context
    context = demand_context()
    # A missing public session attribute is not proof: restricted wrappers can
    # hide their snapshot session in slots/private fields. Only the exact raw
    # database owned by this visible refresh may opt in. All wrappers and other
    # callers retain their serial behavior.
    allowed = (type(db) is AsyncIOMotorDatabase and context is not None
               and context.db is db and context.owner == user_id)
    if allowed:
        context.require_connected()
    return allowed


async def _parallel_inputs(readers):
    """Three request-owned reads; preserve result/error order and join cleanup."""
    tasks = [asyncio.create_task(reader()) for reader in readers]
    try:
        # Await in the old serial order, including when a later read fails
        # first. Completion order must not change the error exposed to callers.
        return [await asyncio.shield(task) for task in tasks]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        settled = asyncio.gather(*tasks, return_exceptions=True)
        cancelled = False
        while not settled.done():
            try:
                await asyncio.shield(settled)
            except asyncio.CancelledError:
                cancelled = True
        settled.result()
        if cancelled:
            raise asyncio.CancelledError


async def load_order_cost_authority(db: Any, user_id: str, orders: list[dict], *, budget: Any = None,
                                    include_corrections: bool = True,
                                    include_frozen_metadata: bool = False,
                                    current_profitability_estimate: bool | None = None,
                                    parallel_reads: bool = False) -> dict:
    """Bounded, tenant-scoped batches, never one DB query per order/component."""
    from bundle_catalog_v2 import BUNDLES
    from stock_component_consumption_service import PLANS, UNITS
    result: dict[str, Any] = {"lines": {}, "composite_products": set(), "read_batches": 0}
    order_map = {str(order.get("order_number") or ""): order for order in orders if order.get("order_number")}
    if not order_map:
        return result
    parallel_reads = _request_parallel_reads(db, user_id, parallel_reads)
    # This small metadata read is tenant scoped. It only guards unknown bundles;
    # current catalog composition is never used as historical price evidence.
    definitions = await _read(db, BUNDLES, {"user_id": user_id, "product_type": "composite"},
                              {"_id": 0, "product_id": 1}, 100000)
    result["composite_products"] = {str(row["product_id"]) for row in definitions}
    numbers = sorted(order_map)
    for offset in range(0, len(numbers), CHUNK_SIZE):
        selected = numbers[offset:offset + CHUNK_SIZE]
        keys = {"user_id": user_id, "order_number": {"$in": selected}}
        limit = max(1, sum(len(order_items(order_map[number])) for number in selected))
        aliases = {alias for number in selected for item in order_items(order_map[number]) for alias in line_aliases(item)}
        async def read_stored():
            return await _read(db, SNAPSHOTS, {**keys, "order_item_id": {"$in": sorted(aliases)}}, {"_id": 0}, max(1, limit * 4))

        async def read_lifecycle():
            return await _read_snapshots(db, LIFECYCLES, keys,
                {"_id": 0, "order_number": 1, "bundle_intake_snapshots": 1,
                 "reporting_cost_snapshots": 1, "source_fingerprint": 1}, "bundle_intake_snapshots", len(selected), full=include_frozen_metadata)

        async def read_plans():
            return await _read_snapshots(db, PLANS, {"user_id": user_id, "order_id": {"$in": selected},
                "bundle_snapshots": {"$exists": True}},
                {"_id": 1, "order_id": 1, "bundle_snapshots": 1, "reconciliation_required": 1, "lines": 1},
                "bundle_snapshots", len(selected), full=include_frozen_metadata)

        if parallel_reads:
            stored, lifecycle, plans = await _parallel_inputs((read_stored, read_lifecycle, read_plans))
        else:
            stored, lifecycle, plans = await read_stored(), await read_lifecycle(), await read_plans()
        units = await _read(db, UNITS, {"user_id": user_id, "plan_id": {"$in": [p["_id"] for p in plans]},
            "state": {"$ne": "released"}, "order_line_id": {"$in": sorted(aliases)}}, {"_id": 0, "plan_id": 1, "order_line_id": 1,
            "unit_index": 1, "state": 1, "selected_context": 1, "bundle_leaves": 1},
            max(10000, sum(int(decimal(item.get("quantity", 1)) or 0) for number in selected for item in order_items(order_map[number]))) + limit) if plans else []
        corrections = await _read(db, CORRECTIONS, {**keys, "state": "applied", "order_item_id": {"$in": sorted(aliases)}},
                                  {"_id": 0}, max(1, limit * 4)) if include_corrections else []
        item_maps = {}
        for number in selected:
            candidates = defaultdict(list)
            for item in order_items(order_map[number]):
                for alias in line_aliases(item):
                    candidates[alias].append(item)
            item_maps[number] = {alias: values[0] for alias, values in candidates.items() if len(values) == 1}
        def ambiguous(item: dict) -> dict:
            return _record(item, _unknown(item.get("quantity"), "ambiguous_historical_line_identity"))

        stored_seen = set()
        for row in stored:
            number, key = str(row.get("order_number")), str(row.get("order_item_id"))
            item = item_maps[number].get(key)
            if item:
                identity = (number, line_id(item))
                result["lines"][identity] = ambiguous(item) if identity in stored_seen else {**row, "cost": _stored_cost(row)}
                stored_seen.add(identity)
        financial_first = set()
        for row in lifecycle:
            number = str(row["order_number"])
            lifecycle_seen = set()
            for key, snapshot in (row.get("reporting_cost_snapshots") or {}).items():
                item = item_maps[number].get(str(key))
                # An ordinary manual snapshot may predate lifecycle capture.
                # First stored proof wins; today's ingestion cannot reprice it.
                if item and (number, line_id(item)) not in result["lines"]:
                    result["lines"][(number, line_id(item))] = deepcopy(snapshot)
                if item:
                    identity = (number, line_id(item))
                    if snapshot.get("bundle_financial") is True:
                        # Supplier execution may still be unresolved. This is
                        # the first complete financial bundle evidence, isolated
                        # from executable intake and older base-only snapshots.
                        record = deepcopy(snapshot)
                        frozen = record.get("bundle_snapshot") or {}
                        from bundle_financial_selection_evidence import verify_selection_recovery
                        valid_identity = ("historical_cost_verified" in frozen and "order_created_at" in frozen
                            and ("financial_selection_recovery" not in frozen
                                 or verify_selection_recovery(frozen, record))
                            and bool(record.get("line_fingerprint"))
                            and record.get("line_fingerprint") == frozen.get("line_fingerprint")
                            and record.get("product_id") == frozen.get("product_id")
                            and record.get("selected_options") == frozen.get("selected_options")
                            and decimal(record.get("quantity")) == decimal(frozen.get("sale_quantity")))
                        record["cost"] = (_bundle_cost(frozen, record.get("quantity")) if valid_identity
                                          else _unknown(item.get("quantity"), "bundle_reporting_cost_incomplete"))
                        result["lines"][identity] = record
                        financial_first.add(identity)
                        result["composite_products"].add(str(record.get("product_id")))
                    if identity in lifecycle_seen:
                        result["lines"][identity] = ambiguous(item)
                    lifecycle_seen.add(identity)
            bundle_seen = set()
            for key, snapshot in (row.get("bundle_intake_snapshots") or {}).items():
                if snapshot is None:
                    continue
                item = item_maps[number].get(str(key))
                if not item:
                    continue
                result["composite_products"].add(str(snapshot.get("product_id")))
                cost = _bundle_cost(snapshot, snapshot.get("sale_quantity", item.get("quantity")))
                if "reporting_cost" in snapshot and "order_created_at" not in snapshot:
                    cost = _unknown(item.get("quantity"), "historical_bundle_cost_unproven")
                record = {"quantity": snapshot.get("sale_quantity", item.get("quantity")), "product_id": snapshot.get("product_id"),
                    "cost": cost, "line_fingerprint": snapshot.get("line_fingerprint"),
                    "selected_options": snapshot.get("selected_options"), "bundle_snapshot": snapshot}
                identity = (number, line_id(item))
                if identity in bundle_seen:
                    result["lines"][identity] = ambiguous(item)
                elif identity not in financial_first:
                    result["lines"][identity] = record
                    if "reporting_cost" in snapshot:
                        financial_first.add(identity)
                bundle_seen.add(identity)
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for unit in units:
            grouped[(str(unit["plan_id"]), str(unit["order_line_id"]))].append(unit)
        for plan in plans:
            number = str(plan["order_id"])
            plan_seen = set()
            for key, snapshot in (plan.get("bundle_snapshots") or {}).items():
                item = item_maps[number].get(str(key))
                if not item:
                    continue
                q = item.get("quantity")
                cost = _unknown(q, "bundle_reconciliation_required") if plan.get("reconciliation_required") else _bundle_cost(snapshot, q, grouped[(str(plan["_id"]), str(key))])
                if "reporting_cost" in snapshot and "order_created_at" not in snapshot:
                    # A plan re-expansion alone is not proof that a future-only
                    # financial policy existed when this sale was created.
                    cost = _unknown(q, "historical_bundle_cost_unproven")
                result["composite_products"].add(str(snapshot.get("product_id")))
                original = next((entry for entry in plan.get("lines") or [] if str(entry.get("order_line_id")) == str(key)), None)
                record = {"quantity": (original or {}).get("quantity", q), "product_id": snapshot.get("product_id"),
                    "cost": cost, "line_fingerprint": snapshot.get("line_fingerprint"),
                    "selected_options": snapshot.get("selected_options"), "bundle_snapshot": snapshot}
                if original:
                    record["line_fingerprint"] = line_fingerprint(original)
                identity = (number, line_id(item))
                if identity in plan_seen:
                    result["lines"][identity] = ambiguous(item)
                elif identity in financial_first:
                    # A later operational plan can establish execution, never
                    # reprice the earlier financial proof from today's catalog.
                    frozen = result["lines"][identity]
                    problem = ("bundle_reconciliation_required" if plan.get("reconciliation_required") else
                               "cost_snapshot_line_changed" if record.get("line_fingerprint") != frozen.get("line_fingerprint") else
                               "cost_snapshot_quantity_mismatch" if decimal(record.get("quantity")) != decimal(frozen.get("quantity")) else None)
                    if problem:
                        frozen["cost"] = _unknown(q, problem)
                    elif frozen.get("cost", {}).get("calculation_cost_available"):
                        frozen["cost"] = _bundle_cost(frozen["bundle_snapshot"], frozen["quantity"],
                            grouped[(str(plan["_id"]), str(key))])
                else:
                    result["lines"][identity] = record
                plan_seen.add(identity)
        correction_seen = set()
        for row in corrections:
            number, key = str(row["order_number"]), str(row["order_item_id"])
            item = item_maps[number].get(key)
            if item:
                from order_cost_reporting_repair import source_authority_evidence_hash
                identity = (number, line_id(item))
                if identity in correction_seen:
                    result["lines"][identity] = ambiguous(item)
                    continue
                correction_seen.add(identity)
                previous = result["lines"].get(identity)
                if (not row.get("source_authority_evidence_hash") or
                        row["source_authority_evidence_hash"] != source_authority_evidence_hash(previous)):
                    result["lines"][identity] = {**deepcopy(row), "cost": _unknown(
                        item.get("quantity"), "cost_correction_source_changed")}
                else:
                    result["lines"][identity] = deepcopy(row)
        result["read_batches"] += 1
        if budget is not None:
            await budget.checkpoint()
    # This reporting-only estimate must NOT redefine historical evidence
    # or completed/provisional marketing outcomes. The protected Dashboard
    # V2 / Sold Products readers already supply CPUWorkBudget; bare historical
    # and marketing callers do not. Support an explicit opt-out for future
    # callers without editing those source-pinned reducer functions.
    if current_profitability_estimate is None:
        from dashboard_cpu_budget import CPUWorkBudget
        current_profitability_estimate = isinstance(budget, CPUWorkBudget)
    if current_profitability_estimate:
        from current_bundle_reporting_estimate import load_current_bundle_estimates
        estimates = await load_current_bundle_estimates(
            db, user_id, orders, result, budget=budget,
        )
        if estimates:
            result["current_bundle_estimates"] = estimates
    return result


def resolve_order_line_cost(item: dict, *, order_number: str, catalog_cost: dict,
                            authority: dict | None, product_id: Any = None) -> dict:
    authority = authority or {}
    row = item_dict(item)
    record = (authority.get("lines") or {}).get((str(order_number), line_id(row)))
    if record is not None:
        if record.get("line_fingerprint") and record["line_fingerprint"] != line_fingerprint(row):
            return _unknown(row.get("quantity"), "cost_snapshot_line_changed")
        if decimal(record.get("quantity")) != decimal(row.get("quantity", 1)):
            return _unknown(row.get("quantity"), "cost_snapshot_quantity_mismatch")
        saved_product = str(record.get("product_id") or record.get("salla_product_id") or "")
        identities = {str(product_id or ""), str(row.get("parent_product_id") or ""), str(row.get("product_id") or "")}
        if saved_product and saved_product not in identities:
            return _unknown(row.get("quantity"), "cost_snapshot_product_mismatch")
        # Historical bundle choices are IDs. Do not attach the cost of another
        # customization merely because SKU or product_id is the same.
        saved_options = record.get("selected_options")
        from bundle_financial_selection_evidence import verify_selection_recovery
        frozen = record.get("bundle_snapshot") or {}
        recovered_financial = (record.get("bundle_financial") is True
                               and "financial_selection_recovery" in frozen
                               and verify_selection_recovery(frozen, record))
        if "financial_selection_recovery" in frozen and not recovered_financial:
            return _unknown(row.get("quantity"), "bundle_financial_selection_evidence_invalid")
        if saved_options == [] and not record.get("line_fingerprint") and _economic_tokens(row):
            return _unknown(row.get("quantity"), "bundle_selected_options_mismatch" if record.get("bundle_snapshot") else "cost_snapshot_selected_options_mismatch")
        if saved_options is not None and (not saved_options or isinstance(saved_options[0], dict)):
            from bundle_fulfillment_v2 import selected_ids
            saved = {(str(o.get("option_id")), str(o.get("value_id"))) for o in saved_options}
            current = {(str(o.get("option_id")), str(o.get("value_id"))) for o in selected_ids(economic_item(row))}
            if saved != current and not recovered_financial:
                # The financial-only recovery has a frozen exact-label/ID
                # proof and the unchanged provider line fingerprint above.
                # Never grant this exception to stock or ordinary services.
                return _unknown(row.get("quantity"), "bundle_selected_options_mismatch")
        elif saved_options is not None and not record.get("line_fingerprint"):
            # Legacy manual snapshots stored token pairs rather than bundle
            # selection objects. Their original empty set is also evidence.
            from order_option_cost_snapshot_routes import selected_option_tokens
            saved = {tuple(pair) for pair in saved_options if isinstance(pair, (list, tuple)) and len(pair) == 2}
            current = selected_option_tokens(row)
            legacy = lambda tokens: {pair for pair in tokens
                if pair[0].startswith("id:") and pair[1].startswith("id:") and pair[1] != "id:filled"
                or pair[0].startswith("name:") and pair[1].startswith("name:")}
            if legacy(saved) != legacy(current) or not saved and _economic_tokens(row):
                return _unknown(row.get("quantity"), "cost_snapshot_selected_options_mismatch")
        cost = deepcopy(record.get("cost") or _stored_cost(record))
        cost["historical_cost_verified"] = bool(cost.get("calculation_cost_available")) and cost.get("historical_cost_verified", True)
        return cost
    identities = {str(product_id or ""), str(row.get("parent_product_id") or ""), str(row.get("product_id") or "")}
    if identities & set(authority.get("composite_products") or []):
        estimate = (authority.get("current_bundle_estimates") or {}).get(
            (str(order_number), line_id(row)))
        # A verified frozen/actual/approved line always wins above; missing
        # composite lines may use today's selected, owner-configured cost
        # as an explicitly labeled expected estimate, never a supplier write.
        if estimate is not None:
            return deepcopy(estimate)
        return _unknown(row.get("quantity"), "bundle_snapshot_missing")
    return {**catalog_cost, "historical_cost_verified": False}


async def capture_reporting_costs(db: Any, owner: str, lines: list[dict], previous: dict | None,
                                  bundle_intake: dict | None = None) -> dict:
    """Freeze ordinary first-ingestion costs inside the existing lifecycle write.

    Existing records are immutable. A changed line is explicitly unresolved until
    a versioned correction; replay cannot silently reprice it from today's catalog.
    Bundle intake already contains all frozen metadata and remains its authority.
    """
    from product_v2_details_routes import COST_PROFILES
    from product_v2_routes import PRODUCTS
    from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
    from product_option_cost_routes import BINDINGS, RESOURCES
    result = deepcopy(previous or {})
    pending = [item_dict(line) for line in lines if line_id(line) not in result and not (bundle_intake or {}).get(line_id(line))]
    if not pending:
        return result
    ids = sorted({str(line.get("parent_product_id") or line.get("product_id") or "") for line in pending} - {""})
    query = {"user_id": owner, "salla_product_id": {"$in": ids}}
    products = await _read(db, PRODUCTS, query, {"_id": 0}, max(len(ids), 1))
    profiles = await _read(db, COST_PROFILES, query, {"_id": 0}, max(len(ids), 1))
    product_bindings = await _read(db, PRODUCT_RESOURCE_BINDINGS, query, {"_id": 0}, 10000)
    option_bindings = await _read(db, BINDINGS, query, {"_id": 0}, 10000)
    resource_ids = sorted({str(b["resource_id"]) for b in product_bindings + option_bindings if b.get("resource_id")})
    resources = await _read(db, RESOURCES, {"user_id": owner, "id": {"$in": resource_ids}}, {"_id": 0}, max(len(resource_ids), 1))
    pm, cm = {str(p.get("salla_product_id")): p for p in products}, {str(p.get("salla_product_id")): p for p in profiles}
    rb = {str(r["id"]): r for r in resources}
    pb, ob = defaultdict(list), defaultdict(list)
    for binding in product_bindings:
        pb[str(binding.get("salla_product_id"))].append(binding)
    for binding in option_bindings:
        ob[str(binding.get("salla_product_id"))].append(binding)
    for item in pending:
        key = str(item.get("parent_product_id") or item.get("product_id") or "")
        cost = calculate_catalog_line_cost(item, product=pm.get(key), profile=cm.get(key),
            product_bindings=pb[key], option_bindings=ob[key], resources=rb)
        result[line_id(item)] = _record(item, _frozen_cost(cost, authority="order_snapshot"),
                                        evidence_version=VERSION)
    return result
