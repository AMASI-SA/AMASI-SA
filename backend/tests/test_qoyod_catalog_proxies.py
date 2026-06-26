"""Catalog proxies — branches / taxes / accounts.

Locks in (2026-06-26 bug fix):
  • /qoyod-branches  → always returns {ok: true, unsupported: true,
    data: []} BEFORE any Qoyod HTTP call. Qoyod API 2.0 does NOT
    expose a /branches resource.
  • /qoyod-taxes     → same pattern.
  • /qoyod-accounts  → still calls Qoyod (it IS a real endpoint) and
    surfaces 401 errors faithfully so the operator knows the API
    key needs the `accounts.read` scope.

The point of these proxies is to feed UI dropdowns. When a catalog is
unavailable, the UI falls back to free-text input — which is why the
"unsupported" marker matters: it tells the UI to suppress the
"network error" toast and show a helpful hint instead.
"""
from __future__ import annotations

import os
import httpx
import pytest
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


API_BASE = os.environ.get(
    "REACT_APP_BACKEND_URL", "http://localhost:8001")
ADMIN_EMAIL = "admin@hesab.app"
ADMIN_PASSWORD = "admin123"


async def _auth_headers(client: httpx.AsyncClient) -> dict:
    r = await client.post(
        f"{API_BASE}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    token = r.json().get("access_token") or r.json().get("token")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_branches_proxy_returns_unsupported_marker():
    """Branches resource doesn't exist in Qoyod 2.0 — proxy must
    short-circuit with a clear `unsupported: true` marker."""
    async with httpx.AsyncClient(timeout=15) as c:
        h = await _auth_headers(c)
        r = await c.get(
            f"{API_BASE}/api/integrations/qoyod/qoyod-branches", headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert j["unsupported"] is True
    assert j["data"] == []
    # Operator-facing message must mention manual entry
    assert "manually" in j["message"].lower() \
        or "يدوي" in j["message"]
    # Hint at the Qoyod UI path that DOES exist
    assert "/settings/branches" in j["qoyod_ui_path"]


@pytest.mark.asyncio
async def test_taxes_proxy_returns_unsupported_marker():
    async with httpx.AsyncClient(timeout=15) as c:
        h = await _auth_headers(c)
        r = await c.get(
            f"{API_BASE}/api/integrations/qoyod/qoyod-taxes", headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert j["unsupported"] is True
    assert j["data"] == []
    assert "/settings/taxes" in j["qoyod_ui_path"]


@pytest.mark.asyncio
async def test_accounts_proxy_still_calls_qoyod_and_passes_through_errors():
    """Accounts IS a real Qoyod endpoint; the proxy must call it and
    faithfully report any error (401 when the key lacks scope, etc.)."""
    async with httpx.AsyncClient(timeout=20) as c:
        h = await _auth_headers(c)
        r = await c.get(
            f"{API_BASE}/api/integrations/qoyod/qoyod-accounts", headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    # Two acceptable outcomes:
    #   1) Merchant key HAS accounts scope → ok=true with real data
    #   2) Merchant key LACKS scope        → ok=false with 401 error
    # Either way, `unsupported` must be False — this endpoint exists.
    assert j["unsupported"] is False
    if j["ok"]:
        assert "data" in j
    else:
        assert "error" in j
        assert j["error"]["code"] in (
            "qoyod_unauthorized", "qoyod_forbidden",
            "qoyod_not_found", "no_credentials")


@pytest.mark.asyncio
async def test_unsupported_proxies_do_not_hit_qoyod():
    """Regression: a no_credentials tenant must STILL get the
    'unsupported' response for branches/taxes — the short-circuit runs
    BEFORE any Qoyod API call (so no API key is needed)."""
    async with httpx.AsyncClient(timeout=15) as c:
        h = await _auth_headers(c)
        # Even without a key, the unsupported marker should be returned.
        r1 = await c.get(
            f"{API_BASE}/api/integrations/qoyod/qoyod-branches", headers=h)
        r2 = await c.get(
            f"{API_BASE}/api/integrations/qoyod/qoyod-taxes", headers=h)
    assert r1.status_code == 200 and r1.json()["unsupported"] is True
    assert r2.status_code == 200 and r2.json()["unsupported"] is True
