from copy import deepcopy

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from integrations_control_center.google_commercial_plan import PLANS
from integrations_control_center.google_linked_campaign_routes import attach_google_linked_campaign_routes
from tests.test_google_linked_campaign_reader import Collection, database, NOW


def fields():
    return {"instruction": "هدايا الشتاء", "season": "شتاء", "starts_on": "2026-10-01", "ends_on": "2026-12-31",
            "market": "SA", "audience": "الباحثون عن هدية", "message": "هدايا", "pinned_product_ids": ["product"],
            "excluded_product_ids": [], "acknowledge_review": True}


def app_and_db(user=None):
    db = database()
    db[PLANS] = Collection([{"user_id": "owner", "plan_id": "seasonal-focus", "revision": 1, "fields": fields()}])
    async def current_user():
        return user if user is not None else {"id": "owner", "role": "owner"}
    app, router = FastAPI(), APIRouter()
    attach_google_linked_campaign_routes(router, db, current_user, clock=lambda: NOW)
    app.include_router(router)
    return app, db


async def get(app, revision=1):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/google_ads/commercial-plans/seasonal-focus/linked-campaign-evidence?expected_revision={revision}")


@pytest.mark.asyncio
async def test_actual_standalone_route_reads_saved_scope_without_writes():
    app, db = app_and_db()
    original = deepcopy(db[PLANS].rows)
    response = await get(app)
    assert response.status_code == 200, response.text
    assert response.json()["plan_revision"] == 1
    assert len(response.json()["campaign_contexts"]) == 1
    assert response.json()["execution_authorized"] is False
    assert db[PLANS].rows == original


@pytest.mark.asyncio
async def test_foreign_owner_cannot_read_saved_plan():
    app, _ = app_and_db({"id": "other", "role": "owner"})
    assert (await get(app)).status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_and_stale_revision_fail_closed():
    app, _ = app_and_db({})
    assert (await get(app)).status_code == 401
    app, _ = app_and_db()
    assert (await get(app, 2)).status_code == 409


@pytest.mark.asyncio
async def test_plan_fields_changed_during_read_invalidates_even_same_revision():
    app, db = app_and_db()
    plans = db[PLANS]
    original = plans.aggregate
    calls = 0
    def aggregate(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            plans.rows[0]["fields"]["pinned_product_ids"] = ["different"]
        return original(*args, **kwargs)
    plans.aggregate = aggregate
    assert (await get(app)).status_code == 409


@pytest.mark.asyncio
async def test_empty_selection_uses_no_association_or_provider_reads():
    app, db = app_and_db()
    db[PLANS].rows[0]["fields"]["pinned_product_ids"] = []
    for name in list(db):
        if name != PLANS:
            del db[name]
    response = await get(app)
    assert response.status_code == 200
    assert response.json()["products"] == []
