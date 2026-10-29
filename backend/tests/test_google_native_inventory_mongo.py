"""Actual disposable owner/plan/store guards; native API is fixture-only."""
import os
from urllib.parse import urlsplit
import uuid
import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from integrations_control_center.google_commercial_plan import PLANS
from integrations_control_center.google_native_inventory_routes import attach_google_native_inventory_routes
from tests.test_google_native_inventory_reader import Native
from tests.test_google_native_inventory_routes import fields


@pytest_asyncio.fixture
async def mongo():
    uri = os.getenv("GOOGLE_NATIVE_INVENTORY_TEST_MONGO_URI")
    if not uri:
        pytest.skip("explicit disposable loopback replica required")
    parsed = urlsplit(uri)
    assert parsed.scheme == "mongodb" and parsed.hostname == "127.0.0.1" and parsed.port == 27479
    assert parsed.path in ("", "/") and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    name = "google_native_inventory_test_" + uuid.uuid4().hex
    db = client[name]
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") == "gNativeInventory" and hello.get("isWritablePrimary") is True
        async with await client.start_session() as session:
            session.start_transaction()
            await db.probe.insert_one({"x": 1}, session=session)
            await session.abort_transaction()
        assert await db.probe.count_documents({}) == 0
        await db[PLANS].create_index([("user_id", 1), ("plan_id", 1)], unique=True)
        await db.salla_integrations.create_index("user_id", unique=True)
        await db[PLANS].insert_one({"user_id": "owner", "plan_id": "winter", "revision": 1, "fields": fields()})
        await db.salla_integrations.insert_one({"user_id": "owner", "store_id": 9, "store_domain": "https://store.test",
            "status": "connected", "token_revision": "opaque", "private_metadata_sentinel": "not_returned"})
        yield db
    finally:
        await client.drop_database(name)
        client.close()


async def request(db, caller=None, user=None, revision=1):
    native = caller or Native()
    app, router = FastAPI(), APIRouter()
    async def current_user():
        return user if user is not None else {"id": "owner", "role": "owner"}
    attach_google_native_inventory_routes(router, db, current_user, caller=native)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/google_ads/commercial-plans/winter/native-inventory-preview", json={
            "expected_revision": revision, "category_id": "8", "selected_product_ids": ["1"]})
    return response, native


@pytest.mark.asyncio
async def test_actual_owner_read_observations_without_db_mutation_or_native_writes(mongo):
    original = await mongo[PLANS].find_one({})
    connection = await mongo.salla_integrations.find_one({})
    response, native = await request(mongo)
    assert response.status_code == 200, response.text
    assert len(native.calls) == 8 and response.json()["execution_authorized"] is False
    assert "private_metadata_sentinel" not in response.text and "token_revision" not in response.text
    assert await mongo[PLANS].find_one({}) == original and await mongo.salla_integrations.find_one({}) == connection


@pytest.mark.asyncio
async def test_actual_foreign_owner_and_stale_revision_no_native_call(mongo):
    response, native = await request(mongo, user={"id": "foreign", "role": "owner"})
    assert response.status_code == 404 and native.calls == []
    response, native = await request(mongo, revision=2)
    assert response.status_code == 409 and native.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["plan_revision", "plan_fields", "credential_revision", "store_identity"])
async def test_actual_outer_binding_covers_category_and_selected_product_flow(mongo, kind):
    native = Native()
    async def changing(db, owner, method, path, *, params):
        result = await native(db, owner, method, path, params=params)
        if path == "/products/1/variants":
            if kind == "plan_revision":
                await mongo[PLANS].update_one({}, {"$set": {"revision": 2}})
            elif kind == "plan_fields":
                await mongo[PLANS].update_one({}, {"$set": {"fields.message": "changed"}})
            elif kind == "credential_revision":
                await mongo.salla_integrations.update_one({}, {"$set": {"token_revision": "changed"}})
            else:
                await mongo.salla_integrations.update_one({}, {"$set": {"store_id": 99}})
        return result
    response, _ = await request(mongo, caller=changing)
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_actual_standalone_route_detects_before_after_product_drift(mongo):
    native = Native()
    async def changing(db, owner, method, path, *, params):
        result = await native(db, owner, method, path, params=params)
        if path == "/products/1/variants":
            native.responses["/products/1"]["data"]["status"] = "hidden"
        return result
    response, _ = await request(mongo, caller=changing)
    assert response.status_code == 503 and response.json()["detail"]["reason"] == "bounded_source_unproved"
