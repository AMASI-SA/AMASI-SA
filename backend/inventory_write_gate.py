"""Owner-wide Mongo admission and transaction-fenced inventory emergency pause."""
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, ExecutionTimeout, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.read_preferences import ReadPreference
from pymongo.write_concern import WriteConcern

ACTIVE = ContextVar("inventory_admission", default=None)
LEASE_MS = 60000
COLLECTION = "mz2_inventory_admission"


def writes_paused(state):
    """Only an explicit, revisioned owner control decision opens this writer."""
    state = state or {}
    revision = state.get("inventory_control_revision")
    return not (state.get("physical_inventory_writes_paused") is False
                and type(revision) is int and revision > 0)


async def read_control(db, owner, *, session=None):
    collection = db.mz2_atomic_owners
    if session is None:
        collection = collection.with_options(read_concern=ReadConcern("majority"),
                                             read_preference=ReadPreference.PRIMARY)
    try:
        return await collection.find_one({"_id": owner}, session=session)
    except PyMongoError as exc:
        raise HTTPException(503, "physical_inventory_control_unavailable") from exc


def root_db(db):
    from accounting_write_control import AccountingDatabase
    return db.current() if isinstance(db, AccountingDatabase) else db


async def admitted(db, owner, operation, callback):
    db = root_db(db)
    if ACTIVE.get() is not None:
        raise HTTPException(409, "inventory_admission_nested")
    if writes_paused(await read_control(db, owner)):
        raise HTTPException(423, "physical_inventory_writes_paused")
    hello = await db.command("hello")
    if not hello.get("setName") or hello.get("logicalSessionTimeoutMinutes") is None:
        raise HTTPException(503, "inventory_requires_transactional_replica_set")
    attempt = uuid4().hex
    collection = db[COLLECTION].with_options(write_concern=WriteConcern("majority"))
    try:
        await collection.insert_one({"_id": owner})
    except DuplicateKeyError:
        pass
    try:
        acquired = await collection.find_one_and_update(
            {"_id": owner, "$expr": {"$lte": [{"$ifNull": ["$expires_at", datetime(1970, 1, 1, tzinfo=timezone.utc)]}, "$$NOW"]}},
            [{"$set": {"token": attempt, "operation": {"$literal": operation},
                       "started_at": "$$NOW", "expires_at": {"$add": ["$$NOW", LEASE_MS]}}}],
            return_document=ReturnDocument.AFTER, maxTimeMS=1000)
        if acquired is None:
            raise HTTPException(429, detail={"code": "inventory_owner_busy", "attempt_id": attempt,
                                            "automatic_retry": False})
    except (DuplicateKeyError, ExecutionTimeout):
        raise HTTPException(429, detail={"code": "inventory_owner_busy", "attempt_id": attempt,
                                        "automatic_retry": False})
    token = ACTIVE.set((owner, attempt))
    try:
        await db.mz2_inventory_attempts.with_options(write_concern=WriteConcern("majority")).insert_one(
            {"_id": attempt, "user_id": owner, "operation": operation,
             "started_at": acquired["started_at"], "state": "admitted"})
        return await callback()
    finally:
        ACTIVE.reset(token)
        # Never release a successor's lease. An uncertain release is allowed to
        # expire; it must not turn an already committed request into a replay.
        try:
            await collection.update_one({"_id": owner, "token": attempt},
                                        [{"$set": {"expires_at": "$$NOW", "finished_at": "$$NOW"}}])
            await db.mz2_inventory_attempts.update_one({"_id": attempt},
                [{"$set": {"state": "finished_check_source_for_outcome", "finished_at": "$$NOW"}}])
        except Exception:
            pass


async def fence(db, owner, session):
    active = ACTIVE.get()
    if active is None:
        return
    if active[0] != owner:
        raise HTTPException(409, "inventory_admission_owner_changed")
    # Updating the lease inside the transaction prevents a TTL takeover while
    # the transaction can still commit. A stale worker cannot start a later tx.
    result = await db[COLLECTION].update_one(
        {"_id": owner, "token": active[1], "$expr": {"$gt": ["$expires_at", "$$NOW"]}},
        {"$inc": {"fence_revision": 1}}, session=session)
    if result.matched_count != 1:
        raise HTTPException(409, "inventory_admission_expired")
    state = await read_control(db, owner, session=session)
    if writes_paused(state):
        raise HTTPException(423, "physical_inventory_writes_paused")


async def set_pause(db, *, user, paused, revision, reason):
    from accounting_write_control import fresh_actor
    from accounting_module_contract import require_owner
    from accounting_atomic import _owner_transaction
    db = root_db(db)
    actor = await fresh_actor(db, user)
    require_owner(actor)
    owner = actor["id"]
    if type(paused) is not bool or type(revision) is not int or revision < 0 or not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise HTTPException(422, "inventory_pause_control_invalid")
    async def change(scoped):
        current = await fresh_actor(scoped, user)
        require_owner(current)
        if current["id"] != owner:
            raise HTTPException(403, "inventory_pause_owner_changed")
        row = await scoped.mz2_atomic_owners.find_one({"_id": owner}) or {}
        if row.get("inventory_control_revision", 0) != revision:
            raise HTTPException(409, "inventory_pause_revision_changed")
        await scoped.mz2_atomic_owners.update_one({"_id": owner}, {"$set": {
            "physical_inventory_writes_paused": paused, "inventory_control_revision": revision + 1}})
        await scoped.mz2_inventory_control_audit.insert_one({"id": uuid4().hex, "user_id": owner,
            "actor_id": current["id"], "paused": paused, "revision": revision + 1,
            "reason": reason.strip(), "at": datetime.now(timezone.utc).isoformat()})
        return {"paused": paused, "revision": revision + 1}
    # Same permanent owner row: acknowledgement is a committed drain barrier.
    return await _owner_transaction(db, owner, change, control=True)
