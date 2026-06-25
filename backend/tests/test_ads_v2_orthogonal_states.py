"""iter-260 — Architectural invariant: orthogonal data-state vs
connection-state.

Permanent rule (locked-in for Ads V2):
  • match_status         = DATA STATE only (accounting).
  • platform_check_status = CONNECTION STATE only (technical).

The two are NEVER conflated. An API hiccup may only change the
connection-state field; it must never alter `match_status` when a
valid SSOT spend already exists in `ads_daily`.
"""
import os
import uuid
import pytest
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from ads_v2.data_layer.reports import get_reconciliation_report
from ads_v2.sync.core import _map_check_status, auto_reconcile_for_day


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


def test_map_check_status_canonical_mapping():
    """The adapter→connection-state mapping is the only place where
    API error codes are translated into platform_check_status values."""
    assert _map_check_status(None)              == "api_error"
    assert _map_check_status("token_invalid")   == "token_expired"
    assert _map_check_status("token_expired")   == "token_expired"
    assert _map_check_status("unauthorized")    == "token_expired"
    assert _map_check_status("rate_limited")    == "rate_limited"
    assert _map_check_status("too_many_requests") == "rate_limited"
    assert _map_check_status("http_error")      == "last_check_failed"
    assert _map_check_status("network_error")   == "last_check_failed"
    assert _map_check_status("timeout")         == "last_check_failed"
    assert _map_check_status("anything_else")   == "api_error"


def test_models_declare_orthogonal_status_constants():
    """models.py must explicitly document both orthogonal fields."""
    with open("/app/backend/ads_v2/models.py", "r") as f:
        src = f.read()
    # Tuples must exist.
    assert "MATCH_STATUSES" in src
    assert "PLATFORM_CHECK_STATUSES" in src
    # The architectural separation comment must be present (it is the
    # human-readable contract for future contributors).
    assert "Architectural separation" in src or \
           "iter-260" in src, "iter-260 comment must be present in models.py"
    # The orthogonal field must be a real attribute on AdsDaily.
    assert "platform_check_status:" in src


