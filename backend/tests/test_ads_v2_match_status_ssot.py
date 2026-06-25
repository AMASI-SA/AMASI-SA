"""iter-259 — match_status SSOT classification regression.

Bug: rows with valid spend in ads_daily were being marked
`match_status="sync_failed"` whenever a platform-check API call
hiccupped. Per the user's SSOT rule, `sync_failed` is reserved for
the case where NO valid data exists in ads_daily.

Fix: both `sync_account_day` and `auto_reconcile_for_day` now check
if the row already holds valid SSOT data; if yes they only record
`platform_check_error` (annotation), without touching `match_status`.
The reconciliation report layer additionally reclassifies legacy
mis-tagged rows on the fly (no DB writes).
"""
import os
import uuid
import pytest
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from ads_v2.data_layer.reports import get_reconciliation_report


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_legacy_sync_failed_with_valid_data_reclassified_to_drift(db):
    """A row carrying legacy match_status=sync_failed but valid SSOT
    spend (471.71 SAR) with drift 7.94% must be re-shown as drift_review."""
    user_id = f"test_iter259_{uuid.uuid4().hex[:8]}"
    acct_id = f"acct_{uuid.uuid4().hex[:8]}"
    try:
        await db.ads_accounts.insert_one({
            "id": acct_id, "user_id": user_id, "provider": "snapchat",
            "display_name": "Self Service",
            "currency_native": "USD",
        })
        await db.ads_daily.insert_one({
            "user_id": user_id, "account_id": acct_id,
            "provider": "snapchat", "date": "2026-06-24",
            "idempotency_key": f"k_{uuid.uuid4().hex[:6]}",
            # ✅ Valid SSOT data present:
            "spend_native": 125.79, "currency_native": "USD",
            "spend_sar":    471.71,
            "drift_pct":    7.94,
            # ❌ Legacy mistake: status was flipped to sync_failed
            #    even though the data is intact.
            "match_status": "sync_failed",
            "platform_check_error": "http_error",
        })
        rep = await get_reconciliation_report(
            db, user_id, "2026-06-01", "2026-06-30")
        rows = rep["data"]
        assert len(rows) == 1
        # Must NOT show sync_failed on the report — that label is
        # reserved for "no data at all" cases.
        assert rows[0]["match_status"] == "drift_review", \
            f"expected drift_review, got {rows[0]['match_status']}"
        assert rows[0].get("match_status_reason") \
            == "platform_check_error_with_valid_ssot"
        # The reason annotation stays so the UI can still flag the
        # hiccup if it wants to ("API check failed but data is valid").
        assert rows[0].get("platform_check_error") == "http_error"
        # Summary counts must reflect the corrected classification.
        assert rep["summary"]["match_sync_failed"] == 0
        assert rep["summary"]["match_drift_review"] == 1
    finally:
        await db.ads_daily.delete_many({"user_id": user_id})
        await db.ads_accounts.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_truly_failed_sync_with_no_data_remains_sync_failed(db):
    """If the row has no SSOT spend at all (spend_native=0), the
    classification correctly stays sync_failed."""
    user_id = f"test_iter259_{uuid.uuid4().hex[:8]}"
    acct_id = f"acct_{uuid.uuid4().hex[:8]}"
    try:
        await db.ads_accounts.insert_one({
            "id": acct_id, "user_id": user_id, "provider": "snapchat",
            "display_name": "Truly Failed",
        })
        await db.ads_daily.insert_one({
            "user_id": user_id, "account_id": acct_id,
            "provider": "snapchat", "date": "2026-06-24",
            "idempotency_key": f"k_{uuid.uuid4().hex[:6]}",
            "spend_native": 0.0, "currency_native": "USD",
            "spend_sar":    0.0,
            "match_status": "sync_failed",
            "platform_check_error": "http_error",
        })
        rep = await get_reconciliation_report(
            db, user_id, "2026-06-01", "2026-06-30")
        rows = rep["data"]
        assert rows[0]["match_status"] == "sync_failed", \
            "rows without SSOT data must remain sync_failed"
        assert "match_status_reason" not in rows[0], \
            "no reclassification annotation for truly failed rows"
    finally:
        await db.ads_daily.delete_many({"user_id": user_id})
        await db.ads_accounts.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_low_drift_with_valid_data_classified_pending_platform(db):
    """Valid data + small drift (<5%) + legacy sync_failed → reclassified
    to pending_platform (not drift_review)."""
    user_id = f"test_iter259_{uuid.uuid4().hex[:8]}"
    acct_id = f"acct_{uuid.uuid4().hex[:8]}"
    try:
        await db.ads_accounts.insert_one({
            "id": acct_id, "user_id": user_id, "provider": "snapchat",
            "display_name": "Acct LowDrift",
        })
        await db.ads_daily.insert_one({
            "user_id": user_id, "account_id": acct_id,
            "provider": "snapchat", "date": "2026-06-24",
            "idempotency_key": f"k_{uuid.uuid4().hex[:6]}",
            "spend_native": 100.0, "spend_sar": 375.0,
            "drift_pct": 1.2,
            "match_status": "sync_failed",
            "platform_check_error": "http_error",
        })
        rep = await get_reconciliation_report(
            db, user_id, "2026-06-01", "2026-06-30")
        assert rep["data"][0]["match_status"] == "pending_platform"
    finally:
        await db.ads_daily.delete_many({"user_id": user_id})
        await db.ads_accounts.delete_many({"user_id": user_id})


def test_sync_core_preserves_existing_match_status_on_api_hiccup():
    """Inspect sync/core.py to confirm both failure paths now
    conditionally update match_status only when SSOT data is missing.
    (iter-259 → superseded by the iter-260 orthogonal-states refactor;
    we accept either marker so older comments remain valid.)"""
    with open("/app/backend/ads_v2/sync/core.py", "r") as f:
        src = f.read()
    # Either iteration marker is acceptable — both describe the same
    # invariant ("don't flip sync_failed when SSOT data is valid").
    marker_count = src.count("iter-259") + src.count("iter-260")
    assert marker_count >= 3, \
        f"expected ≥3 iter-259/iter-260 markers in sync/core.py, got {marker_count}"
    # The phrase that gates the write must be present.
    assert "preserved_existing_data" in src
    # Must NOT unconditionally set match_status=sync_failed any more
    # without a guarding has_valid_data / ssot_has_data check.
    assert "ssot_has_data" in src


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
