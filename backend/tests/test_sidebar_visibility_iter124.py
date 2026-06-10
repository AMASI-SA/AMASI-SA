"""Iter-124 — Sidebar visibility toggle.

This is a frontend-only feature (localStorage), so backend pytest just
sanity-checks that the underlying user profile endpoints aren't
affected by any DB schema we might have added.  No new endpoints.
"""
import os
import pytest
import requests
from dotenv import load_dotenv

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or "https://salla-analytics.preview.emergentagent.com").rstrip("/")
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


def test_auth_me_still_works(session):
    """Smoke: visibility settings are client-side; user profile
    endpoint should remain stable."""
    r = session.get(f"{BASE_URL}/api/auth/me", timeout=30)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert "email" in body, body


def test_no_new_backend_endpoint_for_visibility(session):
    """Visibility lives in localStorage only — there should be no
    PUT /api/user/sidebar-visibility endpoint at this point."""
    r = session.put(f"{BASE_URL}/api/user/sidebar-visibility",
                    json={"hidden": []}, timeout=10)
    # 404 or 405 expected — anything other than 200 confirms
    # the feature is intentionally client-side only.
    assert r.status_code != 200, (
        "Unexpected: a backend endpoint exists. If you've moved "
        "visibility to the server, update this test."
    )
