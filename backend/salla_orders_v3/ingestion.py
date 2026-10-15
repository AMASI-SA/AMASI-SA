"""Durable, idempotent capture for verified Salla order events in V3 shadow."""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .config import (
    EVENT_OUTBOX_BACKOFF_BASE_SECONDS,
    EVENT_OUTBOX_BACKOFF_MAX_SECONDS,
    EVENT_OUTBOX_ROW_LEASE_SECONDS,
    EVENTS_COLLECTION,
    JOBS_COLLECTION,
    MAX_EVENT_OUTBOX_ATTEMPTS,
    MAX_EVENT_OUTBOX_REPAIRS_PER_CYCLE,
    TTL_SECONDS,
    shadow_collection,
)

ORDER_EVENTS = {"order.created", "order.updated", "order.status.updated"}
_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed"})
_MAX_CAS_ATTEMPTS = 5
_SIGNAL_FIELDS = (
    "provider_revision",
    "provider_updated_at",
    "event_created_at",
    "payload_digest",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_outbox_lease_token() -> str:
    return str(uuid.uuid4())


class _OutboxLeaseLost(RuntimeError):
    pass


def _session_kwargs(session: Any) -> dict[str, Any]:
    return {} if session is None else {"session": session}


async def _run_outbox_transaction(
    db: Any,
    operation: Callable[[Any, Any], Awaitable[str]],
) -> str:
    """Run cross-collection materialization atomically or fail closed."""
    test_runner = getattr(
        type(db),
        "_salla_orders_v3_transaction_runner",
        None,
    )
    if callable(test_runner):
        return await test_runner(db, operation)

    client = getattr(db, "client", None)
    start_session = getattr(client, "start_session", None)
    if not callable(start_session):
        raise RuntimeError(
            "Salla event outbox requires Mongo transaction support"
        )
    async with await start_session() as session:
        async def transact(active_session: Any) -> str:
            return await operation(db, active_session)

        return await session.with_transaction(transact)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _utc_iso(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("date") or value.get("value")
    if isinstance(value, datetime):
        parsed = value
    elif value not in (None, ""):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _numeric_revision(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if str(parsed) == str(value).strip() else None


def _payload_digest(light_order: dict[str, Any]) -> str:
    canonical = json.dumps(
        light_order,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _signal(light_order: dict[str, Any], event_created_at: Any) -> dict[str, Any]:
    revision_value = light_order.get("revision")
    if revision_value is None:
        revision_value = light_order.get("version")
    return {
        "provider_revision": _numeric_revision(revision_value),
        "provider_updated_at": _utc_iso(
            light_order.get("updated_at") or light_order.get("modified_at")
        ),
        "event_created_at": _utc_iso(event_created_at),
        # Used only as a deterministic final tie-breaker when every provider
        # clock is identical. It never outranks revision or timestamps.
        "payload_digest": _payload_digest(light_order),
    }


def _signal_order_key(signal: dict[str, Any]) -> tuple[int, int, str, str, str]:
    """Return one total, transitive order for an atomic provider signal."""
    revision = signal.get("provider_revision")
    has_revision = type(revision) is int
    return (
        int(has_revision),
        int(revision) if has_revision else 0,
        str(signal.get("provider_updated_at") or ""),
        str(signal.get("event_created_at") or ""),
        str(signal.get("payload_digest") or ""),
    )


def _stored_signal(existing: dict[str, Any]) -> dict[str, Any]:
    recorded = existing.get("light_order_signal")
    if isinstance(recorded, dict):
        return {field: recorded.get(field) for field in _SIGNAL_FIELDS}
    return {
        "provider_revision": existing.get("provider_revision"),
        "provider_updated_at": existing.get("provider_updated_at"),
        "event_created_at": existing.get("event_created_at"),
        "payload_digest": (
            existing.get("payload_digest")
            or _payload_digest(existing.get("light_order") or {})
        ),
    }


def _has_newer_signal(incoming: dict[str, Any], existing: dict[str, Any]) -> bool:
    return _signal_order_key(incoming) > _signal_order_key(_stored_signal(existing))


def _order_payload(event_body: dict[str, Any]) -> dict[str, Any]:
    data = event_body.get("data")
    data = data if isinstance(data, dict) else {}
    nested = data.get("order")
    if isinstance(nested, dict):
        merged = deepcopy(nested)
        for key, value in data.items():
            if key != "order" and key not in merged:
                merged[key] = deepcopy(value)
        return merged
    return deepcopy(data)


def _event_id(
    *,
    user_id: str,
    store_id: str,
    event_body: dict[str, Any],
) -> str:
    provider_id = _text(
        event_body.get("id")
        or event_body.get("event_id")
        or event_body.get("webhook_id")
    )
    if provider_id:
        suffix = provider_id
    else:
        canonical = json.dumps(
            event_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        suffix = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_text(user_id)}:{_text(store_id)}:{suffix}"


async def capture_verified_order_event(
    db: Any,
    *,
    user_id: str,
    store_id: str,
    event_body: dict[str, Any],
) -> dict[str, Any]:
    """Capture one already-verified event and enqueue shadow enrichment once."""
    event_name = _text(event_body.get("event"))
    if event_name not in ORDER_EVENTS:
        return {"accepted": False, "reason": "not_order_event"}

    payload = _order_payload(event_body)
    internal_id = _text(payload.get("id") or payload.get("order_id"))
    order_number = _text(
        payload.get("reference_id") or payload.get("order_number")
    )
    if not internal_id or not order_number:
        return {"accepted": False, "reason": "missing_order_identity"}

    now = _utcnow()
    event_key = _event_id(
        user_id=user_id,
        store_id=store_id,
        event_body=event_body,
    )
    events = shadow_collection(db, EVENTS_COLLECTION)
    event_document = {
        "_id": event_key,
        "user_id": _text(user_id),
        "store_id": _text(store_id),
        "event": event_name,
        "event_created_at": event_body.get("created_at"),
        "internal_order_id": internal_id,
        "order_number": order_number,
        "payload": deepcopy(payload),
        "received_at": now,
        "outbox_status": "pending",
        "outbox_attempts": 0,
        "next_attempt_at": now,
        "shadow_only": True,
    }
    result = await events.update_one(
        {"_id": event_key},
        {"$setOnInsert": event_document},
        upsert=True,
    )
    created = result.upserted_id is not None
    stored_event = event_document
    if not created:
        stored_event = await events.find_one({"_id": event_key})
        if not isinstance(stored_event, dict):
            raise RuntimeError("Salla event outbox row disappeared before delivery")
    # The direct webhook path uses the same row-level fencing as the periodic
    # repairer. Concurrent redeliveries therefore cannot enqueue under the
    # same outbox intent at the same time.
    claim_status, claimed = await _claim_event_outbox_row(
        events,
        stored_event,
        now=now,
    )
    job_key: str | None = None
    queue_ensured = False
    if claim_status == "quarantined":
        raise RuntimeError("Salla event outbox row is quarantined")
    if claimed is not None:
        try:
            job_key = await _deliver_event_outbox_row(
                db,
                claimed,
            )
        except Exception as exc:
            await _record_event_outbox_failure(
                events,
                claimed,
                error=exc,
                now=_utcnow(),
            )
            raise
        queue_ensured = job_key is not None
    else:
        current = await events.find_one({"_id": event_key})
        if isinstance(current, dict) and current.get("outbox_status") == "delivered":
            stored_job_key = current.get("job_id")
            if type(stored_job_key) is str and stored_job_key.strip():
                job_key = stored_job_key
                queue_ensured = True
    return {
        "accepted": True,
        "created": created,
        "queued": created,
        "queue_ensured": queue_ensured,
        "duplicate": not created,
        "event_id": event_key,
        "job_id": job_key,
    }


def _outbox_candidate_filter(now: datetime) -> dict[str, Any]:
    return {"$and": [
        {"outbox_status": {"$ne": "quarantined"}},
        {"$or": [
            {"outbox_status": {"$in": ["pending", "retrying", "processing"]}},
            {"outbox_status": {"$exists": False}},
            {"job_enqueued_at": {"$exists": False}},
        ]},
        {"$or": [
            {"next_attempt_at": {"$lte": now}},
            {"next_attempt_at": {"$exists": False}},
        ]},
        {"$or": [
            {"outbox_lease_expires_at": {"$lte": now}},
            {"outbox_lease_expires_at": {"$exists": False}},
        ]},
    ]}


def _outbox_attempts_filter(event: dict[str, Any]) -> Any:
    if "outbox_attempts" not in event:
        return {"$exists": False}
    return event.get("outbox_attempts")


async def _quarantine_unclaimable_outbox_row(
    events: Any,
    event: dict[str, Any],
    *,
    reason: str,
    now: datetime,
) -> bool:
    result = await events.update_one(
        {"$and": [
            {"_id": event.get("_id")},
            _outbox_candidate_filter(now),
            {"outbox_attempts": _outbox_attempts_filter(event)},
        ]},
        {
            "$set": {
                "outbox_status": "quarantined",
                "outbox_quarantined_at": now,
                "outbox_last_attempt_at": now,
                "outbox_last_error": reason,
                "outbox_expires_at": now + timedelta(seconds=TTL_SECONDS),
            },
            "$unset": {
                "next_attempt_at": "",
                "outbox_lease_token": "",
                "outbox_lease_acquired_at": "",
                "outbox_lease_heartbeat_at": "",
                "outbox_lease_expires_at": "",
            },
        },
    )
    return getattr(result, "matched_count", 0) == 1


async def _claim_event_outbox_row(
    events: Any,
    event: dict[str, Any],
    *,
    now: datetime,
) -> tuple[str, dict[str, Any] | None]:
    previous_attempts = event.get("outbox_attempts")
    if type(previous_attempts) is not int or previous_attempts < 0:
        quarantined = await _quarantine_unclaimable_outbox_row(
            events,
            event,
            reason="malformed_attempts",
            now=now,
        )
        return ("quarantined" if quarantined else "skipped", None)
    if previous_attempts >= MAX_EVENT_OUTBOX_ATTEMPTS:
        quarantined = await _quarantine_unclaimable_outbox_row(
            events,
            event,
            reason="attempt_limit_reached",
            now=now,
        )
        return ("quarantined" if quarantined else "skipped", None)

    token = _new_outbox_lease_token()
    claimed = await events.find_one_and_update(
        {"$and": [
            {"_id": event.get("_id")},
            _outbox_candidate_filter(now),
            {"outbox_attempts": _outbox_attempts_filter(event)},
        ]},
        {
            "$set": {
                "outbox_status": "processing",
                "outbox_last_attempt_at": now,
                "outbox_lease_token": token,
                "outbox_lease_acquired_at": now,
                "outbox_lease_heartbeat_at": now,
                "outbox_lease_expires_at": now + timedelta(
                    seconds=EVENT_OUTBOX_ROW_LEASE_SECONDS
                ),
            },
            "$inc": {
                "outbox_attempts": 1,
                "outbox_lease_epoch": 1,
            },
            "$unset": {
                "next_attempt_at": "",
                "outbox_quarantined_at": "",
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if not isinstance(claimed, dict):
        return "skipped", None
    if claimed.get("outbox_lease_token") != token:
        return "skipped", None
    epoch = claimed.get("outbox_lease_epoch")
    if type(epoch) is not int or epoch < 1:
        return "skipped", None
    return "claimed", claimed


def _outbox_lease_filter(
    event: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    return {
        "_id": event.get("_id"),
        "outbox_status": "processing",
        "outbox_lease_token": event.get("outbox_lease_token"),
        "outbox_lease_epoch": event.get("outbox_lease_epoch"),
        "outbox_lease_expires_at": {"$gt": now},
    }


async def _deliver_event_outbox_row(
    db: Any,
    event: dict[str, Any],
    *,
    clock: Callable[[], datetime] | None = None,
) -> str | None:
    """Atomically materialize and finalize under one exact row fence."""
    live_clock = clock or _utcnow

    async def materialize(transaction_db: Any, session: Any) -> str:
        events = shadow_collection(transaction_db, EVENTS_COLLECTION)
        before_enqueue = live_clock()
        owned = await events.find_one(
            _outbox_lease_filter(event, now=before_enqueue),
            {"_id": 1},
            **_session_kwargs(session),
        )
        if not isinstance(owned, dict):
            raise _OutboxLeaseLost("Salla event outbox row lease was lost")
        job_key = await enqueue_shadow_job(
            transaction_db,
            user_id=_text(event.get("user_id")),
            store_id=_text(event.get("store_id")),
            light_order=deepcopy(event.get("payload") or {}),
            event_created_at=event.get("event_created_at"),
            source_event_id=_text(event.get("_id")),
            now=before_enqueue,
            mongo_session=session,
        )
        finalized_at = live_clock()
        marked = await events.update_one(
            _outbox_lease_filter(event, now=finalized_at),
            {"$set": {
                "outbox_status": "delivered",
                "job_id": job_key,
                "job_enqueued_at": finalized_at,
                # Pending rows have no TTL. Retention starts only after the job
                # write and this marker commit in the same transaction.
                "outbox_expires_at": (
                    finalized_at + timedelta(seconds=TTL_SECONDS)
                ),
            }, "$unset": {
                "next_attempt_at": "",
                "outbox_last_error": "",
                "outbox_quarantined_at": "",
                "outbox_lease_token": "",
                "outbox_lease_acquired_at": "",
                "outbox_lease_heartbeat_at": "",
                "outbox_lease_expires_at": "",
            }},
            **_session_kwargs(session),
        )
        if getattr(marked, "matched_count", 0) != 1:
            raise _OutboxLeaseLost("Salla event outbox row lease was lost")
        return job_key

    try:
        return await _run_outbox_transaction(db, materialize)
    except _OutboxLeaseLost:
        return None


async def _record_event_outbox_failure(
    events: Any,
    event: dict[str, Any],
    *,
    error: Exception,
    now: datetime,
) -> str:
    attempts = event.get("outbox_attempts")
    if type(attempts) is not int or attempts < 1:
        return "lease_lost"
    terminal = attempts >= MAX_EVENT_OUTBOX_ATTEMPTS
    delay = min(
        EVENT_OUTBOX_BACKOFF_MAX_SECONDS,
        EVENT_OUTBOX_BACKOFF_BASE_SECONDS * (2 ** max(attempts - 1, 0)),
    )
    patch: dict[str, Any] = {
        "outbox_status": "quarantined" if terminal else "retrying",
        "outbox_last_attempt_at": now,
        "outbox_last_error": type(error).__name__,
    }
    unset = {
        "outbox_lease_token": "",
        "outbox_lease_acquired_at": "",
        "outbox_lease_heartbeat_at": "",
        "outbox_lease_expires_at": "",
    }
    if terminal:
        patch.update({
            "outbox_quarantined_at": now,
            "outbox_expires_at": now + timedelta(seconds=TTL_SECONDS),
        })
        unset["next_attempt_at"] = ""
    else:
        patch["next_attempt_at"] = now + timedelta(seconds=delay)
    result = await events.update_one(
        _outbox_lease_filter(event, now=now),
        {"$set": patch, "$unset": unset},
    )
    if getattr(result, "matched_count", 0) != 1:
        return "lease_lost"
    return "quarantined" if terminal else "failed"


async def repair_event_job_outbox_once(
    db: Any,
    *,
    limit: int = MAX_EVENT_OUTBOX_REPAIRS_PER_CYCLE,
    now: datetime | None = None,
    before_row: Callable[[], Awaitable[bool]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, int]:
    """Repair a bounded event-to-job batch for the leased V3 worker."""
    safe_limit = max(
        1,
        min(int(limit), MAX_EVENT_OUTBOX_REPAIRS_PER_CYCLE),
    )
    live_clock = clock or _utcnow
    cycle_now = now or live_clock()
    events = shadow_collection(db, EVENTS_COLLECTION)
    cursor = events.find(_outbox_candidate_filter(cycle_now)).sort([
        ("next_attempt_at", 1),
        ("received_at", 1),
        ("_id", 1),
    ]).limit(safe_limit)
    scanned = 0
    repaired = 0
    failed = 0
    quarantined = 0
    lease_lost = 0
    async for event in cursor:
        if before_row is not None and await before_row() is not True:
            break
        scanned += 1
        attempted_at = live_clock()
        claim_status, claimed = await _claim_event_outbox_row(
            events,
            event,
            now=attempted_at,
        )
        if claim_status == "quarantined":
            quarantined += 1
            failed += 1
            continue
        if claimed is None:
            continue
        try:
            job_key = await _deliver_event_outbox_row(
                db,
                claimed,
                clock=live_clock,
            )
        except Exception as exc:
            outcome = await _record_event_outbox_failure(
                events,
                claimed,
                error=exc,
                now=live_clock(),
            )
            if outcome == "lease_lost":
                lease_lost += 1
            else:
                failed += 1
            if outcome == "quarantined":
                quarantined += 1
        else:
            if job_key is None:
                lease_lost += 1
            else:
                repaired += 1
    return {
        "scanned": scanned,
        "repaired": repaired,
        "failed": failed,
        "quarantined": quarantined,
        "lease_lost": lease_lost,
    }


async def enqueue_shadow_job(
    db: Any,
    *,
    user_id: str,
    store_id: str,
    light_order: dict[str, Any],
    event_created_at: Any = None,
    source_event_id: str | None = None,
    now: datetime | None = None,
    mongo_session: Any = None,
) -> str:
    """Create or signal one order job without resetting existing progress.

    A terminal row is reopened only when an atomic Light signal sorts after the
    stored signal by revision, provider time, event time, then payload digest.
    Rediscovery of the same signal is a no-op; signals are never field-merged.
    """
    now = now or datetime.now(timezone.utc)
    internal_id = _text(light_order.get("id") or light_order.get("order_id"))
    order_number = _text(
        light_order.get("reference_id") or light_order.get("order_number")
    )
    if not internal_id or not order_number:
        raise ValueError("Salla shadow job requires order identity")
    job_key = f"{_text(user_id)}:{_text(store_id)}:{internal_id}"
    jobs = shadow_collection(db, JOBS_COLLECTION)
    incoming_signal = _signal(light_order, event_created_at)

    for _attempt in range(_MAX_CAS_ATTEMPTS):
        existing = await jobs.find_one(
            {"_id": job_key},
            **_session_kwargs(mongo_session),
        )
        if not existing:
            document = {
                "_id": job_key,
                "user_id": _text(user_id),
                "store_id": _text(store_id),
                "internal_order_id": internal_id,
                "order_number": order_number,
                "light_order": deepcopy(light_order),
                "light_order_signal": deepcopy(incoming_signal),
                "signal_order_key": list(_signal_order_key(incoming_signal)),
                **incoming_signal,
                "source_event_id": source_event_id,
                "status": "pending",
                "attempts": 0,
                "signal_revision": 1,
                "attempts_signal_revision": 1,
                "attempt_budget_reset_required": False,
                "queue_revision": 1,
                "created_at": now,
                "next_attempt_at": now,
                "updated_at": now,
                "shadow_only": True,
            }
            try:
                result = await jobs.update_one(
                    {"_id": job_key, "queue_revision": {"$exists": False}},
                    {"$setOnInsert": document},
                    upsert=True,
                    **_session_kwargs(mongo_session),
                )
            except DuplicateKeyError:
                continue
            if result.upserted_id is not None:
                return job_key
            continue

        if not _has_newer_signal(incoming_signal, existing):
            return job_key

        expected_revision = existing.get("queue_revision")
        revision_filter: Any = expected_revision
        if expected_revision is None:
            revision_filter = {"$exists": False}
        patch = {
            "user_id": _text(user_id),
            "store_id": _text(store_id),
            "internal_order_id": internal_id,
            "order_number": order_number,
            # Light rows are atomic scheduling signals. They are never merged
            # across revisions; canonical Order Details builds the snapshot.
            "light_order": deepcopy(light_order),
            "light_order_signal": deepcopy(incoming_signal),
            "signal_order_key": list(_signal_order_key(incoming_signal)),
            **incoming_signal,
            "source_event_id": source_event_id,
            "updated_at": now,
            "shadow_only": True,
        }
        next_signal_revision = int(existing.get("signal_revision") or 0) + 1
        unset: dict[str, str] = {}
        if existing.get("status") in _TERMINAL_JOB_STATUSES:
            patch.update({
                "status": "pending",
                "attempts": 0,
                "attempts_signal_revision": next_signal_revision,
                "attempt_budget_reset_required": False,
                "last_error": None,
                "next_attempt_at": now,
                "completed_at": None,
                "terminal_expires_at": None,
            })
            unset = {
                "lease_token": "",
                "lease_expires_at": "",
                "heartbeat_at": "",
                "processing_started_at": "",
            }
        else:
            # Preserve in-flight/retry accounting at enqueue time. The next
            # atomic claim starts a separate budget for this newer revision.
            patch.update({
                "attempts_signal_revision": next_signal_revision,
                "attempt_budget_reset_required": True,
            })
            if existing.get("status") in {"pending", "retrying"}:
                patch["next_attempt_at"] = now
        update: dict[str, Any] = {
            "$set": patch,
            "$inc": {"queue_revision": 1, "signal_revision": 1},
        }
        if unset:
            update["$unset"] = unset
        result = await jobs.update_one(
            {"_id": job_key, "queue_revision": revision_filter},
            update,
            **_session_kwargs(mongo_session),
        )
        if getattr(result, "matched_count", 0) == 1:
            return job_key

    raise RuntimeError("Salla shadow enqueue CAS conflict")


async def requeue_shadow_job_manually(
    db: Any,
    *,
    job_id: str,
    requested_by: str,
    reason: str,
    owner_authorized: bool,
    now: datetime | None = None,
) -> bool:
    """Explicit owner-only manual requeue; no HTTP route is introduced in P0."""
    if owner_authorized is not True:
        raise PermissionError("Salla V3 manual requeue requires owner authorization")
    normalized_actor = _text(requested_by)
    normalized_reason = _text(reason)
    if not normalized_actor or not normalized_reason:
        raise ValueError("manual requeue requires requested_by and reason")
    now = now or datetime.now(timezone.utc)
    jobs = shadow_collection(db, JOBS_COLLECTION)

    for _attempt in range(_MAX_CAS_ATTEMPTS):
        existing = await jobs.find_one({"_id": _text(job_id)})
        if not existing:
            return False
        if _text(existing.get("user_id")) != normalized_actor:
            raise PermissionError("Salla V3 manual requeue is owner-only")
        if existing.get("status") == "processing":
            return False
        expected_revision = existing.get("queue_revision")
        revision_filter: Any = expected_revision
        if expected_revision is None:
            revision_filter = {"$exists": False}
        result = await jobs.update_one(
            {"_id": _text(job_id), "queue_revision": revision_filter},
            {
                "$set": {
                    "status": "pending",
                    "attempts": 0,
                    "attempts_signal_revision": int(
                        existing.get("signal_revision") or 0
                    ) + 1,
                    "attempt_budget_reset_required": False,
                    "last_error": None,
                    "next_attempt_at": now,
                    "completed_at": None,
                    "terminal_expires_at": None,
                    "manual_requeue": {
                        "requested_by": normalized_actor,
                        "reason": normalized_reason,
                        "requested_at": now,
                    },
                    "updated_at": now,
                },
                "$unset": {
                    "lease_token": "",
                    "lease_expires_at": "",
                    "heartbeat_at": "",
                    "processing_started_at": "",
                },
                "$inc": {"queue_revision": 1, "signal_revision": 1},
            },
        )
        if getattr(result, "matched_count", 0) == 1:
            return True
    raise RuntimeError("Salla shadow manual requeue CAS conflict")
