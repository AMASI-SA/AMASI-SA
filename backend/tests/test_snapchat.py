"""Snapchat Marketing API integration tests.

Real OAuth exchange with snapchat.com is NOT tested (no real credentials).
We test: route mounting, auth requirement, persistence (config),
state JWT generation/decode, error redirects on callback, per-user isolation,
and Arabic error messages when not connected.
"""
import os
import uuid
import pytest
import requests
from urllib.parse import urlparse, parse_qs

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


# ── Helpers / Fixtures ─────────────────────────────────────────────────────
def _register_user():
    email = f"TEST_snap_{uuid.uuid4().hex[:8]}@hesab.app"
    r = requests.post(f"{API}/auth/register", json={
        "name": "Snap Test", "email": email, "password": "snappw123"
    })
    assert r.status_code == 200, r.text
    return {"email": email, "token": r.json()["access_token"], "id": r.json()["id"]}


@pytest.fixture(scope="module")
def user_a():
    return _register_user()


@pytest.fixture(scope="module")
def user_b():
    return _register_user()


def _h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ── Auth requirement ──────────────────────────────────────────────────────
class TestSnapchatAuth:
    def test_config_requires_auth(self):
        r = requests.get(f"{API}/snapchat/config")
        assert r.status_code == 401

    def test_authorize_requires_auth(self):
        r = requests.get(f"{API}/snapchat/authorize-url")
        assert r.status_code == 401

    def test_adaccounts_requires_auth(self):
        r = requests.get(f"{API}/snapchat/adaccounts")
        assert r.status_code == 401

    def test_daily_spend_requires_auth(self):
        r = requests.get(f"{API}/snapchat/daily-spend?date=2026-01-10")
        assert r.status_code == 401


# ── /config CRUD ──────────────────────────────────────────────────────────
class TestSnapchatConfig:
    def test_get_config_empty(self, user_a):
        r = requests.get(f"{API}/snapchat/config", headers=_h(user_a["token"]))
        assert r.status_code == 200
        d = r.json()
        assert d["connected"] is False
        assert d["has_credentials"] is False
        assert d["client_id"] == ""
        assert d["redirect_uri"] == ""

    def test_post_config_saves(self, user_a):
        payload = {
            "client_id": "TEST_snap_app_id",
            "client_secret": "TEST_snap_app_secret",
            "redirect_uri": "https://example.com/api/snapchat/oauth/callback",
        }
        r = requests.post(f"{API}/snapchat/config", headers=_h(user_a["token"]), json=payload)
        assert r.status_code == 200
        assert r.json().get("ok") is True

        g = requests.get(f"{API}/snapchat/config", headers=_h(user_a["token"]))
        assert g.status_code == 200
        d = g.json()
        assert d["has_credentials"] is True
        assert d["connected"] is False  # no refresh_token yet
        assert d["client_id"] == "TEST_snap_app_id"
        assert d["redirect_uri"] == payload["redirect_uri"]
        # client_secret must NEVER be returned
        assert "client_secret" not in d

    def test_post_config_validation(self, user_a):
        r = requests.post(f"{API}/snapchat/config", headers=_h(user_a["token"]), json={
            "client_id": "", "client_secret": "x", "redirect_uri": "y"
        })
        assert r.status_code == 422

    def test_user_isolation(self, user_a, user_b):
        """user_b should not see user_a's config."""
        r = requests.get(f"{API}/snapchat/config", headers=_h(user_b["token"]))
        assert r.status_code == 200
        d = r.json()
        assert d["has_credentials"] is False
        assert d["client_id"] == ""


# ── /authorize-url ────────────────────────────────────────────────────────
class TestAuthorizeUrl:
    def test_authorize_url_requires_config(self, user_b):
        # user_b never saved config
        r = requests.get(f"{API}/snapchat/authorize-url", headers=_h(user_b["token"]))
        assert r.status_code == 400
        assert "App ID" in r.json().get("detail", "") or "أولاً" in r.json().get("detail", "")

    def test_authorize_url_ok(self, user_a):
        r = requests.get(f"{API}/snapchat/authorize-url", headers=_h(user_a["token"]))
        assert r.status_code == 200
        url = r.json()["authorize_url"]
        assert url.startswith("https://accounts.snapchat.com/login/oauth2/authorize?")
        parsed = parse_qs(urlparse(url).query)
        assert parsed["response_type"] == ["code"]
        assert parsed["client_id"] == ["TEST_snap_app_id"]
        assert parsed["scope"] == ["snapchat-marketing-api"]
        assert parsed["redirect_uri"] == ["https://example.com/api/snapchat/oauth/callback"]
        assert "state" in parsed and len(parsed["state"][0]) > 20  # JWT


# ── /oauth/callback ───────────────────────────────────────────────────────
class TestOAuthCallback:
    def test_callback_missing_params(self):
        r = requests.get(f"{API}/snapchat/oauth/callback", allow_redirects=False)
        # Should redirect (no 500)
        assert r.status_code in (302, 307)
        loc = r.headers.get("location", "")
        assert "/settings" in loc and "snapchat=error" in loc

    def test_callback_invalid_state(self):
        r = requests.get(
            f"{API}/snapchat/oauth/callback",
            params={"code": "fake_code", "state": "not-a-jwt"},
            allow_redirects=False,
        )
        # Decode fails → HTTPException 400 (not redirect). Either way: not 500.
        assert r.status_code != 500
        assert r.status_code in (302, 307, 400)

    def test_callback_oauth_error(self):
        r = requests.get(
            f"{API}/snapchat/oauth/callback",
            params={"error": "access_denied", "error_description": "user_denied"},
            allow_redirects=False,
        )
        assert r.status_code in (302, 307)
        loc = r.headers.get("location", "")
        assert "snapchat=error" in loc


# ── /adaccounts & /daily-spend / select-adaccount ─────────────────────────
class TestSnapchatData:
    def test_adaccounts_no_connection(self, user_b):
        r = requests.get(f"{API}/snapchat/adaccounts", headers=_h(user_b["token"]))
        assert r.status_code == 400
        assert "سناب" in r.json().get("detail", "")

    def test_adaccounts_config_no_refresh(self, user_a):
        # user_a has config saved but no refresh_token
        r = requests.get(f"{API}/snapchat/adaccounts", headers=_h(user_a["token"]))
        assert r.status_code == 400
        assert "سناب" in r.json().get("detail", "")

    def test_daily_spend_no_connection(self, user_b):
        r = requests.get(f"{API}/snapchat/daily-spend?date=2026-01-10", headers=_h(user_b["token"]))
        assert r.status_code == 400

    def test_daily_spend_invalid_date(self, user_a):
        r = requests.get(f"{API}/snapchat/daily-spend?date=BAD_DATE", headers=_h(user_a["token"]))
        # Either 400 from date parse OR 400 from no refresh_token (checked first)
        assert r.status_code == 400

    def test_select_adaccount_requires_connection(self, user_b):
        r = requests.post(
            f"{API}/snapchat/select-adaccount",
            headers=_h(user_b["token"]),
            json={"ad_account_id": "ACC_X", "ad_account_name": "X"},
        )
        assert r.status_code == 400


# ── DELETE /config ────────────────────────────────────────────────────────
class TestSnapchatDisconnect:
    def test_disconnect(self, user_a):
        r = requests.delete(f"{API}/snapchat/config", headers=_h(user_a["token"]))
        assert r.status_code == 200
        # Verify gone
        g = requests.get(f"{API}/snapchat/config", headers=_h(user_a["token"]))
        assert g.json()["has_credentials"] is False
