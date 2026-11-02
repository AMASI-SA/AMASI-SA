"""Frozen financial additions for explicitly opted-in bundles only.

This module has no database or fulfillment writes. It does not manufacture a
physical product, change a service binding or prove supplier execution. The
existing operational leaves remain the sole input to reservation/consumption.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json

VERSION = "bundle-reporting-cost-v1"
POLICY_FIELD = "bundle_reporting_cost_policy"
_PRICE_KNOWN_OPERATIONAL_REASONS = {"component_provenance_required", "supplier_component_execution_required"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode()).hexdigest()


def base_addition(product, profile, *, product_id, variant_id, multiplier, path, repeat_policy="once"):
    """Capture only a missing base, never the parent's option/resource total."""
    from order_cost_authority import decimal, _sar
    from order_option_cost_snapshot_routes import resolve_base_unit_cost

    policy = (profile or {}).get(POLICY_FIELD) or {}
    if not isinstance(policy, dict) or policy.get("include_parent_base") is not True:
        return None
    unit, source = resolve_base_unit_cost({"variant_id": variant_id}, profile, product)
    evidence = profile if source.startswith("mezan_") else product
    evidence = evidence or {}
    amount, q = _sar(unit, evidence), decimal(multiplier)
    valid_policy = (type(policy.get("revision")) is int and policy["revision"] > 0
                    and isinstance(policy.get("evidence_ref"), str) and bool(policy["evidence_ref"].strip()))
    valid = valid_policy and amount is not None and q is not None and q > 0
    proof = {"product_id": str(product_id), "variant_id": variant_id,
        "path": list(path), "paths": [list(path)], "dedup_scope": list(path[:-1]),
        "repeat_policy": repeat_policy, "quantity": str(q) if q is not None else None,
        "source_unit_cost": str(unit) if unit is not None else None,
        "cost_source": source, "cost_currency": str(evidence.get("cost_currency") or "SAR").upper(),
        "cost_fx_rate_to_sar": evidence.get("cost_fx_rate_to_sar"),
        "policy_revision": policy.get("revision"), "policy_evidence_ref": policy.get("evidence_ref"),
        "unit_cost_sar": str(amount) if valid else None,
        "line_cost_sar": str(amount * q) if valid else None}
    proof["evidence_hash"] = _hash(proof)
    return proof


def _merge_bases(additions):
    """Match the existing product once/sum rule without adding product leaves."""
    from order_cost_authority import decimal

    merged = {}
    for source in additions:
        row = deepcopy(source)
        key = (row["product_id"], row.get("variant_id"), tuple(row.get("dedup_scope") or []))
        previous = merged.get(key)
        if previous is None:
            merged[key] = row
            continue
        previous["paths"].extend(row["paths"])
        unit, count, prior = decimal(row.get("unit_cost_sar")), decimal(row.get("quantity")), decimal(previous.get("quantity"))
        if (unit is None or unit != decimal(previous.get("unit_cost_sar")) or count is None or prior is None
                or decimal(previous.get("line_cost_sar")) is None or decimal(row.get("line_cost_sar")) is None
                or row.get("policy_revision") != previous.get("policy_revision")):
            previous["line_cost_sar"] = None
        elif "sum" in {row.get("repeat_policy"), previous.get("repeat_policy")}:
            previous.update(quantity=str(count + prior), line_cost_sar=str(unit * (count + prior)), repeat_policy="sum")
        elif count != prior:
            previous["line_cost_sar"] = None
        previous["evidence_hash"] = _hash({k: v for k, v in previous.items() if k != "evidence_hash"})
    return list(merged.values())


def _currency_evidence(records):
    """Copies of one frozen resource must agree, even across nested products."""
    if not records:
        return None
    signatures = {_hash({key: row.get(key) for key in ("cost_currency", "cost_fx_rate_to_sar")}) for row in records}
    return records[0] if len(signatures) == 1 else None


