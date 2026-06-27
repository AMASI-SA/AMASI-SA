"""Tests for Dead-Letter Forensics + force-requeue (Iter-266).

User scenario (2026-02-27, last Go-Live blocker):
    "إذا كانت ما زالت تفشل، أريد Log كامل لسبب الفشل الحالي،
     وليس السبب القديم."

The forensics endpoint must classify every stuck row precisely so
the operator can answer 'why is this stuck?' without touching the DB.
Force-requeue lets the operator override MAX_REQUEUE_ATTEMPTS for a
matched pattern (audit-trailed).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.dead_letter_requeue import (
    forensics, _classify_row, requeue_one, requeue_row,
    KNOWN_FIXED_PATTERNS, MAX_REQUEUE_ATTEMPTS,
)


@pytest.fixture
def db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture
def tenant():
    return f"test-fx-{uuid.uuid4().hex[:8]}"


async def _seed(db, tenant, **kw):
    row = {
        "id": uuid.uuid4().hex,
        "trace_id": uuid.uuid4().hex,
        "user_id": tenant,
        "connector_key": "qoyod",
        "idempotency_key": uuid.uuid4().hex,
        "received_at": datetime.now(timezone.utc),
        "pipeline_stage": "DEAD_LETTER",
        "last_failed_stage": "FAILED_CUSTOMER",
        "pipeline_error": {"details": {"contact_name": ["Can't be blank"]}},
        "dry_run": False,
        "requeue_attempts": 0,
        "stage_history": [],
        "canonical_payload": {"order_id": "X", "order_number": "Y"},
    }
    row.update(kw)
    await db.integration_inbox.insert_one(row)
    return row


async def _cleanup(db, tenant):
    await db.integration_inbox.delete_many({"user_id": tenant})
    await db.qoyod_settings.delete_many({"user_id": tenant})


# ─────────────────────────────────────────────────────────────────────
# _classify_row — pure function
# ─────────────────────────────────────────────────────────────────────
def test_classify_auto_recoverable_pending():
    row = {
        "pipeline_stage": "DEAD_LETTER",
        "last_failed_stage": "FAILED_CUSTOMER",
        "pipeline_error": {"details": {"contact_name": ["Can't be blank"]}},
        "requeue_attempts": 0,
    }
    c = _classify_row(row)
    assert c["status"] == "auto_recoverable_pending"
    assert c["pattern_id"] == "contact_name_blank_2026_02_26"


def test_classify_max_attempts_reached():
    row = {
        "pipeline_stage": "DEAD_LETTER",
        "last_failed_stage": "FAILED_CUSTOMER",
        "pipeline_error": {"details": {"contact_name": ["Can't be blank"]}},
        "requeue_attempts": MAX_REQUEUE_ATTEMPTS,
    }
    c = _classify_row(row)
    assert c["status"] == "max_attempts_reached"
    assert "force=true" in c["hint"]


def test_classify_no_pattern_match():
    row = {
        "pipeline_stage": "DEAD_LETTER",
        "last_failed_stage": "FAILED_VALIDATION",
        "pipeline_error": {"code": "total_missing",
                           "message": "order total is missing"},
        "requeue_attempts": 0,
    }
    c = _classify_row(row)
    assert c["status"] == "no_pattern_match"
    assert "النظام لا يُخفي شيئاً" in c["reason"]


def test_classify_pattern_mismatch_due_to_error_shape():
    """Stage matches a known pattern but error string doesn't — the
    defensive case. User explicitly wants this NOT to be auto-fixed."""
    row = {
        "pipeline_stage": "DEAD_LETTER",
        "last_failed_stage": "FAILED_CUSTOMER",   # contact_name pattern stage
        "pipeline_error": {"code": "qoyod_api_error",
                           "message": "different qoyod problem entirely"},
        "requeue_attempts": 0,
    }
    c = _classify_row(row)
    assert c["status"] == "pattern_mismatch_due_to_error_shape"
    assert "contact_name_blank_2026_02_26" in c["candidate_pattern_ids"]


def test_classify_not_terminal():
    row = {"pipeline_stage": "NORMALIZED",
           "last_failed_stage": "FAILED_CUSTOMER",
           "pipeline_error": {"details": {"contact_name": ["Can't be blank"]}}}
    c = _classify_row(row)
    assert c["status"] == "not_terminal"


# ─────────────────────────────────────────────────────────────────────
# forensics — end-to-end
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_forensics_returns_per_row_classification(db, tenant):
    try:
        activated = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.qoyod_settings.insert_one({
            "user_id": tenant, "go_live_activated_at": activated,
            "enabled": True, "dry_run_mode": False})

        # Three rows in different states.
        await _seed(db, tenant)  # auto_recoverable_pending
        await _seed(db, tenant, requeue_attempts=MAX_REQUEUE_ATTEMPTS)  # max
        await _seed(
            db, tenant, last_failed_stage="FAILED_VALIDATION",
            pipeline_error={"code": "total_missing"})  # no_pattern_match

        rep = await forensics(db, user_id=tenant)
        assert rep["ok"] is True
        assert rep["total"] == 3
        statuses = {r["classification"]["status"] for r in rep["rows"]}
        assert "auto_recoverable_pending" in statuses
        assert "max_attempts_reached" in statuses
        assert "no_pattern_match" in statuses
        # counters reflect the same
        assert rep["counters"]["auto_recoverable_pending"] == 1
        assert rep["counters"]["max_attempts_reached"] == 1
        assert rep["counters"]["no_pattern_match"] == 1
        # Registry is exposed
        assert len(rep["patterns_in_registry"]) == 1
        assert rep["max_requeue_attempts"] == MAX_REQUEUE_ATTEMPTS
        assert rep["go_live_activated_at"]
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_forensics_respects_watermark(db, tenant):
    """Rows received BEFORE go_live_activated_at must not appear."""
    try:
        activated = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.qoyod_settings.insert_one({
            "user_id": tenant, "go_live_activated_at": activated})
        # Old row (pre-activation) — should be excluded
        await _seed(db, tenant,
                    received_at=datetime.now(timezone.utc) - timedelta(hours=5))
        # New row (post-activation) — should appear
        await _seed(db, tenant, received_at=datetime.now(timezone.utc))

        rep = await forensics(db, user_id=tenant)
        assert rep["total"] == 1
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_forensics_includes_stage_history_tail(db, tenant):
    try:
        history = [
            {"to_stage": "NORMALIZED", "at": "t1"},
            {"to_stage": "RETRYING", "at": "t2"},
            {"to_stage": "DEAD_LETTER", "at": "t3"},
        ]
        await _seed(db, tenant, stage_history=history)
        rep = await forensics(db, user_id=tenant)
        assert len(rep["rows"][0]["stage_history_tail"]) == 3
    finally:
        await _cleanup(db, tenant)


# ─────────────────────────────────────────────────────────────────────
# force=True override
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_requeue_row_force_bypasses_max_attempts(db, tenant):
    try:
        row = await _seed(db, tenant,
                          requeue_attempts=MAX_REQUEUE_ATTEMPTS)
        pat = KNOWN_FIXED_PATTERNS[0]
        # Without force → refused
        res = await requeue_row(db, row, pattern=pat)
        assert res["ok"] is False
        assert res["reason"] == "max_requeue_attempts_reached"
        # With force → accepted, attempts increment, audit fields set
        res = await requeue_row(db, row, pattern=pat,
                                actor="operator:admin", force=True)
        assert res["ok"] is True
        fresh = await db.integration_inbox.find_one({"id": row["id"]})
        assert fresh["requeue_attempts"] == MAX_REQUEUE_ATTEMPTS + 1
        assert fresh.get("forced_by") == "operator:admin"
        assert fresh.get("forced_requeue_at") is not None
        assert fresh["pipeline_stage"] == "NORMALIZED"
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_requeue_one_force_still_requires_pattern_match(db, tenant):
    """Even with force=True, a generic DEAD_LETTER (no pattern match)
    is REFUSED. The pattern registry is the final guard."""
    try:
        row = await _seed(
            db, tenant, last_failed_stage="FAILED_VALIDATION",
            pipeline_error={"code": "total_missing"})
        res = await requeue_one(
            db, user_id=tenant, row_id=row["id"], force=True)
        assert res["ok"] is False
        assert res["reason"] == "no_known_fix_pattern_matches"
    finally:
        await _cleanup(db, tenant)
