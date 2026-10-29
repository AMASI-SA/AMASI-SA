from copy import deepcopy
import httpx
import pytest
from fastapi import APIRouter, FastAPI

from integrations_control_center.google_live_category_routes import attach_google_live_category_routes
from tests.test_google_live_category_reader import setup


def fields():
    return {"instruction": "هدايا الشتاء", "season": "شتاء", "starts_on": "2026-10-01", "ends_on": "2026-12-31",
            "market": "SA", "audience": "الباحثون عن هدية", "message": "هدايا", "category_ids": ["8"],
            "pinned_product_ids": [], "excluded_product_ids": ["1"], "acknowledge_review": True}


class Plan:
    def __init__(self):
        self.row = {"user_id": "owner", "plan_id": "winter", "revision": 1, "fields": fields()}
    async def find_one(self, query, projection):
        return deepcopy(self.row) if all(self.row.get(k) == v for k, v in query.items()) else None


class DB:
    def __init__(self, integration):
        self.salla_integrations, self.plan = integration, Plan()
    def __getitem__(self, name):
        assert name == "mezan_google_commercial_plans_v1"
        return self.plan


def app_and_db(user=None):
    source, caller, _ = setup()
    db = DB(source.salla_integrations)
    app, router = FastAPI(), APIRouter()
    async def current_user():
        return user if user is not None else {"id": "owner", "role": "owner"}
    attach_google_live_category_routes(router, db, current_user, caller=caller)
    app.include_router(router)
    return app, db, caller


async def get(app, revision=1, category="8"):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/google_ads/commercial-plans/winter/live-category-preview?expected_revision={revision}&category_id={category}")


@pytest.mark.asyncio
async def test_actual_standalone_route_scope_and_readonly_authority():
    app, db, caller = app_and_db()
    original = deepcopy(db.plan.row)
    response = await get(app)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["plan_id"] == "winter" and data["plan_revision"] == 1
    assert data["inventory_verified"] is False and data["execution_authorized"] is False
    assert data["pagination_traversal_complete"] is True and data["remote_snapshot_atomic"] is False
    assert db.plan.row == original


@pytest.mark.asyncio
async def test_unauthenticated_foreign_or_stale_plan_never_calls_provider():
    for user, revision, status in [({}, 1, 401), ({"id": "foreign", "role": "owner"}, 1, 404), (None, 2, 409)]:
        app, _, caller = app_and_db(user)
        assert (await get(app, revision)).status_code == status
        assert caller.calls == []


@pytest.mark.asyncio
async def test_fields_change_during_read_invalidates_same_revision():
    app, db, caller = app_and_db()
    original = caller.responses["/categories/8/products"]
    class MutatingDict(dict):
        def __deepcopy__(self, memo):
            db.plan.row["fields"]["excluded_product_ids"] = ["2"]
            return deepcopy(original)
    caller.responses["/categories/8/products"] = MutatingDict(original)
    assert (await get(app)).status_code == 409


@pytest.mark.asyncio
async def test_invalid_category_cannot_reach_fixed_transport():
    app, _, caller = app_and_db()
    assert (await get(app, category="99")).status_code == 409
    assert caller.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ReadTimeout("private transport detail"), httpx.ConnectError("private transport detail")])
async def test_native_transport_failures_are_sanitized_and_stop_expansion(error):
    app, _, caller = app_and_db()
    caller.responses["/categories/8"] = error
    response = await get(app)
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "native_read_unavailable"
    assert "private transport detail" not in response.text
    assert [path for path, _ in caller.calls] == ["/store/info", "/categories/8"]
