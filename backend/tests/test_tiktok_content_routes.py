"""Public content seams are bounded, owner-only and separate from ads OAuth."""
import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException

from integrations_control_center.tiktok_connections import attach_tiktok_connection_routes
from integrations_control_center.tiktok_native_reporting_routes import attach_tiktok_native_reporting_routes


def app_for(user, db=None):
    async def current_user():
        return user

    def require_owner(value):
        if value.get("role") != "owner":
            raise HTTPException(403, detail={"code": "owner_only"})
        return value

    app, router = FastAPI(), APIRouter(prefix="/api/integrations-v2")
    attach_tiktok_connection_routes(router, db or object(), current_user, require_owner)
    attach_tiktok_native_reporting_routes(router, db or object(), current_user, require_owner)
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_content_preview_is_a_real_typed_public_route():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner-1", "role": "owner"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok/content/proposals", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", [
    ("POST", "/creators/connect/start", None),
    ("GET", "/creators", None),
    ("POST", "/proposals/proposal-1/approve-and-publish", {"confirmation_digest": "a" * 64}),
])
async def test_non_owner_cannot_reach_content_or_creator_authorization(method, path, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "staff-1", "role": "staff"})), base_url="http://test") as client:
        response = await client.request(method, "/api/integrations-v2/tiktok/content" + path, json=body)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_content_request_is_rejected_before_parsing_large_media_sized_json():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner-1", "role": "owner"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok/content/proposals", content=b'{"caption":"' + b'a' * 32768 + b'"}', headers={"Content-Type": "application/json"})
    assert response.status_code == 413