def _leaf_sar(leaf, definitions, resources, bindings):
    from order_cost_authority import decimal, _sar

    unit = decimal(leaf.get("unit_cost"))
    if unit is None:
        return None
    if leaf.get("kind") == "resource":
        evidence = _currency_evidence(resources.get(str(leaf.get("resource_id")), []))
        return _sar(unit, evidence) if evidence is not None else None
    if leaf.get("kind") == "direct_cost":
        evidence = _currency_evidence(bindings.get(str(leaf.get("binding_id")), []))
        return _sar(unit, evidence) if evidence is not None else None
    if leaf.get("kind") != "product":
        return None
    frozen = definitions.get(str(leaf.get("product_id"))) or {}
    metadata, product = frozen.get("catalog_metadata") or {}, frozen.get("catalog_product") or {}
    if not product:
        return None
    evidence = (metadata.get("profile") or {}) if str(leaf.get("cost_source", "")).startswith("mezan_") else product
    direct = decimal(leaf.get("direct_unit_cost", 0))
    if direct is None or direct > unit:
        return None
    base = _sar(unit - direct, evidence)
    if base is None or direct == 0:
        return base
    pairs = {(str(row.get("option_id")), str(row.get("value_id"))) for row in leaf.get("selected_options") or []}
    original, converted = Decimal(0), Decimal(0)
    for binding in metadata.get("options") or []:
        if binding.get("mode") != "direct" or (str(binding.get("option_id")), str(binding.get("value_id"))) not in pairs:
            continue
        amount, count = decimal(binding.get("direct_amount")), decimal(binding.get("quantity", 1))
        sar = _sar(amount, binding)
        if amount is None or count is None or count <= 0 or sar is None:
            return None
        original += amount * count
        converted += sar * count
    return base + converted if original == direct else None


def _operation_evidence(snapshot):
    return {key: snapshot.get(key) for key in (
        "cost_complete", "needs_selection", "unresolved_components", "component_decisions")}


def _decision_cost(decision, definitions):
    """Price an existing independent binding, without asserting its execution.

    Unknown price treatment, exclusion conflicts and missing prices are never
    inferred from an operational error. Only the frozen independent policy and
    exact existing binding/resource can establish this financial expense.
    """
    from component_workspace_cost_compat_routes import _current_cost
    from order_cost_authority import decimal, _sar

    if (decision.get("reason") not in _PRICE_KNOWN_OPERATIONAL_REASONS
            or decision.get("cost_treatment") != "independent" or decision.get("use") is not True):
        return None
    frozen = definitions.get(str(decision.get("product_id"))) or {}
    metadata = frozen.get("catalog_metadata") or {}
    kind, bid = decision.get("binding_kind"), str(decision.get("binding_id"))
    if kind not in {"product_resource", "option_resource"}:
        return None
    candidates = [binding for binding in metadata.get("bindings" if kind == "product_resource" else "options") or []
                  if str(binding.get("id")) == bid and binding.get("resource_id") == decision.get("resource_id")]
    policy = ((metadata.get("profile") or {}).get("composition_components") or {}).get(kind + ":" + bid)
    if (len(candidates) != 1 or policy != decision.get("policy") or not isinstance(policy, dict)
            or policy.get("cost_treatment") != "independent" or not policy.get("evidence_ref")):
        return None
    resource = (metadata.get("resources") or {}).get(decision.get("resource_id"))
    if not isinstance(resource, dict):
        return None
    source = _current_cost(resource)
    unit, count = _sar(source, resource), decimal(decision.get("quantity"))
    if (unit is None or count is None or count <= 0
            or decimal(source) != decimal(decision.get("unit_cost"))):
        return None
    return {"kind": "inherited_cost", "financial_only": True,
        "product_id": decision.get("product_id"), "binding_kind": kind,
        "binding_id": bid, "resource_id": decision.get("resource_id"),
        "quantity": decision["quantity"], "source_unit_cost": decision.get("unit_cost"),
        "unit_cost_sar": str(unit), "line_cost_sar": str(unit * count),
        "source_decision_hash": _hash(decision)}


def freeze_reporting_cost(snapshot, additions):
    """Compact SAR projection from the metadata already frozen by expansion.

    It is deliberately separate from estimated_cost and leaves: those fields
    retain their existing operational semantics. Frozen replay reconstructs the
    same projection, including when the reservation plan supersedes intake.
    """
    from order_cost_authority import decimal

    if not additions:
        return None
    additions = _merge_bases(additions)
    definitions = snapshot.get("frozen_definitions") or {}
    resources, bindings = {}, {}
    for definition in definitions.values():
        metadata = definition.get("catalog_metadata") or {}
        for key, resource in (metadata.get("resources") or {}).items():
            resources.setdefault(str(key), []).append(resource)
        for binding in metadata.get("options") or []:
            bindings.setdefault(str(binding.get("id")), []).append(binding)
    missing, components = [], []
    base_total = Decimal(0)
    for addition in additions:
        amount = decimal(addition.get("line_cost_sar"))
        if amount is None:
            missing.append("bundle_reporting_base_cost_or_fx_missing:" + addition["product_id"])
        else:
            base_total += amount
    component_total = Decimal(0)
    for leaf in snapshot.get("leaves") or []:
        unit, count = _leaf_sar(leaf, definitions, resources, bindings), decimal(leaf.get("quantity"))
        valid = unit is not None and count is not None and count > 0
        amount = unit * count if valid else None
        if amount is None:
            missing.append("bundle_reporting_component_cost_or_fx_missing:" + str(leaf.get("leaf_id")))
        else:
            component_total += amount
        components.append({key: deepcopy(leaf.get(key)) for key in (
            "leaf_id", "kind", "product_id", "resource_id", "binding_id", "quantity")})
        components[-1].update(unit_cost_sar=str(unit) if valid else None,
                              source_unit_cost=leaf.get("unit_cost"),
                              line_cost_sar=str(amount) if valid else None)
    for decision in snapshot.get("unresolved_components") or []:
        priced = _decision_cost(decision, definitions)
        if priced is None:
            missing.append("bundle_reporting_component_evidence_missing:" + str(decision.get("binding_id")))
        else:
            components.append(priced)
            component_total += decimal(priced["line_cost_sar"])
    if (snapshot.get("needs_selection")
            or not snapshot.get("cost_complete") and not snapshot.get("unresolved_components")):
        missing.append("bundle_reporting_composition_incomplete")
    complete = not missing
    result = {"version": VERSION, "product_id": snapshot.get("product_id"),
        "currency": "SAR", "cost_stage": "expected", "complete": complete,
        "unit_total_sar": str(base_total + component_total) if complete else None,
        "base_total_sar": str(base_total), "components_total_sar": str(component_total),
        "operational_evidence_hash": _hash(_operation_evidence(snapshot)),
        "uses_salla_fallback": any(str(row.get("cost_source", "")).startswith("salla_") for row in additions + snapshot.get("leaves", [])),
        "missing_cost_reasons": missing, "added_bases": deepcopy(additions), "components": components}
    result["evidence_hash"] = _hash(result)
    return result


