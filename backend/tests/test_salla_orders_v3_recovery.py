from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from itertools import permutations
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

import salla_orders_v3.worker as worker_module
from salla_orders_v3.gateway import PaginationPage
from salla_orders_v3.ingestion import (
    enqueue_shadow_job,
    requeue_shadow_job_manually,
)
from salla_orders_v3.shadow import SallaOrdersShadowEngine
from salla_orders_v3.worker import (
    JOBS_COLLECTION,
    MAX_JOB_ATTEMPTS,
    STATE_COLLECTION,
    claim_due_shadow_job,
    heartbeat_shadow_job_lease,
    process_shadow_job,
    run_due_jobs_once,
    run_recovery_once,
)


UTC = timezone.utc


def _nested(document: dict[str, Any], path: str) -> tuple[bool, Any]:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, clause) for clause in expected):
                return False
            continue
        if key == "$and":
            if not all(_matches(document, clause) for clause in expected):
                return False
            continue
        exists, actual = _nested(document, key)
        if isinstance(expected, dict) and any(str(k).startswith("$") for k in expected):
            for operator, operand in expected.items():
                if operator == "$exists" and exists is not bool(operand):
                    return False
                if operator == "$in" and actual not in operand:
                    return False
                if operator == "$nin" and actual in operand:
                    return False
                if operator == "$ne" and actual == operand:
                    return False
                if operator == "$lt" and (not exists or not actual < operand):
                    return False
                if operator == "$lte" and (not exists or not actual <= operand):
                    return False
                if operator == "$gt" and (not exists or not actual > operand):
                    return False
                if operator == "$gte" and (not exists or not actual >= operand):
                    return False
        elif not exists or actual != expected:
            return False
    return True


def _set_nested(document: dict[str, Any], path: str, value: Any) -> None:
    owner = document
    parts = path.split(".")
    for part in parts[:-1]:
        owner = owner.setdefault(part, {})
    owner[parts[-1]] = deepcopy(value)


def _unset_nested(document: dict[str, Any], path: str) -> None:
    owner = document
    parts = path.split(".")
    for part in parts[:-1]:
        owner = owner.get(part)
        if not isinstance(owner, dict):
            return
    owner.pop(parts[-1], None)


class _WriteResult:
    def __init__(self, *, matched_count=0, modified_count=0, upserted_id=None):
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class _StrictMongoCollection:
    """Small Mongo-faithful seam: selectors matter and ``_id`` is immutable."""

    def __init__(self):
        self.rows: dict[str, dict[str, Any]] = {}
        self.updates: list[tuple[dict[str, Any], dict[str, Any], bool]] = []

    async def find_one(self, query, projection=None):
        for row in self.rows.values():
            if _matches(row, query):
                return deepcopy(row)
        return None

    def _apply(self, row, update, *, inserted):
        if "_id" in (update.get("$set") or {}):
            raise RuntimeError("immutable field _id cannot appear in $set")
        if inserted:
            for key, value in (update.get("$setOnInsert") or {}).items():
                _set_nested(row, key, value)
        for key, value in (update.get("$set") or {}).items():
            _set_nested(row, key, value)
        for key, value in (update.get("$inc") or {}).items():
            exists, current = _nested(row, key)
            _set_nested(row, key, (current if exists else 0) + value)
        for key in (update.get("$unset") or {}):
            _unset_nested(row, key)

    async def update_one(self, query, update, upsert=False):
        self.updates.append((deepcopy(query), deepcopy(update), bool(upsert)))
        for key, row in self.rows.items():
            if _matches(row, query):
                before = deepcopy(row)
                self._apply(row, update, inserted=False)
                return _WriteResult(
                    matched_count=1,
                    modified_count=int(row != before),
                )
        if not upsert:
            return _WriteResult()
        key = str(query.get("_id") or (update.get("$setOnInsert") or {}).get("_id"))
        if key in self.rows:
            raise DuplicateKeyError("duplicate _id from failed CAS upsert")
        row = {"_id": key}
        self._apply(row, update, inserted=True)
        self.rows[key] = row
        return _WriteResult(upserted_id=key, modified_count=1)

    async def find_one_and_update(
        self,
        query,
        update,
        *,
        upsert=False,
        return_document=None,
        sort=None,
    ):
        candidates = [row for row in self.rows.values() if _matches(row, query)]
        if candidates and sort:
            for field, direction in reversed(sort):
                candidates.sort(
                    key=lambda row: _nested(row, field)[1] or datetime.min.replace(tzinfo=UTC),
                    reverse=direction < 0,
                )
        if candidates:
            row = candidates[0]
            self._apply(row, update, inserted=False)
            return deepcopy(row)
        if not upsert:
            return None
        await self.update_one(query, update, upsert=True)
        return await self.find_one({"_id": query.get("_id")})


class _DB:
    def __init__(self):
        for name in (
            "salla_orders_v3_shadow",
            "salla_orders_v3_events",
            JOBS_COLLECTION,
            STATE_COLLECTION,
            "salla_orders_v3_leases",
        ):
            setattr(self, name, _StrictMongoCollection())


def _light(updated_at: str, *, internal_id: int = 901) -> dict[str, Any]:
    return {
        "id": internal_id,
        "reference_id": str(3000 + internal_id),
        "updated_at": updated_at,
    }


def _successful_snapshot(*, signal_revision: int = 1) -> dict[str, Any]:
    return {
        "order_id": "901",
        "order_number": "3901",
        "products": [{"order_item_id": "7"}],
        "items_authoritative": True,
        "items_payload_valid": True,
        "needs_items_enrichment": False,
        "signal_revision": signal_revision,
        "items_success_signal_revision": signal_revision,
    }


@pytest.mark.asyncio
async def test_existing_retry_job_keeps_attempts_status_and_error_on_rediscovery():
    db = _DB()
    now = datetime(2026, 9, 13, 9, tzinfo=UTC)
    key = await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        now=now,
    )
    row = db.salla_orders_v3_jobs.rows[key]
    row.update({"status": "retrying", "attempts": 3, "last_error": "TimeoutError"})

    await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:01:00Z"),
        now=now + timedelta(minutes=1),
    )

    stored = db.salla_orders_v3_jobs.rows[key]
    assert stored["status"] == "retrying"
    assert stored["attempts"] == 3
    assert stored["last_error"] == "TimeoutError"
    assert stored["provider_updated_at"] == "2026-09-13T08:01:00+00:00"
    assert stored["signal_revision"] == 2


