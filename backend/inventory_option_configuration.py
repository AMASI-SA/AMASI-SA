"""Stable option conditions and explicit deferred customization for stock input.

This module describes configuration only. It never activates fulfillment or
changes a warehouse balance. Provider labels remain display text, not identity.
"""
from copy import deepcopy

TEXT_TYPES = {"text", "string", "textarea"}
CONDITION_KEYS = ("visibility", "visibility_condition_type",
                  "visibility_condition_option", "visibility_condition_value",
                  "show_if", "conditions", "inventory_role")


def _id(value):
    return str(value).strip() if type(value) in (str, int) else ""


def option_metadata(row, options):
    """Merge saved provider conditions by ID; malformed graphs fail closed."""
    result = deepcopy(options)
    fields = {option["id"]: option for option in result}
    sources = {}
    issues = []
    for source in (row.get("raw_salla"), row.get("raw_salla_details"), row):
        source_options = source.get("options") if isinstance(source, dict) else None
        for option in source_options if isinstance(source_options, list) else []:
            if not isinstance(option, dict) or _id(option.get("id")) not in fields:
                continue
            metadata = sources.setdefault(_id(option["id"]), {})
            metadata.update({key: option[key] for key in CONDITION_KEYS if key in option})

    physical = set()
    variants = row.get("variants") or []
    for variant in variants if isinstance(variants, list) else []:
        selections = variant.get("selections") if isinstance(variant, dict) else None
        for selection in selections if isinstance(selections, list) else []:
            if not isinstance(selection, dict):
                continue
            oid = _id(selection.get("option_id"))
            if oid in fields:
                physical.add(oid)
            elif selection.get("name"):
                matches = [option["id"] for option in result
                           if option["name"] == selection["name"]]
                if len(matches) == 1:
                    physical.add(matches[0])

    graph = {oid: set() for oid in fields}
    for option in result:
        oid = option["id"]
        source = sources.get(oid, {})
        role = source.get("inventory_role")
        if role not in (None, "physical", "customization", "informational"):
            issues.append("دور خيار المخزون غير معروف: " + option["name"])
        if role == "physical":
            physical.add(oid)
        rules = []
        for key in ("show_if", "conditions"):
            conditions = source.get(key, [])
            if conditions is None:
                conditions = []
            if not isinstance(conditions, list):
                issues.append("ترابط خيار المنتج غير صالح: " + option["name"])
                continue
            for condition in conditions:
                if not isinstance(condition, dict) or set(condition) != {"option_id", "value_id"}:
                    issues.append("ترابط خيار المنتج غير صالح: " + option["name"])
                    continue
                rules.append({"option_id": _id(condition["option_id"]),
                              "value_id": _id(condition["value_id"]), "operator": "="})
        visibility = source.get("visibility")
        if visibility == "on_condition":
            rules.append({"option_id": _id(source.get("visibility_condition_option")),
                          "value_id": _id(source.get("visibility_condition_value")),
                          "operator": source.get("visibility_condition_type")})
        elif visibility not in (None, "", "always"):
            issues.append("ترابط خيار المنتج غير مدعوم: " + option["name"])
        valid = []
        for rule in rules:
            parent = fields.get(rule["option_id"])
            if (not parent or rule["operator"] not in ("=", "!=")
                    or rule["value_id"] not in {value["id"] for value in parent["values"]}):
                issues.append("مرجع ترابط خيار المنتج غير مكتمل: " + option["name"])
                continue
            if rule not in valid:
                valid.append(rule)
                graph[oid].add(rule["option_id"])
        option["visibility_rules"] = sorted(valid, key=lambda rule: (
            rule["option_id"], rule["value_id"], rule["operator"]))

    visited = set()
    def visit(oid, active):
        if oid in active or len(active) >= 100:
            return False
        if oid in visited:
            return True
        if not all(visit(parent, active | {oid}) for parent in graph[oid]):
            return False
        visited.add(oid)
        return True
    if not all(visit(oid, set()) for oid in fields):
        issues.append("ترابط خيارات المنتج يحتوي دورة؛ صحح تعريف الخيارات")

    # A conditional customization group is proved by a text field and cannot
    # contain a physical variant dimension. Unrelated choices are not inferred
    # to be customization merely because a provider omitted variant mappings.
    neighbors = {oid: set(parents) for oid, parents in graph.items()}
    for oid, parents in graph.items():
        for parent in parents:
            neighbors[parent].add(oid)
    inferred = set()
    remaining = set(fields)
    while remaining:
        todo = [next(iter(remaining))]
        component = set()
        while todo:
            oid = todo.pop()
            if oid in component:
                continue
            component.add(oid)
            todo.extend(neighbors[oid] - component)
        remaining -= component
        if not component & physical and any(fields[oid]["type"] in TEXT_TYPES for oid in component):
            inferred.update(component)
    for option in result:
        oid = option["id"]
        role = sources.get(oid, {}).get("inventory_role")
        option["stock_identity"] = oid in physical
        option["defer_allowed"] = (not issues and oid not in physical
                                    and role != "informational"
                                    and (role == "customization" or oid in inferred))
    return result, list(dict.fromkeys(issues)), sorted(physical)


def option_visibility(options, selections, deferred=()):
    """Return visible/hidden/pending; unanswered is never a literal 'no'."""
    fields = {option["id"]: option for option in options}
    selected = {selection["option_id"]: selection["value_id"] for selection in selections}
    pending = set(deferred)
    memo = {}
    def evaluate(oid, active=frozenset()):
        if oid in memo:
            return memo[oid]
        if oid in active or oid not in fields or len(active) >= 100:
            return "pending"
        states = []
        for rule in fields[oid].get("visibility_rules", []):
            parent = rule["option_id"]
            parent_state = evaluate(parent, active | {oid})
            if parent_state == "hidden":
                states.append("hidden")
            elif parent_state == "pending" or parent in pending or parent not in selected:
                states.append("pending")
            else:
                match = selected[parent] == rule["value_id"]
                applies = match if rule["operator"] == "=" else not match
                states.append("visible" if applies else "hidden")
        status = "hidden" if "hidden" in states else "pending" if "pending" in states else "visible"
        memo[oid] = status
        return status
    return {oid: evaluate(oid) for oid in fields}


def configuration_identity(config):
    """A stable-ID signature for new configured stock; labels stay separate."""
    fixed = [{"option_id": item["option_id"], "value_id": item["value_id"]}
             for item in config.get("selections", [])]
    fixed.extend({"option_id": item["option_id"], "value": item["value"]}
                 for item in config.get("inputs", []))
    return {"version": 2, "fixed_options": sorted(fixed, key=lambda item: item["option_id"]),
            "deferred_option_ids": sorted(config.get("deferred_option_ids", []))}


def configuration_specifications(config):
    """Collision-free identity specifications, not user-facing field labels."""
    return {"option:" + item["option_id"].encode("utf-8").hex(): ("value:" + item["value_id"].encode("utf-8").hex()
                                             if "value_id" in item else item["value"])
            for item in configuration_identity(config)["fixed_options"]}
