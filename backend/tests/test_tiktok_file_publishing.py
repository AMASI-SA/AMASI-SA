"""The file path sends metadata to Mezan and media only to TikTok."""
import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException

from integrations_control_center.tiktok_native_reporting_routes import attach_tiktok_native_reporting_routes


def app_for(user, db=None):
    async def current_user():
        return user

    def require_owner(value):
        if value.get("role") != "owner":
            raise HTTPException(403)
        return value

    app, router = FastAPI(), APIRouter(prefix="/api/integrations-v2")
    attach_tiktok_native_reporting_routes(router, db if db is not None else object(), current_user, require_owner)
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_file_preview_is_a_typed_public_route():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner", "role": "owner"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok/content/files/proposals", json={})
    assert response.status_code == 422