@pytest.mark.asyncio
async def test_terminal_job_reopens_only_for_newer_signal_or_explicit_manual_requeue():
    db = _DB()
    now = datetime(2026, 9, 13, 9, tzinfo=UTC)
    key = await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        event_created_at="2026-09-13T08:00:01Z",
        now=now,
    )
    row = db.salla_orders_v3_jobs.rows[key]
    row.update({"status": "completed", "attempts": 4, "last_error": None})

    await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T07:59:00Z"),
        event_created_at="2026-09-13T08:00:00Z",
        now=now + timedelta(minutes=1),
    )
    unchanged = deepcopy(db.salla_orders_v3_jobs.rows[key])
    assert unchanged["status"] == "completed"
    assert unchanged["attempts"] == 4
    assert unchanged["signal_revision"] == 1

    await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:02:00Z"),
        event_created_at="2026-09-13T08:00:00Z",
        now=now + timedelta(minutes=2),
    )
    reopened = db.salla_orders_v3_jobs.rows[key]
    assert reopened["status"] == "pending"
    assert reopened["attempts"] == 0
    assert reopened["signal_revision"] == 2

    reopened.update({"status": "failed", "attempts": MAX_JOB_ATTEMPTS})
    with pytest.raises(PermissionError, match="owner authorization"):
        await requeue_shadow_job_manually(
            db,
            job_id=key,
            requested_by="owner-1",
            reason="repair missing options",
            owner_authorized=False,
            now=now + timedelta(minutes=3),
        )
    assert await requeue_shadow_job_manually(
        db,
        job_id=key,
        requested_by="owner-1",
        reason="repair missing options",
        owner_authorized=True,
        now=now + timedelta(minutes=3),
    )
    assert reopened["status"] == "pending"
    assert reopened["attempts"] == 0
    assert reopened["manual_requeue"]["requested_by"] == "owner-1"


@pytest.mark.asyncio
async def test_signal_order_uses_one_atomic_revision_first_tuple():
    db = _DB()
    now = datetime(2026, 9, 13, 9, tzinfo=UTC)
    first = _light("2026-09-13T08:00:00Z") | {"revision": 7}
    key = await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=first,
        event_created_at="2026-09-13T08:00:01Z",
        now=now,
    )
    db.salla_orders_v3_jobs.rows[key]["status"] = "completed"

    older_revision_newer_event = _light("2026-09-13T07:59:00Z") | {"revision": 6}
    await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=older_revision_newer_event,
        event_created_at="2026-09-13T08:00:02Z",
        now=now + timedelta(seconds=1),
    )

    stored = db.salla_orders_v3_jobs.rows[key]
    assert stored["status"] == "completed"
    assert stored["provider_revision"] == 7
    assert stored["provider_updated_at"] == "2026-09-13T08:00:00+00:00"
    assert stored["event_created_at"] == "2026-09-13T08:00:01+00:00"

    stored.update({"status": "completed", "attempts": 2})
    newer_revision_older_times = _light("2026-09-13T07:58:00Z") | {
        "revision": 8,
    }
    await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=newer_revision_older_times,
        event_created_at="2026-09-13T08:00:00Z",
        now=now + timedelta(seconds=2),
    )
    assert stored["status"] == "pending"
    assert stored["attempts"] == 0
    assert stored["provider_revision"] == 8
    assert stored["provider_updated_at"] == "2026-09-13T07:58:00+00:00"
    assert stored["event_created_at"] == "2026-09-13T08:00:00+00:00"


class _DiscoveryGateway:
    def __init__(self):
        self.calls = []
        self.items_calls = 0

    async def list_light_orders_page(self, user_id, *, page, from_date, to_date):
        self.calls.append((user_id, page, from_date, to_date))
        return (
            [_light(f"2026-09-12T08:0{page}:00Z", internal_id=900 + page)],
            PaginationPage(
                current_page=page,
                total_pages=2,
                next_page=page + 1 if page < 2 else None,
                exhausted=page == 2,
            ),
        )

    async def get_order_items(self, *args, **kwargs):
        self.items_calls += 1
        raise AssertionError("discovery must never call Order Items")


@pytest.mark.asyncio
async def test_recovery_discovery_is_queue_only_resumable_and_never_sets_id():
    db = _DB()
    gateway = _DiscoveryGateway()
    now = datetime(2026, 9, 13, 9, tzinfo=UTC)

    first = await run_recovery_once(
        db,
        user_id="owner-1",
        store_id="store-1",
        max_pages=1,
        gateway=gateway,
        now=now,
    )
    first_state = deepcopy(
        next(iter(db.salla_orders_v3_sync_state.rows.values()))
    )
    second = await run_recovery_once(
        db,
        user_id="owner-1",
        store_id="store-1",
        max_pages=1,
        gateway=gateway,
        now=now + timedelta(minutes=1),
    )

    assert first["discovered"] == 1
    assert first["queued"] == 1
    assert first["continuation"] == 1
    assert first["truncated"] == 1
    assert first_state["continuation_reason"] == "pagination"
    assert first_state["truncation_reason"] == "page_budget"
    assert second["discovered"] == 1
    assert gateway.calls == [
        ("owner-1", 1, "2026-09-12", "2026-09-12"),
        ("owner-1", 2, "2026-09-12", "2026-09-12"),
    ]
    assert gateway.items_calls == 0
    assert len(db.salla_orders_v3_jobs.rows) == 2
    assert len(db.salla_orders_v3_sync_state.rows) == 1
    state = next(iter(db.salla_orders_v3_sync_state.rows.values()))
    assert state["next_from_date"] == "2026-09-13"
    assert state["next_page"] == 1
    assert state["state_revision"] == 4
    assert state["continuation_reason"] == "next_window"
    assert state["truncation_reason"] == "page_budget"
    assert all(
        "_id" not in (update.get("$set") or {})
        for _query, update, _upsert in db.salla_orders_v3_sync_state.updates
    )


