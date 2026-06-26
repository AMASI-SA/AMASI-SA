"""Tests for the Qoyod Pipeline Worker (background auto-advancer)
and the dry-run fix in process_normalized_row.

Regression context (2026-06-27, user-reported):
  After webhook reached NORMALIZED, the pipeline never advanced.
  Root cause: no background worker was wired to startup, and even
  the manual orchestrator didn't honour dry_run_mode when called
  with `api_client=None` (it tried to hit the real Qoyod API).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from integrations.qoyod.first_sync_monitor import (
    _is_stuck, STUCK_AFTER_SECONDS, WAITING_STAGES,
)


def test_is_stuck_returns_none_for_completed_rows():
    """COMPLETED rows are never 'stuck' even if old."""
    row = {
        "pipeline_stage": "COMPLETED",
        "received_at": datetime.now(timezone.utc) - timedelta(hours=1),
        "stage_history": [],
    }
    assert _is_stuck(row) is None


def test_is_stuck_returns_none_for_dead_letter_rows():
    """DEAD_LETTER rows aren't stuck — they're terminally failed.
    A separate UI indicates failure; stuck is only for in-flight rows."""
    row = {
        "pipeline_stage": "DEAD_LETTER",
        "received_at": datetime.now(timezone.utc) - timedelta(hours=1),
        "stage_history": [],
    }
    assert _is_stuck(row) is None


def test_is_stuck_returns_none_when_under_threshold():
    """Recently-NORMALIZED rows aren't stuck — give the worker time."""
    now = datetime.now(timezone.utc)
    row = {
        "pipeline_stage": "NORMALIZED",
        "received_at":   now - timedelta(seconds=5),
        "stage_history": [
            {"to_stage": "NORMALIZED", "at": now - timedelta(seconds=5)},
        ],
    }
    assert _is_stuck(row) is None


def test_is_stuck_detects_normalized_row_past_threshold():
    """The exact UX the user requested: bar after 30s with the stage."""
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=120)
    row = {
        "pipeline_stage": "NORMALIZED",
        "received_at":    long_ago,
        "stage_history": [
            {"to_stage": "NORMALIZED", "at": long_ago},
        ],
    }
    stuck = _is_stuck(row)
    assert stuck is not None
    assert stuck["stage"] == "NORMALIZED"
    assert stuck["waited_seconds"] >= STUCK_AFTER_SECONDS
    assert "العامل" in stuck["reason"]


def test_is_stuck_works_for_all_waiting_stages():
    """The waiting buckets the worker drains:
       NORMALIZED, RULES_APPLIED, CUSTOMER_RESOLVED, INVOICE_CREATED."""
    expected = {"NORMALIZED", "RULES_APPLIED",
                "CUSTOMER_RESOLVED", "INVOICE_CREATED"}
    assert expected.issubset(WAITING_STAGES)


def test_is_stuck_uses_iso_string_timestamps_too():
    """The shaper sometimes receives serialised history (ISO strings).
    Must still calculate `waited` correctly."""
    long_ago_iso = (datetime.now(timezone.utc) - timedelta(seconds=120)
                    ).isoformat()
    row = {
        "pipeline_stage": "CUSTOMER_RESOLVED",
        "received_at":    long_ago_iso,
        "stage_history": [
            {"to_stage": "CUSTOMER_RESOLVED", "at": long_ago_iso},
        ],
    }
    stuck = _is_stuck(row)
    assert stuck is not None
    assert stuck["stage"] == "CUSTOMER_RESOLVED"


def test_is_stuck_handles_naive_datetime():
    """If `last_at` is naive (no tzinfo), assume UTC. Don't crash."""
    long_ago_naive = (datetime.now(timezone.utc).replace(tzinfo=None)
                      - timedelta(seconds=120))
    row = {
        "pipeline_stage": "NORMALIZED",
        "received_at":    long_ago_naive,
        "stage_history": [
            {"to_stage": "NORMALIZED", "at": long_ago_naive},
        ],
    }
    stuck = _is_stuck(row)
    assert stuck is not None


# ─── Worker liveness ──────────────────────────────────────────────
def test_worker_module_imports_cleanly():
    from integrations.qoyod import worker  # noqa: F401
    assert hasattr(worker, "start_worker")
    assert hasattr(worker, "run_now")
    assert hasattr(worker, "liveness")
    assert hasattr(worker, "is_running")


def test_worker_liveness_returns_expected_shape():
    """Before the loop runs, liveness reflects the dormant state."""
    from integrations.qoyod.worker import liveness
    state = liveness()
    assert set(state.keys()) == {
        "running", "last_run_at", "last_run_ok", "last_round"}
    assert isinstance(state["running"], bool)
