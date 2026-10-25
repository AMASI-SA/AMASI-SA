import httpx
import pytest
from tests.test_tiktok_management_routes import app_for


@pytest.mark.asyncio
async def test_owner_spend_policy_route_is_reachable_and_typed():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner", "role": "owner"})), base_url="http://test") as client:
        response = await client.put("/api/integrations-v2/tiktok_ads/spend-guard/policy", json={})
    assert response.status_code == 422