@pytest.mark.asyncio
async def test_recovery_restarts_expired_pagination_without_skipping_date():
    db = _DB()
    now = datetime(2026, 9, 16, 9, tzinfo=UTC)
    gateway = _DiscoveryGateway()
    await run_recovery_once(db, user_id="owner-1", store_id="store-1",
                            max_pages=1, gateway=gateway, now=now)
    result = await run_recovery_once(
        db, user_id="owner-1", store_id="store-1", max_pages=1,
        gateway=gateway, now=now + timedelta(minutes=16),
    )
    assert [call[1] for call in gateway.calls] == [1, 1]
    state = db.salla_orders_v3_sync_state.rows["owner-1:store-1"]
    assert state["next_from_date"] == "2026-09-15"
    assert state["next_page"] == 2
    assert len(db.salla_orders_v3_jobs.rows) == 1  # replay is idempotent
    assert result["pagination_restarts"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_start", [None, "not-a-date", "future"])
async def test_recovery_untrusted_pagination_timestamp_restarts_page_one(stored_start):
    db = _DB()
    now = datetime(2026, 9, 16, 9, tzinfo=UTC)
    db.salla_orders_v3_sync_state.rows["owner-1:store-1"] = {
        "_id": "owner-1:store-1", "next_from_date": "2026-09-15",
        "next_to_date": "2026-09-15", "next_page": 2,
        "pagination_started_at": now + timedelta(seconds=1) if stored_start == "future" else stored_start,
    }
    gateway = _DiscoveryGateway()
    await run_recovery_once(db, user_id="owner-1", store_id="store-1",
                            max_pages=1, gateway=gateway, now=now)
    assert gateway.calls[0][1] == 1


@pytest.mark.asyncio
async def test_recovery_pagination_deadline_is_not_extended_by_checkpoints():
    db = _DB()
    now = datetime(2026, 9, 16, 9, tzinfo=UTC)

    class ManyPages:
        def __init__(self):
            self.pages = []

        async def list_light_orders_page(self, user_id, *, page, **kwargs):
            self.pages.append(page)
            return [_light("2026-09-15T08:00:00Z", internal_id=900 + page)], PaginationPage(page, 10, page + 1, False)

    gateway = ManyPages()
    for minutes in (0, 7, 14):
        await run_recovery_once(db, user_id="owner-1", store_id="store-1",
                                max_pages=1, gateway=gateway, now=now + timedelta(minutes=minutes))
    assert gateway.pages == [1, 2, 1]
    state = db.salla_orders_v3_sync_state.rows["owner-1:store-1"]
    assert state["pagination_started_at"] == now + timedelta(minutes=14)
    assert len(db.salla_orders_v3_jobs.rows) == 2


@pytest.mark.asyncio
async def test_recovery_expired_response_cannot_mark_window_complete():
    db = _DB()
    now = datetime(2026, 9, 16, 9, tzinfo=UTC)
    current = now
    db.salla_orders_v3_sync_state.rows["owner-1:store-1"] = {
        "_id": "owner-1:store-1", "next_from_date": "2026-09-15",
        "next_to_date": "2026-09-15", "next_page": 2,
        "pagination_started_at": now - timedelta(minutes=13, seconds=59),
    }

    class ExpiringResponse:
        async def list_light_orders_page(self, user_id, *, page, **kwargs):
            nonlocal current
            current += timedelta(seconds=2)
            return [], PaginationPage(page, page, None, True)

    result = await run_recovery_once(
        db, user_id="owner-1", store_id="store-1", max_pages=1,
        gateway=ExpiringResponse(), now=now, clock=lambda: current,
    )
    state = db.salla_orders_v3_sync_state.rows["owner-1:store-1"]
    assert state["next_from_date"] == "2026-09-15"
    assert state["next_page"] == 1
    assert not state.get("last_completed_to_date")
    assert state["truncation_reason"] == "pagination_session_expired"
    assert result["pages"] == 1
    assert result["pagination_restarts"] == 1
    assert result["lease_lost"] == 0
    assert not db.salla_orders_v3_jobs.rows


@pytest.mark.asyncio
async def test_recovery_accepts_fresh_bson_naive_utc_pagination_timestamp():
    db = _DB()
    now = datetime(2026, 9, 16, 9, tzinfo=UTC)
    db.salla_orders_v3_sync_state.rows["owner-1:store-1"] = {
        "_id": "owner-1:store-1", "next_from_date": "2026-09-15",
        "next_to_date": "2026-09-15", "next_page": 2,
        "pagination_started_at": (now - timedelta(minutes=1)).replace(tzinfo=None),
    }
    gateway = _DiscoveryGateway()
    await run_recovery_once(db, user_id="owner-1", store_id="store-1",
                            max_pages=1, gateway=gateway, now=now)
    assert gateway.calls[0][1] == 2


@pytest.mark.asyncio
async def test_recovery_lost_lease_during_slow_page_never_advances_cursor_or_queue():
    db = _DB()
    now = datetime.now(UTC)

    class _SlowGateway:
        async def list_light_orders_page(self, user_id, *, page, from_date, to_date):
            state = next(iter(db.salla_orders_v3_sync_state.rows.values()))
            state["recovery_lease_token"] = "successor"
            await asyncio.sleep(0.03)
            return (
                [_light("2026-09-12T08:00:00Z")],
                PaginationPage(1, 1, None, True),
            )

    result = await run_recovery_once(
        db,
        user_id="owner-1",
        store_id="store-1",
        max_pages=1,
        gateway=_SlowGateway(),
        now=now,
        heartbeat_interval=0.005,
    )

    assert result["lease_lost"] == 1
    assert result["discovered"] == 0
    assert db.salla_orders_v3_jobs.rows == {}
    state = db.salla_orders_v3_sync_state.rows["owner-1:store-1"]
    assert "next_page" not in state
    assert "pending_discovery_jobs" not in state


class _SuccessfulEngine:
    def __init__(self):
        self.calls = 0

    async def sync_order(self, **kwargs):
        self.calls += 1
        return {
            "ok": True,
            "items_sync_status": "succeeded",
            "items_payload_valid": True,
            "compatibility_order": _successful_snapshot(),
        }


class _SlowSuccessfulEngine(_SuccessfulEngine):
    async def sync_order(self, **kwargs):
        await asyncio.sleep(0.03)
        return await super().sync_order(**kwargs)


@pytest.mark.asyncio
async def test_job_lease_heartbeat_and_fenced_completion_reject_lost_owner():
    db = _DB()
    now = datetime(2026, 9, 13, 9, tzinfo=UTC)
    key = await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        now=now,
    )
    claimed = await claim_due_shadow_job(db, now=now)
    assert claimed is not None
    assert claimed["attempts"] == 1
    token = claimed["lease_token"]
    assert await heartbeat_shadow_job_lease(
        db,
        job_id=key,
        lease_token=token,
        now=now + timedelta(seconds=30),
    )

    # A successor owns the row. The stale worker must not finalize it.
    stored = db.salla_orders_v3_jobs.rows[key]
    stored["lease_token"] = "successor"
    stored["lease_epoch"] += 1
    engine = _SuccessfulEngine()
    outcome = await process_shadow_job(
        db,
        claimed,
        engine=engine,
        now=now + timedelta(seconds=31),
    )

    assert outcome["status"] == "lost_lease"
    assert engine.calls == 0
    assert stored["status"] == "processing"
    assert stored["lease_token"] == "successor"


