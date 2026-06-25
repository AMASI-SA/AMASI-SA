"""Phase 1 — 3-tier status + diagnose endpoint tests."""
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


def _meta_acct(http_client, auth_headers):
    r = http_client.get("/ads-v2/settings", headers=auth_headers)
    for a in r.json()["data"]["accounts"]:
        if a["provider"] == "meta":
            return a
    pytest.skip("no meta account")


# ─────────────────────────────────────────────────────────────────────
# 1. Settings snapshot exposes 3-tier _status on every account
# ─────────────────────────────────────────────────────────────────────
def test_settings_snapshot_includes_3tier_status(http_client, auth_headers):
    r = http_client.get("/ads-v2/settings", headers=auth_headers)
    accounts = r.json()["data"]["accounts"]
    assert len(accounts) > 0
    for a in accounts:
        s = a.get("_status")
        assert s is not None, f"account {a['id']} missing _status"
        assert s.get("token") in (
            "ok", "expired", "needs_relink", "missing", None,
        )
        assert s.get("connection") in (
            "connected", "unreachable", "timeout", "api_error", "unknown", None,
        )
        assert s.get("sync_run") in (
            "synced", "awaiting_first", "no_data", "last_failed", "disabled", None,
        )
        assert "reason" in s
        assert "days_with_data_30d" in s


# ─────────────────────────────────────────────────────────────────────
# 2. /diagnose endpoint
# ─────────────────────────────────────────────────────────────────────
def test_diagnose_returns_full_report(http_client, auth_headers):
    acct = _meta_acct(http_client, auth_headers)
    r = http_client.post(
        f"/ads-v2/settings/accounts/{acct['id']}/diagnose",
        headers=auth_headers,
    )
    assert r.status_code == 200
    d = r.json()["data"]
    # 3-tier
    assert "status" in d
    for k in ("token", "connection", "sync_run", "reason"):
        assert k in d["status"]
    # token_check
    assert "token_check" in d
    # live api probe
    assert "api_probe" in d
    assert "ok" in d["api_probe"]
    assert "date_tested" in d["api_probe"]
    # stats
    assert "stats" in d
    for k in ("days_in_last_30d", "days_with_spend",
              "total_daily_rows", "last_synced_date"):
        assert k in d["stats"]
    # last events
    assert "last_events" in d
    assert isinstance(d["last_events"], list)


def test_diagnose_unknown_account_returns_404(http_client, auth_headers):
    r = http_client.post(
        "/ads-v2/settings/accounts/does_not_exist_xxx/diagnose",
        headers=auth_headers,
    )
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────
# 3. Invariant — diagnose does not modify any ads_daily / V1 docs
# ─────────────────────────────────────────────────────────────────────
def test_diagnose_is_readonly_for_ads_daily(http_client, auth_headers):
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")

    acct = _meta_acct(http_client, auth_headers)

    async def _snap():
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        ads_daily = await db.ads_daily.count_documents(
            {"user_id": acct["user_id"], "account_id": acct["id"]})
        meta = await db.meta_connections.count_documents({})
        return ads_daily, meta

    before = asyncio.run(_snap())
    http_client.post(
        f"/ads-v2/settings/accounts/{acct['id']}/diagnose",
        headers=auth_headers,
    )
    after = asyncio.run(_snap())
    assert before == after, f"diagnose changed counts: {before} → {after}"


# ─────────────────────────────────────────────────────────────────────
# 4. _compute_account_status — pure logic unit tests
# ─────────────────────────────────────────────────────────────────────
def test_compute_account_status_token_ok_synced():
    from backend.ads_v2.data_layer.settings import _compute_account_status
    s = _compute_account_status(
        {
            "v1_token_ref": {"collection": "meta_connections"},
            "sync_enabled": True, "sync_status": "active",
            "last_synced_date": "2026-06-22",
        },
        [
            {"event": "sync_run", "details": {"api_status": "ok"}},
        ],
        days_with_data_30d=7,
    )
    assert s["token"] == "ok"
    assert s["connection"] == "connected"
    assert s["sync_run"] == "synced"
    assert s["reason"] == "ok"


def test_compute_account_status_token_invalid_means_needs_relink():
    from backend.ads_v2.data_layer.settings import _compute_account_status
    s = _compute_account_status(
        {
            "v1_token_ref": {"collection": "snapchat_connections"},
            "sync_enabled": True, "sync_status": "unauthorized",
            "last_synced_date": None,
        },
        [
            {"event": "sync_failed",
             "details": {"api_status": "token_invalid"}},
        ],
        days_with_data_30d=0,
    )
    # Token health is downgraded to needs_relink because of recent 401
    assert s["token"] == "needs_relink"
    # Sync status is last_failed (sync_status='unauthorized' triggers it)
    assert s["sync_run"] == "last_failed"
    # Reason should NOT be a bare "خطأ" — it's the specific cause
    assert s["reason"] == "token_needs_relink"


def test_compute_account_status_token_ok_but_no_data():
    """The exact case the user complained about — token OK, but
    something else is off. Status should explain WHY, not say "خطأ".
    """
    from backend.ads_v2.data_layer.settings import _compute_account_status
    s = _compute_account_status(
        {
            "v1_token_ref": {"collection": "meta_connections"},
            "sync_enabled": True, "sync_status": "active",
            "last_synced_date": "2026-06-22",
        },
        [
            {"event": "sync_run", "details": {"api_status": "empty"}},
        ],
        days_with_data_30d=0,    # no data rows
    )
    assert s["token"] == "ok"
    assert s["connection"] == "connected"
    assert s["connection_reason"] == "no_data_for_date"
    assert s["sync_run"] == "no_data"
    assert s["reason"] == "no_data_for_account"


def test_compute_account_status_disabled_sync():
    from backend.ads_v2.data_layer.settings import _compute_account_status
    s = _compute_account_status(
        {
            "v1_token_ref": {"collection": "meta_connections"},
            "sync_enabled": False, "sync_status": "paused",
        },
        [], days_with_data_30d=0,
    )
    assert s["sync_run"] == "disabled"
    assert s["reason"] == "sync_disabled"
