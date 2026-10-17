"""Resolve frozen Product V2 sourcing rules without inventing supplier evidence."""


def resolve_policy(leaf):
    policy = leaf.get("sourcing_policy") or {}
    pairs = {(str(r.get("option_id")), str(r.get("value_id")))
             for r in leaf.get("selected_options", []) if isinstance(r, dict)}
    matches = []
    for rule in policy.get("rules", []):
        if rule.get("variant_id") and rule["variant_id"] != leaf.get("variant_id"):
            continue
        if any((str(c.get("option_id")), str(c.get("value_id"))) not in pairs
               for c in rule.get("conditions", [])):
            continue
        if any((leaf.get("customization") or {}).get(k) != v
               for k, v in (rule.get("customization") or {}).items()):
            continue
        matches.append(rule)
    if len(matches) > 1:
        return {"supplier_id": None, "allow_split": True, "reason": "conflicting_sourcing_rules"}
    selected = matches[0] if matches else policy
    supplier = selected.get("supplier_id")
    return {"supplier_id": supplier, "allow_split": bool(selected.get("allow_split", False)),
            "rule_id": selected.get("rule_id"), "revision": policy.get("revision"),
            "reason": None if supplier else "supplier_policy_missing"}