@pytest.mark.asyncio
async def test_slow_items_call_heartbeats_owned_job_until_fenced_completion():
    db = _DB()
    now = datetime.now(UTC)
    key = await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        now=now,
    )
    claimed = await claim_due_shadow_job(db, now=now)
    token = claimed["lease_token"]

    outcome = await process_shadow_job(
        db,
        claimed,
        engine=_SlowSuccessfulEngine(),
        heartbeat_interval=0.005,
    )

    heartbeat_writes = [
        update
        for query, update, _upsert in db.salla_orders_v3_jobs.updates
        if query.get("lease_token") == token and "heartbeat_at" in update.get("$set", {})
    ]
    assert outcome["status"] == "completed"
    assert len(heartbeat_writes) >= 2
    assert db.salla_orders_v3_jobs.rows[key]["status"] == "completed"


@pytest.mark.asyncio
async def test_expired_processing_lease_reclaims_only_below_attempt_budget():
    db = _DB()
    now = datetime(2026, 9, 13, 9, tzinfo=UTC)
    jobs = db.salla_orders_v3_jobs.rows
    jobs["retryable"] = {
        "_id": "retryable",
        "status": "processing",
        "attempts": MAX_JOB_ATTEMPTS - 1,
        "next_attempt_at": now - timedelta(minutes=1),
        "lease_expires_at": now - timedelta(seconds=1),
        "signal_revision": 1,
        "queue_revision": 2,
    }
    jobs["exhausted"] = {
        "_id": "exhausted",
        "status": "processing",
        "attempts": MAX_JOB_ATTEMPTS,
        "next_attempt_at": now - timedelta(minutes=1),
        "lease_expires_at": now - timedelta(seconds=1),
        "signal_revision": 1,
        "queue_revision": 2,
    }

    claimed = await claim_due_shadow_job(db, now=now)

    assert claimed is not None
    assert claimed["_id"] == "retryable"
    assert claimed["attempts"] == MAX_JOB_ATTEMPTS
    assert jobs["exhausted"]["status"] == "processing"


@pytest.mark.asyncio
async def test_exhausted_stale_processing_lease_becomes_terminal_without_provider_call():
    db = _DB()
    now = datetime.now(UTC)
    db.salla_orders_v3_jobs.rows["exhausted"] = {
        "_id": "exhausted",
        "status": "processing",
        "attempts": MAX_JOB_ATTEMPTS,
        "next_attempt_at": now - timedelta(minutes=1),
        "lease_expires_at": now - timedelta(seconds=1),
        "signal_revision": 1,
        "queue_revision": 2,
    }

    result = await run_due_jobs_once(db)

    stored = db.salla_orders_v3_jobs.rows["exhausted"]
    assert result["claimed"] == 0
    assert result["failed"] == 1
    assert stored["status"] == "failed"
    assert stored["last_error"] == "worker_lease_expired_at_attempt_limit"


class _SignalChangingEngine:
    def __init__(self, db, job_id, changed_at):
        self.db = db
        self.job_id = job_id
        self.changed_at = changed_at

    async def sync_order(self, **kwargs):
        await enqueue_shadow_job(
            self.db,
            user_id="owner-1",
            store_id="store-1",
            light_order=_light(self.changed_at),
        )
        return {
            "ok": True,
            "items_sync_status": "succeeded",
            "items_payload_valid": True,
        }


@pytest.mark.asyncio
async def test_new_signal_during_processing_releases_stale_revision_for_successor():
    db = _DB()
    now = datetime.now(UTC)
    key = await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        now=now,
    )
    claimed = await claim_due_shadow_job(db, now=now)

    outcome = await process_shadow_job(
        db,
        claimed,
        engine=_SignalChangingEngine(db, key, "2026-09-13T08:01:00Z"),
        now=now + timedelta(seconds=1),
    )

    stored = db.salla_orders_v3_jobs.rows[key]
    assert outcome["status"] == "superseded"
    assert stored["status"] == "pending"
    assert stored["attempts"] == 1
    assert stored["signal_revision"] == 2
    assert "lease_token" not in stored
    assert not await heartbeat_shadow_job_lease(
        db,
        job_id=key,
        lease_token=claimed["lease_token"],
        lease_epoch=claimed["lease_epoch"],
        signal_revision=claimed["signal_revision"],
        now=now + timedelta(seconds=2),
    )


class _FlakyItemsGateway:
    def __init__(self):
        self.calls = 0

    async def get_light_order_details(self, user_id, internal_id):
        return _light("2026-09-13T08:00:00Z")

    async def get_order_items(self, user_id, internal_id):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("temporary Items failure")
        return [{
            "id": 77,
            "sku": "CUSTOM",
            "quantity": 1,
            "customer_options": {"طباعة؟": False, "عدد النجوم": 0},
            "custom_fields": {"الاسم": "نورة"},
        }]


