"""Actual management-router binding with fixture-only native GETs."""
from copy import deepcopy
import httpx
import pytest
from fastapi import APIRouter, FastAPI
from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
from integrations_control_center.google_live_category_routes import attach_google_live_category_routes
from tests.test_google_live_category_reader import setup
from tests.test_google_live_category_routes import DB


def integrated(monkeypatch, user=None):
    source, caller, _ = setup()
    db = DB(source.salla_integrations)
    monkeypatch.setitem(attach_google_live_category_routes.__kwdefaults__, "caller", caller)
    async def current():
        return {"id": "owner", "role": "owner"} if user is None else user
    class NoProvider:
        def __init__(self, *args, **kwargs):
            raise AssertionError("ads provider must not be constructed")
    app, router = FastAPI(), APIRouter(prefix="/integrations-v2")
    attach_google_ads_management_routes(router, db, current, client_factory=NoProvider, ai_client_factory=NoProvider)
    assert sum(route.path.endswith("/live-category-preview") for route in router.routes) == 1
    assert all(any(route.path.endswith(suffix) for route in router.routes) for suffix in ("/linked-campaign-evidence", "/store-observations", "/category-review", "/catalog-review"))
    app.include_router(router)
    return app, db, caller


async def get(app, query="expected_revision=1&category_id=8"):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/integrations-v2/google_ads/commercial-plans/winter/live-category-preview?" + query)


@pytest.mark.asyncio
async def test_actual_management_route_uses_saved_owner_category_without_mutations(monkeypatch):
    app, db, caller = integrated(monkeypatch)
    before = deepcopy((db.plan.row, db.salla_integrations.row))
    assert caller.calls == []
    response = await get(app, "expected_revision=1&category_id=8&owner_id=foreign&category_url=https://evil.test")
    assert response.status_code == 200, response.text
    assert response.json()["products"][0]["excluded"] is True
    assert response.json()["inventory_verified"] is False
    assert before == (db.plan.row, db.salla_integrations.row)
    assert [path for path, _ in caller.calls] == ["/store/info", "/categories/8", "/categories/8/products", "/store/info"]


@pytest.mark.asyncio
@pytest.mark.parametrize("user,status", [({}, 401), ({"id": "foreign", "role": "owner"}, 404), ({"id": "owner", "role": "owner", "disabled": True}, 403)])
async def test_actual_management_route_denies_unbound_actor_before_native_read(monkeypatch, user, status):
    app, _, caller = integrated(monkeypatch, user)
    assert (await get(app)).status_code == status
    assert caller.calls == []


@pytest.mark.asyncio
async def test_actual_management_route_stale_and_same_revision_fields_guard(monkeypatch):
    app, db, caller = integrated(monkeypatch)
    assert (await get(app, "expected_revision=2&category_id=8")).status_code == 409
    assert caller.calls == []
    original = caller.responses["/categories/8/products"]
    class MutatingDict(dict):
        def __deepcopy__(self, memo):
            db.plan.row["fields"]["excluded_product_ids"] = ["2"]
            return deepcopy(original)
    caller.responses["/categories/8/products"] = MutatingDict(original)
    assert (await get(app)).status_code == 409
