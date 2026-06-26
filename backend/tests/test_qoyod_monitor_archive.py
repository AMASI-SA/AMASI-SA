"""Tests for the Qoyod First-Sync Monitor stats + archive-failed-tests
endpoints (sidebar-integration phase, Feb 2026).

Coverage:
    1. `get_monitor_stats` buckets rows correctly across all stages,
       including the `dry_failed` subset.
    2. `archive_failed_dry_run_tests` strictly archives ONLY rows that
       are both (a) DEAD_LETTER or PARTIAL_FAILURE, and (b) dry_run=True.
    3. COMPLETED rows + non-dry failed rows + processing rows are
       NEVER touched.
    4. Confirm-token enforcement raises `ArchiveRefused` when missing
       or wrong.
    5. After archive: matched rows live in `integration_inbox_archive`
       and are removed from `integration_inbox`.
"""
from __future__ import annotations

import os
import uuid
import pytest
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.first_sync_monitor import (
    get_monitor_stats,
    archive_failed_dry_run_tests,
    ArchiveRefused,
    ARCHIVE_CONFIRM_TOKEN,
)


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture
def tenant():
    # Use a unique tenant per run so we never collide with real data.
    return f"test-monitor-{uuid.uuid4().hex[:8]}"


async def _seed(db, tenant: str, rows: list[dict]) -> None:
    for i, r in enumerate(rows):
        r.setdefault("user_id", tenant)
        r.setdefault("trace_id", uuid.uuid4().hex)
        r.setdefault("id",       uuid.uuid4().hex)
        r.setdefault("received_at", datetime.now(timezone.utc))
        r.setdefault("connector_key", "qoyod")
        r.setdefault("idempotency_key", f"test-{tenant}-{i}-{uuid.uuid4().hex}")
    if rows:
        await db.integration_inbox.insert_many(rows)


async def _cleanup(db, tenant: str) -> None:
    await db.integration_inbox.delete_many({"user_id": tenant})
    await db.integration_inbox_archive.delete_many({"user_id": tenant})


# ─── 1) Stats buckets ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_stats_buckets_each_pipeline_stage(db, tenant):
    try:
        await _seed(db, tenant, [
            {"pipeline_stage": "COMPLETED",       "dry_run": False},
            {"pipeline_stage": "COMPLETED",       "dry_run": True},
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": True},
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": False},
            {"pipeline_stage": "PARTIAL_FAILURE", "dry_run": True},
            {"pipeline_stage": "SKIPPED",         "dry_run": False},
            {"pipeline_stage": "NORMALIZED",      "dry_run": True},
            {"pipeline_stage": "CUSTOMER_RESOLVED", "dry_run": True},
        ])
        stats = await get_monitor_stats(db, user_id=tenant)
        assert stats["total"]      == 8
        assert stats["success"]    == 2  # both COMPLETED
        assert stats["failed"]     == 3  # 2× DEAD_LETTER + 1× PARTIAL
        assert stats["dry_failed"] == 2  # 1× DEAD_LETTER dry + 1× PARTIAL
        assert stats["skipped"]    == 1
        assert stats["processing"] == 2  # NORMALIZED + CUSTOMER_RESOLVED
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_stats_empty_tenant_returns_zeros(db, tenant):
    stats = await get_monitor_stats(db, user_id=tenant)
    assert stats == {"processing": 0, "failed": 0, "success": 0,
                     "skipped": 0, "dry_failed": 0, "total": 0}


# ─── 2) Archive — confirm token enforcement ──────────────────────────
@pytest.mark.asyncio
async def test_archive_refuses_without_confirm_token(db, tenant):
    with pytest.raises(ArchiveRefused):
        await archive_failed_dry_run_tests(
            db, user_id=tenant, confirm_token="", actor="t")
    with pytest.raises(ArchiveRefused):
        await archive_failed_dry_run_tests(
            db, user_id=tenant, confirm_token="clean", actor="t")  # lowercase
    with pytest.raises(ArchiveRefused):
        await archive_failed_dry_run_tests(
            db, user_id=tenant, confirm_token="DELETE", actor="t")


@pytest.mark.asyncio
async def test_archive_accepts_canonical_token(db, tenant):
    try:
        # Empty tenant — confirm token is still validated even when
        # there's nothing to archive.
        res = await archive_failed_dry_run_tests(
            db, user_id=tenant,
            confirm_token=ARCHIVE_CONFIRM_TOKEN, actor="t")
        assert res == {"matched": 0, "archived": 0,
                       "deleted": 0, "archive_ids": []}
    finally:
        await _cleanup(db, tenant)


