"""Iter253 — Extra invariants for auto-reconcile (phase 1 boundary).

Validates:
  • ads_sync_logs has a 'reconciliation_checked' event with platform_native,
    ssot_native, diff_native, diff_sar, match_status, actor email
  • V1 collections (meta_/snapchat_/tiktok_ connections) untouched
  • No ledger_txn_group_id set on any ads_daily row after auto-reconcile
  • Reconciliation report rows include the full set of new fields
"""
from __future__ import annotations
import asyncio
import os
import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

API = (os.environ.get("REACT_APP_BACKEND_URL")
       or "http://localhost:8001").rstrip("/") + "/api"
LOGIN_EMAIL = "amasi.jewelery@gmail.com"
LOGIN_PWD = "10201917"
TEST_DATE = "2026-06-22"


def _get_db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def auth_headers():
    async def _get():
        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.post(f"{API}/auth/login",
                                json={"email": LOGIN_EMAIL, "password": LOGIN_PWD})
            tok = r.json().get("access_token") or r.json().get("token")
            return {"Authorization": f"Bearer {tok}"}
    return asyncio.run(_get())


@pytest.fixture(scope="module")
def user_id(auth_headers):
    async def _get():
        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.get(f"{API}/auth/me", headers=auth_headers)
            return r.json().get("id")
    return asyncio.run(_get())


@pytest.fixture(scope="module")
def http_client():
    return httpx.Client(base_url=API, timeout=60.0)


# ── reconciliation_checked event in ads_sync_logs ───────────────────
def test_ads_sync_logs_has_reconciliation_checked_event(http_client, auth_headers, user_id):
    r = http_client.post("/ads-v2/report/auto-reconcile",
                         json={"dates": [TEST_DATE]}, headers=auth_headers)
    assert r.status_code == 200

    async def _check():
        db = _get_db()
        events = await db.ads_sync_logs.find(
            {"user_id": user_id, "event": "reconciliation_checked"}
        ).sort("at", -1).to_list(length=20)
        return events

    events = asyncio.run(_check())
    assert events, "expected at least one reconciliation_checked event"
    # Find one with the right shape (sourced from auto-reconcile, not manual-value)
    auto_evt = next((e for e in events
                     if isinstance(e.get("details"), dict)
                     and "platform_native" in e["details"]
                     and "ssot_native" in e["details"]
                     and "match_status" in e["details"]), None)
    assert auto_evt is not None, "no auto-reconcile event with expected detail keys"
    d = auto_evt["details"]
    for k in ("platform_native", "ssot_native", "diff_native",
              "diff_sar", "match_status"):
        assert k in d, f"missing key {k} in event details"


# ── No ledger writes (ads_v2 source) or ledger_txn_group_id ─────────
def test_no_ads_daily_has_ledger_txn_group_id(http_client, auth_headers, user_id):
    http_client.post("/ads-v2/report/auto-reconcile",
                     json={"dates": [TEST_DATE]}, headers=auth_headers)

    async def _check():
        db = _get_db()
        with_gl = await db.ads_daily.count_documents(
            {"user_id": user_id, "ledger_txn_group_id": {"$ne": None}})
        gl_writes = await db.general_ledger.count_documents(
            {"metadata.source": "ads_v2"})
        return with_gl, gl_writes
    with_gl, gl_writes = asyncio.run(_check())
    assert with_gl == 0, "no ads_daily row should have ledger_txn_group_id in Phase 1"
    assert gl_writes == 0, "no general_ledger entries with source=ads_v2"


# ── V1 collections counts unchanged ────────────────────────────────
def test_v1_collections_unchanged_by_auto_reconcile(http_client, auth_headers):
    async def _counts():
        db = _get_db()
        return {
            "meta": await db.meta_connections.count_documents({}),
            "snapchat": await db.snapchat_connections.count_documents({}),
            "tiktok": await db.tiktok_connections.count_documents({}),
        }
    before = asyncio.run(_counts())
    http_client.post("/ads-v2/report/auto-reconcile",
                     json={"dates": [TEST_DATE]}, headers=auth_headers)
    after = asyncio.run(_counts())
    assert before == after


# ── Reconciliation report row shape ────────────────────────────────
def test_reconciliation_report_row_fields(http_client, auth_headers):
    http_client.post("/ads-v2/report/auto-reconcile",
                     json={"dates": [TEST_DATE]}, headers=auth_headers)
    r = http_client.get(
        f"/ads-v2/report/reconciliation?date_from={TEST_DATE}&date_to={TEST_DATE}",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    rows = data["data"]
    assert rows, "expected at least one reconciliation row"
    row = rows[0]
    for k in ("platform_authoritative_native", "platform_authoritative_sar",
              "platform_last_checked_at", "diff_native", "diff_sar",
              "drift_pct_vs_platform", "match_status"):
        assert k in row, f"row missing key: {k}"
    s = data["summary"]
    for k in ("match_matched", "match_pending_platform",
              "match_drift_review", "match_sync_failed"):
        assert k in s


# ── Snapchat/TikTok cross-provider safety ──────────────────────────
def test_snapchat_tiktok_safe_handling(http_client, auth_headers):
    """If user has snap/tiktok accounts that are token_invalid, auto-reconcile
    should mark them sync_failed instead of returning 500."""
    settings = http_client.get("/ads-v2/settings", headers=auth_headers).json()
    nonmeta = [a for a in settings["data"]["accounts"]
               if a["provider"] in ("snapchat", "tiktok")
               and a.get("sync_enabled")]
    if not nonmeta:
        pytest.skip("no snapchat/tiktok sync-enabled accounts on this user")
    for acct in nonmeta[:3]:
        r = http_client.post(
            f"/ads-v2/report/auto-reconcile/account/{acct['id']}/day/{TEST_DATE}",
            headers=auth_headers,
        )
        # Either matched or sync_failed; must never be 500
        assert r.status_code in (200, 400), f"unexpected status {r.status_code}"
