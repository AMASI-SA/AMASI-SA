from copy import deepcopy
import httpx
import pytest
from fastapi import APIRouter, FastAPI

from integrations_control_center.google_native_inventory_routes import attach_google_native_inventory_routes
from tests.test_google_native_inventory_reader import setup


def fields():
    return {"instruction": "هدايا الشتاء", "season": "شتاء", "starts_on": "2026-10-01", "ends_on": "2026-12-31",
            "market": "SA", "audience": "الباحثون عن هدية", "message": "هدايا", "category_ids": ["8"]}


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


def app_and_db(user=None, injected=None):
    source, native, _ = setup()
    db = DB(source.salla_integrations)
    app, router = FastAPI(), APIRouter()
    async def current_user():
        return user if user is not None else {"id": "owner", "role": "owner"}
    attach_google_native_inventory_routes(router, db, current_user, caller=injected or native)
    app.include_router(router)
    return app, db, native


async def post(app, revision=1):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/google_ads/commercial-plans/winter/native-inventory-preview", json={
            "expected_revision": revision, "category_id": "8", "selected_product_ids": ["1"]})


@pytest.mark.asyncio
async def test_actual_standalone_post_reads_only_native_get_without_policy_or_plan_mutation():
    app, db, native = app_and_db()
    original = deepcopy(db.plan.row)
    response = await post(app)
    assert response.status_code == 200, response.text
    assert response.json()["execution_authorized"] is False
    assert response.json()["products"][0]["parent_stock"]["state"] == "zero_unlimited_unknown"
    assert db.plan.row == original and len(native.calls) == 8


@pytest.mark.asyncio
async def test_foreign_and_stale_and_unauthenticated_do_not_call_native():
    for user, revision, status in [({}, 1, 401), ({"id": "other", "role": "owner"}, 1, 404), (None, 2, 409)]:
        app, _, native = app_and_db(user)
        assert (await post(app, revision)).status_code == status
        assert native.calls == []


@pytest.mark.asyncio
async def test_same_revision_fields_race_after_product_calls_rejects_result():
    app, db, native = app_and_db()
    first = native.responses["/products/1/variants"]
    class Change(dict):
        def __deepcopy__(self, memo):
            db.plan.row["fields"]["message"] = "changed"
            return deepcopy(first)
    native.responses["/products/1/variants"] = Change(first)
    assert (await post(app)).status_code == 409


@pytest.mark.asyncio
async def test_native_transport_error_never_leaks_private_message():
    app, _, native = app_and_db()
    native.responses["/products/1"] = httpx.ConnectError("private transport")
    response = await post(app)
    assert response.status_code == 409 and response.json()["detail"]["reason"] == "native_read_unavailable"
    assert "private transport" not in response.text