@pytest.mark.asyncio
async def test_missing_options_remain_retryable_then_become_authoritative_after_items_success():
    db = _DB()
    gateway = _FlakyItemsGateway()
    engine = SallaOrdersShadowEngine(db, gateway=gateway)
    now = datetime.now(UTC)
    key = await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        now=now,
    )

    first = await process_shadow_job(
        db,
        deepcopy(db.salla_orders_v3_jobs.rows[key]),
        engine=engine,
        now=now,
    )
    assert first["status"] == "retrying"
    assert db.salla_orders_v3_jobs.rows[key]["last_error"] == "TimeoutError"
    assert "compatibility_order" not in db.salla_orders_v3_jobs.rows[key]
    assert db.salla_orders_v3_shadow.rows == {}

    second = await process_shadow_job(
        db,
        deepcopy(db.salla_orders_v3_jobs.rows[key]),
        engine=engine,
        now=now + timedelta(seconds=3),
    )
    after_success = db.salla_orders_v3_jobs.rows[key]["compatibility_order"]
    product = after_success["products"][0]
    options = {row["name"]: row["value"] for row in product["options"]}

    assert second["status"] == "completed"
    assert after_success["items_authoritative"] is True
    assert after_success["needs_items_enrichment"] is False
    assert after_success["items_success_signal_revision"] == 1
    assert options == {"طباعة؟": False, "عدد النجوم": 0}
    assert product["custom_fields"][0]["value"] == "نورة"
    snapshot_writes = [
        (query, update)
        for query, update, _upsert in db.salla_orders_v3_jobs.updates
        if "compatibility_order" in (update.get("$set") or {})
    ]
    assert len(snapshot_writes) == 1
    snapshot_query, snapshot_update = snapshot_writes[0]
    assert snapshot_query["status"] == "processing"
    assert snapshot_query["lease_token"]
    assert snapshot_query["lease_epoch"] == 2
    assert snapshot_query["signal_revision"] == 1
    assert snapshot_update["$set"]["status"] == "completed"


@pytest.mark.asyncio
async def test_recovery_repairs_durable_queue_intent_before_next_provider_page(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 9, tzinfo=UTC)

    class _FirstPage:
        async def list_light_orders_page(self, user_id, *, page, from_date, to_date):
            return (
                [_light("2026-09-12T08:00:00Z")],
                PaginationPage(page, 1, None, True),
            )

    original_enqueue = worker_module.enqueue_shadow_job

    async def crash_after_checkpoint(*args, **kwargs):
        raise RuntimeError("simulated process loss before queue materialization")

    monkeypatch.setattr(
        worker_module,
        "enqueue_shadow_job",
        crash_after_checkpoint,
    )
    with pytest.raises(RuntimeError, match="simulated process loss"):
        await run_recovery_once(
            db,
            user_id="owner-1",
            store_id="store-1",
            max_pages=1,
            gateway=_FirstPage(),
            now=now,
        )

    state = db.salla_orders_v3_sync_state.rows["owner-1:store-1"]
    assert state["next_from_date"] == "2026-09-13"
    assert len(state["pending_discovery_jobs"]) == 1
    assert db.salla_orders_v3_jobs.rows == {}

    monkeypatch.setattr(worker_module, "enqueue_shadow_job", original_enqueue)

    class _NextPage:
        async def list_light_orders_page(self, user_id, *, page, from_date, to_date):
            assert len(db.salla_orders_v3_jobs.rows) == 1
            return ([], PaginationPage(page, 1, None, True))

    repaired = await run_recovery_once(
        db,
        user_id="owner-1",
        store_id="store-1",
        max_pages=1,
        gateway=_NextPage(),
        now=now + timedelta(minutes=1),
    )

    assert repaired["queued"] == 1
    assert state["pending_discovery_jobs"] == []
    assert len(db.salla_orders_v3_jobs.rows) == 1


@pytest.mark.asyncio
async def test_worker_cycle_does_not_serialize_discovery_behind_slow_items(monkeypatch):
    discovery_finished = asyncio.Event()
    release_items = asyncio.Event()

    async def discovery(_db):
        discovery_finished.set()
        return [{"discovered": 1}]

    async def slow_items(_db):
        await release_items.wait()
        return {"completed": 1}

    async def outbox(_db):
        return {"repaired": 0}

    monkeypatch.setattr(worker_module, "run_discovery_cycle", discovery)
    monkeypatch.setattr(worker_module, "run_due_jobs_once", slow_items)
    monkeypatch.setattr(worker_module, "run_event_outbox_repair_once", outbox)
    task = asyncio.create_task(worker_module.run_worker_cycle(object()))

    await asyncio.wait_for(discovery_finished.wait(), timeout=0.2)
    assert not task.done()
    release_items.set()
    result = await asyncio.wait_for(task, timeout=0.2)

    assert result == {
        "recovery": [{"discovered": 1}],
        "jobs": {"completed": 1},
        "event_outbox": {"repaired": 0},
    }


@pytest.mark.asyncio
async def test_runtime_discovery_repeats_while_enrichment_loop_is_blocked(monkeypatch):
    two_discoveries = asyncio.Event()
    discovery_calls = 0

    async def discovery(_db):
        nonlocal discovery_calls
        discovery_calls += 1
        if discovery_calls >= 2:
            two_discoveries.set()
        return []

    async def blocked_items(_db):
        await asyncio.Event().wait()

    async def outbox(_db):
        return {"repaired": 0}

    monkeypatch.setattr(worker_module, "run_discovery_cycle", discovery)
    monkeypatch.setattr(worker_module, "run_due_jobs_once", blocked_items)
    monkeypatch.setattr(worker_module, "run_event_outbox_repair_once", outbox)
    monkeypatch.setattr(worker_module, "RECOVERY_INTERVAL_SECONDS", 0.005)
    task = asyncio.create_task(worker_module._worker_loop(object()))
    try:
        await asyncio.wait_for(two_discoveries.wait(), timeout=0.2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert discovery_calls >= 2


class _SignalDuringItemsGateway:
    def __init__(self, db):
        self.db = db

    async def get_light_order_details(self, user_id, internal_id):
        return _light("2026-09-13T08:00:00Z")

    async def get_order_items(self, user_id, internal_id):
        await enqueue_shadow_job(
            self.db,
            user_id="owner-1",
            store_id="store-1",
            light_order=_light("2026-09-13T08:01:00Z"),
        )
        return [{"id": 77, "sku": "CURRENT", "quantity": 1}]


@pytest.mark.asyncio
async def test_new_signal_before_persistence_cannot_write_stale_authoritative_snapshot():
    db = _DB()
    now = datetime.now(UTC)
    key = await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        now=now,
    )
    claimed = await claim_due_shadow_job(db, now=now)
    engine = SallaOrdersShadowEngine(
        db,
        gateway=_SignalDuringItemsGateway(db),
    )

    result = await process_shadow_job(
        db,
        claimed,
        engine=engine,
        now=now + timedelta(seconds=1),
    )

    assert result["status"] == "superseded"
    assert db.salla_orders_v3_shadow.rows == {}
    stored = db.salla_orders_v3_jobs.rows[key]
    assert stored["status"] == "pending"
    assert stored["signal_revision"] == 2


