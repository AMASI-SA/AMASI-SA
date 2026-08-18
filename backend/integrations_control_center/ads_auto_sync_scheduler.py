"""Server-side five-minute advertising refresh for Mezan V2.

This task runs in the backend process and therefore continues when every browser
window is closed.  A Mongo lease makes it safe with multiple workers/restarts.
Only provider analytical facts are refreshed; campaigns, accounting and Qoyod
are never mutated.
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
from .meta_oauth_security import META_PROVIDER_ID, meta_oauth_configured
from .dashboard_ads_platform_refresh import _refresh_meta_hourly
from .google_ads_reporting import (
    GOOGLE_ADS_PROVIDER_ID,
    GOOGLE_ADS_REPORTING_SOURCE_MODE,
    GoogleAdsReportingError,
    google_ads_reporting_enabled,
    run_google_ads_reporting_sync,
)
from .google_oauth_security import google_oauth_configured
from .tiktok_native_reporting import (
    TIKTOK_REPORTING_SOURCE_MODE,
    TikTokReportingError,
    TikTokReportingSyncInput,
    run_tiktok_reporting_sync,
    tiktok_reporting_enabled,
)
from .tiktok_oauth_security import TIKTOK_PROVIDER_ID, tiktok_oauth_configured
from . import snapchat_account_hourly_refresh as snapchat_hourly
from .snapchat_account_hourly_refresh import ACCOUNT_REFRESH_SOURCE_MODE
from .snapchat_account_selection import _load_selected_accounts
from .snapchat_native_data_common import (
    BUSINESS_TIMEZONE,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatSyncContext,
    _collection,
    _timezone,
    ensure_snapchat_native_sync_indexes,
    snapchat_native_sync_enabled,
)
from .snapchat_oauth_security import snapchat_oauth_configured

logger = logging.getLogger(__name__)

ENABLED_ENV = "MEZAN_ADS_AUTO_SYNC_ENABLED"
INTERVAL_ENV = "MEZAN_ADS_AUTO_SYNC_INTERVAL_SECONDS"
ROLLING_DAYS_ENV = "MEZAN_ADS_AUTO_SYNC_DAYS"
STARTUP_DELAY_ENV = "MEZAN_ADS_AUTO_SYNC_STARTUP_DELAY_SECONDS"

DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 300
MAX_INTERVAL_SECONDS = 3600
DEFAULT_ROLLING_DAYS = 2
MAX_ROLLING_DAYS = 7
DEFAULT_STARTUP_DELAY_SECONDS = 45
HEARTBEAT_SECONDS = 15
LEASE_TTL = timedelta(minutes=25)
ACTIVE_JOB_TTL = timedelta(minutes=25)

SCHEDULER_COLLECTION = "mezan_ads_auto_sync_scheduler_v2"
SCHEDULER_ID = "ads-v2-server-scheduler"
RUNS_COLLECTION = "mezan_integration_sync_runs_v2"
ERRORS_COLLECTION = "mezan_integration_errors_v2"
TRIGGER = "server_scheduler_5m"
META_RUN_TYPE = "meta_reporting_async"
SNAP_RUN_TYPE = "analytics_refresh"
TIKTOK_RUN_TYPE = "tiktok_reporting_async"
GOOGLE_RUN_TYPE = "google_ads_reporting_async"
ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("complete", "partial", "failed", "skipped")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def auto_sync_enabled() -> bool:
    raw = str(os.environ.get(ENABLED_ENV, "true")).strip().lower()
    return raw not in {"0", "false", "off", "no", "disabled"}


def interval_seconds() -> int:
    return _bounded_int(
        os.environ.get(INTERVAL_ENV),
        default=DEFAULT_INTERVAL_SECONDS,
        minimum=MIN_INTERVAL_SECONDS,
        maximum=MAX_INTERVAL_SECONDS,
    )


def rolling_days() -> int:
    return _bounded_int(
        os.environ.get(ROLLING_DAYS_ENV),
        default=DEFAULT_ROLLING_DAYS,
        minimum=1,
        maximum=MAX_ROLLING_DAYS,
    )


def startup_delay_seconds() -> int:
    return _bounded_int(
        os.environ.get(STARTUP_DELAY_ENV),
        default=DEFAULT_STARTUP_DELAY_SECONDS,
        minimum=5,
        maximum=600,
    )


def riyadh_date_range(now: datetime, days: int) -> tuple[date, date]:
    current = now.astimezone(_timezone(BUSINESS_TIMEZONE)).date()
    return current - timedelta(days=days - 1), current


def _tiktok_scheduler_state() -> dict[str, Any]:
    configured = tiktok_oauth_configured()
    enabled = configured and tiktok_reporting_enabled()
    if enabled:
        return {
            "mode": "native_polling",
            "status": "native_polling",
            "native_polling": True,
            "reason": None,
        }
    return {
        "mode": "automatic_webhook_feed",
        "status": "automatic_webhook_feed",
        "native_polling": False,
        "reason": (
            "native_reporting_disabled"
            if configured
            else "awaiting_tiktok_oauth_approval"
        ),
    }


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:10]}"


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


async def _targets(db: Any) -> list[tuple[str, str]]:
    cursor = _collection(db, "mezan_integrations_v2").find(
        {
            "provider": {"$in": [
                META_PROVIDER_ID,
                SNAPCHAT_PROVIDER_ID,
                TIKTOK_PROVIDER_ID,
                GOOGLE_ADS_PROVIDER_ID,
            ]},
            "connection_status": "connected",
            "connection_provenance": "api_connection",
        },
        {"_id": 0, "user_id": 1, "provider": 1},
    )
    pairs: set[tuple[str, str]] = set()
    for row in await _to_list(cursor, 2000):
        user_id = str(row.get("user_id") or "").strip()
        provider = str(row.get("provider") or "").strip()
        if user_id and provider in {
            META_PROVIDER_ID,
            SNAPCHAT_PROVIDER_ID,
            TIKTOK_PROVIDER_ID,
            GOOGLE_ADS_PROVIDER_ID,
        }:
            pairs.add((user_id, provider))
    return sorted(pairs)


async def _active_run(
    db: Any,
    *,
    user_id: str,
    provider: str,
    now: datetime,
) -> dict[str, Any] | None:
    runs = _collection(db, RUNS_COLLECTION)
    active = await runs.find_one(
        {
            "user_id": user_id,
            "provider": provider,
            "status": {"$in": list(ACTIVE_STATUSES)},
        },
        {"_id": 0, "run_id": 1, "started_at": 1, "created_at": 1},
        sort=[("started_at", -1), ("created_at", -1)],
    )
    if not active:
        return None
    marker = _parse_datetime(active.get("started_at") or active.get("created_at"))
    if marker and marker >= now - ACTIVE_JOB_TTL:
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
                    "message": "A stale advertising refresh was released.",
                    "retryable": True,
                },
            }
        },
    )
    return None


async def _start_run(
    db: Any,
    *,
    user_id: str,
    provider: str,
    run_type: str,
    source_mode: str,
    start_date: date,
    end_date: date,
    now: datetime,
) -> str:
    run_id = str(uuid.uuid4())
    await _collection(db, RUNS_COLLECTION).insert_one(
        {
            "run_id": run_id,
            "user_id": user_id,
            "provider": provider,
            "run_type": run_type,
            "status": "running",
            "trigger": TRIGGER,
            "created_at": _iso(now),
            "started_at": _iso(now),
            "finished_at": None,
            "lock_expires_at": _iso(now + ACTIVE_JOB_TTL),
            "source_mode": source_mode,
            "summary": {
                "date_from": start_date.isoformat(),
                "date_to": end_date.isoformat(),
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


def _safe_summary(result: dict[str, Any]) -> dict[str, Any]:
    error_samples = []
    for item in list(result.get("error_samples") or result.get("errors") or [])[:10]:
        if not isinstance(item, dict):
            continue
        error_samples.append({
            key: item.get(key)
            for key in ("error_id", "ad_account_id", "code", "message", "retryable", "kind", "error")
            if item.get(key) is not None
        })
    account_provider_calls = []
    for item in list(result.get("account_provider_calls") or [])[:20]:
        if not isinstance(item, dict):
            continue
        account_provider_calls.append({
            "ad_account_id": item.get("ad_account_id"),
            "provider_calls": int(item.get("provider_calls") or 0),
        })
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
        "provider_call_budget_scope": result.get("provider_call_budget_scope"),
        "account_provider_calls": account_provider_calls,
        "campaign_rows_saved": int(result.get("campaign_rows_saved") or 0),
        "campaign_facts_source_mode": result.get("campaign_facts_source_mode"),
        "campaign_facts_schema_version": (
            int(result.get("campaign_facts_schema_version"))
            if result.get("campaign_facts_schema_version") is not None
            else None
        ),
        "error_samples": error_samples,
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
) -> None:
    result = result or {}
    await _collection(db, RUNS_COLLECTION).update_one(
        {"user_id": user_id, "run_id": run_id},
        {
            "$set": {
                "status": status if status in TERMINAL_STATUSES else "failed",
                "finished_at": _iso(),
                "summary": _safe_summary(result),
                "error": error,
            }
        },
    )


async def _evaluate_snapchat_outcomes_after_sync(
    db: Any,
    user_id: str,
    *,
    now: datetime,
    limit: int = 5,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Run a bounded learning batch only after the provider sync lock is released."""
    try:
        from .snapchat_decision_outcomes import evaluate_due_ad_decisions

        result = await asyncio.wait_for(
            evaluate_due_ad_decisions(db, user_id, now=now, limit=limit),
            timeout=timeout_seconds,
        )
        return {
            "status": "complete",
            "scanned": int(result.get("scanned") or 0),
            "eligible_due": int(result.get("eligible_due") or 0),
            "deferred_due": int(result.get("deferred_due") or 0),
            "evaluated": int(result.get("evaluated") or 0),
            "already_recorded": int(result.get("already_recorded") or 0),
            "pending": int(result.get("pending") or 0),
        }
    except asyncio.TimeoutError:
        return {"status": "deferred_timeout", "retryable": True}
    except Exception as exc:
        logger.exception("Snapchat decision outcome evaluation failed")
        return {
            "status": "deferred",
            "retryable": True,
            "error_type": type(exc).__name__,
        }


