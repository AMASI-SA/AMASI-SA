"""Iter-293 — Webhook Activity Log tests.

Coverage:
 1. Helper writes a row on every call.
 2. List filters by event_type / order_id / skipped_only.
 3. Counts aggregate: total / accepted / skipped / errors / by_event.
 4. Soft cap trims older rows past `keep`.
 5. Failure in the log path NEVER raises (best-effort).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from integrations.qoyod.models import ensure_qoyod_indexes
from integrations.qoyod.webhook_activity import (
    record_webhook_event, list_recent_events,
    get_event_counts, soft_cap_old_rows,
)


@pytest.fixture
def db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup(db):
    await ensure_qoyod_indexes(db)
    await db.qoyod_webhook_events.delete_many({"user_id": "iter293"})


@pytest.mark.asyncio
async def test_record_inserts_one_row_per_call(db):
    await _setup(db)
    await record_webhook_event(
        db, user_id="iter293", trace_id="t1",
        event_type="order_completed", salla_order_id="111",
        items_parsed_ok=True, items_count=2,
        skipped_reason=None, target_inbox_row_id="row-a",
        pipeline_stage_after="COMPLETED", http_response_status=200,
        raw_payload_size=4096)
    rows = await list_recent_events(db, user_id="iter293", limit=10)
    assert len(rows) == 1
    r = rows[0]
    assert r["event_type"] == "order_completed"
    assert r["salla_order_id"] == "111"
    assert r["items_count"] == 2
    assert r["pipeline_stage_after"] == "COMPLETED"
    assert r["http_response_status"] == 200


@pytest.mark.asyncio
async def test_list_filters_by_event_type(db):
    await _setup(db)
    for et in ["order_completed", "order_updated", "order_completed"]:
        await record_webhook_event(
            db, user_id="iter293", trace_id=uuid.uuid4().hex,
            event_type=et, salla_order_id=None,
            items_parsed_ok=True, items_count=1,
            skipped_reason=None, target_inbox_row_id=None,
            pipeline_stage_after=None, http_response_status=200,
            raw_payload_size=100)
    completed = await list_recent_events(
        db, user_id="iter293", event_type="order_completed", limit=10)
    updated = await list_recent_events(
        db, user_id="iter293", event_type="order_updated", limit=10)
    assert len(completed) == 2
    assert len(updated) == 1


@pytest.mark.asyncio
async def test_list_filters_by_order_id(db):
    await _setup(db)
    await record_webhook_event(
        db, user_id="iter293", trace_id="t1", event_type="order_completed",
        salla_order_id="42", items_parsed_ok=True, items_count=1,
        skipped_reason=None, target_inbox_row_id=None,
        pipeline_stage_after=None, http_response_status=200, raw_payload_size=10)
    await record_webhook_event(
        db, user_id="iter293", trace_id="t2", event_type="order_completed",
        salla_order_id="43", items_parsed_ok=True, items_count=1,
        skipped_reason=None, target_inbox_row_id=None,
        pipeline_stage_after=None, http_response_status=200, raw_payload_size=10)
    rows = await list_recent_events(
        db, user_id="iter293", salla_order_id="42", limit=10)
    assert len(rows) == 1
    assert rows[0]["salla_order_id"] == "42"


@pytest.mark.asyncio
async def test_list_filters_skipped_only(db):
    await _setup(db)
    await record_webhook_event(
        db, user_id="iter293", trace_id="ok",
        event_type="order_completed", salla_order_id=None,
        items_parsed_ok=True, items_count=1,
        skipped_reason=None, target_inbox_row_id=None,
        pipeline_stage_after=None, http_response_status=200, raw_payload_size=10)
    await record_webhook_event(
        db, user_id="iter293", trace_id="skip",
        event_type="order_created", salla_order_id=None,
        items_parsed_ok=True, items_count=None,
        skipped_reason="duplicate_idempotency_key", target_inbox_row_id=None,
        pipeline_stage_after=None, http_response_status=200, raw_payload_size=10)
    rows = await list_recent_events(
        db, user_id="iter293", skipped_only=True, limit=10)
    assert len(rows) == 1
    assert rows[0]["skipped_reason"] == "duplicate_idempotency_key"


@pytest.mark.asyncio
async def test_counts_aggregates_accepted_skipped_errors(db):
    await _setup(db)
    # 2 accepted
    for _ in range(2):
        await record_webhook_event(
            db, user_id="iter293", trace_id=uuid.uuid4().hex,
            event_type="order_completed", salla_order_id=None,
            items_parsed_ok=True, items_count=1, skipped_reason=None,
            target_inbox_row_id=None, pipeline_stage_after=None,
            http_response_status=200, raw_payload_size=10)
    # 1 skipped
    await record_webhook_event(
        db, user_id="iter293", trace_id="x",
        event_type="order_updated", salla_order_id=None,
        items_parsed_ok=True, items_count=None,
        skipped_reason="dup", target_inbox_row_id=None,
        pipeline_stage_after=None, http_response_status=200, raw_payload_size=10)
    # 1 error (http 500)
    await record_webhook_event(
        db, user_id="iter293", trace_id="y",
        event_type="order_completed", salla_order_id=None,
        items_parsed_ok=False, items_count=None,
        skipped_reason=None, target_inbox_row_id=None,
        pipeline_stage_after="DEAD_LETTER",
        http_response_status=500, raw_payload_size=10)
    counts = await get_event_counts(db, user_id="iter293", since_hours=1)
    assert counts["total"] == 4
    assert counts["accepted"] == 2
    assert counts["skipped"] == 1
    assert counts["errors"] == 1
    assert counts["by_event"].get("order_completed") == 3
    assert counts["by_event"].get("order_updated") == 1


@pytest.mark.asyncio
async def test_soft_cap_trims_older_rows(db):
    await _setup(db)
    # Insert 55 rows; keep=50 → 5 oldest should be deleted.
    for i in range(55):
        await record_webhook_event(
            db, user_id="iter293", trace_id=f"r{i}",
            event_type="order_completed", salla_order_id=None,
            items_parsed_ok=True, items_count=1, skipped_reason=None,
            target_inbox_row_id=None, pipeline_stage_after=None,
            http_response_status=200, raw_payload_size=10)
    deleted = await soft_cap_old_rows(db, user_id="iter293", keep=50)
    remaining = await list_recent_events(db, user_id="iter293", limit=200)
    assert deleted == 5
    assert len(remaining) == 50


@pytest.mark.asyncio
async def test_record_never_raises_on_db_error(db):
    """If the collection is dropped mid-call, record_webhook_event must
    NOT raise — the live pipeline must never be disrupted by audit log
    failures."""
    # Pass a deliberately broken db (None) — should swallow the error.
    await record_webhook_event(
        db=None,  # type: ignore[arg-type]
        user_id="iter293", trace_id="x", event_type="x",
        salla_order_id=None, items_parsed_ok=True, items_count=None,
        skipped_reason=None, target_inbox_row_id=None,
        pipeline_stage_after=None, http_response_status=200,
        raw_payload_size=0,
    )
    # If we got here without exception, the test passes.
    assert True
