"""Timeout-safe preparation jobs for governed Snapchat proposal previews.

Preparing a proposal captures a large, immutable decision baseline and may
perform provider reads.  None of those steps writes to Snapchat, but keeping
them in the request/response path can exceed an edge proxy timeout.  This
module moves only that read-only preparation behind a durable 202 + poll job.
The existing proposal lifecycle remains the sole approval/execution path.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
PREVIEW_JOB_EXECUTION_TIMEOUT_SECONDS = 150
PREVIEW_JOB_RECONCILIATION_GRACE = timedelta(seconds=150)
ACTIVE_PREVIEW_JOB_STATUSES = {"queued", "running"}
LOGGER = logging.getLogger(__name__)

# asyncio only keeps weak references to scheduled tasks.  Keep a process-local,
# strongly referenced registry until each worker finishes, while Mongo remains
# the durable source of truth and the atomic queued -> running claim remains the
# cross-process duplicate-work guard.
_PREVIEW_WORKER_TASKS: dict[tuple[int, str, str], asyncio.Task[None]] = {}


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
        "phase": row.get("phase") or "unknown",
        "phase_started_at": row.get("phase_started_at"),
        "terminal_reconciled": row.get("terminal_reconciled") is True,
        "reconcile_deadline_at": row.get("reconcile_deadline_at"),
        "recovery_action": row.get("recovery_action"),
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
                "phase": "ready",
                "phase_started_at": _iso(),
                "terminal_reconciled": True,
                "reconcile_deadline_at": None,
                "recovery_action": None,
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
    """Persist one bounded job; return whether its durable row was inserted."""
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
        "phase": "queued",
        "phase_started_at": _iso(now_value),
        "terminal_reconciled": False,
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
    *,
    terminal_reconciled: bool = True,
    recovery_action: str = "create_new_preview",
    reconcile_deadline_at: str | None = None,
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
                "recovery_action": recovery_action,
                "phase": "failed",
                "phase_started_at": _iso(),
                "terminal_reconciled": terminal_reconciled,
                "reconcile_deadline_at": reconcile_deadline_at,
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
    row: dict[str, Any] = {
        "user_id": user_id,
        "preview_job_id": preview_job_id,
    }
    try:
        async with asyncio.timeout(PREVIEW_JOB_EXECUTION_TIMEOUT_SECONDS):
            phase_at = _iso()
            claimed = await jobs.update_one(
                {
                    "user_id": user_id,
                    "preview_job_id": preview_job_id,
                    "status": "queued",
                },
                {
                    "$set": {
                        "status": "running",
                        "started_at": phase_at,
                        "phase": "claimed",
                        "phase_started_at": phase_at,
                        "terminal_reconciled": False,
                    }
                },
            )
            if int(getattr(claimed, "matched_count", 0) or 0) != 1:
                return
            loaded = await jobs.find_one(
                {"user_id": user_id, "preview_job_id": preview_job_id},
                {"_id": 0},
            )
            if not loaded:
                raise RuntimeError("preview_job_missing_after_claim")
            row = loaded
            phase_at = _iso()
            await jobs.update_one(
                {"user_id": user_id, "preview_job_id": preview_job_id},
                {
                    "$set": {
                        "phase": "request_loaded",
                        "phase_started_at": phase_at,
                    }
                },
            )
            payload = SnapchatManagementProposalInput(
                **dict(row.get("request") or {})
            )
            phase_at = _iso()
            await jobs.update_one(
                {"user_id": user_id, "preview_job_id": preview_job_id},
                {
                    "$set": {
                        "phase": "preparing_proposal",
                        "phase_started_at": phase_at,
                    }
                },
            )
            proposal = await create_snapchat_management_proposal(
                db, user_id, actor_id, payload
            )
            ready_at = _iso()
            updated = await jobs.update_one(
                {
                    "user_id": user_id,
                    "preview_job_id": preview_job_id,
                    "status": "running",
                },
                {
                    "$set": {
                        "status": "ready",
                        "proposal_id": proposal.get("proposal_id"),
                        "finished_at": ready_at,
                        "failure": None,
                        "phase": "ready",
                        "phase_started_at": ready_at,
                        "terminal_reconciled": True,
                        "reconcile_deadline_at": None,
                        "recovery_action": None,
                    }
                },
            )
            if int(getattr(updated, "matched_count", 0) or 0) != 1:
                # A lazy stale read may have marked the row failed while the
                # read-only worker was finishing.  The durable proposal is the
                # authority, so reconcile that late success back to ready.
                await _reconcile_ready_job(db, row)
    except TimeoutError:
        await _mark_job_failed(
            db,
            row,
            {
                "code": "snapchat_management_preview_worker_timeout",
                "message": "تجاوز تجهيز معاينة Snapchat مهلة الأمان؛ أنشئ معاينة جديدة.",
                "retryable": False,
            },
            terminal_reconciled=True,
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
            terminal_reconciled=True,
        )
        raise exc
    except Exception as exc:  # noqa: BLE001 - converted to a bounded job error
        await _mark_job_failed(db, row, _safe_failure(exc))


def _consume_preview_worker_result(
    key: tuple[int, str, str],
    task: asyncio.Task[None],
) -> None:
    """Release the strong reference and retrieve every terminal exception."""
    if _PREVIEW_WORKER_TASKS.get(key) is task:
        _PREVIEW_WORKER_TASKS.pop(key, None)
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except BaseException as exc:  # pragma: no cover - worker bounds normal errors
        # Do not log the exception message: an unexpected dependency failure
        # can contain provider or database details.  Retrieving it here avoids
        # an unhandled-task warning while the durable job's stale guard remains
        # the recovery source of truth if the worker could not mark it failed.
        LOGGER.error(
            "Detached Snapchat preview worker ended unexpectedly (%s)",
            type(exc).__name__,
        )


def schedule_snapchat_management_preview_job(
    db: Any,
    user_id: str,
    actor_id: str,
    preview_job_id: str,
) -> asyncio.Task[None]:
    """Schedule one truly detached worker and retain it until completion."""
    key = (id(db), user_id, preview_job_id)
    existing = _PREVIEW_WORKER_TASKS.get(key)
    if existing is not None and not existing.done():
        return existing

    task = asyncio.create_task(
        execute_snapchat_management_preview_job(
            db,
            user_id,
            actor_id,
            preview_job_id,
        ),
        name=f"snapchat-preview-{preview_job_id}",
    )
    _PREVIEW_WORKER_TASKS[key] = task
    task.add_done_callback(
        lambda completed, task_key=key: _consume_preview_worker_result(
            task_key, completed
        )
    )
    return task


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
    now_value = now().astimezone(timezone.utc)
    stale_at = _parse_datetime(row.get("stale_at"))
    if (
        row.get("status") in ACTIVE_PREVIEW_JOB_STATUSES
        and stale_at is not None
        and stale_at <= now_value
    ):
        reconcile_deadline = now_value + PREVIEW_JOB_RECONCILIATION_GRACE
        await _mark_job_failed(
            db,
            row,
            {
                "code": "snapchat_management_preview_job_stale",
                "message": (
                    "تجاوزت المعاينة مهلة المتابعة، وما زال التحقق من نتيجتها "
                    "جاريًا؛ استمر بمتابعة نفس المعاينة ولا تنشئ أخرى الآن."
                ),
                "retryable": False,
            },
            terminal_reconciled=False,
            recovery_action="continue_read_only_reconciliation",
            reconcile_deadline_at=_iso(reconcile_deadline),
        )
        row = await _collection(db, PREVIEW_JOB_COLLECTION).find_one(
            {"user_id": user_id, "preview_job_id": preview_job_id}, {"_id": 0}
        ) or row
    if row.get("status") == "failed" and row.get("terminal_reconciled") is not True:
        reconcile_deadline = _parse_datetime(row.get("reconcile_deadline_at"))
        if reconcile_deadline is None:
            # Upgrade an older non-terminal stale row safely.  Give any
            # already-running read-only worker the same bounded grace window
            # before allowing a new preview.
            reconcile_deadline = now_value + PREVIEW_JOB_RECONCILIATION_GRACE
            await _collection(db, PREVIEW_JOB_COLLECTION).update_one(
                {
                    "user_id": user_id,
                    "preview_job_id": preview_job_id,
                    "status": "failed",
                },
                {
                    "$set": {
                        "reconcile_deadline_at": _iso(reconcile_deadline),
                        "recovery_action": "continue_read_only_reconciliation",
                        "failure": {
                            "code": "snapchat_management_preview_job_stale",
                            "message": (
                                "تجاوزت المعاينة مهلة المتابعة، وما زال التحقق من "
                                "نتيجتها جاريًا؛ استمر بمتابعة نفس المعاينة ولا "
                                "تنشئ أخرى الآن."
                            ),
                            "retryable": False,
                        },
                    }
                },
            )
            row = await _collection(db, PREVIEW_JOB_COLLECTION).find_one(
                {"user_id": user_id, "preview_job_id": preview_job_id},
                {"_id": 0},
            ) or row
        elif reconcile_deadline <= now_value:
            terminal_at = _iso(now_value)
            await _collection(db, PREVIEW_JOB_COLLECTION).update_one(
                {
                    "user_id": user_id,
                    "preview_job_id": preview_job_id,
                    "status": "failed",
                },
                {
                    "$set": {
                        "terminal_reconciled": True,
                        "recovery_action": "create_new_preview",
                        "finished_at": terminal_at,
                        "phase": "failed",
                        "phase_started_at": terminal_at,
                        "failure": {
                            "code": "snapchat_management_preview_job_stale",
                            "message": (
                                "لم تكتمل المعاينة بعد انتهاء مهلة التحقق؛ "
                                "يمكنك إنشاء معاينة جديدة."
                            ),
                            "retryable": False,
                        },
                    }
                },
            )
            row = await _collection(db, PREVIEW_JOB_COLLECTION).find_one(
                {"user_id": user_id, "preview_job_id": preview_job_id},
                {"_id": 0},
            ) or row
    return _safe_job(row)


async def get_current_snapchat_management_preview_job(
    db: Any,
    user_id: str,
    idempotency_key: str,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Recover one exact tenant-owned job without exposing its stored request."""
    row = await _collection(db, PREVIEW_JOB_COLLECTION).find_one(
        {"user_id": user_id, "idempotency_key": idempotency_key},
        {"_id": 0},
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"code": "snapchat_management_preview_job_not_found"},
        )
    return await get_snapchat_management_preview_job(
        db,
        user_id,
        str(row.get("preview_job_id") or ""),
        now=now,
    )


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
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        job, _created = await queue_snapchat_management_preview_job(
            db, user_id, user_id, payload
        )
        # A replay may be recovering an insertion whose original process died
        # before scheduling.  Always schedule a still-queued durable job; the
        # registry de-duplicates locally and Mongo's queued -> running claim
        # de-duplicates across replicas.
        if job.get("status") == "queued":
            schedule_snapchat_management_preview_job(
                db,
                user_id,
                user_id,
                str(job["preview_job_id"]),
            )
        return job

    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/management/preview-jobs/current"
    )
    async def read_current_preview_job(
        idempotency_key: str = Query(min_length=8, max_length=128),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await get_current_snapchat_management_preview_job(
            db,
            str(owner["id"]),
            idempotency_key,
        )

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
    "get_current_snapchat_management_preview_job",
    "get_snapchat_management_preview_job",
    "queue_snapchat_management_preview_job",
    "schedule_snapchat_management_preview_job",
]
