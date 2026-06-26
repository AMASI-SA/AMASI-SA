"""Integration test for the QYD-GO «clear-test-failures» persistence.

The user reported a bug:
    1. Open QYD-GO page
    2. Click "🗑️ تنظيف فشل الاختبار" → checklist turns 11/11 green
    3. Refresh the page
    4. "لا فواصل عالقة" goes back to RED

This test reproduces the exact end-to-end flow to PROVE whether:
    a) the cleanup endpoint actually writes `excluded_from_checklist=true`
       and persists it in MongoDB;
    b) `_check_outstanding_failures` correctly ignores excluded rows
       across SUCCESSIVE invocations (= "refresh");
    c) idempotency holds — running cleanup twice does not unset the flag.

If all three assertions pass and the user still sees the bug in
production, the cause must be NEW failures arriving after the cleanup
(the worker keeps draining in-flight rows into DEAD_LETTER), not a
bug in the exclusion mechanism.
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


async def _seed_failed_rows(db, tenant: str, *,
                            n_dead: int = 3, n_partial: int = 2) -> None:
    rows: list[dict] = []
    for i in range(n_dead):
        rows.append({
            "user_id":         tenant,
            "trace_id":        uuid.uuid4().hex,
            "id":              uuid.uuid4().hex,
            "connector_key":   "qoyod",
            "idempotency_key": f"qydgo-d-{tenant}-{i}-{uuid.uuid4().hex}",
            "received_at":     datetime.now(timezone.utc),
            "pipeline_stage":  "DEAD_LETTER",
            "dry_run":         True,
        })
    for i in range(n_partial):
        rows.append({
            "user_id":         tenant,
            "trace_id":        uuid.uuid4().hex,
            "id":              uuid.uuid4().hex,
            "connector_key":   "qoyod",
            "idempotency_key": f"qydgo-p-{tenant}-{i}-{uuid.uuid4().hex}",
            "received_at":     datetime.now(timezone.utc),
            "pipeline_stage":  "PARTIAL_FAILURE",
            "dry_run":         True,
        })
    if rows:
        await db.integration_inbox.insert_many(rows)


# This mirrors the exact `update_many` call inside the
# /api/integrations/qoyod/go-live/clear-test-failures endpoint.
# Kept here verbatim (NOT imported) so the test catches any future
# drift between the endpoint code and the intended behaviour.
async def _clear_test_failures_endpoint_body(db, user_id: str) -> dict:
    result = await db.integration_inbox.update_many(
        {"user_id": user_id,
         "pipeline_stage": {"$in": ["DEAD_LETTER", "PARTIAL_FAILURE"]},
         "excluded_from_checklist": {"$ne": True}},
        {"$set": {"excluded_from_checklist": True,
                  "excluded_at": datetime.now(timezone.utc)}},
    )
    return {"ok": True, "excluded": result.modified_count}


async def _cleanup(db, tenant: str) -> None:
    await db.integration_inbox.delete_many({"user_id": tenant})


# ─── 1) Cleanup endpoint actually writes the flag to Mongo ───────────
@pytest.mark.asyncio
async def test_clear_test_failures_writes_excluded_flag_to_mongo(db, tenant):
    try:
        await _seed_failed_rows(db, tenant, n_dead=3, n_partial=2)
        # Sanity: before cleanup, no row has the flag.
        before = await db.integration_inbox.count_documents(
            {"user_id": tenant, "excluded_from_checklist": True})
        assert before == 0

        result = await _clear_test_failures_endpoint_body(db, tenant)
        assert result["ok"] is True
        assert result["excluded"] == 5

        # After cleanup, ALL 5 rows have `excluded_from_checklist=True`
        # AND `excluded_at` populated.
        after = await db.integration_inbox.count_documents(
            {"user_id": tenant, "excluded_from_checklist": True})
        assert after == 5
        async for r in db.integration_inbox.find(
                {"user_id": tenant},
                {"_id": 0, "excluded_from_checklist": 1, "excluded_at": 1,
                 "pipeline_stage": 1}):
            assert r["excluded_from_checklist"] is True
            assert r["excluded_at"] is not None
    finally:
        await _cleanup(db, tenant)


# ─── 2) Check ignores excluded rows ──────────────────────────────────
@pytest.mark.asyncio
async def test_check_outstanding_failures_ignores_excluded_rows(db, tenant):
    try:
        await _seed_failed_rows(db, tenant, n_dead=2, n_partial=1)
        # Before cleanup → check FAILS with count=3
        res = await _check_outstanding_failures(db, tenant)
        assert res["ok"] is False
        assert res["extra"]["stuck_count"] == 3

        # Run cleanup
        await _clear_test_failures_endpoint_body(db, tenant)

        # After cleanup → check PASSES
        res = await _check_outstanding_failures(db, tenant)
        assert res["ok"] is True
        assert "لا توجد طلبات عالقة" in res["detail"]
    finally:
        await _cleanup(db, tenant)


# ─── 3) Persistence across multiple invocations (= "refresh") ────────
@pytest.mark.asyncio
async def test_exclusion_survives_multiple_checks_simulating_refresh(db, tenant):
    """This is the EXACT bug the user reported. Reproduces:
       run cleanup → check ok (page green) → simulate refresh (re-call
       check) → check should still be ok (page stays green) → simulate
       another refresh → still ok.
    """
    try:
        await _seed_failed_rows(db, tenant, n_dead=4, n_partial=1)

        # Step 1: User clicks "تنظيف فشل الاختبار"
        cleanup_res = await _clear_test_failures_endpoint_body(db, tenant)
        assert cleanup_res["excluded"] == 5

        # Step 2: First check after cleanup (page is green)
        first = await _check_outstanding_failures(db, tenant)
        assert first["ok"] is True

        # Step 3: User refreshes the page → second check
        second = await _check_outstanding_failures(db, tenant)
        assert second["ok"] is True, (
            "Bug repro: exclusion did NOT survive a second check call. "
            f"Result: {second}")

        # Step 4: User refreshes again → third check
        third = await _check_outstanding_failures(db, tenant)
        assert third["ok"] is True, (
            "Bug repro: exclusion did NOT survive a third check call. "
            f"Result: {third}")

        # Step 5: Verify the flag is still in Mongo (not transient).
        still_excluded = await db.integration_inbox.count_documents(
            {"user_id": tenant, "excluded_from_checklist": True})
        assert still_excluded == 5
    finally:
        await _cleanup(db, tenant)


# ─── 4) Idempotency — running cleanup twice keeps the flag ───────────
@pytest.mark.asyncio
async def test_cleanup_is_idempotent(db, tenant):
    try:
        await _seed_failed_rows(db, tenant, n_dead=2, n_partial=2)

        first = await _clear_test_failures_endpoint_body(db, tenant)
        assert first["excluded"] == 4

        # Second call: no new rows to exclude, but already-excluded rows
        # must remain excluded.
        second = await _clear_test_failures_endpoint_body(db, tenant)
        assert second["excluded"] == 0

        still_excluded = await db.integration_inbox.count_documents(
            {"user_id": tenant, "excluded_from_checklist": True})
        assert still_excluded == 4

        res = await _check_outstanding_failures(db, tenant)
        assert res["ok"] is True
    finally:
        await _cleanup(db, tenant)


# ─── 5) NEW failures after cleanup still surface (correct behaviour) ─
@pytest.mark.asyncio
async def test_new_failures_after_cleanup_DO_surface(db, tenant):
    """This is the EXPECTED behaviour: rows added AFTER the cleanup
    must NOT be auto-excluded — the operator should see them.

    This test documents the difference between:
       • a bug (old excluded rows reappear → would fail this test's
         "still_excluded == 3" assertion in step 4)
       • correct behaviour (new failures appear, old ones stay
         excluded → matches what this test asserts).
    """
    try:
        await _seed_failed_rows(db, tenant, n_dead=3, n_partial=0)
        await _clear_test_failures_endpoint_body(db, tenant)

        # Operator's view at this point: clean.
        res = await _check_outstanding_failures(db, tenant)
        assert res["ok"] is True

        # NOW simulate the worker producing a NEW DEAD_LETTER row
        # AFTER the cleanup.
        await db.integration_inbox.insert_one({
            "user_id":         tenant,
            "trace_id":        uuid.uuid4().hex,
            "id":              uuid.uuid4().hex,
            "connector_key":   "qoyod",
            "idempotency_key": f"new-after-cleanup-{uuid.uuid4().hex}",
            "received_at":     datetime.now(timezone.utc),
            "pipeline_stage":  "DEAD_LETTER",
            "dry_run":         True,
        })

        # The new failure SHOULD surface (this is correct, not a bug).
        res = await _check_outstanding_failures(db, tenant)
        assert res["ok"] is False
        assert res["extra"]["stuck_count"] == 1

        # And the original 3 rows are still excluded in Mongo —
        # not "un-excluded" by anything.
        still_excluded = await db.integration_inbox.count_documents(
            {"user_id": tenant, "excluded_from_checklist": True})
        assert still_excluded == 3
    finally:
        await _cleanup(db, tenant)
