"""Tests for the new QYD-GO `_check_outstanding_failures` behaviour
(2026-02-26 user spec).

Spec
────
Dry-run failures NEVER block Go-Live. Only production failures
(`dry_run != True`) are counted by `_check_outstanding_failures`.
The operator archives stale dry-run failures from the First-Sync Monitor
page (separate flow, already built).

These tests reproduce the user's exact workflow on real MongoDB:
    • Seed dry-run failures → check returns ok=True (page stays green).
    • Seed production failures → check returns ok=False (page red).
    • Successive calls (= page refresh) keep returning the same result
      — no false-positive RED after refresh.
"""
from __future__ import annotations

import os
import uuid
import pytest
from datetime import datetime, timezone
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


# ─── Dry-run failures never block ────────────────────────────────────
@pytest.mark.asyncio
async def test_dry_run_failures_do_not_block(db, tenant):
    """The user's main complaint reframed: even with multiple dry-run
    DEAD_LETTER / PARTIAL_FAILURE rows in the inbox, Go-Live readiness
    should stay GREEN. No magic button needed.
    """
    try:
        await _seed(db, tenant, [
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": True},
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": True},
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": True},
            {"pipeline_stage": "PARTIAL_FAILURE", "dry_run": True},
        ])
        res = await _check_outstanding_failures(db, tenant)
        assert res["ok"] is True, f"expected ok but got {res}"
        assert "Dry Run" in res["detail"]
    finally:
        await _cleanup(db, tenant)


# ─── Production failures always block ────────────────────────────────
@pytest.mark.asyncio
async def test_production_failures_always_block(db, tenant):
    try:
        await _seed(db, tenant, [
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": False},
            {"pipeline_stage": "PARTIAL_FAILURE", "dry_run": False},
        ])
        res = await _check_outstanding_failures(db, tenant)
        assert res["ok"] is False
        assert res["extra"]["stuck_count"] == 2
    finally:
        await _cleanup(db, tenant)


# ─── Mixed: only production count ────────────────────────────────────
@pytest.mark.asyncio
async def test_mixed_failures_count_only_production(db, tenant):
    try:
        await _seed(db, tenant, [
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": True},
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": True},
            {"pipeline_stage": "PARTIAL_FAILURE", "dry_run": True},
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": False},
        ])
        res = await _check_outstanding_failures(db, tenant)
        assert res["ok"] is False
        assert res["extra"]["stuck_count"] == 1
    finally:
        await _cleanup(db, tenant)


# ─── Refresh stability ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_refresh_stability_with_only_dry_failures(db, tenant):
    """The exact user-reported bug, rewritten for the new spec.
    Three successive calls (= refresh ×3) all return the same green
    result. There is no stateful exclusion mechanism that could be
    forgotten between calls.
    """
    try:
        await _seed(db, tenant, [
            {"pipeline_stage": "DEAD_LETTER", "dry_run": True},
            {"pipeline_stage": "DEAD_LETTER", "dry_run": True},
        ])
        for i in range(3):
            res = await _check_outstanding_failures(db, tenant)
            assert res["ok"] is True, f"call {i+1} flipped to red: {res}"
    finally:
        await _cleanup(db, tenant)


# ─── Worker-produced new dry-run failures still don't block ──────────
@pytest.mark.asyncio
async def test_worker_produced_dry_failures_still_dont_block(db, tenant):
    """Original symptom: after a click, worker added NEW dry-run
    DEAD_LETTER rows → page flipped to red. With the new spec the
    new dry-run rows are still ignored → page stays green.
    """
    try:
        await _seed(db, tenant, [
            {"pipeline_stage": "DEAD_LETTER", "dry_run": True}])
        assert (await _check_outstanding_failures(db, tenant))["ok"]
        # Simulate the worker adding 4 more dry-run DEAD_LETTER rows.
        await _seed(db, tenant, [
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": True},
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": True},
            {"pipeline_stage": "PARTIAL_FAILURE", "dry_run": True},
            {"pipeline_stage": "DEAD_LETTER",     "dry_run": True},
        ])
        # Page still green.
        assert (await _check_outstanding_failures(db, tenant))["ok"]
    finally:
        await _cleanup(db, tenant)