async def _record_error(
    db: Any,
    *,
    user_id: str,
    provider: str,
    run_id: str,
    source_mode: str,
    code: str,
    message: str,
    retryable: bool,
) -> str:
    error_id = str(uuid.uuid4())
    await _collection(db, ERRORS_COLLECTION).insert_one(
        {
            "error_id": error_id,
            "user_id": user_id,
            "provider": provider,
            "run_id": run_id,
            "trigger": TRIGGER,
            "source_mode": source_mode,
            "code": code,
            "message": message[:300],
            "retryable": bool(retryable),
            "occurred_at": _iso(),
        }
    )
    return error_id


async def _mark_needs_reauth(db: Any, user_id: str, provider: str) -> None:
    await _collection(db, "mezan_integrations_v2").update_one(
        {"user_id": user_id, "provider": provider},
        {
            "$set": {
                "connection_status": "needs_reauth",
                "data_quality": "unavailable",
                "data_delay_minutes": None,
                "checked_at": _iso(),
                "updated_at": _iso(),
            }
        },
        upsert=True,
    )


async def _refresh_meta(
    db: Any,
    *,
    user_id: str,
    start_date: date,
    end_date: date,
    now: datetime,
) -> dict[str, Any]:
    if not meta_oauth_configured() or not meta_reporting_enabled():
        return {"provider": META_PROVIDER_ID, "status": "skipped", "reason": "disabled"}
    active = await _active_run(
        db, user_id=user_id, provider=META_PROVIDER_ID, now=now
    )
    if active:
        return {
            "provider": META_PROVIDER_ID,
            "status": "skipped",
            "reason": "sync_in_progress",
            "run_id": active.get("run_id"),
        }
    run_id = await _start_run(
        db,
        user_id=user_id,
        provider=META_PROVIDER_ID,
        run_type=META_RUN_TYPE,
        source_mode=META_REPORTING_SOURCE_MODE,
        start_date=start_date,
        end_date=end_date,
        now=now,
    )
    try:
        result = await run_meta_reporting_sync(
            db,
            user_id,
            MetaReportingSyncInput(
                days=(end_date - start_date).days + 1,
                from_date=start_date.isoformat(),
                to_date=end_date.isoformat(),
            ),
        )
        # Reuse the exact Meta hourly projection already used by the Dashboard
        # refresh path. This is part of the same canonical Meta scheduler run:
        # no second scheduler, OAuth flow, or parallel Meta sync pipeline.
        hourly = await _refresh_meta_hourly(db, user_id, end_date)
        result = {**result, "hourly": hourly}
        status = str(result.get("status") or "complete")
        if status not in {"complete", "partial"}:
            status = "complete"
        await _finish_run(
            db, user_id=user_id, run_id=run_id, status=status, result=result
        )
        return {"provider": META_PROVIDER_ID, "run_id": run_id, "status": status, **_safe_summary(result)}
    except MetaReportingError as exc:
        error_id = await _record_error(
            db,
            user_id=user_id,
            provider=META_PROVIDER_ID,
            run_id=run_id,
            source_mode=META_REPORTING_SOURCE_MODE,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
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
        )
        if exc.code == "meta_needs_reauth":
            await _mark_needs_reauth(db, user_id, META_PROVIDER_ID)
        return {"provider": META_PROVIDER_ID, "run_id": run_id, "status": "failed", "code": exc.code}


