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
from .snapchat_account_selection import (
    _load_canonical_scheduler_accounts,
)
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
CAMPAIGN_AI_EXECUTION_PROOF_DAYS = 3
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
SNAPCHAT_PERFORMANCE_RESULT_KEYS = (
    "ad_squad_performance",
    "ad_performance",
)
SNAPCHAT_FAILURE_STAGES = frozenset({
    "integration_account_credential_proof",
    "selected_accounts_load",
    "fact_storage_prepare",
    "credential_decrypt_or_refresh",
    "provider_refresh",
    "fact_write",
    "account_state_persist",
    "coverage_aggregation",
    "integration_state_persist",
    "run_finalize",
    "decision_outcomes_evaluation",
})
SNAPCHAT_DEFAULT_FAILURE_STAGE = "integration_account_credential_proof"
SNAPCHAT_FAILURE_MODULE_PREFIX = "integrations_control_center"
SNAPCHAT_FAILURE_MODULE_LIMIT = 160
SNAPCHAT_FAILURE_FUNCTION_LIMIT = 80
SNAPCHAT_FAILURE_LINE_LIMIT = 1_000_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_ascii_identifier(value: Any, *, limit: int) -> str | None:
    if (
        isinstance(value, str)
        and value == value.strip()
        and 1 <= len(value) <= limit
        and value.isascii()
        and (value[0].isalpha() or value[0] == "_")
        and all(character.isalnum() or character == "_" for character in value)
    ):
        return value
    return None


def _safe_exception_type(exc: BaseException) -> str:
    try:
        name = type(exc).__name__
    except Exception:  # noqa: BLE001
        return "Exception"
    safe_name = _safe_ascii_identifier(name, limit=80)
    if safe_name:
        return safe_name
    return "Exception"


def _safe_failure_location_values(
    module: Any,
    function: Any,
    line: Any,
) -> dict[str, Any]:
    if (
        not isinstance(module, str)
        or module != module.strip()
        or not module
        or len(module) > SNAPCHAT_FAILURE_MODULE_LIMIT
        or not module.isascii()
    ):
        return {}
    module_parts = module.split(".")
    if (
        module_parts[0] != SNAPCHAT_FAILURE_MODULE_PREFIX
        or any(
            not part
            or not (part[0].isalpha() or part[0] == "_")
            or any(not (character.isalnum() or character == "_") for character in part)
            for part in module_parts
        )
    ):
        return {}
    if (
        _safe_ascii_identifier(
            function,
            limit=SNAPCHAT_FAILURE_FUNCTION_LIMIT,
        )
        is None
    ):
        return {}
    if (
        isinstance(line, bool)
        or not isinstance(line, int)
        or not (1 <= line <= SNAPCHAT_FAILURE_LINE_LIMIT)
    ):
        return {}
    return {
        "failure_module": module,
        "failure_function": function,
        "failure_line": int(line),
    }


