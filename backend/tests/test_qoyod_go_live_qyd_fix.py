"""Tests for the QYD-GO 3-bugs fix (2026-06-27) — refined 2026-02-26.

  1. Branch ID is now OPTIONAL — single-branch accounts must pass.
  2. `outstanding_failures` ONLY counts production failures created
     AFTER `go_live_activated_at` (the activation watermark).
     Pre-activation rows are test data by definition and never block.
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
    """Tiny fake — only the bits `_check_outstanding_failures` needs."""
    def __init__(self):
        self.integration_inbox = _FakeColl()


# Reusable activation watermark = 1 hour ago. Any row with
# `received_at` ≥ this is "post-activation".
_ACTIVATED_AT = datetime.now(timezone.utc) - timedelta(hours=1)
_AFTER  = _ACTIVATED_AT + timedelta(minutes=5)
_BEFORE = _ACTIVATED_AT - timedelta(hours=24)


@pytest.mark.asyncio
async def test_pre_go_live_always_passes_even_with_failures():
    """No `go_live_activated_at` → check always green. Old test rows
    cannot block a tenant that hasn't even activated yet."""
    db = _FakeDB()
    db.integration_inbox.rows = [
        # Lots of "production failures" — must all be ignored pre-activation.
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",     "dry_run": False},
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",     "dry_run": False},
        {"user_id": "u1", "pipeline_stage": "PARTIAL_FAILURE", "dry_run": False},
        # Rows with missing dry_run flag (the user's repro scenario).
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER"},
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER"},
    ]
    res = await _check_outstanding_failures(db, "u1", settings={})
    assert res["ok"] is True
    assert "تفعيل" in res["detail"]


@pytest.mark.asyncio
async def test_post_go_live_counts_only_post_activation_production_failures():
    """The whole spec in one test."""
    db = _FakeDB()
    db.integration_inbox.rows = [
        # ✗ Pre-activation production failure — ignored.
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "dry_run": False, "received_at": _BEFORE},
        # ✗ Pre-activation missing dry_run — ignored.
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "received_at": _BEFORE},
        # ✗ Post-activation but dry_run — ignored.
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "dry_run": True, "received_at": _AFTER},
        # ✓ Post-activation production — COUNTS.
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "dry_run": False, "received_at": _AFTER},
        {"user_id": "u1", "pipeline_stage": "PARTIAL_FAILURE",
         "dry_run": False, "received_at": _AFTER},
    ]
    res = await _check_outstanding_failures(
        db, "u1", settings={"go_live_activated_at": _ACTIVATED_AT})
    assert res["ok"] is False
    assert res["extra"]["stuck_count"] == 2


@pytest.mark.asyncio
async def test_post_go_live_passes_when_no_post_activation_failures():
    db = _FakeDB()
    db.integration_inbox.rows = [
        # All pre-activation — should pass.
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "dry_run": False, "received_at": _BEFORE},
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "received_at": _BEFORE},
    ]
    res = await _check_outstanding_failures(
        db, "u1", settings={"go_live_activated_at": _ACTIVATED_AT})
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_legacy_activated_at_field_is_honoured():
    """Backward compat: settings written before this iter used
    `activated_at` (not `go_live_activated_at`). Both must work."""
    db = _FakeDB()
    db.integration_inbox.rows = [
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "dry_run": False, "received_at": _AFTER},
    ]
    res = await _check_outstanding_failures(
        db, "u1", settings={"activated_at": _ACTIVATED_AT})
    assert res["ok"] is False
    assert res["extra"]["stuck_count"] == 1


@pytest.mark.asyncio
async def test_iso_string_activation_timestamp_is_parsed():
    """Mongo stores datetimes as BSON Date by default, but some legacy
    code paths persisted ISO strings. Must still be honoured."""
    db = _FakeDB()
    db.integration_inbox.rows = [
        {"user_id": "u1", "pipeline_stage": "DEAD_LETTER",
         "dry_run": False, "received_at": _AFTER},
    ]
    res = await _check_outstanding_failures(
        db, "u1",
        settings={"go_live_activated_at": _ACTIVATED_AT.isoformat()})
    assert res["ok"] is False


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