def verified_reporting_cost(snapshot):
    """Validate the compact captured proof; no report-time catalogue reads."""
    from order_cost_authority import decimal

    report = snapshot.get("reporting_cost")
    if not isinstance(report, dict) or report.get("version") != VERSION:
        return None
    if (report.get("product_id") != snapshot.get("product_id") or report.get("currency") != "SAR"
            or snapshot.get("needs_selection")
            or report.get("operational_evidence_hash") != _hash(_operation_evidence(snapshot))
            or report.get("cost_stage") != "expected" or report.get("complete") is not True
            or report.get("missing_cost_reasons") or not report.get("added_bases")
            or report.get("evidence_hash") != _hash({k: v for k, v in report.items() if k != "evidence_hash"})):
        return None
    bases = report["added_bases"]
    if not isinstance(bases, list) or any(not isinstance(row, dict) for row in bases):
        return None
    components = report.get("components")
    leaves = snapshot.get("leaves")
    if not isinstance(components, list) or any(not isinstance(row, dict) for row in components) or not isinstance(leaves, list):
        return None
    physical = [row for row in components if not row.get("financial_only")]
    expenses = [row for row in components if row.get("financial_only")]
    decisions = snapshot.get("unresolved_components") or []
    if len(physical) != len(leaves) or len(expenses) != len(decisions):
        return None
    base_total = Decimal(0)
    for row in bases:
        unit, count, amount = (decimal(row.get(key)) for key in ("unit_cost_sar", "quantity", "line_cost_sar"))
        if (unit is None or count is None or count <= 0 or amount != unit * count
                or row.get("evidence_hash") != _hash({k: v for k, v in row.items() if k != "evidence_hash"})):
            return None
        base_total += amount
    component_total = Decimal(0)
    for row, leaf in zip(physical, leaves):
        if not isinstance(row, dict) or any(row.get(key) != leaf.get(key) for key in (
                "leaf_id", "kind", "product_id", "resource_id", "binding_id", "quantity")):
            return None
        if row.get("source_unit_cost") != leaf.get("unit_cost"):
            return None
        unit, count, amount = (decimal(row.get(key)) for key in ("unit_cost_sar", "quantity", "line_cost_sar"))
        if unit is None or count is None or count <= 0 or amount != unit * count:
            return None
        component_total += amount
    for row, decision in zip(expenses, decisions):
        if (decision.get("reason") not in _PRICE_KNOWN_OPERATIONAL_REASONS
                or decision.get("cost_treatment") != "independent" or decision.get("use") is not True
                or row.get("source_decision_hash") != _hash(decision)
                or any(row.get(key) != decision.get(key) for key in ("product_id", "resource_id", "binding_id", "binding_kind", "quantity"))
                or row.get("source_unit_cost") != decision.get("unit_cost")):
            return None
        unit, count, amount = (decimal(row.get(key)) for key in ("unit_cost_sar", "quantity", "line_cost_sar"))
        if unit is None or count is None or count <= 0 or amount != unit * count:
            return None
        component_total += amount
    if (decimal(report.get("base_total_sar")) != base_total
            or decimal(report.get("components_total_sar")) != component_total
            or decimal(report.get("unit_total_sar")) != base_total + component_total):
        return None
    return report
