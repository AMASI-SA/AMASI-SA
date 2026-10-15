"""Independent, lease-fenced V3 discovery and item-enrichment workers.

Discovery performs only sequential ``GET /orders`` pagination and durable queue
writes. The independent queue worker is the only component which performs
Order Details / Order Items enrichment. P0 remains shadow-only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from .config import (
    CONFIG_ENV_ALLOWLIST,
    DISCOVERY_INITIAL_LOOKBACK_DAYS,
    DISCOVERY_WINDOW_DAYS,
    EVENTS_COLLECTION,
    JOBS_COLLECTION,
    LEASES_COLLECTION,
    LEASE_HEARTBEAT_SECONDS,
    LEASE_SECONDS,
    MAX_CONCURRENCY,
    MAX_DISCOVERED_ORDERS_PER_RUN,
    MAX_DISCOVERY_PAGES_PER_RUN,
    MAX_EVENT_OUTBOX_REPAIRS_PER_CYCLE,
    MAX_INTEGRATIONS_PER_CYCLE,
    MAX_JOB_ATTEMPTS,
    MAX_JOBS_PER_CYCLE,
    PARITY_AUDITS_COLLECTION,
    PARITY_EVIDENCE_COLLECTION,
    PARITY_RUNS_COLLECTION,
    PAGINATION_SESSION_SECONDS,
    RECOVERY_INTERVAL_SECONDS,
    SHADOW_COLLECTION,
    SHADOW_ENABLED_ENV,
    STATE_COLLECTION,
    TTL_SECONDS,
    shadow_collection,
)
from .gateway import SallaOrdersGateway
from .ingestion import enqueue_shadow_job, repair_event_job_outbox_once
from .shadow import SallaOrdersShadowEngine


log = logging.getLogger("salla.orders_v3.worker")

# Compatibility alias retained for the existing internal import surface.
MAX_PAGES_PER_RUN = MAX_DISCOVERY_PAGES_PER_RUN

_task: Optional[asyncio.Task] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_environment() -> None:
    unknown = sorted(
        key
        for key in os.environ
        if key.startswith("SALLA_ORDERS_V3_") and key not in CONFIG_ENV_ALLOWLIST
    )
    if unknown:
        raise RuntimeError(
            "Salla Orders V3 environment key is not allowlisted: "
            + ", ".join(unknown)
        )


def shadow_enabled() -> bool:
    _validate_environment()
    return os.environ.get(SHADOW_ENABLED_ENV, "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


async def ensure_salla_orders_v3_indexes(db: Any) -> None:
    await shadow_collection(db, SHADOW_COLLECTION).create_index(
        [("user_id", ASCENDING), ("store_id", ASCENDING), ("order_number", ASCENDING)],
        unique=True,
        name="salla_orders_v3_shadow_identity",
    )
    await shadow_collection(db, SHADOW_COLLECTION).create_index(
        "updated_at",
        expireAfterSeconds=TTL_SECONDS,
        name="salla_orders_v3_shadow_ttl",
    )
    await shadow_collection(db, EVENTS_COLLECTION).create_index(
        "outbox_expires_at",
        expireAfterSeconds=0,
        name="salla_orders_v3_events_ttl",
    )
    await shadow_collection(db, JOBS_COLLECTION).create_index(
        [("status", ASCENDING), ("next_attempt_at", ASCENDING)],
        name="salla_orders_v3_jobs_due",
    )
    await shadow_collection(db, JOBS_COLLECTION).create_index(
        "terminal_expires_at",
        expireAfterSeconds=0,
        name="salla_orders_v3_jobs_terminal_ttl",
    )
    await shadow_collection(db, STATE_COLLECTION).create_index(
        [("user_id", ASCENDING), ("store_id", ASCENDING)],
        unique=True,
        name="salla_orders_v3_state_identity",
    )
    await shadow_collection(db, LEASES_COLLECTION).create_index(
        "expires_at",
        expireAfterSeconds=0,
        name="salla_orders_v3_leases_ttl",
    )
    await shadow_collection(db, PARITY_AUDITS_COLLECTION).create_index(
        [
            ("owner_id", ASCENDING),
            ("gate", ASCENDING),
            ("evidence_head_sha", ASCENDING),
            ("evidence_run_id", ASCENDING),
            ("audit_id", ASCENDING),
        ],
        unique=True,
        name="salla_orders_v3_parity_audit_identity",
    )
    await shadow_collection(db, PARITY_AUDITS_COLLECTION).create_index(
        "audited_at",
        expireAfterSeconds=TTL_SECONDS,
        name="salla_orders_v3_parity_audit_ttl",
    )
    await shadow_collection(db, PARITY_RUNS_COLLECTION).create_index(
        "expires_at",
        expireAfterSeconds=0,
        name="salla_orders_v3_parity_run_ttl",
    )
    await shadow_collection(db, PARITY_EVIDENCE_COLLECTION).create_index(
        "expires_at",
        expireAfterSeconds=0,
        name="salla_orders_v3_parity_evidence_ttl",
    )


async def _acquire_recovery_lease(
    db: Any,
    state_key: str,
    *,
    user_id: str,
    store_id: str,
    now: datetime,
) -> Optional[dict[str, Any]]:
    token = str(uuid.uuid4())
    state = shadow_collection(db, STATE_COLLECTION)
    try:
        row = await state.find_one_and_update(
            {
                "_id": state_key,
                "$or": [
                    {"recovery_lease_expires_at": {"$lte": now}},
                    {"recovery_lease_expires_at": {"$exists": False}},
                ],
            },
            {
                "$setOnInsert": {
                    "_id": state_key,
                    "user_id": str(user_id),
                    "store_id": str(store_id),
                    "created_at": now,
                    "shadow_only": True,
                },
                "$set": {
                    "recovery_lease_token": token,
                    "recovery_lease_acquired_at": now,
                    "recovery_lease_heartbeat_at": now,
                    "recovery_lease_expires_at": (
                        now + timedelta(seconds=LEASE_SECONDS)
                    ),
                },
                "$inc": {"recovery_lease_epoch": 1},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return None
    if row and row.get("recovery_lease_token") == token:
        return {
            "token": token,
            "epoch": int(row.get("recovery_lease_epoch") or 0),
        }
    return None


async def _heartbeat_recovery_lease(
    db: Any,
    state_key: str,
    token: str,
    *,
    epoch: int,
    now: datetime,
) -> bool:
    result = await shadow_collection(db, STATE_COLLECTION).update_one(
        {
            "_id": state_key,
            "recovery_lease_token": token,
            "recovery_lease_epoch": int(epoch),
            "recovery_lease_expires_at": {"$gt": now},
        },
        {"$set": {
            "recovery_lease_heartbeat_at": now,
            "recovery_lease_expires_at": now + timedelta(seconds=LEASE_SECONDS),
        }},
    )
    return getattr(result, "matched_count", 0) == 1


async def _recovery_heartbeat_loop(
    db: Any,
    *,
    state_key: str,
    token: str,
    epoch: int,
    stop: asyncio.Event,
    lost: asyncio.Event,
    interval: float,
    clock: Callable[[], datetime],
) -> None:
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.01, interval))
            return
        except asyncio.TimeoutError:
            pass
        try:
            owned = await _heartbeat_recovery_lease(
                db,
                state_key,
                token,
                epoch=epoch,
                now=clock(),
            )
        except Exception:
            lost.set()
            return
        if not owned:
            lost.set()
            return


async def _release_recovery_lease(
    db: Any,
    state_key: str,
    token: str,
    *,
    epoch: int,
    now: Optional[datetime] = None,
) -> None:
    released_at = now or _utcnow()
    await shadow_collection(db, STATE_COLLECTION).update_one(
        {
            "_id": state_key,
            "recovery_lease_token": token,
            "recovery_lease_epoch": int(epoch),
        },
        {"$set": {
            "recovery_lease_expires_at": released_at,
            "recovery_lease_released_at": released_at,
        }},
    )


def _job_availability_query(now: datetime) -> dict[str, Any]:
    return {
        "$or": [
            {
                "status": {"$in": ["pending", "retrying"]},
                "next_attempt_at": {"$lte": now},
            },
            {
                "status": "processing",
                "lease_expires_at": {"$lte": now},
            },
        ]
    }


async def claim_due_shadow_job(
    db: Any,
    *,
    now: Optional[datetime] = None,
    job_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Atomically claim one due job and fence it with token plus epoch."""
    now = now or _utcnow()
    jobs = shadow_collection(db, JOBS_COLLECTION)
    availability = _job_availability_query(now)
    identity = [{"_id": str(job_id)}] if job_id else []

    # A newer signal gets its own budget without mutating attempts at enqueue
    # time. Claiming that revision atomically starts it at attempt one.
    reset_query = {
        "$and": [
            *identity,
            availability,
            {"attempt_budget_reset_required": True},
        ]
    }
    token = str(uuid.uuid4())
    common_set = {
        "status": "processing",
        "lease_token": token,
        "processing_started_at": now,
        "heartbeat_at": now,
        "lease_expires_at": now + timedelta(seconds=LEASE_SECONDS),
        "updated_at": now,
    }
    reset = await jobs.find_one_and_update(
        reset_query,
        {
            "$set": {**common_set, "attempts": 1},
            "$inc": {"lease_epoch": 1, "queue_revision": 1},
            "$unset": {
                "terminal_expires_at": "",
                "attempt_budget_reset_required": "",
            },
        },
        sort=[("next_attempt_at", ASCENDING), ("_id", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )
    if reset:
        return reset

    due_query = {
        "$and": [
            *identity,
            availability,
            {"attempt_budget_reset_required": {"$ne": True}},
            {"$or": [
                {"attempts": {"$lt": MAX_JOB_ATTEMPTS}},
                {"attempts": {"$exists": False}},
            ]},
        ]
    }
    return await jobs.find_one_and_update(
        due_query,
        {
            "$set": common_set,
            "$inc": {
                "attempts": 1,
                "lease_epoch": 1,
                "queue_revision": 1,
            },
            "$unset": {"terminal_expires_at": ""},
        },
        sort=[("next_attempt_at", ASCENDING), ("_id", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )


async def heartbeat_shadow_job_lease(
    db: Any,
    *,
    job_id: str,
    lease_token: str,
    now: Optional[datetime] = None,
    lease_epoch: Optional[int] = None,
    signal_revision: Optional[int] = None,
) -> bool:
    """Extend exactly the current worker lease; stale workers fail closed."""
    now = now or _utcnow()
    query: dict[str, Any] = {
        "_id": str(job_id),
        "status": "processing",
        "lease_token": str(lease_token),
        "lease_expires_at": {"$gt": now},
    }
    if lease_epoch is not None:
        query["lease_epoch"] = int(lease_epoch)
    if signal_revision is not None:
        query["signal_revision"] = int(signal_revision)
    result = await shadow_collection(db, JOBS_COLLECTION).update_one(
        query,
        {"$set": {
            "heartbeat_at": now,
            "lease_expires_at": now + timedelta(seconds=LEASE_SECONDS),
            "updated_at": now,
        }},
    )
    return getattr(result, "matched_count", 0) == 1


async def _job_heartbeat_loop(
    db: Any,
    *,
    job_id: str,
    lease_token: str,
    lease_epoch: int,
    signal_revision: int,
    stop: asyncio.Event,
    lost: asyncio.Event,
    interval: float,
) -> None:
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.01, interval))
            return
        except asyncio.TimeoutError:
            pass
        try:
            owned = await heartbeat_shadow_job_lease(
                db,
                job_id=job_id,
                lease_token=lease_token,
                lease_epoch=lease_epoch,
                signal_revision=signal_revision,
            )
        except Exception:
            lost.set()
            return
        if not owned:
            lost.set()
            return


async def _release_superseded_job(
    db: Any,
    *,
    job_id: str,
    lease_token: str,
    lease_epoch: int,
    signal_revision: int,
    now: datetime,
) -> bool:
    """Release an owned stale revision so its successor can be claimed."""
    result = await shadow_collection(db, JOBS_COLLECTION).update_one(
        {
            "_id": str(job_id),
            "status": "processing",
            "lease_token": str(lease_token),
            "lease_epoch": int(lease_epoch),
            "signal_revision": {"$gt": int(signal_revision)},
            "lease_expires_at": {"$gt": now},
        },
        {
            "$set": {
                "status": "pending",
                "next_attempt_at": now,
                "updated_at": now,
            },
            "$unset": {
                "lease_token": "",
                "lease_expires_at": "",
                "heartbeat_at": "",
                "processing_started_at": "",
                "terminal_expires_at": "",
            },
            "$inc": {"queue_revision": 1},
        },
    )
    return getattr(result, "matched_count", 0) == 1


async def _finalize_shadow_job(
    db: Any,
    *,
    jobs: Any,
    job: dict[str, Any],
    result: dict[str, Any],
    lease_token: str,
    lease_epoch: int,
    signal_revision: int,
    attempts: int,
    fixed_now: Optional[datetime],
    heartbeat_lost: asyncio.Event,
) -> dict[str, Any]:
    """Commit one outcome while the exact, unexpired worker fence is owned."""
    superseded_check_at = fixed_now or _utcnow()
    if await _release_superseded_job(
        db,
        job_id=str(job["_id"]),
        lease_token=lease_token,
        lease_epoch=lease_epoch,
        signal_revision=signal_revision,
        now=superseded_check_at,
    ):
        return {"status": "superseded", "attempts": attempts, "result": result}

    still_owned = False
    if not heartbeat_lost.is_set():
        ownership_check_at = fixed_now or _utcnow()
        still_owned = await heartbeat_shadow_job_lease(
            db,
            job_id=str(job["_id"]),
            lease_token=lease_token,
            lease_epoch=lease_epoch,
            signal_revision=signal_revision,
            now=ownership_check_at,
        )
    if not still_owned:
        superseded_release_at = fixed_now or _utcnow()
        if await _release_superseded_job(
            db,
            job_id=str(job["_id"]),
            lease_token=lease_token,
            lease_epoch=lease_epoch,
            signal_revision=signal_revision,
            now=superseded_release_at,
        ):
            return {
                "status": "superseded",
                "attempts": attempts,
                "result": result,
            }
        return {"status": "lost_lease", "attempts": attempts, "result": result}

    candidate = result.get("compatibility_order")
    candidate_valid = bool(
        isinstance(candidate, dict)
        and candidate.get("items_authoritative") is True
        and candidate.get("items_payload_valid") is True
        and candidate.get("needs_items_enrichment") is False
        and type(candidate.get("signal_revision")) is int
        and candidate.get("signal_revision") == signal_revision
        and type(candidate.get("items_success_signal_revision")) is int
        and candidate.get("items_success_signal_revision") == signal_revision
        and str(candidate.get("order_id") or "").strip()
        == str(job.get("internal_order_id") or "").strip()
        and str(candidate.get("order_number") or "").strip()
        == str(job.get("order_number") or "").strip()
        and isinstance(candidate.get("products"), list)
    )
    completed = bool(
        result.get("ok")
        and result.get("items_sync_status") == "succeeded"
        and result.get("items_payload_valid") is True
        and candidate_valid
    )
    if not completed and result.get("items_sync_status") == "succeeded":
        result = {**result, "error": "invalid_snapshot_candidate"}
    # No await occurs between this fresh sample and the terminal Mongo CAS.
    # The query therefore fences against the lease at the actual commit edge.
    finalized_at = fixed_now or _utcnow()
    unset = {
        "lease_token": "",
        "lease_expires_at": "",
        "heartbeat_at": "",
        "processing_started_at": "",
        "attempt_budget_reset_required": "",
    }
    if completed:
        status = "completed"
        patch = {
            "status": status,
            "completed_at": finalized_at,
            "terminal_expires_at": finalized_at + timedelta(seconds=TTL_SECONDS),
            "last_error": None,
            "updated_at": finalized_at,
            "compatibility_order": deepcopy(candidate),
            "snapshot_signal_revision": signal_revision,
            "snapshot_saved_at": finalized_at,
        }
    else:
        status = "failed" if attempts >= MAX_JOB_ATTEMPTS else "retrying"
        delay = min(300, 2 ** min(max(attempts, 1), 8))
        patch = {
            "status": status,
            "last_error": (
                result.get("error_type")
                or result.get("error")
                or "items_enrichment_failed"
            ),
            "next_attempt_at": finalized_at + timedelta(seconds=delay),
            "updated_at": finalized_at,
        }
        if status == "failed":
            patch["terminal_expires_at"] = (
                finalized_at + timedelta(seconds=TTL_SECONDS)
            )

    final = await jobs.update_one(
        {
            "_id": job["_id"],
            "status": "processing",
            "lease_token": lease_token,
            "lease_epoch": lease_epoch,
            "signal_revision": signal_revision,
            "lease_expires_at": {"$gt": finalized_at},
        },
        {"$set": patch, "$unset": unset, "$inc": {"queue_revision": 1}},
    )
    if getattr(final, "matched_count", 0) != 1:
        superseded_release_at = fixed_now or _utcnow()
        if await _release_superseded_job(
            db,
            job_id=str(job["_id"]),
            lease_token=lease_token,
            lease_epoch=lease_epoch,
            signal_revision=signal_revision,
            now=superseded_release_at,
        ):
            return {
                "status": "superseded",
                "attempts": attempts,
                "result": result,
            }
        return {"status": "lost_lease", "attempts": attempts, "result": result}
    return {"status": status, "attempts": attempts, "result": result}


async def process_shadow_job(
    db: Any,
    job: dict[str, Any],
    *,
    engine: Optional[SallaOrdersShadowEngine] = None,
    now: Optional[datetime] = None,
    heartbeat_interval: float = LEASE_HEARTBEAT_SECONDS,
) -> dict[str, Any]:
    """Enrich one claimed job and finalize only while its fence is owned."""
    fixed_now = now
    if not job.get("lease_token"):
        claim_now = fixed_now or _utcnow()
        claimed = await claim_due_shadow_job(
            db,
            now=claim_now,
            job_id=str(job.get("_id") or ""),
        )
        if not claimed:
            return {
                "status": "not_claimed",
                "attempts": int(job.get("attempts") or 0),
            }
        job = claimed

    engine = engine or SallaOrdersShadowEngine(db)
    jobs = shadow_collection(db, JOBS_COLLECTION)
    token = str(job.get("lease_token") or "")
    lease_epoch = int(job.get("lease_epoch") or 0)
    signal_revision = int(job.get("signal_revision") or 0)
    attempts = int(job.get("attempts") or 0)
    ownership_now = fixed_now or _utcnow()
    try:
        owned = await heartbeat_shadow_job_lease(
            db,
            job_id=str(job["_id"]),
            lease_token=token,
            lease_epoch=lease_epoch,
            signal_revision=signal_revision,
            now=ownership_now,
        )
    except Exception:
        owned = False
    if not owned:
        return {"status": "lost_lease", "attempts": attempts}

    stop = asyncio.Event()
    lost = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _job_heartbeat_loop(
            db,
            job_id=str(job["_id"]),
            lease_token=token,
            lease_epoch=lease_epoch,
            signal_revision=signal_revision,
            stop=stop,
            lost=lost,
            interval=heartbeat_interval,
        )
    )

    async def fence_before_items() -> bool:
        if lost.is_set():
            return False
        fence_now = fixed_now or _utcnow()
        try:
            fence_owned = await heartbeat_shadow_job_lease(
                db,
                job_id=str(job["_id"]),
                lease_token=token,
                lease_epoch=lease_epoch,
                signal_revision=signal_revision,
                now=fence_now,
            )
        except Exception:
            fence_owned = False
        if not fence_owned:
            lost.set()
        return fence_owned

    try:
        try:
            prepare = getattr(engine, "prepare_order_snapshot", None)
            call_args = {
                "user_id": str(job.get("user_id") or ""),
                "store_id": str(job.get("store_id") or ""),
                "light_order": job.get("light_order") or {},
                "event_created_at": job.get("event_created_at"),
            }
            if callable(prepare):
                result = await prepare(
                    **call_args,
                    signal_revision=signal_revision,
                    before_items=fence_before_items,
                )
            else:
                result = await engine.sync_order(**call_args)
        except Exception as exc:
            result = {
                "ok": False,
                "items_sync_status": "failed",
                "items_payload_valid": False,
                "error_type": type(exc).__name__,
            }

        return await _finalize_shadow_job(
            db,
            jobs=jobs,
            job=job,
            result=result,
            lease_token=token,
            lease_epoch=lease_epoch,
            signal_revision=signal_revision,
            attempts=attempts,
            fixed_now=fixed_now,
            heartbeat_lost=lost,
        )
    finally:
        # The periodic heartbeat remains live through the terminal Mongo CAS.
        stop.set()
        await heartbeat_task


async def _fail_one_exhausted_stale_job(db: Any, *, now: datetime) -> bool:
    row = await shadow_collection(db, JOBS_COLLECTION).find_one_and_update(
        {
            "status": "processing",
            "attempts": {"$gte": MAX_JOB_ATTEMPTS},
            "attempt_budget_reset_required": {"$ne": True},
            "lease_expires_at": {"$lte": now},
        },
        {
            "$set": {
                "status": "failed",
                "last_error": "worker_lease_expired_at_attempt_limit",
                "terminal_expires_at": now + timedelta(seconds=TTL_SECONDS),
                "updated_at": now,
            },
            "$unset": {
                "lease_token": "",
                "lease_expires_at": "",
                "heartbeat_at": "",
                "processing_started_at": "",
            },
            "$inc": {"queue_revision": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    return row is not None


async def run_due_jobs_once(
    db: Any,
    *,
    limit: int = MAX_JOBS_PER_CYCLE,
) -> dict[str, int]:
    safe_limit = max(1, min(int(limit), MAX_JOBS_PER_CYCLE))
    outcomes: list[dict[str, Any]] = []
    claimed_count = 0
    claim_reservations = 0
    counter_lock = asyncio.Lock()

    async def worker_slot() -> None:
        nonlocal claimed_count, claim_reservations
        while True:
            # A claim happens only while this coroutine owns a real processing
            # slot. No lease or attempt can age behind a semaphore wait.
            async with counter_lock:
                if claim_reservations >= safe_limit:
                    return
                claim_reservations += 1
            claim_now = _utcnow()
            job = await claim_due_shadow_job(db, now=claim_now)
            if not job:
                return
            claimed_count += 1
            outcomes.append(await process_shadow_job(db, job))

    await asyncio.gather(*(
        worker_slot()
        for _ in range(min(safe_limit, MAX_CONCURRENCY))
    ))
    exhausted = 0
    while exhausted < safe_limit:
        sweep_now = _utcnow()
        if not await _fail_one_exhausted_stale_job(db, now=sweep_now):
            break
        exhausted += 1
    return {
        "completed": sum(result["status"] == "completed" for result in outcomes),
        "retrying": sum(result["status"] == "retrying" for result in outcomes),
        "failed": sum(result["status"] == "failed" for result in outcomes) + exhausted,
        "lost_lease": sum(result["status"] == "lost_lease" for result in outcomes),
        "superseded": sum(result["status"] == "superseded" for result in outcomes),
        "claimed": claimed_count,
    }


async def run_event_outbox_repair_once(
    db: Any,
    *,
    limit: int = MAX_EVENT_OUTBOX_REPAIRS_PER_CYCLE,
    now: Optional[datetime] = None,
    clock: Optional[Callable[[], datetime]] = None,
    heartbeat_interval: float = LEASE_HEARTBEAT_SECONDS,
) -> dict[str, int]:
    """Repair bounded verified-event intents under one isolated lease."""
    if clock is None:
        clock = (lambda: now) if now is not None else _utcnow
    safe_limit = max(
        1,
        min(int(limit), MAX_EVENT_OUTBOX_REPAIRS_PER_CYCLE),
    )
    state_key = "__salla_orders_v3_event_outbox_repair__"
    started_at = now or clock()
    lease = await _acquire_recovery_lease(
        db,
        state_key,
        user_id="__system__",
        store_id="__event_outbox__",
        now=started_at,
    )
    empty = {
        "scanned": 0,
        "repaired": 0,
        "failed": 0,
        "quarantined": 0,
        "lease_busy": 1,
        "lease_lost": 0,
    }
    if not lease:
        return empty

    token = str(lease["token"])
    epoch = int(lease["epoch"])
    stop = asyncio.Event()
    lost = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _recovery_heartbeat_loop(
            db,
            state_key=state_key,
            token=token,
            epoch=epoch,
            stop=stop,
            lost=lost,
            interval=heartbeat_interval,
            clock=clock,
        )
    )

    async def before_row() -> bool:
        if lost.is_set():
            return False
        owned = await _heartbeat_recovery_lease(
            db,
            state_key,
            token,
            epoch=epoch,
            now=clock(),
        )
        if not owned:
            lost.set()
        return owned

    try:
        metrics = await repair_event_job_outbox_once(
            db,
            limit=safe_limit,
            now=started_at,
            before_row=before_row,
            clock=clock,
        )
        return {
            "scanned": int(metrics.get("scanned") or 0),
            "repaired": int(metrics.get("repaired") or 0),
            "failed": int(metrics.get("failed") or 0),
            "quarantined": int(metrics.get("quarantined") or 0),
            "lease_busy": 0,
            "lease_lost": max(
                int(metrics.get("lease_lost") or 0),
                int(lost.is_set()),
            ),
        }
    finally:
        stop.set()
        await heartbeat_task
        await _release_recovery_lease(
            db,
            state_key,
            token,
            epoch=epoch,
            now=clock(),
        )


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _initial_cursor(
    state: dict[str, Any],
    *,
    today: date,
) -> tuple[date, date, int]:
    start = _parse_date(state.get("next_from_date"))
    if start is None:
        start = today - timedelta(days=DISCOVERY_INITIAL_LOOKBACK_DAYS)
    if start > today:
        start = today
    end = min(start + timedelta(days=DISCOVERY_WINDOW_DAYS - 1), today)
    stored_end = _parse_date(state.get("next_to_date"))
    if stored_end is not None and start <= stored_end <= end:
        end = stored_end
    page = max(1, int(state.get("next_page") or 1))
    return start, end, page


def _pagination_session_live(started_at: Any, now: datetime) -> bool:
    if not isinstance(started_at, datetime):
        return False
    # This timestamp comes from Mongo, whose default decoder returns naive UTC.
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    age = (now - started_at).total_seconds()
    return 0 <= age < PAGINATION_SESSION_SECONDS


async def _write_sync_state(
    db: Any,
    *,
    state_key: str,
    expected_revision: int | None,
    user_id: str,
    store_id: str,
    patch: dict[str, Any],
    now: datetime,
    recovery_lease_token: str,
    recovery_lease_epoch: int,
) -> bool:
    """CAS one checkpoint under the exact co-located recovery lease fence."""
    state = shadow_collection(db, STATE_COLLECTION)
    revision_filter: Any = expected_revision
    update: dict[str, Any] = {
        "$set": {
            "user_id": str(user_id),
            "store_id": str(store_id),
            **patch,
            "updated_at": now,
            "shadow_only": True,
        },
        "$inc": {"state_revision": 1},
    }
    if expected_revision is None:
        revision_filter = {"$exists": False}
    result = await state.update_one(
        {
            "_id": state_key,
            "state_revision": revision_filter,
            "recovery_lease_token": str(recovery_lease_token),
            "recovery_lease_epoch": int(recovery_lease_epoch),
            "recovery_lease_expires_at": {"$gt": now},
        },
        update,
    )
    return bool(
        getattr(result, "matched_count", 0) == 1
        or getattr(result, "upserted_id", None) is not None
    )


def _discovery_queue_intents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents: list[dict[str, Any]] = []
    for row in rows:
        internal_id = str(row.get("id") or row.get("order_id") or "").strip()
        order_number = str(
            row.get("reference_id") or row.get("order_number") or ""
        ).strip()
        if not internal_id or not order_number:
            raise RuntimeError("Salla Orders recovery row has no stable identity")
        intents.append({"light_order": deepcopy(row)})
    return intents


async def _materialize_discovery_intents(
    db: Any,
    *,
    user_id: str,
    store_id: str,
    intents: list[dict[str, Any]],
    now: datetime,
) -> int:
    materialized = 0
    for intent in intents:
        light_order = intent.get("light_order") if isinstance(intent, dict) else None
        if not isinstance(light_order, dict):
            raise RuntimeError("Salla Orders recovery outbox intent is invalid")
        await enqueue_shadow_job(
            db,
            user_id=user_id,
            store_id=store_id,
            light_order=light_order,
            now=now,
        )
        materialized += 1
    return materialized


async def run_recovery_once(
    db: Any,
    *,
    user_id: str,
    store_id: str,
    max_pages: int = MAX_PAGES_PER_RUN,
    gateway: Optional[SallaOrdersGateway] = None,
    now: Optional[datetime] = None,
    clock: Optional[Callable[[], datetime]] = None,
    heartbeat_interval: float = LEASE_HEARTBEAT_SECONDS,
) -> dict[str, int]:
    """Discover bounded date windows and enqueue identities; never fetch Items."""
    if clock is None:
        clock = (lambda: now) if now is not None else _utcnow
    page_budget = max(1, min(int(max_pages), MAX_PAGES_PER_RUN))
    state_key = f"{user_id}:{store_id}"
    now = now or clock()
    lease = await _acquire_recovery_lease(
        db,
        state_key,
        user_id=user_id,
        store_id=store_id,
        now=now,
    )
    if not lease:
        return {
            "discovered": 0,
            "queued": 0,
            "pages": 0,
            "continuation": 0,
            "truncated": 0,
            "lease_busy": 1,
            "lease_lost": 0,
            "synced": 0,
            "failed": 0,
            "pagination_restarts": 0,
        }

    token = str(lease["token"])
    lease_epoch = int(lease["epoch"])
    state_collection = shadow_collection(db, STATE_COLLECTION)
    gateway = gateway or SallaOrdersGateway(db)
    discovered = 0
    queued = 0
    pages = 0
    continuation = 0
    truncated = 0
    lease_lost = 0
    pagination_restarts = 0
    heartbeat_stop = asyncio.Event()
    heartbeat_lost = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _recovery_heartbeat_loop(
            db,
            state_key=state_key,
            token=token,
            epoch=lease_epoch,
            stop=heartbeat_stop,
            lost=heartbeat_lost,
            interval=heartbeat_interval,
            clock=clock,
        )
    )
    try:
        state = await state_collection.find_one({"_id": state_key}) or {}
        expected_revision = state.get("state_revision")

        # Cursor checkpoints and queue intent live in the same fenced document.
        # A crash after the checkpoint can only cause idempotent re-materialize,
        # never an order gap.
        pending = state.get("pending_discovery_jobs") or []
        if pending:
            if heartbeat_lost.is_set() or not await _heartbeat_recovery_lease(
                db,
                state_key,
                token,
                epoch=lease_epoch,
                now=clock(),
            ):
                lease_lost = 1
                continuation = 1
            else:
                queued += await _materialize_discovery_intents(
                    db,
                    user_id=user_id,
                    store_id=store_id,
                    intents=pending,
                    now=now,
                )
                checkpoint_now = clock()
                if not await _write_sync_state(
                    db,
                    state_key=state_key,
                    expected_revision=expected_revision,
                    user_id=user_id,
                    store_id=store_id,
                    patch={
                        "pending_discovery_jobs": [],
                        "pending_materialized_at": checkpoint_now,
                    },
                    now=checkpoint_now,
                    recovery_lease_token=token,
                    recovery_lease_epoch=lease_epoch,
                ):
                    lease_lost = 1
                    continuation = 1
                else:
                    expected_revision = int(expected_revision or 0) + 1
                    state = {
                        **state,
                        "pending_discovery_jobs": [],
                        "state_revision": expected_revision,
                    }

        window_start, window_end, page = _initial_cursor(
            state,
            today=now.date(),
        )
        pagination_started_at = state.get("pagination_started_at")

        while not lease_lost and pages < page_budget:
            if heartbeat_lost.is_set() or not await _heartbeat_recovery_lease(
                db,
                state_key,
                token,
                epoch=lease_epoch,
                now=clock(),
            ):
                lease_lost = 1
                continuation = 1
                break

            requested_at = clock()
            if page > 1 and not _pagination_session_live(
                pagination_started_at, requested_at
            ):
                # Replay this same date window from page 1. Already committed
                # queue intents were repaired above and enqueue is idempotent.
                page = 1
                pagination_restarts += 1
            if page == 1:
                pagination_started_at = requested_at
            rows, pagination = await gateway.list_light_orders_page(
                user_id,
                page=page,
                from_date=window_start.isoformat(),
                to_date=window_end.isoformat(),
            )
            if heartbeat_lost.is_set() or not await _heartbeat_recovery_lease(
                db,
                state_key,
                token,
                epoch=lease_epoch,
                now=clock(),
            ):
                lease_lost = 1
                continuation = 1
                break
            response_at = clock()
            if not _pagination_session_live(pagination_started_at, response_at):
                # An empty late response may mean expired provider cache, not
                # exhaustion. Never advance the date window from this response.
                pages += 1
                pagination_restarts += 1
                continuation = 1
                truncated = 1
                if not await _write_sync_state(
                    db,
                    state_key=state_key,
                    expected_revision=expected_revision,
                    user_id=user_id,
                    store_id=store_id,
                    patch={
                        "next_from_date": window_start.isoformat(),
                        "next_to_date": window_end.isoformat(),
                        "next_page": 1,
                        "pagination_started_at": None,
                        "continuation": True,
                        "continuation_reason": "pagination_restart",
                        "truncated": True,
                        "truncation_reason": "pagination_session_expired",
                        "last_run_at": now,
                    },
                    now=response_at,
                    recovery_lease_token=token,
                    recovery_lease_epoch=lease_epoch,
                ):
                    lease_lost = 1
                break
            if pagination.current_page != page:
                raise RuntimeError("Salla Orders recovery page identity mismatch")
            if discovered + len(rows) > MAX_DISCOVERED_ORDERS_PER_RUN:
                raise RuntimeError("Salla Orders recovery order budget exceeded")
            intents = _discovery_queue_intents(rows)

            if pagination.exhausted:
                completed_to = window_end
                if window_end < now.date():
                    window_start = window_end + timedelta(days=1)
                    window_end = min(
                        window_start + timedelta(days=DISCOVERY_WINDOW_DAYS - 1),
                        now.date(),
                    )
                    page = 1
                    continuation = 1
                    continuation_reason = "next_window"
                else:
                    # Re-read the current date next cycle. Signal ordering makes
                    # this idempotent while allowing newly arriving orders.
                    window_start = now.date()
                    window_end = now.date()
                    page = 1
                    continuation = 0
                    continuation_reason = "caught_up"
            else:
                if pagination.next_page is None:
                    raise RuntimeError("Salla Orders recovery next page is missing")
                page = pagination.next_page
                completed_to = _parse_date(state.get("last_completed_to_date"))
                continuation = 1
                continuation_reason = "pagination"

            truncation_reason = None
            if pages + 1 >= page_budget and continuation:
                truncated = 1
                truncation_reason = "page_budget"
            state_patch = {
                "next_from_date": window_start.isoformat(),
                "next_to_date": window_end.isoformat(),
                "next_page": page,
                "pagination_started_at": pagination_started_at if page > 1 else None,
                "last_completed_to_date": (
                    completed_to.isoformat() if completed_to else None
                ),
                "continuation": bool(continuation),
                "continuation_reason": continuation_reason,
                "truncated": bool(truncated),
                "truncation_reason": truncation_reason,
                "last_discovered": discovered + len(rows),
                "last_queued": queued + len(rows),
                "last_run_at": now,
                "pending_discovery_jobs": intents,
                "pending_recorded_at": clock(),
            }
            checkpoint_now = clock()
            if not await _write_sync_state(
                db,
                state_key=state_key,
                expected_revision=expected_revision,
                user_id=user_id,
                store_id=store_id,
                patch=state_patch,
                now=checkpoint_now,
                recovery_lease_token=token,
                recovery_lease_epoch=lease_epoch,
            ):
                lease_lost = 1
                continuation = 1
                break
            expected_revision = int(expected_revision or 0) + 1
            state = {**state, **state_patch, "state_revision": expected_revision}

            discovered += len(rows)
            pages += 1
            if heartbeat_lost.is_set() or not await _heartbeat_recovery_lease(
                db,
                state_key,
                token,
                epoch=lease_epoch,
                now=clock(),
            ):
                lease_lost = 1
                continuation = 1
                break
            queued += await _materialize_discovery_intents(
                db,
                user_id=user_id,
                store_id=store_id,
                intents=intents,
                now=now,
            )
            clear_now = clock()
            if not await _write_sync_state(
                db,
                state_key=state_key,
                expected_revision=expected_revision,
                user_id=user_id,
                store_id=store_id,
                patch={
                    "pending_discovery_jobs": [],
                    "pending_materialized_at": clear_now,
                },
                now=clear_now,
                recovery_lease_token=token,
                recovery_lease_epoch=lease_epoch,
            ):
                lease_lost = 1
                continuation = 1
                break
            expected_revision += 1
            state = {
                **state,
                "pending_discovery_jobs": [],
                "state_revision": expected_revision,
            }

            if continuation_reason == "caught_up":
                break

        return {
            "discovered": discovered,
            "queued": queued,
            "pages": pages,
            "continuation": int(bool(continuation)),
            "truncated": truncated,
            "lease_busy": 0,
            "lease_lost": lease_lost,
            "synced": 0,
            "failed": 0,
            "pagination_restarts": pagination_restarts,
        }
    finally:
        heartbeat_stop.set()
        await heartbeat_task
        await _release_recovery_lease(
            db,
            state_key,
            token,
            epoch=lease_epoch,
            now=clock(),
        )


async def run_discovery_cycle(db: Any) -> list[dict[str, int]]:
    """Run one stable, durably continued connected-store scan."""
    scan_key = "__salla_orders_v3_integration_scan__"
    state_collection = shadow_collection(db, STATE_COLLECTION)
    scan_state = await state_collection.find_one({"_id": scan_key}) or {}
    expected_revision = scan_state.get("state_revision")
    after = scan_state.get("integration_cursor_after")
    query: dict[str, Any] = {"status": "connected"}
    if isinstance(after, dict):
        after_user = after.get("user_id")
        after_store = after.get("store_id")
        after_id = after.get("_id")
        if after_user is not None and after_store is not None and after_id is not None:
            query = {
                "$and": [
                    {"status": "connected"},
                    {"$or": [
                        {"user_id": {"$gt": after_user}},
                        {
                            "user_id": after_user,
                            "store_id": {"$gt": after_store},
                        },
                        {
                            "user_id": after_user,
                            "store_id": after_store,
                            "_id": {"$gt": after_id},
                        },
                    ]},
                ]
            }

    recovery_results: list[dict[str, int]] = []
    cursor = db.salla_integrations.find(
        query,
        {"_id": 1, "user_id": 1, "store_id": 1},
    ).sort([
        ("user_id", ASCENDING),
        ("store_id", ASCENDING),
        ("_id", ASCENDING),
    ]).limit(MAX_INTEGRATIONS_PER_CYCLE)
    last_cursor: Optional[dict[str, Any]] = None
    async for integration in cursor:
        last_cursor = {
            "user_id": integration.get("user_id"),
            "store_id": integration.get("store_id"),
            "_id": integration.get("_id"),
        }
        user_id = str(integration.get("user_id") or "").strip()
        store_id = str(integration.get("store_id") or "").strip()
        if not user_id or not store_id:
            continue
        try:
            recovery_results.append(
                await run_recovery_once(db, user_id=user_id, store_id=store_id)
            )
        except Exception:
            log.exception(
                "salla.orders_v3.shadow_recovery_failed user_id=%s store_id=%s",
                user_id,
                store_id,
            )

    now = _utcnow()
    revision_filter: Any = expected_revision
    upsert = False
    update: dict[str, Any] = {
        "$set": {
            "user_id": "__system__",
            "store_id": "__integration_scan__",
            "integration_cursor_after": last_cursor,
            "integration_scan_exhausted": last_cursor is None,
            "updated_at": now,
            "shadow_only": True,
        },
        "$inc": {"state_revision": 1},
    }
    if expected_revision is None:
        revision_filter = {"$exists": False}
        update["$setOnInsert"] = {"_id": scan_key, "created_at": now}
        upsert = True
    try:
        await state_collection.update_one(
            {"_id": scan_key, "state_revision": revision_filter},
            update,
            upsert=upsert,
        )
    except DuplicateKeyError:
        # A concurrent scan won the cursor CAS. Queue writes are idempotent and
        # the winner's durable cursor remains authoritative.
        pass
    return recovery_results


async def run_worker_cycle(db: Any) -> dict[str, Any]:
    """One testable cycle with three independently bounded tasks."""
    recovery_results, job_results, outbox_results = await asyncio.gather(
        run_discovery_cycle(db),
        run_due_jobs_once(db),
        run_event_outbox_repair_once(db),
    )
    return {
        "recovery": recovery_results,
        "jobs": job_results,
        "event_outbox": outbox_results,
    }


async def _discovery_loop(db: Any) -> None:
    while True:
        try:
            await run_discovery_cycle(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("salla.orders_v3.discovery_cycle_failed")
        await asyncio.sleep(RECOVERY_INTERVAL_SECONDS)


async def _enrichment_loop(db: Any) -> None:
    while True:
        try:
            await run_due_jobs_once(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("salla.orders_v3.enrichment_cycle_failed")
        await asyncio.sleep(RECOVERY_INTERVAL_SECONDS)


async def _event_outbox_repair_loop(db: Any) -> None:
    while True:
        try:
            metrics = await run_event_outbox_repair_once(db)
            log.info("salla.orders_v3.event_outbox_repair metrics=%s", metrics)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("salla.orders_v3.event_outbox_repair_failed")
        await asyncio.sleep(RECOVERY_INTERVAL_SECONDS)


async def _worker_loop(db: Any) -> None:
    # Separate loops are deliberate: a slow/failing Order Items call can never
    # delay the next metadata-driven List Orders discovery tick.
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(_discovery_loop(db), name="salla-orders-v3-discovery")
        tasks.create_task(_enrichment_loop(db), name="salla-orders-v3-enrichment")
        tasks.create_task(
            _event_outbox_repair_loop(db),
            name="salla-orders-v3-event-outbox",
        )


def start_salla_orders_v3_shadow_worker(db: Any) -> Optional[asyncio.Task]:
    global _task
    if not shadow_enabled():
        return None
    if _task is not None and not _task.done():
        return _task
    _task = asyncio.create_task(_worker_loop(db), name="salla-orders-v3-shadow")
    return _task


async def start_salla_orders_v3_shadow_runtime(db: Any) -> Optional[asyncio.Task]:
    """Initialize the optional observer for this process, including restarts.

    Indexes are idempotent. This must not live inside the once-per-release
    startup lease: a restarted process still needs its own task. Mongo fences
    retain ownership of each bounded discovery/enrichment operation.
    """
    if not shadow_enabled():
        return None
    if _task is not None and not _task.done():
        return _task
    await ensure_salla_orders_v3_indexes(db)
    return start_salla_orders_v3_shadow_worker(db)


async def stop_salla_orders_v3_shadow_worker(task: Optional[asyncio.Task]) -> None:
    """Drain cancellation before the caller closes its Mongo client."""
    global _task
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        if _task is task:
            _task = None