async def _refresh_tiktok(
    db: Any,
    *,
    user_id: str,
    start_date: date,
    end_date: date,
    now: datetime,
) -> dict[str, Any]:
    if not tiktok_oauth_configured() or not tiktok_reporting_enabled():
        return {
            "provider": TIKTOK_PROVIDER_ID,
            "status": "skipped",
            "reason": "disabled",
        }
    active = await _active_run(
        db, user_id=user_id, provider=TIKTOK_PROVIDER_ID, now=now
    )
    if active:
        return {
            "provider": TIKTOK_PROVIDER_ID,
            "status": "skipped",
            "reason": "sync_in_progress",
            "run_id": active.get("run_id"),
        }
    run_id = await _start_run(
        db,
        user_id=user_id,
        provider=TIKTOK_PROVIDER_ID,
        run_type=TIKTOK_RUN_TYPE,
        source_mode=TIKTOK_REPORTING_SOURCE_MODE,
        start_date=start_date,
        end_date=end_date,
        now=now,
    )
    try:
        result = await run_tiktok_reporting_sync(
            db,
            user_id,
            TikTokReportingSyncInput(
                days=(end_date - start_date).days + 1,
                from_date=start_date.isoformat(),
                to_date=end_date.isoformat(),
            ),
        )
        status = str(result.get("status") or "complete")
        if status not in {"complete", "partial"}:
            status = "complete"
        await _finish_run(
            db, user_id=user_id, run_id=run_id, status=status, result=result
        )
        return {
            "provider": TIKTOK_PROVIDER_ID,
            "run_id": run_id,
            "status": status,
            **_safe_summary(result),
        }
    except TikTokReportingError as exc:
        error_id = await _record_error(
            db,
            user_id=user_id,
            provider=TIKTOK_PROVIDER_ID,
            run_id=run_id,
            source_mode=TIKTOK_REPORTING_SOURCE_MODE,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
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
        )
        if exc.code == "tiktok_needs_reauth":
            await _mark_needs_reauth(db, user_id, TIKTOK_PROVIDER_ID)
        return {
            "provider": TIKTOK_PROVIDER_ID,
            "run_id": run_id,
            "status": "failed",
            "code": exc.code,
        }


