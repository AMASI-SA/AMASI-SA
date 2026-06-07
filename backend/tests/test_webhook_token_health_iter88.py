"""Iter-88 — Webhook token health endpoints."""
import os
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0],
)


def test_validate_token_returns_valid_for_known_token():
    """Known token: /webhook/validate-token returns 200 with valid=True
    + diagnostic metadata."""
    r = requests.get(
        f"{BASE_URL}/api/webhook/validate-token/5c172bcaf12e4d71ae5324e7b90fc2f0",
        timeout=10,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["valid"] is True
    assert "environment" in d
    assert "webhook_url" in d
    assert d["webhook_url"].endswith("/api/webhook/make/5c172bcaf12e4d71ae5324e7b90fc2f0")


def test_validate_token_returns_invalid_for_unknown():
    """Unknown token: 200 with valid=False + reason — never 401, so the
    frontend can render a friendly diagnostic instead of parsing
    error responses."""
    r = requests.get(
        f"{BASE_URL}/api/webhook/validate-token/000000aaa-fake-bad",
        timeout=10,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["valid"] is False
    assert d["reason"] == "token_not_found_in_this_environment"


def test_ping_known_token_returns_200_ok():
    r = requests.post(
        f"{BASE_URL}/api/webhook/ping/5c172bcaf12e4d71ae5324e7b90fc2f0",
        timeout=10,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["valid"] is True
    assert "received_at" in d


def test_ping_unknown_token_returns_401_with_arabic_hint():
    """Make.com's error 'The service rejected the webhook token' maps
    to a 401 with a structured detail that the UI can surface."""
    r = requests.post(
        f"{BASE_URL}/api/webhook/ping/this-token-does-not-exist",
        timeout=10,
    )
    assert r.status_code == 401
    d = r.json()["detail"]
    assert d["ok"] is False
    assert d["reason"] == "token_not_found"
    # Arabic merchant-friendly hint must be present
    assert "هذا الرمز" in d["hint"]
    assert "Make" in d["hint"]


def test_main_make_webhook_unauthorized_for_unknown_token():
    """The actual ingestion endpoint must still 401 unknown tokens."""
    r = requests.post(
        f"{BASE_URL}/api/webhook/make/this-token-does-not-exist",
        json={"order_number": "TEST"},
        timeout=10,
    )
    assert r.status_code == 401
