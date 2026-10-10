"""Owner-scoped native AI public route contract; no real OpenAI calls."""
import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from integrations_control_center import tiktok_native_reporting_routes as routes


def app_for(monkeypatch, *, role="owner"):
    calls = []
    async def user():
        return {"id": "owner", "role": role}
    def owner(value):
        if value["role"] != "owner":
            raise HTTPException(status_code=403, detail="owner_only")
        return value
    async def analyze(db, user_id, payload):
        calls.append((user_id, payload.account_id, payload.campaign_id))
        return {"source_mode": "tiktok_native_campaign_ai_observe_v1",
                "policy": {"mutations_allowed": False}, "context": {"salla_evidence": {"sales_sar": None}}}
    monkeypatch.setattr(routes, "install_tiktok_reporting_actions", lambda: None)
    monkeypatch.setattr(routes, "analyze_tiktok_campaign", analyze, raising=False)
    router = APIRouter(prefix="/api/integrations-v2")
    routes.attach_tiktok_native_reporting_routes(router, object(), user, owner)
    app = FastAPI(); app.include_router(router)
    return app, calls


PAYLOAD = {"account_id": "70001", "campaign_id": "campaign-1",
           "from_date": "2026-10-03", "to_date": "2026-10-09"}


@pytest.mark.asyncio
async def test_ai_route_uses_authenticated_owner_and_ignores_body_tenant(monkeypatch):
    app, calls = app_for(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok_ads/analyze-campaign",
                                     json={**PAYLOAD, "user_id": "other"})
    assert response.status_code == 200
    assert calls == [("owner", "70001", "campaign-1")]
    assert response.json()["policy"]["mutations_allowed"] is False
    assert response.json()["context"]["salla_evidence"]["sales_sar"] is None


@pytest.mark.asyncio
async def test_ai_route_denies_employee_before_any_analysis(monkeypatch):
    app, calls = app_for(monkeypatch, role="employee")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok_ads/analyze-campaign", json=PAYLOAD)
    assert response.status_code == 403
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("campaign_id", ["x" * 121, {"$ne": None}, "../other"])
async def test_ai_route_rejects_invalid_campaign_identifiers(monkeypatch, campaign_id):
    app, calls = app_for(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok_ads/analyze-campaign",
                                     json={**PAYLOAD, "campaign_id": campaign_id})
    assert response.status_code == 422
    assert calls == []
