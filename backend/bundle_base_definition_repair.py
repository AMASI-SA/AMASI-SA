"""Explicit, reviewed repair of a missing physical base in a bundle definition.

Composites normally replace their parent. This helper never infers that a
parent should be added: callers select the products and the sourcing policy.
It appends one real ``current_product`` unit using the ordinary catalog service.
Historical intake, fulfillment, inventory and accounting records are not edited.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

from fastapi import HTTPException

from bundle_catalog_v2 import BUNDLES, expand_bundle, save_bundle
from product_cost_revision import bump_product_cost_revision
from product_fulfillment_routes import _product, _product_key


PLAN_VERSION = "bundle-base-definition-repair/v1"
MAX_PRODUCTS = 20
_PAYLOAD_FIELDS = ("product_type", "schema_version", "items", "component_scopes")
_POLICY_FIELDS = {"sourcing_mode", "supplier_id", "location_id", "variant_id", "slots"}


class BundleBaseDefinitionRepairRefused(ValueError):
    def __init__(self, code: str, *, results: list[dict] | None = None):
        super().__init__(code)
        self.code = code
        self.results = results or []


def _refuse(code: str) -> None:
    raise BundleBaseDefinitionRepairRefused(code)


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        _refuse("explicit_identity_required")
    return value.strip()


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False,
                                     default=str).encode("utf-8")).hexdigest()


def _public_definition(doc: dict) -> dict:
    return {k: deepcopy(v) for k, v in doc.items() if k not in {"_id", "user_id", "history"}}


def _contract(doc: dict) -> dict:
    return {"product_id": doc.get("product_id"), "revision": doc.get("revision"),
            "product_type": doc.get("product_type"), "schema_version": doc.get("schema_version", 1),
            "items": deepcopy(doc.get("items", [])),
            "component_scopes": deepcopy(doc.get("component_scopes", {}))}


def _source_policy(value: Any) -> dict:
    if (not isinstance(value, dict) or set(value) - _POLICY_FIELDS
            or value.get("sourcing_mode") not in {"stock", "supplier", "auto"}):
        _refuse("explicit_current_product_source_policy_required")
    policy = deepcopy(value)
    for field in ("supplier_id", "location_id", "variant_id"):
        if field in policy:
            policy[field] = _text(policy[field])
    if policy["sourcing_mode"] == "supplier" and not policy.get("supplier_id"):
        _refuse("current_product_supplier_required")
    # The ordinary catalog validator checks the slot's variant/customization
    # mappings. Quantity is deliberately fixed; this is not a general editor.
    if "slots" in policy and (not isinstance(policy["slots"], list) or len(policy["slots"]) != 1):
        _refuse("current_product_exactly_one_slot_required")
    return policy


def _inclusion(doc: dict, aliases: set[str]) -> str:
    items = doc.get("items")
    if (doc.get("product_type") != "composite" or not isinstance(items, list)
            or not items or any(not isinstance(item, dict) for item in items)):
        return "existing_composite_definition_required"
    current = [item for item in items if item.get("kind") == "current_product"]
    if any(item.get("kind") == "product" and str(item.get("product_id")) in aliases for item in items):
        return "ambiguous_parent_product_reference"
    if len(current) > 1:
        return "ambiguous_multiple_current_product_rules"
    if current:
        row = current[0]
        try:
            one = Decimal(str(row.get("quantity", 1))) == 1
        except (InvalidOperation, ValueError, TypeError):
            one = False
        if not one or row.get("condition") is not None or row.get("required", True) is not True:
            return "ambiguous_current_product_quantity_or_condition"
        return "already_included"
    return "change"


def _proposal(doc: dict, product_id: str, policy: dict) -> dict:
    revision = doc.get("revision")
    schema = doc.get("schema_version", 1)
    if type(revision) is not int or revision < 1 or type(schema) is not int or schema not in {1, 2}:
        _refuse("unsupported_bundle_definition_version")
    # save_bundle canonicalizes these fields. Refuse legacy/malformed rows
    # that would otherwise be silently changed while appending the base.
    for item in doc["items"]:
        try:
            canonical_quantity = isinstance(item.get("quantity"), str) and str(Decimal(item["quantity"])) == item["quantity"]
        except (InvalidOperation, ValueError, TypeError):
            canonical_quantity = False
        unit = item.get("unit")
        if (not canonical_quantity or type(item.get("required")) is not bool
                or not isinstance(unit, str) or not unit or unit != unit.strip()
                or item.get("sourcing_mode") not in {"stock", "supplier", "auto"}
                or schema == 2 and (not isinstance(item.get("rule_id"), str)
                                    or not item["rule_id"].strip()
                                    or item["rule_id"] != item["rule_id"].strip()
                                    or item.get("repeat_policy") != "sum")):
            _refuse("existing_definition_requires_canonical_review")
    current = {"kind": "current_product", "quantity": "1", "unit": "unit",
               "required": True, "repeat_policy": "sum" if schema == 2 else "once", **deepcopy(policy)}
    if schema == 2:
        # Stable across preview/retry, without touching any existing rule IDs.
        current["rule_id"] = "base-repair-" + _hash({"product_id": product_id, "revision": revision})[:24]
        if any(item.get("rule_id") == current["rule_id"] for item in doc["items"]):
            _refuse("repair_rule_identity_conflict")
    return {"expected_revision": revision, "product_type": "composite", "schema_version": schema,
            "items": deepcopy(doc["items"]) + [current],
            "component_scopes": deepcopy(doc.get("component_scopes", {}))}


def _proposed_contract(product_id: str, payload: dict) -> dict:
    return {"product_id": product_id, "revision": payload["expected_revision"] + 1,
            **{field: deepcopy(payload[field]) for field in _PAYLOAD_FIELDS}}


def _error_code(exc: HTTPException) -> str:
    detail = exc.detail
    return str(detail.get("code", "bundle_validation_failed")) if isinstance(detail, dict) else "bundle_validation_failed"


def _context(expansion: dict, root: str) -> str:
    # Root definition changes are guarded separately. Also bind the reviewed
    # costs, options, resource policies, supplier rules and child definitions.
    facts = deepcopy(expansion.get("frozen_definitions", {}))
    if root in facts:
        facts[root] = {k: v for k, v in facts[root].items() if k in {"catalog_metadata", "catalog_product"}}
    return _hash(facts)


async def build_bundle_base_definition_repair_plan(
    db: Any, owner: str, product_ids: list[str], *, source_policy: dict,
) -> dict:
    """Read-only preview; no indexes, fences, cache bumps or snapshot writes."""
    owner = _text(owner)
    if not isinstance(product_ids, list) or not 1 <= len(product_ids) <= MAX_PRODUCTS:
        _refuse("explicit_bounded_product_selection_required")
    requested = [_text(value) for value in product_ids]
    if len(set(requested)) != len(requested):
        _refuse("duplicate_product_selection")
    policy = _source_policy(source_policy)
    rows, seen = [], set()
    for requested_id in requested:
        product = await _product(db, owner, requested_id)
        key = _product_key(product)
        if key in seen:
            _refuse("duplicate_product_alias_selection")
        seen.add(key)
        doc = await db[BUNDLES].find_one({"user_id": owner, "product_id": key}, {"_id": 0}) or {}
        before = _public_definition(doc)
        aliases = {str(product.get(field)) for field in ("id", "mezan_product_id", "salla_product_id") if product.get(field)} | {key}
        status = _inclusion(doc, aliases)
        row = {"requested_product_id": requested_id, "product_id": key,
               "before_definition": before, "before_definition_sha": _hash(before),
               "status": status if status in {"change", "already_included"} else "blocked",
               "blockers": [] if status in {"change", "already_included"} else [status],
               "proposed_payload": None, "proposed_definition": None}
        if status == "change":
            try:
                payload = _proposal(doc, key, policy)
                row["proposed_payload"] = payload
                row["proposed_definition"] = _proposed_contract(key, payload)
                before_expansion = await expand_bundle(db, owner, key, strict=False)
                if any(item.get("kind") == "product" and item.get("product_id") not in before_expansion["frozen_definitions"]
                       for item in doc["items"]):
                    _refuse("existing_definition_requires_canonical_review")
                proposed = {**before, **row["proposed_definition"]}
                preview = await expand_bundle(db, owner, key, strict=False, _overrides={key: proposed})
                row["catalog_context_sha"] = _context(before_expansion, key)
                root_facts = (preview.get("frozen_definitions") or {}).get(key, {})
                metadata = root_facts.get("catalog_metadata") or {}
                base_leaves = [leaf for leaf in preview["leaves"] if leaf.get("current_product_reference") and leaf.get("product_id") == key]
                row["source_metadata"] = {
                    "inventory_leaf": True, "quantity_per_sold_unit": "1",
                    "bundle_sourcing": deepcopy((metadata.get("operation_profile") or {}).get("bundle_sourcing", {})),
                    "base_unit_cost": base_leaves[0].get("unit_cost") if len(base_leaves) == 1 else None,
                    "customization_mappings_inferred": False,
                }
                row["validation"] = {"missing_options": deepcopy(preview.get("missing_options", [])),
                                     "unresolved_components": deepcopy(preview.get("unresolved_components", []))}
                if any(need.get("slot_id") and need.get("variant_required") for need in preview.get("missing_options", [])):
                    row["blockers"].append("current_product_variant_mapping_required")
                elif len(base_leaves) != 1 or base_leaves[0].get("unit_cost") is None:
                    row["blockers"].append("current_product_cost_unknown")
                if preview.get("unresolved_components"):
                    row["blockers"].append("bundle_component_policy_unresolved")
                if row["blockers"]:
                    row["status"] = "blocked"
            except HTTPException as exc:
                row["status"], row["blockers"] = "blocked", [_error_code(exc)]
            except BundleBaseDefinitionRepairRefused as exc:
                row["status"], row["blockers"] = "blocked", [exc.code]
        rows.append(row)
    plan = {"plan_version": PLAN_VERSION, "owner_id": owner,
            "requested_product_ids": requested, "source_policy": policy,
            "ready": all(row["status"] != "blocked" for row in rows), "products": rows}
    return {**plan, "manifest_sha": _hash(plan)}


async def apply_bundle_base_definition_repair(
    db: Any, owner: str, plan: dict, *, expected_manifest_sha: str, actor_id: str,
) -> dict:
    """Apply reviewed definitions through save_bundle, one product transaction each.

