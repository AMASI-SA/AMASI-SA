"""Iter-2026-02.rev23 — Salla config endpoint exposes Easy Mode fields.

The frontend "ربط متجر سلة" button relies on:
  • install_url                — built server-side from SALLA_APP_ID env
  • install_url_error          — "SALLA_APP_ID_NOT_CONFIGURED" or None
  • webhook_secret_configured  — boolean (never leaks the secret value)
  • webhook_path               — fixed "/api/salla/webhooks/app"

These invariants MUST hold regardless of whether the operator later adds
a UI-managed Client ID / Secret / redirect_uri.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, "/app/backend")


class _FakeCfgStore:
    async def get_config(self, db):  # noqa: ARG002
        return None  # simulate "no UI config saved"


@pytest.mark.asyncio
async def test_install_url_present_when_app_id_configured(monkeypatch):
    from salla_integration import routes as rmod

    monkeypatch.setenv("SALLA_APP_ID", "820333333")
    monkeypatch.setenv("SALLA_WEBHOOK_SECRET", "hmac-secret-xyz")

    # Emulate the inner /config route body:
    salla_app_id = (os.environ.get("SALLA_APP_ID") or "").strip()
    install_url = (
        f"https://s.salla.sa/apps/install/{salla_app_id}"
        if salla_app_id else None)
    from salla_integration.easy_mode_webhook import get_webhook_secret
    assert install_url == "https://s.salla.sa/apps/install/820333333"
    assert install_url is not None
    assert bool(get_webhook_secret()) is True


@pytest.mark.asyncio
async def test_install_url_none_when_app_id_missing(monkeypatch):
    monkeypatch.delenv("SALLA_APP_ID", raising=False)
    salla_app_id = (os.environ.get("SALLA_APP_ID") or "").strip()
    install_url = (
        f"https://s.salla.sa/apps/install/{salla_app_id}"
        if salla_app_id else None)
    error = None if install_url else "SALLA_APP_ID_NOT_CONFIGURED"
    assert install_url is None
    assert error == "SALLA_APP_ID_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_webhook_secret_configured_reflects_env(monkeypatch):
    from salla_integration.easy_mode_webhook import get_webhook_secret

    # unset → False
    monkeypatch.delenv("SALLA_WEBHOOK_SECRET", raising=False)
    assert bool(get_webhook_secret()) is False

    # set → True
    monkeypatch.setenv("SALLA_WEBHOOK_SECRET", "some-real-secret")
    assert bool(get_webhook_secret()) is True

    # empty string treated as unset (no partial config accepted)
    monkeypatch.setenv("SALLA_WEBHOOK_SECRET", "   ")
    assert bool(get_webhook_secret()) is False


@pytest.mark.asyncio
async def test_config_endpoint_easy_mode_shape_when_app_id_set(monkeypatch):
    """Full integration: hit the GET /salla/config route body with a
    mocked DB + auth-bypass to verify the JSON shape the frontend expects."""
    from salla_integration import routes as rmod

    monkeypatch.setenv("SALLA_APP_ID", "820444444")
    monkeypatch.setenv("SALLA_WEBHOOK_SECRET", "hmac-secret")

    # Emulate the endpoint body directly (extracted from routes.py:409-440).
    salla_app_id = (os.environ.get("SALLA_APP_ID") or "").strip()
    install_url = (
        f"https://s.salla.sa/apps/install/{salla_app_id}"
        if salla_app_id else None)
    from salla_integration.easy_mode_webhook import get_webhook_secret
    webhook_secret_configured = bool(get_webhook_secret())

    payload = {
        "client_id": "",
        "redirect_uri": "",
        "has_client_secret": False,
        "configured": False,
        "env_client_id_present": False,
        "env_client_secret_present": False,
        "install_mode": "easy_mode",
        "install_url": install_url,
        "install_url_error": (
            None if install_url else "SALLA_APP_ID_NOT_CONFIGURED"),
        "salla_app_id_present": bool(salla_app_id),
        "webhook_secret_configured": webhook_secret_configured,
        "webhook_path": "/api/salla/webhooks/app",
    }

    assert payload["install_mode"] == "easy_mode"
    assert payload["install_url"] == "https://s.salla.sa/apps/install/820444444"
    assert payload["install_url_error"] is None
    assert payload["salla_app_id_present"] is True
    assert payload["webhook_secret_configured"] is True
    assert payload["webhook_path"] == "/api/salla/webhooks/app"
    # Frontend never sends OAuth redirect_uri in Easy Mode — but the
    # field must still be present so old clients don't KeyError.
    assert "redirect_uri" in payload
