"""Phase 5 — Dashboard card customization tests.

Verify that:
- Settings exposes `dashboard_hidden_cards` (defaults to []).
- PUT /api/settings persists the new field.
- Re-reading settings returns the persisted list.
"""
import os
import uuid

import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"u{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(f"{API}/auth/register",
                      json={"name": "T", "email": email, "password": "test12345"})
    r.raise_for_status()
    return r.json()["access_token"]


def test_settings_exposes_dashboard_hidden_cards_default():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/settings", headers=h)
    assert r.status_code == 200
    assert r.json().get("dashboard_hidden_cards") == []


def test_settings_persists_dashboard_hidden_cards():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    s = requests.get(f"{API}/settings", headers=h).json()
    payload = {
        "payment_methods": s["payment_methods"],
        "shipping_companies": s["shipping_companies"],
        "dashboard_hidden_cards": ["tamara_fees", "tabby_fees", "emkan_fees", " ", ""],
    }
    r = requests.put(f"{API}/settings", json=payload, headers=h)
    assert r.status_code == 200, r.text
    s2 = requests.get(f"{API}/settings", headers=h).json()
    # Empty/whitespace entries should be stripped
    assert s2["dashboard_hidden_cards"] == ["tamara_fees", "tabby_fees", "emkan_fees"]


def test_settings_dashboard_hidden_cards_can_be_cleared():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    s = requests.get(f"{API}/settings", headers=h).json()
    # Set some hidden
    requests.put(f"{API}/settings", json={
        "payment_methods": s["payment_methods"],
        "shipping_companies": s["shipping_companies"],
        "dashboard_hidden_cards": ["a", "b"],
    }, headers=h)
    # Clear them
    requests.put(f"{API}/settings", json={
        "payment_methods": s["payment_methods"],
        "shipping_companies": s["shipping_companies"],
        "dashboard_hidden_cards": [],
    }, headers=h)
    assert requests.get(f"{API}/settings", headers=h).json()["dashboard_hidden_cards"] == []
