"""Owner-only timeout-safe TikTok reporting routes for Integrations V2."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from .tiktok_native_reporting import (
    TIKTOK_REPORTING_SOURCE_MODE,
    TikTokReportingError,
    TikTokReportingSyncInput,
    run_tiktok_reporting_sync,
    tiktok_reporting_enabled,
)
from .tiktok_oauth_security import (
    TIKTOK_PROVIDER_ID,
    tiktok_oauth_configured,
)

TIKTOK_REPORTING_RUN_TYPE = "tiktok_reporting_async"
TIKTOK_REPORTING_JOB_STALE_AFTER = timedelta(minutes=20)


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


def _safe_result(run: dict[str, Any]) -> dict[str, Any]:
    summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
    result = {
        "run_id": str(run.get("run_id") or ""),
        "provider": TIKTOK_PROVIDER_ID,
        "status": str(run.get("status") or "unknown"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "date_from": summary.get("date_from") or summary.get("requested_from"),
        "date_to": summary.get("date_to") or summary.get("requested_to"),
        "accounts_attempted": int(summary.get("accounts_attempted") or 0),
        "accounts_complete": int(summary.get("accounts_complete") or 0),
        "rows_saved": int(summary.get("rows_saved") or 0),
        "errors_count": int(summary.get("errors_count") or 0),
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }
    error = run.get("error")
    if isinstance(error, dict) and error:
        result["error"] = {
            "code": str(error.get("code") or "tiktok_reporting_failed"),
            "message": str(
                error.get("message")
                or "TikTok reporting did not complete."
            )[:300],
            "retryable": bool(error.get("retryable")),
        }
    return result


async def _insert_error(
    db: Any,
    *,
    user_id: str,
    run_id: str,
    error: TikTokReportingError,
    occurred_at: str,
) -> str:
    error_id = str(uuid.uuid4())
    await db.mezan_integration_errors_v2.insert_one(
        {
            "error_id": error_id,
            "user_id": user_id,
            "provider": TIKTOK_PROVIDER_ID,
            "code": error.code,
            "message": error.message,
            "occurred_at": occurred_at,
            "retryable": error.retryable,
            "source_mode": TIKTOK_REPORTING_SOURCE_MODE,
            "run_id": run_id,
        }
    )
    return error_id


async def _execute_reporting_job(
    db: Any,
    user_id: str,
    run_id: str,
    payload: TikTokReportingSyncInput,
) -> None:
    runs = db.mezan_integration_sync_runs_v2
    started_at = _iso()
    await runs.update_one(
        {"user_id": user_id, "run_id": run_id, "status": "queued"},
        {"$set": {"status": "running", "started_at": started_at}},
    )
    try:
        result = await run_tiktok_reporting_sync(db, user_id, payload)
        finished_at = _iso()
        await runs.update_one(
            {"user_id": user_id, "run_id": run_id},
            {
                "$set": {
                    "status": result["status"],
                    "finished_at": finished_at,
                    "summary": {**result, "run_id": run_id},
                    "error": None,
                }
            },
        )
    except TikTokReportingError as exc:
        finished_at = _iso()
        error_id = await _insert_error(
            db,
            user_id=user_id,
            run_id=run_id,
            error=exc,
            occurred_at=finished_at,
        )
        failure = exc.result or {}
        summary = {
            "run_id": run_id,
            "provider": TIKTOK_PROVIDER_ID,
            "status": "failed",
            "requested_days": payload.days,
            "requested_from": payload.from_date,
            "requested_to": payload.to_date,
            "accounts_attempted": int(failure.get("accounts_attempted") or 0),
            "accounts_complete": int(failure.get("accounts_complete") or 0),
            "rows_saved": int(failure.get("rows_saved") or 0),
            "errors_count": int(failure.get("errors_count") or 1),
            "source_only": True,
            "provider_write_reached": False,
            "campaign_write_reached": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }
        await runs.update_one(
            {"user_id": user_id, "run_id": run_id},
            {
                "$set": {
                    "status": "failed",
                    "finished_at": finished_at,
                    "summary": summary,
                    "error": {
                        "error_id": error_id,
                        "code": exc.code,
                        "message": exc.message,
                        "retryable": exc.retryable,
                    },
                }
            },
        )
        connection_status = (
            "needs_reauth" if exc.code == "tiktok_needs_reauth" else "error"
        )
        await db.mezan_integrations_v2.update_one(
            {"user_id": user_id, "provider": TIKTOK_PROVIDER_ID},
            {
                "$set": {
                    "connection_status": connection_status,
                    "connection_provenance": "api_connection",
                    "source_mode": TIKTOK_REPORTING_SOURCE_MODE,
                    "data_quality": "unavailable",
                    "checked_at": finished_at,
                    "updated_at": finished_at,
                }
            },
            upsert=True,
        )
    except Exception:  # noqa: BLE001
        finished_at = _iso()
        fallback = TikTokReportingError(
            "tiktok_reporting_unexpected_failure",
            "TikTok reporting failed unexpectedly.",
            status_code=500,
            retryable=True,
        )
        error_id = await _insert_error(
            db,
            user_id=user_id,
            run_id=run_id,
            error=fallback,
            occurred_at=finished_at,
        )
        await runs.update_one(
            {"user_id": user_id, "run_id": run_id},
            {
                "$set": {
                    "status": "failed",
                    "finished_at": finished_at,
                    "summary": {
                        "run_id": run_id,
                        "provider": TIKTOK_PROVIDER_ID,
                        "status": "failed",
                        "rows_saved": 0,
                        "errors_count": 1,
                        "source_only": True,
                        "accounting_write_reached": False,
                        "qoyod_write_reached": False,
                    },
                    "error": {
                        "error_id": error_id,
                        "code": fallback.code,
                        "message": fallback.message,
                        "retryable": True,
                    },
                }
            },
        )


async def start_tiktok_reporting_job(
    db: Any,
    user_id: str,
    payload: TikTokReportingSyncInput,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    if not tiktok_oauth_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "tiktok_oauth_not_configured",
                "message": "إعدادات TikTok Marketing API غير مكتملة.",
            },
        )
    if not tiktok_reporting_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "tiktok_reporting_disabled",
                "message": "مزامنة TikTok المباشرة متوقفة بحارس الأمان التشغيلي.",
            },
        )

    runs = db.mezan_integration_sync_runs_v2
    active = await runs.find_one(
        {
            "user_id": user_id,
            "provider": TIKTOK_PROVIDER_ID,
            "run_type": TIKTOK_REPORTING_RUN_TYPE,
            "status": {"$in": ["queued", "running"]},
        },
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    if active:
        created_at = _parse_datetime(active.get("created_at"))
        if created_at and created_at >= _utcnow() - TIKTOK_REPORTING_JOB_STALE_AFTER:
            return _safe_result(active)
        await runs.update_one(
            {
                "user_id": user_id,
                "run_id": active.get("run_id"),
                "status": {"$in": ["queued", "running"]},
            },
            {
                "$set": {
                    "status": "failed",
                    "finished_at": _iso(),
                    "error": {"code": "stale_tiktok_reporting_job_recovered"},
                }
            },
        )

    run_id = str(uuid.uuid4())
    created_at = _iso()
    run = {
        "run_id": run_id,
        "user_id": user_id,
        "provider": TIKTOK_PROVIDER_ID,
        "run_type": TIKTOK_REPORTING_RUN_TYPE,
        "status": "queued",
        "created_at": created_at,
        "started_at": None,
        "finished_at": None,
        "source_mode": TIKTOK_REPORTING_SOURCE_MODE,
        "summary": {
            "requested_days": payload.days,
            "requested_from": payload.from_date,
            "requested_to": payload.to_date,
        },
        "error": None,
    }
    await runs.insert_one(run)
    background_tasks.add_task(
        _execute_reporting_job,
        db,
        user_id,
        run_id,
        payload,
    )
    return _safe_result(run)


def install_tiktok_reporting_actions() -> None:
    from . import service as service_module

    original_actions = service_module._actions
    if getattr(original_actions, "_mezan_tiktok_reporting_actions", False):
        return

    def wrapped_actions(definition: Any, snapshot: dict) -> dict:
        actions = original_actions(definition, snapshot)
        if definition.provider != TIKTOK_PROVIDER_ID:
            return actions
        connected = bool(
            snapshot.get("connection_status") == "connected"
            and snapshot.get("connection_provenance") == "api_connection"
            and snapshot.get("accounts")
        )
        configured = tiktok_oauth_configured()
        enabled = connected and configured and tiktok_reporting_enabled()
        if enabled:
            reason = None
        elif not connected:
            reason = "اربط TikTok Marketing API واكتشف حسابًا إعلانيًا أولًا."
        elif not configured:
            reason = "إعدادات TikTok Marketing API غير مكتملة."
        else:
            reason = "مزامنة TikTok المباشرة متوقفة بحارس الأمان التشغيلي."
        actions["sync_data"] = {
            "enabled": enabled,
            "reason": reason,
            "href": None,
        }
        return actions

    wrapped_actions._mezan_tiktok_reporting_actions = True  # type: ignore[attr-defined]
    service_module._actions = wrapped_actions


def attach_tiktok_native_reporting_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    install_tiktok_reporting_actions()

    @router.post(
        f"/{TIKTOK_PROVIDER_ID}/sync-async",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_reporting_sync(
        payload: TikTokReportingSyncInput,
        background_tasks: BackgroundTasks,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await start_tiktok_reporting_job(
            db,
            str(owner["id"]),
            payload,
            background_tasks,
        )

    @router.get(f"/{TIKTOK_PROVIDER_ID}/sync-async/{{run_id}}")
    async def reporting_sync_status(
        run_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        run = await db.mezan_integration_sync_runs_v2.find_one(
            {
                "user_id": str(owner["id"]),
                "provider": TIKTOK_PROVIDER_ID,
                "run_type": TIKTOK_REPORTING_RUN_TYPE,
                "run_id": run_id,
            },
            {"_id": 0},
        )
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "tiktok_reporting_run_not_found",
                    "message": "تعذر العثور على مهمة مزامنة TikTok.",
                },
            )
        return _safe_result(run)


__all__ = [
    "TIKTOK_REPORTING_RUN_TYPE",
    "attach_tiktok_native_reporting_routes",
    "install_tiktok_reporting_actions",
    "start_tiktok_reporting_job",
]
