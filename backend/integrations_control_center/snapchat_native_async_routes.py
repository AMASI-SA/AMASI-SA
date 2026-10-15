"""Timeout-safe background orchestration for canonical Snapchat reporting sync."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from .snapchat_account_selection import _load_selected_accounts
from .snapchat_native_data_common import (
    BUSINESS_TIMEZONE,
    SNAPCHAT_NATIVE_SYNC_LOCK_TTL,
    SNAPCHAT_PROVIDER_ID,
    SNAPCHAT_SYNC_HEARTBEAT_STALE_AFTER,
    SnapchatNativeSyncError,
    SnapchatNativeSyncInput,
    _collection,
    _iso,
    _parse_datetime,
    _timezone,
    _utcnow,
    _start_sync_run_heartbeat,
    _stop_sync_run_heartbeat,
    enumerate_native_sync_dates,
    snapchat_native_sync_enabled,
)
ASYNC_SYNC_RUN_TYPE = "analytics_refresh_async"
ASYNC_SYNC_SOURCE_MODE = "snapchat_marketing_native_async_sync_v2"
ACTIVE_SYNC_STATUSES = ("queued", "running")
SCHEDULER_SYNC_RUN_TYPE = "analytics_refresh"
SCHEDULER_ACTIVE_RUN_TTL = timedelta(minutes=25)
RUN_CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)
CANONICAL_SYNC_RETRY_ATTEMPTS = 12
CANONICAL_SYNC_RETRY_DELAY_SECONDS = 5.0
CANONICAL_SYNC_RETRY_REASONS = {"lease_unavailable", "resource_pressure"}


def _safe_job(document: dict[str, Any]) -> dict[str, Any]:
    summary = (
        document.get("summary")
        if isinstance(document.get("summary"), dict)
        else {}
    )
    error = (
        document.get("error")
        if isinstance(document.get("error"), dict)
        else None
    )
    return {
        "run_id": document.get("run_id"),
        "provider": SNAPCHAT_PROVIDER_ID,
        "run_type": ASYNC_SYNC_RUN_TYPE,
        "status": document.get("status"),
        "date_from": summary.get("date_from"),
        "date_to": summary.get("date_to"),
        "selected_accounts": int(summary.get("selected_accounts") or 0),
        "accounts_attempted": int(summary.get("accounts_attempted") or 0),
        "accounts_complete": int(summary.get("accounts_complete") or 0),
        "rows_saved": int(summary.get("rows_saved") or 0),
        "errors_count": int(summary.get("errors_count") or 0),
        "child_run_id": summary.get("child_run_id"),
        "started_at": document.get("started_at"),
        "finished_at": document.get("finished_at"),
        "source_only": True,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
        "error": (
            {
                "code": error.get("code"),
                "message": error.get("message"),
                "retryable": bool(error.get("retryable")),
            }
            if error
            else None
        ),
    }


def _failure_detail(
    exc: SnapchatNativeSyncError,
    payload: SnapchatNativeSyncInput,
) -> dict[str, Any]:
    result = exc.result or {}
    return {
        "run_id": getattr(exc, "run_id", None),
        "provider": SNAPCHAT_PROVIDER_ID,
        "status": "failed",
        "date_from": result.get("date_from") or payload.from_date,
        "date_to": result.get("date_to") or payload.to_date,
        "accounts_attempted": int(result.get("accounts_synced") or 0),
        "accounts_complete": int(result.get("accounts_complete") or 0),
        "rows_saved": int(result.get("rows_saved") or 0),
        "errors_count": int(result.get("errors_count") or 1),
        "source_only": True,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
    }


async def execute_snapchat_dashboard_sync(
    db: Any,
    user_id: str,
    payload: SnapchatNativeSyncInput,
    *,
    now: Callable[[], datetime] = _utcnow,
    pipeline_factory: Callable[..., Any] | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> dict[str, Any]:
    """Refresh the canonical V2 facts used by Dashboard and Snapchat pages."""
    now_value = now().astimezone(timezone.utc)
    dates = enumerate_native_sync_dates(
        payload,
        today=now_value.astimezone(_timezone(BUSINESS_TIMEZONE)).date(),
    )
    if pipeline_factory is None:
        from snapchat_v2.sync_pipeline import SnapchatV2SyncPipeline

        pipeline_factory = SnapchatV2SyncPipeline

    result: dict[str, Any] = {}
    for attempt in range(CANONICAL_SYNC_RETRY_ATTEMPTS):
        pipeline = pipeline_factory(db, now=now)
        result = await pipeline.run(
            user_id,
            **({"ad_account_id": payload.ad_account_id} if payload.ad_account_id else {}),
            date_from=dates[0],
            date_to=dates[-1],
            action_report_time="conversion",
            run_type="manual",
        )
        status = str(result.get("status") or "").strip().lower()
        reason = str(result.get("reason") or "").strip().lower()
        should_retry = (
            status == "skipped"
            and reason in CANONICAL_SYNC_RETRY_REASONS
            and attempt + 1 < CANONICAL_SYNC_RETRY_ATTEMPTS
        )
        if not should_retry:
            break
        await sleep(CANONICAL_SYNC_RETRY_DELAY_SECONDS)

    status = str(result.get("status") or "").strip().lower()
    summary = (
        result.get("summary")
        if isinstance(result.get("summary"), dict)
        else {}
    )
    warnings = (
        summary.get("warnings")
        if isinstance(summary.get("warnings"), list)
        else []
    )
    if status not in {"complete", "partial"}:
        error = (
            result.get("error")
            if isinstance(result.get("error"), dict)
            else {}
        )
        reason = str(result.get("reason") or "").strip().lower()
        code = str(error.get("code") or "").strip()
        if not code:
            code = (
                f"snapchat_reporting_v2_{reason}"
                if reason
                else "snapchat_reporting_v2_sync_failed"
            )
        raise SnapchatNativeSyncError(
            code,
            "The canonical Snapchat reporting sync did not complete.",
            status_code=503 if status == "skipped" else 502,
            retryable=(
                status == "skipped"
                or bool(error.get("retryable"))
            ),
            result={
                "date_from": dates[0].isoformat(),
                "date_to": dates[-1].isoformat(),
                "accounts_synced": 0,
                "accounts_complete": 0,
                "rows_saved": int(summary.get("rows_saved") or 0),
                "errors_count": 1,
            },
        )

    return {
        "run_id": result.get("sync_run_id"),
        "provider": SNAPCHAT_PROVIDER_ID,
        "status": status,
        "date_from": dates[0].isoformat(),
        "date_to": dates[-1].isoformat(),
        "accounts_attempted": 1,
        "accounts_complete": 1 if status == "complete" else 0,
        "rows_saved": int(summary.get("rows_saved") or 0),
        "errors_count": (
            len(warnings)
            if status == "complete"
            else max(1, len(warnings))
        ),
        "source_only": True,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def _recover_stale_jobs(
    db: Any,
    user_id: str,
    *,
    now_value: datetime,
) -> None:
    collection = _collection(db, "mezan_integration_sync_runs_v2")
    cursor = collection.find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "run_type": {
                "$in": [ASYNC_SYNC_RUN_TYPE, SCHEDULER_SYNC_RUN_TYPE]
            },
            "status": {"$in": list(ACTIVE_SYNC_STATUSES)},
        },
        {
            "_id": 0,
            "run_id": 1,
            "run_type": 1,
            "started_at": 1,
            "created_at": 1,
            "lock_expires_at": 1,
            "status": 1,
            "worker_started_at": 1,
            "worker_heartbeat_at": 1,
        },
    )
    rows = (
        await cursor.to_list(length=20)
        if hasattr(cursor, "to_list")
        else [row async for row in cursor]
    )
    for row in rows:
        run_type = str(row.get("run_type") or "")
        heartbeat = _parse_datetime(row.get("worker_heartbeat_at"))
        heartbeat_live = bool(
            heartbeat
            and now_value - SNAPCHAT_SYNC_HEARTBEAT_STALE_AFTER
            <= heartbeat
            <= now_value + RUN_CLOCK_SKEW_TOLERANCE
        )
        if run_type == ASYNC_SYNC_RUN_TYPE:
            expiry = _parse_datetime(row.get("lock_expires_at"))
            if heartbeat is not None:
                live = heartbeat_live
            elif row.get("status") == "queued":
                live = bool(
                    expiry
                    and now_value < expiry
                    <= now_value + SNAPCHAT_NATIVE_SYNC_LOCK_TTL
                )
            else:
                marker = _parse_datetime(
                    row.get("worker_started_at")
                    or row.get("started_at")
                    or row.get("created_at")
                )
                live = bool(
                    marker
                    and now_value - SCHEDULER_ACTIVE_RUN_TTL
                    <= marker
                    <= now_value + RUN_CLOCK_SKEW_TOLERANCE
                )
            error_code = "snapchat_async_sync_stale"
            message = (
                "The asynchronous Snapchat sync worker stopped "
                "heartbeating before it finished."
            )
        else:
            marker = _parse_datetime(
                row.get("started_at") or row.get("created_at")
            )
            live = heartbeat_live or bool(
                heartbeat is None
                and marker
                and now_value - SCHEDULER_ACTIVE_RUN_TTL
                <= marker
                <= now_value + RUN_CLOCK_SKEW_TOLERANCE
            )
            error_code = "snapchat_scheduler_sync_stale"
            message = (
                "The scheduled Snapchat sync was orphaned before "
                "the manual recovery request."
            )
        if live:
            continue
        await collection.update_one(
            {
                "user_id": user_id,
                "run_id": row.get("run_id"),
                "run_type": run_type,
                "status": {"$in": list(ACTIVE_SYNC_STATUSES)},
            },
            {
                "$set": {
                    "status": "failed",
                    "finished_at": _iso(now_value),
                    "error": {
                        "code": error_code,
                        "message": message,
                        "retryable": True,
                    },
                }
            },
        )


async def _assert_no_active_sync(
    db: Any,
    user_id: str,
    *,
    requested_date_from: str,
    requested_date_to: str,
    requested_ad_account_id: str | None = None,
) -> None:
    active = await _collection(
        db, "mezan_integration_sync_runs_v2"
    ).find_one(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "run_type": {
                "$in": [ASYNC_SYNC_RUN_TYPE, SCHEDULER_SYNC_RUN_TYPE]
            },
            "status": {"$in": list(ACTIVE_SYNC_STATUSES)},
        },
        {
            "_id": 0,
            "run_id": 1,
            "run_type": 1,
            "summary.date_from": 1,
            "summary.date_to": 1,
            "summary.ad_account_id": 1,
        },
        sort=[("started_at", -1)],
    )
    if not active:
        return
    summary = (
        active.get("summary")
        if isinstance(active.get("summary"), dict)
        else {}
    )
    same_manual_request = (
        active.get("run_type") == ASYNC_SYNC_RUN_TYPE
        and summary.get("date_from") == requested_date_from
        and summary.get("date_to") == requested_date_to
        and summary.get("ad_account_id") == requested_ad_account_id
    )
    error = SnapchatNativeSyncError(
        "snapchat_analytics_sync_in_progress",
        "A Snapchat native data sync is already running.",
        status_code=409,
        retryable=True,
        result={
            "date_from": requested_date_from,
            "date_to": requested_date_to,
        },
    )
    # Only a duplicate click for the exact same manual range may join the
    # active job. A scheduler run or another range must never be reported as
    # successful for this request.
    error.run_id = active.get("run_id") if same_manual_request else None
    raise error


async def create_snapchat_native_sync_job(
    db: Any,
    user_id: str,
    payload: SnapchatNativeSyncInput,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Validate and persist a queued job without contacting Snapchat."""
    if not snapchat_native_sync_enabled():
        raise SnapchatNativeSyncError(
            "snapchat_native_sync_disabled",
            "Snapchat native data sync is temporarily disabled.",
            status_code=503,
        )

    now_value = now().astimezone(timezone.utc)
    dates = enumerate_native_sync_dates(
        payload,
        today=now_value.astimezone(_timezone(BUSINESS_TIMEZONE)).date(),
    )
    selected_accounts = await _load_selected_accounts(db, user_id)
    await _recover_stale_jobs(db, user_id, now_value=now_value)
    await _assert_no_active_sync(
        db,
        user_id,
        requested_date_from=dates[0].isoformat(),
        requested_date_to=dates[-1].isoformat(),
        requested_ad_account_id=payload.ad_account_id,
    )

    run_id = str(uuid.uuid4())
    document = {
        "run_id": run_id,
        "user_id": user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "run_type": ASYNC_SYNC_RUN_TYPE,
        "status": "queued",
        "started_at": _iso(now_value),
        "finished_at": None,
        "lock_expires_at": _iso(
            now_value + SNAPCHAT_NATIVE_SYNC_LOCK_TTL
        ),
        "source_mode": ASYNC_SYNC_SOURCE_MODE,
        "summary": {
            "date_from": dates[0].isoformat(),
            "date_to": dates[-1].isoformat(),
            "ad_account_id": payload.ad_account_id,
            "selected_accounts": len(selected_accounts),
            "accounts_attempted": 0,
            "accounts_complete": 0,
            "rows_saved": 0,
            "errors_count": 0,
            "source_only": True,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        },
        "error": None,
    }
    await _collection(
        db, "mezan_integration_sync_runs_v2"
    ).insert_one(document)
    return _safe_job(document)


