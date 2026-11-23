"""Scoped, append-only reversal of a single posted manual operational movement."""
from fastapi import Depends, Path
from pydantic import BaseModel, ConfigDict, Field

from operational_balance_access import RESOURCE_KINDS, guard_access, load_access
from operational_balance_service import active_gate
from operational_balance_store import STATES, audit, digest, fail, mutate, now, remember, replay
from operational_financial_activity_runtime import checked_rows, prepare_in_thread
from operational_movement_reversal import build_reverse, index_documents, inspect, reversal_id, reversal_index


OPERATION = "movements.reverse"


class Reversal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(min_length=8, max_length=160)
    expected_session_scope: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=500)


def movement_resources(item):
    """Use the actually stored counterpart and every original workspace leg."""
    result = [(item.get("party_type"), item.get("party_id"))]
    if item.get("bank_id") or item.get("bank_kind"):
        result.append((item.get("bank_kind"), item.get("bank_id")))
    linked = item.get("workspace_resources", [])
    if not isinstance(linked, list) or any(not isinstance(r, dict) for r in linked):
        return ()
    result.extend((r.get("kind"), r.get("id")) for r in linked)
    if any(not isinstance(kind, str) or kind not in RESOURCE_KINDS
           or not isinstance(identity, str) or not identity for kind, identity in result):
        return ()
    return tuple(dict.fromkeys(result))


def may_reverse(policy, item, resources):
    actor = item.get("actor_id")
    return (bool(resources) and policy.can_operation(OPERATION)
            and (policy.owner_override or actor == policy.actor["id"])
            and all(policy.resource(kind, identity)["use"] and policy.can_read_movement(kind, identity, actor)
                    for kind, identity in resources))


def add_reversal_actions(items, state, policy, idx):
    """Enrich displayed rows using the existing register pass over movements."""
    if not items:
        return
    active = state.get("status") == "active" and policy.can_operation(OPERATION)
    if not active and not idx.reversed:
        return
    checked_documents = False
    for item in checked_rows(items):
        original = idx.originals.get(item["id"])
        if not original or item.get("summary") or item.get("informational") or item.get("expected"):
            continue
        resources = movement_resources(original)
        if not resources:
            continue
        reverse_of = original.get("reverse_of")
        if reverse_of or item["id"] in idx.reversed:
            if not all(policy.can_read_movement(kind, identity, original.get("actor_id"))
                       for kind, identity in resources):
                continue
            if isinstance(reverse_of, str) and reverse_of and item["id"] == reversal_id(reverse_of):
                item["reverse_of"] = reverse_of
            else:
                rid = reversal_id(item["id"])
                if idx.originals.get(rid, {}).get("reverse_of") == item["id"]:
                    item["reversal_id"] = rid
        elif active and original.get("source") in {"mezan2", "employee_app"} and may_reverse(policy, original, resources):
            if not checked_documents:
                index_documents(idx, state)
                checked_documents = True
            if inspect(state, original, index=idx) is None:
                item["can_reverse"] = True


async def reverse_movement(db, owner, actor, source, identity, payload):
    if payload.expected_session_scope != digest([owner, actor["id"]]):
        fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 409)
    policy = await load_access(db, actor, owner)
    policy.require_operation(OPERATION)
    stored = await db[STATES].find_one({"_id": owner, "owner_id": owner},
        {"_id": 0, "movements": {"$elemMatch": {"id": identity}}})
    first = next(iter((stored or {}).get("movements", [])), {})
    resources = movement_resources(first)
    if not may_reverse(policy, first, resources):
        fail("operational_workspace_not_found", "الحركة غير متاحة", 404)

    data = {**payload.model_dump(), "original_id": identity}
    action = "movement_reversal:" + actor["id"]
    stamp = now()

    async def apply(state):
        idx = await prepare_in_thread(reversal_index, state)
        original = idx.originals.get(identity)
        if original != first:
            fail("operational_reversal_changed", "تغيرت الحركة؛ افتحها مجددًا")
        prior = replay(state, action, data)
        rid = reversal_id(identity)
        result = {"id": rid, "reverse_of": identity}
        if identity in idx.reversed:
            existing = idx.originals.get(rid)
            if not existing or existing.get("reverse_of") != identity or (prior is not None and prior != result):
                fail("operational_reversal_conflict", "تعذر مطابقة القيد العكسي بسجله")
            return result
        if prior is not None or rid in idx.originals:
            fail("operational_reversal_conflict", "تعذر مطابقة القيد العكسي بسجله")
        await active_gate(db, owner, state)
        blocked = inspect(state, original, index=idx)
        if blocked:
            fail("operational_reversal_unavailable", blocked)
        reverse = build_reverse(original, rid, actor, data["reason"], stamp)
        reverse["source"] = source
        state["movements"].append(reverse)
        await prepare_in_thread(audit, state, actor["id"], "movement_saved", None, reverse, data["reason"], source, stamp)
        remember(state, action, data, result)
        return result

    # Includes real account membership, the separate grant and all source/destination
    # grants on every CAS retry, and immediately before the winning commit or replay.
    async with guard_access(db, actor, owner, OPERATION, resources, movement_actor=first.get("actor_id")):
        return await mutate(db, owner, apply, threaded_preparation=True)


def install_reversal_routes(router, db, current_user, scope):
    @router.post("/workspace/movements/{identity}/reverse")
    async def reverse(payload: Reversal, identity: str = Path(min_length=1, max_length=250), user=Depends(current_user)):
        actor, owner, source = await scope(db, user, "workspace")
        return await reverse_movement(db, owner, actor, source, identity, payload)
