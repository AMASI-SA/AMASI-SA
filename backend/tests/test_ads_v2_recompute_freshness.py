"""iter-261 — Stale drift / match_status regression after manual platform
value entry.

Reported bug: the report showed "يحتاج مراجعة" with drift 14.91% / 26.24%
even after the merchant entered a manual platform value equal to
spend_sar (e.g. both 542.03). The recompute path was missing:
  • match_status (kept stale)
  • diff_sar / diff_native (never updated)
  • drift_pct_vs_platform (never updated)
  • platform_authoritative_sar (never updated → "قيمة المنصة الآن"
    still reflected an older auto_reconcile)

Fix: `recompute_drift_for_day` now re-derives every drift/match field
from the current `spend_native` and the freshly entered manual value.
"""
import os
import uuid
import pytest
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from ads_v2.sync.core import recompute_drift_for_day


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_matching_manual_value_clears_all_stale_drift(db):
    """User's exact scenario: ads_daily=542.03 SAR, user enters
    platform value=542.03 → drift must be 0 and status must be
    matched (or pending_platform if confidence=provisional & hours<24)."""
    user_id = f"test_iter261_{uuid.uuid4().hex[:8]}"
    acct_id = f"acct_{uuid.uuid4().hex[:8]}"
    try:
        await db.ads_accounts.insert_one({
            "id": acct_id, "user_id": user_id, "provider": "snapchat",
            "display_name": "Test", "soft_deleted": False,
            "timezone": "Asia/Riyadh",
            "currency_native": "USD",
            "review_settings": {"drift_warning_threshold_pct": 5.0,
                                "drift_block_threshold_pct": 15.0},
        })
        await db.ads_daily.insert_one({
            "user_id": user_id, "account_id": acct_id,
            "provider": "snapchat", "date": "2026-06-23",
            "idempotency_key": f"k_{uuid.uuid4().hex[:6]}",
            "spend_native": 144.54, "currency_native": "USD",
            "spend_sar":    542.03,        # = 144.54 × 3.75
            "fx_rate":      3.75,
            "confidence":   "final",
            # ── STALE legacy values that triggered the bug ──
            "drift_pct":               14.91,
            "drift_pct_vs_previous_sync": 14.91,
            "drift_pct_vs_platform":   14.91,
            "diff_sar":                81.0,
            "diff_native":             21.6,
            "platform_authoritative_sar": 461.03,
            "match_status":            "drift_review",
            "review_status":           "pending",
        })
        # User enters a manual platform value EQUAL to spend (144.54 USD).
        result = await recompute_drift_for_day(
            db, user_id, acct_id, "2026-06-23",
            manual_value_native=144.54, actor_email="user@test")
        # Function result reflects fresh recompute.
        assert result["ok"] is True
        assert result["diff_native"] == 0.0
        assert result["diff_sar"] == 0.0
        assert result["drift_pct_vs_platform"] == 0.0
        assert result["drift_pct"] == 0.0 or result["drift_pct"] is None
        # match_status is recomputed from drift=0 → "matched"
        # (confidence=final removes pending_platform path).
        assert result["match_status"] == "matched", \
            f"expected matched, got {result['match_status']}"

        # DB row reflects the fresh values, NOT the stale ones.
        row = await db.ads_daily.find_one(
            {"user_id": user_id, "account_id": acct_id, "date": "2026-06-23"})
        assert row["diff_sar"] == 0.0
        assert row["diff_native"] == 0.0
        assert row["drift_pct_vs_platform"] == 0.0
        assert row["match_status"] == "matched"
        # Platform-authoritative MUST now equal the manual value the
        # user just entered (single source for "platform value now").
        assert row["platform_authoritative_native"] == 144.54
        assert row["platform_authoritative_sar"]    == 542.03
    finally:
        await db.ads_daily.delete_many({"user_id": user_id})
        await db.ads_accounts.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_manual_value_with_real_drift_classifies_drift_review(db):
    """If the user enters a manual value that genuinely differs from
    spend, match_status must be drift_review (not stuck on matched)."""
    user_id = f"test_iter261_{uuid.uuid4().hex[:8]}"
    acct_id = f"acct_{uuid.uuid4().hex[:8]}"
    try:
        await db.ads_accounts.insert_one({
            "id": acct_id, "user_id": user_id, "provider": "snapchat",
            "display_name": "Test", "soft_deleted": False,
            "timezone": "Asia/Riyadh",
            "review_settings": {"drift_warning_threshold_pct": 5.0,
                                "drift_block_threshold_pct": 15.0},
        })
        await db.ads_daily.insert_one({
            "user_id": user_id, "account_id": acct_id,
            "provider": "snapchat", "date": "2026-06-23",
            "idempotency_key": f"k_{uuid.uuid4().hex[:6]}",
            "spend_native": 100.0, "spend_sar": 375.0,
            "fx_rate": 3.75, "confidence": "final",
            "match_status": "matched",   # stale (was matching before)
        })
        # Now user enters 110 USD (10% drift).
        result = await recompute_drift_for_day(
            db, user_id, acct_id, "2026-06-23",
            manual_value_native=110.0)
        assert result["ok"] is True
        # |100−110|/110 = 9.09%  (drift_manual is computed vs manual)
        assert abs(result["drift_pct_vs_manual"] - 9.09) < 0.05
        # 9.09% > 5% warn threshold → drift_review
        assert result["match_status"] == "drift_review"
    finally:
        await db.ads_daily.delete_many({"user_id": user_id})
        await db.ads_accounts.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_diff_sar_recomputed_from_current_values(db):
    """Even if the row carries an old huge diff_sar, recompute uses
    only the current spend_sar and the freshly entered manual_sar."""
    user_id = f"test_iter261_{uuid.uuid4().hex[:8]}"
    acct_id = f"acct_{uuid.uuid4().hex[:8]}"
    try:
        await db.ads_accounts.insert_one({
            "id": acct_id, "user_id": user_id, "provider": "snapchat",
            "display_name": "Test", "soft_deleted": False,
            "timezone": "Asia/Riyadh",
            "review_settings": {"drift_warning_threshold_pct": 5.0,
                                "drift_block_threshold_pct": 15.0},
        })
        await db.ads_daily.insert_one({
            "user_id": user_id, "account_id": acct_id,
            "provider": "snapchat", "date": "2026-06-23",
            "idempotency_key": f"k_{uuid.uuid4().hex[:6]}",
            "spend_native": 50.0, "spend_sar": 187.5,
            "fx_rate": 3.75, "confidence": "final",
            "diff_sar":  9999.0,   # ← absurd stale value
            "drift_pct": 9999.0,   # ← absurd stale value
            "match_status": "drift_review",
        })
        # New manual = 50 USD → diff_native = 0, diff_sar = 0.
        result = await recompute_drift_for_day(
            db, user_id, acct_id, "2026-06-23",
            manual_value_native=50.0)
        assert result["diff_sar"] == 0.0
        assert result["drift_pct_vs_platform"] == 0.0
        # Stale values are GONE.
        row = await db.ads_daily.find_one(
            {"user_id": user_id, "account_id": acct_id, "date": "2026-06-23"})
        assert row["diff_sar"] == 0.0
        assert row["drift_pct_vs_platform"] == 0.0
    finally:
        await db.ads_daily.delete_many({"user_id": user_id})
        await db.ads_accounts.delete_many({"user_id": user_id})


def test_recompute_function_marks_iter_261():
    """Inspect source for the iter-261 markers and key recompute lines."""
    with open("/app/backend/ads_v2/sync/core.py", "r") as f:
        src = f.read()
    # All the recompute callsites must be tagged with the iteration so
    # future refactors can locate them.
    assert src.count("iter-261") >= 3
    # The diff_sar/diff_native recompute on manual entry must exist.
    assert 'diff_native = round(manual_value_native - spend_native' in src
    assert 'diff_sar    = round(manual_sar - spend_sar' in src
    # match_status MUST be recomputed inside recompute_drift_for_day.
    # (Find the function body containing _compute_match_status.)
    idx_func = src.index("async def recompute_drift_for_day")
    idx_next = src.index("async def ", idx_func + 1)
    body = src[idx_func:idx_next]
    assert "_compute_match_status(" in body, \
        "recompute_drift_for_day MUST call _compute_match_status"
    assert '"match_status"' in body, \
        "recompute_drift_for_day MUST write match_status to the DB"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