async def execute_snapchat_native_sync_job(
    db: Any,
    user_id: str,
    run_id: str,
    payload_data: dict[str, Any],
    *,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    """Run the existing bounded sync after the HTTP response has returned."""
    collection = _collection(db, "mezan_integration_sync_runs_v2")
    worker_started_at = now().astimezone(timezone.utc)
    await collection.update_one(
        {
            "user_id": user_id,
            "run_id": run_id,
            "run_type": ASYNC_SYNC_RUN_TYPE,
            "status": "queued",
        },
        {
            "$set": {
                "status": "running",
                "worker_started_at": _iso(worker_started_at),
                "worker_heartbeat_at": _iso(worker_started_at),
            }
        },
    )
    payload = SnapchatNativeSyncInput(**payload_data)
    heartbeat = _start_sync_run_heartbeat(
        collection,
        {
            "user_id": user_id,
            "run_id": run_id,
            "run_type": ASYNC_SYNC_RUN_TYPE,
        },
        now=now,
    )
    try:
        try:
            result = await execute_snapchat_dashboard_sync(
                db,
                user_id,
                payload,
                now=now,
            )
        finally:
            await _stop_sync_run_heartbeat(heartbeat)
    except SnapchatNativeSyncError as exc:
        failure = exc.result or {}
        await collection.update_one(
            {
                "user_id": user_id,
                "run_id": run_id,
                "run_type": ASYNC_SYNC_RUN_TYPE,
            },
            {
                "$set": {
                    "status": "failed",
                    "finished_at": _iso(now()),
                    "summary.accounts_attempted": int(
                        failure.get("accounts_synced") or 0
                    ),
                    "summary.accounts_complete": int(
                        failure.get("accounts_complete") or 0
                    ),
                    "summary.rows_saved": int(
                        failure.get("rows_saved") or 0
                    ),
                    "summary.errors_count": int(
                        failure.get("errors_count") or 1
                    ),
                    "summary.child_run_id": getattr(
                        exc, "run_id", None
                    ),
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "retryable": exc.retryable,
                    },
                }
            },
        )
        return
    except Exception:  # noqa: BLE001
        await collection.update_one(
            {
                "user_id": user_id,
                "run_id": run_id,
                "run_type": ASYNC_SYNC_RUN_TYPE,
            },
            {
                "$set": {
                    "status": "failed",
                    "finished_at": _iso(now()),
                    "summary.errors_count": 1,
                    "error": {
                        "code": "snapchat_async_sync_worker_failed",
                        "message": (
                            "The asynchronous Snapchat sync worker "
                            "failed unexpectedly."
                        ),
                        "retryable": True,
                    },
                }
            },
        )
        return

    await collection.update_one(
        {
            "user_id": user_id,
            "run_id": run_id,
            "run_type": ASYNC_SYNC_RUN_TYPE,
        },
        {
            "$set": {
                "status": result.get("status") or "complete",
                "finished_at": _iso(now()),
                "summary.accounts_attempted": int(
                    result.get("accounts_attempted") or 0
                ),
                "summary.accounts_complete": int(
                    result.get("accounts_complete") or 0
                ),
                "summary.rows_saved": int(
                    result.get("rows_saved") or 0
                ),
                "summary.errors_count": int(
                    result.get("errors_count") or 0
                ),
                "summary.child_run_id": result.get("run_id"),
                "error": None,
            }
        },
    )


