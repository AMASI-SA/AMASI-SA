"""iter-258 HTTP smoke tests for the new Webhook Token endpoints.

Exercises the live preview URL with the seeded admin so the test
matches what the operator UI does in the browser.
"""
from __future__ import annotations

import hashlib
import os

import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://salla-analytics.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": "admin@hesab.app", "password": "admin123"})
    assert r.status_code == 200, r.text
    token = r.json().get("access_token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    # Always clear any pre-existing token so tests are deterministic
    s.delete(f"{API}/integrations/qoyod/webhook-token")
    yield s
    s.delete(f"{API}/integrations/qoyod/webhook-token")


def _sha_44(plaintext: str) -> str:
    h = hashlib.sha256(plaintext.encode()).hexdigest()
    return f"{h[:4]}…{h[-4:]}"


def test_status_empty_then_generate_then_status_configured(session):
    # 1) initially clean
    r = session.get(f"{API}/integrations/qoyod/webhook-token")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["configured"] is False
    # meta may be None or a dict with configured:false (after a previous revoke)
    assert (body["meta"] is None) or (body["meta"].get("configured") is False)

    # 2) generate
    r = session.post(f"{API}/integrations/qoyod/webhook-token/generate")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    token = data["token"]
    assert token.startswith("mzn_qoyod_prod_")
    assert len(token) == 79
    assert "warning" in data
    fp = data["meta"]["fingerprint"]
    assert fp == _sha_44(token)

    # 3) GET now reports configured and only fingerprint
    r = session.get(f"{API}/integrations/qoyod/webhook-token")
    body = r.json()
    assert body["ok"] is True
    assert body["configured"] is True
    assert body["meta"]["fingerprint"] == fp
    assert "token" not in body
    # response body should not contain the plaintext at all
    assert token not in r.text


def test_regenerate_rotates_token_and_returns_different_plaintext(session):
    r1 = session.post(f"{API}/integrations/qoyod/webhook-token/generate")
    t1 = r1.json()["token"]
    r2 = session.post(f"{API}/integrations/qoyod/webhook-token/generate")
    t2 = r2.json()["token"]
    assert t1 != t2
    # status reflects only the latest fingerprint
    fp = r2.json()["meta"]["fingerprint"]
    status = session.get(f"{API}/integrations/qoyod/webhook-token").json()
    assert status["meta"]["fingerprint"] == fp


def test_delete_revokes_then_status_unconfigured(session):
    session.post(f"{API}/integrations/qoyod/webhook-token/generate")
    r = session.delete(f"{API}/integrations/qoyod/webhook-token")
    assert r.status_code == 200
    assert r.json()["ok"] in (True, False)  # idempotent
    status = session.get(f"{API}/integrations/qoyod/webhook-token").json()
    assert status["configured"] is False


def test_webhook_accepts_new_db_token_rejects_env_token(session):
    """Once a DB token is configured, the legacy env value MUST NOT pass."""
    gen = session.post(f"{API}/integrations/qoyod/webhook-token/generate")
    new_token = gen.json()["token"]
    # Use the new DB token — payload empty {} → must be 4xx but NOT 401/503
    r = requests.post(
        f"{API}/integrations/qoyod/webhook",
        headers={"X-Webhook-Token": new_token, "Content-Type": "application/json"},
        json={},
    )
    # Token must NOT be rejected. Empty payload may be accepted (200 with
    # ok:false + DEAD_LETTER stage) or rejected with 4xx — either way it
    # is NOT 401 (invalid token) or 503 (token-not-configured).
    assert r.status_code not in (401, 503), f"Expected payload error, got {r.status_code}: {r.text}"
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    # Confirm we passed the token check (we got into the pipeline)
    assert "invalid_webhook_token" not in r.text
    assert "webhook_token_not_configured" not in r.text
    # Legacy env value must now be REJECTED with 401
    r2 = requests.post(
        f"{API}/integrations/qoyod/webhook",
        headers={"X-Webhook-Token": "mzn_qoyod_dev_token_change_me", "Content-Type": "application/json"},
        json={},
    )
    assert r2.status_code == 401, f"Env value must no longer pass once DB token exists, got {r2.status_code}"


def test_unauth_status_blocked():
    r = requests.get(f"{API}/integrations/qoyod/webhook-token")
    assert r.status_code in (401, 403)
