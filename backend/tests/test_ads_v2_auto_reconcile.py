"""Phase 1 — Auto-reconciliation tests.

Validates the NEW auto-reconcile flow that replaces "manual-input-as-default":
  • POST /api/ads-v2/report/auto-reconcile re-queries provider APIs
  • Updates only shadow fields (`platform_authoritative_*`), never spend_native
  • Computes match_status (matched/pending_platform/drift_review/sync_failed)
  • Works identically across Meta / Snapchat / TikTok via the dispatcher
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


# ─────────────────────────────────────────────────────────────────────
# 1. match_status computation
# ─────────────────────────────────────────────────────────────────────
def test_compute_match_status_all_branches():
    from backend.ads_v2.sync.core import _compute_match_status

    rs = {"drift_warning_threshold_pct": 5.0}
    # sync_failed beats everything
    assert _compute_match_status(None, rs, "final", 100, True, True) == "sync_failed"
    # no_data
    assert _compute_match_status(None, rs, "final", 100, False, False) == "no_data"
    # drift_review (drift >= warn)
    assert _compute_match_status(7.5, rs, "final", 100, False, True) == "drift_review"
    # pending_platform (provisional + day still open)
    assert _compute_match_status(1.0, rs, "provisional", 10, False, True) == "pending_platform"
    # matched
    assert _compute_match_status(0.0, rs, "final", 100, False, True) == "matched"
    # matched (drift None counts as matched)
    assert _compute_match_status(None, rs, "final", 100, False, True) == "matched"


# ─────────────────────────────────────────────────────────────────────
# 2. /report/auto-reconcile endpoint
# ─────────────────────────────────────────────────────────────────────
def test_auto_reconcile_bulk_endpoint_returns_counts(http_client, auth_headers):
    """POST /report/auto-reconcile returns matched/drift/pending/failed counts."""
    r = http_client.post(
        "/ads-v2/report/auto-reconcile",
        json={"dates": ["2026-06-22"]},
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert "checked_count" in data
    assert "matched_count" in data
    assert "drift_count" in data
    assert "pending_count" in data
    assert "failed_count" in data
    assert "results" in data
    # Sum equals checked_count
    total = data["matched_count"] + data["drift_count"] + data["pending_count"] + data["failed_count"]
    assert total == data["checked_count"]


def test_auto_reconcile_requires_dates(http_client, auth_headers):
    r = http_client.post(
        "/ads-v2/report/auto-reconcile",
        json={},  # missing dates
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_auto_reconcile_single_day_does_not_modify_spend_native(http_client, auth_headers):
    """The most important invariant: auto-reconcile is READ-ONLY against SSOT."""
    # Find a Meta account
    settings = http_client.get("/ads-v2/settings", headers=auth_headers).json()
    meta = next((a for a in settings["data"]["accounts"]
                  if a["provider"] == "meta" and a.get("sync_enabled")), None)
    if not meta:
        pytest.skip("no Meta account")

    # Ensure a row exists for the test date
    http_client.post(
        f"/ads-v2/sync/account/{meta['id']}/day/2026-06-22",
        headers=auth_headers,
    )

    # Read spend_native BEFORE reconcile
    before = http_client.get(
        "/ads-v2/report/daily?date_from=2026-06-22&date_to=2026-06-22",
        headers=auth_headers,
    ).json()["data"]["data"]
    before_row = next((x for x in before if x["account_id"] == meta["id"]), None)
    assert before_row is not None, "expected an ads_daily row for the test date"
    spend_before = before_row["spend_native"]

    # Auto-reconcile
    r = http_client.post(
        f"/ads-v2/report/auto-reconcile/account/{meta['id']}/day/2026-06-22",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert "platform_authoritative_native" in data
    assert "diff_native" in data
    assert "diff_sar" in data
    assert "match_status" in data

    # Read AFTER — spend_native MUST be unchanged (SSOT preserved)
    after = http_client.get(
        "/ads-v2/report/daily?date_from=2026-06-22&date_to=2026-06-22",
        headers=auth_headers,
    ).json()["data"]["data"]
    after_row = next((x for x in after if x["account_id"] == meta["id"]), None)
    assert after_row is not None
    spend_after = after_row["spend_native"]
    assert spend_before == spend_after, (
        "auto-reconcile must NOT change spend_native (the SSOT). "
        f"before={spend_before} after={spend_after}"
    )
    # The shadow fields must be populated
    assert after_row.get("platform_last_checked_at") is not None
    assert "match_status" in after_row


def test_reconciliation_report_exposes_match_status(http_client, auth_headers):
    """Reconciliation report rows have match_status + summary has counters."""
    r = http_client.get(
        "/ads-v2/report/reconciliation?date_from=2026-06-19&date_to=2026-06-23",
        headers=auth_headers,
    )
    data = r.json()["data"]
    for row in data["data"]:
        assert "match_status" in row
        assert row["match_status"] in (
            "matched", "pending_platform", "drift_review",
            "sync_failed", "no_data", None,
        )
    s = data["summary"]
    for k in ("match_matched", "match_pending_platform",
              "match_drift_review", "match_sync_failed"):
        assert k in s, f"missing summary counter {k}"


# ─────────────────────────────────────────────────────────────────────
# 3. Invariants — Phase 1 GL + V1 untouched after auto-reconcile
# ─────────────────────────────────────────────────────────────────────
def test_auto_reconcile_does_not_write_general_ledger(http_client, auth_headers):
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")

    async def _count():
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        return await db.general_ledger.count_documents(
            {"metadata.source": "ads_v2"})
    before = asyncio.run(_count())

    http_client.post(
        "/ads-v2/report/auto-reconcile",
        json={"dates": ["2026-06-22"]},
        headers=auth_headers,
    )
    after = asyncio.run(_count())
    assert before == after
    assert after == 0
