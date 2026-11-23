"""Read-only current-cost projections for sold composite units without a frozen cost.

Owner policy: today\'s Mezan cost per selected physical/option component applies
to every sold unit in profit *reports*, including old unpriced orders. This is
an estimate, NEVER evidence of supplier receipt or a historical invoice.

Exact frozen, corrected and actual cost authorities continue to win. Missing choices and recorded-price gaps retain known evidence but remain incomplete.
Unsupported recipes, ambiguous identities and invalid currency fail closed.
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
from product_option_cost_routes import FILL_BASED_OPTION_TYPES


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


def _choice_pairs(item: dict, product: dict, missing_choices: list | None = None,
                  *, selection_evidence: list | None = None) -> set[tuple[str, str]]:
    """Recover price choices from current catalog without changing provider IDs.

    Order-row id is NOT catalog option id. Existing valid ID wins; otherwise
    a unique exact name/value match is needed. Ambiguous Yes answers fail.
    Customer text is only a presence signal and is never emitted or persisted.
    Required fill fields are fulfillment instructions; actual cost bindings
    determine whether their absence leaves a financial amount unresolved.
    """
    from report_option_identity import catalog_options
    from report_option_recovery import report_cost_item
    item = report_cost_item(item)
    aliases = catalog_options(product)
    known_value_ids = {vid for entry in aliases.values() for vid in entry.get("values", {})}
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
        if not candidate and _id(row.get("id")) in aliases:
            candidate = _id(row["id"])
            if candidate not in by_id:
                _fail("current_order_option_id_unknown")
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
        explicit_value_ids = []
        for value in pieces:
            data = value if isinstance(value, dict) else {}
            explicit_values = {_id(row.get("value_id")), _id(data.get("value_id")),
                               _id(data.get("option_value_id"))} - {""}
            if len(explicit_values) > 1:
                _fail("current_order_value_id_conflict")
            explicit_value_ids.append(next(iter(explicit_values), ""))
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
            data = pieces[0] if isinstance(pieces[0], dict) else {}
            raw_vid = explicit_value_ids[0] or _id(data.get("id"))
            if raw_vid != vid and (explicit_value_ids[0] or raw_vid in known_value_ids):
                _fail("current_order_value_unproved")
            pairs.add((oid, vid))
            continue
        option = by_id[candidate]
        if not values[candidate]:
            if any(pieces):
                filled.add(candidate)
            if selection_evidence is not None:
                has_content = bool(_label(row.get("text") or row.get("answer"))) or any(
                    _label((value.get("name") or value.get("label") or value.get("value")
                            or value.get("text")) if isinstance(value, dict) else value)
                    for value in pieces)
                # Preserve present-empty versus absent without passing customer
                # text to the financial resolver or its output.
                selection_evidence.append({"option_id": candidate,
                                           "values": "filled" if has_content else ""})
            continue
        for value, explicit_vid in zip(pieces, explicit_value_ids):
            data = value if isinstance(value, dict) else {}
            raw_vid = explicit_vid or _id(data.get("id"))
            if raw_vid not in values[candidate] and (explicit_vid or raw_vid in known_value_ids):
                # A known retired catalog identity is not a checkout-row ID.
                # Relabeling it could falsely prove that a paid field is hidden.
                _fail("current_order_value_unproved")
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
        if not values[oid] and _label(option.get("type")) in FILL_BASED_OPTION_TYPES:
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
            if missing_choices is None:
                _fail("current_order_required_choice_missing")
            missing_choices.append((oid, option.get("name") or oid))
    if selection_evidence is not None:
        # Retain the estimator's strict legacy label recovery as stable IDs.
        selection_evidence.extend({"option_id": oid, "values": [{"value_id": vid}]}
                                  for oid, vid in sorted(pairs))
    return pairs


def _cost(product: dict, profile: dict, variant_id: str = "") -> tuple[Decimal | None, bool]:
    unit, source = resolve_base_unit_cost({"variant_id": variant_id}, profile, product)
    evidence = profile if source.startswith("mezan_") else product
    cost = _sar(unit, evidence)
    if cost is None:
        # A genuinely absent price is recoverable presentation evidence.
        # A recorded but invalid price or missing currency rate is not.
        recorded = [profile.get("base_cost")]
        variant_costs = profile.get("variant_costs") or {}
        if isinstance(variant_costs, dict) and variant_id:
            recorded.append(variant_costs.get(variant_id))
        cost_keys = ("cost_price_from_salla", "cost_price", "cost")
        recorded.extend(product.get(key) for key in cost_keys)
        for variant in product.get("variants") or []:
            if isinstance(variant, dict) and variant_id and _id(variant.get("id")) == variant_id:
                recorded.extend(variant.get(key) for key in cost_keys)
        if unit is not None or any(value not in (None, "") for value in recorded):
            _fail("current_bundle_base_cost_or_fx_invalid")
    return cost, source.startswith("mezan_")


def _recipe_selection(row: dict, product: dict, pairs: set) -> bool | None:
    conditions = row.get("condition")
    if conditions is None:
        return True
    if not isinstance(conditions, dict) or set(conditions) != {"option_id", "value_id"}:
        _fail("current_bundle_condition_invalid")
    oid, vid = _id(conditions["option_id"]), _id(conditions["value_id"])
    option = next((o for o in _options(product) if _id(o.get("id")) == oid), None)
    if not option or vid not in {_id(v.get("id")) for v in option.get("values") or []
                                 if isinstance(v, dict)}:
        _fail("current_bundle_condition_not_in_current_catalog")
    if (oid, vid) in pairs:
        return True
    # Only another proved answer to this option proves non-selection.
    return False if any(chosen == oid for chosen, _ in pairs) else None


def _current_product_quantity(row: dict, product: dict, pairs: set,
                              variant: str, selected: bool) -> Decimal:
    """Price a simple physical repeat; slot choices only describe each piece.

    Variant assignment, text customization and component overrides still need
    the full frozen composition engine. Sourcing does not change this current
    catalog estimate into evidence of receipt or actual supplier cost.
    """
    count = _number(row.get("quantity", 1))
    if count != count.to_integral_value() or count > 100:
        _fail("current_bundle_current_product_quantity_invalid")
    if row.get("variant_id") or selected and (
            variant or product.get("variants") or product.get("skus") or product.get("variants_count")):
        _fail("current_bundle_current_product_variant_unresolved")
    if row.get("sourcing_mode", "stock") not in {"auto", "stock", "supplier"}:
        _fail("current_bundle_current_product_source_invalid")
    slots = row.get("slots")
    if slots is None:
        return count
    if not isinstance(slots, list) or len(slots) != int(count):
        _fail("current_bundle_slot_count_invalid")
    catalog = {_id(o.get("id")): o for o in _options(product)}
    choice_decisions = {}
    if selected:
        from report_option_identity import resolve_report_choices
        decisions, _ = resolve_report_choices(
            {"options_raw": [{"option_id": oid, "values": [{"value_id": vid}]}
                             for oid, vid in sorted(pairs)]}, product,
            [{"option_id": oid, "value_id": "__option__"} for oid in catalog])
        choice_decisions = dict(zip(catalog, decisions))
    seen = set()
    for slot in slots:
        if not isinstance(slot, dict) or set(slot) - {
                "slot_id", "variant_id", "variant_rules", "customization_option_ids", "selection_option_ids"}:
            _fail("current_bundle_slot_invalid")
        sid = _id(slot.get("slot_id"))
        if not sid or sid in seen:
            _fail("current_bundle_slot_identity_invalid")
        seen.add(sid)
        if slot.get("variant_id") or slot.get("variant_rules", []) != [] or slot.get("customization_option_ids", []) != []:
            _fail("current_bundle_advanced_slot_unresolved")
        choices = slot.get("selection_option_ids", [])
        if (not isinstance(choices, list) or len(choices) > 100 or
                any(not isinstance(oid, str) for oid in choices) or len(set(choices)) != len(choices)):
            _fail("current_bundle_slot_choices_invalid")
        for oid in choices:
            if oid not in catalog or not catalog[oid].get("values"):
                _fail("current_bundle_slot_choice_not_in_catalog")
            if not selected:
                continue
            dependencies = catalog[oid].get("show_if") or catalog[oid].get("conditions") or []
            if not isinstance(dependencies, list) or any(
                    not isinstance(condition, dict) or set(condition) != {"option_id", "value_id"}
                    for condition in dependencies):
                _fail("current_catalog_option_condition_invalid")
            visible = [_recipe_selection({"condition": condition}, product, pairs)
                       for condition in dependencies]
            if False in visible:
                if any(chosen == oid for chosen, _ in pairs):
                    _fail("current_bundle_slot_selection_conflicts_with_visibility")
                continue
            # The canonical resolver also proves saved provider conditions
            # omitted by normalized options; absence alone never proves hidden.
            if choice_decisions[oid] is False:
                continue
            if None in visible or choice_decisions[oid] is not True:
                _fail("current_bundle_slot_selection_unavailable")
    return count


def _build_line_cost(item: dict, root: str, definitions: dict, products: dict,
                     profiles: dict, options: dict, bindings: dict, resources: dict) -> dict:
    quantity = _number(item.get("quantity"))
    if quantity != quantity.to_integral_value():
        _fail("current_bundle_quantity_fractional")
    missing_choices = []
    selection_evidence = []
    selected = _choice_pairs(item, products[root], missing_choices,
                             selection_evidence=selection_evidence)
    from report_option_identity import resolve_report_choices
    root_option_decisions, _ = resolve_report_choices(
        {"options_raw": selection_evidence}, products[root], options.get(root, []))
    missing_reasons = []
    used_resources = set()
    mezan_complete = True
    detail_cells = []

    def cell(key, label, kind, status, amount=None):
        detail_cells.append({"key": key, "label": str(label or key), "kind": kind,
                             "status": status, "unit_cost": _amount(amount) if amount is not None else None})

    missing_decisions, _ = resolve_report_choices(
        {"options_raw": selection_evidence}, products[root],
        [{"option_id": oid, "value_id": "__option__"} for oid, _ in missing_choices])
    for (oid, label), decision in zip(missing_choices, missing_decisions):
        if decision is False:
            continue  # A saved provider condition proves this field was hidden.
        cell("root:choice:" + oid, label, "service", "unresolved")
        missing_reasons.append("current_bundle_sale_selection_unavailable:" + oid)

    def price(pid: str, pairs: set, variant: str, stack: tuple, scale=Decimal(1), path="root") -> tuple[Decimal, Decimal, Decimal]:
        nonlocal mezan_complete
        if pid not in products or pid in stack or len(stack) >= 5:
            _fail("current_bundle_product_missing_or_recursive")
        product, profile = products[pid], profiles.get(pid, {})
        doc = definitions.get(pid)
        items = doc.get("items") if doc else []
        if doc and (not isinstance(items, list) or not 1 <= len(items) <= 100):
            _fail("current_bundle_recipe_invalid")
        current = []
        has_current = current_unresolved = False
        for row in items:
            if not isinstance(row, dict):
                _fail("current_bundle_recipe_invalid")
            if row.get("kind") == "current_product":
                has_current = True
                selection = _recipe_selection(row, product, pairs)
                count = _current_product_quantity(row, product, pairs, variant, selection is True)
                current_unresolved |= selection is None
                if selection is True:
                    current.append(count)
        # Simultaneous rules may share slots or apply once/sum semantics.
        # This fallback only proves mutually exclusive physical selections.
        if len(current) > 1:
            _fail("current_bundle_overlapping_current_rules_unresolved")
        current_count = sum(current, Decimal(0))
        base, from_mezan = _cost(product, profile, variant)
        if base is not None:
            mezan_complete &= from_mezan
            # A selected physical repeat already covers the parent base.
            # Resource-only compositions retain their existing single base.
            base *= current_count or Decimal(1)
        base_key = "root:base" if path == "root" else path
        cell(base_key, product.get("name") or pid,
             "product" if path == "root" else "component",
             "missing_cost" if base is None else "costed", base * scale if base is not None else None)
        if base is None:
            missing_reasons.append("current_bundle_base_missing:" + base_key)
            base = Decimal(0)  # Known subtotal only; completeness remains false.
        physical, services = Decimal(0), Decimal(0)

        def scoped_quantity(count, scope, key, label, kind):
            if scope not in {"per_bundle", "per_unit"} or scope == "per_unit" and not has_current:
                _fail("current_bundle_component_scope_unresolved")
            if scope == "per_bundle":
                return count
            if current_unresolved:
                cell(key, label, kind, "unresolved")
                missing_reasons.append("current_bundle_sale_selection_unavailable:" + key)
                return None
            if not current_count:
                cell(key, label, kind, "not_required")
                return None
            return count * current_count

        if doc:
            seen_recipe_items = set()
            for recipe_index, row in enumerate(items):
                recipe_key = path + ":recipe:" + str(row.get("id") or recipe_index)
                recipe_resource = resources.get(_id(row.get("resource_id"))) or {}
                recipe_product = products.get(_id(row.get("product_id"))) or {}
                recipe_label = recipe_resource.get("name") or recipe_product.get("name") or row.get("name") or row.get("resource_id") or row.get("product_id") or "مكوّن"
                recipe_kind = "service" if recipe_resource.get("kind") == "service" else "component"
                cond = row.get("condition") or {}
                signature = (row.get("kind"), _id(row.get("product_id")),
                             _id(row.get("resource_id")), _id(row.get("variant_id")),
                             _id(cond.get("option_id")) if isinstance(cond, dict) else "",
                             _id(cond.get("value_id")) if isinstance(cond, dict) else "")
                if signature in seen_recipe_items and row.get("repeat_policy", "once") != "sum":
                    _fail("current_bundle_duplicate_once_unresolved")
                seen_recipe_items.add(signature)
                if ("slots" in row and row.get("kind") != "current_product" or
                        any(k in row for k in ("variant_rules", "option_mappings",
                                               "customization_mappings", "component_overrides"))):
                    _fail("current_bundle_advanced_recipe_unresolved")
                selection = _recipe_selection(row, product, pairs)
                if selection is not True:
                    cell(recipe_key, recipe_label, recipe_kind, "unresolved" if selection is None else "not_required")
                    if selection is None:
                        missing_reasons.append("current_bundle_sale_selection_unavailable:" + recipe_key)
                    continue
                count = _number(row.get("quantity", 1))
                kind = row.get("kind")
                scope = row.get("component_scope", "per_bundle")
                if kind != "resource" and scope != "per_bundle":
                    _fail("current_bundle_component_scope_unresolved")
                if kind == "current_product":
                    # The selected physical count is already in the base cell.
                    continue
                elif kind == "product":
                    child = _id(row.get("product_id"))
                    child_pairs = set()
                    for choice in row.get("selected_options") or []:
                        if not isinstance(choice, dict):
                            _fail("current_bundle_nested_selection_invalid")
                        child_pairs.add((_id(choice.get("option_id")), _id(choice.get("value_id"))))
                    child_base, child_physical, child_services = price(
                        child, child_pairs, _id(row.get("variant_id")), stack + (pid,), scale * count, recipe_key)
                    physical += count * (child_base + child_physical)
                    services += count * child_services
                elif kind == "resource":
                    count = scoped_quantity(count, scope, recipe_key, recipe_label, recipe_kind)
                    if count is None:
                        continue
                    rid = _id(row.get("resource_id"))
                    key = (pid, rid)
                    if not rid or key in used_resources:
                        _fail("current_bundle_resource_duplicate_or_missing")
                    used_resources.add(key)
                    source = resources.get(rid) or {}
                    unit = _sar(source.get("unit_cost"), source)
                    if unit is None:
                        if source.get("unit_cost") is not None:
                            _fail("current_bundle_resource_cost_or_fx_invalid")
                        cell(recipe_key, recipe_label, recipe_kind, "missing_cost")
                        missing_reasons.append("current_bundle_resource_cost_missing:" + recipe_key)
                        continue
                    cell(recipe_key, recipe_label, recipe_kind, "costed", count * unit * scale)
                    if source.get("kind") == "service" and not source.get("track_inventory"):
                        services += count * unit
                    else:
                        physical += count * unit
                else:
                    _fail("current_bundle_recipe_kind_unresolved")
        current_option_bindings = [
            ("component", row, True) for row in bindings.get(pid, [])] + [
            ("option", row, root_option_decisions[index] if path == "root" else
             True if (_id(row.get("option_id")), _id(row.get("value_id"))) in pairs else
             False if any(oid == _id(row.get("option_id")) for oid, _ in pairs) else None)
            for index, row in enumerate(options.get(pid, []))
        ]
        seen_bindings = set()
        for kind, binding, selection in current_option_bindings:
            bid = _id(binding.get("id"))
            resource = resources.get(_id(binding.get("resource_id"))) or {}
            detail_key = path + ":" + kind + ":" + (bid or str(binding.get("resource_id") or "unknown"))
            detail_label = resource.get("name") or binding.get("name") or binding.get("value_name") or binding.get("option_name") or bid
            detail_kind = "service" if resource.get("kind") == "service" or kind == "option" and binding.get("mode") == "direct" else "component"
            policy = ((profile.get("composition_components") or {}).get(
                ("option_resource:" if kind == "option" else "product_resource:") + bid) or {})
            included = policy.get("cost_treatment") == "included_in_base" or binding.get("cost_treatment") == "included_in_base"
            if kind == "option" and selection is not True:
                if included:
                    cell(detail_key, detail_label, detail_kind, "included_in_base", Decimal(0))
                    continue
                if selection is None:
                    cell(detail_key, detail_label, detail_kind, "unresolved")
                    missing_reasons.append("current_bundle_sale_selection_unavailable:" + detail_key)
                    continue
                cell(detail_key, detail_label, detail_kind, "not_required")
                continue
            if not bid or (kind, bid) in seen_bindings:
                _fail("current_bundle_binding_identity_invalid")
            seen_bindings.add((kind, bid))
            if included:
                cell(detail_key, detail_label, detail_kind, "included_in_base", Decimal(0))
                continue
            q = _number(binding.get("quantity", 1))
            if binding.get("mode") == "direct":
                unit = _sar(binding.get("direct_amount"), binding)
            else:
                rid = _id(binding.get("resource_id"))
                if has_current:
                    scopes = doc.get("component_scopes") or {}
                    if not isinstance(scopes, dict):
                        _fail("current_bundle_component_scope_unresolved")
                    # Legacy single-piece estimates predate explicit scopes;
                    # both scopes are equivalent only for one proved piece.
                    default_scope = "per_bundle" if (doc.get("schema_version", 1) == 2 or
                        current_count == 1 and not current_unresolved) else None
                    scope = scopes.get(rid, default_scope)
                    q = scoped_quantity(q, scope, detail_key, detail_label, detail_kind)
                    if q is None:
                        continue
                key = (pid, rid)
                if not rid or key in used_resources:
                    _fail("current_bundle_resource_duplicate_or_missing")
                used_resources.add(key)
                source = resources.get(rid) or {}
                unit = _sar(source.get("unit_cost"), source)
            if unit is None:
                raw_amount = binding.get("direct_amount") if binding.get("mode") == "direct" else source.get("unit_cost")
                if raw_amount is not None:
                    _fail("current_bundle_binding_cost_or_fx_invalid")
                cell(detail_key, detail_label, detail_kind, "missing_cost")
                missing_reasons.append("current_bundle_binding_cost_missing:" + detail_key)
                continue
            cell(detail_key, detail_label, detail_kind, "costed", q * unit * scale)
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
        "base_complete": not missing_reasons, "calculation_cost_available": not missing_reasons,
        "calculation_cost_source": "current_bundle_catalog_estimate",
        "mezan_cost_complete": mezan_complete and not missing_reasons, "mezan_cost_missing": not mezan_complete or bool(missing_reasons),
        "uses_salla_fallback": not mezan_complete,
        "cost_semantics_version": VERSION,
        "base_total": _amount(base_total),
        "product_components_total": _amount(physical_total),
        "selected_options_total": _amount(service_total),
        "components_total": _amount(physical_total + service_total),
        "line_total": _amount(total), "cost_authority": "current_bundle_catalog_estimate",
        "cost_stage": "expected", "historical_cost_verified": False,
        "estimated_from_current_catalog": True,
        "cost_detail_cells": detail_cells,
        "missing_cost_reasons": missing_reasons,
    }


async def _gather_owned_reads(*reads):
    """Keep gather's first observed error and drain every owned cursor."""
    from operational_financial_activity_runtime import join_task
    tasks = [asyncio.create_task(read) for read in reads]
    group = asyncio.gather(*tasks)
    try:
        return await asyncio.shield(group)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await join_task(asyncio.gather(*tasks, return_exceptions=True))
        if group.done() and not group.cancelled():
            group.exception()
        raise


