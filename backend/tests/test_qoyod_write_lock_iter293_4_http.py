"""Iter-293.4 — HTTP-level verification of Global Qoyod Production Write Lock.

Confines tests to Mezan internal API surface — does NOT call api.qoyod.com.
Uses admin@hesab.app credentials. Verifies:
    - PUT /settings persists `production_writes_locked` true/false toggling
    - GET /admin/write-lock-report shape and lock_source reflects explicit setting
    - POST /admin/preview-reprocess works regardless of lock (read-only path)
    - POST /test-connection still works (GET-only) — but skipped when no creds
    - QoyodAPIClient._request raises QoyodWriteLockedError WITHOUT calling httpx
      (in-process mock — pattern reused from the existing pytest suite)
"""
from __future__ import annotations

import os
import asyncio
import pytest
import requests
from unittest.mock import AsyncMock, MagicMock, patch

def _read_backend_url():
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass
    return os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

BASE_URL = _read_backend_url()
assert BASE_URL, "REACT_APP_BACKEND_URL not configured"
LOGIN = {"email": "admin@hesab.app", "password": "admin123"}


# ── Fixtures ─────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def auth_headers():
    r = requests.post(f"{BASE_URL}/api/auth/login", json=LOGIN, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    tok = r.json().get("access_token") or r.json().get("token")
    assert tok, f"no access_token in login response: {r.json()}"
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def original_lock_state(auth_headers):
    """Snapshot the lock value at start; restore at end of module."""
    r = requests.get(
        f"{BASE_URL}/api/integrations/qoyod/settings",
        headers=auth_headers, timeout=15)
    assert r.status_code == 200
    snap = r.json().get("production_writes_locked")
    yield snap
    # Restore (None means field absent — set to False as the safe baseline)
    restore_val = False if snap is None else bool(snap)
    requests.put(
        f"{BASE_URL}/api/integrations/qoyod/settings",
        headers=auth_headers,
        json={"production_writes_locked": restore_val},
        timeout=15)


# ── PUT /settings persistence ────────────────────────────────────────
class TestSettingsToggle:
    def test_put_lock_true_persists(self, auth_headers, original_lock_state):
        r = requests.put(
            f"{BASE_URL}/api/integrations/qoyod/settings",
            headers=auth_headers,
            json={"production_writes_locked": True}, timeout=15)
        assert r.status_code == 200, r.text[:300]
        assert r.json().get("production_writes_locked") is True

        # GET verifies persistence
        g = requests.get(
            f"{BASE_URL}/api/integrations/qoyod/settings",
            headers=auth_headers, timeout=15)
        assert g.status_code == 200
        assert g.json().get("production_writes_locked") is True

    def test_put_lock_false_persists(self, auth_headers, original_lock_state):
        r = requests.put(
            f"{BASE_URL}/api/integrations/qoyod/settings",
            headers=auth_headers,
            json={"production_writes_locked": False}, timeout=15)
        assert r.status_code == 200
        assert r.json().get("production_writes_locked") is False

        g = requests.get(
            f"{BASE_URL}/api/integrations/qoyod/settings",
            headers=auth_headers, timeout=15)
        assert g.json().get("production_writes_locked") is False

    def test_put_lock_toggle_back_true(self, auth_headers, original_lock_state):
        r = requests.put(
            f"{BASE_URL}/api/integrations/qoyod/settings",
            headers=auth_headers,
            json={"production_writes_locked": True}, timeout=15)
        assert r.status_code == 200
        assert r.json().get("production_writes_locked") is True

    def test_put_rejects_unknown_field(self, auth_headers):
        r = requests.put(
            f"{BASE_URL}/api/integrations/qoyod/settings",
            headers=auth_headers,
            json={"nonexistent_extra_xyz": True}, timeout=15)
        assert r.status_code in (400, 422), f"expected extra=forbid: {r.status_code}"


# ── GET /admin/write-lock-report ─────────────────────────────────────
class TestWriteLockReport:
    REQUIRED_TOP = {
        "ok", "production_writes_locked", "production_writes_locked_field",
        "fail_closed_default_enabled", "lock_source", "summary", "items",
    }
    REQUIRED_SUMMARY = {"total_blocked_24h", "by_action_24h", "operator_note"}

    def test_report_when_locked_true(self, auth_headers, original_lock_state):
        # Ensure locked first
        requests.put(
            f"{BASE_URL}/api/integrations/qoyod/settings",
            headers=auth_headers,
            json={"production_writes_locked": True}, timeout=15)

        r = requests.get(
            f"{BASE_URL}/api/integrations/qoyod/admin/write-lock-report",
            headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text[:300]
        data = r.json()

        missing = self.REQUIRED_TOP - set(data.keys())
        assert not missing, f"missing top keys: {missing} got={list(data.keys())}"
        assert data["ok"] is True
        assert data["production_writes_locked"] is True
        assert data["production_writes_locked_field"] is True
        assert data["lock_source"] == "explicit_setting", data["lock_source"]
        assert isinstance(data["fail_closed_default_enabled"], bool)
        assert isinstance(data["items"], list)

        missing_sum = self.REQUIRED_SUMMARY - set(data["summary"].keys())
        assert not missing_sum, f"missing summary keys: {missing_sum}"
        assert isinstance(data["summary"]["total_blocked_24h"], int)
        assert isinstance(data["summary"]["by_action_24h"], dict)

    def test_report_when_explicit_false(self, auth_headers, original_lock_state):
        requests.put(
            f"{BASE_URL}/api/integrations/qoyod/settings",
            headers=auth_headers,
            json={"production_writes_locked": False}, timeout=15)
        r = requests.get(
            f"{BASE_URL}/api/integrations/qoyod/admin/write-lock-report",
            headers=auth_headers, timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert data["production_writes_locked"] is False
        assert data["production_writes_locked_field"] is False
        assert data["lock_source"] == "explicit_setting"


# ── POST /admin/preview-reprocess works regardless of lock ───────────
class TestPreviewReprocessUnaffectedByLock:
    def test_preview_returns_safety_summary_when_locked(self, auth_headers):
        # Lock ON
        requests.put(
            f"{BASE_URL}/api/integrations/qoyod/settings",
            headers=auth_headers,
            json={"production_writes_locked": True}, timeout=15)

        # Pick any order_number from inbox; tolerate "row_not_found"
        r = requests.post(
            f"{BASE_URL}/api/integrations/qoyod/admin/preview-reprocess",
            headers=auth_headers,
            json={"order_number": "nonexistent-preview-probe-zzz"}, timeout=30)
        # Whether the row exists or not, preview must NOT 500. Acceptable
        # outcomes: 200 with ok=false + failed_at_stage, OR 404.
        assert r.status_code in (200, 404), f"unexpected status: {r.status_code} {r.text[:300]}"
        if r.status_code == 200:
            body = r.json()
            assert body.get("qoyod_request_sent") in (False, None), \
                "preview MUST NOT call api.qoyod.com"


# ── POST /test-connection still works regardless of lock state ───────
class TestTestConnectionUnaffected:
    def test_test_connection_when_locked(self, auth_headers):
        requests.put(
            f"{BASE_URL}/api/integrations/qoyod/settings",
            headers=auth_headers,
            json={"production_writes_locked": True}, timeout=15)
        r = requests.post(
            f"{BASE_URL}/api/integrations/qoyod/test-connection",
            headers=auth_headers, timeout=30)
        # Acceptable: 200 with ok=true/false (depending on creds), or 400
        # with `no_credentials`. The lock MUST NOT intercept a GET probe.
        assert r.status_code in (200, 400), \
            f"test-connection blocked unexpectedly: {r.status_code} {r.text[:300]}"
        if r.status_code == 400:
            assert "no_credentials" in r.text


# ── In-process: QoyodAPIClient enforcement w/o calling httpx ─────────
class TestApiClientEnforcement:
    """Constructs QoyodAPIClient(write_lock_enabled=True) and asserts a
    write raises QoyodWriteLockedError BEFORE httpx.AsyncClient.request
    fires. Mirrors the pattern in test_qoyod_global_write_lock_iter293_4.
    """

    def test_write_blocked_no_httpx_call(self):
        from integrations.qoyod.api_client import QoyodAPIClient
        from integrations.qoyod.write_lock import QoyodWriteLockedError

        db = MagicMock()
        db.qoyod_write_lock_attempts.insert_one = AsyncMock(return_value=None)

        client = QoyodAPIClient(
            "dummy-key", db=db, user_id="main", write_lock_enabled=True)

        mock_request = AsyncMock(return_value=MagicMock(status_code=200))

        async def run():
            with patch("httpx.AsyncClient.request", mock_request):
                with pytest.raises(QoyodWriteLockedError):
                    # Call _request directly with a POST — the lock layer
                    # is enforced inside _request before httpx fires.
                    await client._request(
                        "POST", "/invoices",
                        json_body={"invoice": {"reference": "TEST_x"}})
                assert mock_request.await_count == 0, (
                    f"httpx.request was called {mock_request.await_count}x "
                    "— lock leaked!")
        asyncio.run(run())

    def test_read_not_blocked(self):
        from integrations.qoyod.api_client import QoyodAPIClient

        db = MagicMock()
        client = QoyodAPIClient(
            "dummy-key", db=db, user_id="main", write_lock_enabled=True)

        async def run():
            resp = MagicMock()
            resp.status_code = 200
            resp.json = MagicMock(return_value={"products": []})
            resp.text = '{"products": []}'
            resp.headers = {}
            mock_req = AsyncMock(return_value=resp)
            with patch("httpx.AsyncClient.request", mock_req):
                # GET path — must NOT be blocked
                try:
                    await client.list_products()
                except Exception:
                    pass
                assert mock_req.await_count >= 1, \
                    "GET was blocked by write lock — should be allowed!"
        asyncio.run(run())