@pytest.mark.asyncio
async def test_new_signal_during_last_attempt_gets_an_independent_revision_budget():
    db = _DB()
    now = datetime.now(UTC)
    key = await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        now=now,
    )
    row = db.salla_orders_v3_jobs.rows[key]
    row["attempts"] = MAX_JOB_ATTEMPTS - 1
    row["attempts_signal_revision"] = row["signal_revision"]
    claimed = await claim_due_shadow_job(db, now=now)
    assert claimed["attempts"] == MAX_JOB_ATTEMPTS

    result = await process_shadow_job(
        db,
        claimed,
        engine=_SignalChangingEngine(db, key, "2026-09-13T08:01:00Z"),
        now=now + timedelta(seconds=1),
    )
    successor = await claim_due_shadow_job(
        db,
        now=now + timedelta(seconds=2),
    )

    assert result["status"] == "superseded"
    assert successor is not None
    assert successor["signal_revision"] == 2
    assert successor["attempts_signal_revision"] == 2
    assert successor["attempts"] == 1


@pytest.mark.asyncio
async def test_run_due_jobs_claims_only_inside_available_worker_slots(monkeypatch):
    db = _DB()
    now = datetime.now(UTC)
    for offset in range(worker_module.MAX_CONCURRENCY + 4):
        await enqueue_shadow_job(
            db,
            user_id="owner-1",
            store_id="store-1",
            light_order=_light(
                "2026-09-13T08:00:00Z",
                internal_id=1000 + offset,
            ),
            now=now,
        )

    all_slots_busy = asyncio.Event()
    release = asyncio.Event()
    running = 0

    async def blocked_process(_db, job, **kwargs):
        nonlocal running
        running += 1
        if running == worker_module.MAX_CONCURRENCY:
            all_slots_busy.set()
        await release.wait()
        return {"status": "completed", "attempts": job["attempts"]}

    monkeypatch.setattr(worker_module, "process_shadow_job", blocked_process)
    task = asyncio.create_task(
        run_due_jobs_once(db, limit=worker_module.MAX_CONCURRENCY + 4)
    )
    try:
        await asyncio.wait_for(all_slots_busy.wait(), timeout=0.2)
        attempts = [
            int(row.get("attempts") or 0)
            for row in db.salla_orders_v3_jobs.rows.values()
        ]
        assert sum(value > 0 for value in attempts) == worker_module.MAX_CONCURRENCY
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_cursor_checkpoint_fails_if_recovery_lease_changes_at_write(monkeypatch):
    db = _DB()
    now = datetime.now(UTC)

    class _OnePage:
        async def list_light_orders_page(self, user_id, *, page, from_date, to_date):
            return (
                [_light("2026-09-12T08:00:00Z")],
                PaginationPage(page, 1, None, True),
            )

    original = worker_module._write_sync_state

    async def steal_then_write(*args, **kwargs):
        leases = db.salla_orders_v3_leases.rows
        if leases:
            next(iter(leases.values()))["lease_token"] = "successor"
        else:
            state = next(iter(db.salla_orders_v3_sync_state.rows.values()))
            state["recovery_lease_token"] = "successor"
        return await original(*args, **kwargs)

    monkeypatch.setattr(worker_module, "_write_sync_state", steal_then_write)
    result = await run_recovery_once(
        db,
        user_id="owner-1",
        store_id="store-1",
        max_pages=1,
        gateway=_OnePage(),
        now=now,
    )

    state = db.salla_orders_v3_sync_state.rows.get("owner-1:store-1") or {}
    assert result["lease_lost"] == 1
    assert "next_page" not in state


class _IntegrationCursor:
    def __init__(self, rows, owner):
        self.rows = [deepcopy(row) for row in rows]
        self.owner = owner

    def sort(self, spec):
        self.owner.sort_calls.append(spec)
        for field, direction in reversed(spec):
            self.rows.sort(
                key=lambda row: str(row.get(field) or ""),
                reverse=direction < 0,
            )
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Integrations:
    def __init__(self, rows):
        self.rows = rows
        self.sort_calls = []

    def find(self, query, projection=None):
        return _IntegrationCursor(
            [row for row in self.rows if _matches(row, query)],
            self,
        )


@pytest.mark.asyncio
async def test_integration_scan_is_stably_sorted_and_resumes_beyond_first_hundred(
    monkeypatch,
):
    db = _DB()
    db.salla_integrations = _Integrations([
        {
            "_id": f"i-{index:03d}",
            "user_id": f"u-{index:03d}",
            "store_id": "s",
            "status": "connected",
        }
        for index in reversed(range(205))
    ])
    processed = []

    async def recovery(_db, *, user_id, store_id):
        processed.append((user_id, store_id))
        return {"discovered": 0}

    monkeypatch.setattr(worker_module, "run_recovery_once", recovery)
    await worker_module.run_discovery_cycle(db)
    await worker_module.run_discovery_cycle(db)
    await worker_module.run_discovery_cycle(db)

    assert len(processed) == 205
    assert len(set(processed)) == 205
    assert processed == sorted(processed)
    assert db.salla_integrations.sort_calls


@pytest.mark.asyncio
async def test_due_cycle_reads_a_fresh_clock_before_every_claim_and_sweep(
    monkeypatch,
):
    base = datetime(2026, 9, 13, 10, tzinfo=UTC)
    clock_values = iter(base + timedelta(seconds=offset) for offset in range(3))
    claim_times = []
    sweep_times = []

    async def claim(_db, *, now, job_id=None):
        claim_times.append(now)
        if len(claim_times) == 1:
            return {"_id": "job-1", "attempts": 1}
        return None

    async def process(_db, job, **kwargs):
        return {"status": "completed", "attempts": job["attempts"]}

    async def sweep(_db, *, now):
        sweep_times.append(now)
        return False

    monkeypatch.setattr(worker_module, "MAX_CONCURRENCY", 1)
    monkeypatch.setattr(worker_module, "_utcnow", lambda: next(clock_values))
    monkeypatch.setattr(worker_module, "claim_due_shadow_job", claim)
    monkeypatch.setattr(worker_module, "process_shadow_job", process)
    monkeypatch.setattr(worker_module, "_fail_one_exhausted_stale_job", sweep)

    result = await run_due_jobs_once(object(), limit=2)

    assert result["claimed"] == 1
    assert claim_times == [base, base + timedelta(seconds=1)]
    assert sweep_times == [base + timedelta(seconds=2)]


