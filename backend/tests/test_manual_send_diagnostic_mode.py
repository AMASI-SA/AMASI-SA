"""Diagnostic Mode gate for POST /manual/send/{order_number}.

Contract (user directive 2026-07-09):
    • Default: HTTP 500 with ONLY `{"error_reference": "<hex>"}`. No
      exception details in the body. The full traceback goes to the
      backend logger.
    • With `?diag=1` OR header `X-Debug-Diagnostic: 1` → HTTP 500 with
      diagnostic payload containing exception_type, exception_message,
      last 20 traceback lines, error_reference.

REMOVE this test alongside the diagnostic except-block once the root
cause of the current 500 is found and fixed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from integrations.qoyod_manual.routes import make_qoyod_manual_router


class _FakeDB:
    """Minimal DB stub — never used because we short-circuit at
    manual_send_one via patching."""
    def __getattr__(self, _name):
        raise AttributeError(_name)


def _make_app():
    app = FastAPI()
    async def _fake_user():
        return {"id": "test-user", "email": "diag@test"}
    # The router already sets its own `prefix="/integrations/qoyod/manual"`.
    app.include_router(
        make_qoyod_manual_router(db=_FakeDB(), current_user=_fake_user),
        prefix="/api",
    )
    return app


@pytest.mark.asyncio
async def test_default_500_returns_only_error_reference():
    async def _boom(*_args, **_kwargs):
        raise KeyError("manual_qoyod_invoice_id")
    with patch("integrations.qoyod_manual.routes.manual_send_one",
               new=AsyncMock(side_effect=_boom)):
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport,
                               base_url="http://test") as client:
            r = await client.post(
                "/api/integrations/qoyod/manual/send/271257282")
    assert r.status_code == 500
    body = r.json()
    detail = body.get("detail") or body
    assert set(detail.keys()) == {"error_reference"}
    assert len(detail["error_reference"]) == 8
    # NO exception details in default mode.
    assert "exception_type" not in detail
    assert "traceback_tail" not in detail


@pytest.mark.asyncio
async def test_diag_query_param_returns_traceback_payload():
    async def _boom(*_args, **_kwargs):
        raise AttributeError(
            "'NoneType' object has no attribute 'to_mongo'")
    with patch("integrations.qoyod_manual.routes.manual_send_one",
               new=AsyncMock(side_effect=_boom)):
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport,
                               base_url="http://test") as client:
            r = await client.post(
                "/api/integrations/qoyod/manual/send/271257282?diag=1")
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail["code"] == "unhandled_exception"
    assert detail["exception_type"] == "AttributeError"
    assert "NoneType" in detail["exception_message"]
    assert isinstance(detail["traceback_tail"], list)
    assert 1 <= len(detail["traceback_tail"]) <= 20
    assert detail["diagnostic_mode"] is True
    assert len(detail["error_reference"]) == 8


@pytest.mark.asyncio
async def test_diag_header_returns_traceback_payload():
    async def _boom(*_args, **_kwargs):
        raise TypeError("unexpected keyword argument 'foo'")
    with patch("integrations.qoyod_manual.routes.manual_send_one",
               new=AsyncMock(side_effect=_boom)):
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport,
                               base_url="http://test") as client:
            r = await client.post(
                "/api/integrations/qoyod/manual/send/271257282",
                headers={"X-Debug-Diagnostic": "1"})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail["exception_type"] == "TypeError"
    assert "unexpected keyword" in detail["exception_message"]


@pytest.mark.asyncio
async def test_diag_header_wrong_value_stays_default():
    """Only exact `X-Debug-Diagnostic: 1` unlocks diagnostic mode."""
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")
    with patch("integrations.qoyod_manual.routes.manual_send_one",
               new=AsyncMock(side_effect=_boom)):
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport,
                               base_url="http://test") as client:
            r = await client.post(
                "/api/integrations/qoyod/manual/send/271257282",
                headers={"X-Debug-Diagnostic": "true"})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert set(detail.keys()) == {"error_reference"}


@pytest.mark.asyncio
async def test_manualsendrefused_still_returns_409():
    """The diagnostic catch-all must NOT swallow business refusals."""
    from integrations.qoyod_manual.send import ManualSendRefused
    async def _refuse(*_args, **_kwargs):
        raise ManualSendRefused(
            "already_sent",
            "الطلب أُرسل مسبقاً",
            {"manual_qoyod_invoice_id": "335861"})
    with patch("integrations.qoyod_manual.routes.manual_send_one",
               new=AsyncMock(side_effect=_refuse)):
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport,
                               base_url="http://test") as client:
            r = await client.post(
                "/api/integrations/qoyod/manual/send/271257282?diag=1")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "already_sent"
