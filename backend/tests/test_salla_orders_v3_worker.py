from copy import deepcopy
from datetime import datetime, timezone

import pytest

from salla_orders_v3.ingestion import capture_verified_order_event
from salla_orders_v3.worker import process_shadow_job


class _Result:
    def __init__(self, *, upserted_id=None):
        self.upserted_id = upserted_id


class _Collection:
    def __init__(self):
        self.rows = {}

    async def find_one(self, query, projection=None):
        row = self.rows.get(query.get("_id"))
        return deepcopy(row) if row else None

    async def update_one(self, query, update, upsert=False):
        key = query["_id"]
        created = key not in self.rows
        row = deepcopy(self.rows.get(key) or {})
        if created:
            row.update(deepcopy(update.get("$setOnInsert") or {}))
        row.update(deepcopy(update.get("$set") or {}))
        for field in (update.get("$inc") or {}):
            row[field] = row.get(field, 0) + update["$inc"][field]
        self.rows[key] = row
        return _Result(upserted_id=key if created else None)


class _DB:
    def __init__(self):
        self.salla_orders_v3_events = _Collection()
        self.salla_orders_v3_jobs = _Collection()


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

    first = await process_shadow_job(db, deepcopy(job), engine=engine, now=datetime.now(timezone.utc))
    job_after_failure = next(iter(db.salla_orders_v3_jobs.rows.values()))
    second = await process_shadow_job(
        db,
        deepcopy(job_after_failure),
        engine=engine,
        now=datetime.now(timezone.utc),
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
