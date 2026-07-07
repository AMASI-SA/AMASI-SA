"""Tests for Qoyod Dead-Letter Auto-Requeue (KNOWN_FIXED_PATTERNS).

User directive (2026-02-27): the worker self-heals rows that died
against a now-fixed bug — strictly within bounds:

    1. Only rows matching `KNOWN_FIXED_PATTERNS` are requeued.
    2. Bounded by `MAX_REQUEUE_ATTEMPTS` (default 2).
    3. Generic DEAD_LETTER rows stay red.
    4. After successful re-run the row's stage updates and the
       QYD-GO outstanding_failures check stops counting it.

Today (2026-02-27) only one pattern is registered:
    contact_name_blank_2026_02_26
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
    KNOWN_FIXED_PATTERNS, MAX_REQUEUE_ATTEMPTS,
    match_pattern, requeue_row, requeue_one,
    find_requeue_candidates, auto_requeue_known_fixed,
)
from integrations.qoyod.go_live import _check_outstanding_failures


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture
def tenant():
    return f"test-drq-{uuid.uuid4().hex[:8]}"


async def _seed_row(db, tenant, *, stage="DEAD_LETTER",
                    last_failed="FAILED_CUSTOMER",
                    error=None, dry_run=False,
                    requeue_attempts=0,
                    received_at=None) -> dict:
    row = {
        "id":           uuid.uuid4().hex,
        "trace_id":     uuid.uuid4().hex,
        "user_id":      tenant,
        "connector_key": "qoyod",
        "idempotency_key": uuid.uuid4().hex,
        "received_at":  received_at or datetime.now(timezone.utc),
        "pipeline_stage": stage,
        "last_failed_stage": last_failed,
        "pipeline_error": error or {},
        "dry_run":      dry_run,
        "requeue_attempts": requeue_attempts,
        "stage_history": [],
        "canonical_payload": {
            "order_id": uuid.uuid4().hex,
            "order_number": "TST-" + uuid.uuid4().hex[:6],
        },
    }
    await db.integration_inbox.insert_one(row)
    return row


async def _cleanup(db, tenant):
    await db.integration_inbox.delete_many({"user_id": tenant})
    await db.qoyod_settings.delete_many({"user_id": tenant})


# ─────────────────────────────────────────────────────────────────────
# Pattern matcher tests
# ─────────────────────────────────────────────────────────────────────
def test_known_fixed_patterns_registry_has_reviewed_patterns_only():
    """Registry pin: exactly the reviewed patterns, nothing more.
    rev47/rev48 (2026-07) added the two MANUAL-ONLY recovery patterns
    alongside contact_name_blank."""
    assert len(KNOWN_FIXED_PATTERNS) == 3
    by_id = {p["id"]: p for p in KNOWN_FIXED_PATTERNS}
    assert set(by_id) == {"contact_name_blank_2026_02_26",
                          "false_skip_history_veto_2026_07_07",
                          "canary_budget_false_block_2026_07_07"}
    p = by_id["contact_name_blank_2026_02_26"]
    assert "FAILED_CUSTOMER" in p["applies_to_failed_stages"]
    assert not p.get("manual_only")
    for rid in ("false_skip_history_veto_2026_07_07",
                "canary_budget_false_block_2026_07_07"):
        r = by_id[rid]
        assert r["manual_only"] is True
        assert "FAILED_CUSTOMER" in r["applies_to_failed_stages"]


def test_matcher_accepts_classic_qoyod_validation_shape():
    row = {
        "last_failed_stage": "FAILED_CUSTOMER",
        "pipeline_error": {
            "code": "qoyod_validation_error",
            "details": {"contact_name": ["Can't be blank"]},
        },
    }
    pat = match_pattern(row)
    assert pat is not None
    assert pat["id"] == "contact_name_blank_2026_02_26"


def test_matcher_accepts_inline_message_shape():
    row = {
        "last_failed_stage": "FAILED_CUSTOMER",
        "pipeline_error": {
            "code": "qoyod_api_error",
            "message": "Validation failed: contact_name Can't be blank",
        },
    }
    assert match_pattern(row) is not None


def test_matcher_accepts_production_shape_with_repr_escaped_apostrophe():
    """Regression for Iter-267 production bug: the row was sitting in
    DEAD_LETTER with this exact error shape but the matcher rejected
    it because `str(err)` escaped the apostrophe (`Can't` → `Can\\'t`)
    breaking the literal substring search."""
    row = {
        "last_failed_stage": "FAILED_CUSTOMER",
        "pipeline_error": {
            "code":         "qoyod_validation_error",
            "message":      "{'contact_name': [\"Can't be blank\"]}",
            "status_code":  422,
            "endpoint":     "POST /customers",
            "qoyod_response_excerpt": "{'errors': {'contact_name': [\"Can't be blank\"]}}",
        },
    }
    pat = match_pattern(row)
    assert pat is not None, "Production shape must match — was failing pre-Iter-267"
    assert pat["id"] == "contact_name_blank_2026_02_26"


def test_matcher_rejects_wrong_failed_stage():
    """Pattern only applies to FAILED_CUSTOMER. Same error on
    FAILED_INVOICE must NOT match."""
    row = {
        "last_failed_stage": "FAILED_INVOICE",
        "pipeline_error": {"message": "contact_name Can't be blank"},
    }
    assert match_pattern(row) is None


def test_matcher_rejects_unrelated_error():
    """Generic DEAD_LETTER rows (e.g. `total_missing`) must NEVER
    match the known-fix registry."""
    row = {
        "last_failed_stage": "FAILED_VALIDATION",
        "pipeline_error": {"code": "total_missing",
                           "message": "order total is missing"},
    }
    assert match_pattern(row) is None


def test_matcher_safe_on_empty_error():
    assert match_pattern({"last_failed_stage": "FAILED_CUSTOMER"}) is None
    assert match_pattern({"last_failed_stage": "FAILED_CUSTOMER",
                          "pipeline_error": None}) is None


# ─────────────────────────────────────────────────────────────────────
# Single-row requeue mechanic
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_requeue_row_moves_dead_letter_to_normalized(db, tenant):
    try:
        row = await _seed_row(
            db, tenant,
            error={"code": "qoyod_validation_error",
                   "details": {"contact_name": ["Can't be blank"]}})
        pat = KNOWN_FIXED_PATTERNS[0]
        res = await requeue_row(db, row, pattern=pat)
        assert res["ok"] is True
        assert res["resume_stage"] == "NORMALIZED"
        assert res["requeue_attempts"] == 1
        assert res["pattern_id"] == "contact_name_blank_2026_02_26"

        # Verify row state in DB
        fresh = await db.integration_inbox.find_one({"id": row["id"]})
        assert fresh["pipeline_stage"] == "NORMALIZED"
        assert fresh["requeue_attempts"] == 1
        assert fresh["last_requeue_pattern"] == "contact_name_blank_2026_02_26"
        # Two history entries: DEAD_LETTER → RETRYING → NORMALIZED
        history = fresh.get("stage_history") or []
        assert any(h.get("to_stage") == "RETRYING" for h in history)
        assert any(h.get("to_stage") == "NORMALIZED" for h in history)
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_requeue_row_refuses_when_attempts_exhausted(db, tenant):
    try:
        row = await _seed_row(
            db, tenant,
            error={"details": {"contact_name": ["Can't be blank"]}},
            requeue_attempts=MAX_REQUEUE_ATTEMPTS,  # already exhausted
        )
        pat = KNOWN_FIXED_PATTERNS[0]
        res = await requeue_row(db, row, pattern=pat)
        assert res["ok"] is False
        assert res["reason"] == "max_requeue_attempts_reached"
        # DB unchanged
        fresh = await db.integration_inbox.find_one({"id": row["id"]})
        assert fresh["pipeline_stage"] == "DEAD_LETTER"
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_requeue_row_refuses_non_terminal(db, tenant):
    try:
        row = await _seed_row(
            db, tenant, stage="NORMALIZED", last_failed=None)
        pat = KNOWN_FIXED_PATTERNS[0]
        res = await requeue_row(db, row, pattern=pat)
        assert res["ok"] is False
        assert res["reason"] == "row_not_in_terminal_failure"
    finally:
        await _cleanup(db, tenant)


# ─────────────────────────────────────────────────────────────────────
# Bulk auto-requeue
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_auto_requeue_only_touches_matching_rows(db, tenant):
    """Seeds 3 rows: 1 matching contact_name pattern, 1 generic
    total_missing failure, 1 dry-run with the same matching error.
    Only the production matching row is requeued.
    """
    try:
        await _seed_row(
            db, tenant,
            error={"details": {"contact_name": ["Can't be blank"]}})
        await _seed_row(
            db, tenant, last_failed="FAILED_VALIDATION",
            error={"code": "total_missing",
                   "message": "order total is missing"})
        await _seed_row(
            db, tenant, dry_run=True,
            error={"details": {"contact_name": ["Can't be blank"]}})

        res = await auto_requeue_known_fixed(db, user_id=tenant)
        assert res["scanned"] == 2  # dry_run row excluded
        assert res["requeued"] == 1
        assert res["skipped_no_pattern"] == 1
        # Generic total_missing stays DEAD_LETTER
        stuck = await db.integration_inbox.count_documents(
            {"user_id": tenant, "pipeline_stage": "DEAD_LETTER",
             "dry_run": {"$ne": True},
             "pipeline_error.code": "total_missing"})
        assert stuck == 1
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_auto_requeue_bounded_by_max_attempts(db, tenant):
    """Run auto-requeue MAX_REQUEUE_ATTEMPTS+1 times against the same
    row — the row stops being requeued after the cap.

    Between rounds we manually send the row back to DEAD_LETTER to
    simulate the worker failing again with the same error. After
    the cap, no further requeues happen.
    """
    try:
        row = await _seed_row(
            db, tenant,
            error={"details": {"contact_name": ["Can't be blank"]}})

        # Round 1: requeues
        r1 = await auto_requeue_known_fixed(db, user_id=tenant)
        assert r1["requeued"] == 1
        # Send back to DEAD_LETTER to simulate another failure.
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {"pipeline_stage": "DEAD_LETTER",
                      "last_failed_stage": "FAILED_CUSTOMER"}})

        # Round 2: requeues (now at attempts=2)
        r2 = await auto_requeue_known_fixed(db, user_id=tenant)
        assert r2["requeued"] == 1
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {"pipeline_stage": "DEAD_LETTER",
                      "last_failed_stage": "FAILED_CUSTOMER"}})

        # Round 3: refuses (max reached)
        r3 = await auto_requeue_known_fixed(db, user_id=tenant)
        assert r3["requeued"] == 0
        assert r3["skipped_max_attempts"] == 1

        fresh = await db.integration_inbox.find_one({"id": row["id"]})
        assert fresh["requeue_attempts"] == MAX_REQUEUE_ATTEMPTS
        assert fresh["pipeline_stage"] == "DEAD_LETTER"
    finally:
        await _cleanup(db, tenant)


# ─────────────────────────────────────────────────────────────────────
# QYD-GO integration — auto-recoverable rows are NOT blocking
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_qyd_go_outstanding_failures_excludes_auto_recoverable(db, tenant):
    """After Go-Live activation, a contact_name_blank failure is
    auto-recoverable. The check passes (ok=True) but surfaces the
    auto_recoverable_count for UI transparency."""
    try:
        # Set activation watermark to 1 hour ago.
        activated = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.qoyod_settings.insert_one({
            "user_id": tenant,
            "go_live_activated_at": activated,
            "enabled": True, "dry_run_mode": False,
        })
        # Seed a contact_name_blank failure AFTER activation watermark.
        await _seed_row(
            db, tenant,
            error={"details": {"contact_name": ["Can't be blank"]}},
            received_at=datetime.now(timezone.utc))

        result = await _check_outstanding_failures(db, tenant)
        assert result["ok"] is True
        assert result["extra"]["blocking_count"] == 0
        assert result["extra"]["auto_recoverable_count"] == 1
        # The Arabic detail mentions auto re-processing
        assert "تلقائياً" in result["detail"]
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_qyd_go_outstanding_failures_blocks_on_unknown_error(db, tenant):
    """A generic DEAD_LETTER (e.g. total_missing) DOES block Go-Live —
    it's not in the known-fix registry."""
    try:
        activated = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.qoyod_settings.insert_one({
            "user_id": tenant,
            "go_live_activated_at": activated,
            "enabled": True, "dry_run_mode": False,
        })
        await _seed_row(
            db, tenant, last_failed="FAILED_VALIDATION",
            error={"code": "total_missing"},
            received_at=datetime.now(timezone.utc))

        result = await _check_outstanding_failures(db, tenant)
        assert result["ok"] is False
        assert result["extra"]["blocking_count"] == 1
        assert result["extra"]["auto_recoverable_count"] == 0
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_qyd_go_outstanding_failures_clears_after_requeue_succeeds(
    db, tenant,
):
    """End-to-end: row failed → requeued → manually marked COMPLETED
    (simulating worker success). QYD-GO check returns OK with no
    blocking and no auto-recoverable."""
    try:
        activated = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.qoyod_settings.insert_one({
            "user_id": tenant,
            "go_live_activated_at": activated,
            "enabled": True, "dry_run_mode": False,
        })
        row = await _seed_row(
            db, tenant,
            error={"details": {"contact_name": ["Can't be blank"]}},
            received_at=datetime.now(timezone.utc))

        # Auto-requeue moves it to NORMALIZED.
        await auto_requeue_known_fixed(db, user_id=tenant)
        # Simulate successful processing → COMPLETED.
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {"pipeline_stage": "COMPLETED"}})

        result = await _check_outstanding_failures(db, tenant)
        assert result["ok"] is True
        assert result["extra"]["blocking_count"] == 0
        assert result["extra"]["auto_recoverable_count"] == 0
        # Standard "no stuck failures" detail
        assert "لا توجد فواصل إنتاجية عالقة" in result["detail"]
    finally:
        await _cleanup(db, tenant)


