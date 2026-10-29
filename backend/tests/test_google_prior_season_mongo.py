"""Real replica writer/prior-window/raw-projection acceptance; no provider reads."""
from copy import deepcopy
import os
from urllib.parse import urlsplit
import uuid

import httpx
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations_control_center.google_commercial_plan import PLANS
from integrations_control_center.google_plan_store_observations import read_store_observations
from integrations_control_center import google_prior_season_routes as routes
from tests.test_google_prior_season_routes import app_for, payload, get
from tests.test_google_prior_season_context import context
from tests.test_google_prior_season_observations import NOW, prior_order
from tests.test_google_store_product_observations import order


@pytest_asyncio.fixture
async def mongo():
    uri = os.getenv("GOOGLE_PRIOR_SEASON_TEST_MONGO_URI")
    if not uri:
        pytest.skip("explicit disposable loopback replica required")
    parsed = urlsplit(uri)
    assert parsed.scheme == "mongodb" and parsed.hostname == "127.0.0.1" and parsed.port == 27481
    assert parsed.path in ("", "/") and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    name = "google_prior_season_test_" + uuid.uuid4().hex
    db = client[name]
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") == "gPriorSeason" and hello.get("isWritablePrimary") is True
        async with await client.start_session() as session:
            session.start_transaction()
            await db.probe.insert_one({"x": 1}, session=session)
            await session.abort_transaction()
        assert await db.probe.count_documents({}) == 0
        await db.unified_orders.create_index([("user_id", 1), ("order_date", -1)])
        await db.users.insert_one({"id": "owner", "role": "owner"})
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def units(data, window="owner_reviewed_prior_season"):
    return data["products"][0]["currency_groups"][0]["windows"][window]["observed_units"]


@pytest.mark.asyncio
async def test_actual_writer_prior_query_and_current_four_windows_remain_separate(mongo, monkeypatch):
    previous = prior_order()
    current = order(order_id="11", order_number="21")
    current["raw_by_source"]["salla_direct"].update(id=11, reference_id=21)
    gap = order(order_id="12", order_number="22", order_date="2026-06-01")
    gap["raw_by_source"]["salla_direct"].update(id=12, reference_id=22, date="2026-06-01T10:00:00Z")
    foreign = prior_order()
    foreign["user_id"] = "foreign"
    await mongo.unified_orders.insert_many([previous, current, gap, foreign])
    app = app_for(monkeypatch, mongo)
    value = payload()
    value["prior_season_context"]["calendar_reference_text"] = "owner-declared official-looking reference, not verified"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        saved = await client.put("/google_ads/commercial-plans/winter", json=value)
        assert saved.status_code == 200
        row = await mongo[PLANS].find_one({})
        assert "acknowledge_review" not in row["fields"]
        result = await get(client)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["calendar_alignment_verification"] == "unknown"
    assert units(data["observations"]) == "2"
    assert data["observations"]["diagnostics"]["observed_documents"] == 1
    recent = await read_store_observations(mongo, owner_id="owner", product_ids=["123"], as_of=NOW)
    assert units(recent, "last_14_days") == "2"
    assert len(recent["windows"]) == 4 and recent["diagnostics"]["observed_documents"] == 1
    assert data["growth"] is data["profitability"] is None
    assert await mongo[PLANS].find_one({}) == row and await mongo.unified_orders.count_documents({}) == 4


@pytest.mark.asyncio
async def test_actual_prior_exact_riyadh_day_keeps_offset_candidate_binding(mongo, monkeypatch):
    await mongo.unified_orders.insert_one(prior_order())
    app = app_for(monkeypatch, mongo)
    value = payload()
    value["prior_season_context"] = context(date_from="2025-12-01", date_to="2025-12-01")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/google_ads/commercial-plans/winter", json=value)).status_code == 200
        result = await get(client)
    assert result.status_code == 200 and units(result.json()["observations"]) == "2"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["duplicate", "refund_action", "malformed_container"])