async def _refresh_google(
    db: Any,
    *,
    user_id: str,
    start_date: date,
    end_date: date,
    now: datetime,
) -> dict[str, Any]:
    if not google_oauth_configured() or not google_ads_reporting_enabled():
        return {
            "provider": GOOGLE_ADS_PROVIDER_ID,
            "status": "skipped",
            "reason": "disabled",
        }
    active = await _active_run(
        db, user_id=user_id, provider=GOOGLE_ADS_PROVIDER_ID, now=now
    )
    if active:
        return {
            "provider": GOOGLE_ADS_PROVIDER_ID,
            "status": "skipped",
            "reason": "sync_in_progress",
            "run_id": active.get("run_id"),
        }
    run_id = await _start_run(
        db,
        user_id=user_id,
        provider=GOOGLE_ADS_PROVIDER_ID,
        run_type=GOOGLE_RUN_TYPE,
        source_mode=GOOGLE_ADS_REPORTING_SOURCE_MODE,
        start_date=start_date,
        end_date=end_date,
        now=now,
    )
    try:
        result = await run_google_ads_reporting_sync(
            db,
            user_id,
            date_from=start_date.isoformat(),
            date_to=end_date.isoformat(),
        )
        status = str(result.get("status") or "complete")
        if status not in {"complete", "partial", "failed"}:
            status = "complete"
        await _finish_run(
            db, user_id=user_id, run_id=run_id, status=status, result=result
        )
        return {
            "provider": GOOGLE_ADS_PROVIDER_ID,
            "run_id": run_id,
            "status": status,
            **_safe_summary(result),
        }
    except GoogleAdsReportingError as exc:
        error_id = await _record_error(
            db,
            user_id=user_id,
            provider=GOOGLE_ADS_PROVIDER_ID,
            run_id=run_id,
            source_mode=GOOGLE_ADS_REPORTING_SOURCE_MODE,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
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
        )
        if exc.code == "google_ads_needs_reauth":
            await _mark_needs_reauth(db, user_id, GOOGLE_ADS_PROVIDER_ID)
        return {
            "provider": GOOGLE_ADS_PROVIDER_ID,
            "run_id": run_id,
            "status": "failed",
            "code": exc.code,
        }


