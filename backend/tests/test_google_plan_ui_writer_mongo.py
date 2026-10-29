"""The actual parent route must read the real writer shape on disposable Mongo."""
from copy import deepcopy
import httpx
import pytest
from fastapi import APIRouter, FastAPI
from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
from integrations_control_center.google_live_category_routes import attach_google_live_category_routes
from integrations_control_center.google_native_inventory_routes import attach_google_native_inventory_routes
from integrations_control_center.google_commercial_plan import PLANS
from tests.test_google_saved_plan_fields_contract_mongo import mongo
from tests.test_google_saved_plan_fields_contract import request_body
from tests.test_google_native_inventory_reader import Native


@pytest.mark.asyncio
async def test_actual_parent_mongo_put_then_category_and_native_read_preserves_saved_fields_and_scope(mongo, monkeypatch):
    native = Native()
    monkeypatch.setitem(attach_google_live_category_routes.__kwdefaults__, "caller", native)
    monkeypatch.setitem(attach_google_native_inventory_routes.__kwdefaults__, "caller", native)
    await mongo.users.insert_many([{"id": "owner", "role": "owner"}, {"id": "staff", "role": "viewer", "created_by": "owner"}])
    user = {"id": "owner", "role": "owner"}
    async def current():
        return user
    class NoAdsProvider:
        def __init__(self, *args, **kwargs):
            raise AssertionError("ads provider must not be constructed")
    app, router = FastAPI(), APIRouter(prefix="/integrations-v2")
    attach_google_ads_management_routes(router, mongo, current, client_factory=NoAdsProvider, ai_client_factory=NoAdsProvider)
    assert sum(route.path.endswith("/native-inventory-preview") for route in router.routes) == 1
    app.include_router(router)
    path = "/integrations-v2/google_ads/commercial-plans/winter"
    payload = {"expected_revision": 1, "category_id": "8", "selected_product_ids": ["1"]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        saved = await client.put(path, json=request_body())
        assert saved.status_code == 200, saved.text
        assert "acknowledge_review" not in saved.json()["fields"] and "expected_revision" not in saved.json()["fields"]
        assert native.calls == []
        before = {name: deepcopy(await mongo[name].find({}).to_list(100)) for name in (PLANS, "salla_integrations")}
        category = await client.get(path + "/live-category-preview?expected_revision=1&category_id=8")
        assert category.status_code == 200, category.text
        assert category.json()["products"][0]["salla_product_id"] == "1"
        result = await client.post(path + "/native-inventory-preview", json=payload)
        assert result.status_code == 200, result.text
        assert result.json()["products"][0]["parent_stock"]["state"] == "zero_unlimited_unknown"
        assert result.json()["products"][0]["variants"][0]["stock"]["state"] == "positive"
        assert result.json()["globally_ready"] is False and result.json()["execution_authorized"] is False
        native.calls.clear()
        assert (await client.post(path + "/native-inventory-preview", json=payload | {"expected_revision": 2})).status_code == 409
        user.update(id="foreign", role="owner")
        assert (await client.post(path + "/native-inventory-preview", json=payload)).status_code == 404
        user.update(id="staff", role="viewer")
        assert (await client.post(path + "/native-inventory-preview", json=payload)).status_code == 403
        assert native.calls == []
    assert before == {name: await mongo[name].find({}).to_list(100) for name in before}