def _safe_failure_location(exc: BaseException) -> dict[str, Any]:
    """Return the deepest scheduler-package frame without source, path or locals."""
    try:
        current = exc.__traceback__
    except Exception:  # noqa: BLE001
        return {}
    candidate: dict[str, Any] = {}
    traversed = 0
    while current is not None and traversed < 64:
        traversed += 1
        try:
            frame = current.tb_frame
            location = _safe_failure_location_values(
                frame.f_globals.get("__name__"),
                frame.f_code.co_name,
                current.tb_lineno,
            )
            next_traceback = current.tb_next
        except Exception:  # noqa: BLE001
            break
        if location:
            candidate = location
        current = next_traceback
    return candidate


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
            TIKTOK_PROVIDER_ID,
            GOOGLE_ADS_PROVIDER_ID,
        }:
            pairs.add((user_id, provider))

    # Migrated Snapchat integrations can predate connection_provenance.  Keep
    # discovery read-only and broad across connected tenants; the exact
    # selected-account and credential proof is repeated after the durable run
    # starts and immediately before any provider or fact writer is reached.
    snapchat_cursor = _collection(db, "mezan_integrations_v2").find(
        {
            "provider": SNAPCHAT_PROVIDER_ID,
            "connection_status": "connected",
        },
        {"_id": 0, "user_id": 1},
    )
    snapchat_user_ids = {
        value
        for row in await _to_list(snapchat_cursor, 2000)
        if isinstance((value := row.get("user_id")), str)
        and value
        and value == value.strip()
    }
    for user_id in snapchat_user_ids:
        pairs.add((user_id, SNAPCHAT_PROVIDER_ID))
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
    coverage = result.get("coverage")
    safe_coverage = (
        {
            key: coverage.get(key)
            for key in (
                "status",
                "data_state",
                "expected_requests",
                "completed_requests",
            )
            if coverage.get(key) is not None
        }
        if isinstance(coverage, dict)
        else None
    )
    financial_proof = result.get("financial_proof")
    safe_financial_proof = None
    if isinstance(financial_proof, dict):
        financial_coverage = financial_proof.get("coverage")
        safe_financial_coverage = (
            {
                key: financial_coverage.get(key)
                for key in (
                    "status",
                    "data_state",
                    "expected_requests",
                    "completed_requests",
                )
                if financial_coverage.get(key) is not None
            }
            if isinstance(financial_coverage, dict)
            else None
        )
        safe_financial_proof = {
            key: financial_proof.get(key)
            for key in (
                "version",
                "status",
                "accounts_complete",
                "errors_count",
            )
            if financial_proof.get(key) is not None
        }
        safe_financial_proof["coverage"] = safe_financial_coverage
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
        "coverage": safe_coverage,
        "financial_proof": safe_financial_proof,
        "error_samples": error_samples,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def _strict_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return int(value)