# ─────────────────────────────────────────────────────────────────────
# Discovery / preview
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_find_requeue_candidates_only_returns_matching(db, tenant):
    try:
        await _seed_row(
            db, tenant,
            error={"details": {"contact_name": ["Can't be blank"]}})
        await _seed_row(
            db, tenant, last_failed="FAILED_VALIDATION",
            error={"code": "total_missing"})

        cands = await find_requeue_candidates(db, user_id=tenant)
        assert len(cands) == 1
        assert cands[0]["pattern_id"] == "contact_name_blank_2026_02_26"
        assert cands[0]["last_failed_stage"] == "FAILED_CUSTOMER"
    finally:
        await _cleanup(db, tenant)


# ─────────────────────────────────────────────────────────────────────
# Per-row manual requeue
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_requeue_one_by_trace_id(db, tenant):
    try:
        row = await _seed_row(
            db, tenant,
            error={"details": {"contact_name": ["Can't be blank"]}})
        res = await requeue_one(db, user_id=tenant, trace_id=row["trace_id"])
        assert res["ok"] is True
        fresh = await db.integration_inbox.find_one({"id": row["id"]})
        assert fresh["pipeline_stage"] == "NORMALIZED"
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_requeue_one_refuses_unknown_error(db, tenant):
    try:
        row = await _seed_row(
            db, tenant, last_failed="FAILED_VALIDATION",
            error={"code": "total_missing"})
        res = await requeue_one(db, user_id=tenant, row_id=row["id"])
        assert res["ok"] is False
        assert res["reason"] == "no_known_fix_pattern_matches"
    finally:
        await _cleanup(db, tenant)
