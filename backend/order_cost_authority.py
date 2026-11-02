"""One read-only cost authority for order profitability, always expressed in SAR.

Catalog costs are estimates for legacy ordinary lines with no historical proof.
Frozen bundle composition replaces (never augments) that catalog estimate. A
physical base product belongs in a composition through ``current_product``.
Reads never reserve stock, rewrite orders, create snapshots, or post journals.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

VERSION = "order-cost-authority-v2"
LIFECYCLES = "mezan_component_order_lifecycle_v1"
SNAPSHOTS = "mezan_order_item_cost_snapshots_v2"
CORRECTIONS = "mezan_order_cost_corrections_v1"
CHUNK_SIZE = 250


def decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _amount(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001")))


def item_dict(item: Any) -> dict:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="python")
    return vars(item)


def line_id(item: Any) -> str:
    row = item_dict(item)
    return str(row.get("order_item_id") or row.get("order_line_id") or
               row.get("source_item_id") or row.get("id") or "").strip()


def order_items(order: dict) -> list[dict]:
    return [item_dict(item) for item in (order.get("products") or order.get("items") or [])]


def line_fingerprint(item: Any) -> str:
    from order_option_cost_snapshot_routes import selected_option_tokens
    row = item_dict(item)
    payload = {
        "line_id": line_id(row),
        "product_id": str(row.get("parent_product_id") or row.get("product_id") or row.get("salla_product_id") or ""),
        "variant_id": str(row.get("variant_id") or ""),
        "quantity": str(decimal(row.get("quantity", 1))),
        "options": sorted(selected_option_tokens(row)),
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


def calculate_catalog_line_cost(
    item: dict, *, product: dict | None, profile: dict | None,
    product_bindings: list[dict], option_bindings: list[dict],
    resources: dict[str, dict],
) -> dict:
    """Base + selected, independent resources/options, with one quantity pass."""
    from order_option_cost_snapshot_routes import classify_base_unit_cost, binding_matches, selected_option_tokens
    row = item_dict(item)
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
    tokens = selected_option_tokens(row)
    seen: set[tuple[str, str]] = set()
    for kind, bindings in (("product", product_bindings), ("option", option_bindings)):
        for binding in bindings:
            if kind == "option" and not binding_matches(binding, tokens):
                continue
            binding_id = str(binding.get("id") or "")
            # Separate binding kinds can legitimately reference the same resource.
            # Deduplicate only identical source identities, never a resource SKU.
            if binding_id and (kind, binding_id) in seen:
                continue
            if binding_id:
                seen.add((kind, binding_id))
            policy_key = ("product_resource" if kind == "product" else "option_resource") + ":" + binding_id
            policy = ((profile or {}).get("composition_components") or {}).get(policy_key) or {}
            if policy.get("cost_treatment") == "included_in_base" or binding.get("cost_treatment") == "included_in_base":
                continue
            multiplier = decimal(binding.get("quantity", 1))
            resource = resources.get(str(binding.get("resource_id") or "")) or {}
            if kind == "product" or binding.get("mode") == "resource":
                unit = _sar(resource.get("unit_cost"), resource)
            else:
                unit = _sar(binding.get("direct_amount"), binding)
            if unit is None or multiplier is None or multiplier <= 0:
                complete = False
                missing.append("component_cost_or_fx_missing:" + (binding_id or str(binding.get("resource_id") or "unknown")))
                continue
            amount = unit * multiplier
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
        "cost_authority": "catalog_estimate", "cost_stage": "expected",
        "historical_cost_verified": False, "missing_cost_reasons": missing,
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


def _bundle_cost(snapshot: dict, quantity: Any, units: list[dict] | None = None) -> dict:
    q = decimal(quantity)
    if q is None or q <= 0:
        return _unknown(quantity, "bundle_quantity_unresolved")
    estimate = decimal(snapshot.get("estimated_cost"))
    stage, total = "expected", estimate * q if estimate is not None else None
    if units is not None:
        indexes = [unit.get("unit_index") for unit in units]
        if Decimal(len(units)) != q or len(set(indexes)) != len(indexes):
            return _unknown(quantity, "bundle_unit_quantity_mismatch")
        leaves = [leaf for unit in units for leaf in unit.get("bundle_leaves") or []]
        actual = [decimal(leaf.get("actual_cost")) for leaf in leaves]
        # Actual is valid only for a complete consumption of every physical unit.
        if leaves and all(unit.get("state") == "consumed" for unit in units) and all(value is not None for value in actual):
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
    return _frozen_cost(result, authority="bundle_snapshot", stage=stage)


def _stored_cost(row: dict) -> dict:
    if isinstance(row.get("cost"), dict):
        return _frozen_cost(row["cost"], authority=row["cost"].get("cost_authority") or "order_snapshot",
                            stage=row["cost"].get("cost_stage", "expected"))
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


async def load_order_cost_authority(db: Any, user_id: str, orders: list[dict], *, budget: Any = None) -> dict:
    """Bounded, tenant-scoped batches, never one DB query per order/component."""
    from bundle_catalog_v2 import BUNDLES
    from stock_component_consumption_service import PLANS, UNITS
    result: dict[str, Any] = {"lines": {}, "composite_products": set(), "read_batches": 0}
    order_map = {str(order.get("order_number") or ""): order for order in orders if order.get("order_number")}
    if not order_map:
        return result
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
        stored = await _read(db, SNAPSHOTS, keys, {"_id": 0}, limit)
        lifecycle = await _read(db, LIFECYCLES, keys,
            {"_id": 0, "order_number": 1, "bundle_intake_snapshots": 1,
             "reporting_cost_snapshots": 1, "source_fingerprint": 1}, len(selected))
        plans = await _read(db, PLANS, {"user_id": user_id, "order_id": {"$in": selected},
            "bundle_snapshots": {"$exists": True}},
            {"_id": 1, "order_id": 1, "bundle_snapshots": 1, "reconciliation_required": 1, "lines": 1}, len(selected))
        units = await _read(db, UNITS, {"user_id": user_id, "plan_id": {"$in": [p["_id"] for p in plans]},
            "state": {"$ne": "released"}}, {"_id": 0, "plan_id": 1, "order_line_id": 1,
            "unit_index": 1, "state": 1, "selected_context": 1, "bundle_leaves": 1},
            max(1, sum(int(decimal(item.get("quantity", 1)) or 0) for number in selected for item in order_items(order_map[number]))) + limit)
        corrections = await _read(db, CORRECTIONS, {**keys, "state": "applied"}, {"_id": 0}, limit)
        item_maps = {number: {line_id(item): item for item in order_items(order_map[number]) if line_id(item)} for number in selected}
        for row in stored:
            number, key = str(row.get("order_number")), str(row.get("order_item_id"))
            result["lines"][(number, key)] = {**row, "cost": _stored_cost(row)}
        for row in lifecycle:
            number = str(row["order_number"])
            for key, snapshot in (row.get("reporting_cost_snapshots") or {}).items():
                result["lines"][(number, str(key))] = deepcopy(snapshot)
            for key, snapshot in (row.get("bundle_intake_snapshots") or {}).items():
                if snapshot is None:
                    continue
                item = item_maps[number].get(str(key))
                if not item:
                    continue
                result["composite_products"].add(str(snapshot.get("product_id")))
                cost = _bundle_cost(snapshot, snapshot.get("sale_quantity", item.get("quantity")))
                record = _record(item, cost, selected_options=snapshot.get("selected_options"), bundle_snapshot=snapshot)
                if snapshot.get("line_fingerprint"):
                    record["line_fingerprint"] = snapshot["line_fingerprint"]
                result["lines"][(number, str(key))] = record
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for unit in units:
            grouped[(str(unit["plan_id"]), str(unit["order_line_id"]))].append(unit)
        for plan in plans:
            number = str(plan["order_id"])
            for key, snapshot in (plan.get("bundle_snapshots") or {}).items():
                item = item_maps[number].get(str(key))
                if not item:
                    continue
                q = item.get("quantity")
                cost = _unknown(q, "bundle_reconciliation_required") if plan.get("reconciliation_required") else _bundle_cost(snapshot, q, grouped[(str(plan["_id"]), str(key))])
                result["composite_products"].add(str(snapshot.get("product_id")))
                record = _record(item, cost, selected_options=snapshot.get("selected_options"), bundle_snapshot=snapshot)
                result["lines"][(number, str(key))] = record
        for row in corrections:
            result["lines"][(str(row["order_number"]), str(row["order_item_id"]))] = deepcopy(row)
        result["read_batches"] += 1
        if budget is not None:
            await budget.checkpoint()
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
        # Historical bundle choices are IDs. Do not attach the cost of another
        # customization merely because SKU or product_id is the same.
        saved_options = record.get("selected_options")
        if saved_options and isinstance(saved_options[0], dict):
            from bundle_fulfillment_v2 import selected_ids
            saved = {(str(o.get("option_id")), str(o.get("value_id"))) for o in saved_options}
            current = {(str(o.get("option_id")), str(o.get("value_id"))) for o in selected_ids(row)}
            if saved != current:
                return _unknown(row.get("quantity"), "bundle_selected_options_mismatch")
        cost = deepcopy(record.get("cost") or _stored_cost(record))
        cost["historical_cost_verified"] = bool(cost.get("calculation_cost_available"))
        return cost
    identities = {str(product_id or ""), str(row.get("parent_product_id") or ""), str(row.get("product_id") or "")}
    if identities & set(authority.get("composite_products") or []):
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