async def _refresh_snapchat(
    db: Any,
    *,
    user_id: str,
    start_date: date,
    end_date: date,
    now: datetime,
) -> dict[str, Any]:
    if not snapchat_oauth_configured() or not snapchat_native_sync_enabled():
        return {"provider": SNAPCHAT_PROVIDER_ID, "status": "skipped", "reason": "disabled"}
    active = await _active_run(
        db, user_id=user_id, provider=SNAPCHAT_PROVIDER_ID, now=now
    )
    if active:
        return {
            "provider": SNAPCHAT_PROVIDER_ID,
            "status": "skipped",
            "reason": "sync_in_progress",
            "run_id": active.get("run_id"),
        }
    run_id = await _start_run(
        db,
        user_id=user_id,
        provider=SNAPCHAT_PROVIDER_ID,
        run_type=SNAP_RUN_TYPE,
        source_mode=ACCOUNT_REFRESH_SOURCE_MODE,
        start_date=start_date,
        end_date=end_date,
        now=now,
    )
    try:
        accounts = await _load_selected_accounts(db, user_id)
        await ensure_snapchat_native_sync_indexes(db)
        token_context = SnapchatSyncContext(db, user_id, now=_utcnow)
        access_token = await token_context.access_token()
        items: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        provider_calls_total = 0
        account_provider_calls: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=35.0) as client:
            for account in accounts:
                account_context = SnapchatSyncContext(db, user_id, now=_utcnow)
                account_id = str(account.get("ad_account_id") or "").strip()
                try:
                    item = await snapchat_hourly.refresh_snapchat_account_hours(
                        account_context,
                        client,
                        access_token,
                        account,
                        start_date=start_date,
                        end_date=end_date,
                        now=now,
                    )
                    items.append(item)
                    for item_error in item.get("errors") or []:
                        code = str(item_error.get("code") or "snapchat_account_stats_partial")
                        message = str(
                            item_error.get("message")
                            or item_error.get("error")
                            or "Snapchat returned a partial account stats response."
                        )
                        error_id = await _record_error(
                            db,
                            user_id=user_id,
                            provider=SNAPCHAT_PROVIDER_ID,
                            run_id=run_id,
                            source_mode=ACCOUNT_REFRESH_SOURCE_MODE,
                            code=code,
                            message=f"account={account_id}: {message}",
                            retryable=bool(item_error.get("retryable")),
                        )
                        errors.append({
                            "error_id": error_id,
                            "ad_account_id": account_id,
                            "code": code,
                            "message": message[:300],
                            "retryable": bool(item_error.get("retryable")),
                        })
                    await _collection(db, "mezan_integration_accounts_v2").update_one(
                        {
                            "user_id": user_id,
                            "provider": SNAPCHAT_PROVIDER_ID,
                            "$or": [
                                {"external_account_id": account_id},
                                {"ad_account_id": account_id},
                            ],
                        },
                        {
                            "$set": {
                                "last_sync_at": _iso(),
                                "last_observed_at": _iso(),
                                "data_delay_minutes": 0,
                                "health_score": 100,
                                "performance_rows_saved": int(item.get("rows_saved") or 0),
                                "source_mode": ACCOUNT_REFRESH_SOURCE_MODE,
                            }
                        },
                    )
                except SnapchatNativeSyncError as exc:
                    if exc.code == "snapchat_needs_reauth":
                        raise
                    error_id = await _record_error(
                        db,
                        user_id=user_id,
                        provider=SNAPCHAT_PROVIDER_ID,
                        run_id=run_id,
                        source_mode=ACCOUNT_REFRESH_SOURCE_MODE,
                        code=exc.code,
                        message=f"account={account_id}: {exc.message}",
                        retryable=exc.retryable,
                    )
                    errors.append({
                        "error_id": error_id,
                        "ad_account_id": account_id,
                        "code": exc.code,
                        "message": exc.message[:300],
                        "retryable": exc.retryable,
                    })
                finally:
                    provider_calls_total += int(account_context.provider_calls)
                    account_provider_calls.append({
                        "ad_account_id": account_id,
                        "provider_calls": int(account_context.provider_calls),
                    })
        rows_saved = sum(int(item.get("rows_saved") or 0) for item in items)
        campaign_rows_saved = sum(
            int(item.get("campaign_rows_saved") or 0) for item in items
        )
        complete = sum(int(item.get("errors_count") or 0) == 0 for item in items)
        status = "complete" if not errors and complete == len(accounts) else "partial"
        campaign_facts_complete = bool(accounts) and len(items) == len(accounts) and all(
            int(item.get("errors_count") or 0) == 0
            and item.get("campaign_facts_source_mode")
            == snapchat_hourly.CAMPAIGN_FACTS_SOURCE_MODE
            and int(item.get("campaign_facts_schema_version") or 0)
            == snapchat_hourly.CAMPAIGN_FACTS_SCHEMA_VERSION
            for item in items
        )
        decision_outcomes = {
            "status": (
                "queued_outside_sync"
                if status == "complete"
                else "deferred_partial_refresh"
            ),
            "scanned": 0,
            "evaluated": 0,
            "already_recorded": 0,
            "pending": 0,
        }
        result = {
            "provider": SNAPCHAT_PROVIDER_ID,
            "status": status,
            "date_from": start_date.isoformat(),
            "date_to": end_date.isoformat(),
            "accounts_attempted": len(accounts),
            "accounts_complete": complete,
            "rows_saved": rows_saved,
            "campaign_rows_saved": campaign_rows_saved,
            "campaign_facts_source_mode": (
                snapchat_hourly.CAMPAIGN_FACTS_SOURCE_MODE
                if campaign_facts_complete
                else None
            ),
            "campaign_facts_schema_version": (
                snapchat_hourly.CAMPAIGN_FACTS_SCHEMA_VERSION
                if campaign_facts_complete
                else None
            ),
            "errors_count": len(errors),
            "provider_calls": provider_calls_total,
            "provider_call_budget_scope": "per_selected_account",
            "account_provider_calls": account_provider_calls,
            "error_samples": errors[:10],
            "decision_outcomes": decision_outcomes,
        }
        await _collection(db, "mezan_integrations_v2").update_one(
            {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
            {
                "$set": {
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                    "last_sync_at": _iso(),
                    "checked_at": _iso(),
                    "updated_at": _iso(),
                    "data_delay_minutes": 0 if status == "complete" else None,
                    "data_quality": "complete" if status == "complete" else "partial",
                    "health_score": 100 if status == "complete" else 85,
                    "source_mode": ACCOUNT_REFRESH_SOURCE_MODE,
                }
            },
            upsert=True,
        )
        await _finish_run(
            db, user_id=user_id, run_id=run_id, status=status, result=result
        )
        if status == "complete":
            decision_outcomes = await _evaluate_snapchat_outcomes_after_sync(
                db,
                user_id,
                now=now,
            )
            result["decision_outcomes"] = decision_outcomes
        return {"run_id": run_id, **result}
    except SnapchatNativeSyncError as exc:
        error_id = await _record_error(
            db,
            user_id=user_id,
            provider=SNAPCHAT_PROVIDER_ID,
            run_id=run_id,
            source_mode=ACCOUNT_REFRESH_SOURCE_MODE,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
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
        )
        if exc.code == "snapchat_needs_reauth":
            await _mark_needs_reauth(db, user_id, SNAPCHAT_PROVIDER_ID)
        return {"provider": SNAPCHAT_PROVIDER_ID, "run_id": run_id, "status": "failed", "code": exc.code}


async def run_auto_sync_cycle(
    db: Any,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    started = now().astimezone(timezone.utc)
    start_date, end_date = riyadh_date_range(started, rolling_days())
    targets = await _targets(db)
    semaphore = asyncio.Semaphore(3)

    async def execute(user_id: str, provider: str) -> dict[str, Any]:
        async with semaphore:
            if provider == META_PROVIDER_ID:
                return await _refresh_meta(
                    db,
                    user_id=user_id,
                    start_date=start_date,
                    end_date=end_date,
                    now=started,
                )
            if provider == TIKTOK_PROVIDER_ID:
                return await _refresh_tiktok(
                    db,
                    user_id=user_id,
                    start_date=start_date,
                    end_date=end_date,
                    now=started,
                )
            if provider == GOOGLE_ADS_PROVIDER_ID:
                return await _refresh_google(
                    db,
                    user_id=user_id,
                    start_date=start_date,
                    end_date=end_date,
                    now=started,
                )
            return await _refresh_snapchat(
                db,
                user_id=user_id,
                start_date=start_date,
                end_date=end_date,
                now=started,
            )

    raw = await asyncio.gather(
        *(execute(user_id, provider) for user_id, provider in targets),
        return_exceptions=True,
    )
    results: list[dict[str, Any]] = []
    for (user_id, provider), item in zip(targets, raw):
        if isinstance(item, Exception):
            logger.error(
                "ads auto-sync provider task failed user=%s provider=%s: %s",
                user_id,
                provider,
                item,
            )
            results.append(
                {
                    "provider": provider,
                    "status": "failed",
                    "code": "scheduler_provider_task_failed",
                }
            )
        else:
            results.append(item)
    failed = sum(item.get("status") == "failed" for item in results)
    succeeded = sum(item.get("status") in {"complete", "partial"} for item in results)
    skipped = sum(item.get("status") == "skipped" for item in results)
    return {
        "status": "failed" if failed and not succeeded else ("partial" if failed else "complete"),
        "started_at": _iso(started),
        "finished_at": _iso(now()),
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "targets": len(targets),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "results": results,
        "runs_without_browser": True,
        "tiktok": _tiktok_scheduler_state(),
    }


async def _acquire_lease(
    db: Any,
    *,
    worker_id: str,
    now: datetime,
) -> bool:
    collection = _collection(db, SCHEDULER_COLLECTION)
    try:
        document = await collection.find_one_and_update(
            {
                "_id": SCHEDULER_ID,
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
            },
            {
                "$set": {
                    "status": "running",
                    "lease_owner": worker_id,
                    "lease_expires_at": now + LEASE_TTL,
                    "last_started_at": now,
                    "next_due_at": now + timedelta(seconds=interval_seconds()),
                    "interval_seconds": interval_seconds(),
                    "rolling_days": rolling_days(),
                    "enabled": True,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return False
    return bool(document and document.get("lease_owner") == worker_id)


async def _release_lease(
    db: Any,
    *,
    worker_id: str,
    result: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> None:
    now = _utcnow()
    patch: dict[str, Any] = {
        "lease_expires_at": now,
        "last_finished_at": now,
        "updated_at": now,
    }
    if error is None:
        patch.update(
            {
                "status": (result or {}).get("status") or "complete",
                "last_result": result,
                "last_error": None,
            }
        )
    else:
        patch.update(
            {
                "status": "failed",
                "last_error": {
                    "code": "ads_auto_sync_cycle_failed",
                    "message": str(error)[:300],
                    "retryable": True,
                },
            }
        )
    await _collection(db, SCHEDULER_COLLECTION).update_one(
        {"_id": SCHEDULER_ID, "lease_owner": worker_id},
        {"$set": patch},
    )


async def auto_sync_loop(db: Any) -> None:
    if not auto_sync_enabled():
        logger.info("Mezan V2 ads auto-sync disabled")
        return
    worker_id = _worker_id()
    await asyncio.sleep(startup_delay_seconds())
    logger.info(
        "Mezan V2 ads auto-sync started interval=%ss worker=%s",
        interval_seconds(),
        worker_id,
    )
    while True:
        try:
            acquired = await _acquire_lease(
                db, worker_id=worker_id, now=_utcnow()
            )
            if acquired:
                try:
                    result = await run_auto_sync_cycle(db)
                    await _release_lease(
                        db, worker_id=worker_id, result=result
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Mezan V2 ads auto-sync cycle failed")
                    await _release_lease(
                        db, worker_id=worker_id, error=exc
                    )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Mezan V2 ads auto-sync heartbeat failed")
        await asyncio.sleep(HEARTBEAT_SECONDS)


async def auto_sync_status(db: Any, user_id: str) -> dict[str, Any]:
    scheduler = await _collection(db, SCHEDULER_COLLECTION).find_one(
        {"_id": SCHEDULER_ID},
        {"_id": 0, "lease_owner": 0, "lease_expires_at": 0},
    ) or {}
    cursor = _collection(db, RUNS_COLLECTION).find(
        {
            "user_id": user_id,
            "trigger": TRIGGER,
            "provider": {"$in": [
                META_PROVIDER_ID,
                SNAPCHAT_PROVIDER_ID,
                TIKTOK_PROVIDER_ID,
                GOOGLE_ADS_PROVIDER_ID,
            ]},
        },
        {"_id": 0},
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("started_at", -1)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(20)
    latest: dict[str, dict[str, Any]] = {}
    for run in await _to_list(cursor, 20):
        provider = str(run.get("provider") or "")
        if provider and provider not in latest:
            latest[provider] = run
    global_last_result = scheduler.get("last_result")
    global_last_result = (
        global_last_result if isinstance(global_last_result, dict) else {}
    )
    global_last_error = scheduler.get("last_error")
    global_last_error = (
        global_last_error if isinstance(global_last_error, dict) else None
    )
    return {
        "enabled": auto_sync_enabled(),
        "interval_seconds": interval_seconds(),
        "interval_minutes": interval_seconds() // 60,
        "rolling_days": rolling_days(),
        "runs_without_browser": True,
        "scheduler": {
            "status": scheduler.get("status") or "pending",
            "last_started_at": scheduler.get("last_started_at"),
            "last_finished_at": scheduler.get("last_finished_at"),
            "next_due_at": scheduler.get("next_due_at"),
            "last_result": (
                {
                    "status": global_last_result.get("status"),
                    "started_at": global_last_result.get("started_at"),
                    "finished_at": global_last_result.get("finished_at"),
                }
                if global_last_result
                else None
            ),
            "last_error": (
                {
                    "code": "ads_auto_sync_cycle_failed",
                    "retryable": bool(global_last_error.get("retryable", True)),
                }
                if global_last_error
                else None
            ),
        },
        "providers": latest,
        "tiktok": _tiktok_scheduler_state(),
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
    task: asyncio.Task | None = None

    @router.get("/ads-auto-sync/status")
    async def read_status(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await auto_sync_status(db, str(owner["id"]))

    async def start() -> None:
        nonlocal task
        if auto_sync_enabled() and (task is None or task.done()):
            task = asyncio.create_task(
                auto_sync_loop(db),
                name="mezan-v2-ads-auto-sync-5min",
            )

    async def stop() -> None:
        nonlocal task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        task = None

    router.on_startup.append(start)
    router.on_shutdown.append(stop)


__all__ = [
    "ENABLED_ENV",
    "INTERVAL_ENV",
    "ROLLING_DAYS_ENV",
    "attach_ads_auto_sync_scheduler",
    "auto_sync_enabled",
    "auto_sync_status",
    "interval_seconds",
    "riyadh_date_range",
    "rolling_days",
    "run_auto_sync_cycle",
]
