"""Independent, lease-protected V3 shadow recovery and enrichment worker."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from .gateway import MAX_PAGES_PER_RUN, SallaOrdersGateway
from .ingestion import enqueue_shadow_job
from .shadow import COLLECTION as SHADOW_COLLECTION
from .shadow import SallaOrdersShadowEngine


log = logging.getLogger("salla.orders_v3.worker")

JOBS_COLLECTION = "salla_orders_v3_jobs"
EVENTS_COLLECTION = "salla_orders_v3_events"
STATE_COLLECTION = "salla_orders_v3_sync_state"
LEASES_COLLECTION = "salla_orders_v3_leases"

MAX_JOB_ATTEMPTS = 5
MAX_CONCURRENCY = 4
OVERLAP_MINUTES = 10
LEASE_SECONDS = 180
RECOVERY_INTERVAL_SECONDS = 300

_task: Optional[asyncio.Task] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def shadow_enabled() -> bool:
    return os.environ.get("SALLA_ORDERS_V3_SHADOW_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


async def ensure_salla_orders_v3_indexes(db: Any) -> None:
    await getattr(db, SHADOW_COLLECTION).create_index(
        [("user_id", ASCENDING), ("store_id", ASCENDING), ("order_number", ASCENDING)],
        unique=True,
        name="salla_orders_v3_shadow_identity",
    )
    await getattr(db, SHADOW_COLLECTION).create_index(
        "updated_at", expireAfterSeconds=30 * 24 * 60 * 60,
        name="salla_orders_v3_shadow_ttl",
    )
    await getattr(db, EVENTS_COLLECTION).create_index(
        "received_at", expireAfterSeconds=30 * 24 * 60 * 60,
        name="salla_orders_v3_events_ttl",
    )
    await getattr(db, JOBS_COLLECTION).create_index(
        [("status", ASCENDING), ("next_attempt_at", ASCENDING)],
        name="salla_orders_v3_jobs_due",
    )
    await getattr(db, STATE_COLLECTION).create_index(
        [("user_id", ASCENDING), ("store_id", ASCENDING)],
        unique=True,
        name="salla_orders_v3_state_identity",
    )


async def _acquire_lease(db: Any, key: str, *, now: datetime) -> Optional[str]:
    token = str(uuid.uuid4())
    try:
        row = await getattr(db, LEASES_COLLECTION).find_one_and_update(
            {
                "_id": key,
                "$or": [
                    {"expires_at": {"$lte": now}},
                    {"expires_at": {"$exists": False}},
                ],
            },
            {"$set": {
                "lease_token": token,
                "acquired_at": now,
                "expires_at": now + timedelta(seconds=LEASE_SECONDS),
            }},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return None
    if row and row.get("lease_token") == token:
        return token
    return None


async def _release_lease(db: Any, key: str, token: str) -> None:
    await getattr(db, LEASES_COLLECTION).update_one(
        {"_id": key, "lease_token": token},
        {"$set": {"expires_at": _utcnow()}},
    )


async def process_shadow_job(
    db: Any,
    job: dict[str, Any],
    *,
    engine: Optional[SallaOrdersShadowEngine] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now = now or _utcnow()
    engine = engine or SallaOrdersShadowEngine(db)
    jobs = getattr(db, JOBS_COLLECTION)
    attempts = int(job.get("attempts") or 0) + 1
    try:
        result = await engine.sync_order(
            user_id=str(job.get("user_id") or ""),
            store_id=str(job.get("store_id") or ""),
            light_order=job.get("light_order") or {},
            fetch_details=True,
            event_created_at=job.get("event_created_at"),
        )
    except Exception as exc:
        result = {
            "ok": False,
            "items_sync_status": "failed",
            "items_payload_valid": False,
            "error_type": type(exc).__name__,
        }
    completed = bool(
        result.get("ok")
        and result.get("items_sync_status") == "succeeded"
        and result.get("items_payload_valid") is True
    )
    if completed:
        status = "completed"
        patch = {
            "status": status,
            "attempts": attempts,
            "completed_at": now,
            "last_error": None,
            "updated_at": now,
        }
    else:
        status = "failed" if attempts >= MAX_JOB_ATTEMPTS else "retrying"
        delay = min(300, 2 ** min(attempts, 8))
        patch = {
            "status": status,
            "attempts": attempts,
            "last_error": result.get("error_type") or "items_enrichment_failed",
            "next_attempt_at": now + timedelta(seconds=delay),
            "updated_at": now,
        }
    await jobs.update_one({"_id": job["_id"]}, {"$set": patch})
    return {"status": status, "attempts": attempts, "result": result}


async def run_due_jobs_once(db: Any, *, limit: int = 20) -> dict[str, int]:
    now = _utcnow()
    jobs = getattr(db, JOBS_COLLECTION)
    completed = 0
    retrying = 0
    failed = 0
    for _ in range(max(1, min(int(limit), 100))):
        job = await jobs.find_one_and_update(
            {"$or": [
                {
                    "status": {"$in": ["pending", "retrying"]},
                    "next_attempt_at": {"$lte": now},
                },
                {
                    "status": "processing",
                    "processing_started_at": {
                        "$lte": now - timedelta(seconds=LEASE_SECONDS)
                    },
                },
            ]},
            {"$set": {
                "status": "processing",
                "processing_started_at": now,
                "updated_at": now,
            }},
            sort=[("next_attempt_at", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        if not job:
            break
        outcome = await process_shadow_job(db, job, now=now)
        completed += int(outcome["status"] == "completed")
        retrying += int(outcome["status"] == "retrying")
        failed += int(outcome["status"] == "failed")
    return {"completed": completed, "retrying": retrying, "failed": failed}


async def run_recovery_once(
    db: Any,
    *,
    user_id: str,
    store_id: str,
    max_pages: int = MAX_PAGES_PER_RUN,
) -> dict[str, int]:
    now = _utcnow()
    lease_key = f"recovery:{user_id}:{store_id}"
    token = await _acquire_lease(db, lease_key, now=now)
    if not token:
        return {"discovered": 0, "synced": 0, "failed": 0, "lease_busy": 1}

    state_collection = getattr(db, STATE_COLLECTION)
    gateway = SallaOrdersGateway(db)
    engine = SallaOrdersShadowEngine(db, gateway=gateway)
    state_key = f"{user_id}:{store_id}"
    state = await state_collection.find_one({"_id": state_key}) or {}
    cursor = state.get("last_success_at")
    if isinstance(cursor, datetime):
        cursor = cursor - timedelta(minutes=OVERLAP_MINUTES)
        updated_at_gt = cursor.strftime("%Y-%m-%d %H:%M:%S")
    else:
        updated_at_gt = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

    discovered = 0
    synced = 0
    failed = 0
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def sync_row(row: dict[str, Any]) -> None:
        nonlocal synced, failed
        async with semaphore:
            try:
                result = await engine.sync_order(
                    user_id=user_id,
                    store_id=store_id,
                    light_order=row,
                    fetch_details=False,
                )
                synced += int(bool(result.get("ok")))
                failed += int(not bool(result.get("ok")))
                if (
                    not result.get("ok")
                    or result.get("items_sync_status") != "succeeded"
                    or result.get("items_payload_valid") is not True
                ):
                    await enqueue_shadow_job(
                        db,
                        user_id=user_id,
                        store_id=store_id,
                        light_order=row,
                        now=now,
                    )
            except Exception:
                failed += 1
                await enqueue_shadow_job(
                    db,
                    user_id=user_id,
                    store_id=store_id,
                    light_order=row,
                    now=now,
                )

    try:
        async for rows in gateway.iter_light_orders(
            user_id,
            updated_at_gt=updated_at_gt,
            max_pages=max_pages,
        ):
            discovered += len(rows)
            await asyncio.gather(*(sync_row(row) for row in rows))
        await state_collection.update_one(
            {"_id": state_key},
            {"$set": {
                "_id": state_key,
                "user_id": user_id,
                "store_id": store_id,
                "last_success_at": now,
                "last_discovered": discovered,
                "last_synced": synced,
                "last_failed": failed,
                "shadow_only": True,
            }},
            upsert=True,
        )
        return {"discovered": discovered, "synced": synced, "failed": failed, "lease_busy": 0}
    finally:
        await _release_lease(db, lease_key, token)


async def run_worker_cycle(db: Any) -> None:
    await run_due_jobs_once(db)
    cursor = db.salla_integrations.find(
        {"status": "connected"},
        {"_id": 0, "user_id": 1, "store_id": 1},
    )
    async for integration in cursor:
        user_id = str(integration.get("user_id") or "").strip()
        store_id = str(integration.get("store_id") or "").strip()
        if not user_id or not store_id:
            continue
        try:
            await run_recovery_once(db, user_id=user_id, store_id=store_id)
        except Exception:
            log.exception(
                "salla.orders_v3.shadow_recovery_failed user_id=%s store_id=%s",
                user_id,
                store_id,
            )


async def _worker_loop(db: Any) -> None:
    while True:
        try:
            await run_worker_cycle(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("salla.orders_v3.shadow_cycle_failed")
        await asyncio.sleep(RECOVERY_INTERVAL_SECONDS)


def start_salla_orders_v3_shadow_worker(db: Any) -> Optional[asyncio.Task]:
    global _task
    if not shadow_enabled():
        return None
    if _task is not None and not _task.done():
        return _task
    _task = asyncio.create_task(_worker_loop(db), name="salla-orders-v3-shadow")
    return _task
