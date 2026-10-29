"""Real disposable replica tests using existing association/commit writers."""
from copy import deepcopy
from datetime import datetime, timezone
import os
from urllib.parse import urlsplit
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from integrations_control_center import campaign_product_associations as graph
from integrations_control_center.google_commercial_plan import PLANS
from integrations_control_center.google_linked_campaign_reader import linked_campaign_evidence, SNAPSHOTS, RUNS
from integrations_control_center.google_linked_campaign_routes import attach_google_linked_campaign_routes
from snapchat_v2.total_facts import upsert_total_facts
from snapchat_v2.committed_totals import publish_committed_totals
from tests.test_google_linked_campaign_evidence import source, NOW
from tests.test_google_linked_campaign_routes import fields


@pytest_asyncio.fixture
async def mongo():
    uri = os.getenv("GOOGLE_LINKED_CAMPAIGN_TEST_MONGO_URI")
    if not uri:
        pytest.skip("explicit disposable loopback replica required")
    parsed = urlsplit(uri)
    assert parsed.scheme == "mongodb" and parsed.hostname == "127.0.0.1" and parsed.port == 27477
    assert parsed.path in ("", "/") and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    name = "google_linked_campaign_test_" + uuid.uuid4().hex
    db = client[name]
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") == "gLinkedCampaign" and hello.get("isWritablePrimary") is True
        async with await client.start_session() as session:
            session.start_transaction()
            await db.probe.insert_one({"x": 1}, session=session)
            await session.abort_transaction()
        assert await db.probe.count_documents({}) == 0
        yield db
    finally:
        await client.drop_database(name)
        client.close()


async def seed(db, monkeypatch):
    recorded = datetime(2026, 10, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(graph, "_utcnow", lambda: recorded)
    await graph.ensure_campaign_product_association_indexes(db)
    await graph.attach_campaign_product(db, "owner", {
        "provider": "snapchat_ads", "account_id": "account", "campaign_id": "campaign", "product_id": "product",
        "valid_from": recorded, "evidence": {"source": "manual", "verification_status": "verified", "observed_at": recorded}},
        idempotency_key="source-fixture-1")
    snapshot, run = source()
    await db[RUNS].insert_one(run)
    fact = snapshot["rows"]["campaign"][0]
    facts = [fact]
    for level in ("ad_squad", "ad"):
        child = deepcopy(fact)
        child.update(entity_type=level, external_id=level + "-id")
        facts.append(child)
    await upsert_total_facts(db, facts, now=fact["observed_at"])
    assert await publish_committed_totals(db, "run") == 1


async def read(db):
    return await linked_campaign_evidence(db, owner_id="owner", product_ids=["product"], as_of=NOW)


@pytest.mark.asyncio
async def test_actual_graph_and_committed_writers_native_bson_context(mongo, monkeypatch):
    await seed(mongo, monkeypatch)
    original = await mongo[SNAPSHOTS].find_one({})
    result = await read(mongo)
    assert len(result["campaign_contexts"]) == 1
    row = result["campaign_contexts"][0]
    assert row["currency"] == "SAR" and row["metrics"]["provider_reported_purchases"] == 3
    assert row["independent_store_orders"] is None and row["product_attribution_verified"] is False
    assert await mongo[SNAPSHOTS].find_one({}) == original


@pytest.mark.asyncio
async def test_actual_late_variant_writer_cannot_inherit_earlier_association_evidence(mongo, monkeypatch):
    await seed(mongo, monkeypatch)
    late = datetime(2026, 10, 29, tzinfo=timezone.utc)
    monkeypatch.setattr(graph, "_utcnow", lambda: late)
    newer = await graph.attach_campaign_product(mongo, "owner", {
        "provider": "snapchat_ads", "account_id": "account", "campaign_id": "campaign", "product_id": "product",
        "product_variant_id": "new-size", "valid_from": late,
        "evidence": {"source": "manual", "verification_status": "verified", "observed_at": late}},
        idempotency_key="source-late-variant")
    result = await read(mongo)
    association = next(a for a in result["products"][0]["associations"] if a["event_id"] == newer["event_id"])
    assert association["observed_context_days"] == 0
    assert association["metrics_status"] == "no_qualified_historical_context"
    assert len(result["campaign_contexts"]) == 1 and len(result["products"][0]["context_refs"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["foreign_owner", "wrong_account", "incomplete", "wrong_window"])
async def test_producing_run_mismatch_never_promotes_committed_snapshot(mongo, monkeypatch, mutation):
    await seed(mongo, monkeypatch)
    patch = {"foreign_owner": {"user_id": "foreign"}, "wrong_account": {"ad_account_id": "foreign"},
             "incomplete": {"status": "running"}, "wrong_window": {"request_window.end_utc": datetime(2026, 10, 28, 12, tzinfo=timezone.utc)}}[mutation]
    await mongo[RUNS].update_one({"sync_run_id": "run"}, {"$set": patch})
    result = await read(mongo)
    assert result["campaign_contexts"] == []
    assert result["products"][0]["associations"][0]["rejected_source_snapshots"] == 1


@pytest.mark.asyncio
async def test_legacy_mutable_facts_never_fallback_when_commit_absent(mongo, monkeypatch):
    await seed(mongo, monkeypatch)
    await mongo[SNAPSHOTS].delete_many({})
    assert (await read(mongo))["campaign_contexts"] == []


@pytest.mark.asyncio
async def test_scalar_metrics_container_and_expired_snapshot_remain_unknown(mongo, monkeypatch):
    await seed(mongo, monkeypatch)
    await mongo[SNAPSHOTS].update_one({}, {"$set": {"rows.campaign": "malformed"}})
    assert (await read(mongo))["campaign_contexts"] == []
    await mongo[SNAPSHOTS].delete_many({})
    assert await publish_committed_totals(mongo, "run") == 1
    await mongo[SNAPSHOTS].update_one({}, {"$set": {"expires_at": NOW}})
    assert (await read(mongo))["campaign_contexts"] == []


@pytest.mark.asyncio
async def test_actual_bson_size_guard_rejects_before_nested_materialization(mongo, monkeypatch):
    await seed(mongo, monkeypatch)
    await mongo[SNAPSHOTS].update_one({}, {"$set": {"oversized": "x" * 530000}})
    with pytest.raises(ValueError, match="source_document_limit"):
        await read(mongo)


@pytest.mark.asyncio
async def test_actual_standalone_owner_route_saved_scope_and_revision(mongo, monkeypatch):
    await seed(mongo, monkeypatch)
    await mongo[PLANS].insert_one({"user_id": "owner", "plan_id": "winter", "revision": 1, "fields": fields()})
    app, router = FastAPI(), APIRouter()
    async def owner():
        return {"id": "owner", "role": "owner"}
    attach_google_linked_campaign_routes(router, mongo, owner, clock=lambda: NOW)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/google_ads/commercial-plans/winter/linked-campaign-evidence?expected_revision=1")
        stale = await client.get("/google_ads/commercial-plans/winter/linked-campaign-evidence?expected_revision=2")
    assert response.status_code == 200, response.text
    assert len(response.json()["campaign_contexts"]) == 1
    assert stale.status_code == 409
    assert await mongo[PLANS].count_documents({}) == 1
