"""Timeout-safe preparation jobs for governed Snapchat proposal previews.

Preparing a proposal captures a large, immutable decision baseline and may
perform provider reads.  None of those steps writes to Snapchat, but keeping
them in the request/response path can exceed an edge proxy timeout.  This
module moves only that read-only preparation behind a durable 202 + poll job.
The existing proposal lifecycle remains the sole approval/execution path.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pymongo.errors import DuplicateKeyError

from .snapchat_campaign_management import (
    PROPOSAL_COLLECTION,
    SnapchatManagementProposalInput,
    create_snapchat_management_proposal,
    snapchat_management_request_fingerprint,
)
from .snapchat_native_data_common import (
    SNAPCHAT_PROVIDER_ID,
    _collection,
)


PREVIEW_JOB_COLLECTION = "mezan_snapchat_campaign_preview_jobs_v1"
PREVIEW_JOB_SOURCE_MODE = "snapchat_campaign_preview_async_v1"
PREVIEW_JOB_STALE_AFTER = timedelta(minutes=15)
ACTIVE_PREVIEW_JOB_STATUSES = {"queued", "running"}


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


def _bounded_text(value: Any, maximum: int = 500) -> str | None:
    text = " ".join(str(value or "").split()).strip()
    return text[:maximum] if text else None


def _safe_failure(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
        return {
            "code": _bounded_text(
                exc.detail.get("code"), 160
            ) or "snapchat_management_preview_failed",
            "message": _bounded_text(exc.detail.get("message")),
            "retryable": exc.detail.get("retryable") is True,
        }
    return {
        "code": "snapchat_management_preview_worker_failed",
        "message": "تعذر تجهيز معاينة Snapchat في الخلفية.",
        "error_type": type(exc).__name__[:160],
        "retryable": False,
    }


def _safe_job(row: dict[str, Any]) -> dict[str, Any]:
    failure = row.get("failure") if isinstance(row.get("failure"), dict) else None
    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "preview_job_id": row.get("preview_job_id"),
        "status": row.get("status") or "unknown",
        "proposal_id": row.get("proposal_id"),
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "failure": failure,
        "provider_write_reached": False,
        "provider_write_state": "not_attempted",
        "provider_write_uncertain": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def ensure_snapchat_preview_job_indexes(db: Any) -> None:
    jobs = _collection(db, PREVIEW_JOB_COLLECTION)
    await jobs.create_index(
        [("user_id", 1), ("preview_job_id", 1)],
        unique=True,
        name="snap_management_preview_job_unique",
    )
    await jobs.create_index(
        [("user_id", 1), ("idempotency_key", 1)],
        unique=True,
        name="snap_management_preview_idempotency_unique",
    )
    await jobs.create_index(
        [("user_id", 1), ("status", 1), ("created_at", -1)],
        name="snap_management_preview_status_latest",
    )


async def _proposal_for_job(db: Any, row: dict[str, Any]) -> dict[str, Any] | None:
    return await _collection(db, PROPOSAL_COLLECTION).find_one(
        {
            "user_id": row.get("user_id"),
            "idempotency_key": row.get("idempotency_key"),
            "request_fingerprint": row.get("request_fingerprint"),
        },
        {"_id": 0, "proposal_id": 1, "status": 1},
    )


async def _reconcile_ready_job(db: Any, row: dict[str, Any]) -> dict[str, Any]:
    proposal = await _proposal_for_job(db, row)
    if not proposal:
        return row
    await _collection(db, PREVIEW_JOB_COLLECTION).update_one(
        {
            "user_id": row.get("user_id"),
            "preview_job_id": row.get("preview_job_id"),
            "status": {"$in": list(ACTIVE_PREVIEW_JOB_STATUSES | {"failed"})},
        },
        {
            "$set": {
                "status": "ready",
                "proposal_id": proposal.get("proposal_id"),
                "finished_at": row.get("finished_at") or _iso(),
                "failure": None,
            }
        },
    )
    return (
        await _collection(db, PREVIEW_JOB_COLLECTION).find_one(
            {
                "user_id": row.get("user_id"),
                "preview_job_id": row.get("preview_job_id"),
            },
            {"_id": 0},
        )
        or row
    )


async def queue_snapchat_management_preview_job(
    db: Any,
    user_id: str,
    actor_id: str,
    payload: SnapchatManagementProposalInput,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> tuple[dict[str, Any], bool]:
    """Persist one bounded job; return whether a new worker must be scheduled."""
    await ensure_snapchat_preview_job_indexes(db)
    fingerprint = snapchat_management_request_fingerprint(payload)
    jobs = _collection(db, PREVIEW_JOB_COLLECTION)
    existing = await jobs.find_one(
        {"user_id": user_id, "idempotency_key": payload.idempotency_key},
        {"_id": 0},
    )
    if existing:
        if existing.get("request_fingerprint") != fingerprint:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "snapchat_management_idempotency_conflict",
                    "message": "مفتاح التكرار مستخدم لطلب مختلف؛ أنشئ مفتاحًا جديدًا.",
                },
            )
        existing = await _reconcile_ready_job(db, existing)
        return _safe_job(existing), False

    now_value = now().astimezone(timezone.utc)
    row = {
        "preview_job_id": str(uuid.uuid4()),
        "user_id": user_id,
        "actor_id": actor_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "status": "queued",
        "idempotency_key": payload.idempotency_key,
        "request_fingerprint": fingerprint,
        "request": payload.model_dump(mode="json"),
        "proposal_id": None,
        "created_at": _iso(now_value),
        "started_at": None,
        "finished_at": None,
        "stale_at": _iso(now_value + PREVIEW_JOB_STALE_AFTER),
        "failure": None,
        "provider_write_reached": False,
        "provider_write_state": "not_attempted",
        "provider_write_uncertain": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
        "automatic_retry_allowed": False,
        "source_mode": PREVIEW_JOB_SOURCE_MODE,
    }
    try:
        await jobs.insert_one(row)
    except DuplicateKeyError:
        existing = await jobs.find_one(
            {"user_id": user_id, "idempotency_key": payload.idempotency_key},
            {"_id": 0},
        )
        if not existing or existing.get("request_fingerprint") != fingerprint:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_idempotency_conflict"},
            )
        return _safe_job(await _reconcile_ready_job(db, existing)), False
    return _safe_job(row), True


async def _mark_job_failed(
    db: Any,
    row: dict[str, Any],
    failure: dict[str, Any],
) -> None:
    reconciled = await _reconcile_ready_job(db, row)
    if reconciled.get("status") == "ready":
        return
    await _collection(db, PREVIEW_JOB_COLLECTION).update_one(
        {
            "user_id": row.get("user_id"),
            "preview_job_id": row.get("preview_job_id"),
            "status": {"$in": list(ACTIVE_PREVIEW_JOB_STATUSES)},
        },
        {
            "$set": {
                "status": "failed",
                "finished_at": _iso(),
                "failure": failure,
                "provider_write_reached": False,
                "provider_write_state": "not_attempted",
                "provider_write_uncertain": False,
                "automatic_retry_allowed": False,
                "recovery_action": "create_new_preview",
            }
        },
    )


async def execute_snapchat_management_preview_job(
    db: Any,
    user_id: str,
    actor_id: str,
    preview_job_id: str,
) -> None:
    """Run the existing read-only proposal preparation after the 202 response."""
    jobs = _collection(db, PREVIEW_JOB_COLLECTION)
    claimed = await jobs.update_one(
        {
            "user_id": user_id,
            "preview_job_id": preview_job_id,
            "status": "queued",
        },
        {"$set": {"status": "running", "started_at": _iso()}},
    )
    if int(getattr(claimed, "matched_count", 0) or 0) != 1:
        return
    row = await jobs.find_one(
        {"user_id": user_id, "preview_job_id": preview_job_id}, {"_id": 0}
    )
    if not row:
        return
    try:
        payload = SnapchatManagementProposalInput(**dict(row.get("request") or {}))
        proposal = await create_snapchat_management_proposal(
            db, user_id, actor_id, payload
        )
        await jobs.update_one(
            {
                "user_id": user_id,
                "preview_job_id": preview_job_id,
                "status": "running",
            },
            {
                "$set": {
                    "status": "ready",
                    "proposal_id": proposal.get("proposal_id"),
                    "finished_at": _iso(),
                    "failure": None,
                }
            },
        )
    except asyncio.CancelledError as exc:
        await _mark_job_failed(
            db,
            row,
            {
                "code": "snapchat_management_preview_worker_cancelled",
                "message": "توقف عامل المعاينة قبل اكتمالها؛ أنشئ معاينة جديدة.",
                "retryable": False,
            },
        )
        raise exc
    except Exception as exc:  # noqa: BLE001 - converted to a bounded job error
        await _mark_job_failed(db, row, _safe_failure(exc))


async def get_snapchat_management_preview_job(
    db: Any,
    user_id: str,
    preview_job_id: str,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    row = await _collection(db, PREVIEW_JOB_COLLECTION).find_one(
        {"user_id": user_id, "preview_job_id": preview_job_id}, {"_id": 0}
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"code": "snapchat_management_preview_job_not_found"},
        )
    row = await _reconcile_ready_job(db, row)
    stale_at = _parse_datetime(row.get("stale_at"))
    if (
        row.get("status") in ACTIVE_PREVIEW_JOB_STATUSES
        and stale_at is not None
        and stale_at <= now().astimezone(timezone.utc)
    ):
        await _mark_job_failed(
            db,
            row,
            {
                "code": "snapchat_management_preview_job_stale",
                "message": "لم تكتمل المعاينة ضمن مهلة الأمان؛ أنشئ معاينة جديدة.",
                "retryable": False,
            },
        )
        row = await _collection(db, PREVIEW_JOB_COLLECTION).find_one(
            {"user_id": user_id, "preview_job_id": preview_job_id}, {"_id": 0}
        ) or row
    return _safe_job(row)


def attach_snapchat_campaign_preview_async_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.post(
        f"/{SNAPCHAT_PROVIDER_ID}/management/preview-jobs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_preview_job(
        payload: SnapchatManagementProposalInput,
        background_tasks: BackgroundTasks,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        job, created = await queue_snapchat_management_preview_job(
            db, user_id, user_id, payload
        )
        if created:
            background_tasks.add_task(
                execute_snapchat_management_preview_job,
                db,
                user_id,
                user_id,
                str(job["preview_job_id"]),
            )
        return job

    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/management/preview-jobs/{{preview_job_id}}"
    )
    async def read_preview_job(
        preview_job_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await get_snapchat_management_preview_job(
            db, str(owner["id"]), preview_job_id
        )


__all__ = [
    "PREVIEW_JOB_COLLECTION",
    "attach_snapchat_campaign_preview_async_routes",
    "ensure_snapchat_preview_job_indexes",
    "execute_snapchat_management_preview_job",
    "get_snapchat_management_preview_job",
    "queue_snapchat_management_preview_job",
]
