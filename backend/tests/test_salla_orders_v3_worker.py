import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

import salla_orders_v3.ingestion as ingestion_module

from salla_orders_v3.ingestion import capture_verified_order_event
from salla_orders_v3.worker import process_shadow_job


class _Result:
    def __init__(self, *, upserted_id=None, matched_count=0):
        self.upserted_id = upserted_id
        self.matched_count = matched_count


def _matches(row, query):
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches(row, clause) for clause in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(row, clause) for clause in expected):
                return False
            continue
        exists = key in row
        actual = row.get(key)
        if isinstance(expected, dict):
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


class _Collection:
    def __init__(self):
        self.rows = {}

    async def find_one(self, query, projection=None, **kwargs):
        if query.get("_id") is not None:
            row = self.rows.get(query.get("_id"))
            if row is None or not _matches(row, query):
                return None
            return deepcopy(row)
        for row in self.rows.values():
            if _matches(row, query):
                return deepcopy(row)
        return None

    async def update_one(self, query, update, upsert=False, **kwargs):
        key = query.get("_id")
        if key is None:
            matches = [stored_key for stored_key, row in self.rows.items() if _matches(row, query)]
            key = matches[0] if matches else None
        existing = self.rows.get(key) if key is not None else None
        if existing is not None and not _matches(existing, query):
            return _Result()
        if key is None:
            return _Result()
        created = key not in self.rows
        if created and not upsert:
            return _Result()
        row = deepcopy(self.rows.get(key) or {})
        if created:
            row.update(deepcopy(update.get("$setOnInsert") or {}))
        row.update(deepcopy(update.get("$set") or {}))
        for field in (update.get("$inc") or {}):
            row[field] = row.get(field, 0) + update["$inc"][field]
        for field in (update.get("$unset") or {}):
            row.pop(field, None)
        self.rows[key] = row
        return _Result(
            upserted_id=key if created else None,
            matched_count=0 if created else 1,
        )

    async def find_one_and_update(self, query, update, **kwargs):
        for key, row in self.rows.items():
            if _matches(row, query):
                await self.update_one({"_id": key}, update)
                return deepcopy(self.rows[key])
        return None

    def find(self, query, projection=None):
        rows = [deepcopy(row) for row in self.rows.values() if _matches(row, query)]

        class _Cursor:
            def __init__(self, values):
                self.values = values

            def sort(self, spec):
                for field, direction in reversed(spec):
                    self.values.sort(
                        key=lambda row: row.get(field),
                        reverse=direction < 0,
                    )
                return self

            def limit(self, value):
                self.values = self.values[:value]
                return self

            def __aiter__(self):
                self.iterator = iter(self.values)
                return self

            async def __anext__(self):
                try:
                    return next(self.iterator)
                except StopIteration as exc:
                    raise StopAsyncIteration from exc

        return _Cursor(rows)


class _DB:
    def __init__(self):
        self.salla_orders_v3_events = _Collection()
        self.salla_orders_v3_jobs = _Collection()

    async def _salla_orders_v3_transaction_runner(self, operation):
        event_rows = deepcopy(self.salla_orders_v3_events.rows)
        job_rows = deepcopy(self.salla_orders_v3_jobs.rows)
        transaction_db = _DB()
        transaction_db.salla_orders_v3_events.rows = deepcopy(event_rows)
        transaction_db.salla_orders_v3_jobs.rows = deepcopy(job_rows)
        result = await operation(transaction_db, None)
        if (
            self.salla_orders_v3_events.rows != event_rows
            or self.salla_orders_v3_jobs.rows != job_rows
        ):
            raise ingestion_module._OutboxLeaseLost(
                "simulated transaction write conflict"
            )
        self.salla_orders_v3_events.rows = (
            transaction_db.salla_orders_v3_events.rows
        )
        self.salla_orders_v3_jobs.rows = (
            transaction_db.salla_orders_v3_jobs.rows
        )
        return result


def _event(updated_at="2026-08-30T10:00:00+03:00"):
    return {
        "event": "order.updated",
        "merchant": 50,
        "created_at": updated_at,
        "data": {
            "id": 901,
            "reference_id": "3001",
            "updated_at": updated_at,
        },
    }


def _successful_snapshot():
    return {
        "order_id": "901",
        "order_number": "3001",
        "products": [{"order_item_id": "7"}],
        "items_authoritative": True,
        "items_payload_valid": True,
        "needs_items_enrichment": False,
        "signal_revision": 1,
        "items_success_signal_revision": 1,
    }


