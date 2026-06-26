"""Tests for the QYD-GO 3-bugs fix (2026-06-27):

  1. Branch ID is now OPTIONAL — single-branch accounts must pass.
  2. `outstanding_failures` ignores dry-run failures and counts ONLY
     production (`dry_run != True`) failures (per user spec 2026-02-26).
  3. `eligible_orders` recognises recent COMPLETED dry-run rows as proof
     the pipeline is healthy (was always 0 after the worker fix).
  4. `dry_run_proven` reads `integration_inbox` (not legacy `qoyod_invoices`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from integrations.qoyod.go_live import (
    _check_branch, _check_outstanding_failures,
    _check_eligible_orders, _check_dry_run_proven,
)


# ─── Branch (#1) ──────────────────────────────────────────────────
def test_branch_passes_when_blank_single_branch_account():
    """Per user spec 2026-06-27: single-branch accounts must not be
    blocked. Empty branch is valid; Qoyod auto-fills."""
    out = _check_branch({})
    assert out["ok"] is True
    assert "اختياري" in out["detail"]


def test_branch_passes_when_set():
    out = _check_branch({"default_branch_id": "BR-1"})
    assert out["ok"] is True
    assert "BR-1" in out["detail"]


def test_branch_passes_for_empty_string_value():
    out = _check_branch({"default_branch_id": ""})
    assert out["ok"] is True
    assert "اختياري" in out["detail"]


# ─── Outstanding failures (#2) ────────────────────────────────────
class _FakeColl:
    def __init__(self, rows=None):
        self.rows = rows or []
    async def count_documents(self, query):
        def _get(r, path):
            parts = path.split(".")
            v = r
            for p in parts:
                if not isinstance(v, dict):
                    return None
                v = v.get(p)
            return v
        def _match(r):
            for k, v in query.items():
                rv = _get(r, k)
                if isinstance(v, dict):
                    if "$in" in v and rv not in v["$in"]:
                        return False
                    if "$ne" in v and rv == v["$ne"]:
                        return False
                    if "$gte" in v and (rv is None or rv < v["$gte"]):
                        return False
                elif rv != v:
                    return False
            return True
        return sum(1 for r in self.rows if _match(r))


class _FakeDB:
    def __init__(self):
        self.integration_inbox = _FakeColl()


@pytest.mark.asyncio
async def test_outstanding_failures_ignores_dry_run_failures():
    """Spec 2026-02-26: dry-run failures NEVER block Go-Live.
    Only production failures (dry_run != True) are counted."""
    db = _FakeDB()
    db.integration_inbox.rows = [
        # Dry-run failures — must be ignored.
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "dry_run": True},
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "dry_run": True},
        {"user_id": "u1", "pipeline_stage": "PARTIAL_FAILURE",
         "dry_run": True},
        # Production failures — must count.
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "dry_run": False},
        {"user_id": "u1", "pipeline_stage": "PARTIAL_FAILURE",
         "dry_run": False},
    ]
    res = await _check_outstanding_failures(db, "u1")
    assert res["ok"] is False
    assert res["extra"]["stuck_count"] == 2  # only the 2 production failures


@pytest.mark.asyncio
async def test_outstanding_failures_passes_when_only_dry_run_failures():
    """If the ONLY failures are dry-run noise, the check passes."""
    db = _FakeDB()
    db.integration_inbox.rows = [
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "dry_run": True},
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "dry_run": True},
        {"user_id": "u1", "pipeline_stage": "PARTIAL_FAILURE",
         "dry_run": True},
    ]
    res = await _check_outstanding_failures(db, "u1")
    assert res["ok"] is True
    assert "Dry Run" in res["detail"]


@pytest.mark.asyncio
async def test_outstanding_failures_treats_missing_dry_run_as_production():
    """Defensive: a row without `dry_run` field is treated as production
    (the safer interpretation — it blocks)."""
    db = _FakeDB()
    db.integration_inbox.rows = [
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER"},  # no dry_run key
    ]
    res = await _check_outstanding_failures(db, "u1")
    assert res["ok"] is False
    assert res["extra"]["stuck_count"] == 1


# ─── Eligible orders (#3) ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_eligible_orders_passes_with_inflight_rows():
    db = _FakeDB()
    settings = {"invoice_trigger_statuses": ["completed"]}
    res = await _check_eligible_orders(
        db, "u1", eligible_count=3, settings=settings)
    assert res["ok"] is True
    assert "3" in res["detail"]


@pytest.mark.asyncio
async def test_eligible_orders_passes_with_recent_dry_completion():
    """After the worker fix, rows drain quickly out of NORMALIZED. The
    check must still pass if there's a recent COMPLETED dry-run row
    matching a trigger status."""
    db = _FakeDB()
    db.integration_inbox.rows = [
        {"user_id": "u1", "pipeline_stage": "COMPLETED",
         "dry_run": True,
         "canonical_payload": {"order_status": "completed"},
         "received_at": datetime.now(timezone.utc) - timedelta(minutes=5)},
    ]
    settings = {"invoice_trigger_statuses": ["completed"]}
    res = await _check_eligible_orders(
        db, "u1", eligible_count=0, settings=settings)
    assert res["ok"] is True
    assert "Dry Run" in res["detail"]


@pytest.mark.asyncio
async def test_eligible_orders_fails_when_nothing_eligible_or_recent():
    db = _FakeDB()
    db.integration_inbox.rows = []
    settings = {"invoice_trigger_statuses": ["completed"]}
    res = await _check_eligible_orders(
        db, "u1", eligible_count=0, settings=settings)
    assert res["ok"] is False


# ─── Dry-run proven from integration_inbox (#4) ───────────────────
@pytest.mark.asyncio
async def test_dry_run_proven_reads_integration_inbox():
    """Bug fix: legacy `qoyod_invoices` collection was never populated
    by the new pipeline. We now read `integration_inbox` instead."""
    db = _FakeDB()
    db.integration_inbox.rows = [
        {"user_id": "u1", "pipeline_stage": "COMPLETED", "dry_run": True},
        {"user_id": "u1", "pipeline_stage": "COMPLETED", "dry_run": True},
    ]
    settings = {"dry_run_mode": True}
    res = await _check_dry_run_proven(db, "u1", settings)
    assert res["ok"] is True
    assert "2" in res["detail"]


@pytest.mark.asyncio
async def test_dry_run_fails_when_dry_run_mode_off():
    db = _FakeDB()
    res = await _check_dry_run_proven(db, "u1", {"dry_run_mode": False})
    assert res["ok"] is False
    assert "غير مُفعّل" in res["detail"]