async def load_current_bundle_estimates(
    db: Any, owner: str, orders: list[dict], authority: dict,
    *, budget: Any = None, owned_reads: bool = False,
) -> dict:
    """One tenant-scoped batch for missing bundle lines, without report writes.

    This is called from the existing order-cost authority, not from any
    protected Dashboard V2 / Sold Products reducer. In the absence of missing
    composite lines, it performs zero additional MongoDB reads.
    """
    from order_cost_authority import _request_parallel_reads
    owned_reads = _request_parallel_reads(db, owner, owned_reads)

    def read(cursor):
        if owned_reads:
            from operational_read_runtime import read_source_cursor
            return read_source_cursor(cursor, 10001)
        return cursor.to_list(length=10001)

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

    definitions_rows = await read(db["mezan_bundle_definitions_v2"].find(
        {"user_id": owner, "product_type": "composite"},
        {"_id": 0, "product_id": 1, "items": 1,
         "schema_version": 1, "revision": 1, "component_scopes": 1},
    ))
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
    catalog = await read(db["mezan_products_v2"].find(
        {"user_id": owner, "$or": [
            {"salla_product_id": {"$in": wanted}},
            {"mezan_product_id": {"$in": wanted}},
            {"id": {"$in": wanted}},
        ]},
        {"_id": 0, "id": 1, "salla_product_id": 1,
         "mezan_product_id": 1, "name": 1, "options": 1,
         "raw_salla": 1, "raw_salla_details": 1, "variants": 1, "skus": 1, "variants_count": 1,
         "cost_price_from_salla": 1, "cost_price": 1, "cost": 1},
    ))
    if len(catalog) > 10000:
        return {}
    product_map = {}
    for product in catalog:
        key = _id(product.get("salla_product_id") or product.get("mezan_product_id") or product.get("id"))
        if key in product_map:
            return {}
        product_map[key] = product
    scope = {"user_id": owner, "salla_product_id": {"$in": wanted}}
    gather = _gather_owned_reads if owned_reads else asyncio.gather
    profile_rows, option_rows, product_bindings = await gather(
        read(db["mezan_product_cost_profiles_v2"].find(
            scope, {"_id": 0},
        )),
        read(db["mezan_product_option_cost_bindings_v2"].find(
            scope, {"_id": 0},
        )),
        read(db["mezan_product_resource_bindings_v2"].find(
            scope, {"_id": 0},
        )),
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
    resource_rows = await read(db["mezan_cost_resources_v2"].find(
        {"user_id": owner, "id": {"$in": sorted(resource_ids)}},
        {"_id": 0},
    )) if resource_ids else []
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
