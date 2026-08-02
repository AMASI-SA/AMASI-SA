"""Five-minute, source-only advertising performance refresh for Mezan 2.

The runtime refreshes the current Riyadh business day for owner-selected
Snapchat and Meta accounts even when no browser is open. TikTok remains
webhook-driven in this phase. The module never writes campaigns, budgets,
accounting entries, Salla, or Qoyod.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pymongo.errors import DuplicateKeyError

from .meta_native_reporting import (
    MetaReportingError,
    MetaReportingSyncInput,
    meta_reporting_enabled,
    run_meta_reporting_sync,
)
from .meta_oauth_security import meta_oauth_configured
from .snapchat_native_async_routes import (
    create_snapchat_native_sync_job,
    execute_snapchat_native_sync_job,
    get_snapchat_native_sync_job,
)
from .snapchat_native_data_common import (
    SnapchatNativeSyncError,
    SnapchatNativeSyncInput,
    snapchat_native_sync_enabled,
)

logger = logging.getLogger(__name__)

RIYADH_TZ = ZoneInfo("Asia/Riyadh")
AUTO_SYNC_ENABLED_ENV = "MEZAN_ADS_AUTO_SYNC_ENABLED"
AUTO_SYNC_INTERVAL_ENV = "MEZAN_ADS_AUTO_SYNC_INTERVAL_SECONDS"
AUTO_SYNC_INITIAL_DELAY_ENV = "MEZAN_ADS_AUTO_SYNC_INITIAL_DELAY_SECONDS"
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 300
MAX_INTERVAL_SECONDS = 3600
DEFAULT_INITIAL_DELAY_SECONDS = 20
LEASE_RETENTION = timedelta(days=2)
LEASE_COLLECTION = "mezan_ads_auto_sync_leases_v2"
STATUS_COLLECTION = "mezan_ads_auto_sync_status_v2"
RUN_COLLECTION = "mezan_ads_auto_sync_runs_v2"
SCHEDULER_COLLECTION = "mezan_ads_auto_sync_scheduler_v2"
SELECTED_ACCOUNT_COLLECTION = "mezan_integration_accounts_v2"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _collection(db: Any, name: str) -> Any:
    try:
        return db[name]
    except (KeyError, TypeError, AttributeError):
        return getattr(db, name)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def ads_auto_sync_enabled() -> bool:
    return _env_bool(AUTO_SYNC_ENABLED_ENV, True)


def ads_auto_sync_interval_seconds() -> int:
    try:
        parsed = int(os.environ.get(AUTO_SYNC_INTERVAL_ENV, DEFAULT_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        parsed = DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, parsed))


def ads_auto_sync_initial_delay_seconds() -> int:
    try:
        parsed = int(
            os.environ.get(
                AUTO_SYNC_INITIAL_DELAY_ENV,
                DEFAULT_INITIAL_DELAY_SECONDS,
            )
        )
    except (TypeError, ValueError):
        parsed = DEFAULT_INITIAL_DELAY_SECONDS
    return max(0, min(300, parsed))


async def _cursor_rows(cursor: Any, *, limit: int = 1000) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return list(await cursor.to_list(length=limit))
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


async def ensure_ads_auto_sync_indexes(db: Any) -> None:
    await _collection(db, LEASE_COLLECTION).create_index(
        "expires_at",
        expireAfterSeconds=0,
        name="mezan_ads_auto_sync_lease_ttl",
    )
    await _collection(db, STATUS_COLLECTION).create_index(
        "user_id",
        unique=True,
        name="mezan_ads_auto_sync_status_user_unique",
    )
    await _collection(db, RUN_COLLECTION).create_index(
        [("user_id", 1), ("started_at", -1)],
        name="mezan_ads_auto_sync_runs_user_latest",
    )


async def _acquire_cycle_bucket(
    db: Any,
    *,
    now_value: datetime,
    interval_seconds: int,
    instance_id: str,
) -> tuple[bool, int]:
    bucket = int(now_value.timestamp()) // interval_seconds
    document = {
        "_id": f"ads-auto-sync:{bucket}",
        "bucket": bucket,
        "instance_id": instance_id,
        "acquired_at": now_value,
        "expires_at": now_value + LEASE_RETENTION,
    }
    try:
        await _collection(db, LEASE_COLLECTION).insert_one(document)
    except DuplicateKeyError:
        return False, bucket
    return True, bucket


async def _eligible_user_ids(db: Any) -> list[str]:
    selected_cursor = _collection(db, SELECTED_ACCOUNT_COLLECTION).find(
        {
            "provider": {"$in": ["snapchat_ads", "meta_ads"]},
            "mezan_selected": True,
            "connection_provenance": "api_connection",
        },
        {"_id": 0, "user_id": 1},
    )
    selected_rows = await _cursor_rows(selected_cursor, limit=2000)

    # Compatibility fallback for stores that selected Snapchat accounts before
    # Integrations V2 became the canonical account-selection store.
    snapchat_cursor = _collection(db, "snapchat_ad_accounts").find(
        {"enabled": True},
        {"_id": 0, "user_id": 1},
    )
    snapchat_rows = await _cursor_rows(snapchat_cursor, limit=1000)

    return sorted(
        {
            str(row.get("user_id") or "").strip()
            for row in [*selected_rows, *snapchat_rows]
            if str(row.get("user_id") or "").strip()
        }
    )


def _safe_provider_result(
    provider: str,
    *,
    status: str,
    message: str | None = None,
    rows_saved: int = 0,
    errors_count: int = 0,
    run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "status": status,
        "message": message,
        "rows_saved": int(rows_saved or 0),
        "errors_count": int(errors_count or 0),
        "run_id": run_id,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def _sync_snapchat_today(
    db: Any,
    user_id: str,
    *,
    today_iso: str,
    bucket: int,
) -> dict[str, Any]:
    if not snapchat_native_sync_enabled():
        return _safe_provider_result(
            "snapchat",
            status="disabled",
            message="Snapchat native sync safety flag is disabled.",
        )

    payload = SnapchatNativeSyncInput(
        days=1,
        from_date=today_iso,
        to_date=today_iso,
        idempotency_key=f"ads-auto-sync:{today_iso}:{bucket}",
    )
    try:
        accepted = await create_snapchat_native_sync_job(db, user_id, payload)
        run_id = str(accepted.get("run_id") or "")
        await execute_snapchat_native_sync_job(
            db,
            user_id,
            run_id,
            payload.model_dump(),
        )
        final = await get_snapchat_native_sync_job(db, user_id, run_id)
        return _safe_provider_result(
            "snapchat",
            status=str(final.get("status") or "unknown"),
            rows_saved=int(final.get("rows_saved") or 0),
            errors_count=int(final.get("errors_count") or 0),
            run_id=run_id or None,
            message=(final.get("error") or {}).get("message"),
        )
    except SnapchatNativeSyncError as exc:
        status = (
            "already_running"
            if exc.code == "snapchat_analytics_sync_in_progress"
            else "failed"
        )
        return _safe_provider_result(
            "snapchat",
            status=status,
            message=exc.message,
            rows_saved=int((exc.result or {}).get("rows_saved") or 0),
            errors_count=int((exc.result or {}).get("errors_count") or 1),
            run_id=getattr(exc, "run_id", None),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("ads auto-sync: unexpected Snapchat failure for %s", user_id)
        return _safe_provider_result(
            "snapchat",
            status="failed",
            message=str(exc)[:240],
            errors_count=1,
        )


async def _sync_meta_today(
    db: Any,
    user_id: str,
    *,
    today_iso: str,
) -> dict[str, Any]:
    if not meta_oauth_configured():
        return _safe_provider_result(
            "meta",
            status="not_configured",
            message="Meta OAuth is not configured.",
        )
    if not meta_reporting_enabled():
        return _safe_provider_result(
            "meta",
            status="disabled",
            message="Meta native reporting safety flag is disabled.",
        )

    payload = MetaReportingSyncInput(
        days=1,
        from_date=today_iso,
        to_date=today_iso,
    )
    try:
        result = await run_meta_reporting_sync(db, user_id, payload)
        return _safe_provider_result(
            "meta",
            status=str(result.get("status") or "unknown"),
            rows_saved=int(result.get("rows_saved") or 0),
            errors_count=int(result.get("errors_count") or 0),
            run_id=result.get("run_id"),
        )
    except MetaReportingError as exc:
        return _safe_provider_result(
            "meta",
            status="failed",
            message=exc.message,
            rows_saved=int((exc.result or {}).get("rows_saved") or 0),
            errors_count=int((exc.result or {}).get("errors_count") or 1),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("ads auto-sync: unexpected Meta failure for %s", user_id)
        return _safe_provider_result(
            "meta",
            status="failed",
            message=str(exc)[:240],
            errors_count=1,
        )


def _overall_status(providers: dict[str, dict[str, Any]]) -> str:
    pull_results = [providers["snapchat"], providers["meta"]]
    statuses = {str(result.get("status") or "unknown") for result in pull_results}
    successful = statuses & {"complete", "completed", "success"}
    partial = "partial" in statuses
    failed = "failed" in statuses
    if failed and (successful or partial):
        return "partial"
    if failed and not successful and not partial:
        return "failed"
    if partial:
        return "partial"
    if successful:
        return "complete"
    return "idle"


async def _store_user_result(
    db: Any,
    *,
    user_id: str,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    next_run_at: datetime,
    providers: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    status_collection = _collection(db, STATUS_COLLECTION)
    previous = await status_collection.find_one(
        {"user_id": user_id},
        {"_id": 0, "providers": 1},
    ) or {}
    previous_providers = previous.get("providers") or {}
    for provider_name in ("snapchat", "meta"):
        current = providers[provider_name]
        prior = previous_providers.get(provider_name) or {}
        if current.get("status") in {"complete", "completed", "success", "partial"}:
            current["last_success_at"] = _iso(finished_at)
        elif prior.get("last_success_at"):
            current["last_success_at"] = prior["last_success_at"]

    document = {
        "user_id": user_id,
        "enabled": ads_auto_sync_enabled(),
        "interval_seconds": ads_auto_sync_interval_seconds(),
        "interval_minutes": ads_auto_sync_interval_seconds() // 60,
        "mode": "server_background_source_only",
        "status": _overall_status(providers),
        "last_run_id": run_id,
        "last_started_at": _iso(started_at),
        "last_finished_at": _iso(finished_at),
        "next_run_at": _iso(next_run_at),
        "providers": providers,
        "updated_at": _iso(finished_at),
    }
    await status_collection.update_one(
        {"user_id": user_id},
        {"$set": document},
        upsert=True,
    )
    await _collection(db, RUN_COLLECTION).insert_one(
        {
            **document,
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
        }
    )
    return document


async def run_ads_auto_sync_for_user(
    db: Any,
    user_id: str,
    *,
    now_value: datetime,
    bucket: int,
    interval_seconds: int,
) -> dict[str, Any]:
    started_at = _utcnow()
    today_iso = now_value.astimezone(RIYADH_TZ).date().isoformat()
    snapchat, meta = await asyncio.gather(
        _sync_snapchat_today(
            db,
            user_id,
            today_iso=today_iso,
            bucket=bucket,
        ),
        _sync_meta_today(db, user_id, today_iso=today_iso),
    )
    providers = {
        "snapchat": snapchat,
        "meta": meta,
        "tiktok": _safe_provider_result(
            "tiktok",
            status="webhook",
            message="TikTok remains event-driven through the existing webhook feed.",
        ),
    }
    finished_at = _utcnow()
    return await _store_user_result(
        db,
        user_id=user_id,
        run_id=str(uuid.uuid4()),
        started_at=started_at,
        finished_at=finished_at,
        next_run_at=now_value + timedelta(seconds=interval_seconds),
        providers=providers,
    )


async def run_ads_auto_sync_cycle(
    db: Any,
    *,
    now: Callable[[], datetime] = _utcnow,
    instance_id: str | None = None,
) -> dict[str, Any]:
    interval_seconds = ads_auto_sync_interval_seconds()
    now_value = now().astimezone(timezone.utc)
    if not ads_auto_sync_enabled():
        return {
            "enabled": False,
            "interval_seconds": interval_seconds,
            "users_processed": 0,
            "lease_acquired": False,
        }

    worker_id = instance_id or str(uuid.uuid4())
    acquired, bucket = await _acquire_cycle_bucket(
        db,
        now_value=now_value,
        interval_seconds=interval_seconds,
        instance_id=worker_id,
    )
    if not acquired:
        return {
            "enabled": True,
            "interval_seconds": interval_seconds,
            "users_processed": 0,
            "lease_acquired": False,
            "bucket": bucket,
        }

    user_ids = await _eligible_user_ids(db)
    results: list[dict[str, Any]] = []
    for user_id in user_ids:
        results.append(
            await run_ads_auto_sync_for_user(
                db,
                user_id,
                now_value=now_value,
                bucket=bucket,
                interval_seconds=interval_seconds,
            )
        )

    finished_at = _utcnow()
    summary = {
        "enabled": True,
        "interval_seconds": interval_seconds,
        "interval_minutes": interval_seconds // 60,
        "lease_acquired": True,
        "bucket": bucket,
        "instance_id": worker_id,
        "users_processed": len(results),
        "statuses": {
            status: sum(1 for result in results if result.get("status") == status)
            for status in {str(result.get("status") or "unknown") for result in results}
        },
        "started_at": _iso(now_value),
        "finished_at": _iso(finished_at),
        "next_run_at": _iso(now_value + timedelta(seconds=interval_seconds)),
    }
    await _collection(db, SCHEDULER_COLLECTION).update_one(
        {"_id": "global"},
        {"$set": summary},
        upsert=True,
    )
    return summary


async def ads_auto_sync_loop(
    db: Any,
    *,
    sleep: Callable[[float], Any] = asyncio.sleep,
    now: Callable[[], datetime] = _utcnow,
    instance_id: str | None = None,
) -> None:
    worker_id = instance_id or str(uuid.uuid4())
    initial_delay = ads_auto_sync_initial_delay_seconds()
    if initial_delay:
        await sleep(initial_delay)
    while True:
        cycle_started = asyncio.get_running_loop().time()
        try:
            await run_ads_auto_sync_cycle(db, now=now, instance_id=worker_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("ads auto-sync: scheduler cycle failed")
        elapsed = asyncio.get_running_loop().time() - cycle_started
        await sleep(max(1.0, ads_auto_sync_interval_seconds() - elapsed))


async def get_ads_auto_sync_status(db: Any, user_id: str) -> dict[str, Any]:
    interval_seconds = ads_auto_sync_interval_seconds()
    row = await _collection(db, STATUS_COLLECTION).find_one(
        {"user_id": user_id},
        {"_id": 0},
    )
    if row:
        return row
    return {
        "user_id": user_id,
        "enabled": ads_auto_sync_enabled(),
        "interval_seconds": interval_seconds,
        "interval_minutes": interval_seconds // 60,
        "mode": "server_background_source_only",
        "status": "waiting_for_first_cycle",
        "last_run_id": None,
        "last_started_at": None,
        "last_finished_at": None,
        "next_run_at": None,
        "providers": {
            "snapchat": {"status": "waiting", "mode": "native_pull"},
            "meta": {"status": "waiting", "mode": "native_pull"},
            "tiktok": {"status": "webhook", "mode": "event_driven"},
        },
    }


def attach_ads_auto_sync_runtime(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    task: asyncio.Task | None = None

    @router.get("/ads-auto-sync/status")
    async def read_ads_auto_sync_status(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await get_ads_auto_sync_status(db, str(owner["id"]))

    @router.on_event("startup")
    async def start_ads_auto_sync_runtime() -> None:
        nonlocal task
        await ensure_ads_auto_sync_indexes(db)
        if ads_auto_sync_enabled() and (task is None or task.done()):
            task = asyncio.create_task(ads_auto_sync_loop(db))
            logger.info(
                "ads auto-sync: started source-only scheduler interval=%ss",
                ads_auto_sync_interval_seconds(),
            )

    @router.on_event("shutdown")
    async def stop_ads_auto_sync_runtime() -> None:
        nonlocal task
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        task = None


__all__ = [
    "ads_auto_sync_enabled",
    "ads_auto_sync_interval_seconds",
    "attach_ads_auto_sync_runtime",
    "ensure_ads_auto_sync_indexes",
    "get_ads_auto_sync_status",
    "run_ads_auto_sync_cycle",
    "run_ads_auto_sync_for_user",
]
