"""Phase 1 — Drift logic + Ads Manager manual-value endpoint tests.

These tests focus on the structural correctness of the drift/reconciliation
logic added on top of Phase 1:

  • drift_pct is NULL (not 0) when no comparison anchor exists
  • Entering a manual Ads Manager value triggers an immediate
    drift recompute (no provider re-fetch)
  • drift_reason returns structured likely_causes
  • Reconciliation report exposes platform_manual_value_* fields
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest

API = (os.environ.get("REACT_APP_BACKEND_URL")
       or "http://localhost:8001").rstrip("/") + "/api"
LOGIN_EMAIL = "amasi.jewelery@gmail.com"
LOGIN_PWD = "10201917"


@pytest.fixture(scope="module")
def auth_headers():
    async def _get():
        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.post(
                f"{API}/auth/login",
                json={"email": LOGIN_EMAIL, "password": LOGIN_PWD},
            )
            tok = r.json().get("access_token") or r.json().get("token")
            return {"Authorization": f"Bearer {tok}"}
    return asyncio.run(_get())


@pytest.fixture(scope="module")
def http_client():
    return httpx.Client(base_url=API, timeout=60.0)


def _first_meta_account(http_client, auth_headers):
    r = http_client.get("/ads-v2/settings", headers=auth_headers)
    for a in r.json()["data"]["accounts"]:
        if a["provider"] == "meta" and a.get("sync_enabled"):
            return a
    pytest.skip("no Meta account available")


# ─────────────────────────────────────────────────────────────────────
# 1. drift_pct unit semantics
# ─────────────────────────────────────────────────────────────────────
def test_compute_anomaly_flags_no_manual_no_prev_returns_null_drift():
    """When there is NO previous sync and NO manual value, drift = None.
    The frontend renders None as "—" instead of "0%".
    """
    from backend.ads_v2.sync.core import _compute_anomaly_flags

    flags, dp, dm, st, reason = _compute_anomaly_flags(
        new_spend_native=687.37, prev_spend_native=None,
        has_fx=True, review_settings={}, hours_after_close=10.0,
        manual_value_native=None,
    )
    assert dp is None, "drift_pct_vs_previous_sync must be None"
    assert dm is None, "drift_pct_vs_manual must be None"
    assert reason["compared_against"] == "none"


def test_compute_anomaly_flags_with_manual_detects_mismatch():
    """723.43 expected vs 687.37 actual = 4.99% drift → just below 5%."""
    from backend.ads_v2.sync.core import _compute_anomaly_flags

    flags, dp, dm, st, reason = _compute_anomaly_flags(
        new_spend_native=687.37, prev_spend_native=687.37,
        has_fx=True, review_settings={
            "drift_warning_threshold_pct": 5.0,
            "drift_block_threshold_pct": 15.0,
        }, hours_after_close=48.0,
        manual_value_native=723.43,
    )
    # |687.37 - 723.43| / 723.43 = 0.04988 → 4.99% (below 5% warn threshold)
    assert 4.9 <= dm <= 5.1, f"drift_pct_vs_manual={dm}"
    assert reason["compared_against"] == "ads_manager_manual"


def test_compute_anomaly_flags_large_mismatch_flags_drift():
    from backend.ads_v2.sync.core import _compute_anomaly_flags

    flags, dp, dm, st, reason = _compute_anomaly_flags(
        new_spend_native=600.0, prev_spend_native=600.0,
        has_fx=True, review_settings={
            "drift_warning_threshold_pct": 5.0,
            "drift_block_threshold_pct": 15.0,
        }, hours_after_close=48.0,
        manual_value_native=800.0,
    )
    # 25% drift
    assert dm > 15
    assert "drift_above_15pct" in flags
    assert "mismatch_vs_ads_manager" in flags
    assert st == "held_anomaly"
    assert "ads_manager_value_differs" in reason["likely_causes"]


# ─────────────────────────────────────────────────────────────────────
# 2. Manual value endpoint
# ─────────────────────────────────────────────────────────────────────
def test_manual_value_endpoint_requires_existing_row(http_client, auth_headers):
    """POST /report/manual-value returns no_ads_daily_row_for_date when
    the row hasn't been synced yet — does NOT silently create.
    """
    acct = _first_meta_account(http_client, auth_headers)
    r = http_client.post(
        "/ads-v2/report/manual-value",
        json={
            "account_id": acct["id"],
            "date": "1999-01-01",            # impossible date — no sync
            "manual_value_native": 100.0,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is False
    assert data["error"] == "no_ads_daily_row_for_date"


def test_manual_value_endpoint_updates_drift_and_reason(http_client, auth_headers):
    """Sync a day first, then POST manual-value and verify drift & reason."""
    acct = _first_meta_account(http_client, auth_headers)

    # sync a recent date — adapter may return zero or real spend, both fine.
    http_client.post(
        f"/ads-v2/sync/account/{acct['id']}/day/2026-06-22",
        headers=auth_headers,
    )

    # Submit a fake Ads Manager value bigger than current row spend
    r = http_client.post(
        "/ads-v2/report/manual-value",
        json={
            "account_id": acct["id"], "date": "2026-06-22",
            "manual_value_native": 1234.56,
            "note": "test_unit_iteration",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is True
    assert data["manual_value_native"] == 1234.56
    # drift_pct_vs_manual computed (may be None only if spend==1234.56)
    assert "drift_pct_vs_manual" in data
    assert "drift_reason" in data
    assert data["drift_reason"]["has_manual_value"] is True
    assert data["drift_reason"]["compared_against"] == "ads_manager_manual"


def test_reconciliation_exposes_manual_fields(http_client, auth_headers):
    """The reconciliation report must include manual value fields and
    `has_manual_value` flag.
    """
    r = http_client.get(
        "/ads-v2/report/reconciliation?date_from=2026-06-22&date_to=2026-06-22",
        headers=auth_headers,
    )
    data = r.json()["data"]
    assert "data" in data
    # All rows expose the new fields (even if null)
    for row in data["data"]:
        assert "has_manual_value" in row
        assert "drift_pct_vs_manual" in row
        assert "drift_reason" in row
    # Summary includes the new counters
    s = data["summary"]
    assert "rows_with_manual_value" in s
    assert "rows_pending_manual" in s


def test_reconciliation_report_no_drift_inflation(http_client, auth_headers):
    """Rows without a manual value MUST NOT report drift_pct=0 — it
    should be None so the UI shows '—'. This is the user's #1 complaint.
    """
    r = http_client.get(
        "/ads-v2/report/reconciliation?date_from=2026-06-19&date_to=2026-06-23",
        headers=auth_headers,
    )
    rows = r.json()["data"]["data"]
    for row in rows:
        if not row.get("has_manual_value"):
            # drift_pct_vs_manual must be None when no manual entered
            assert row.get("drift_pct_vs_manual") in (None,), \
                f"Row {row['date']}/{row['account_id']} has drift_pct_vs_manual=" \
                f"{row.get('drift_pct_vs_manual')} but no manual value"
