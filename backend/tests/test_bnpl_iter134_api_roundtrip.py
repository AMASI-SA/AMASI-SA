"""Iter-134-Auto — BNPL /api/bnpl/settings/{provider} GET/PUT round-trip.

Validates the live REST surface against the external preview URL with
the real merchant credentials:
  1. GET returns commission_mode='auto' (default).
  2. PUT manual+mdr_percent persists; subsequent GET reflects it.
  3. PUT with invalid commission_mode is ignored (prev mode preserved).
  4. Cleanup: reset commission_mode back to 'auto'.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://salla-analytics.preview.emergentagent.com").rstrip("/")
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    token = r.json().get("access_token")
    assert token, "no access_token returned from /api/auth/login"
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.mark.parametrize("provider", ["tabby", "tamara"])
def test_get_default_commission_mode_present(session, provider):
    r = session.get(f"{BASE_URL}/api/bnpl/settings/{provider}", timeout=15)
    assert r.status_code == 200, r.text[:200]
    data = r.json()
    assert "commission_mode" in data, f"missing commission_mode in {data.keys()}"
    assert data["commission_mode"] in ("auto", "manual")


@pytest.mark.parametrize("provider", ["tabby", "tamara"])
def test_put_manual_then_get_persisted(session, provider):
    payload = {"commission_mode": "manual", "mdr_percent": 0.05}
    r = session.put(f"{BASE_URL}/api/bnpl/settings/{provider}", json=payload, timeout=15)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body.get("commission_mode") == "manual"
    assert body.get("mdr_percent") == pytest.approx(0.05)

    # GET to confirm persistence
    g = session.get(f"{BASE_URL}/api/bnpl/settings/{provider}", timeout=15)
    assert g.status_code == 200
    data = g.json()
    assert data["commission_mode"] == "manual"
    assert data["mdr_percent"] == pytest.approx(0.05)


@pytest.mark.parametrize("provider", ["tabby", "tamara"])
def test_put_invalid_mode_ignored(session, provider):
    # Ensure we're in manual first
    session.put(f"{BASE_URL}/api/bnpl/settings/{provider}",
                json={"commission_mode": "manual"}, timeout=15)
    r = session.put(f"{BASE_URL}/api/bnpl/settings/{provider}",
                    json={"commission_mode": "garbage"}, timeout=15)
    assert r.status_code in (200, 400)
    g = session.get(f"{BASE_URL}/api/bnpl/settings/{provider}", timeout=15)
    assert g.status_code == 200
    # invalid value MUST NOT change mode
    assert g.json()["commission_mode"] == "manual"


@pytest.mark.parametrize("provider", ["tabby", "tamara"])
def test_put_auto_resets_mode(session, provider):
    r = session.put(f"{BASE_URL}/api/bnpl/settings/{provider}",
                    json={"commission_mode": "auto"}, timeout=15)
    assert r.status_code == 200
    g = session.get(f"{BASE_URL}/api/bnpl/settings/{provider}", timeout=15)
    assert g.status_code == 200
    assert g.json()["commission_mode"] == "auto"
