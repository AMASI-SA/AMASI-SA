"""Read-only current-cost projections for sold composite units without a frozen cost.

Owner policy: today\'s Mezan cost per selected physical/option component applies
to every sold unit in profit *reports*, including old unpriced orders. This is
an estimate, NEVER evidence of supplier receipt or a historical invoice.

Exact frozen, corrected and actual cost authorities continue to win. A missing
choice, unsupported complex recipe, currency, or cost fails closed.
The batch is tenant-scoped, at most two catalog reads plus one resources read,
with no per-order Mongo queries or writes.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from decimal import Decimal
from typing import Any

from order_cost_authority import VERSION, _amount, _sar, decimal, line_id
from order_option_cost_snapshot_routes import resolve_base_unit_cost


class _Unresolved(ValueError):
    pass


def _fail(reason: str) -> None:
    raise _Unresolved(reason)


def _id(value: Any) -> str:
    return str(value).strip() if value not in (None, "") and not isinstance(value, (dict, list, bool)) else ""


def _label(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _number(value: Any) -> Decimal:
    amount = decimal(value)
    if amount is None or amount <= 0 or amount > Decimal("100000"):
        _fail("invalid_bundle_quantity")
    return amount


def _options(product: dict) -> list[dict]:
    raw = product.get("options")
    if not raw:
        provider = product.get("raw_salla_details") or product.get("raw_salla") or {}
        provider_options = provider.get("options") if isinstance(provider, dict) else None
        if isinstance(provider_options, list) and provider_options:
            raw = provider_options
    if not isinstance(raw, list) or len(raw) > 100:
        _fail("current_product_options_missing")
    return [o for o in raw if isinstance(o, dict)]


def _choice_pairs(item: dict, product: dict) -> set[tuple[str, str]]:
    """Recover price choices from current catalog without changing provider IDs.

    Order-row id is NOT catalog option id. Existing valid ID wins; otherwise
    a unique exact name/value match is needed. Ambiguous Yes answers fail.
    Customer text is only a presence signal and is never emitted or persisted.
    """
    from report_option_identity import catalog_options
    from report_option_recovery import report_cost_item
    item = report_cost_item(item)
    aliases = catalog_options(product)
    options = _options(product)
    by_id = {_id(o.get("id")): o for o in options if _id(o.get("id"))}
    if len(by_id) != len(options):
        _fail("current_catalog_option_ids_ambiguous")
    values = {}
    for oid, option in by_id.items():
        vals = option.get("values") or []
        if not isinstance(vals, list):
            _fail("current_catalog_values_invalid")
        values[oid] = {_id(v.get("id")): v for v in vals if isinstance(v, dict) and _id(v.get("id"))}
        if len(values[oid]) != len(vals):
            _fail("current_catalog_values_ambiguous")
    rows = item.get("options_raw") or item.get("selected_options") or item.get("options") or []
    if not isinstance(rows, list) or len(rows) > 100:
        _fail("current_order_options_invalid")
    pairs = set()
    filled = set()
    for original in rows:
        if not isinstance(original, dict):
            _fail("current_order_option_invalid")
        row = original.get("raw") if isinstance(original.get("raw"), dict) else original
        nested = row.get("option") if isinstance(row.get("option"), dict) else {}
        strong_ids = {_id(row.get("product_option_id")), _id(row.get("option_id")), _id(nested.get("id"))} - {""}
        if len(strong_ids) > 1:
            _fail("current_order_option_id_conflict")
        candidate = next(iter(strong_ids), "")
        if candidate and candidate not in by_id:
            _fail("current_order_option_id_unknown")
        if not candidate and _id(row.get("id")) in by_id:
            candidate = _id(row["id"])
        name = _label(row.get("option_name") or row.get("name") or nested.get("name"))
        if not candidate and name:
            matching = [oid for oid, o in by_id.items() if name in aliases.get(oid, {}).get("labels", set())]
            if len(matching) == 1:
                candidate = matching[0]
            elif len(matching) > 1:
                _fail("current_order_option_name_ambiguous")
        raw_values = row.get("values") if "values" in row else row.get(
            "value", row.get("value_name", row.get("selected", row.get("answer", row.get("text")))))
        pieces = raw_values if isinstance(raw_values, list) else [raw_values]
        pieces = [v for v in pieces if v not in (None, "", [], {})]
        if not candidate:
            # Some historical Salla choices only provide a selected value label.
            # Its match must be globally unique within the product options.
            labels = [_label((v.get("name") or v.get("label") or v.get("value")
                              or v.get("text")) if isinstance(v, dict) else v) for v in pieces]
            matches = [(oid, vid) for oid, choices in values.items()
                       for vid, val in choices.items()
                       if any(x in aliases.get(oid, {}).get("values", {}).get(vid, set()) for x in labels if x)]
            if len(matches) != 1 or len(pieces) != 1:
                _fail("current_order_choice_ambiguous")
            oid, vid = matches[0]
            pairs.add((oid, vid))
            continue
        option = by_id[candidate]
        if not values[candidate]:
            if any(pieces):
                filled.add(candidate)
            continue
        for value in pieces:
            data = value if isinstance(value, dict) else {}
            raw_vid = _id(row.get("value_id") or data.get("value_id") or data.get("option_value_id") or data.get("id"))
            label = _label(data.get("name") or data.get("label") or data.get("value") or
                           data.get("text") if data else value)
            if raw_vid in values[candidate]:
                vid = raw_vid
            else:
                found = [vid for vid, v in values[candidate].items() if label and label in aliases.get(candidate, {}).get("values", {}).get(vid, set())]
                if len(found) != 1:
                    _fail("current_order_value_unproved")
                vid = found[0]
            pairs.add((candidate, vid))
    for oid, option in by_id.items():
        matching_count = sum(1 for chosen, _ in pairs if chosen == oid)
        if matching_count > 1 and option.get("type") not in {
                "checkbox", "multiple", "multi_select"}:
            _fail("current_order_multiple_conflicting_values")
        if not (option.get("required") or option.get("is_required")):
            continue
        dependencies = option.get("show_if") or option.get("conditions") or []
        if not isinstance(dependencies, list) or any(not isinstance(c, dict)
                or set(c) != {"option_id", "value_id"} for c in dependencies):
            _fail("current_catalog_option_condition_invalid")
        visible = all((_id(c["option_id"]), _id(c["value_id"])) in pairs
                      for c in dependencies)
        if option.get("visibility") == "on_condition":
            key = (_id(option.get("visibility_condition_option")),
                   _id(option.get("visibility_condition_value")))
            op = option.get("visibility_condition_type")
            if op not in {"=", "!="} or not key[0] or not key[1]:
                _fail("current_catalog_option_condition_invalid")
            visible = key in pairs if op == "=" else (
                any(p[0] == key[0] for p in pairs) and key not in pairs)
        if visible and (values[oid] and not any(p[0] == oid for p in pairs) or
                        not values[oid] and oid not in filled):
            _fail("current_order_required_choice_missing")
    return pairs


def _cost(product: dict, profile: dict, variant_id: str = "") -> tuple[Decimal, bool]:
    unit, source = resolve_base_unit_cost({"variant_id": variant_id}, profile, product)
    evidence = profile if source.startswith("mezan_") else product
    cost = _sar(unit, evidence)
    if cost is None:
        _fail("current_bundle_base_missing")
    return cost, source.startswith("mezan_")


def _build_line_cost(item: dict, root: str, definitions: dict, products: dict,
                     profiles: dict, options: dict, bindings: dict, resources: dict) -> dict:
    quantity = _number(item.get("quantity"))
    if quantity != quantity.to_integral_value():
        _fail("current_bundle_quantity_fractional")
    selected = _choice_pairs(item, products[root])
    # Missing provider answers cannot prove that a formerly optional,
    # potentially priced rose/embroidery choice was not selected.
    raw_choices = item.get("options_raw") or item.get("selected_options") or item.get("options")
    if not raw_choices and (any(row.get("condition") for row in
                               (definitions.get(root) or {}).get("items") or [])
                            or options.get(root)):
        _fail("current_bundle_sale_selection_unavailable")
    used_resources = set()
    mezan_complete = True

    def price(pid: str, pairs: set, variant: str, stack: tuple) -> tuple[Decimal, Decimal, Decimal]:
        nonlocal mezan_complete
        if pid not in products or pid in stack or len(stack) >= 5:
            _fail("current_bundle_product_missing_or_recursive")
        product, profile = products[pid], profiles.get(pid, {})
        base, from_mezan = _cost(product, profile, variant)
        mezan_complete &= from_mezan
        physical, services = Decimal(0), Decimal(0)
        doc = definitions.get(pid)
        if doc:
            items = doc.get("items")
            if not isinstance(items, list) or not 1 <= len(items) <= 100:
                _fail("current_bundle_recipe_invalid")
            seen_recipe_items = set()
            for row in items:
                if not isinstance(row, dict):
                    _fail("current_bundle_recipe_invalid")
                cond = row.get("condition") or {}
                signature = (row.get("kind"), _id(row.get("product_id")),
                             _id(row.get("resource_id")), _id(row.get("variant_id")),
                             _id(cond.get("option_id")) if isinstance(cond, dict) else "",
                             _id(cond.get("value_id")) if isinstance(cond, dict) else "")
                if signature in seen_recipe_items and row.get("repeat_policy", "once") != "sum":
                    _fail("current_bundle_duplicate_once_unresolved")
                seen_recipe_items.add(signature)
                if any(k in row for k in ("slots", "variant_rules", "option_mappings",
                                          "customization_mappings", "component_overrides")):
                    _fail("current_bundle_advanced_recipe_unresolved")
                conditions = row.get("condition")
                if conditions is not None:
                    if not isinstance(conditions, dict) or set(conditions) != {"option_id", "value_id"}:
                        _fail("current_bundle_condition_invalid")
                    oid, vid = _id(conditions["option_id"]), _id(conditions["value_id"])
                    catalog_option = next((o for o in _options(product)
                                           if _id(o.get("id")) == oid), None)
                    if not catalog_option or vid not in {
                            _id(v.get("id")) for v in catalog_option.get("values") or []
                            if isinstance(v, dict)}:
                        _fail("current_bundle_condition_not_in_current_catalog")
                    if (oid, vid) not in pairs:
                        continue
                count = _number(row.get("quantity", 1))
                if row.get("component_scope", "per_bundle") != "per_bundle":
                    _fail("current_bundle_component_scope_unresolved")
                kind = row.get("kind")
                if kind == "current_product":
                    if count != 1 or row.get("variant_id") or row.get("sourcing_mode") == "auto":
                        _fail("current_bundle_extra_parent_unresolved")
                    # Parent has already been counted exactly once.
                elif kind == "product":
                    child = _id(row.get("product_id"))
                    child_pairs = set()
                    for choice in row.get("selected_options") or []:
                        if not isinstance(choice, dict):
                            _fail("current_bundle_nested_selection_invalid")
                        child_pairs.add((_id(choice.get("option_id")), _id(choice.get("value_id"))))
                    child_base, child_physical, child_services = price(
                        child, child_pairs, _id(row.get("variant_id")), stack + (pid,))
                    physical += count * (child_base + child_physical)
                    services += count * child_services
                elif kind == "resource":
                    rid = _id(row.get("resource_id"))
                    key = (pid, rid)
                    if not rid or key in used_resources:
                        _fail("current_bundle_resource_duplicate_or_missing")
                    used_resources.add(key)
                    source = resources.get(rid) or {}
                    unit = _sar(source.get("unit_cost"), source)
                    if unit is None:
                        _fail("current_bundle_resource_cost_missing")
                    if source.get("kind") == "service" and not source.get("track_inventory"):
                        services += count * unit
                    else:
                        physical += count * unit
                else:
                    _fail("current_bundle_recipe_kind_unresolved")
        current_option_bindings = [
            ("component", row) for row in bindings.get(pid, [])] + [
            ("option", row) for row in options.get(pid, [])
            if (_id(row.get("option_id")), _id(row.get("value_id"))) in pairs
        ]
        seen_bindings = set()
        for kind, binding in current_option_bindings:
            bid = _id(binding.get("id"))
            if not bid or (kind, bid) in seen_bindings:
                _fail("current_bundle_binding_identity_invalid")
            seen_bindings.add((kind, bid))
            policy = ((profile.get("composition_components") or {}).get(
                ("option_resource:" if kind == "option" else "product_resource:") + bid) or {})
            if policy.get("cost_treatment") == "included_in_base" or binding.get("cost_treatment") == "included_in_base":
                continue
            q = _number(binding.get("quantity", 1))
            if binding.get("mode") == "direct":
                unit = _sar(binding.get("direct_amount"), binding)
            else:
                rid = _id(binding.get("resource_id"))
                key = (pid, rid)
                if not rid or key in used_resources:
                    _fail("current_bundle_resource_duplicate_or_missing")
                used_resources.add(key)
                source = resources.get(rid) or {}
                unit = _sar(source.get("unit_cost"), source)
            if unit is None:
                _fail("current_bundle_binding_cost_missing")
            if kind == "component":
                physical += q * unit
            else:
                services += q * unit
        return base, physical, services

    base, physical, services = price(root, selected, _id(item.get("variant_id")), ())
    base_total, physical_total, service_total = (
        base * quantity, physical * quantity, services * quantity)
    total = base_total + physical_total + service_total
    return {
        "quantity": float(quantity), "currency": "SAR", "cost_currency": "SAR",
        "base_cost_source": "current_bundle_catalog_estimate",
        "base_complete": True, "calculation_cost_available": True,
        "calculation_cost_source": "current_bundle_catalog_estimate",
        "mezan_cost_complete": mezan_complete, "mezan_cost_missing": not mezan_complete,
        "uses_salla_fallback": not mezan_complete,
        "cost_semantics_version": VERSION,
        "base_total": _amount(base_total),
        "product_components_total": _amount(physical_total),
        "selected_options_total": _amount(service_total),
        "components_total": _amount(physical_total + service_total),
        "line_total": _amount(total), "cost_authority": "current_bundle_catalog_estimate",
        "cost_stage": "expected", "historical_cost_verified": False,
        "estimated_from_current_catalog": True,
        "missing_cost_reasons": [],
    }


async def load_current_bundle_estimates(
    db: Any, owner: str, orders: list[dict], authority: dict,
    *, budget: Any = None,
) -> dict:
    """One tenant-scoped batch for missing bundle lines, without report writes.

    This is called from the existing order-cost authority, not from any
    protected Dashboard V2 / Sold Products reducer. In the absence of missing
    composite lines, it performs zero additional MongoDB reads.
    """
    composite = set(authority.get("composite_products") or [])
    if not composite:
        return {}
    pending: list[tuple[str, dict, str]] = []
    aliases = defaultdict(int)
    for order in orders:
        number = str(order.get("order_number") or "")
        for item in order.get("products") or order.get("items") or []:
            if isinstance(item, dict):
                aliases[(number, line_id(item))] += 1
    for order in orders:
        number = str(order.get("order_number") or "")
        for item in order.get("products") or order.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = line_id(item)
            if not number or not key or aliases[(number, key)] != 1 or (
                    number, key) in authority.get("lines", {}):
                continue
            pid = _id(item.get("parent_product_id") or item.get("product_id"))
            if pid in composite:
                pending.append((number, item, pid))
    if not pending or len(pending) > 5000:
        return {}
    if budget is not None:
        await budget.checkpoint()

    definitions_rows = await db["mezan_bundle_definitions_v2"].find(
        {"user_id": owner, "product_type": "composite"},
        {"_id": 0, "product_id": 1, "items": 1,
         "schema_version": 1, "revision": 1},
    ).to_list(length=10001)
    if len(definitions_rows) > 10000:
        return {}
    definitions = {str(row["product_id"]): row for row in definitions_rows}
    if len(definitions) != len(definitions_rows):
        return {}
    wanted = {pid for _, _, pid in pending}
    # Traverse only product IDs; do not dereference arbitrary order option
    # values, which could be customer-provided text or private information.
    for _ in range(5):
        extra = {
            str(item["product_id"])
            for pid in wanted for item in (definitions.get(pid) or {}).get("items") or []
            if isinstance(item, dict) and item.get("kind") == "product"
            and item.get("product_id")
        }
        if extra.issubset(wanted):
            break
        wanted.update(extra)
        if len(wanted) > 10000:
            return {}
    wanted = sorted(wanted)
    if budget is not None:
        await budget.checkpoint()
    catalog = await db["mezan_products_v2"].find(
        {"user_id": owner, "$or": [
            {"salla_product_id": {"$in": wanted}},
            {"mezan_product_id": {"$in": wanted}},
            {"id": {"$in": wanted}},
        ]},
        {"_id": 0, "id": 1, "salla_product_id": 1,
         "mezan_product_id": 1, "options": 1,
         "raw_salla": 1, "raw_salla_details": 1, "variants": 1,
         "cost_price_from_salla": 1, "cost_price": 1, "cost": 1},
    ).to_list(length=10001)
    if len(catalog) > 10000:
        return {}
    product_map = {}
    for product in catalog:
        key = _id(product.get("salla_product_id") or product.get("mezan_product_id") or product.get("id"))
        if key in product_map:
            return {}
        product_map[key] = product
    scope = {"user_id": owner, "salla_product_id": {"$in": wanted}}
    profile_rows, option_rows, product_bindings = await asyncio.gather(
        db["mezan_product_cost_profiles_v2"].find(
            scope, {"_id": 0},
        ).to_list(length=10001),
        db["mezan_product_option_cost_bindings_v2"].find(
            scope, {"_id": 0},
        ).to_list(length=10001),
        db["mezan_product_resource_bindings_v2"].find(
            scope, {"_id": 0},
        ).to_list(length=10001),
    )
    if any(len(rows) > 10000 for rows in (
            profile_rows, option_rows, product_bindings)):
        return {}
    profile_map = {}
    for profile in profile_rows:
        pid = _id(profile.get("salla_product_id"))
        if pid in profile_map:
            return {}
        profile_map[pid] = profile
    option_map = defaultdict(list)
    product_binding_map = defaultdict(list)
    for row in option_rows:
        option_map[_id(row.get("salla_product_id"))].append(row)
    for row in product_bindings:
        product_binding_map[_id(row.get("salla_product_id"))].append(row)
    resource_ids = {
        str(row["resource_id"])
        for row in option_rows + product_bindings
        if row.get("resource_id")
    }
    for definition in definitions_rows:
        for row in definition.get("items") or []:
            if isinstance(row, dict) and row.get("kind") == "resource" and row.get("resource_id"):
                resource_ids.add(str(row["resource_id"]))
    if budget is not None:
        await budget.checkpoint()
    resource_rows = await db["mezan_cost_resources_v2"].find(
        {"user_id": owner, "id": {"$in": sorted(resource_ids)}},
        {"_id": 0},
    ).to_list(length=10001) if resource_ids else []
    if len(resource_rows) > 10000:
        return {}
    resources = {}
    for resource in resource_rows:
        rid = _id(resource.get("id"))
        if rid in resources:
            return {}
        resources[rid] = resource

    result = {}
    for number, item, pid in pending:
        if budget is not None:
            await budget.checkpoint()
        try:
            estimate = _build_line_cost(
                item, pid, definitions, product_map, profile_map,
                option_map, product_binding_map, resources,
            )
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        result[(number, line_id(item))] = estimate
    return result
