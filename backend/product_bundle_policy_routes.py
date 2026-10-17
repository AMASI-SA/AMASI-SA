"""Explicit Product V2 evidence used by bundle costing and sourcing.

Policies live on the existing product profiles. Composition edits never write
these profiles; this separate, permission-checked action is an owner decision.
"""
from copy import deepcopy
from datetime import datetime, timezone

from fastapi import Body, Depends, HTTPException
from operational_atomic import operational_owner
from product_fulfillment_routes import _product, _product_key
from product_fulfillment_rules import PRODUCT_OPERATION_PROFILES, PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS
from product_v2_details_routes import COST_PROFILES
from security_sensitive_routes import require_product_permission
from supplier_identity_service import require_supplier_v2


def reject(code, status=422):
    raise HTTPException(status, detail={"code": "bundle_policy_" + code})


def is_text_option(option):
    """Only textual customizations; absent type preserves historical catalogs."""
    return (isinstance(option, dict) and not option.get("values")
            and option.get("type") in (None, "", "text", "textarea", "long_text"))


async def get_policy(db, owner, product_id):
    product = await _product(db, owner, product_id)
    query = {"user_id": owner, "salla_product_id": _product_key(product)}
    operations = await db[PRODUCT_OPERATION_PROFILES].find_one(query) or {}
    costs = await db[COST_PROFILES].find_one(query) or {}
    return {"revision": operations.get("bundle_policy_revision", 0),
            "composition_components": deepcopy(costs.get("composition_components", {})),
            "sourcing": deepcopy(operations.get("bundle_sourcing", {}))}


