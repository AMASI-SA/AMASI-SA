"""Independent operational state. Atomic Mongo CAS; no accounting capability.

One owner aggregate makes a movement, both transfer effects, allocations,
idempotency receipt and audit visible together. Size is checked before writing;
never truncate financial history to satisfy Mongo's document limit.
"""
from copy import deepcopy
from contextvars import ContextVar
from datetime import datetime, timezone
from hashlib import sha256
import json

from bson import BSON
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

STATES = "operational_balance_states_v1"
RECEIPTS = "operational_balance_receipts_v1"
MAX_STATE_BYTES = 12 * 1024 * 1024

# Set only by authenticated mutation routes; background reconciliation has no actor.
AUTHORIZATION_GUARD = ContextVar("operational_authorization_guard", default=None)


async def check_authorization():
    guard = AUTHORIZATION_GUARD.get()
    if guard is not None:
        await guard()


def fail(code, message="تعذر تنفيذ العملية", status=409):
    raise HTTPException(status, detail={"code": code, "message": message})


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), default=str).encode()).hexdigest()


def initial(owner):
    return {"_id": owner, "owner_id": owner, "revision": 0, "status": "draft",
            "started_at": None, "openings": {}, "movements": [], "requests": {},
            "audit": [], "engine": {}, "snapshot": None}


async def read(db, owner):
    return await db[STATES].find_one({"_id": owner, "owner_id": owner}) or initial(owner)


async def mutate(db, owner, operation):
    """Operation is async and side-effect free outside the returned aggregate.

    Callers revalidate source identity/permissions on every retry. No process
    mutex and no last-write-wins fallback; concurrent workers use the revision.
    """
    for _ in range(12):
        await check_authorization()
        before = await read(db, owner)
        after = deepcopy(before)
        result = await operation(after)
        await check_authorization()
        if after == before:
            return result
        after["revision"] = before["revision"] + 1
        if len(BSON.encode(after)) > MAX_STATE_BYTES:
            fail("operational_capacity_requires_archive", "بلغ السجل حد السعة؛ يلزم أرشفة معتمدة دون حذف التاريخ")
        # Recheck after every source read and immediately before the CAS write.
        await check_authorization()
        if before["revision"] == 0:
            try:
                await db[STATES].insert_one(after)
                return result
            except DuplicateKeyError:
                continue
        changed = await db[STATES].replace_one(
            {"_id": owner, "owner_id": owner, "revision": before["revision"]}, after)
        if changed.matched_count == 1:
            return result
    fail("operational_concurrent_change", "تغيرت البيانات بالتزامن؛ أعد المحاولة")


def request_key(action, request_id):
    return digest([action, request_id])


def replay(state, action, payload):
    key = request_key(action, payload["request_id"])
    old = state["requests"].get(key)
    if old:
        if old["hash"] != digest(payload):
            fail("operational_request_conflict", "رقم العملية مستخدم بمحتوى مختلف")
        return deepcopy(old["result"])


def remember(state, action, payload, result):
    state["requests"][request_key(action, payload["request_id"])] = {
        "hash": digest(payload), "result": deepcopy(result)}


def audit(state, actor, action, before, after, reason, source, at=None):
    stamp = at or now()
    event = {"id": digest([state["owner_id"], len(state["audit"]), action, stamp]),
             "actor_id": actor, "at": stamp, "action": action, "before": deepcopy(before),
             "after": deepcopy(after), "reason": reason, "source": source}
    state["audit"].append(event)
