"""Actual parent router and disposable Mongo; native Salla responses are fixtures."""
from copy import deepcopy
import httpx
import pytest
from fastapi import APIRouter, FastAPI
from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
from integrations_control_center.google_live_category_routes import attach_google_live_category_routes
from integrations_control_center.google_commercial_plan import PLANS
from tests.test_google_live_category_reader import Native
from tests.test_google_live_category_mongo import mongo


@pytest.mark.asyncio
async def test_actual_management_route_owned_native_preview_and_zero_plan_source_mutations(mongo, monkeypatch):
    await mongo.users.insert_many([{"id": "owner", "role": "owner"}, {"id": "staff", "role": "viewer", "created_by": "owner", "extra_permissions": ["ads.view"]}])
    before = {name: deepcopy(await mongo[name].find({}).to_list(1000)) for name in (PLANS, "salla_integrations")}
    native, user = Native(), {"id": "owner", "role": "owner"}
    monkeypatch.setitem(attach_google_live_category_routes.__kwdefaults__, "caller", native)
    async def current():
        return user
    class NoProvider:
        def __init__(self, *args, **kwargs):
            raise AssertionError("ads provider must not be constructed")
    app, router = FastAPI(), APIRouter(prefix="/integrations-v2")
    attach_google_ads_management_routes(router, mongo, current, client_factory=NoProvider, ai_client_factory=NoProvider)
    assert sum(route.path.endswith("/live-category-preview") for route in router.routes) == 1
    app.include_router(router)
    assert native.calls == []
    path = "/integrations-v2/google_ads/commercial-plans/winter/live-category-preview"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path + "?expected_revision=1&category_id=8&owner_id=foreign")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["category_id"] == "8" and data["store_id"] == "9"
        assert data["products"][0]["excluded"] is True and data["inventory_verified"] is False
        assert data["pagination_traversal_complete"] is True and data["remote_snapshot_atomic"] is False
        assert all(p["eligible_for_ad_selection"] is False for p in data["products"])
        assert "private_metadata_sentinel" not in response.text and "token_revision" not in response.text
        native.calls.clear()
        assert (await client.get(path + "?expected_revision=2&category_id=8")).status_code == 409
        user.update(id="foreign", role="owner")
        assert (await client.get(path + "?expected_revision=1&category_id=8")).status_code == 404
        user.update(id="staff", role="viewer")
        assert (await client.get(path + "?expected_revision=1&category_id=8")).status_code == 403
        assert native.calls == []
    assert before == {name: await mongo[name].find({}).to_list(1000) for name in before}
