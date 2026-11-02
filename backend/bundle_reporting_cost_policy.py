"""Future-only bundle reporting policy, separate from physical composition.

The owner fence is shared with bundle definition edits.  The only business write
is the policy field on the existing cost profile; no operational recipe or
historical order is changed by this endpoint.
"""
from copy import deepcopy
from datetime import datetime, timezone

from fastapi import Body, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from operational_atomic import operational_owner
from product_fulfillment_routes import _product, _product_key
from product_v2_details_routes import COST_PROFILES
from security_sensitive_routes import require_product_permission


BUNDLES = "mezan_bundle_definitions_v2"
POLICY_FIELD = "bundle_reporting_cost_policy"
_FIELDS = {"expected_revision", "expected_bundle_revision", "include_parent_base", "evidence_ref"}


def _reject(code, status=422):
    raise HTTPException(status, detail={"code": "bundle_reporting_policy_" + code})


def _policy(profile):
    saved = (profile or {}).get(POLICY_FIELD)
    if saved is None:
        return {"revision": 0, "include_parent_base": False, "evidence_ref": ""}
    if (not isinstance(saved, dict)
            or type(saved.get("revision")) is not int or saved["revision"] < 0
            or type(saved.get("include_parent_base")) is not bool
            or not isinstance(saved.get("evidence_ref"), str)):
        _reject("stored_policy_invalid", 409)
    return deepcopy(saved)


def _view(policy, definition):
    return {"revision": policy["revision"],
            "bundle_revision": (definition or {}).get("revision", 0),
            "include_parent_base": policy["include_parent_base"],
            "evidence_ref": policy["evidence_ref"]}


async def get_bundle_reporting_cost_policy(db, owner, product_id):
    product = await _product(db, owner, product_id)
    key = _product_key(product)
    definition = await db[BUNDLES].find_one(
        {"user_id": owner, "product_id": key}, {"_id": 0, "revision": 1, "product_type": 1})
    profile = await db[COST_PROFILES].find_one(
        {"user_id": owner, "salla_product_id": key}, {"_id": 0, POLICY_FIELD: 1})
    return _view(_policy(profile), definition)


async def save_bundle_reporting_cost_policy(db, owner, product_id, payload, actor_id):
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        _reject("invalid_payload")
    for field in ("expected_revision", "expected_bundle_revision"):
        if type(payload[field]) is not int or payload[field] < 0:
            _reject("invalid_revision")
    if type(payload["include_parent_base"]) is not bool:
        _reject("invalid_parent_base")
    evidence = payload["evidence_ref"]
    if not isinstance(evidence, str) or len(evidence) > 1000:
        _reject("invalid_evidence")
    evidence = evidence.strip()
    if payload["include_parent_base"] and not evidence:
        _reject("evidence_required")

    async def save(scoped):
        product = await _product(scoped, owner, product_id)
        key = _product_key(product)
        definition = await scoped[BUNDLES].find_one(
            {"user_id": owner, "product_id": key}, {"_id": 0, "revision": 1, "product_type": 1})
        if not definition or definition.get("product_type") != "composite":
            _reject("composite_required", 409)
        if definition.get("revision") != payload["expected_bundle_revision"]:
            _reject("bundle_revision_conflict", 409)
        query = {"user_id": owner, "salla_product_id": key}
        profile = await scoped[COST_PROFILES].find_one(query, {POLICY_FIELD: 1})
        old = _policy(profile)
        if old["revision"] != payload["expected_revision"]:
            _reject("revision_conflict", 409)
        policy = {"revision": old["revision"] + 1,
                  "include_parent_base": payload["include_parent_base"],
                  # Disabling must not discard the basis previously recorded.
                  "evidence_ref": evidence or old["evidence_ref"],
                  "updated_at": datetime.now(timezone.utc).isoformat(),
                  "updated_by": actor_id}
        selector = {**query}
        if (profile or {}).get(POLICY_FIELD) is None:
            selector[POLICY_FIELD] = None  # Both absent and explicit null.
        else:
            selector[POLICY_FIELD + ".revision"] = old["revision"]
        try:
            result = await scoped[COST_PROFILES].update_one(
                selector, {"$set": {POLICY_FIELD: policy}}, upsert=profile is None)
        except DuplicateKeyError:
            _reject("revision_conflict", 409)
        if result.matched_count != 1 and result.upserted_id is None:
            _reject("revision_conflict", 409)
        return _view(policy, definition)

    return await operational_owner(db, owner, save, profile="bundle_policy")


def register_bundle_reporting_cost_policy_routes(router, db, current_user):
    @router.get("/{product_id}/bundle-reporting-cost-policy")
    async def read(product_id: str, user=Depends(current_user)):
        owner = await require_product_permission(db, user, "products.cost.read")
        return await get_bundle_reporting_cost_policy(db, owner, product_id)

    @router.put("/{product_id}/bundle-reporting-cost-policy")
    async def update(product_id: str, payload: dict = Body(...), user=Depends(current_user)):
        owner = await require_product_permission(db, user, "products.cost.write")
        return await save_bundle_reporting_cost_policy(
            db, owner, product_id, payload, user.get("_mobile_actor_id") or user["id"])