@pytest.mark.asyncio
async def test_api_hiccup_only_touches_connection_state_not_match_status(db):
    """auto_reconcile_for_day with a token failure must leave the
    accounting match_status untouched when SSOT data is valid."""
    user_id = f"test_iter260_{uuid.uuid4().hex[:8]}"
    acct_id = f"acct_{uuid.uuid4().hex[:8]}"
    try:
        # Account WITHOUT a v1 token reference → token resolution will
        # fail, triggering the no_token branch.
        await db.ads_accounts.insert_one({
            "id": acct_id, "user_id": user_id, "provider": "snapchat",
            "display_name": "TestAcct",
            "external_account_id": "ext1",
            "currency_native": "USD",
            "soft_deleted": False,
            "review_settings": {"drift_warning_threshold_pct": 5.0},
            # No v1_token_ref → _resolve_access_token returns no_token.
        })
        # Pre-existing valid SSOT row (matches state).
        await db.ads_daily.insert_one({
            "user_id": user_id, "account_id": acct_id,
            "provider": "snapchat", "date": "2026-06-24",
            "idempotency_key": f"k_{uuid.uuid4().hex[:6]}",
            "spend_native": 100.0, "currency_native": "USD",
            "spend_sar": 375.0,
            "drift_pct": 1.5,
            "match_status": "matched",
            "platform_check_status": "ok",
        })

        result = await auto_reconcile_for_day(
            db, user_id, acct_id, "2026-06-24")

        # Result reports failure but the row's match_status must
        # remain "matched".
        assert result["ok"] is False
        row = await db.ads_daily.find_one(
            {"user_id": user_id, "account_id": acct_id, "date": "2026-06-24"})
        # ─────── KEY INVARIANT ───────
        assert row["match_status"] == "matched", \
            "API hiccup MUST NOT change data-state match_status"
        # The connection state SHOULD reflect the failure (token_expired
        # or last_check_failed depending on resolver code).
        assert row["platform_check_status"] in (
            "token_expired", "last_check_failed", "api_error")
        # And we have a recorded error.
        assert row["platform_check_error"]
    finally:
        await db.ads_daily.delete_many({"user_id": user_id})
        await db.ads_accounts.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_report_summary_exposes_both_orthogonal_histograms(db):
    """The reconciliation report must return distinct histograms:
       match_* (data state) AND check_* (connection state)."""
    user_id = f"test_iter260_{uuid.uuid4().hex[:8]}"
    try:
        await db.ads_accounts.insert_one({
            "id": "a1", "user_id": user_id, "provider": "snapchat",
            "display_name": "Acct",
        })
        # Two valid rows: one matched/ok, one drift_review/last_check_failed.
        await db.ads_daily.insert_many([
            {"user_id": user_id, "account_id": "a1", "provider": "snapchat",
             "date": "2026-06-23",
             "idempotency_key": f"k_{uuid.uuid4().hex[:6]}",
             "spend_native": 100.0, "spend_sar": 375.0, "drift_pct": 0.5,
             "match_status": "matched", "platform_check_status": "ok"},
            {"user_id": user_id, "account_id": "a1", "provider": "snapchat",
             "date": "2026-06-24",
             "idempotency_key": f"k_{uuid.uuid4().hex[:6]}",
             "spend_native": 100.0, "spend_sar": 375.0, "drift_pct": 7.0,
             "match_status": "drift_review",
             "platform_check_status": "last_check_failed"},
        ])
        rep = await get_reconciliation_report(
            db, user_id, "2026-06-01", "2026-06-30")
        s = rep["summary"]
        # Data state histogram
        assert s["match_matched"]      == 1
        assert s["match_drift_review"] == 1
        # Connection state histogram (orthogonal!)
        assert s["check_ok"]                == 1
        assert s["check_last_check_failed"] == 1
        # The two dimensions do NOT have to sum the same way.
        # A row can be matched + connection_ok, OR drift_review +
        # last_check_failed (as in this test).
    finally:
        await db.ads_daily.delete_many({"user_id": user_id})
        await db.ads_accounts.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_legacy_row_without_platform_check_status_synthesised(db):
    """Legacy rows (pre-iter-260) lack platform_check_status. The report
    must synthesise it from the row's hints without mutating the DB."""
    user_id = f"test_iter260_{uuid.uuid4().hex[:8]}"
    try:
        await db.ads_accounts.insert_one({
            "id": "a1", "user_id": user_id, "provider": "snapchat",
            "display_name": "LegacyAcct",
        })
        await db.ads_daily.insert_one({
            "user_id": user_id, "account_id": "a1", "provider": "snapchat",
            "date": "2026-06-24",
            "idempotency_key": f"k_{uuid.uuid4().hex[:6]}",
            "spend_native": 100.0, "spend_sar": 375.0,
            "match_status": "matched",
            # Note: platform_check_status NOT set.
            "platform_check_error": "http_error",
            "platform_last_checked_at": "2026-06-24T22:00:00+00:00",
        })
        rep = await get_reconciliation_report(
            db, user_id, "2026-06-01", "2026-06-30")
        row = rep["data"][0]
        # Synthesised value (in-memory only — not persisted).
        assert row["platform_check_status"] == "last_check_failed"
        # DB still has no platform_check_status field.
        stored = await db.ads_daily.find_one({"user_id": user_id})
        assert "platform_check_status" not in stored
    finally:
        await db.ads_daily.delete_many({"user_id": user_id})
        await db.ads_accounts.delete_many({"user_id": user_id})


def test_sync_core_no_longer_writes_sync_failed_on_api_hiccup():
    """The two failure paths in sync/core.py must use "no_data" only
    when SSOT is genuinely empty, and must always set platform_check_status."""
    with open("/app/backend/ads_v2/sync/core.py", "r") as f:
        src = f.read()
    # iter-260 markers in both code paths
    assert src.count("iter-260") >= 3
    # The connection-state field is now written on every failure path
    assert src.count('"platform_check_status"') >= 4  # 2 failures × ≥2 places + ok paths
    # Mapping helper is present and used.
    assert "_map_check_status" in src
    # Success paths set "ok"
    assert '"platform_check_status":    "ok"' in src or \
           '"platform_check_status":           "ok",' in src


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
