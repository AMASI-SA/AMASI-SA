"""Native owner-approved text replies; no live provider/model calls."""
import httpx
import pytest

from tests.test_tiktok_content_routes import app_for

ROOT = "/api/integrations-v2/tiktok/content/comment-replies"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,body", [
    ("/preview", {"conversation_id": "thread", "suggestion_id": "draft", "version": 1, "text": "كودك AM"}),
    ("/" + "a" * 32 + "/execute", {"confirmation_digest": "b" * 64}),
    ("/" + "a" * 32 + "/status", {}),
])
async def test_reply_routes_reject_staff_before_any_database_or_provider(path, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "staff", "role": "staff"})), base_url="http://test") as client:
        response = await client.post(ROOT + path, json=body)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reply_preview_has_typed_bounded_public_seam():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner", "role": "owner"})), base_url="http://test") as client:
        response = await client.post(ROOT + "/preview", json={})
        oversized = await client.post(ROOT + "/preview", content=b'{"text":"' + b'x' * 32768 + b'"}', headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert oversized.status_code == 413
