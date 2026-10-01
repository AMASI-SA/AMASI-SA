"""Setup-only single-document CAS; audit and idempotency commit together.

No callback, control bypass, financial transaction, opening or ledger writer is
available here. The one mutable document is pinned by every financial consumer.
"""
from copy import deepcopy
from pymongo.errors import DuplicateKeyError

from accounting_module_contract import accounting_owner_id, require_owner, require_accounting_permission
from accounting_write_control import fresh_actor
from accounting_shipping_native_contract import (
    SETUP, MAX_ROWS, CourierInput, RateInput, BindingInput, digest, now, instant, fail,
)


async def read_setup(db, owner):
    return await db[SETUP].find_one({"_id": owner, "user_id": owner}, {"_id": 1, "user_id": 1, "version": 1, "couriers": 1, "contracts": 1, "bindings": 1, "requests": 1, "audit": 1, "usage_revision": 1}) or {
        "_id": owner, "user_id": owner, "version": 0, "couriers": [],
        "contracts": [], "bindings": [], "requests": {}, "audit": [],
    }


async def require_party(db, owner, setup, kind, identity):
    if kind == "courier":
        rows = [r for r in setup["couriers"] if r["courier_key"] == identity and r["status"] == "active"
                and r.get("confirmed_by") and r.get("confirmed_at")]
    elif kind == "store_driver":
        rows = await db.store_drivers.find({"user_id": owner, "id": identity,
            "status": {"$nin": ["inactive", "archived", "deleted"]},
            "archived": {"$ne": True}, "deleted": {"$ne": True},
            "is_archived": {"$ne": True}, "is_deleted": {"$ne": True}}, {"_id": 0}).to_list(2)
    else:
        rows = []
    if len(rows) != 1:
        fail("shipping_canonical_identity_required")
    return rows[0]


async def save_setup(db, owner, actor_id, payload):
    actor = await fresh_actor(db, {"id": actor_id})
    require_owner(actor)
    require_accounting_permission(actor, "accounting.rules.manage")
    if accounting_owner_id(actor) != owner:
        fail("shipping_owner_scope_mismatch", 403)
    if payload.confirmed is not True:
        fail("shipping_owner_confirmation_required")
    old = await read_setup(db, owner)
    key, fingerprint = digest(payload.request_id), digest(payload.model_dump(mode="json"))
    prior = old["requests"].get(key)
    if prior:
        if prior != fingerprint:
            fail("shipping_setup_idempotency_conflict")
        return {"version": old["version"], "state": "already_saved"}
    if payload.version != old["version"]:
        fail("shipping_setup_version_conflict")
    if old["version"] >= MAX_ROWS:
        fail("shipping_setup_history_limit")
    doc = deepcopy(old)
    at = now()
    approval = {"confirmed_by": actor_id, "confirmed_at": at, "version": old["version"] + 1}
    data = payload.model_dump(mode="json", exclude={"request_id", "version", "confirmed", "reason"})
    if isinstance(payload, CourierInput):
        if any(c["courier_key"] == payload.courier_key or set(c["salla_carrier_keys"]) & set(payload.salla_carrier_keys)
               for c in old["couriers"]):
            fail("shipping_courier_identity_conflict")
        if payload.courier_key in {"mandoob", "mandoob_riyadh", "pickup"}:
            fail("shipping_external_courier_required")
        doc["couriers"].append({**data, **approval, "status": "active"})
        action = "courier_confirmed"
    else:
        await require_party(db, owner, old, payload.party_type, payload.party_id)
        if isinstance(payload, RateInput):
            for r in old["contracts"]:
                if (r["party_type"], r["party_id"], r["context"]) != (payload.party_type, payload.party_id, payload.context):
                    continue
                if (payload.effective_to is None or instant(r["effective_from"]) < instant(payload.effective_to)) and (
                    r["effective_to"] is None or instant(payload.effective_from) < instant(r["effective_to"])):
                    fail("shipping_rate_policy_overlap")
            doc["contracts"].append({**data, **approval, "id": digest([owner, key]), "status": "approved"})
            action = "rate_confirmed"
        elif isinstance(payload, BindingInput):
            # Setup stores the owner's intended canonical ID only. It is not a
            # validated bank identity until the Track A port resolves at posting.
            doc["bindings"] = [r for r in old["bindings"] if (r["party_type"], r["party_id"]) != (payload.party_type, payload.party_id)]
            doc["bindings"].append({**data, **approval, "validation": "track_a_resolution_required"})
            action = "binding_confirmed"
        else:
            fail("shipping_setup_payload_invalid", 422)
    doc["version"] += 1
    doc["requests"][key] = fingerprint
    doc["audit"].append({**approval, "action": action, "request_hash": fingerprint,
                         "reason": payload.reason, "data": data})
    try:
        if old["version"] == 0:
            await db[SETUP].insert_one(doc)
        else:
            result = await db[SETUP].replace_one({"_id": owner, "user_id": owner, "version": old["version"]}, doc)
            if result.matched_count != 1:
                fail("shipping_setup_version_conflict")
    except DuplicateKeyError:
        fail("shipping_setup_version_conflict")
    return {"state": "saved", "version": doc["version"]}


async def pin_setup(db, owner):
    row = await read_setup(db, owner)
    if row["version"] == 0:
        fail("shipping_canonical_identity_required")
    result = await db[SETUP].update_one({"_id": owner, "user_id": owner, "version": row["version"]},
                                      {"$inc": {"usage_revision": 1}})
    if result.matched_count != 1:
        fail("shipping_setup_version_conflict")
    return row
