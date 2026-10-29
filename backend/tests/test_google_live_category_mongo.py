"""Disposable replica proves owner/plan/connection guards; provider is fixture-only."""
from copy import deepcopy
import os
from urllib.parse import urlsplit
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from integrations_control_center.google_live_category_routes import attach_google_live_category_routes
from integrations_control_center.google_commercial_plan import PLANS
from tests.test_google_live_category_reader import Native
from tests.test_google_live_category_routes import fields


@pytest_asyncio.fixture
async def mongo():
    uri = os.getenv("GOOGLE_LIVE_CATEGORY_TEST_MONGO_URI")
    if not uri:
        pytest.skip("explicit own disposable loopback replica required")
    parsed = urlsplit(uri)
    assert parsed.scheme == "mongodb" and parsed.hostname == "127.0.0.1" and parsed.port == 27478
    assert parsed.path in ("", "/") and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    name = "google_live_category_test_" + uuid.uuid4().hex
    db = client[name]
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") == "gLiveCategory" and hello.get("isWritablePrimary") is True
        async with await client.start_session() as session:
            session.start_transaction()
            await db.probe.insert_one({"x": 1}, session=session)
            await session.abort_transaction()
        assert await db.probe.count_documents({}) == 0
        await db[PLANS].create_index([("user_id", 1), ("plan_id", 1)], unique=True)
        await db.salla_integrations.create_index("user_id", unique=True)
        await db[PLANS].insert_one({"user_id": "owner", "plan_id": "winter", "revision": 1, "fields": fields()})
        await db.salla_integrations.insert_one({"user_id": "owner", "store_id": 9, "store_domain": "https://store.test",
            "status": "connected", "token_revision": "opaque", "private_metadata_sentinel": "not_for_response"})
        yield db
    finally:
        await client.drop_database(name)
        client.close()


async def request(db, *, caller=None, user=None, revision=1):
    app, router = FastAPI(), APIRouter()
    async def current_user():
        return user if user is not None else {"id": "owner", "role": "owner"}
    native = caller or Native()
    attach_google_live_category_routes(router, db, current_user, caller=native)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/google_ads/commercial-plans/winter/live-category-preview?expected_revision={revision}&category_id=8")
    return response, native


@pytest.mark.asyncio
async def test_actual_mongo_native_fixture_read_without_scope_or_mutations(mongo):
    before_plan = await mongo[PLANS].find_one({})
    before_integration = await mongo.salla_integrations.find_one({})
    response, native = await request(mongo)
    assert response.status_code == 200, response.text
    assert response.json()["pagination_traversal_complete"] is True
    assert response.json()["inventory_verified"] is False
    assert "private_metadata_sentinel" not in response.text and "token_revision" not in response.text
    assert before_plan == await mongo[PLANS].find_one({})
    assert before_integration == await mongo.salla_integrations.find_one({})
    assert len(native.calls) == 4


@pytest.mark.asyncio
async def test_actual_foreign_owner_and_stale_revision_no_native_read(mongo):
    response, native = await request(mongo, user={"id": "foreign", "role": "owner"})
    assert response.status_code == 404 and native.calls == []
    response, native = await request(mongo, revision=2)
    assert response.status_code == 409 and native.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["revision", "same_revision_fields", "connection_revision", "store_identity"])
async def test_actual_plan_and_integration_race_invalidate_fixture_observation(mongo, kind):
    native = Native()
    async def changing(db, owner, method, path, *, params):
        result = await native(db, owner, method, path, params=params)
        if path == "/categories/8/products":
            if kind == "revision":
                await mongo[PLANS].update_one({}, {"$set": {"revision": 2}})
            elif kind == "same_revision_fields":
                await mongo[PLANS].update_one({}, {"$set": {"fields.excluded_product_ids": ["2"]}})
            elif kind == "connection_revision":
                await mongo.salla_integrations.update_one({}, {"$set": {"token_revision": "changed"}})
            else:
                await mongo.salla_integrations.update_one({}, {"$set": {"store_id": 99}})
        return result
    response, _ = await request(mongo, caller=changing)
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_actual_staff_is_not_owner_even_with_ads_read_permission(mongo):
    await mongo.users.insert_many([{"id": "owner", "role": "owner"},
        {"id": "staff", "role": "viewer", "created_by": "owner", "extra_permissions": ["ads.view"]}])
    response, native = await request(mongo, user={"id": "staff", "role": "viewer"})
    assert response.status_code == 403 and native.calls == []
