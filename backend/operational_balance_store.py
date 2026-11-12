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

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from operational_balance_archive import AUDIT_PAGES, ArchiveError, CapacityError, prepare, stage_pages, unpack

STATES = "operational_balance_states_v1"
RECEIPTS = "operational_balance_receipts_v1"
OPERATION_CLAIMS = "operational_balance_operation_claims_v1"
MAX_STATE_BYTES = 12 * 1024 * 1024

# Set only by authenticated mutation routes, including visible-page refresh.
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


async def _raw_state(db, owner):
    return await db[STATES].find_one({"_id": owner, "owner_id": owner}) or initial(owner)


async def logical_state(db, owner, stored):
    try:
        return await unpack(db, owner, stored)
    except ArchiveError:
        fail("operational_audit_archive_invalid", "تعذر التحقق من اكتمال سجل التدقيق التشغيلي", 503)


async def read(db, owner):
    return await logical_state(db, owner, await _raw_state(db, owner))


async def mutate(db, owner, operation):
    """Operation is async and side-effect free outside the returned aggregate.

    Callers revalidate source identity/permissions on every retry. No process
    mutex and no last-write-wins fallback; concurrent workers use the revision.
    """
    from operational_demand_refresh import demand_context, refresh_stage, run_calculation

    async def prepare_change(function, *args):
        # Only a visible refresh owns cancellable calculation here. Explicit
        # financial writers retain their existing admission and commit rules.
        if demand_context() is not None:
            return await run_calculation(function, *args)
        return function(*args)

    for _ in range(12):
        await check_authorization()
        with refresh_stage("snapshot_load"):
            stored = await _raw_state(db, owner)
            before = await logical_state(db, owner, stored)
            after = await prepare_change(deepcopy, before)
        result = await operation(after)
        await check_authorization()
        with refresh_stage("snapshot_compare"):
            if await prepare_change(lambda: after == before):
                return result
        after["revision"] = before["revision"] + 1
        try:
            with refresh_stage("snapshot_pack"):
                packed, pages = await prepare_change(prepare, owner, stored, before, after, MAX_STATE_BYTES)
        except ArchiveError:
            fail("operational_audit_archive_invalid", "تعذر التحقق من اكتمال سجل التدقيق التشغيلي", 503)
        except CapacityError:
            fail("operational_capacity_requires_archive", "بلغ السجل حد السعة؛ يلزم أرشفة معتمدة دون حذف التاريخ")

        async def commit():
            try:
                await stage_pages(db, pages)
            except ArchiveError:
                fail("operational_audit_archive_invalid", "تعذر التحقق من اكتمال سجل التدقيق التشغيلي", 503)
            # Recheck source identity/permissions immediately before the CAS.
            await check_authorization()
            if before["revision"] == 0:
                try:
                    await db[STATES].insert_one(packed)
                    return True
                except DuplicateKeyError:
                    return False
            changed = await db[STATES].replace_one(
                {"_id": owner, "owner_id": owner, "revision": before["revision"]}, packed)
            return changed.matched_count == 1

        # Demand refresh admits this write only while connected, then observes
        # its atomic result even if the page leaves. Other writers are unchanged.
        from operational_demand_refresh import run_commit
        with refresh_stage("snapshot_commit"):
            if await run_commit(commit):
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
    from operational_financial_activity import capture_after_balances
    capture_after_balances(state, event)
    state["audit"].append(event)


async def claim_movement(db, owner, actor, payload):
    """Pin a caller intent across owner changes before any financial CAS.

    The unique Mongo _id arbitrates concurrent processes. A claim never moves
    money and is deliberately retained after a failed/unknown attempt.
    """
    await check_authorization()
    expected = payload.get("expected_session_scope")
    if expected is not None and expected != digest([owner, actor]):
        fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ راجع العملية السابقة قبل المتابعة", 409)
    # Honor already saved WIP movements created before scope claims existed.
    historical = await db[STATES].find_one({"movements": {"$elemMatch": {
        "actor_id": actor, "request_id": payload["request_id"]}}}, {"owner_id": 1})
    if historical and historical["owner_id"] != owner:
        fail("operational_request_scope_conflict", "العملية محفوظة في نطاق سابق؛ لا يمكن نقلها بإعادة الإرسال", 409)
    identity = digest(["movement", actor, payload["request_id"]])
    intent = {key: value for key, value in payload.items() if key != "expected_session_scope"}
    claim = {"_id": identity, "owner_id": owner, "actor_id": actor, "payload_hash": digest(intent)}
    try:
        await db[OPERATION_CLAIMS].insert_one(claim)
    except DuplicateKeyError:
        previous = await db[OPERATION_CLAIMS].find_one({"_id": identity})
        if previous != claim:
            fail("operational_request_scope_conflict", "العملية مرتبطة بنطاق أو محتوى سابق؛ يلزم التحقق قبل إعادة إرسالها", 409)
    return identity
