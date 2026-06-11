"""Iter-141 — Cross-device sidebar visibility test.

Before this iteration the merchant's hidden-pages list was stored ONLY
in localStorage, so hiding a page on phone A didn't propagate to
phone B / desktop / tablet.  The fix lifts it to
`users.settings.sidebar_hidden_pages` (same pattern as
`dashboard_hidden_cards`).

These tests hit the live API on preview to confirm:
  1. GET /settings returns sidebar_hidden_pages (default []).
  2. PUT /settings persists the list.
  3. The next GET round-trips identically.
  4. Invalid / non-list payload is sanitized.
"""
import os
import requests

BASE = os.environ.get(
    "TEST_API_BASE",
    "https://salla-analytics.preview.emergentagent.com",
)


def _login():
    r = requests.post(
        f"{BASE}/api/auth/login",
        json={"email": "amasi.jewelery@gmail.com", "password": "10201917"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _get(tok):
    r = requests.get(f"{BASE}/api/settings",
                     headers={"Authorization": f"Bearer {tok}"},
                     timeout=15)
    r.raise_for_status()
    return r.json()


def _put(tok, body):
    r = requests.put(f"{BASE}/api/settings",
                     headers={"Authorization": f"Bearer {tok}"},
                     json=body, timeout=15)
    r.raise_for_status()
    return r.json()


def test_settings_returns_sidebar_hidden_pages_field():
    tok = _login()
    s = _get(tok)
    assert "sidebar_hidden_pages" in s
    assert isinstance(s["sidebar_hidden_pages"], list)


def test_save_and_roundtrip():
    tok = _login()
    base = _get(tok)
    body = dict(base)
    body["sidebar_hidden_pages"] = ["nav-tabby", "nav-tamara", "nav-imports"]
    _put(tok, body)
    after = _get(tok)
    try:
        assert after["sidebar_hidden_pages"] == [
            "nav-tabby", "nav-tamara", "nav-imports",
        ]
    finally:
        # Always reset so this test doesn't leak state.
        body["sidebar_hidden_pages"] = base.get("sidebar_hidden_pages") or []
        _put(tok, body)


def test_blank_strings_are_dropped():
    tok = _login()
    base = _get(tok)
    body = dict(base)
    body["sidebar_hidden_pages"] = ["nav-tabby", "", "   ", "nav-imports"]
    _put(tok, body)
    after = _get(tok)
    try:
        assert after["sidebar_hidden_pages"] == ["nav-tabby", "nav-imports"]
    finally:
        body["sidebar_hidden_pages"] = base.get("sidebar_hidden_pages") or []
        _put(tok, body)


def test_empty_list_persists():
    tok = _login()
    base = _get(tok)
    body = dict(base)
    body["sidebar_hidden_pages"] = []
    _put(tok, body)
    after = _get(tok)
    assert after["sidebar_hidden_pages"] == []
