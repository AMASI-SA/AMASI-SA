"""Server-side five-minute advertising refresh for Mezan V2.

The scheduler runs inside the backend process, so it keeps refreshing provider
facts even when no browser tab is open.  It uses a Mongo lease to ensure that
only one backend worker performs a cycle at a time.

Provider policy:
* Meta: native reporting refresh for a rolling two-day window.
* Snapchat: lightweight performance-only refresh for selected accounts.  The
  expensive entity discovery remains a manual/on-demand operation.
* TikTok: its existing webhook/data-feed remains live until TikTok approves the
  native OAuth application; this scheduler never pretends a provider pull was
  performed when that data plane is unavailable.

The module writes only V2 analytical facts, integration health, scheduler state,
and audited sync-run records.  It never posts accounting entries or mutates ad
campaigns.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .meta_native_reporting import (
    META_REPORTING_SOURCE_MODE,
    MetaReportingError,
    MetaReportingSyncInput,
    meta_reporting_enabled,
    run_meta_reporting_sync,
)
from .meta_oauth_security import (
    META_PROVIDER_ID,
    meta_oauth_configured,
)
from .snapchat_account_selection import _load_selected_accounts
from .snapchat_native_data_common import (
    BUSINESS_TIMEZONE,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatNativeSyncInput,
    SnapchatSyncContext,
    _collection,
    _timezone,
    ensure_snapchat_native_sync_indexes,
    enumerate_native_sync_dates,
    snapchat_native_sync_enabled,
)
from .snapchat_native_performance_sync import sync_snapchat_performance
from .snapchat_oauth_security import snapchat_oauth_configured

logger = logging.getLogger(__name__)

ADS_AUTO_SYNC_ENABLED_ENV = "MEZAN_ADS_AUTO_SYNC_ENABLED"
ADS_AUTO_SYNC_INTERVAL_ENV = "MEZAN_ADS_AUTO_SYNC_INTERVAL_SECONDS"
ADS_AUTO_SYNC_DAYS_ENV = "MEZAN_ADS_AUTO_SYNC_DAYS"
ADS_AUTO_SYNC_STARTUP_DELAY_ENV = "MEZAN_ADS_AUTO_SYNC_STARTUP_DELAY_SECONDS"

DEFAULT_INTERVAL_SECONDS = 5 * 60
MIN_INTERVAL_SECONDS = 5 * 60
MAX_INTERVAL_SECONDS = 60 * 60
DEFAULT_ROLLING_DAYS = 2
MAX_ROLLING_DAYS = 7
DEFAULT_STARTUP_DELAY_SECONDS = 45
MIN_STARTUP_DELAY_SECONDS = 5
MAX_STARTUP_DELAY_SECONDS = 10 * 60
LOOP_HEARTBEAT_SECONDS = 15
PROVIDER_JOB_STALE_AFTER = timedelta(minutes=25)
LEASE_TTL = timedelta(minutes=25)

SCHEDULER_COLLECTION = "mezan_ads_auto_sync_scheduler_v2"
SCHEDULER_DOCUMENT_ID = "ads-v2-server-scheduler"
SYNC_RUN_COLLECTION = "mezan_integration_sync_runs_v2"
INTEGRATION_ERROR_COLLECTION = "mezan_integration_errors_v2"
SCHEDULER_TRIGGER = "server_scheduler_5m"
SCHEDULED_META_RUN_TYPE = "scheduled_meta_reporting_refresh"
# Use analytics_refresh so every existing Snapchat manual/async lock also sees
# this lightweight scheduled refresh and cannot overlap it.
SCHEDULED_SNAPCHAT_RUN_TYPE = "analytics_refresh"
SCHEDULED_SNAPCHAT_SOURCE_MODE = "snapchat_scheduled_performance_refresh_v2"

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("complete", "partial", "failed", "skipped")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def ads_auto_sync_enabled() -> bool:
    value = str(os.environ.get(ADS_AUTO_SYNC_ENABLED_ENV, "true")).strip().lower()
    return value not in {"0", "false", "off", "no", "disabled"}


def ads_auto_sync_interval_seconds() -> int:
    return _bounded_int(
        os.environ.get(ADS_AUTO_SYNC_INTERVAL_ENV),
        default=DEFAULT_INTERVAL_SECONDS,
        minimum=MIN_INTERVAL_SECONDS,
        maximum=MAX_INTERVAL_SECONDS,
    )


def ads_auto_sync_rolling_days() -> int:
    return _bounded_int(
        os.environ.get(ADS_AUTO_SYNC_DAYS_ENV),
        default=DEFAULT_ROLLING_DAYS,
        minimum=1,
        maximum=MAX_ROLLING_DAYS,
    )


def ads_auto_sync_startup_delay_seconds() -> int:
    return _bounded_int(
        os.environ.get(ADS_AUTO_SYNC_STARTUP_DELAY_ENV),
        default=DEFAULT_STARTUP_DELAY_SECONDS,
        minimum=MIN_STARTUP_DELAY_SECONDS,
        maximum=MAX_STARTUP_DELAY_SECONDS,
    )


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:10]}"


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


def _target_pairs(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        user_id = str(row.get("user_id") or "").strip()
        provider = str(row.get("provider") or "").strip()
        if user_id and provider in {META_PROVIDER_ID, SNAPCHAT_PROVIDER_ID}:
            pairs.add((user_id, provider))
    return sorted(pairs)


async def _connected_targets(db: Any) -> list[tuple[str, str]]:
    cursor = _collection(db, "mezan_integrations_v2").find(
        {
            "provider": {"$in": [META_PROVIDER_ID, SNAPCHAT_PROVIDER_ID]},
            "connection_status": "connected",
            "connection_provenance": "api_connection",
        },
        {"_id": 0, "user_id": 1, "provider": 1},
    )
    return _target_pairs(await _to_list(cursor, 2000))


async def _active_provider_job(
    db: Any,
    user_id: str,
    provider: str,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    runs = _collection(db, SYNC_RUN_COLLECTION)
    active = await runs.find_one(
        {
            "user_id": user_id,
            "provider": provider,
            "status": {"$in": list(ACTIVE_STATUSES)},
        },
        {"_id": 0, "run_id": 1, "status": 1, "created_at": 1, "started_at": 1},
        sort=[("started_at", -1), ("created_at", -1)],
    )
    if not active:
        return None
    marker = _parse_datetime(active.get("started_at") or active.get("created_at"))
    if marker and marker >= now - PROVIDER_JOB_STALE_AFTER:
        return active
    await runs.update_one(
        {
            "user_id": user_id,
            "provider": provider,
            "run_id": active.get("run_id"),
            "status": {"$in": list(ACTIVE_STATUSES)},
        },
        {
            "$set": {
                "status": "failed",
                "finished_at": _iso(now),
                "error": {
                    "code": "scheduled_sync_stale_job_recovered",
                    "message": "A stale provider refresh was released by the scheduler.",
                    "retryable": True,
                },
            }
        },
    )
    return None


async def _insert_run(
    db: Any,
    *,
    user_id: str,
    provider: str,
    run_type: str,
    source_mode: str,
    days: int,
    now: datetime,
) -> str:
    run_id = str(uuid.uuid4())
    await _collection(db, SYNC_RUN_COLLECTION).insert_one(
        {
            "run_id": run_id,
            "user_id": user_id,
            "provider": provider,
            "run_type": run_type,
            "status": "running",
            "trigger": SCHEDULER_TRIGGER,
            "created_at": _iso(now),
            "started_at": _iso(now),
            "finished_at": None,
            "lock_expires_at": _iso(now + PROVIDER_JOB_STALE_AFTER),
            "source_mode": source_mode,
            "summary": {
                "requested_days": days,
                "source_only": True,
                "provider_write_reached": False,
                "campaign_write_reached": False,
                "accounting_write_reached": False,
                "qoyod_write_reached": False,
            },
            "error": None,
        }
    )
    return run_id


def _summary_fields(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "date_from": result.get("date_from"),
        "date_to": result.get("date_to"),
        "accounts_attempted": int(
            result.get("accounts_attempted")
            or result.get("accounts_synced")
            or 0
        ),
        "accounts_complete": int(result.get("accounts_complete") or 0),
        "rows_saved": int(result.get("rows_saved") or 0),
        "errors_count": int(result.get("errors_count") or 0),
        "provider_calls": int(result.get("provider_calls") or 0),
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def _finish_run(
    db: Any,
    *,
    user_id: str,
    run_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    result = result or {}
    payload: dict[str, Any] = {
        "status": status if status in TERMINAL_STATUSES else "failed",
        "finished_at": _iso(now),
        "summary": _summary_fields(result),
        "error": error,
    }
    await _collection(db, SYNC_RUN_COLLECTION).update_one(
        {"user_id": user_id, "run_id": run_id},
        {"$set": payload},
    )


async def _insert_integration_error(
    db: Any,
    *,
    user_id: str,
    provider: str,
    run_id: str,
    source_mode: str,
    code: str,
    message: str,
    retryable: bool,
    now: datetime,
) -> str:
    error_id = str(uuid.uuid4())
    await _collection(db, INTEGRATION_ERROR_COLLECTION).insert_one(
        {
            "error_id": error_id,
            "user_id": user_id,
            "provider": provider,
            "run_id": run_id,
            "source_mode": source_mode,
            "code": code,
            "message": message[:300],
            "retryable": bool(retryable),
            "occurred_at": _iso(now),
            "trigger": SCHEDULER_TRIGGER,
        }
    )
    return error_id


async def _mark_provider_failure(
    db: Any,
    *,
    user_id: str,
    provider: str,
    needs_reauth: bool,
    now: datetime,
) -> None:
    patch: dict[str, Any] = {
        "data_quality": "unavailable",
        "data_delay_minutes": None,
        "checked_at": _iso(now),
        "updated_at": _iso(now),
    }
    if needs_reauth:
        patch["connection_status"] = "needs_reauth"
    # Transient rate/network failures must not disconnect a valid OAuth link.
    await _collection(db, "mezan_integrations_v2").update_one(
        {"user_id": user_id, "provider": provider},
        {"$set": patch},
        upsert=True,
    )


async def _refresh_meta(
    db: Any,
    user_id: str,
    *,
    days: int,
    now: datetime,
) -> dict[str, Any]:
    if not meta_oauth_configured() or not meta_reporting_enabled():
        return {
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
            "status": "skipped",
            "reason": "meta_runtime_not_configured",
        }
    active = await _active_provider_job(
        db, user_id, META_PROVIDER_ID, now=now
    )
    if active:
        return {
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
            "status": "skipped",
            "reason": "provider_sync_in_progress",
            "run_id": active.get("run_id"),
        }

    run_id = await _insert_run(
        db,
        user_id=user_id,
        provider=META_PROVIDER_ID,
        run_type=SCHEDULED_META_RUN_TYPE,
        source_mode=META_REPORTING_SOURCE_MODE,
        days=days,
        now=now,
    )
    try:
        result = await run_meta_reporting_sync(
            db, user_id, MetaReportingSyncInput(days=days)
        )
        status = str(result.get("status") or "complete")
        if status not in {"complete", "partial"}:
            status = "complete"
        await _finish_run(
            db,
            user_id=user_id,
            run_id=run_id,
            status=status,
            result=result,
        )
        return {
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
            "status": status,
            "run_id": run_id,
            **_summary_fields(result),
        }
    except MetaReportingError as exc:
        failed_at = _utcnow()
        error_id = await _insert_integration_error(
            db,
            user_id=user_id,
            provider=META_PROVIDER_ID,
            run_id=run_id,
            source_mode=META_REPORTING_SOURCE_MODE,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            now=failed_at,
        )
        result = exc.result or {}
        await _finish_run(
            db,
            user_id=user_id,
            run_id=run_id,
            status="failed",
            result=result,
            error={
                "error_id": error_id,
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            },
            now=failed_at,
        )
        await _mark_provider_failure(
            db,
            user_id=user_id,
            provider=META_PROVIDER_ID,
            needs_reauth=exc.code == "meta_needs_reauth",
            now=failed_at,
        )
        return {
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
            "status": "failed",
            "run_id": run_id,
            "code": exc.code,
            "retryable": exc.retryable,
        }
    except Exception as exc:  # noqa: BLE001
        failed_at = _utcnow()
        code = "meta_scheduled_refresh_unexpected_failure"
        error_id = await _insert_integration_error(
            db,
            user_id=user_id,
            provider=META_PROVIDER_ID,
            run_id=run_id,
            source_mode=META_REPORTING_SOURCE_MODE,
            code=code,
            message="Meta scheduled reporting refresh failed unexpectedly.",
            retryable=True,
            now=failed_at,
        )
        await _finish_run(
            db,
            user_id=user_id,
            run_id=run_id,
            status="failed",
            error={
                "error_id": error_id,
                "code": code,
                "message": str(exc)[:200],
                "retryable": True,
            },
            now=failed_at,
        )
        return {
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
            "status": "failed",
            "run_id": run_id,
            "code": code,
            "retryable": True,
        }


async def _refresh_snapchat_performance(
    db: Any,
    user_id: str,
    *,
    days: int,
    now: datetime,
) -> dict[str, Any]:
    if not snapchat_oauth_configured() or not snapchat_native_sync_enabled():
        return {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "status": "skipped",
            "reason": "snapchat_runtime_not_configured",
        }
    active = await _active_provider_job(
        db, user_id, SNAPCHAT_PROVIDER_ID, now=now
    )
    if active:
        return {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "status": "skipped",
            "reason": "provider_sync_in_progress",
            "run_id": active.get("run_id"),
        }

    payload = SnapchatNativeSyncInput(days=days)
    dates = enumerate_native_sync_dates(
        payload,
        today=now.astimezone(_timezone(BUSINESS_TIMEZONE)).date(),
    )
    selected_accounts = await _load_selected_accounts(db, user_id)
    run_id = await _insert_run(
        db,
        user_id=user_id,
        provider=SNAPCHAT_PROVIDER_ID,
        run_type=SCHEDULED_SNAPCHAT_RUN_TYPE,
        source_mode=SCHEDULED_SNAPCHAT_SOURCE_MODE,
        days=days,
        now=now,
    )
    await ensure_snapchat_native_sync_indexes(db)
    context = SnapchatSyncContext(db, user_id, now=lambda: _utcnow())

    try:
        access_token = await context.access_token()
        rows_saved = 0
        accounts_complete = 0
        errors: list[dict[str, str]] = []
        async with httpx.AsyncClient(timeout=35.0) as client:
            for account in selected_accounts:
                try:
                    saved, account_errors = await sync_snapchat_performance(
                        context,
                        client,
                        access_token,
                        account,
                        start_date=dates[0],
                        end_date=dates[-1],
                    )
                    rows_saved += int(saved)
                    errors.extend(account_errors)
                    complete = not account_errors
                    accounts_complete += int(complete)
                    account_id = str(account.get("ad_account_id") or "")
                    account_patch = {
                        "performance_rows_saved": int(saved),
                        "last_observed_at": _iso(),
                        "source_mode": SCHEDULED_SNAPCHAT_SOURCE_MODE,
                    }
                    if complete:
                        account_patch.update(
                            {
                                "last_sync_at": _iso(),
                                "data_delay_minutes": 0,
                                "health_score": 100,
                            }
                        )
                    await _collection(
                        db, "mezan_integration_accounts_v2"
                    ).update_one(
                        {
                            "user_id": user_id,
                            "provider": SNAPCHAT_PROVIDER_ID,
                            "$or": [
                                {"external_account_id": account_id},
                                {"ad_account_id": account_id},
                            ],
                        },
                        {"$set": account_patch},
                    )
                except SnapchatNativeSyncError as exc:
                    if exc.code == "snapchat_needs_reauth":
                        raise
                    errors.append(
                        {
                            "kind": "performance",
                            "error": exc.code,
                        }
                    )

        status = "complete" if not errors else "partial"
        result = {
            "provider": SNAPCHAT_PROVIDER_ID,
            "status": status,
            "date_from": dates[0].isoformat(),
            "date_to": dates[-1].isoformat(),
            "accounts_attempted": len(selected_accounts),
            "accounts_complete": accounts_complete,
            "rows_saved": rows_saved,
            "errors_count": len(errors),
            "provider_calls": context.provider_calls,
        }
        finished_at = _utcnow()
        await _collection(db, "mezan_integrations_v2").update_one(
            {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
            {
                "$set": {
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                    "last_sync_at": _iso(finished_at),
                    "checked_at": _iso(finished_at),
                    "updated_at": _iso(finished_at),
                    "data_delay_minutes": 0 if status == "complete" else None,
                    "data_quality": "complete" if status == "complete" else "partial",
                    "health_score": 100 if status == "complete" else 85,
                    "source_mode": SCHEDULED_SNAPCHAT_SOURCE_MODE,
                }
            },
            upsert=True,
        )
        await _finish_run(
            db,
            user_id=user_id,
            run_id=run_id,
            status=status,
            result=result,
            now=finished_at,
        )
        return {
            "user_id": user_id,
            "run_id": run_id,
            **result,
        }
    except SnapchatNativeSyncError as exc:
        failed_at = _utcnow()
        error_id = await _insert_integration_error(
            db,
            user_id=user_id,
            provider=SNAPCHAT_PROVIDER_ID,
            run_id=run_id,
            source_mode=SCHEDULED_SNAPCHAT_SOURCE_MODE,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            now=failed_at,
        )
        await _finish_run(
            db,
            user_id=user_id,
            run_id=run_id,
            status="failed",
            result=exc.result,
            error={
                "error_id": error_id,
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            },
            now=failed_at,
        )
        await _mark_provider_failure(
            db,
            user_id=user_id,
            provider=SNAPCHAT_PROVIDER_ID,
            needs_reauth=exc.code == "snapchat_needs_reauth",
            now=failed_at,
        )
        return {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "status": "failed",
            "run_id": run_id,
            "code": exc.code,
            "retryable": exc.retryable,
        }
    except Exception as exc:  # noqa: BLE001
        failed_at = _utcnow()
        code = "snapchat_scheduled_refresh_unexpected_failure"
        error_id = await _insert_integration_error(
            db,
            user_id=user_id,
            provider=SNAPCHAT_PROVIDER_ID,
            run_id=run_id,
            source_mode=SCHEDULED_SNAPCHAT_SOURCE_MODE,
            code=code,
            message="Snapchat scheduled performance refresh failed unexpectedly.",
            retryable=True,
            now=failed_at,
        )
        await _finish_run(
            db,
            user_id=user_id,
            run_id=run_id,
            status="failed",
            error={
                "error_id": error_id,
                "code": code,
                "message": str(exc)[:200],
                "retryable": True,
            },
            now=failed_at,
        )
        return {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "status": "failed",
            "run_id": run_id,
            "code": code,
            "retryable": True,
        }


async def run_ads_auto_sync_cycle(
    db: Any,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    started_at = now().astimezone(timezone.utc)
    days = ads_auto_sync_rolling_days()
    targets = await _connected_targets(db)
    semaphore = asyncio.Semaphore(3)

    async def execute(user_id: str, provider: str) -> dict[str, Any]:
        async with semaphore:
            if provider == META_PROVIDER_ID:
                return await _refresh_meta(
                    db, user_id, days=days, now=started_at
                )
            return await _refresh_snapchat_performance(
                db, user_id, days=days, now=started_at
            )

    raw_results = await asyncio.gather(
        *(execute(user_id, provider) for user_id, provider in targets),
        return_exceptions=True,
    )
    results: list[dict[str, Any]] = []
    for target, raw in zip(targets, raw_results):
        user_id, provider = target
        if isinstance(raw, Exception):
            logger.exception(
                "ads auto-sync provider task failed: user=%s provider=%s",
                user_id,
                provider,
                exc_info=raw,
            )
            results.append(
                {
                    "user_id": user_id,
                    "provider": provider,
                    "status": "failed",
                    "code": "scheduler_provider_task_failed",
                    "retryable": True,
                }
            )
        else:
            results.append(raw)

    finished_at = now().astimezone(timezone.utc)
    succeeded = sum(
        item.get("status") in {"complete", "partial"}
        for item in results
    )
    failed = sum(item.get("status") == "failed" for item in results)
    skipped = sum(item.get("status") == "skipped" for item in results)
    return {
        "status": "failed" if failed and not succeeded else (
            "partial" if failed else "complete"
        ),
        "started_at": _iso(started_at),
        "finished_at": _iso(finished_at),
        "rolling_days": days,
        "targets": len(targets),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "results": results,
        "tiktok": {
            "status": "webhook_feed",
            "detail": (
                "TikTok continues through the existing automatic data feed "
                "until native OAuth reporting is approved."
            ),
        },
        "runs_without_browser": True,
    }


async def _acquire_cycle_lease(
    db: Any,
    *,
    worker_id: str,
    now: datetime,
    interval_seconds: int,
) -> dict[str, Any] | None:
    collection = _collection(db, SCHEDULER_COLLECTION)
    query = {
        "_id": SCHEDULER_DOCUMENT_ID,
        "$and": [
            {
                "$or": [
                    {"lease_expires_at": {"$lte": now}},
                    {"lease_expires_at": None},
                    {"lease_expires_at": {"$exists": False}},
                ]
            },
            {
                "$or": [
                    {"next_due_at": {"$lte": now}},
                    {"next_due_at": {"$exists": False}},
                ]
            },
        ],
    }
    update = {
        "$set": {
            "status": "running",
            "lease_owner": worker_id,
            "lease_expires_at": now + LEASE_TTL,
            "last_started_at": now,
            "next_due_at": now + timedelta(seconds=interval_seconds),
            "interval_seconds": interval_seconds,
            "rolling_days": ads_auto_sync_rolling_days(),
            "enabled": True,
            "updated_at": now,
        },
        "$setOnInsert": {"created_at": now},
    }
    try:
        document = await collection.find_one_and_update(
            query,
            update,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return None
    if not document or document.get("lease_owner") != worker_id:
        return None
    return document


async def _complete_cycle_lease(
    db: Any,
    *,
    worker_id: str,
    result: dict[str, Any],
    now: datetime,
) -> None:
    await _collection(db, SCHEDULER_COLLECTION).update_one(
        {
            "_id": SCHEDULER_DOCUMENT_ID,
            "lease_owner": worker_id,
        },
        {
            "$set": {
                "status": result.get("status") or "complete",
                "lease_expires_at": now,
                "last_finished_at": now,
                "last_result": result,
                "last_error": None,
                "updated_at": now,
            }
        },
    )


async def _fail_cycle_lease(
    db: Any,
    *,
    worker_id: str,
    error: Exception,
    now: datetime,
) -> None:
    await _collection(db, SCHEDULER_COLLECTION).update_one(
        {
            "_id": SCHEDULER_DOCUMENT_ID,
            "lease_owner": worker_id,
        },
        {
            "$set": {
                "status": "failed",
                "lease_expires_at": now,
                "last_finished_at": now,
                "last_error": {
                    "code": "ads_auto_sync_cycle_failed",
                    "message": str(error)[:300],
                    "retryable": True,
                },
                "updated_at": now,
            }
        },
    )


async def ads_auto_sync_loop(db: Any) -> None:
    if not ads_auto_sync_enabled():
        logger.info("Mezan V2 ads auto-sync is disabled")
        return
    worker_id = _worker_id()
    await asyncio.sleep(ads_auto_sync_startup_delay_seconds())
    logger.info(
        "Mezan V2 ads auto-sync started: interval=%ss rolling_days=%s worker=%s",
        ads_auto_sync_interval_seconds(),
        ads_auto_sync_rolling_days(),
        worker_id,
    )
    while True:
        try:
            now = _utcnow()
            interval = ads_auto_sync_interval_seconds()
            lease = await _acquire_cycle_lease(
                db,
                worker_id=worker_id,
                now=now,
                interval_seconds=interval,
            )
            if lease:
                try:
                    result = await run_ads_auto_sync_cycle(db)
                    await _complete_cycle_lease(
                        db,
                        worker_id=worker_id,
                        result=result,
                        now=_utcnow(),
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Mezan V2 ads auto-sync cycle failed")
                    await _fail_cycle_lease(
                        db,
                        worker_id=worker_id,
                        error=exc,
                        now=_utcnow(),
                    )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Mezan V2 ads auto-sync scheduler heartbeat failed")
        await asyncio.sleep(LOOP_HEARTBEAT_SECONDS)


def _public_scheduler_document(document: dict[str, Any] | None) -> dict[str, Any]:
    document = document or {}
    return {
        "status": document.get("status") or (
            "enabled" if ads_auto_sync_enabled() else "disabled"
        ),
        "last_started_at": document.get("last_started_at"),
        "last_finished_at": document.get("last_finished_at"),
        "next_due_at": document.get("next_due_at"),
        "last_result": document.get("last_result"),
        "last_error": document.get("last_error"),
    }


async def ads_auto_sync_status(db: Any, user_id: str) -> dict[str, Any]:
    scheduler = await _collection(db, SCHEDULER_COLLECTION).find_one(
        {"_id": SCHEDULER_DOCUMENT_ID},
        {"_id": 0, "lease_owner": 0, "lease_expires_at": 0},
    )
    cursor = _collection(db, SYNC_RUN_COLLECTION).find(
        {
            "user_id": user_id,
            "trigger": SCHEDULER_TRIGGER,
            "provider": {"$in": [META_PROVIDER_ID, SNAPCHAT_PROVIDER_ID]},
        },
        {"_id": 0},
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("started_at", -1)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(20)
    runs = await _to_list(cursor, 20)
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        provider = str(run.get("provider") or "")
        if provider and provider not in latest:
            latest[provider] = {
                "run_id": run.get("run_id"),
                "status": run.get("status"),
                "started_at": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "summary": run.get("summary"),
                "error": run.get("error"),
            }
    return {
        "enabled": ads_auto_sync_enabled(),
        "interval_seconds": ads_auto_sync_interval_seconds(),
        "interval_minutes": ads_auto_sync_interval_seconds() // 60,
        "rolling_days": ads_auto_sync_rolling_days(),
        "runs_without_browser": True,
        "scheduler": _public_scheduler_document(scheduler),
        "providers": latest,
        "tiktok": {
            "mode": "automatic_webhook_feed",
            "native_provider_polling": False,
            "reason": "awaiting_tiktok_native_oauth_approval",
        },
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_ads_auto_sync_scheduler(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    """Register status route plus backend startup/shutdown lifecycle hooks."""
    task: asyncio.Task | None = None

    @router.get("/ads-auto-sync/status")
    async def read_ads_auto_sync_status(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await ads_auto_sync_status(db, str(owner["id"]))

    async def start_scheduler() -> None:
        nonlocal task
        if not ads_auto_sync_enabled():
            return
        if task is None or task.done():
            task = asyncio.create_task(
                ads_auto_sync_loop(db),
                name="mezan-v2-ads-auto-sync-5min",
            )

    async def stop_scheduler() -> None:
        nonlocal task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        task = None

    # APIRouter startup handlers are propagated to FastAPI when included.
    router.on_startup.append(start_scheduler)
    router.on_shutdown.append(stop_scheduler)


__all__ = [
    "ADS_AUTO_SYNC_ENABLED_ENV",
    "ADS_AUTO_SYNC_INTERVAL_ENV",
    "ADS_AUTO_SYNC_DAYS_ENV",
    "attach_ads_auto_sync_scheduler",
    "ads_auto_sync_enabled",
    "ads_auto_sync_interval_seconds",
    "ads_auto_sync_rolling_days",
    "ads_auto_sync_status",
    "run_ads_auto_sync_cycle",
]