@pytest.mark.asyncio
async def test_final_snapshot_write_is_unexpired_and_heartbeat_stays_live(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=UTC)
    await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        now=now,
    )
    claimed = await claim_due_shadow_job(db, now=now)
    heartbeat_calls = 0

    async def heartbeat(*args, **kwargs):
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        return True

    monkeypatch.setattr(worker_module, "heartbeat_shadow_job_lease", heartbeat)
    jobs = db.salla_orders_v3_jobs
    original_update = jobs.update_one

    async def delayed_final_update(query, update, upsert=False):
        if "compatibility_order" in (update.get("$set") or {}):
            expiry = query.get("lease_expires_at")
            assert isinstance(expiry, dict)
            assert expiry.get("$gt") == now + timedelta(seconds=1)
            calls_before_wait = heartbeat_calls
            await asyncio.sleep(0.03)
            assert heartbeat_calls > calls_before_wait
        return await original_update(query, update, upsert=upsert)

    jobs.update_one = delayed_final_update
    outcome = await process_shadow_job(
        db,
        claimed,
        engine=_SuccessfulEngine(),
        now=now + timedelta(seconds=1),
        heartbeat_interval=0.005,
    )

    assert outcome["status"] == "completed"


@pytest.mark.asyncio
async def test_final_snapshot_fence_samples_clock_at_the_commit_edge(monkeypatch):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=UTC)
    await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        now=now,
    )
    claimed = await claim_due_shadow_job(db, now=now)
    clock_values = iter(
        now + timedelta(seconds=offset)
        for offset in (1, 2, 3, 4)
    )
    monkeypatch.setattr(worker_module, "_utcnow", lambda: next(clock_values))

    outcome = await process_shadow_job(
        db,
        claimed,
        engine=_SuccessfulEngine(),
        heartbeat_interval=10,
    )

    final_queries = [
        query
        for query, update, _upsert in db.salla_orders_v3_jobs.updates
        if "compatibility_order" in (update.get("$set") or {})
    ]
    assert outcome["status"] == "completed"
    assert len(final_queries) == 1
    assert final_queries[0]["lease_expires_at"] == {
        "$gt": now + timedelta(seconds=4)
    }


@pytest.mark.asyncio
async def test_expired_lease_at_finalize_cannot_commit_snapshot():
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=UTC)
    key = await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        now=now,
    )
    claimed = await claim_due_shadow_job(db, now=now)

    class _ExpiresBeforeFinalize(_SuccessfulEngine):
        async def sync_order(self, **kwargs):
            result = await super().sync_order(**kwargs)
            db.salla_orders_v3_jobs.rows[key]["lease_expires_at"] = now
            return result

    outcome = await process_shadow_job(
        db,
        claimed,
        engine=_ExpiresBeforeFinalize(),
        now=now + timedelta(seconds=1),
        heartbeat_interval=10,
    )

    stored = db.salla_orders_v3_jobs.rows[key]
    assert outcome["status"] == "lost_lease"
    assert "compatibility_order" not in stored


@pytest.mark.asyncio
async def test_signal_change_after_details_fences_before_items_call():
    db = _DB()
    now = datetime.now(UTC)
    await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        now=now,
    )
    claimed = await claim_due_shadow_job(db, now=now)

    class _SignalAfterDetailsGateway:
        def __init__(self):
            self.items_calls = 0

        async def get_light_order_details(self, user_id, internal_id):
            await enqueue_shadow_job(
                db,
                user_id="owner-1",
                store_id="store-1",
                light_order=_light("2026-09-13T08:01:00Z"),
                now=now + timedelta(seconds=1),
            )
            return _light("2026-09-13T08:00:00Z")

        async def get_order_items(self, user_id, internal_id):
            self.items_calls += 1
            return [{"id": 77, "sku": "STALE", "quantity": 1}]

    gateway = _SignalAfterDetailsGateway()
    outcome = await process_shadow_job(
        db,
        claimed,
        engine=SallaOrdersShadowEngine(db, gateway=gateway),
        now=now + timedelta(seconds=2),
        heartbeat_interval=0.005,
    )

    assert outcome["status"] == "superseded"
    assert gateway.items_calls == 0


@pytest.mark.parametrize("stolen_fence", ["lease_token", "lease_epoch"])
@pytest.mark.asyncio
async def test_token_or_epoch_change_after_details_fences_before_items_call(
    stolen_fence,
):
    db = _DB()
    now = datetime.now(UTC)
    key = await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=_light("2026-09-13T08:00:00Z"),
        now=now,
    )
    claimed = await claim_due_shadow_job(db, now=now)

    class _StolenAfterDetailsGateway:
        def __init__(self):
            self.items_calls = 0

        async def get_light_order_details(self, user_id, internal_id):
            row = db.salla_orders_v3_jobs.rows[key]
            if stolen_fence == "lease_token":
                row["lease_token"] = "successor"
            else:
                row["lease_epoch"] += 1
            return _light("2026-09-13T08:00:00Z")

        async def get_order_items(self, user_id, internal_id):
            self.items_calls += 1
            return [{"id": 77, "sku": "STALE", "quantity": 1}]

    gateway = _StolenAfterDetailsGateway()
    outcome = await process_shadow_job(
        db,
        claimed,
        engine=SallaOrdersShadowEngine(db, gateway=gateway),
        now=now + timedelta(seconds=2),
        heartbeat_interval=0.005,
    )

    assert outcome["status"] == "lost_lease"
    assert gateway.items_calls == 0


