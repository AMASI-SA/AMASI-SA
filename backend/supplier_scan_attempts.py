"""Durable scan admission, atomic completion and read-only outcome recovery.

The request row is also a fencing document: expiring it conflicts with any
older transaction trying to commit receipt writes. Never compensate a receipt
after an ambiguous commit. Read or fence the request instead.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from functools import partial

from fastapi import HTTPException
from pymongo import ReturnDocument, ReadPreference
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

ATTEMPTS = "mezan_supplier_scan_attempts_v1"
SESSIONS = "mezan_supplier_receiving_sessions_v1"
EXECUTION_SECONDS = 45
LEASE_SECONDS = 120
LOCK_FIELDS = {"scan_lock_token": "", "scan_lock_started_at": "", "scan_lock_expires_at": ""}
log = logging.getLogger(__name__)


class ScanDatabase:
    """Bind every read/write in the receipt callback to one Mongo transaction."""
    def __init__(self, db, session):
        self.db, self.session = db, session

    def __getitem__(self, name):
        collection, session = self.db[name], self.session

        class BoundCollection:
            def __getattr__(self, operation):
                return partial(getattr(collection, operation), session=session)

        return BoundCollection()

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return self[name]


async def transaction(db, callback):
    async with await db.client.start_session() as session:
        return await session.with_transaction(
            lambda tx: callback(ScanDatabase(db, tx)),
            read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority"),
            read_preference=ReadPreference.PRIMARY,
        )


async def server_now(db):
    # The same primary clock governs absence proof and execution admission,
    # independent of clock skew between application replicas.
    hello = await db.command("hello", read_preference=ReadPreference.PRIMARY)
    now = hello["localTime"]
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now


def attempt_key(owner, session_id, request_id):
    return uuid.uuid5(uuid.NAMESPACE_URL, repr((owner, session_id, request_id))).hex


async def settle_aborted(db, attempt, detail):
    """A competing commit either wins first or aborts; no receipt rollback."""
    async def settle(scoped):
        changed = await scoped[ATTEMPTS].update_one(
            {"_id": attempt["_id"], "state": "in_progress", "token": attempt["token"]},
            {"$set": {"state": "rejected", "detail": detail, "finished_at": datetime.now(timezone.utc)}},
        )
        if changed.modified_count:
            await scoped[SESSIONS].update_one(
                {"user_id": attempt["user_id"], "id": attempt["session_id"], "scan_lock_token": attempt["token"]},
                {"$unset": LOCK_FIELDS},
            )
    await transaction(db, settle)


async def expire_attempts(db):
    rows = await db[ATTEMPTS].find({
        "state": "in_progress", "$expr": {"$lte": ["$expires_at", "$$NOW"]},
    }).limit(100).to_list(100)
    for row in rows:
        await settle_aborted(db, row, {"code": "supplier_receiving_scan_expired"})


def install_reconciler(router, db):
    task = None

    async def sweep():
        while True:
            try:
                await expire_attempts(db)
            except Exception:
                log.exception("supplier scan reconciliation failed")
            await asyncio.sleep(5)

    @router.on_event("startup")
    async def start():
        nonlocal task
        await db[ATTEMPTS].create_index([("state", 1), ("expires_at", 1)])
        await db[ATTEMPTS].create_index([("user_id", 1), ("session_id", 1), ("state", 1)])
        task = asyncio.create_task(sweep())

    @router.on_event("shutdown")
    async def stop():
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def read_outcome(db, *, owner, session_id, request_id, proof, client_expires_at=None):
    read_started_at = await server_now(db) if client_expires_at is not None else None
    if client_expires_at is not None and client_expires_at.tzinfo is None:
        raise HTTPException(422, detail={"code": "supplier_receiving_scan_deadline_invalid"})
    primary = db.with_options(read_preference=ReadPreference.PRIMARY)
    row = await primary[ATTEMPTS].find_one(
        {"_id": attempt_key(owner, session_id, request_id)})
    # Alias re-scans point at the exact existing events, never at SKU matching.
    result = await proof(proof_db=primary, event_ids=(row or {}).get("event_ids"))
    result["client_request_id"] = request_id
    if result.get("committed"):
        result["outcome"] = "confirmed"
    elif row and row["state"] == "rejected":
        result.update(outcome="rejected", rejection=row.get("detail"))
        if row.get("preview"):
            result.update(row["preview"])
    elif result.get("cancelled"):
        result["outcome"] = "rejected"
    elif row and row["state"] == "in_progress":
        result["outcome"] = "in_progress"
    elif not row and not result.get("found") and client_expires_at is not None and client_expires_at <= read_started_at:
        # A later-arriving immutable POST cannot begin receipt execution after
        # this deadline. Read time precedes the primary lookup, avoiding a
        # pre-deadline absence being reclassified after a slow read.
        result.update(outcome="rejected", rejection={"code": "supplier_receiving_scan_not_admitted_before_deadline"})
    else:
        # An absent request is not proof that an HTTP POST never arrived.
        result["outcome"] = "unresolved"
    result["retry_after_ms"] = 1500 if result["outcome"] in {"in_progress", "unresolved"} else None
    return result


async def run_scan(db, *, owner, actor, session_id, request_id, shape, callback, recover, client_expires_at=None):
    key = attempt_key(owner, session_id, request_id or uuid.uuid4().hex)
    now = await server_now(db)
    if client_expires_at is not None:
        if not request_id or client_expires_at.tzinfo is None or client_expires_at > now + timedelta(minutes=5):
            raise HTTPException(422, detail={"code": "supplier_receiving_scan_deadline_invalid"})
        shape = {**shape, "client_expires_at": client_expires_at.isoformat()}
    attempt = {"_id": key, "user_id": owner, "session_id": session_id,
               "client_request_id": request_id, "shape": shape, "token": uuid.uuid4().hex,
               "state": "in_progress", "created_at": now,
               "expires_at": now + timedelta(seconds=LEASE_SECONDS)}

    async def admit(scoped):
        previous = await scoped[ATTEMPTS].find_one({"_id": key})
        if previous:
            if previous["shape"] != shape:
                raise HTTPException(409, detail={"code": "supplier_receiving_scan_request_conflict"})
            return previous
        if client_expires_at is not None and client_expires_at <= now:
            row = {**attempt, "state": "rejected", "detail": {"code": "supplier_receiving_scan_not_admitted_before_deadline"}}
            await scoped[ATTEMPTS].insert_one(row)
            return row
        locked = await scoped[SESSIONS].find_one_and_update(
            {"user_id": owner, "id": session_id, "opened_by": actor, "status": "open",
             "$or": [{"scan_lock_token": {"$exists": False}}, {"scan_lock_token": None},
                     {"scan_lock_expires_at": {"$lte": now}}]},
            {"$set": {"scan_lock_token": attempt["token"], "scan_lock_started_at": now,
                      "scan_lock_expires_at": attempt["expires_at"]}},
            return_document=ReturnDocument.AFTER,
        )
        row = dict(attempt)
        if not locked:
            current_session = await scoped[SESSIONS].find_one({"user_id": owner, "id": session_id})
            code = "supplier_receiving_scan_busy" if (current_session or {}).get("status") == "open" else "supplier_receiving_session_closed"
            row.update(state="rejected", detail={"code": code})
        await scoped[ATTEMPTS].insert_one(row)
        return row

    try:
        admitted = await transaction(db, admit)
    except DuplicateKeyError:
        admitted = await db[ATTEMPTS].find_one({"_id": key})
        if not admitted or admitted["shape"] != shape:
            raise HTTPException(409, detail={"code": "supplier_receiving_scan_request_conflict"})
    except BaseException:
        # Admission itself can commit while its acknowledgement is lost.
        try:
            await settle_aborted(db, attempt, {"code": "supplier_receiving_scan_admission_interrupted"})
        except Exception:
            log.exception("supplier scan admission awaiting reconciliation")
        raise
    if admitted["state"] == "rejected":
        raise HTTPException(409, detail=admitted["detail"])
    if admitted["token"] != attempt["token"]:
        return await recover()

    async def execute(scoped):
        # Admission may have committed after the client's deadline. It must
        # never turn a read-only proof of absence into a late receipt.
        selector = {"_id": key, "token": attempt["token"], "state": "in_progress"}
        if client_expires_at is not None:
            selector["$expr"] = {"$gt": [{"$literal": client_expires_at}, "$$NOW"]}
        current = await scoped[ATTEMPTS].find_one(selector)
        if not current:
            raise HTTPException(409, detail={"code": "supplier_receiving_scan_lease_lost"})
        result = await callback(scoped, attempt["token"])
        preview = result.get("requires_quantity_selection") is True
        finished = await scoped[ATTEMPTS].update_one(
            {"_id": key, "token": attempt["token"], "state": "in_progress",
             "$expr": {"$gt": ["$expires_at", "$$NOW"]}},
            {"$set": {"state": "rejected" if preview else "confirmed",
                      "event_ids": [event["id"] for event in result.get("scans", [])],
                      "preview": result if preview else None,
                      "detail": {"code": "supplier_receiving_quantity_selection_required"} if preview else None}},
        )
        if not finished.modified_count:
            raise HTTPException(409, detail={"code": "supplier_receiving_scan_lease_lost"})
        result.update(outcome="rejected" if preview else "confirmed", committed=not preview,
                      client_request_id=request_id or None)
        return result

    try:
        async with asyncio.timeout(EXECUTION_SECONDS):
            return await transaction(db, execute)
    except BaseException as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else {"code": "supplier_receiving_scan_aborted"}
        # Covers CancelledError too. A failed cleanup leaves the durable request
        # for the expiry reconciler; it never deletes already committed pieces.
        try:
            await settle_aborted(db, attempt, detail)
        except Exception:
            log.exception("supplier scan outcome awaiting reconciliation")
        if isinstance(exc, asyncio.CancelledError):
            raise
        if isinstance(exc, HTTPException):
            raise
        return await recover()
