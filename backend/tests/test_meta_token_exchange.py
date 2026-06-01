"""Tests for POST /api/meta/exchange-token (Short-lived → Long-lived).

Acceptance criteria covered:
- Empty / too-short token → 400 with friendly Arabic.
- Missing stored creds + no payload creds → 400 with friendly Arabic.
- Bad ad_account_id format → 400 with friendly Arabic.
- Valid request shape but fake creds → 400 (Meta classified) with friendly Arabic;
  never leaks raw OAuthException / English JSON to the client.
- GET /api/meta/config exposes token_expires_at / token_exchanged_at keys.

Run: pytest /app/backend/tests/test_meta_token_exchange.py -v
"""
from __future__ import annotations

import os
import re
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@hesab.app", "password": "admin123"},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


def _is_friendly_arabic(text: str) -> bool:
    """The detail must contain Arabic characters and MUST NOT contain raw
    Meta error markers (OAuthException, _id, raw JSON braces with English keys)."""
    has_arabic = bool(re.search(r"[\u0600-\u06FF]", text))
    leaks_raw = any(marker in text for marker in (
        "OAuthException", "\"_id\"", "Traceback", "\"error\":",
    ))
    return has_arabic and not leaks_raw


class TestExchangeTokenValidation:
    def test_empty_token_returns_friendly_arabic(self, auth_headers):
        r = requests.post(
            f"{BASE_URL}/api/meta/exchange-token",
            headers=auth_headers,
            json={"short_lived_token": ""},
            timeout=15,
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert isinstance(detail, str)
        assert _is_friendly_arabic(detail)
        assert "Short-lived" in detail or "قصير" in detail

    def test_too_short_token_returns_friendly_arabic(self, auth_headers):
        r = requests.post(
            f"{BASE_URL}/api/meta/exchange-token",
            headers=auth_headers,
            json={"short_lived_token": "abc"},
            timeout=15,
        )
        assert r.status_code == 400
        assert _is_friendly_arabic(r.json()["detail"])

    def test_missing_app_creds_returns_friendly_arabic(self, auth_headers):
        # Long enough token (>20 chars) but no stored config + no creds in payload
        r = requests.post(
            f"{BASE_URL}/api/meta/exchange-token",
            headers=auth_headers,
            json={"short_lived_token": "EAA" + "x" * 40},
            timeout=15,
        )
        assert r.status_code == 400
        d = r.json()["detail"]
        assert _is_friendly_arabic(d)
        assert "App ID" in d and "App Secret" in d

    def test_bad_ad_account_id_returns_friendly_arabic(self, auth_headers):
        r = requests.post(
            f"{BASE_URL}/api/meta/exchange-token",
            headers=auth_headers,
            json={
                "short_lived_token": "EAA" + "x" * 40,
                "app_id": "1234567890",
                "app_secret": "fakefakefake",
                "ad_account_id": "NOT_A_NUMBER",
            },
            timeout=15,
        )
        assert r.status_code == 400
        d = r.json()["detail"]
        assert _is_friendly_arabic(d)
        assert "Ad Account" in d

    def test_fake_creds_returns_classified_meta_error(self, auth_headers):
        """Hit Meta with bogus creds — should classify the error and return
        a friendly Arabic detail, NEVER raw OAuthException/JSON."""
        r = requests.post(
            f"{BASE_URL}/api/meta/exchange-token",
            headers=auth_headers,
            json={
                "short_lived_token": "EAA" + "x" * 50,
                "app_id": "1234567890",
                "app_secret": "fakefakefake",
                "ad_account_id": "act_1234567890",
            },
            timeout=30,
        )
        assert r.status_code == 400
        d = r.json()["detail"]
        assert isinstance(d, str)
        assert _is_friendly_arabic(d)


class TestMetaConfigExposesTokenExpiry:
    def test_config_response_shape(self, auth_headers):
        """GET /api/meta/config — when disconnected, returns {connected:false};
        when connected (in any state), exposes token_expires_at + token_exchanged_at."""
        r = requests.get(
            f"{BASE_URL}/api/meta/config",
            headers={"Authorization": auth_headers["Authorization"]},
            timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        if body.get("connected"):
            # Connected path must expose the new keys (may be null if never exchanged).
            assert "token_expires_at" in body
            assert "token_exchanged_at" in body
        else:
            # Disconnected path is allowed to short-circuit to {connected:false}.
            assert body.get("connected") is False