@pytest.mark.asyncio
async def test_signal_order_is_total_transitive_and_arrival_independent():
    now = datetime(2026, 9, 13, 10, tzinfo=UTC)
    signals = [
        ({
            **_light("2026-09-13T08:02:00Z"),
            "revision": 6,
            "status": "created",
            "customer": {"name": "old", "phone": "111"},
            "legacy_only": "must-not-survive",
        }, "2026-09-13T08:03:00Z"),
        ({
            **_light("2026-09-13T08:01:00Z"),
            "revision": 7,
            "status": "paid",
            "customer": {"phone": "222"},
        }, "2026-09-13T08:02:00Z"),
        ({
            **_light("2026-09-13T08:00:00Z"),
            "revision": 8,
            "status": "shipped",
            "customer": {},
        }, "2026-09-13T08:01:00Z"),
        ({
            **_light("2026-09-13T09:00:00Z"),
            "status": "revision-missing",
        }, "2026-09-13T09:01:00Z"),
    ]
    outcomes = []

    for arrival_order in permutations(signals):
        db = _DB()
        for offset, (payload, event_created_at) in enumerate(arrival_order):
            key = await enqueue_shadow_job(
                db,
                user_id="owner-1",
                store_id="store-1",
                light_order=payload,
                event_created_at=event_created_at,
                now=now + timedelta(seconds=offset),
            )
        stored = db.salla_orders_v3_jobs.rows[key]
        outcomes.append({
            "light_order": deepcopy(stored["light_order"]),
            "provider_revision": stored.get("provider_revision"),
            "provider_updated_at": stored.get("provider_updated_at"),
            "event_created_at": stored.get("event_created_at"),
            "signal_order_key": deepcopy(stored.get("signal_order_key")),
        })

    assert all(outcome == outcomes[0] for outcome in outcomes)
    stored = outcomes[0]
    assert stored["light_order"] == signals[2][0]
    assert stored["light_order"]["customer"] == {}
    assert "legacy_only" not in stored["light_order"]
    assert stored["provider_revision"] == 8
    assert stored["provider_updated_at"] == "2026-09-13T08:00:00+00:00"
    assert stored["event_created_at"] == "2026-09-13T08:01:00+00:00"


@pytest.mark.asyncio
async def test_equal_provider_signals_have_a_deterministic_payload_tiebreaker():
    now = datetime(2026, 9, 13, 10, tzinfo=UTC)
    payloads = [
        {
            **_light("2026-09-13T08:00:00Z"),
            "revision": 8,
            "status": "paid",
        },
        {
            **_light("2026-09-13T08:00:00Z"),
            "revision": 8,
            "status": "shipped",
        },
    ]
    outcomes = []
    for arrival_order in permutations(payloads):
        db = _DB()
        for offset, payload in enumerate(arrival_order):
            key = await enqueue_shadow_job(
                db,
                user_id="owner-1",
                store_id="store-1",
                light_order=payload,
                event_created_at="2026-09-13T08:01:00Z",
                now=now + timedelta(seconds=offset),
            )
        outcomes.append(db.salla_orders_v3_jobs.rows[key]["light_order"])

    assert outcomes[0] == outcomes[1]


@pytest.mark.asyncio
async def test_lower_total_signal_cannot_create_an_aggregate_clock_revision():
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=UTC)
    newest = {
        **_light("2026-09-13T08:00:00Z"),
        "revision": 8,
        "status": "shipped",
    }
    key = await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=newest,
        event_created_at="2026-09-13T08:01:00Z",
        now=now,
    )
    before = deepcopy(db.salla_orders_v3_jobs.rows[key])
    lower_revision_with_later_clocks = {
        **_light("2026-09-13T09:00:00Z"),
        "revision": 6,
        "status": "created",
    }
    await enqueue_shadow_job(
        db,
        user_id="owner-1",
        store_id="store-1",
        light_order=lower_revision_with_later_clocks,
        event_created_at="2026-09-13T09:01:00Z",
        now=now + timedelta(seconds=1),
    )
    stored = db.salla_orders_v3_jobs.rows[key]
    assert stored["signal_revision"] == before["signal_revision"]
    assert stored["queue_revision"] == before["queue_revision"]
    assert stored["light_order"] == newest
    assert stored["provider_updated_at"] == before["provider_updated_at"]
    assert stored["event_created_at"] == before["event_created_at"]


@pytest.mark.asyncio
async def test_event_outbox_repair_cycle_is_bounded_leased_and_reports_metrics(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=UTC)
    entered = asyncio.Event()
    release = asyncio.Event()
    observed_limits = []

    async def repair(_db, *, limit, now, before_row=None, clock=None):
        observed_limits.append(limit)
        assert callable(before_row)
        assert callable(clock)
        assert await before_row() is True
        entered.set()
        await release.wait()
        return {
            "scanned": 2,
            "repaired": 1,
            "failed": 1,
            "lease_lost": 2,
        }

    run_repair = getattr(worker_module, "run_event_outbox_repair_once", None)
    assert callable(run_repair)
    monkeypatch.setattr(worker_module, "repair_event_job_outbox_once", repair)

    first = asyncio.create_task(run_repair(db, limit=999, now=now))
    await asyncio.wait_for(entered.wait(), timeout=0.2)
    second = await run_repair(db, limit=999, now=now)
    release.set()
    first_result = await asyncio.wait_for(first, timeout=0.2)

    assert second == {
        "scanned": 0,
        "repaired": 0,
        "failed": 0,
        "quarantined": 0,
        "lease_busy": 1,
        "lease_lost": 0,
    }
    assert first_result == {
        "scanned": 2,
        "repaired": 1,
        "failed": 1,
        "quarantined": 0,
        "lease_busy": 0,
        "lease_lost": 2,
    }
    assert observed_limits == [worker_module.MAX_EVENT_OUTBOX_REPAIRS_PER_CYCLE]


@pytest.mark.asyncio
async def test_event_outbox_repair_stops_when_its_exact_lease_is_lost(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=UTC)

    async def repair(_db, *, limit, now, before_row=None, clock=None):
        assert callable(clock)
        state = db.salla_orders_v3_sync_state.rows[
            "__salla_orders_v3_event_outbox_repair__"
        ]
        state["recovery_lease_token"] = "successor"
        assert await before_row() is False
        return {"scanned": 0, "repaired": 0, "failed": 0}

    monkeypatch.setattr(worker_module, "repair_event_job_outbox_once", repair)

    result = await worker_module.run_event_outbox_repair_once(db, now=now)

    assert result == {
        "scanned": 0,
        "repaired": 0,
        "failed": 0,
        "quarantined": 0,
        "lease_busy": 0,
        "lease_lost": 1,
    }
