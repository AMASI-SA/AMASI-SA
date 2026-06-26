"""iter-259 — verify QoyodSettings.enrichment_fallback_enabled is exposed
via the existing settings endpoint (PUT/PATCH /api/integrations/qoyod/settings).

Flow:
  1. Login as admin → cookie.
  2. GET /settings           → baseline value (should default to False).
  3. PUT /settings {enrichment_fallback_enabled: true} → 200.
  4. GET /settings           → echoes True.
  5. PUT /settings {enrichment_fallback_enabled: false} → reset.
  6. GET /settings           → echoes False.
"""
from __future__ import annotations

import os
import pytest
import requests


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fallback to frontend/.env value baked into preview
    BASE_URL = "https://salla-analytics.preview.emergentagent.com"


@pytest.fixture(scope="module")
def admin_session() -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@hesab.app", "password": "admin123"},
        timeout=15,
    )
    if r.status_code != 200:
        pytest.skip(f"admin login failed: {r.status_code} {r.text[:120]}")
    return s


def _get_settings(s: requests.Session) -> dict:
    r = s.get(f"{BASE_URL}/api/integrations/qoyod/settings", timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


def _put_settings(s: requests.Session, body: dict) -> dict:
    # the routes file declares PUT; some clients still try PATCH.
    r = s.put(
        f"{BASE_URL}/api/integrations/qoyod/settings",
        json=body,
        timeout=15,
    )
    assert r.status_code == 200, f"PUT failed {r.status_code}: {r.text}"
    return r.json()


def test_enrichment_fallback_toggle_round_trip(admin_session):
    baseline = _get_settings(admin_session)
    # field exists & is boolean
    # Could be nested in settings/data envelope — handle both.
    def _flag(doc):
        return (
            doc.get("enrichment_fallback_enabled")
            if "enrichment_fallback_enabled" in doc
            else (doc.get("settings") or {}).get("enrichment_fallback_enabled")
        )

    initial = _flag(baseline)
    assert isinstance(initial, bool), f"flag missing or non-bool: {baseline}"

    try:
        # Flip ON
        _put_settings(admin_session, {"enrichment_fallback_enabled": True})
        after_on = _flag(_get_settings(admin_session))
        assert after_on is True

        # Flip OFF
        _put_settings(admin_session, {"enrichment_fallback_enabled": False})
        after_off = _flag(_get_settings(admin_session))
        assert after_off is False
    finally:
        # Always restore baseline
        _put_settings(
            admin_session,
            {"enrichment_fallback_enabled": bool(initial)},
        )