async def save_policy(db, owner, product_id, payload, actor_id):
    if not isinstance(payload, dict) or set(payload) != {"expected_revision", "composition_components", "sourcing"}:
        reject("invalid_payload")
    if type(payload["expected_revision"]) is not int or payload["expected_revision"] < 0:
        reject("invalid_revision")
    components, sourcing = payload["composition_components"], payload["sourcing"]
    if not isinstance(components, dict) or len(components) > 500 or not isinstance(sourcing, dict):
        reject("invalid_payload")

    async def save(scoped):
        product = await _product(scoped, owner, product_id)
        query = {"user_id": owner, "salla_product_id": _product_key(product)}
        old = await get_policy(scoped, owner, product_id)
        if old["revision"] != payload["expected_revision"]:
            reject("revision_conflict", 409)
        bindings = {}
        for kind, collection in (("product_resource", PRODUCT_RESOURCE_BINDINGS), ("option_resource", BINDINGS)):
            for binding in await scoped[collection].find(query, {"_id": 0}).to_list(length=501):
                if binding.get("resource_id") and binding.get("id"):
                    bindings[kind + ":" + str(binding["id"])] = binding
        clean_components = {}
        for key, policy in components.items():
            if key not in bindings or not isinstance(policy, dict) or set(policy) - {"cost_treatment", "assembly_origin", "supplier_omission_supported", "evidence_ref"}:
                reject("unknown_component")
            cost, origin = policy.get("cost_treatment", "unknown"), policy.get("assembly_origin", "unknown")
            if not isinstance(cost, str) or not isinstance(origin, str) or cost not in {"independent", "included_in_base", "unknown"} or origin not in {"inhouse", "supplier", "purchased_complete", "unknown"}:
                reject("invalid_component_evidence")
            omission = policy.get("supplier_omission_supported", False)
            evidence = policy.get("evidence_ref", "")
            if type(omission) is not bool or not isinstance(evidence, str) or len(evidence) > 1000:
                reject("invalid_component_evidence")
            if (cost != "unknown" or origin != "unknown" or omission) and not evidence.strip():
                reject("evidence_required")
            clean_components[key] = {"cost_treatment": cost, "assembly_origin": origin,
                                     "supplier_omission_supported": omission, "evidence_ref": evidence.strip()}
        if set(sourcing) - {"stock_first", "allow_split", "supplier_id", "rules"} or sourcing.get("stock_first", True) is not True or type(sourcing.get("allow_split", False)) is not bool:
            reject("invalid_sourcing")
        options = {str(option.get("id")): option for option in product.get("options", [])}
        variants = {str(variant.get("id")) for variant in (product.get("variants") or product.get("skus") or [])}
        async def supplier(value):
            if value is not None:
                await require_supplier_v2(scoped, owner, value)
            return value
        rules = sourcing.get("rules", [])
        if not isinstance(rules, list) or len(rules) > 100:
            reject("invalid_rules")
        clean_rules, seen = [], set()
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) - {"rule_id", "conditions", "variant_id", "customization", "supplier_id", "allow_split"}:
                reject("invalid_rule")
            rid = rule.get("rule_id")
            if not isinstance(rid, str) or not rid.strip() or len(rid) > 200 or rid in seen:
                reject("duplicate_rule")
            seen.add(rid)
            vid = rule.get("variant_id")
            if vid is not None and (not isinstance(vid, str) or vid not in variants):
                reject("unknown_variant")
            conditions, custom = rule.get("conditions", []), rule.get("customization", {})
            if not isinstance(conditions, list) or len(conditions) > 100 or not isinstance(custom, dict) or len(custom) > 100:
                reject("invalid_rule")
            pairs = set()
            for condition in conditions:
                if not isinstance(condition, dict) or set(condition) != {"option_id", "value_id"}:
                    reject("invalid_condition")
                oid, value = condition["option_id"], condition["value_id"]
                if not isinstance(oid, str) or not isinstance(value, str) or oid not in options or value not in {str(v.get("id")) for v in options[oid].get("values", [])} or (oid, value) in pairs:
                    reject("unknown_option")
                pairs.add((oid, value))
            for oid, answer in custom.items():
                if oid not in options or not is_text_option(options[oid]) or not isinstance(answer, str) or not answer.strip() or len(answer) > 2000:
                    reject("invalid_customization")
            if not (vid or conditions or custom) or type(rule.get("allow_split", False)) is not bool:
                reject("invalid_rule")
            clean_rules.append({"rule_id": rid, "variant_id": vid, "conditions": deepcopy(conditions),
                                "customization": {k: v.strip() for k, v in custom.items()},
                                "supplier_id": await supplier(rule.get("supplier_id")), "allow_split": rule.get("allow_split", False)})
        revision = old["revision"] + 1
        stamp = {"revision": revision, "updated_by": actor_id, "updated_at": datetime.now(timezone.utc).isoformat()}
        clean_sourcing = {"stock_first": True, "allow_split": sourcing.get("allow_split", False),
                          "supplier_id": await supplier(sourcing.get("supplier_id")), "rules": clean_rules, **stamp}
        await scoped[COST_PROFILES].update_one(query, {"$set": {**query, "composition_components": clean_components,
             "composition_policy_evidence": stamp}}, upsert=True)
        await scoped[PRODUCT_OPERATION_PROFILES].update_one(query, {"$set": {**query, "bundle_policy_revision": revision,
             "bundle_sourcing": clean_sourcing}}, upsert=True)
        return {"revision": revision, "composition_components": clean_components, "sourcing": clean_sourcing}
    return await operational_owner(db, owner, save, profile="bundle_policy")


def register_bundle_policy_routes(router, db, current_user):
    @router.get("/{product_id}/bundle-policy")
    async def read(product_id: str, user=Depends(current_user)):
        owner = await require_product_permission(db, user, "products.cost.read")
        return await get_policy(db, owner, product_id)

    @router.put("/{product_id}/bundle-policy")
    async def update(product_id: str, payload: dict = Body(...), user=Depends(current_user)):
        owner = await require_product_permission(db, user, "products.cost.write")
        return await save_policy(db, owner, product_id, payload, user.get("_mobile_actor_id") or user["id"])
