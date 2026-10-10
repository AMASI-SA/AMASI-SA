"""Public route presence and owner boundaries for native TikTok management."""
import httpx
import pytest
from fastapi import FastAPI, APIRouter, HTTPException

from integrations_control_center.tiktok_native_reporting_routes import attach_tiktok_native_reporting_routes


def app_for(user):
    async def current_user():
        return user

    def owner(value):
        if value.get("role") != "owner":
            raise HTTPException(403, detail={"code": "owner_only"})
        return value

    app, router = FastAPI(), APIRouter(prefix="/api/integrations-v2")
    attach_tiktok_native_reporting_routes(router, object(), current_user, owner)
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_campaign_preview_endpoint_validates_typed_input_before_provider():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner-1", "role": "owner"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok_ads/management/proposals", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_non_owner_cannot_reach_campaign_execution():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "staff-1", "role": "staff"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok_ads/management/proposals/proposal-1/approve-and-execute", json={"confirmation_digest": "0" * 64})
    assert response.status_code == 403
