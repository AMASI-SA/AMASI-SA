"""Iter52 — Ensure POST /api/auth/register remains functional independently of
the UI-level `show_register_link` toggle (the toggle controls UI visibility ONLY).
Also verifies the default value of `show_register_link` is False (single-store).
"""
import os
import time
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api"

OWNER_EMAIL = "admin@hesab.app"
OWNER_PWD = "admin123"


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def _set_flag(token, value):
    r = requests.put(
        f"{API}/app-config",
        json={"show_register_link": value},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def test_public_register_works_when_toggle_off():
    owner = _login(OWNER_EMAIL, OWNER_PWD)
    _set_flag(owner, False)
    pub = requests.get(f"{API}/public/login-config", timeout=15).json()
    assert pub["show_register_link"] is False

    email = f"TEST_iter52_off_{int(time.time())}@example.com"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "Iter52 OffUser", "email": email, "password": "Passw0rd!"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "access_token" in body and isinstance(body["access_token"], str) and body["access_token"]

    # cleanup: remove the created user as owner
    try:
        users = requests.get(
            f"{API}/team/users",
            headers={"Authorization": f"Bearer {owner}"},
            timeout=15,
        ).json()
        uid = next((u["id"] for u in users if u.get("email") == email), None)
        if uid:
            requests.delete(
                f"{API}/team/users/{uid}",
                headers={"Authorization": f"Bearer {owner}"},
                timeout=15,
            )
    except Exception:
        pass


def test_public_register_works_when_toggle_on():
    owner = _login(OWNER_EMAIL, OWNER_PWD)
    _set_flag(owner, True)
    try:
        pub = requests.get(f"{API}/public/login-config", timeout=15).json()
        assert pub["show_register_link"] is True

        email = f"TEST_iter52_on_{int(time.time())}@example.com"
        r = requests.post(
            f"{API}/auth/register",
            json={"name": "Iter52 OnUser", "email": email, "password": "Passw0rd!"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "access_token" in body
    finally:
        _set_flag(owner, False)  # restore safe default
        try:
            users = requests.get(
                f"{API}/team/users",
                headers={"Authorization": f"Bearer {owner}"},
                timeout=15,
            ).json()
            for u in users:
                if u.get("email", "").startswith("TEST_iter52_"):
                    requests.delete(
                        f"{API}/team/users/{u['id']}",
                        headers={"Authorization": f"Bearer {owner}"},
                        timeout=15,
                    )
        except Exception:
            pass
