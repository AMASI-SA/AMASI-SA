from __future__ import annotations

from copy import deepcopy

import pytest

from integrations_control_center import meta_management_readiness as readiness


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return deepcopy(self._payload)


class FakeClient:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        type(self).calls.append((url, deepcopy(kwargs.get("params") or {})))
        if url.endswith("/act_1/assigned_users"):
            return FakeResponse({"data": [{"id": "meta-user-1", "name": "Owner", "tasks": ["ADVERTISE", "ANALYZE"]}]})
        if url.endswith("/act_1"):
            return FakeResponse({
                "id": "act_1",
                "name": "اماسي",
                "account_status": 1,
                "disable_reason": 0,
                "currency": "SAR",
                "timezone_name": "Asia/Riyadh",
            })
        raise AssertionError(url)


@pytest.mark.asyncio
async def test_readiness_proves_scope_and_account_task_without_provider_write(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr(readiness, "_credential", lambda *args: None)

    async def credential(*args):
        return "opaque-token"

    async def debug(*args):
        return {
            "is_valid": True,
            "user_id": "meta-user-1",
            "scopes": ["ads_read", "ads_management", "business_management"],
        }

    async def selection(*args):
        return {
            "accounts": [{
                "account_id": "act_1",
                "display_name": "اماسي",
                "account_status": 1,
                "selected": True,
            }]
        }

    monkeypatch.setattr(readiness, "_credential", credential)
    monkeypatch.setattr(readiness, "debug_meta_token", debug)
    monkeypatch.setattr(readiness, "get_meta_account_selection", selection)
    monkeypatch.setattr(readiness.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(readiness, "meta_appsecret_proof", lambda token: "proof")
    monkeypatch.setattr(readiness, "meta_graph_base", lambda: "https://graph.facebook.com/v25.0")

    result = await readiness.inspect_meta_management_readiness(object(), "owner-1")

    assert result["write_ready"] is True
    assert all(result["capabilities"].values())
    assert result["accounts"][0]["tasks"] == ["ADVERTISE", "ANALYZE"]
    assert result["provider_write_reached"] is False
    assert result["campaign_write_reached"] is False
    assert all(call[1]["appsecret_proof"] == "proof" for call in FakeClient.calls)
    assert "opaque-token" not in repr(result)


@pytest.mark.asyncio
async def test_readiness_blocks_when_role_tasks_cannot_be_proven(monkeypatch):
    class NoRoleClient(FakeClient):
        async def get(self, url, **kwargs):
            if url.endswith("/assigned_users"):
                return FakeResponse({"error": {"code": 100}}, 400)
            return await super().get(url, **kwargs)

    async def credential(*args):
        return "opaque-token"

    async def debug(*args):
        return {
            "is_valid": True,
            "user_id": "meta-user-1",
            "scopes": ["ads_read", "ads_management", "business_management"],
        }

    async def selection(*args):
        return {"accounts": [{"account_id": "act_1", "account_status": 1, "selected": True}]}

    monkeypatch.setattr(readiness, "_credential", credential)
    monkeypatch.setattr(readiness, "debug_meta_token", debug)
    monkeypatch.setattr(readiness, "get_meta_account_selection", selection)
    monkeypatch.setattr(readiness.httpx, "AsyncClient", NoRoleClient)
    monkeypatch.setattr(readiness, "meta_appsecret_proof", lambda token: "proof")
    monkeypatch.setattr(readiness, "meta_graph_base", lambda: "https://graph.facebook.com/v25.0")

    result = await readiness.inspect_meta_management_readiness(object(), "owner-1")
    assert result["write_ready"] is False
    assert not any(result["capabilities"].values())
    assert result["accounts"][0]["role_verified"] is False
    assert result["accounts"][0]["errors"] == ["100"]
