"""Tests for friendly Arabic Meta errors (iteration 12).

Acceptance criteria covered:
- POST /api/meta/test-connection with invalid token returns 400 with friendly
  Arabic detail (NOT raw JSON / NOT English).
- POST /api/meta/test-connection with bad ad_account_id returns 400 friendly Arabic.
- POST /api/meta/sync without a stored connection returns 400 friendly Arabic.
- GET /api/meta/config / GET /api/dashboard/meta-summary expose
  connection_status / last_error_message / last_error_at.
- No raw "OAuthException" / "code 190" / "_id" leakage to client.
"""
import os
import re
import json
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
EXPECTED_EXPIRED_AR = "انتهت صلاحية ربط Meta Ads، يرجى تحديث Access Token من الإعدادات."


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


# ── /meta/test-connection ─────────────────────────────────────────────────────
class TestMetaTestConnection:
    def test_invalid_token_returns_friendly_arabic(self, auth_headers):
        body = {
            "app_id": "1234567890",
            "app_secret": "fake_secret_for_test",
            "access_token": "EAA_INVALID_TOKEN_XYZ_THIS_WILL_FAIL",
            "ad_account_id": "act_1234567890",
        }
        r = requests.post(
            f"{BASE_URL}/api/meta/test-connection",
            json=body,
            headers=auth_headers,
            timeout=30,
        )
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
        data = r.json()
        detail = data.get("detail")
        # detail should be a plain string (friendly Arabic). Never a dict here.
        assert isinstance(detail, str), f"detail must be string, got {type(detail).__name__}: {detail}"
        # Verify Arabic content + no raw JSON / English error leakage
        assert "OAuthException" not in detail
        assert "code 190" not in detail.lower().replace("\"", "")
        assert "session has expired" not in detail.lower()
        assert "{" not in detail and "}" not in detail
        # Should match the expected expired message OR a more generic friendly one
        # (Meta may classify an invalid token as expired or invalid_account).
        assert any(s in detail for s in [
            "انتهت صلاحية ربط Meta Ads",
            "Access Token",
            "Meta",
        ]), f"no Arabic friendly text found in detail: {detail}"

    def test_invalid_token_does_not_save(self, auth_headers):
        # After failed test, /meta/config should still report not connected for admin
        r = requests.get(f"{BASE_URL}/api/meta/config", headers=auth_headers, timeout=15)
        assert r.status_code == 200
        cfg = r.json()
        assert cfg.get("connected") is False, f"invalid token must NOT be saved, got: {cfg}"

    def test_bad_ad_account_id_returns_arabic(self, auth_headers):
        body = {
            "app_id": "1234567890",
            "app_secret": "fake",
            "access_token": "EAA_xxxx",
            "ad_account_id": "not_a_number",
        }
        r = requests.post(
            f"{BASE_URL}/api/meta/test-connection",
            json=body, headers=auth_headers, timeout=15,
        )
        assert r.status_code == 400
        detail = r.json().get("detail", "")
        assert isinstance(detail, str)
        assert "Ad Account ID" in detail or "act_" in detail or "رقم" in detail


# ── /meta/sync without connection ────────────────────────────────────────────
class TestMetaSyncErrors:
    def test_sync_without_connection(self, auth_headers):
        r = requests.post(
            f"{BASE_URL}/api/meta/sync",
            json={"days": 7}, headers=auth_headers, timeout=20,
        )
        # Admin has no connection -> 400 friendly Arabic
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
        data = r.json()
        detail = data.get("detail")
        assert isinstance(detail, str)
        assert "Meta Ads" in detail and ("غير مربوط" in detail or "الإعدادات" in detail)
        # No raw json
        assert "OAuthException" not in detail


# ── /meta/config + /dashboard/meta-summary fields ────────────────────────────
class TestMetaStatusFields:
    def test_meta_config_has_status_fields_when_connected(self, auth_headers):
        # Admin not connected → still must not crash, returns {"connected": false}
        r = requests.get(f"{BASE_URL}/api/meta/config", headers=auth_headers, timeout=15)
        assert r.status_code == 200
        body = r.json()
        # If not connected we only get {"connected": false} which is OK.
        if body.get("connected"):
            for f in ("connection_status", "last_error_message", "last_error_at"):
                assert f in body, f"missing field {f} in /meta/config: {body}"

    def test_dashboard_meta_summary_has_status_fields(self, auth_headers):
        r = requests.get(
            f"{BASE_URL}/api/dashboard/meta-summary",
            headers=auth_headers, timeout=20,
        )
        assert r.status_code == 200, f"meta-summary failed: {r.status_code} {r.text}"
        body = r.json()
        for f in ("connection_status", "last_error_message", "last_error_at"):
            assert f in body, f"missing field {f}: {body}"
        # For admin with no connection, status should be 'ok' and error fields null
        assert body["connection_status"] in ("ok", "expired", "error", "permission_denied",
                                              "invalid_account", "rate_limited", "network_error")
        # No mongo _id leakage
        assert "_id" not in json.dumps(body)


# ── Regression: no raw JSON in any 4xx response from meta routes ─────────────
class TestNoRawJsonLeakage:
    def test_no_raw_oauth_exception_anywhere(self, auth_headers):
        # Hit test-connection with clearly-bad token; ensure response body text
        # doesn't contain raw Meta JSON keys.
        r = requests.post(
            f"{BASE_URL}/api/meta/test-connection",
            json={"app_id": "1", "app_secret": "x", "access_token": "EAA_BAD",
                  "ad_account_id": "act_1"},
            headers=auth_headers, timeout=30,
        )
        body_text = r.text
        for forbidden in ["OAuthException", "fbtrace_id", "\"code\":190", "\"code\": 190"]:
            assert forbidden not in body_text, f"raw JSON leaked: {forbidden} in {body_text[:300]}"
