"""Full owner route through the actual reviewed association/snapshot writers."""
from copy import deepcopy
from datetime import datetime

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from integrations_control_center import google_linked_campaign_routes as module
from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
from integrations_control_center.google_commercial_plan import PLANS
from integrations_control_center.google_linked_campaign_reader import SNAPSHOTS, RUNS
from integrations_control_center.campaign_product_associations import CAMPAIGN_PRODUCT_LINK_COLLECTION as LINKS
from tests.test_google_linked_campaign_mongo import mongo, seed
from tests.test_google_linked_campaign_routes import fields
from tests.test_google_linked_campaign_evidence import NOW


@pytest.mark.asyncio
async def test_actual_management_route_keeps_owner_revision_scope_and_zero_mutations(mongo, monkeypatch):
    await seed(mongo, monkeypatch)
    saved = {**fields(), "excluded_product_ids": ["excluded-product"]}
    await mongo[PLANS].insert_one({"user_id": "owner", "plan_id": "winter", "revision": 3, "fields": saved})
    await mongo.users.insert_many([{"id": "owner", "role": "owner"}, {"id": "staff", "role": "viewer", "created_by": "owner"}])
    before = {name: deepcopy(await mongo[name].find({}).to_list(1000)) for name in (PLANS, LINKS, SNAPSHOTS, RUNS)}
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(module, "datetime", FixedDatetime)
    user = {"id": "owner", "role": "owner"}
    async def current():
        return user
    class NoProvider:
        def __init__(self, *args, **kwargs):
            raise AssertionError("provider must not be constructed")
    app, router = FastAPI(), APIRouter(prefix="/integrations-v2")
    attach_google_ads_management_routes(router, mongo, current, client_factory=NoProvider, ai_client_factory=NoProvider)
    assert sum(route.path.endswith("/linked-campaign-evidence") for route in router.routes) == 1
    app.include_router(router)
    path = "/integrations-v2/google_ads/commercial-plans/winter/linked-campaign-evidence"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path + "?expected_revision=3")
        assert response.status_code == 200, response.text
        result = response.json()
        assert [product["product_id"] for product in result["products"]] == ["product"]
        assert len(result["campaign_contexts"]) == 1 and result["campaign_contexts"][0]["metrics"]["provider_reported_purchases"] == 3
        assert result["product_attribution_verified"] is False and result["execution_authorized"] is False
        assert (await client.get(path + "?expected_revision=2")).status_code == 409
        user.update(id="foreign", role="owner")
        assert (await client.get(path + "?expected_revision=3")).status_code == 404
        user.update(id="staff", role="viewer")
        assert (await client.get(path + "?expected_revision=3")).status_code == 403
    assert before == {name: await mongo[name].find({}).to_list(1000) for name in before}
