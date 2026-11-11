"""Durable, explicitly requested Campaign AI work outside shared event loops.

Manual monitoring creates recommendations; comparison only checks saved facts.
Neither kind approves or executes advertising changes. One owner slot coalesces
both kinds and all windows, and one global runner lease bounds subprocess work
across replicas. Existing results remain available during refresh or failure.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import sys
import threading
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

COLLECTION = "mezan_campaign_ai_shadow_jobs_v1"
RUNNER_ID = "shadow-comparison-runner"
LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 15
POLL_SECONDS = 3
MAX_JOB_SECONDS = 300
MAX_MONITOR_SECONDS = 480
MAX_ATTEMPTS = 3
CACHE_SECONDS = 7200
REQUEST_COOLDOWN_SECONDS = 60
JOB_KINDS = ("comparison", "monitor")


class ClaimLost(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _key(user_id: str, days: int):
    if not str(user_id).strip() or type(days) is not int or not 1 <= days <= 14:
        raise ValueError("campaign_shadow_scope_invalid")
    # One durable slot per owner, including requests for different day windows.
    # Changing days cannot build a backlog of overlapping heavy comparisons.
    return sha256(f"campaign-shadow-v1:{user_id}".encode()).hexdigest()


def _instant(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return None


def _public(row, *, now=None, requested_days=None, kind="comparison"):
    clock = now or _now()
    cached = (row.get("cached_results") or {}).get(kind) or {}
    result = cached.get("result") if isinstance(cached.get("result"), dict) else None
    if requested_days is not None and cached.get("days") != requested_days:
        result = None
    expires = _instant(cached.get("expires_at"))
    status = row.get("status") or "idle"
    if row.get("kind") != kind and status not in {"queued", "running"}:
        status = "idle"
    # Never expose lease tokens, owner IDs, raw exceptions or internal records.
    return {
        "result": result,
        "kind": kind,
        "generated_at": cached.get("generated_at") if result else None,
        "result_days": cached.get("days") if result else None,
        "stale": bool(result and (expires is None or expires <= clock)),
        "job": {
            "status": status,
            "kind": row.get("kind"),
            "active": status in {"queued", "running"},
            "request_id": row.get("request_id"),
            "days": row.get("days"),
            "requested_at": row.get("requested_at"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
            "error_code": row.get("error_code"),
            "poll_after_seconds": 5 if status in {"queued", "running"} else None,
        },
        "read_only": kind == "comparison",
        "provider_writes_performed": False,
        "analysis_may_call_openai": kind == "monitor",
        **({"openai_called": False} if kind == "comparison" else {}),
    }


def _scope(days, kind):
    if kind not in JOB_KINDS or (kind == "monitor" and days != 3):
        raise ValueError("campaign_job_scope_invalid")


async def read_status(db, user_id: str, *, days: int = 1, kind: str = "comparison"):
    _scope(days, kind)
    row = await db[COLLECTION].find_one({"_id": _key(user_id, days), "user_id": str(user_id)}) or {}
    return _public(row, requested_days=days, kind=kind)


async def request_refresh(db, user_id: str, *, days: int = 1, kind: str = "comparison"):
    """Atomic coalescing: browser refresh/read never calls this function."""
    _scope(days, kind)
    key, clock = _key(user_id, days), _now()
    query = {"_id": key, "status": {"$nin": ["queued", "running"]},
             "$or": [{"requested_at": {"$exists": False}},
                     {"requested_at": {"$lte": clock - timedelta(seconds=REQUEST_COOLDOWN_SECONDS)}}]}
    try:
        await db[COLLECTION].update_one(query, {
            "$set": {"kind": kind, "user_id": str(user_id), "days": days,
                     "status": "queued", "request_id": uuid.uuid4().hex,
                     "requested_at": clock, "attempts": 0, "error_code": None,
                     "started_at": None, "finished_at": None},
            "$unset": {"lease_token": "", "lease_until": "", "monitor_snapshot_id": ""},
            "$setOnInsert": {"created_at": clock},
        }, upsert=True)
    except DuplicateKeyError:
        # An existing active job or a recent request owns this same bounded key.
        pass
    response = await read_status(db, user_id, days=days, kind=kind)
    current = response["job"]
    accepted = current["active"] and current["kind"] == kind and current["days"] == days
    return {**response, "request_accepted": accepted,
            "request_blocked_by": None if accepted else "active_job" if current["active"] else "cooldown"}


def _live(key, token):
    return {"_id": key, "lease_token": token,
            "$expr": {"$gt": ["$lease_until", "$$NOW"]}}


async def _claim_runner(db, token):
    try:
        row = await db[COLLECTION].find_one_and_update(
            {"_id": RUNNER_ID},
            [{"$replaceWith": {"$cond": [
                {"$lte": [{"$ifNull": ["$lease_until", "$$NOW"]}, "$$NOW"]},
                {"$mergeObjects": ["$$ROOT", {"kind": "runner", "lease_token": {"$literal": token},
                    "lease_until": {"$add": ["$$NOW", LEASE_SECONDS * 1000]}}]},
                "$$ROOT",
            ]}}],
            upsert=True, return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return False
    return bool(row and row.get("lease_token") == token)


async def _release_runner(db, token):
    await db[COLLECTION].update_one({"_id": RUNNER_ID, "lease_token": token},
                                   [{"$set": {"lease_until": "$$NOW"}}])


async def claim_next(db):
    """One runner; crashed claims are retried at most three times."""
    pending = {"kind": {"$in": list(JOB_KINDS)},
               "$or": [{"status": "queued"}, {"status": "running",
                         "$expr": {"$lte": ["$lease_until", "$$NOW"]}}]}
    if not await db[COLLECTION].find_one(pending, {"_id": 1}):
        return None
    token = uuid.uuid4().hex
    if not await _claim_runner(db, token):
        return None
    try:
        # Permanently failed claims need an explicit user refresh to try again.
        await db[COLLECTION].update_many(
            {"kind": {"$in": list(JOB_KINDS)}, "status": "running", "attempts": {"$gte": MAX_ATTEMPTS},
             "$expr": {"$lte": ["$lease_until", "$$NOW"]}},
            [{"$set": {"status": "failed", "error_code": "worker_recovery_exhausted",
                       "finished_at": "$$NOW"}}],
        )
        row = await db[COLLECTION].find_one_and_update(
            {"kind": {"$in": list(JOB_KINDS)}, "attempts": {"$lt": MAX_ATTEMPTS},
             "$or": [{"status": "queued"},
                     {"status": "running", "$expr": {"$lte": ["$lease_until", "$$NOW"]}}]},
            [{"$set": {"status": "running", "lease_token": {"$literal": token},
                       "lease_until": {"$add": ["$$NOW", LEASE_SECONDS * 1000]},
                       "started_at": "$$NOW", "attempts": {"$add": ["$attempts", 1]},
                       "error_code": None}}],
            sort=[("requested_at", 1)], return_document=ReturnDocument.AFTER,
        )
        if row is None:
            await _release_runner(db, token)
        return row
    except BaseException:
        with suppress(Exception):
            await _release_runner(db, token)
        raise


async def renew_claim(db, row):
    token = row["lease_token"]
    for key in (RUNNER_ID, row["_id"]):
        result = await db[COLLECTION].update_one(
            _live(key, token),
            [{"$set": {"lease_until": {"$add": ["$$NOW", LEASE_SECONDS * 1000]}}}],
        )
        if result.matched_count != 1:
            raise RuntimeError("campaign_shadow_lease_lost")


async def _fail(db, row, code):
    await db[COLLECTION].update_one(
        {**_live(row["_id"], row["lease_token"]), "status": "running"},
        [{"$set": {"status": "failed", "error_code": {"$literal": code},
                   "finished_at": "$$NOW"}}],
    )


async def _owner_available(db, user_id, *, session=None):
    owner = await db.users.find_one({"$or": [{"id": user_id}, {"_id": user_id}]},
        {"role": 1, "disabled": 1, "is_active": 1, "deleted_at": 1}, session=session)
    return bool(owner and owner.get("role") == "owner" and not owner.get("disabled")
                and owner.get("is_active") is not False and not owner.get("deleted_at"))


async def _publish_monitor_snapshot(db, row, document):
    """Fence canonical advice and its source pointer in a short transaction."""
    from campaign_ai_monitor_legacy import RECOMMENDATION_COLLECTION
    if document.get("user_id") != row["user_id"] or not document.get("snapshot_id"):
        raise RuntimeError("campaign_monitor_snapshot_scope_invalid")
    document.update({"trigger": "manual", "automatic_execution": False,
                     "next_run_at": None, "decision_interval_hours": None,
                     "manual_job_request_id": row["request_id"],
                     "manual_job_status": "building"})

    async def publish(session):
        if not await _owner_available(db, row["user_id"], session=session):
            raise ClaimLost("owner_access_unavailable")
        runner = await db[COLLECTION].update_one(
            _live(RUNNER_ID, row["lease_token"]),
            [{"$set": {"publication_checked_at": "$$NOW"}}], session=session,
        )
        claim = await db[COLLECTION].update_one(
            {**_live(row["_id"], row["lease_token"]), "status": "running",
             "request_id": row["request_id"], "user_id": row["user_id"]},
            {"$set": {"monitor_snapshot_id": document["snapshot_id"]}}, session=session,
        )
        if runner.matched_count != 1 or claim.matched_count != 1:
            raise ClaimLost("campaign_monitor_publication_lease_lost")
        await db[RECOMMENDATION_COLLECTION].insert_one(dict(document), session=session)

    async with await db.client.start_session() as session:
        await session.with_transaction(publish, read_concern=ReadConcern("snapshot"),
                                       write_concern=WriteConcern("majority", j=True))


async def _build_monitor(db, row):
    from campaign_ai_monitor import RECOMMENDATION_COLLECTION, ensure_campaign_ai_indexes, run_campaign_ai_monitor
    from mezan_campaign_profit_loader import make_mezan_campaign_profit_loader
    # If a worker died after publishing the core analysis, keep that evidence
    # instead of billing another model run for this same explicit request.
    if row.get("monitor_snapshot_id"):
        prior = await db[RECOMMENDATION_COLLECTION].find_one({
            "user_id": row["user_id"], "snapshot_id": row["monitor_snapshot_id"],
            "manual_job_request_id": row["request_id"],
        }, {"_id": 0, "user_id": 0, "execution_targets": 0})
        if prior:
            if prior.get("manual_job_status") != "complete":
                prior["manual_job_status"] = "partial"
                prior["limitations"] = [*(prior.get("limitations") or []), "manual_worker_interrupted_after_core_analysis"]
                await db[RECOMMENDATION_COLLECTION].update_one(
                    {"user_id": row["user_id"], "snapshot_id": prior["snapshot_id"]},
                    {"$set": {"manual_job_status": "partial", "limitations": prior["limitations"]}},
                )
            return prior
    await ensure_campaign_ai_indexes(db)
    async def publish(document):
        await _publish_monitor_snapshot(db, row, document)
    result = await run_campaign_ai_monitor(
        db, row["user_id"], refresh_meta=False, recommendation_only=True,
        business_context_loader=make_mezan_campaign_profit_loader(db),
        snapshot_publisher=publish,
    )
    # The cached status never exposes internal execution targets or Mongo IDs.
    return {key: value for key, value in result.items() if key not in {"_id", "user_id", "execution_targets"}}


async def execute_claim(db, key, token, *, builder=None):
    """Child-process entry. Revalidate the actor and fence final publication."""
    row = await db[COLLECTION].find_one({**_live(key, token), "status": "running"})
    runner = await db[COLLECTION].find_one(_live(RUNNER_ID, token))
    if not row or not runner:
        return False
    if not await _owner_available(db, row["user_id"]):
        await _fail(db, row, "owner_access_unavailable")
        return False
    if row["kind"] == "monitor":
        result = await (builder or _build_monitor)(db, row)
    else:
        # Heavy imports stay out of the request handler and shared worker loop.
        if builder is None:
            from campaign_ai_unified_shadow import build_campaign_ai_unified_shadow
            builder = build_campaign_ai_unified_shadow
        result = await builder(db, row["user_id"], days=row["days"])
    cached = f"cached_results.{row['kind']}"
    async def publish(session):
        if not await _owner_available(db, row["user_id"], session=session):
            raise ClaimLost("owner_access_unavailable")
        runner = await db[COLLECTION].update_one(
            _live(RUNNER_ID, token), [{"$set": {"publication_checked_at": "$$NOW"}}], session=session,
        )
        saved = await db[COLLECTION].update_one(
            {**_live(key, token), "status": "running", "request_id": row["request_id"]},
            [{"$set": {"status": "complete", f"{cached}.result": {"$literal": result},
                       f"{cached}.days": row["days"], f"{cached}.generated_at": "$$NOW",
                       "finished_at": "$$NOW", "error_code": None,
                       f"{cached}.expires_at": {"$add": ["$$NOW", CACHE_SECONDS * 1000]}}}],
            session=session,
        )
        if runner.matched_count != 1 or saved.matched_count != 1:
            raise ClaimLost("campaign_job_publication_lease_lost")
    try:
        async with await db.client.start_session() as session:
            await session.with_transaction(publish, read_concern=ReadConcern("snapshot"),
                                           write_concern=WriteConcern("majority", j=True))
    except ClaimLost:
        return False
    return True


async def _stop_process(process):
    if process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
    if process.stdin is not None:
        process.stdin.close()


async def run_claim(db, row, *, launcher=None):
    launcher = launcher or asyncio.create_subprocess_exec
    process = None
    heartbeat = waiter = None
    try:
        process = await launcher(
            sys.executable, str(Path(__file__).resolve()), "--job", row["_id"], row["lease_token"],
            cwd=str(Path(__file__).resolve().parent), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )

        async def beat():
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                await renew_claim(db, row)

        heartbeat = asyncio.create_task(beat())
        waiter = asyncio.create_task(process.wait())
        done, _ = await asyncio.wait_for(
            asyncio.wait([heartbeat, waiter], return_when=asyncio.FIRST_COMPLETED),
            timeout=MAX_MONITOR_SECONDS if row.get("kind") == "monitor" else MAX_JOB_SECONDS,
        )
        if heartbeat in done:
            await heartbeat  # Lost lease must stop its old subprocess.
        elif waiter.result() != 0:
            await _fail(db, row, "analysis_failed")
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        await _fail(db, row, "analysis_timeout")
    except Exception:
        await _fail(db, row, "analysis_worker_unavailable")
    finally:
        if process is not None:
            await _stop_process(process)
        for task in (heartbeat, waiter):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(task for task in (heartbeat, waiter) if task is not None), return_exceptions=True)
        with suppress(Exception):
            await _release_runner(db, row["lease_token"])


async def ensure_indexes(db):
    await db[COLLECTION].create_index([("kind", 1), ("status", 1), ("requested_at", 1)],
                                     name="campaign_manual_job_queue")


async def run(db):
    """Dedicated background runtime hook; never attach this to FastAPI startup."""
    from background_worker_process import is_worker_process
    if not is_worker_process():
        raise RuntimeError("campaign_shadow_requires_background_worker")
    await ensure_indexes(db)
    while True:
        try:
            row = await claim_next(db)
            if row is not None:
                await run_claim(db, row)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient queue read must not terminate the other automatic jobs.
            pass
        await asyncio.sleep(POLL_SECONDS)


async def _child(key, token):
    from dotenv import load_dotenv
    from motor.motor_asyncio import AsyncIOMotorClient
    from runtime_mongo import main_client_options
    from mongo_observability import mongo_metrics
    from review_acceptance_config_guard import AcceptanceConfigDatabase
    load_dotenv(Path(__file__).resolve().parent / ".env")
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], **main_client_options(event_listener=mongo_metrics))
    try:
        return await execute_claim(AcceptanceConfigDatabase(client[os.environ["DB_NAME"]]), key, token)
    finally:
        client.close()


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--job":
        raise SystemExit(2)
    # The worker owns this pipe. Its death closes stdin even on a hard restart,
    # so orphaned comparison processes cannot outlive their supervising worker.
    def parent_closed():
        sys.stdin.buffer.read()
        os._exit(2)
    threading.Thread(target=parent_closed, daemon=True).start()
    try:
        raise SystemExit(0 if asyncio.run(_child(sys.argv[2], sys.argv[3])) else 1)
    except Exception:
        raise SystemExit(1) from None
