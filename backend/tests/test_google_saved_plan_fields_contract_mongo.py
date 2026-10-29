"""Actual saved-plan writer and all three readers on an owned disposable replica."""
from copy import deepcopy
import os
from urllib.parse import urlsplit
import uuid

import httpx
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations_control_center.google_commercial_plan import PLANS
from tests.test_google_saved_plan_fields_contract import app_for, request_body
from tests.test_google_native_inventory_reader import Native


@pytest_asyncio.fixture
async def mongo():
    uri = os.getenv("GOOGLE_SAVED_PLAN_FIELDS_TEST_MONGO_URI")
    if not uri:
        pytest.skip("explicit disposable loopback replica required")
    parsed = urlsplit(uri)
    assert parsed.scheme == "mongodb" and parsed.hostname == "127.0.0.1" and parsed.port == 27480
    assert parsed.path in ("", "/") and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    name = "google_saved_fields_test_" + uuid.uuid4().hex
    db = client[name]
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") == "gSavedPlanFields" and hello.get("isWritablePrimary") is True
        async with await client.start_session() as session:
            session.start_transaction()
            await db.probe.insert_one({"x": 1}, session=session)
            await session.abort_transaction()
        assert await db.probe.count_documents({}) == 0
        await db.salla_integrations.insert_one({"user_id": "owner", "store_id": 9, "store_domain": "https://store.test",
            "status": "connected", "token_revision": "opaque", "private_metadata_sentinel": "not_returned"})
        yield db
    finally:
        await client.drop_database(name)
        client.close()


async def read(client, reader):
    if reader == "linked":
        return await client.get("/google_ads/commercial-plans/winter/linked-campaign-evidence?expected_revision=1")
    if reader == "category":
        return await client.get("/google_ads/commercial-plans/winter/live-category-preview?expected_revision=1&category_id=8")
    return await client.post("/google_ads/commercial-plans/winter/native-inventory-preview", json={
        "expected_revision": 1, "category_id": "8", "selected_product_ids": ["1"]})


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["linked", "category", "native"])
async def test_actual_mongo_put_then_each_reader_accepts_writer_saved_fields_without_synthesized_ack(mongo, reader):
    native = Native()
    app = app_for(mongo, native)
    payload = request_body()
    payload["pinned_product_ids"] = ["1"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        saved = await client.put("/google_ads/commercial-plans/winter", json=payload)
        assert saved.status_code == 200, saved.text
        row = await mongo[PLANS].find_one({"user_id": "owner", "plan_id": "winter"})
        assert "acknowledge_review" not in row["fields"] and "expected_revision" not in row["fields"]
        assert row["fields"] == saved.json()["fields"] and row["reviews"][0]["fields"] == row["fields"]
        connection = await mongo.salla_integrations.find_one({})
        result = await read(client, reader)
    assert result.status_code == 200, result.text
    assert result.json()["execution_authorized"] is False
    assert result.json()["plan_revision"] == 1
    assert await mongo[PLANS].find_one({"user_id": "owner", "plan_id": "winter"}) == row
    assert await mongo.salla_integrations.find_one({}) == connection
    assert "private_metadata_sentinel" not in result.text and "token_revision" not in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [None, False])
async def test_actual_mongo_write_missing_or_false_review_ack_is_rejected_without_plan(mongo, ack):
    native = Native()
    app = app_for(mongo, native)
    payload = request_body()
    if ack is None:
        payload.pop("acknowledge_review")
    else:
        payload["acknowledge_review"] = ack
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/google_ads/commercial-plans/winter", json=payload)
    assert response.status_code == 422
    assert await mongo[PLANS].count_documents({}) == 0 and native.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["linked", "category", "native"])
async def test_actual_mongo_invalid_saved_business_fields_still_fail_before_provider(mongo, reader):
    native = Native()
    app = app_for(mongo, native)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        saved = await client.put("/google_ads/commercial-plans/winter", json=request_body())
        assert saved.status_code == 200
        await mongo[PLANS].update_one({"user_id": "owner", "plan_id": "winter"}, {"$set": {"fields.ends_on": "2026-02-30"}})
        row = await mongo[PLANS].find_one({})
        result = await read(client, reader)
    assert result.status_code == 503
    assert native.calls == [] and await mongo[PLANS].find_one({}) == row
