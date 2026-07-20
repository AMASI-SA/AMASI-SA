"""Regression tests for Salla OAuth refresh resilience."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from salla_integration import service as svc


@respx.mock
def test_explicit_scope_401_does_not_refresh_or_disconnect(monkeypatch):
    ensure = AsyncMock(return_value="valid-access")
    mark = AsyncMock()
    monkeypatch.setattr(svc, "ensure_fresh_access_token", ensure)
    monkeypatch.setattr(svc, "mark_needs_reauth", mark)
    respx.get(f"{svc.SALLA_API_BASE}/products").mock(
        return_value=httpx.Response(
            401,
            json={
                "error": {
                    "code": "Unauthorized",
                    "message": "The access token should have access to one of those scopes: products.read",
                }
            },
        )
    )

    async def run():
        with pytest.raises(svc.SallaError) as caught:
            await svc.call_salla(object(), "user-1", "GET", "/products")
        assert caught.value.status_code == 403
        assert caught.value.needs_reauth is False

    asyncio.run(run())
    assert ensure.await_count == 1
    mark.assert_not_awaited()


@respx.mock
def test_ambiguous_endpoint_401_with_valid_token_stays_connected(monkeypatch):
    ensure = AsyncMock(return_value="valid-access")
    mark = AsyncMock()
    monkeypatch.setattr(svc, "ensure_fresh_access_token", ensure)
    monkeypatch.setattr(svc, "mark_needs_reauth", mark)
    respx.get(f"{svc.SALLA_API_BASE}/products").mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"})
    )
    respx.get(f"{svc.SALLA_API_BASE}/store/info").mock(
        return_value=httpx.Response(200, json={"data": {"id": 123}})
    )

    async def run():
        with pytest.raises(svc.SallaError) as caught:
            await svc.call_salla(object(), "user-1", "GET", "/products")
        assert caught.value.status_code == 403
        assert caught.value.needs_reauth is False

    asyncio.run(run())
    assert ensure.await_count == 1
    mark.assert_not_awaited()


def test_public_status_reports_automatic_refresh_readiness():
    now = datetime.now(timezone.utc)
    public = svc.integration_to_public(
        {
            "status": "connected",
            "scope": "settings.read orders.read_write offline_access",
            "refresh_token_encrypted": b"encrypted",
            "expires_at": now + timedelta(days=14),
        }
    )
    assert public["automatic_refresh_ready"] is True
    assert (
        public["automatic_refresh_before_seconds"]
        == svc.PROACTIVE_REFRESH_BEFORE_SEC
    )
