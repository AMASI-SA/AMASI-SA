"""Regression — Backfill Gate (user directive 2026-02-27).

User scenario: clicking 🚀 تفعيل وضع الإنتاج must NOT push any old
Dry-Run row to Qoyod. The default `backfill_mode="now_forward_only"`
moves every pre-activation in-flight row to SKIPPED with reason
`pre_activation_skipped`. The operator can opt in via
`backfill_mode="backfill_unsent"`.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.backfill_gate import skip_pre_activation_rows


@pytest.fixture
def db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture
def tenant():
    return f"test-backfill-{uuid.uuid4().hex[:8]}"


async def _seed_row(db, tenant, *, stage, received_at, **kw):
    row = {
        "id": uuid.uuid4().hex,
        "trace_id": uuid.uuid4().hex,
        "user_id": tenant,
        "connector_key": "qoyod",
        "idempotency_key": uuid.uuid4().hex,
        "received_at": received_at,
        "pipeline_stage": stage,
        "dry_run": False,
        "stage_history": [],
        "canonical_payload": {"order_id": "O", "order_number": "123"},
    }
    row.update(kw)
    await db.integration_inbox.insert_one(row)
    return row


async def _cleanup(db, tenant):
    await db.integration_inbox.delete_many({"user_id": tenant})
    await db.qoyod_settings.delete_many({"user_id": tenant})


# ─────────────────────────────────────────────────────────────────────
# Default behaviour: now_forward_only
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_default_skips_pre_activation_in_flight_rows(db, tenant):
    """Three pre-activation in-flight rows must be SKIPPED with
    reason `pre_activation_skipped`. Post-activation row stays
    untouched in NORMALIZED ready for the worker."""
    try:
        activated = datetime.now(timezone.utc)
        await db.qoyod_settings.insert_one({
            "user_id": tenant, "go_live_activated_at": activated,
            "enabled": True, "dry_run_mode": False,
            # No explicit backfill_mode → relies on default
        })
        pre = activated - timedelta(hours=2)
        post = activated + timedelta(seconds=5)
        r_norm   = await _seed_row(db, tenant, stage="NORMALIZED",        received_at=pre)
        r_cust   = await _seed_row(db, tenant, stage="CUSTOMER_RESOLVED", received_at=pre)
        r_prod   = await _seed_row(db, tenant, stage="PRODUCT_RESOLVED",  received_at=pre)
        r_after  = await _seed_row(db, tenant, stage="NORMALIZED",        received_at=post)

        res = await skip_pre_activation_rows(db, user_id=tenant)
        assert res["mode"] == "now_forward_only"
        assert res["skipped"] == 3
        # All 3 pre-activation rows → SKIPPED with the right reason
        for r in (r_norm, r_cust, r_prod):
            fresh = await db.integration_inbox.find_one({"id": r["id"]})
            assert fresh["pipeline_stage"] == "SKIPPED"
            assert fresh["skipped_reason"] == "pre_activation_skipped"
            assert fresh["skipped_by"] == "backfill_gate"
            history = [h.get("to_stage") for h in (fresh.get("stage_history") or [])]
            assert "SKIPPED" in history
        # Post-activation row stays in NORMALIZED
        fresh = await db.integration_inbox.find_one({"id": r_after["id"]})
        assert fresh["pipeline_stage"] == "NORMALIZED"
        # Row not deleted — still in DB
        total = await db.integration_inbox.count_documents({"user_id": tenant})
        assert total == 4
    finally:
        await _cleanup(db, tenant)


# ─────────────────────────────────────────────────────────────────────
# Operator opt-in: backfill_unsent
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_backfill_unsent_leaves_rows_untouched(db, tenant):
    try:
        activated = datetime.now(timezone.utc)
        await db.qoyod_settings.insert_one({
            "user_id": tenant, "go_live_activated_at": activated,
            "enabled": True, "dry_run_mode": False,
            "backfill_mode": "backfill_unsent",
        })
        pre = activated - timedelta(hours=2)
        r = await _seed_row(db, tenant, stage="NORMALIZED", received_at=pre)
        res = await skip_pre_activation_rows(db, user_id=tenant)
        assert res["skipped"] == 0
        assert res["mode"] == "backfill_unsent"
        fresh = await db.integration_inbox.find_one({"id": r["id"]})
        assert fresh["pipeline_stage"] == "NORMALIZED"   # untouched
    finally:
        await _cleanup(db, tenant)


# ─────────────────────────────────────────────────────────────────────
# Pre-activation safety
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_no_op_when_not_activated_yet(db, tenant):
    """Before Go-Live, the gate must be a no-op — pre-Go-Live rows
    are still being tested in Dry-Run and must not be skipped."""
    try:
        # No qoyod_settings → no watermark
        pre = datetime.now(timezone.utc) - timedelta(hours=1)
        r = await _seed_row(db, tenant, stage="NORMALIZED", received_at=pre)
        res = await skip_pre_activation_rows(db, user_id=tenant)
        assert res["skipped"] == 0
        assert res.get("reason") == "not_activated_yet"
        fresh = await db.integration_inbox.find_one({"id": r["id"]})
        assert fresh["pipeline_stage"] == "NORMALIZED"
    finally:
        await _cleanup(db, tenant)


# ─────────────────────────────────────────────────────────────────────
# Worker integration — pre-activation NORMALIZED row never reaches Qoyod
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_worker_integration_pre_activation_row_never_reaches_qoyod(db, tenant):
    """Full worker scenario: a pre-activation row in NORMALIZED would
    normally be drained by `process_pending_normalized`. With the
    gate, it's flipped to SKIPPED first → the drain finds nothing
    matching and never calls Qoyod."""
    try:
        activated = datetime.now(timezone.utc)
        await db.qoyod_settings.insert_one({
            "user_id": tenant, "go_live_activated_at": activated,
            "enabled": True, "dry_run_mode": False,
        })
        pre = activated - timedelta(hours=1)
        r = await _seed_row(db, tenant, stage="NORMALIZED", received_at=pre)

        # Run gate (worker calls this as Step 0a)
        gate = await skip_pre_activation_rows(db, user_id=tenant)
        assert gate["skipped"] == 1

        # Now the drain query (same one `process_pending_normalized` uses)
        # must find ZERO eligible rows for this row_id.
        eligible = await db.integration_inbox.count_documents({
            "user_id": tenant,
            "pipeline_stage": "NORMALIZED",
            "id": r["id"],
        })
        assert eligible == 0, "Row must no longer be NORMALIZED after gate"

        # And the row is in SKIPPED with the right audit reason.
        fresh = await db.integration_inbox.find_one({"id": r["id"]})
        assert fresh["pipeline_stage"] == "SKIPPED"
        assert fresh["skipped_reason"] == "pre_activation_skipped"
    finally:
        await _cleanup(db, tenant)