def _snapchat_coverage_complete(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("status") != "complete":
        return False
    if value.get("data_state") not in {
        "confirmed_data",
        "confirmed_zero",
        "confirmed_no_data",
    }:
        return False
    expected = _strict_nonnegative_int(value.get("expected_requests"))
    completed = _strict_nonnegative_int(value.get("completed_requests"))
    if expected is None or completed is None:
        return False
    return expected > 0 and completed == expected


def _snapchat_item_errors(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten P0 performance failures without treating HTTP 200 as success."""
    errors = _snapchat_financial_item_errors(item)

    for key in SNAPCHAT_PERFORMANCE_RESULT_KEYS:
        nested = item.get(key)
        if not isinstance(nested, dict):
            errors.append({
                "kind": key,
                "code": f"snapchat_{key}_result_missing",
                "message": f"Snapchat {key} result is missing.",
                "retryable": True,
            })
            continue
        raw_nested_errors = nested.get("errors", [])
        nested_errors = [
            dict(error)
            for error in raw_nested_errors
            if isinstance(error, dict)
        ] if isinstance(raw_nested_errors, list) else []
        if not isinstance(raw_nested_errors, list) or any(
            not isinstance(error, dict) for error in raw_nested_errors
        ):
            errors.append({
                "kind": key,
                "code": f"snapchat_{key}_error_envelope_invalid",
                "message": f"Snapchat {key} error metadata is malformed.",
                "retryable": True,
            })
        for error in nested_errors:
            error.setdefault("kind", key)
            errors.append(error)
        nested_error_count = _strict_nonnegative_int(
            nested.get("errors_count", 0)
        )
        if nested_error_count is None:
            errors.append({
                "kind": key,
                "code": f"snapchat_{key}_error_count_invalid",
                "message": f"Snapchat {key} error metadata is malformed.",
                "retryable": True,
            })
        elif nested_error_count > 0 and not nested_errors:
            errors.append({
                "kind": key,
                "code": f"snapchat_{key}_partial",
                "message": f"Snapchat {key} response was partial.",
                "retryable": True,
            })
        if not _snapchat_coverage_complete(nested.get("coverage")):
            errors.append({
                "kind": key,
                "code": f"snapchat_{key}_coverage_incomplete",
                "message": f"Snapchat {key} coverage was not proven complete.",
                "retryable": True,
            })
    return errors


def _snapchat_financial_item_errors(
    item: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate only the canonical account-hour facts used by finance."""
    raw_errors = item.get("errors", [])
    errors = [
        dict(error)
        for error in raw_errors
        if isinstance(error, dict)
    ] if isinstance(raw_errors, list) else []
    if not isinstance(raw_errors, list) or any(
        not isinstance(error, dict) for error in raw_errors
    ):
        errors.append({
            "kind": "account_hour_performance",
            "code": "snapchat_account_error_envelope_invalid",
            "message": "Snapchat account error metadata is malformed.",
            "retryable": True,
        })
    item_error_count = _strict_nonnegative_int(item.get("errors_count", 0))
    if item_error_count is None:
        errors.append({
            "kind": "account_hour_performance",
            "code": "snapchat_account_error_count_invalid",
            "message": "Snapchat account error metadata is malformed.",
            "retryable": True,
        })
    elif item_error_count > 0 and not errors:
        errors.append({
            "kind": "account_hour_performance",
            "code": "snapchat_account_stats_partial",
            "message": "Snapchat account stats response was partial.",
            "retryable": True,
        })
    if not _snapchat_coverage_complete(item.get("coverage")):
        errors.append({
            "kind": "account_hour_performance",
            "code": "snapchat_account_coverage_incomplete",
            "message": "Snapchat account stats coverage was not proven complete.",
            "retryable": True,
        })
    return errors


def _snapchat_item_complete(item: dict[str, Any]) -> bool:
    return not _snapchat_item_errors(item)


def _snapchat_financial_item_complete(item: dict[str, Any]) -> bool:
    return not _snapchat_financial_item_errors(item)


def _snapchat_financial_run_coverage(
    items: list[dict[str, Any]],
    *,
    accounts_expected: int,
) -> dict[str, Any]:
    coverages = [
        item["coverage"]
        for item in items
        if isinstance(item.get("coverage"), dict)
    ]
    expected_requests = sum(
        _strict_nonnegative_int(value.get("expected_requests")) or 0
        for value in coverages
    )
    completed_requests = sum(
        _strict_nonnegative_int(value.get("completed_requests")) or 0
        for value in coverages
    )
    complete = (
        accounts_expected > 0
        and len(items) == accounts_expected
        and all(_snapchat_financial_item_complete(item) for item in items)
    )
    states = {str(value.get("data_state") or "") for value in coverages}
    if not complete:
        data_state = "unknown_incomplete"
    elif "confirmed_data" in states:
        data_state = "confirmed_data"
    elif "confirmed_zero" in states:
        data_state = "confirmed_zero"
    else:
        data_state = "confirmed_no_data"
    return {
        "status": "complete" if complete else "incomplete",
        "data_state": data_state,
        "expected_requests": expected_requests,
        "completed_requests": completed_requests,
    }


def _snapchat_run_coverage(
    items: list[dict[str, Any]],
    *,
    accounts_expected: int,
) -> dict[str, Any]:
    coverages: list[dict[str, Any]] = []
    current_request_coverages: list[dict[str, Any]] = []
    for item in items:
        top = item.get("coverage")
        if isinstance(top, dict):
            coverages.append(top)
            current_request_coverages.append(top)
        for key in SNAPCHAT_PERFORMANCE_RESULT_KEYS:
            nested = item.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("coverage"), dict):
                coverages.append(nested["coverage"])
                if not (
                    nested.get("skipped") is True
                    and nested.get("skip_reason") == "fresh_within_15_minutes"
                ):
                    current_request_coverages.append(nested["coverage"])
    expected_requests = sum(
        _strict_nonnegative_int(value.get("expected_requests")) or 0
        for value in current_request_coverages
    )
    completed_requests = sum(
        _strict_nonnegative_int(value.get("completed_requests")) or 0
        for value in current_request_coverages
    )
    complete = (
        accounts_expected > 0
        and len(items) == accounts_expected
        and all(_snapchat_item_complete(item) for item in items)
    )
    states = {str(value.get("data_state") or "") for value in coverages}
    if not complete:
        data_state = "unknown_incomplete"
    elif "confirmed_data" in states:
        data_state = "confirmed_data"
    elif "confirmed_zero" in states:
        data_state = "confirmed_zero"
    else:
        data_state = "confirmed_no_data"
    return {
        "status": "complete" if complete else "incomplete",
        "data_state": data_state,
        "expected_requests": expected_requests,
        "completed_requests": completed_requests,
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
        exception_type = _safe_exception_type(exc)
        logger.exception(
            "Snapchat decision outcome evaluation failed "
            "failure_stage=decision_outcomes_evaluation exception_type=%s",
            exception_type,
            exc_info=False,
        )
        return {
            "status": "deferred",
            "retryable": True,
            "error_type": exception_type,
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
                "health_score": 0,
                "checked_at": _iso(),
                "updated_at": _iso(),
            }
        },
        upsert=True,
    )


