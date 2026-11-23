"""Stable option conditions and explicit deferred customization for stock input.

This module describes configuration only. It never activates fulfillment or
changes a warehouse balance. Provider labels remain display text, not identity.
"""
from copy import deepcopy

TEXT_TYPES = {"text", "string", "textarea"}
INVENTORY_ROLES = {"physical", "customization", "informational"}
POLICY_FIELD = "mezan_inventory_option_policy"
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

    # Mezan's local policy survives Salla refreshes and never rewrites provider
    # definitions or customer answers. A changed option catalog needs review.
    policy = row.get(POLICY_FIELD)
    if policy is not None:
        roles = policy.get("roles") if isinstance(policy, dict) else None
        revision = policy.get("revision") if isinstance(policy, dict) else None
        if (not isinstance(roles, dict) or type(revision) is not int or revision < 1
                or any(not isinstance(oid, str) or not isinstance(role, str) or role not in INVENTORY_ROLES
                       for oid, role in (roles or {}).items())):
            issues.append("تصنيف خيارات المخزون غير صالح؛ راجع تصنيف الخيارات")
        else:
            if set(roles) != set(fields):
                issues.append("تغير تعريف خيارات المنتج؛ راجع تصنيف الخيارات")
            for oid, role in roles.items():
                if oid in fields:
                    sources.setdefault(oid, {})["inventory_role"] = role

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

    variant_dimensions = set(physical)

    graph = {oid: set() for oid in fields}
    for option in result:
        oid = option["id"]
        source = sources.get(oid, {})
        role = source.get("inventory_role")
        if role not in (None, "physical", "customization", "informational"):
            issues.append("دور خيار المخزون غير معروف: " + option["name"])
        if oid in variant_dimensions and role not in (None, "physical"):
            issues.append("خيار مرتبط بنسخة المنتج ويجب أن يبقى مواصفة للقطعة: " + option["name"])
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
        if not component & physical and any(fields[oid]["type"] in TEXT_TYPES
                and sources.get(oid, {}).get("inventory_role") != "informational" for oid in component):
            inferred.update(component)
    for option in result:
        oid = option["id"]
        role = sources.get(oid, {}).get("inventory_role")
        option["inventory_role"] = ("physical" if oid in variant_dimensions else role
                                    or ("customization" if oid in inferred else "physical"))
        option["role_locked"] = oid in variant_dimensions
        option["stock_identity"] = oid in physical
    for option in result:
        if option["inventory_role"] != "informational" and any(
                fields[parent]["inventory_role"] == "informational" for parent in graph[option["id"]]):
            issues.append("خيار تجهيز أو مواصفة يعتمد على خيار جانبي؛ راجع تصنيف الخيارات المرتبطة: " + option["name"])
    for option in result:
        oid = option["id"]
        role = option["inventory_role"]
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