@pytest.mark.asyncio
async def test_same_verified_webhook_is_idempotent_and_creates_one_job():
    db = _DB()

    first = await capture_verified_order_event(
        db,
        user_id="owner-1",
        store_id="50",
        event_body=_event(),
    )
    second = await capture_verified_order_event(
        db,
        user_id="owner-1",
        store_id="50",
        event_body=_event(),
    )

    assert first["created"] is True
    assert first["queued"] is True
    assert second["created"] is False
    assert second["queued"] is False
    assert len(db.salla_orders_v3_events.rows) == 1
    assert len(db.salla_orders_v3_jobs.rows) == 1


@pytest.mark.asyncio
async def test_duplicate_delivered_webhook_returns_existing_job_without_reclaim():
    db = _DB()
    first = await capture_verified_order_event(
        db,
        user_id="owner-1",
        store_id="50",
        event_body=_event(),
    )
    replay = await capture_verified_order_event(
        db,
        user_id="owner-1",
        store_id="50",
        event_body=_event(),
    )

    assert first["created"] is True
    assert replay["created"] is False
    assert replay["duplicate"] is True
    assert replay["queue_ensured"] is True
    assert len(db.salla_orders_v3_jobs.rows) == 1


@pytest.mark.asyncio
async def test_durable_event_outbox_repairs_without_provider_redelivery():
    db = _DB()
    first = await capture_verified_order_event(
        db,
        user_id="owner-1",
        store_id="50",
        event_body=_event(),
    )
    db.salla_orders_v3_jobs.rows.clear()
    event = db.salla_orders_v3_events.rows[first["event_id"]]
    event.pop("job_enqueued_at", None)
    event.pop("job_id", None)

    repair = getattr(ingestion_module, "repair_event_job_outbox_once", None)
    assert callable(repair)
    result = await repair(db, limit=10)

    assert result == {
        "scanned": 1,
        "repaired": 1,
        "failed": 0,
        "quarantined": 0,
        "lease_lost": 0,
    }
    assert len(db.salla_orders_v3_jobs.rows) == 1
    assert db.salla_orders_v3_events.rows[first["event_id"]]["job_enqueued_at"] is not None


@pytest.mark.asyncio
async def test_pending_event_outbox_has_no_ttl_and_repairs_insert_enqueue_crash(
    monkeypatch,
):
    db = _DB()
    original_enqueue = ingestion_module.enqueue_shadow_job

    async def fail_enqueue(*args, **kwargs):
        raise RuntimeError("simulated event-to-job gap")

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", fail_enqueue)
    with pytest.raises(RuntimeError, match="event-to-job gap"):
        await capture_verified_order_event(
            db,
            user_id="owner-1",
            store_id="50",
            event_body=_event(),
        )

    event = next(iter(db.salla_orders_v3_events.rows.values()))
    assert event["outbox_status"] == "retrying"
    assert event["outbox_attempts"] == 1
    assert "outbox_expires_at" not in event
    assert db.salla_orders_v3_jobs.rows == {}

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", original_enqueue)
    retry_at = event["next_attempt_at"]
    assert await ingestion_module.repair_event_job_outbox_once(
        db,
        limit=10,
        now=retry_at,
        clock=lambda: retry_at,
    ) == {
        "scanned": 1,
        "repaired": 1,
        "failed": 0,
        "quarantined": 0,
        "lease_lost": 0,
    }
    assert len(db.salla_orders_v3_jobs.rows) == 1
    assert event is not db.salla_orders_v3_events.rows[event["_id"]]
    delivered = db.salla_orders_v3_events.rows[event["_id"]]
    assert delivered["outbox_status"] == "delivered"
    assert delivered["outbox_expires_at"] is not None


def _pending_outbox_row(index, now, *, attempts=0):
    return {
        "_id": f"event-{index:03d}",
        "user_id": "owner-1",
        "store_id": "50",
        "event": "order.updated",
        "event_created_at": now.isoformat(),
        "internal_order_id": str(9000 + index),
        "order_number": str(3000 + index),
        "payload": {
            "id": 9000 + index,
            "reference_id": str(3000 + index),
            "revision": index + 1,
        },
        "received_at": now + timedelta(microseconds=index),
        "outbox_status": "pending",
        "outbox_attempts": attempts,
        "next_attempt_at": now,
        "shadow_only": True,
    }