async def test_actual_prior_projection_preserves_dedup_and_refund_rejection(mongo, monkeypatch, kind):
    value = prior_order()
    if kind == "duplicate":
        await mongo.unified_orders.insert_many([value, deepcopy(value)])
    else:
        value["raw_by_source"]["salla_direct"]["payment_actions"] = (
            {"refund_action": {"has_refund_amount": True}} if kind == "refund_action" else "malformed")
        await mongo.unified_orders.insert_one(value)
    app = app_for(monkeypatch, mongo)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/google_ads/commercial-plans/winter", json=payload())).status_code == 200
        result = await get(client)
    assert result.status_code == 200
    assert result.json()["observations"]["products"][0]["currency_groups"] == []
    assert result.json()["observations"]["refunds_reconciled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["revision", "fields", "audit"])
async def test_actual_prior_read_race_invalidates_revision_fields_and_review_provenance(mongo, monkeypatch, kind):
    await mongo.unified_orders.insert_one(prior_order())
    app = app_for(monkeypatch, mongo)
    original = routes.read_prior_season_observations
    async def changing(*args, **kwargs):
        result = await original(*args, **kwargs)
        change = {"revision": 2} if kind == "revision" else {"fields.prior_season_context.alignment_note": "changed"} if kind == "fields" else {"updated_by": "foreign"}
        await mongo[PLANS].update_one({"user_id": "owner", "plan_id": "winter"}, {"$set": change})
        return result
    monkeypatch.setattr(routes, "read_prior_season_observations", changing)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/google_ads/commercial-plans/winter", json=payload())).status_code == 200
        assert (await get(client)).status_code == 409


@pytest.mark.asyncio
async def test_actual_prior_missing_index_has_no_unbounded_fallback(mongo, monkeypatch):
    await mongo.unified_orders.drop_indexes()
    app = app_for(monkeypatch, mongo)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/google_ads/commercial-plans/winter", json=payload())).status_code == 200
        result = await get(client)
    assert result.status_code == 503 and "unified_orders" not in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [None, False])
async def test_actual_prior_context_write_still_requires_explicit_review_ack(mongo, monkeypatch, ack):
    app = app_for(monkeypatch, mongo)
    value = payload()
    if ack is None:
        value.pop("acknowledge_review")
    else:
        value["acknowledge_review"] = ack
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/google_ads/commercial-plans/winter", json=value)).status_code == 422
    assert await mongo[PLANS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_actual_legacy_null_prior_context_skips_source_reader(mongo, monkeypatch):
    app = app_for(monkeypatch, mongo)
    async def forbidden(*args, **kwargs):
        raise AssertionError("legacy prior=None must not query orders")
    monkeypatch.setattr(routes, "read_prior_season_observations", forbidden)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/google_ads/commercial-plans/winter", json=payload(prior=False))).status_code == 200
        result = await get(client)
    assert result.status_code == 200 and result.json()["observations"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["disabled", "staff", "deleted", "bool_revision", "float_revision"])
async def test_actual_owner_or_typed_revision_race_never_returns_observations(mongo, monkeypatch, kind):
    await mongo.unified_orders.insert_one(prior_order())
    original = routes.read_prior_season_observations
    async def changing(*args, **kwargs):
        result = await original(*args, **kwargs)
        if kind == "deleted": await mongo.users.delete_many({"id": "owner"})
        elif kind in ("disabled", "staff"):
            await mongo.users.update_one({"id": "owner"}, {"$set": {"disabled": True} if kind == "disabled" else {"role": "staff"}})
        else:
            await mongo[PLANS].update_one({"user_id": "owner"}, {"$set": {"revision": True if kind == "bool_revision" else 1.0}})
        return result
    monkeypatch.setattr(routes, "read_prior_season_observations", changing)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(monkeypatch, mongo, actual_auth=True)), base_url="http://test", headers={"Authorization": "Bearer synthetic-token"}) as client:
        assert (await client.put("/google_ads/commercial-plans/winter", json=payload())).status_code == 200
        result = await get(client)
    assert result.status_code == (409 if kind.endswith("revision") else 403)
