"""Regression tests for iter52 — App-level Login settings.

Covers:
- GET /api/public/login-config is reachable WITHOUT auth and returns the toggle.
- GET /api/app-config requires auth and Owner role (admin/viewer rejected).
- PUT /api/app-config (Owner) flips the public flag end-to-end.
- Default is False (single-store deployment).
"""
import os
import time
import requests

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")
API = f"{BACKEND_URL}/api"

OWNER_EMAIL = "admin@hesab.app"
OWNER_PWD = "admin123"


def _login(email: str, password: str) -> str:
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def _set_flag(token: str, value: bool) -> dict:
    r = requests.put(
        f"{API}/app-config",
        json={"show_register_link": value},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def test_public_login_config_does_not_require_auth():
    r = requests.get(f"{API}/public/login-config", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "show_register_link" in body
    assert isinstance(body["show_register_link"], bool)


def test_owner_can_read_and_toggle_show_register_link():
    token = _login(OWNER_EMAIL, OWNER_PWD)
    try:
        # Reset to OFF baseline first
        _set_flag(token, False)
        time.sleep(0.2)
        pub = requests.get(f"{API}/public/login-config", timeout=15).json()
        assert pub["show_register_link"] is False

        # Toggle ON via owner endpoint
        owner = _set_flag(token, True)
        assert owner["show_register_link"] is True
        time.sleep(0.2)
        pub = requests.get(f"{API}/public/login-config", timeout=15).json()
        assert pub["show_register_link"] is True

        # Toggle OFF again
        owner = _set_flag(token, False)
        assert owner["show_register_link"] is False
        pub = requests.get(f"{API}/public/login-config", timeout=15).json()
        assert pub["show_register_link"] is False
    finally:
        # Always restore the safe default so other suites aren't affected
        _set_flag(token, False)


def test_non_owner_cannot_read_or_update_app_config():
    # Create a viewer user via owner, then ensure that viewer is blocked.
    owner_token = _login(OWNER_EMAIL, OWNER_PWD)
    viewer_email = f"iter52_viewer_{int(time.time())}@example.com"
    viewer_pwd = "Viewer1234!"

    r = requests.post(
        f"{API}/team/users",
        json={
            "name": "Iter52 Viewer",
            "email": viewer_email,
            "password": viewer_pwd,
            "role": "viewer",
        },
        headers={"Authorization": f"Bearer {owner_token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    viewer_id = r.json()["id"]

    try:
        viewer_token = _login(viewer_email, viewer_pwd)

        # Viewer is rejected on read
        r = requests.get(
            f"{API}/app-config",
            headers={"Authorization": f"Bearer {viewer_token}"},
            timeout=15,
        )
        assert r.status_code == 403, r.text

        # Viewer is rejected on write
        r = requests.put(
            f"{API}/app-config",
            json={"show_register_link": True},
            headers={"Authorization": f"Bearer {viewer_token}"},
            timeout=15,
        )
        assert r.status_code == 403, r.text
    finally:
        requests.delete(
            f"{API}/team/users/{viewer_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=15,
        )
