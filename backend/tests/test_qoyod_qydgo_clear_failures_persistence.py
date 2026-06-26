"""Integration tests for the watermark-based `_check_outstanding_failures`.

User spec (2026-02-26, final):
    • Pre-Go-Live (no `go_live_activated_at`) — check always passes;
      old test rows can never block activation.
    • Post-Go-Live — only rows with `received_at ≥ go_live_activated_at`
      AND `dry_run != True` count as production failures.
    • Refresh stability: successive checks return the same result.
"""
from __future__ import annotations

import os
import uuid
import pytest
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.go_live import _check_outstanding_failures


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture
def tenant():
    return f"qydgo-test-{uuid.uuid4().hex[:8]}"


async def _seed(db, tenant: str, rows: list[dict]) -> None:
    for i, r in enumerate(rows):
        r.setdefault("user_id", tenant)
        r.setdefault("trace_id", uuid.uuid4().hex)
        r.setdefault("id",       uuid.uuid4().hex)
        r.setdefault("connector_key", "qoyod")
        r.setdefault("idempotency_key",
                     f"qydgo-{tenant}-{i}-{uuid.uuid4().hex}")
        r.setdefault("received_at", datetime.now(timezone.utc))
    if rows:
        await db.integration_inbox.insert_many(rows)


async def _cleanup(db, tenant: str) -> None:
    await db.integration_inbox.delete_many({"user_id": tenant})


@pytest.mark.asyncio
async def test_pre_go_live_no_failures_count(db, tenant):
    """The exact user-reported bug: 27 old test rows, no Go-Live
    activation yet. Check must stay green regardless of dry_run flag."""
    try:
        await _seed(db, tenant, [
            {"pipeline_stage": "DEAD_LETTER", "dry_run": False},
            {"pipeline_stage": "DEAD_LETTER", "dry_run": True},
            # legacy row — no dry_run field at all
            {"pipeline_stage": "DEAD_LETTER"},
            {"pipeline_stage": "PARTIAL_FAILURE"},
        ])
        # No settings → no go_live_activated_at → ALWAYS green.
        res = await _check_outstanding_failures(db, tenant, settings={})
        assert res["ok"] is True
        assert "تفعيل" in res["detail"]
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_post_go_live_pre_activation_rows_ignored(db, tenant):
    """Activation watermark = 30 minutes ago. Pre-activation rows
    must NOT count, even if they look like production failures."""
    try:
        activated_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        before = activated_at - timedelta(hours=24)
        after  = activated_at + timedelta(minutes=1)
        await _seed(db, tenant, [
            # Pre-activation — ignored.
            {"pipeline_stage": "DEAD_LETTER", "dry_run": False,
             "received_at": before},
            {"pipeline_stage": "DEAD_LETTER", "received_at": before},
            # Post-activation dry-run — ignored.
            {"pipeline_stage": "DEAD_LETTER", "dry_run": True,
             "received_at": after},
        ])
        res = await _check_outstanding_failures(
            db, tenant, settings={"go_live_activated_at": activated_at})
        assert res["ok"] is True
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_post_go_live_post_activation_production_failures_block(db, tenant):
    try:
        activated_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        before = activated_at - timedelta(hours=24)
        after  = activated_at + timedelta(minutes=1)
        await _seed(db, tenant, [
            # Pre-activation production — ignored.
            {"pipeline_stage": "DEAD_LETTER", "dry_run": False,
             "received_at": before},
            # Post-activation production — COUNTED.
            {"pipeline_stage": "DEAD_LETTER", "dry_run": False,
             "received_at": after},
            {"pipeline_stage": "PARTIAL_FAILURE", "dry_run": False,
             "received_at": after},
        ])
        res = await _check_outstanding_failures(
            db, tenant, settings={"go_live_activated_at": activated_at})
        assert res["ok"] is False
        assert res["extra"]["stuck_count"] == 2
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_refresh_stability_pre_go_live(db, tenant):
    """User's repro: 27 old test rows + many refreshes. Check must
    stay green stably (no flicker)."""
    try:
        await _seed(db, tenant, [
            {"pipeline_stage": "DEAD_LETTER"} for _ in range(27)
        ])
        for _ in range(5):
            res = await _check_outstanding_failures(db, tenant, settings={})
            assert res["ok"] is True
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_legacy_activated_at_field_works(db, tenant):
    """Tenants activated before this iter may have only the legacy
    `activated_at` field. Must still trigger the watermark."""
    try:
        activated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        after = activated_at + timedelta(seconds=1)
        await _seed(db, tenant, [
            {"pipeline_stage": "DEAD_LETTER", "dry_run": False,
             "received_at": after},
        ])
        res = await _check_outstanding_failures(
            db, tenant, settings={"activated_at": activated_at})
        assert res["ok"] is False
        assert res["extra"]["stuck_count"] == 1
    finally:
        await _cleanup(db, tenant)
