"""Real management-router attachment to the reviewed read-only evidence seam."""
from copy import deepcopy
from datetime import datetime

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
from integrations_control_center.google_commercial_plan import PLANS
from integrations_control_center import google_linked_campaign_routes as module
from tests.test_google_linked_campaign_reader import Collection, database, event, NOW
from tests.test_google_linked_campaign_routes import fields


def integrated(monkeypatch, user=None):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(module, "datetime", FixedDatetime)
    db = database([event("123"), event("456")])
    saved = {**fields(), "pinned_product_ids": ["123"], "excluded_product_ids": ["456"]}
    db[PLANS] = Collection([{"user_id": "owner", "plan_id": "winter", "revision": 3, "fields": saved}])
    async def current():
        return {"id": "owner", "role": "owner"} if user is None else user
    class NoProvider:
        def __init__(self, *args, **kwargs):
            raise AssertionError("provider must not be constructed")
    app, router = FastAPI(), APIRouter(prefix="/integrations-v2")
    attach_google_ads_management_routes(router, db, current, client_factory=NoProvider, ai_client_factory=NoProvider)
    assert sum(route.path.endswith("/linked-campaign-evidence") for route in router.routes) == 1
    assert any(route.path.endswith("/store-observations") for route in router.routes)
    assert any(route.path.endswith("/category-review") for route in router.routes)
    app.include_router(router)
    return app, db


async def get(app, query="expected_revision=3"):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/integrations-v2/google_ads/commercial-plans/winter/linked-campaign-evidence?" + query)


@pytest.mark.asyncio
async def test_actual_management_route_uses_saved_scope_once_without_source_or_provider_writes(monkeypatch):
    app, db = integrated(monkeypatch)
    before = {name: deepcopy(collection.rows) for name, collection in db.items()}
    response = await get(app, "expected_revision=3&owner_id=foreign&as_of=2000-01-01")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["as_of"] == NOW.isoformat()
    assert [product["product_id"] for product in result["products"]] == ["123"]
    assert len(result["campaign_contexts"]) == 1
    assert result["campaign_contexts"][0]["association_proofs"] == [{"product_id": "123", "event_id": "event-123"}]
    assert result["execution_authorized"] is False and result["independent_store_orders"] is None
    assert before == {name: collection.rows for name, collection in db.items()}


@pytest.mark.asyncio
@pytest.mark.parametrize("user,status", [({}, 401), ({"id": "foreign", "role": "owner"}, 404), ({"id": "owner", "role": "owner", "disabled": True}, 403)])
async def test_actual_route_denies_unbound_actor(monkeypatch, user, status):
    app, _ = integrated(monkeypatch, user)
    assert (await get(app)).status_code == status


@pytest.mark.asyncio
async def test_actual_route_rejects_revision_and_full_fields_races(monkeypatch):
    app, db = integrated(monkeypatch)
    assert (await get(app, "expected_revision=2")).status_code == 409
    plans = db[PLANS]
    original = plans.aggregate
    calls = 0
    def aggregate(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            plans.rows[0]["fields"]["instruction"] = "توجيه جديد"
        return original(*args, **kwargs)
    plans.aggregate = aggregate
    assert (await get(app)).status_code == 409