All selected definitions are preflighted before the first write. Concurrent
changes still fail the service's revision check. A multi-product failure can
leave earlier products committed; results describe them and the same manifest
is safe to retry. Cache invalidation follows each commit; retrying an already
applied manifest repeats invalidation without appending a second base rule.
"""
    owner, actor_id = _text(owner), _text(actor_id)
    if (not isinstance(plan, dict) or plan.get("plan_version") != PLAN_VERSION
            or plan.get("owner_id") != owner):
        _refuse("repair_manifest_owner_or_version_mismatch")
    unsigned = {k: v for k, v in plan.items() if k != "manifest_sha"}
    if (not isinstance(expected_manifest_sha, str) or len(expected_manifest_sha) != 64
            or expected_manifest_sha != plan.get("manifest_sha") or _hash(unsigned) != expected_manifest_sha):
        _refuse("repair_manifest_hash_mismatch")
    if plan.get("ready") is not True:
        _refuse("repair_manifest_not_ready")
    fresh = await build_bundle_base_definition_repair_plan(
        db, owner, plan.get("requested_product_ids"), source_policy=plan.get("source_policy"))
    original_rows, fresh_rows = plan.get("products"), fresh["products"]
    if not isinstance(original_rows, list) or len(original_rows) != len(fresh_rows):
        _refuse("repair_manifest_selection_mismatch")
    actions = []
    for old, new in zip(original_rows, fresh_rows):
        if (old.get("product_id") != new["product_id"]
                or old.get("requested_product_id") != new["requested_product_id"]):
            _refuse("repair_manifest_selection_mismatch")
        if old.get("before_definition_sha") != _hash(old.get("before_definition")):
            _refuse("repair_definition_hash_mismatch")
        if old.get("status") == "already_included":
            if new["status"] != "already_included" or old["before_definition_sha"] != new["before_definition_sha"]:
                _refuse("repair_definition_changed")
            actions.append((old, "already_included"))
            continue
        if old.get("status") != "change":
            _refuse("repair_manifest_not_ready")
        expected_payload = _proposal(old["before_definition"], old["product_id"], fresh["source_policy"])
        if (expected_payload != old.get("proposed_payload")
                or _proposed_contract(old["product_id"], expected_payload) != old.get("proposed_definition")):
            _refuse("repair_proposal_changed")
        if _contract(new["before_definition"]) == old["proposed_definition"]:
            actions.append((old, "already_applied"))
            continue
        if (new["status"] != "change" or old["before_definition_sha"] != new["before_definition_sha"]
                or old.get("catalog_context_sha") != new.get("catalog_context_sha")
                or old["proposed_payload"] != new["proposed_payload"]):
            _refuse("repair_definition_or_catalog_changed")
        actions.append((old, "change"))
    results = []
    for row, action in actions:
        key = row["product_id"]
        saved_differs = False
        if action == "already_included":
            results.append({"product_id": key, "status": action, "revision": row["before_definition"]["revision"]})
            continue
        if action == "change":
            try:
                saved = await save_bundle(db, owner, key, deepcopy(row["proposed_payload"]), actor_id)
                action = "applied"
                saved_differs = _contract(saved["definition"]) != row["proposed_definition"]
            except HTTPException as exc:
                # Concurrent replay of this exact manifest is still idempotent.
                current = await db[BUNDLES].find_one({"user_id": owner, "product_id": key}) or {}
                if _error_code(exc) != "bundle_revision_conflict" or _contract(current) != row["proposed_definition"]:
                    raise BundleBaseDefinitionRepairRefused(_error_code(exc), results=results) from None
                action = "already_applied"
        result = {"product_id": key, "status": action, "revision": row["proposed_definition"]["revision"]}
        try:
            await bump_product_cost_revision(db, owner)
        except Exception:
            # Commit may already exist. Report that fact; a manifest retry does
            # not append another rule and completes the pending invalidation.
            results.append({**result, "cache_invalidated": False})
            raise BundleBaseDefinitionRepairRefused("definition_committed_cache_invalidation_pending", results=results) from None
        results.append({**result, "cache_invalidated": True})
        if saved_differs:
            raise BundleBaseDefinitionRepairRefused("saved_definition_differs_from_preview", results=results)
    return {"ok": True, "manifest_sha": expected_manifest_sha, "products": results}