async def _mark_snapchat_sync_unhealthy(
    db: Any,
    user_id: str,
) -> None:
    """Invalidate stale green health without advancing successful freshness."""
    observed_at = _iso()
    integration_update: dict[str, Any] = {
        "$set": {
            "data_quality": "incomplete",
            "data_delay_minutes": None,
            "health_score": 70,
            "checked_at": observed_at,
            "updated_at": observed_at,
        }
    }
    account_update: dict[str, Any] = {
        "$set": {
            "data_quality": "incomplete",
            "data_delay_minutes": None,
            "health_score": 70,
            "updated_at": observed_at,
        }
    }
    await _collection(db, "mezan_integrations_v2").update_one(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
        integration_update,
        upsert=False,
    )
    await _collection(db, "mezan_integration_accounts_v2").update_many(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "mezan_selected": True,
        },
        account_update,
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
    failure_stage = SNAPCHAT_DEFAULT_FAILURE_STAGE

    def observe_failure_stage(stage: str) -> None:
        nonlocal failure_stage
        if stage in SNAPCHAT_FAILURE_STAGES:
            failure_stage = stage

    try:
        accounts = await _load_canonical_scheduler_accounts(
            db,
            user_id,
            failure_stage_observer=observe_failure_stage,
        )
        observe_failure_stage("fact_storage_prepare")
        await ensure_snapchat_native_sync_indexes(db)
        token_context = SnapchatSyncContext(db, user_id, now=_utcnow)
        token_context.failure_stage_observer = observe_failure_stage
        observe_failure_stage("credential_decrypt_or_refresh")
        access_token = await token_context.access_token()
        items: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        financial_errors_count = 0
        failed_coverages: list[dict[str, Any]] = []
        provider_calls_total = 0
        account_provider_calls: list[dict[str, Any]] = []
        observe_failure_stage("provider_refresh")
        async with httpx.AsyncClient(timeout=35.0) as client:
            for account in accounts:
                account_context = SnapchatSyncContext(db, user_id, now=_utcnow)
                account_context.failure_stage_observer = observe_failure_stage
                account_id = str(account.get("ad_account_id") or "").strip()
                try:
                    observe_failure_stage("provider_refresh")
                    item = await snapchat_hourly.refresh_snapchat_account_hours(
                        account_context,
                        client,
                        access_token,
                        account,
                        start_date=start_date,
                        end_date=end_date,
                        now=now,
                    )
                    observe_failure_stage("account_state_persist")
                    items.append(item)
                    financial_item_errors = _snapchat_financial_item_errors(item)
                    financial_errors_count += len(financial_item_errors)
                    item_errors = _snapchat_item_errors(item)
                    for item_error in item_errors:
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
                    observed_at = _iso()
                    financial_item_complete = not financial_item_errors
                    item_complete = not item_errors
                    account_patch: dict[str, Any] = {
                        "data_delay_minutes": 0 if item_complete else None,
                        "data_quality": "complete" if item_complete else "incomplete",
                        "health_score": 100 if item_complete else 70,
                        "performance_rows_saved": int(item.get("rows_saved") or 0),
                        "source_mode": ACCOUNT_REFRESH_SOURCE_MODE,
                        "coverage": item.get("coverage"),
                        "financial_data_delay_minutes": (
                            0 if financial_item_complete else None
                        ),
                        "financial_data_quality": (
                            "complete" if financial_item_complete else "incomplete"
                        ),
                        "financial_source_mode": ACCOUNT_REFRESH_SOURCE_MODE,
                        "financial_coverage": item.get("coverage"),
                    }
                    if item_complete:
                        account_patch.update({
                            "last_sync_at": observed_at,
                            "last_observed_at": observed_at,
                        })
                    if financial_item_complete:
                        account_patch.update({
                            "financial_last_sync_at": observed_at,
                            "financial_last_observed_at": observed_at,
                        })
                    account_update = await _collection(
                        db, "mezan_integration_accounts_v2"
                    ).update_one(
                        {
                            "user_id": user_id,
                            "provider": SNAPCHAT_PROVIDER_ID,
                            "connection_status": "connected",
                            "mezan_selected": True,
                            "$or": [
                                {"external_account_id": account_id},
                                {"ad_account_id": account_id},
                            ],
                        },
                        {"$set": account_patch},
                    )
                    if getattr(account_update, "matched_count", 0) != 1:
                        state_error = {
                            "code": "snapchat_account_state_changed",
                            "message": (
                                "Selected Snapchat account state changed during "
                                "the refresh."
                            ),
                            "retryable": True,
                        }
                        existing_errors = [
                            dict(error)
                            for error in item.get("errors", [])
                            if isinstance(error, dict)
                        ] if isinstance(item.get("errors", []), list) else []
                        item["errors"] = [*existing_errors, state_error]
                        item["errors_count"] = len(item["errors"])
                        item["coverage"] = {
                            **(
                                item.get("coverage")
                                if isinstance(item.get("coverage"), dict)
                                else {}
                            ),
                            "status": "incomplete",
                            "data_state": "unknown_incomplete",
                        }
                        error_id = await _record_error(
                            db,
                            user_id=user_id,
                            provider=SNAPCHAT_PROVIDER_ID,
                            run_id=run_id,
                            source_mode=ACCOUNT_REFRESH_SOURCE_MODE,
                            code=state_error["code"],
                            message=f"account={account_id}: {state_error['message']}",
                            retryable=True,
                        )
                        errors.append({
                            "error_id": error_id,
                            "ad_account_id": account_id,
                            **state_error,
                        })
                        financial_errors_count += 1
                except SnapchatNativeSyncError as exc:
                    if exc.code == "snapchat_needs_reauth":
                        raise
                    failed_coverage = (
                        exc.result.get("coverage")
                        if isinstance(exc.result, dict)
                        and isinstance(exc.result.get("coverage"), dict)
                        else {
                            "status": "incomplete",
                            "data_state": "unknown_incomplete",
                            "expected_requests": 1,
                            "completed_requests": 0,
                        }
                    )
                    failed_coverages.append(failed_coverage)
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
                    financial_errors_count += 1
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
                                "data_delay_minutes": None,
                                "data_quality": "incomplete",
                                "health_score": 70,
                                "source_mode": ACCOUNT_REFRESH_SOURCE_MODE,
                                "coverage": failed_coverage,
                                "financial_data_delay_minutes": None,
                                "financial_data_quality": "incomplete",
                                "financial_source_mode": ACCOUNT_REFRESH_SOURCE_MODE,
                                "financial_coverage": failed_coverage,
                            }
                        },
                    )
                finally:
                    provider_calls_total += int(account_context.provider_calls)
                    account_provider_calls.append({
                        "ad_account_id": account_id,
                        "provider_calls": int(account_context.provider_calls),
                    })
        observe_failure_stage("coverage_aggregation")
        rows_saved = sum(int(item.get("rows_saved") or 0) for item in items)
        campaign_rows_saved = sum(
            int(item.get("campaign_rows_saved") or 0) for item in items
        )
        complete = sum(_snapchat_item_complete(item) for item in items)
        financial_complete = sum(
            _snapchat_financial_item_complete(item) for item in items
        )
        coverage = _snapchat_run_coverage(
            items,
            accounts_expected=len(accounts),
        )
        coverage["expected_requests"] += sum(
            _strict_nonnegative_int(item.get("expected_requests")) or 0
            for item in failed_coverages
        )
        coverage["completed_requests"] += sum(
            _strict_nonnegative_int(item.get("completed_requests")) or 0
            for item in failed_coverages
        )
        financial_coverage = _snapchat_financial_run_coverage(
            items,
            accounts_expected=len(accounts),
        )
        financial_coverage["expected_requests"] += sum(
            _strict_nonnegative_int(item.get("expected_requests")) or 0
            for item in failed_coverages
        )
        financial_coverage["completed_requests"] += sum(
            _strict_nonnegative_int(item.get("completed_requests")) or 0
            for item in failed_coverages
        )
        financial_status = (
            "complete"
            if financial_errors_count == 0
            and financial_complete == len(accounts)
            and financial_coverage["status"] == "complete"
            else "partial"
        )
        status = (
            "complete"
            if not errors
            and complete == len(accounts)
            and coverage["status"] == "complete"
            else "partial"
        )
        campaign_facts_complete = bool(accounts) and len(items) == len(accounts) and all(
            _snapchat_item_complete(item)
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
            "coverage": coverage,
            "financial_proof": {
                "version": 1,
                "status": financial_status,
                "accounts_complete": financial_complete,
                "errors_count": financial_errors_count,
                "coverage": financial_coverage,
            },
            "decision_outcomes": decision_outcomes,
        }
        observe_failure_stage("integration_state_persist")
        integration_observed_at = _iso()
        integration_patch: dict[str, Any] = {
            "checked_at": integration_observed_at,
            "updated_at": integration_observed_at,
            "data_delay_minutes": 0 if status == "complete" else None,
            "data_quality": "complete" if status == "complete" else "incomplete",
            "health_score": 100 if status == "complete" else 70,
            "source_mode": ACCOUNT_REFRESH_SOURCE_MODE,
            "coverage": coverage,
            "financial_data_delay_minutes": (
                0 if financial_status == "complete" else None
            ),
            "financial_data_quality": (
                "complete" if financial_status == "complete" else "incomplete"
            ),
            "financial_source_mode": ACCOUNT_REFRESH_SOURCE_MODE,
            "financial_coverage": financial_coverage,
            "projection_data_quality": (
                "complete" if status == "complete" else "incomplete"
            ),
            "projection_coverage": coverage,
        }
        if status == "complete":
            integration_patch["last_sync_at"] = integration_observed_at
        if financial_status == "complete":
            integration_patch["financial_last_sync_at"] = integration_observed_at
        integration_update = await _collection(db, "mezan_integrations_v2").update_one(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "connection_status": "connected",
            },
            {"$set": integration_patch},
            upsert=False,
        )
        if getattr(integration_update, "matched_count", 0) != 1:
            state_error = {
                "code": "snapchat_integration_state_changed",
                "message": "Snapchat integration state changed during the refresh.",
                "retryable": True,
            }
            try:
                error_id = await _record_error(
                    db,
                    user_id=user_id,
                    provider=SNAPCHAT_PROVIDER_ID,
                    run_id=run_id,
                    source_mode=ACCOUNT_REFRESH_SOURCE_MODE,
                    code=state_error["code"],
                    message=state_error["message"],
                    retryable=True,
                )
            except Exception as persist_exc:  # noqa: BLE001
                logger.exception(
                    "Failed to persist Snapchat state race error "
                    "failure_stage=%s exception_type=%s",
                    failure_stage,
                    _safe_exception_type(persist_exc),
                    exc_info=False,
                )
                error_id = None
            errors.append({"error_id": error_id, **state_error})
            financial_errors_count += 1
            financial_status = "partial"
            status = "partial"
            coverage = {
                **coverage,
                "status": "incomplete",
                "data_state": "unknown_incomplete",
            }
            financial_coverage = {
                **financial_coverage,
                "status": "incomplete",
                "data_state": "unknown_incomplete",
            }
            result.update({
                "status": status,
                "errors_count": len(errors),
                "error_samples": errors[:10],
                "coverage": coverage,
                "financial_proof": {
                    "version": 1,
                    "status": financial_status,
                    "accounts_complete": financial_complete,
                    "errors_count": financial_errors_count,
                    "coverage": financial_coverage,
                },
            })
            await _mark_snapchat_sync_unhealthy(db, user_id)
        observe_failure_stage("run_finalize")
        await _finish_run(
            db, user_id=user_id, run_id=run_id, status=status, result=result
        )
        if status == "complete":
            observe_failure_stage("decision_outcomes_evaluation")
            decision_outcomes = await _evaluate_snapchat_outcomes_after_sync(
                db,
                user_id,
                now=now,
            )
            result["decision_outcomes"] = decision_outcomes
        return {"run_id": run_id, **result}
    except SnapchatNativeSyncError as exc:
        failure_result = {
            **(exc.result if isinstance(exc.result, dict) else {}),
            "provider": SNAPCHAT_PROVIDER_ID,
            "status": "failed",
            "date_from": start_date.isoformat(),
            "date_to": end_date.isoformat(),
        }
        error_id: str | None = None
        try:
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
        except Exception as persist_exc:  # noqa: BLE001
            logger.exception(
                "Failed to persist controlled Snapchat sync error "
                "failure_stage=%s exception_type=%s",
                failure_stage,
                _safe_exception_type(persist_exc),
                exc_info=False,
            )
        await _finish_run(
            db,
            user_id=user_id,
            run_id=run_id,
            status="failed",
            result=failure_result,
            error={
                "error_id": error_id,
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            },
        )
        await _mark_snapchat_sync_unhealthy(db, user_id)
        needs_reauth = bool(
            exc.code == "snapchat_needs_reauth"
            or (
                isinstance(exc.result, dict)
                and exc.result.get("needs_reauth") is True
            )
        )
        if needs_reauth:
            await _mark_needs_reauth(db, user_id, SNAPCHAT_PROVIDER_ID)
        return {"provider": SNAPCHAT_PROVIDER_ID, "run_id": run_id, "status": "failed", "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        code = "snapchat_scheduler_runtime_error"
        message = "Snapchat scheduler refresh failed unexpectedly."
        failed_stage = failure_stage
        exception_type = _safe_exception_type(exc)
        failure_location = _safe_failure_location(exc)
        logger.exception(
            "Canonical Snapchat scheduler failure "
            "failure_stage=%s exception_type=%s",
            failed_stage,
            exception_type,
            exc_info=False,
        )
        error_id: str | None = None
        try:
            error_id = await _record_error(
                db,
                user_id=user_id,
                provider=SNAPCHAT_PROVIDER_ID,
                run_id=run_id,
                source_mode=ACCOUNT_REFRESH_SOURCE_MODE,
                code=code,
                message=message,
                retryable=True,
            )
        except Exception as persist_exc:  # noqa: BLE001
            logger.exception(
                "Failed to persist canonical Snapchat scheduler error "
                "failure_stage=%s exception_type=%s",
                failed_stage,
                _safe_exception_type(persist_exc),
                exc_info=False,
            )
        failure_result = {
            "provider": SNAPCHAT_PROVIDER_ID,
            "status": "failed",
            "date_from": start_date.isoformat(),
            "date_to": end_date.isoformat(),
            "accounts_attempted": 0,
            "accounts_complete": 0,
            "rows_saved": 0,
            "errors_count": 1,
            "provider_calls": 0,
            "account_provider_calls": [],
            "error_samples": [{"code": code, "message": message}],
            "coverage": {
                "status": "incomplete",
                "data_state": "unknown_incomplete",
                "expected_requests": 1,
                "completed_requests": 0,
            },
        }
        await _finish_run(
            db,
            user_id=user_id,
            run_id=run_id,
            status="failed",
            result=failure_result,
            error={
                "error_id": error_id,
                "code": code,
                "message": message,
                "retryable": True,
                "failure_stage": failed_stage,
                "exception_type": exception_type,
                "run_id": run_id,
                **failure_location,
            },
        )
        await _mark_snapchat_sync_unhealthy(db, user_id)
        return {
            "provider": SNAPCHAT_PROVIDER_ID,
            "run_id": run_id,
            "status": "failed",
            "code": code,
        }


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
            # Meta/Snap Campaign AI uses an inclusive three-day decision
            # window.  Extend only those two analytical refreshes so every
            # decision day has current provider proof; TikTok/Google and the
            # scheduler's existing global cadence remain unchanged.
            provider_start_date = (
                min(
                    start_date,
                    end_date
                    - timedelta(days=CAMPAIGN_AI_EXECUTION_PROOF_DAYS - 1),
                )
                if provider in {META_PROVIDER_ID, SNAPCHAT_PROVIDER_ID}
                else start_date
            )
            if provider == META_PROVIDER_ID:
                return await _refresh_meta(
                    db,
                    user_id=user_id,
                    start_date=provider_start_date,
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
                start_date=provider_start_date,
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


def _safe_status_identifier(value: Any, *, limit: int = 120) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > limit or not normalized.isascii():
        return None
    if not all(
        character.isalnum() or character in {"_", "-", ".", ":"}
        for character in normalized
    ):
        return None
    return normalized


def _safe_status_timestamp(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    return _iso(parsed) if parsed is not None else None


def _safe_provider_run_status(run: dict[str, Any]) -> dict[str, Any]:
    provider = str(run.get("provider") or "")
    if provider not in {
        META_PROVIDER_ID,
        SNAPCHAT_PROVIDER_ID,
        TIKTOK_PROVIDER_ID,
        GOOGLE_ADS_PROVIDER_ID,
    }:
        return {}
    run_id = _safe_status_identifier(run.get("run_id"))
    status = str(run.get("status") or "")
    if status not in {*ACTIVE_STATUSES, *TERMINAL_STATUSES}:
        status = "unknown"
    safe_run: dict[str, Any] = {
        "provider": provider,
        "run_id": run_id,
        "status": status,
        "started_at": _safe_status_timestamp(run.get("started_at")),
        "finished_at": _safe_status_timestamp(run.get("finished_at")),
    }
    run_type = _safe_status_identifier(run.get("run_type"))
    if run_type:
        safe_run["run_type"] = run_type
    source_mode = run.get("source_mode")
    if source_mode in {
        META_REPORTING_SOURCE_MODE,
        ACCOUNT_REFRESH_SOURCE_MODE,
        TIKTOK_REPORTING_SOURCE_MODE,
        GOOGLE_ADS_REPORTING_SOURCE_MODE,
    }:
        safe_run["source_mode"] = source_mode

    summary = run.get("summary")
    coverage = summary.get("coverage") if isinstance(summary, dict) else None
    if isinstance(coverage, dict):
        coverage_status = str(coverage.get("status") or "")
        data_state = str(coverage.get("data_state") or "")
        expected_requests = _strict_nonnegative_int(
            coverage.get("expected_requests")
        )
        completed_requests = _strict_nonnegative_int(
            coverage.get("completed_requests")
        )
        if (
            coverage_status in {"complete", "incomplete"}
            and data_state in {
                "confirmed_data",
                "confirmed_zero",
                "confirmed_no_data",
                "unknown_incomplete",
            }
            and expected_requests is not None
            and completed_requests is not None
        ):
            safe_run["summary"] = {
                "coverage": {
                    "status": coverage_status,
                    "data_state": data_state,
                    "expected_requests": expected_requests,
                    "completed_requests": completed_requests,
                }
            }

    error = run.get("error")
    if isinstance(error, dict):
        safe_error: dict[str, Any] = {}
        code = _safe_status_identifier(error.get("code"))
        if code:
            safe_error["code"] = code
        if isinstance(error.get("retryable"), bool):
            safe_error["retryable"] = error["retryable"]
        failure_stage = str(error.get("failure_stage") or "")
        if failure_stage in SNAPCHAT_FAILURE_STAGES:
            safe_error["failure_stage"] = failure_stage
        exception_type = _safe_ascii_identifier(
            error.get("exception_type"),
            limit=80,
        )
        if exception_type:
            safe_error["exception_type"] = exception_type
        if run_id and error.get("run_id") == run_id:
            safe_error["run_id"] = run_id
            safe_error.update(_safe_failure_location_values(
                error.get("failure_module"),
                error.get("failure_function"),
                error.get("failure_line"),
            ))
        if safe_error:
            safe_run["error"] = safe_error
    return safe_run


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
        {
            "_id": 0,
            "provider": 1,
            "run_id": 1,
            "run_type": 1,
            "status": 1,
            "started_at": 1,
            "finished_at": 1,
            "source_mode": 1,
            "summary.coverage": 1,
            "error.code": 1,
            "error.retryable": 1,
            "error.failure_stage": 1,
            "error.exception_type": 1,
            "error.run_id": 1,
            "error.failure_module": 1,
            "error.failure_function": 1,
            "error.failure_line": 1,
        },
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("started_at", -1)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(20)
    latest: dict[str, dict[str, Any]] = {}
    for run in await _to_list(cursor, 20):
        provider = str(run.get("provider") or "")
        if provider and provider not in latest:
            safe_run = _safe_provider_run_status(run)
            if safe_run:
                latest[provider] = safe_run
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

