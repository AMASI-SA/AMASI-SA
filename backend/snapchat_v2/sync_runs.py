"""Observable stage and coverage tracking for Snapchat V2 sync runs."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .models import SNAPCHAT_PROVIDER, clean_text

SNAPCHAT_SYNC_RUNS_COLLECTION = "mezan_snapchat_sync_runs_v2"
RUN_HEARTBEAT_STALE_AFTER = timedelta(minutes=3)

LEVEL_STATUS_FIELDS = {
    "financial": "financial_sync_status",
    "campaign": "campaign_sync_status",
    "ad_squad": "ad_squad_sync_status",
    "ad": "ad_sync_status",
    "identity": "identity_sync_status",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_error(error: Any) -> dict[str, Any]:
    code = clean_text(getattr(error, "code", None) or type(error).__name__, limit=96)
    return {
        "code": code or "snapchat_sync_failed",
        "retryable": bool(getattr(error, "retryable", False)),
        "needs_reauth": bool(getattr(error, "needs_reauth", False)),
    }


async def ensure_sync_run_indexes(db: Any) -> None:
    collection = db[SNAPCHAT_SYNC_RUNS_COLLECTION]
    await collection.create_index(
        [("sync_run_id", 1)],
        unique=True,
        name="snapchat_v2_sync_run_id_unique",
    )
    await collection.create_index(
        [("user_id", 1), ("ad_account_id", 1), ("started_at", -1)],
        name="snapchat_v2_sync_run_account_latest",
    )
    await collection.create_index(
        [("status", 1), ("heartbeat_at", 1)],
        name="snapchat_v2_sync_run_status_heartbeat",
    )


def new_sync_run(
    user_id: str,
    ad_account_id: str,
    *,
    owner_id: str | None = None,
    run_type: str = "rolling_refresh",
    request_window: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or _utcnow()).astimezone(timezone.utc)
    return {
        "sync_run_id": str(uuid.uuid4()),
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": str(ad_account_id),
        "owner_id": owner_id,
        "run_type": clean_text(run_type, limit=64) or "rolling_refresh",
        "status": "running",
        "stage": "initialized",
        "stage_status": "running",
        "financial_sync_status": "pending",
        "campaign_sync_status": "pending",
        "ad_squad_sync_status": "pending",
        "ad_sync_status": "pending",
        "identity_sync_status": "pending",
        "request_window": dict(request_window or {}),
        "coverage": {},
        "stage_history": [
            {
                "stage": "initialized",
                "status": "running",
                "at": current,
            }
        ],
        "started_at": current,
        "heartbeat_at": current,
        "created_at": current,
        "updated_at": current,
    }


async def create_sync_run(db: Any, run: dict[str, Any]) -> None:
    await db[SNAPCHAT_SYNC_RUNS_COLLECTION].insert_one(dict(run))


async def update_sync_stage(
    db: Any,
    sync_run_id: str,
    stage: str,
    status: str | None = None,
    *,
    details: dict[str, Any] | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    current = now().astimezone(timezone.utc)
    stage_name = clean_text(stage, limit=96)
    stage_status = clean_text(status or "running", limit=32)
    safe_details = {
        clean_text(key, limit=64): value
        for key, value in dict(details or {}).items()
        if clean_text(key, limit=64)
        and clean_text(key, limit=64).lower()
        not in {"access_token", "refresh_token", "authorization", "client_secret"}
    }
    await db[SNAPCHAT_SYNC_RUNS_COLLECTION].update_one(
        {"sync_run_id": str(sync_run_id), "status": "running"},
        {
            "$set": {
                "stage": stage_name,
                "stage_status": stage_status,
                "heartbeat_at": current,
                "updated_at": current,
            },
            "$push": {
                "stage_history": {
                    "stage": stage_name,
                    "status": stage_status,
                    "details": safe_details,
                    "at": current,
                }
            },
        },
    )


async def heartbeat_sync_run(
    db: Any,
    sync_run_id: str,
    *,
    owner_id: str | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> bool:
    query: dict[str, Any] = {"sync_run_id": str(sync_run_id), "status": "running"}
    if owner_id is not None:
        query["owner_id"] = owner_id
    current = now().astimezone(timezone.utc)
    result = await db[SNAPCHAT_SYNC_RUNS_COLLECTION].update_one(
        query,
        {"$set": {"heartbeat_at": current, "updated_at": current}},
    )
    matched_count = getattr(result, "matched_count", None)
    if matched_count is not None:
        return int(matched_count or 0) == 1
    return int(getattr(result, "modified_count", 0) or 0) == 1


async def set_level_status(
    db: Any,
    sync_run_id: str,
    level: str,
    status: str,
    *,
    coverage: dict[str, Any] | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    field = LEVEL_STATUS_FIELDS.get(level)
    if field is None:
        raise ValueError(f"Unknown Snapchat sync level: {level}")
    current = now().astimezone(timezone.utc)
    updates: dict[str, Any] = {
        field: clean_text(status, limit=32),
        "heartbeat_at": current,
        "updated_at": current,
    }
    if coverage is not None:
        updates[f"coverage.{level}"] = dict(coverage)
    await db[SNAPCHAT_SYNC_RUNS_COLLECTION].update_one(
        {"sync_run_id": str(sync_run_id), "status": "running"},
        {"$set": updates},
    )


async def complete_sync_run(
    db: Any,
    sync_run_id: str,
    *,
    summary: dict[str, Any] | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    current = now().astimezone(timezone.utc)
    projection = {"_id": 0}
    projection.update({field: 1 for field in LEVEL_STATUS_FIELDS.values()})
    row = await db[SNAPCHAT_SYNC_RUNS_COLLECTION].find_one(
        {"sync_run_id": str(sync_run_id)},
        projection,
    ) or {}
    level_values = [str(row.get(field) or "pending") for field in LEVEL_STATUS_FIELDS.values()]
    financial_complete = row.get("financial_sync_status") == "complete"
    overall = (
        "complete"
        if all(value == "complete" for value in level_values)
        else "partial"
        if financial_complete
        else "failed"
    )
    await db[SNAPCHAT_SYNC_RUNS_COLLECTION].update_one(
        {"sync_run_id": str(sync_run_id), "status": "running"},
        {
            "$set": {
                "status": overall,
                "stage": "completed",
                "stage_status": overall,
                "summary": dict(summary or {}),
                "finished_at": current,
                "heartbeat_at": current,
                "updated_at": current,
            },
            "$push": {
                "stage_history": {
                    "stage": "completed",
                    "status": overall,
                    "at": current,
                }
            },
        },
    )


async def fail_sync_run(
    db: Any,
    sync_run_id: str,
    error: Any,
    *,
    stage: str | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    current = now().astimezone(timezone.utc)
    safe_error = _safe_error(error)
    failed_stage = clean_text(stage, limit=96) or "failed"
    await db[SNAPCHAT_SYNC_RUNS_COLLECTION].update_one(
        {"sync_run_id": str(sync_run_id), "status": "running"},
        {
            "$set": {
                "status": "failed",
                "stage": failed_stage,
                "stage_status": "failed",
                "last_error": safe_error,
                "finished_at": current,
                "heartbeat_at": current,
                "updated_at": current,
            },
            "$push": {
                "stage_history": {
                    "stage": failed_stage,
                    "status": "failed",
                    "error": safe_error,
                    "at": current,
                }
            },
        },
    )


async def recover_abandoned_sync_runs(
    db: Any,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> int:
    current = now().astimezone(timezone.utc)
    cutoff = current - RUN_HEARTBEAT_STALE_AFTER
    result = await db[SNAPCHAT_SYNC_RUNS_COLLECTION].update_many(
        {
            "provider": SNAPCHAT_PROVIDER,
            "status": "running",
            "heartbeat_at": {"$lte": cutoff},
        },
        {
            "$set": {
                "status": "abandoned",
                "stage_status": "abandoned",
                "finished_at": current,
                "updated_at": current,
                "last_error": {
                    "code": "worker_heartbeat_stale",
                    "retryable": True,
                    "needs_reauth": False,
                },
            },
            "$push": {
                "stage_history": {
                    "stage": "worker_recovery",
                    "status": "abandoned",
                    "at": current,
                }
            },
        },
    )
    return int(getattr(result, "modified_count", 0) or 0)