@pytest.mark.asyncio
async def test_outbox_backoff_prevents_first_hundred_poison_rows_starving_101(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    for index in range(101):
        row = _pending_outbox_row(index, now)
        db.salla_orders_v3_events.rows[row["_id"]] = row

    async def enqueue(_db, *, light_order, **kwargs):
        if int(light_order["id"]) < 9100:
            raise RuntimeError("poison row")
        return "owner-1:50:9100"

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", enqueue)
    first = await ingestion_module.repair_event_job_outbox_once(
        db,
        limit=100,
        now=now,
        clock=lambda: now,
    )
    second_now = now + timedelta(seconds=300)
    second = await ingestion_module.repair_event_job_outbox_once(
        db,
        limit=100,
        now=second_now,
        clock=lambda: second_now,
    )

    assert first == {
        "scanned": 100,
        "repaired": 0,
        "failed": 100,
        "quarantined": 0,
        "lease_lost": 0,
    }
    assert second == {
        "scanned": 1,
        "repaired": 1,
        "failed": 0,
        "quarantined": 0,
        "lease_lost": 0,
    }
    assert db.salla_orders_v3_events.rows["event-100"]["outbox_status"] == (
        "delivered"
    )


@pytest.mark.asyncio
async def test_outbox_due_time_sort_prevents_two_poison_pages_starving_201(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    for index in range(201):
        row = _pending_outbox_row(index, now)
        db.salla_orders_v3_events.rows[row["_id"]] = row

    async def enqueue(_db, *, light_order, **kwargs):
        if int(light_order["id"]) < 9200:
            raise RuntimeError("poison row")
        return "owner-1:50:9200"

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", enqueue)
    cycle_times = [
        now,
        now + timedelta(seconds=300),
        now + timedelta(seconds=600),
    ]
    results = []
    for cycle_time in cycle_times:
        results.append(
            await ingestion_module.repair_event_job_outbox_once(
                db,
                limit=100,
                now=cycle_time,
                clock=lambda cycle_time=cycle_time: cycle_time,
            )
        )

    assert [result["scanned"] for result in results] == [100, 100, 100]
    assert results[2]["repaired"] == 1
    assert db.salla_orders_v3_events.rows["event-200"]["outbox_status"] == (
        "delivered"
    )


@pytest.mark.asyncio
async def test_outbox_attempt_limit_quarantines_with_bounded_audit(
    monkeypatch,
):
    max_attempts = getattr(
        ingestion_module,
        "MAX_EVENT_OUTBOX_ATTEMPTS",
        None,
    )
    assert type(max_attempts) is int and max_attempts > 0
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    poison = _pending_outbox_row(0, now, attempts=max_attempts - 1)
    healthy = _pending_outbox_row(1, now)
    db.salla_orders_v3_events.rows[poison["_id"]] = poison
    db.salla_orders_v3_events.rows[healthy["_id"]] = healthy

    async def enqueue(_db, *, light_order, **kwargs):
        if int(light_order["id"]) == 9000:
            raise RuntimeError("poison row")
        return "owner-1:50:9001"

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", enqueue)
    result = await ingestion_module.repair_event_job_outbox_once(
        db,
        limit=2,
        now=now,
        clock=lambda: now,
    )

    quarantined = db.salla_orders_v3_events.rows[poison["_id"]]
    assert result == {
        "scanned": 2,
        "repaired": 1,
        "failed": 1,
        "quarantined": 1,
        "lease_lost": 0,
    }
    assert quarantined["outbox_status"] == "quarantined"
    assert quarantined["outbox_attempts"] == max_attempts
    assert quarantined["outbox_quarantined_at"] == now
    assert quarantined["outbox_expires_at"] > now
    assert quarantined["outbox_last_error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_outbox_row_claim_allows_only_one_concurrent_enqueue(monkeypatch):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    row = _pending_outbox_row(0, now)
    db.salla_orders_v3_events.rows[row["_id"]] = row
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def enqueue(*args, **kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return "owner-1:50:9000"

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", enqueue)
    first_task = asyncio.create_task(
        ingestion_module.repair_event_job_outbox_once(
            db, limit=1, now=now, clock=lambda: now
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=0.2)
    second_task = asyncio.create_task(
        ingestion_module.repair_event_job_outbox_once(
            db, limit=1, now=now, clock=lambda: now
        )
    )
    await asyncio.sleep(0)
    release.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert calls == 1
    assert first["repaired"] + second["repaired"] == 1
    stored = db.salla_orders_v3_events.rows[row["_id"]]
    assert stored["outbox_status"] == "delivered"
    assert stored["outbox_lease_epoch"] == 1
    assert "outbox_lease_token" not in stored


@pytest.mark.asyncio
async def test_outbox_does_not_finalize_after_row_lease_is_known_lost(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    row = _pending_outbox_row(0, now)
    db.salla_orders_v3_events.rows[row["_id"]] = row

    async def enqueue(*args, **kwargs):
        stored = db.salla_orders_v3_events.rows[row["_id"]]
        stored["outbox_lease_token"] = "successor"
        stored["outbox_lease_epoch"] += 1
        return "owner-1:50:9000"

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", enqueue)
    result = await ingestion_module.repair_event_job_outbox_once(
        db,
        limit=1,
        now=now,
        clock=lambda: now,
    )

    stored = db.salla_orders_v3_events.rows[row["_id"]]
    assert result["repaired"] == 0
    assert result["lease_lost"] == 1
    assert stored["outbox_status"] == "processing"
    assert "job_enqueued_at" not in stored


@pytest.mark.asyncio
async def test_transaction_rolls_back_job_write_when_event_takeover_wins(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    row = _pending_outbox_row(0, now)
    db.salla_orders_v3_events.rows[row["_id"]] = row
    original_find_one = _Collection.find_one
    takeover_done = False

    async def find_one_with_takeover(collection, query, projection=None, **kwargs):
        nonlocal takeover_done
        key = str(query.get("_id") or "")
        if key.startswith("owner-1:50:") and not takeover_done:
            takeover_done = True
            stored = db.salla_orders_v3_events.rows[row["_id"]]
            stored["outbox_lease_token"] = "successor"
            stored["outbox_lease_epoch"] += 1
        return await original_find_one(
            collection,
            query,
            projection,
            **kwargs,
        )

    monkeypatch.setattr(_Collection, "find_one", find_one_with_takeover)
    result = await ingestion_module.repair_event_job_outbox_once(
        db,
        limit=1,
        now=now,
        clock=lambda: now,
    )

    stored = db.salla_orders_v3_events.rows[row["_id"]]
    assert takeover_done is True
    assert result["lease_lost"] == 1
    assert result["repaired"] == 0
    assert db.salla_orders_v3_jobs.rows == {}
    assert stored["outbox_lease_token"] == "successor"
    assert "job_enqueued_at" not in stored


@pytest.mark.asyncio
async def test_missing_transaction_support_fails_closed_without_job_write():
    class _NoTransactionDB:
        def __init__(self):
            self.salla_orders_v3_events = _Collection()
            self.salla_orders_v3_jobs = _Collection()

    db = _NoTransactionDB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    row = _pending_outbox_row(0, now)
    db.salla_orders_v3_events.rows[row["_id"]] = row

    result = await ingestion_module.repair_event_job_outbox_once(
        db,
        limit=1,
        now=now,
        clock=lambda: now,
    )

    stored = db.salla_orders_v3_events.rows[row["_id"]]
    assert result["failed"] == 1
    assert result["lease_lost"] == 0
    assert db.salla_orders_v3_jobs.rows == {}
    assert stored["outbox_status"] == "retrying"
    assert stored["outbox_last_error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_outbox_expiry_rolls_back_actual_job_materialization():
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    row = _pending_outbox_row(0, now)
    db.salla_orders_v3_events.rows[row["_id"]] = row
    clock_values = iter([now, now, now + timedelta(seconds=61)])

    result = await ingestion_module.repair_event_job_outbox_once(
        db,
        limit=1,
        now=now,
        clock=lambda: next(clock_values),
    )

    stored = db.salla_orders_v3_events.rows[row["_id"]]
    assert result["repaired"] == 0
    assert result["lease_lost"] == 1
    assert stored["outbox_status"] == "processing"
    assert db.salla_orders_v3_jobs.rows == {}
    assert "job_enqueued_at" not in stored


@pytest.mark.asyncio
async def test_outbox_exception_after_expiry_cannot_write_retry_state(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    row = _pending_outbox_row(0, now)
    db.salla_orders_v3_events.rows[row["_id"]] = row
    clock_values = iter([now, now, now + timedelta(seconds=61)])

    async def enqueue(*args, **kwargs):
        raise RuntimeError("provider queue failed after lease expiry")

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", enqueue)
    result = await ingestion_module.repair_event_job_outbox_once(
        db,
        limit=1,
        now=now,
        clock=lambda: next(clock_values),
    )

    stored = db.salla_orders_v3_events.rows[row["_id"]]
    assert result["failed"] == 0
    assert result["lease_lost"] == 1
    assert stored["outbox_status"] == "processing"
    assert "next_attempt_at" not in stored


@pytest.mark.asyncio
async def test_outbox_exception_after_takeover_cannot_write_quarantine(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    row = _pending_outbox_row(
        0,
        now,
        attempts=ingestion_module.MAX_EVENT_OUTBOX_ATTEMPTS - 1,
    )
    db.salla_orders_v3_events.rows[row["_id"]] = row

    async def enqueue(*args, **kwargs):
        stored = db.salla_orders_v3_events.rows[row["_id"]]
        stored["outbox_lease_token"] = "successor"
        stored["outbox_lease_epoch"] += 1
        raise RuntimeError("failed under superseded owner")

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", enqueue)
    result = await ingestion_module.repair_event_job_outbox_once(
        db,
        limit=1,
        now=now,
        clock=lambda: now,
    )

    stored = db.salla_orders_v3_events.rows[row["_id"]]
    assert result["quarantined"] == 0
    assert result["lease_lost"] == 1
    assert stored["outbox_status"] == "processing"
    assert "outbox_quarantined_at" not in stored


@pytest.mark.asyncio
async def test_expired_processing_outbox_row_is_reclaimed_with_new_epoch(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    row = _pending_outbox_row(0, now)
    row.update({
        "outbox_status": "processing",
        "outbox_lease_token": "dead-owner",
        "outbox_lease_epoch": 3,
        "outbox_lease_expires_at": now - timedelta(seconds=1),
    })
    row.pop("next_attempt_at")
    db.salla_orders_v3_events.rows[row["_id"]] = row

    async def enqueue(*args, **kwargs):
        return "owner-1:50:9000"

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", enqueue)
    result = await ingestion_module.repair_event_job_outbox_once(
        db,
        limit=1,
        now=now,
        clock=lambda: now,
    )

    stored = db.salla_orders_v3_events.rows[row["_id"]]
    assert result["repaired"] == 1
    assert stored["outbox_status"] == "delivered"
    assert stored["outbox_lease_epoch"] == 4
    assert "outbox_lease_token" not in stored


@pytest.mark.asyncio
async def test_concurrent_direct_webhook_capture_enqueues_once(monkeypatch):
    db = _DB()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def enqueue(*args, **kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return "owner-1:50:901"

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", enqueue)
    first_task = asyncio.create_task(
        capture_verified_order_event(
            db,
            user_id="owner-1",
            store_id="50",
            event_body=_event(),
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=0.2)
    second = await capture_verified_order_event(
        db,
        user_id="owner-1",
        store_id="50",
        event_body=_event(),
    )
    release.set()
    first = await asyncio.wait_for(first_task, timeout=0.2)

    assert calls == 1
    assert first["queue_ensured"] is True
    assert second["queue_ensured"] is False
    stored = next(iter(db.salla_orders_v3_events.rows.values()))
    assert stored["outbox_status"] == "delivered"


@pytest.mark.asyncio
async def test_direct_capture_cannot_finalize_after_its_row_lease_expires(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    clock_values = iter([now, now, now + timedelta(seconds=61)])
    monkeypatch.setattr(
        ingestion_module,
        "_utcnow",
        lambda: next(clock_values),
    )

    async def enqueue(*args, **kwargs):
        return "owner-1:50:901"

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", enqueue)
    result = await capture_verified_order_event(
        db,
        user_id="owner-1",
        store_id="50",
        event_body=_event(),
    )

    stored = next(iter(db.salla_orders_v3_events.rows.values()))
    assert result["queue_ensured"] is False
    assert result["job_id"] is None
    assert stored["outbox_status"] == "processing"
    assert "job_enqueued_at" not in stored


@pytest.mark.asyncio
async def test_direct_capture_exception_after_expiry_cannot_write_retry(
    monkeypatch,
):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    clock_values = iter([now, now, now + timedelta(seconds=61)])
    monkeypatch.setattr(
        ingestion_module,
        "_utcnow",
        lambda: next(clock_values),
    )

    async def enqueue(*args, **kwargs):
        raise RuntimeError("enqueue failed after lease expiry")

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", enqueue)
    with pytest.raises(RuntimeError, match="after lease expiry"):
        await capture_verified_order_event(
            db,
            user_id="owner-1",
            store_id="50",
            event_body=_event(),
        )

    stored = next(iter(db.salla_orders_v3_events.rows.values()))
    assert stored["outbox_status"] == "processing"
    assert "next_attempt_at" not in stored


@pytest.mark.asyncio
async def test_malformed_outbox_attempts_are_atomically_quarantined(monkeypatch):
    db = _DB()
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    row = _pending_outbox_row(0, now)
    row["outbox_attempts"] = "not-an-integer"
    db.salla_orders_v3_events.rows[row["_id"]] = row

    async def enqueue(*args, **kwargs):
        raise AssertionError("malformed attempts must never be delivered")

    monkeypatch.setattr(ingestion_module, "enqueue_shadow_job", enqueue)
    result = await ingestion_module.repair_event_job_outbox_once(
        db,
        limit=1,
        now=now,
        clock=lambda: now,
    )

    stored = db.salla_orders_v3_events.rows[row["_id"]]
    assert result["quarantined"] == 1
    assert stored["outbox_status"] == "quarantined"
    assert stored["outbox_attempts"] == "not-an-integer"
    assert stored["outbox_last_error"] == "malformed_attempts"


class _Engine:
    def __init__(self):
        self.calls = 0

    async def sync_order(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "ok": True,
                "items_sync_status": "failed",
                "items_payload_valid": False,
            }
        return {
            "ok": True,
            "items_sync_status": "succeeded",
            "items_payload_valid": True,
            "items_count": 1,
            "compatibility_order": _successful_snapshot(),
        }


@pytest.mark.asyncio
async def test_items_failure_retries_then_completes_without_dropping_order_job():
    db = _DB()
    await capture_verified_order_event(
        db,
        user_id="owner-1",
        store_id="50",
        event_body=_event(),
    )
    job = next(iter(db.salla_orders_v3_jobs.rows.values()))
    engine = _Engine()
    now = datetime.now(timezone.utc)

    first = await process_shadow_job(db, deepcopy(job), engine=engine, now=now)
    job_after_failure = next(iter(db.salla_orders_v3_jobs.rows.values()))
    second = await process_shadow_job(
        db,
        deepcopy(job_after_failure),
        engine=engine,
        now=now + timedelta(seconds=3),
    )
    job_after_success = next(iter(db.salla_orders_v3_jobs.rows.values()))

    assert first["status"] == "retrying"
    assert job_after_failure["attempts"] == 1
    assert job_after_failure["last_error"] == "items_enrichment_failed"
    assert second["status"] == "completed"
    assert job_after_success["status"] == "completed"
    assert job_after_success["attempts"] == 2


class _RaisingEngine:
    async def sync_order(self, **kwargs):
        raise TimeoutError("provider timeout")


@pytest.mark.asyncio
async def test_unexpected_provider_error_is_persisted_for_retry():
    db = _DB()
    await capture_verified_order_event(
        db,
        user_id="owner-1",
        store_id="50",
        event_body=_event(),
    )
    job = next(iter(db.salla_orders_v3_jobs.rows.values()))

    outcome = await process_shadow_job(
        db,
        deepcopy(job),
        engine=_RaisingEngine(),
        now=datetime.now(timezone.utc),
    )
    stored = next(iter(db.salla_orders_v3_jobs.rows.values()))

    assert outcome["status"] == "retrying"
    assert stored["status"] == "retrying"
    assert stored["last_error"] == "TimeoutError"


@pytest.mark.asyncio
async def test_success_without_fenced_snapshot_candidate_remains_retryable():
    class _MissingSnapshotEngine:
        async def sync_order(self, **kwargs):
            return {
                "ok": True,
                "items_sync_status": "succeeded",
                "items_payload_valid": True,
            }

    db = _DB()
    await capture_verified_order_event(
        db,
        user_id="owner-1",
        store_id="50",
        event_body=_event(),
    )
    job = next(iter(db.salla_orders_v3_jobs.rows.values()))

    outcome = await process_shadow_job(
        db,
        deepcopy(job),
        engine=_MissingSnapshotEngine(),
        now=datetime.now(timezone.utc),
    )
    stored = next(iter(db.salla_orders_v3_jobs.rows.values()))

    assert outcome["status"] == "retrying"
    assert stored["last_error"] == "invalid_snapshot_candidate"
    assert "compatibility_order" not in stored
