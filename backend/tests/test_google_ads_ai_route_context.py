"""Typed draft context stays within the selected campaign family."""
from types import SimpleNamespace
import asyncio

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException

from integrations_control_center import google_ads_routes as routes
from integrations_control_center import google_ads_ai


@pytest.mark.asyncio
@pytest.mark.parametrize("required_failure", [False, True])
async def test_search_context_skips_unrelated_permissions_and_drains_failed_required_reads(monkeypatch, required_failure):
    calls, model_contexts = [], []
    client = SimpleNamespace(closed=False)
    pending, started = set(), asyncio.Event()

    async def metadata():
        return {"account_id": "123", "currency_code": "SAR", "time_zone": "Asia/Riyadh"}

    async def assets():
        return [], True

    async def close():
        assert not pending, "a provider read outlived its request/client"
        client.closed = True

    client.account_metadata, client.assets, client.close = metadata, assets, close

    async def principal(*args, **kwargs):
        return {"user_id": "owner", "actor_id": "owner", "owner": True, "can_draft": True}

    async def account_client(db, user_id, account_id, **kwargs):
        assert user_id == "owner" and account_id == "123"
        return client, "epoch"

    async def lookup(provider, kind, **kwargs):
        assert provider is client
        calls.append(kind)
        if kind in {"hotels", "apps", "merchants"}:
            raise HTTPException(status_code=403, detail={"code": "unrelated_account_permission"})
        if required_failure and kind == "languages":
            await asyncio.wait_for(started.wait(), timeout=1)
            raise HTTPException(status_code=403, detail={"code": "required_language_permission"})
        if required_failure:
            pending.add(kind); started.set()
            try:
                await asyncio.sleep(10)
            finally:
                pending.remove(kind)
        return {"items": [], "complete": True}

    async def generate(db, user_id, request, **kwargs):
        assert user_id == "owner" and request.campaign_type == "SEARCH"
        model_contexts.append(kwargs["account_context"])
        return {"provider_writes": 0, "campaign_type": "SEARCH"}

    monkeypatch.setattr(routes, "principal", principal)
    monkeypatch.setattr(routes, "account_client", account_client)
    monkeypatch.setattr(routes, "google_lookup", lookup)
    monkeypatch.setattr(google_ads_ai, "generate_google_campaign_draft", generate)
    app, router = FastAPI(), APIRouter()

    async def current_user():
        return {"id": "owner"}

    routes.attach_google_ads_management_routes(router, object(), current_user)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
        response = await http.post("/google_ads/ai/draft", json={"account_id": "123", "campaign_type": "SEARCH", "prompt": "مسودة اختبار تقنية", "budget_ceiling_native": "1"})
    if required_failure:
        assert response.status_code == 403 and response.json()["detail"]["code"] == "required_language_permission"
        assert model_contexts == [] and client.closed is True and not pending
        return
    assert response.status_code == 200, response.text
    assert set(calls) == {"languages", "audiences", "user_lists", "conversion_actions", "conversion_goals"}
    assert len(model_contexts) == 1 and client.closed is True
    assert model_contexts[0]["merchant_ids"] == model_contexts[0]["apps"] == model_contexts[0]["hotel_center_ids"] == []
