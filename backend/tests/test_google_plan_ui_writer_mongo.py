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


@pytest.mark.asyncio
async def test_actual_authenticated_parent_put_then_all_readers_preserves_optional_prior_context(mongo, monkeypatch):
    from tests.test_google_plan_ui_writer_fixtures import app_for
    from tests.test_google_prior_season_context import context
    from tests.test_google_prior_season_observations import prior_order
    native = Native()
    await mongo.users.insert_one({"id": "owner", "role": "owner"})
    await mongo.unified_orders.create_index([("user_id", 1), ("order_date", -1)])
    await mongo.unified_orders.insert_one(prior_order())
    app = app_for(monkeypatch, mongo, native)
    path = "/integrations-v2/google_ads/commercial-plans/winter"
    payload = request_body() | {"pinned_product_ids": ["123"]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": "Bearer synthetic-token"}) as client:
        initial = await client.put(path, json=payload)
        assert initial.status_code == 200, initial.text
        assert initial.json()["fields"]["prior_season_context"] is None
        empty = await client.get(path + "/prior-season-observations?expected_revision=1")
        assert empty.status_code == 200 and empty.json()["status"] == "prior_season_not_configured"
        payload.update(expected_revision=1, prior_season_context=context(calendar_reference_text=None))
        saved = await client.put(path, json=payload)
        assert saved.status_code == 200, saved.text
        fields = saved.json()["fields"]
        assert "acknowledge_review" not in fields and "expected_revision" not in fields
        before = {name: deepcopy(await mongo[name].find({}).to_list(100)) for name in (PLANS, "salla_integrations", "unified_orders")}
        linked = await client.get(path + "/linked-campaign-evidence?expected_revision=2")
        category = await client.get(path + "/live-category-preview?expected_revision=2&category_id=8")
        inventory = await client.post(path + "/native-inventory-preview", json={"expected_revision": 2, "category_id": "8", "selected_product_ids": ["1"]})
        previous = await client.get(path + "/prior-season-observations?expected_revision=2")
        for response in (linked, category, inventory, previous):
            assert response.status_code == 200, response.text
            assert response.json()["plan_revision"] == 2 and response.json()["execution_authorized"] is False
        assert previous.json()["owner_review_provenance"]["reviewed_by"] == "owner"
        assert previous.json()["observations"]["products"][0]["currency_groups"][0]["windows"]["owner_reviewed_prior_season"]["observed_units"] == "2"
        assert before == {name: await mongo[name].find({}).to_list(100) for name in before}
        edited = await client.put(path, json={**fields, "instruction": "تعديل آخر مع حفظ الفترة", "expected_revision": 2, "acknowledge_review": True})
        assert edited.status_code == 200 and edited.json()["fields"]["prior_season_context"] == fields["prior_season_context"]
        removed = await client.put(path, json={**edited.json()["fields"], "prior_season_context": None, "expected_revision": 3, "acknowledge_review": True})
        assert removed.status_code == 200 and removed.json()["fields"]["prior_season_context"] is None
        previous = await client.get(path + "/prior-season-observations?expected_revision=4")
        assert previous.status_code == 200 and previous.json()["observations"] is None
        assert (await client.get(path + "/prior-season-observations?expected_revision=2")).status_code == 409
        row = await mongo[PLANS].find_one({"user_id": "owner", "plan_id": "winter"})
        assert "acknowledge_review" not in row["fields"] and "expected_revision" not in row["fields"]
        invalid = await client.put(path, json={**row["fields"], "prior_season_context": context(date_to=row["fields"]["starts_on"]), "expected_revision": 4, "acknowledge_review": True})
        assert invalid.status_code == 422
        assert await mongo[PLANS].find_one({"user_id": "owner", "plan_id": "winter"}) == row