# ─── 3) Archive — strict filter (the safety contract) ────────────────
@pytest.mark.asyncio
async def test_archive_only_touches_dry_run_failed_rows(db, tenant):
    """Critical safety test: archive must NEVER touch
       COMPLETED rows, NEVER touch real (non-dry) failed rows, and
       NEVER touch in-flight processing rows.
    """
    try:
        await _seed(db, tenant, [
            # ✗ Should NOT be archived — wrong stage
            {"pipeline_stage": "COMPLETED",       "dry_run": True,
             "trace_id": "keep-1"},
            {"pipeline_stage": "COMPLETED",       "dry_run": False,
             "trace_id": "keep-2"},
            {"pipeline_stage": "SKIPPED",         "dry_run": True,
             "trace_id": "keep-3"},
            {"pipeline_stage": "NORMALIZED",      "dry_run": True,
             "trace_id": "keep-4"},
            # ✗ Should NOT be archived — production (non-dry) failed
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": False,
             "trace_id": "keep-5"},
            {"pipeline_stage": "PARTIAL_FAILURE", "dry_run": False,
             "trace_id": "keep-6"},
            # ✓ SHOULD be archived — failed AND dry-run
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": True,
             "trace_id": "archive-1"},
            {"pipeline_stage": "PARTIAL_FAILURE", "dry_run": True,
             "trace_id": "archive-2"},
        ])

        res = await archive_failed_dry_run_tests(
            db, user_id=tenant,
            confirm_token=ARCHIVE_CONFIRM_TOKEN, actor="qa")

        assert res["matched"]  == 2
        assert res["archived"] == 2
        assert res["deleted"]  == 2

        # Verify the live collection still has all "keep-*" rows.
        live = await db.integration_inbox.find(
            {"user_id": tenant}).to_list(length=100)
        live_traces = {r["trace_id"] for r in live}
        assert live_traces == {
            "keep-1", "keep-2", "keep-3", "keep-4", "keep-5", "keep-6"}

        # Verify the archive collection has exactly the 2 archived rows
        # with the stamped metadata.
        arch = await db.integration_inbox_archive.find(
            {"user_id": tenant}).to_list(length=100)
        arch_traces = {r["trace_id"] for r in arch}
        assert arch_traces == {"archive-1", "archive-2"}
        for r in arch:
            assert r["archive_reason"] == "dry_run_failed_test_cleanup"
            assert r["archived_by"]    == "qa"
            assert isinstance(r["archived_at"], str)
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_archive_idempotent_when_no_matches(db, tenant):
    try:
        await _seed(db, tenant, [
            {"pipeline_stage": "COMPLETED", "dry_run": True,
             "trace_id": "ok-1"},
            {"pipeline_stage": "DEAD_LETTER", "dry_run": False,
             "trace_id": "ok-2"},  # production failure — protected
        ])
        res = await archive_failed_dry_run_tests(
            db, user_id=tenant,
            confirm_token=ARCHIVE_CONFIRM_TOKEN, actor="qa")
        assert res["matched"] == 0
        # Live rows still intact.
        live = await db.integration_inbox.find(
            {"user_id": tenant}).to_list(length=100)
        assert len(live) == 2
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_archive_does_not_leak_across_tenants(db, tenant):
    """Archive for tenant A must not touch tenant B's rows even if
    they match the strict filter."""
    other = f"test-monitor-other-{uuid.uuid4().hex[:6]}"
    try:
        await _seed(db, tenant, [
            {"pipeline_stage": "DEAD_LETTER", "dry_run": True,
             "trace_id": "a-1"},
        ])
        await _seed(db, other, [
            {"pipeline_stage": "DEAD_LETTER", "dry_run": True,
             "trace_id": "b-1"},
        ])
        res = await archive_failed_dry_run_tests(
            db, user_id=tenant,
            confirm_token=ARCHIVE_CONFIRM_TOKEN, actor="qa")
        assert res["matched"] == 1
        # Other tenant's failing row is untouched.
        other_rows = await db.integration_inbox.find(
            {"user_id": other}).to_list(length=10)
        assert len(other_rows) == 1
        assert other_rows[0]["trace_id"] == "b-1"
    finally:
        await _cleanup(db, tenant)
        await _cleanup(db, other)