async def get_snapchat_native_sync_job(
    db: Any,
    user_id: str,
    run_id: str,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    now_value = now().astimezone(timezone.utc)
    await _recover_stale_jobs(db, user_id, now_value=now_value)
    document = await _collection(
        db, "mezan_integration_sync_runs_v2"
    ).find_one(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "run_type": ASYNC_SYNC_RUN_TYPE,
            "run_id": run_id,
        },
        {"_id": 0},
    )
    if not document:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "snapchat_async_sync_not_found",
                "message": (
                    "تعذر العثور على مهمة مزامنة Snapchat المطلوبة."
                ),
            },
        )
    return _safe_job(document)


def attach_snapchat_native_async_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.post(
        f"/{SNAPCHAT_PROVIDER_ID}/sync-async",
        status_code=202,
        name="start_snapchat_native_sync_job",
    )
    async def start_snapchat_native_sync_job(
        payload: SnapchatNativeSyncInput,
        background_tasks: BackgroundTasks,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        try:
            accepted = await create_snapchat_native_sync_job(
                db, user_id, payload
            )
        except SnapchatNativeSyncError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=_failure_detail(exc, payload),
            ) from exc
        background_tasks.add_task(
            execute_snapchat_native_sync_job,
            db,
            user_id,
            str(accepted["run_id"]),
            payload.model_dump(),
        )
        return accepted

    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/sync-async/{{run_id}}",
        name="get_snapchat_native_sync_job",
    )
    async def read_snapchat_native_sync_job(
        run_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await get_snapchat_native_sync_job(
            db, str(owner["id"]), run_id
        )


__all__ = [
    "ASYNC_SYNC_RUN_TYPE",
    "attach_snapchat_native_async_routes",
    "create_snapchat_native_sync_job",
    "execute_snapchat_dashboard_sync",
    "execute_snapchat_native_sync_job",
    "get_snapchat_native_sync_job",
]
