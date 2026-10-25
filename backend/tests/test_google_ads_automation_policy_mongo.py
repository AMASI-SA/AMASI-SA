"""Standing-policy revision races against disposable real MongoDB storage.

Only the provider transport is fake. Owner resolution, native metadata parsing,
account discovery, unique indexes, inserts, CAS updates and audit pushes use their
real production implementations. No credentials or external Google calls exist.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlsplit
import uuid

import httpx
from fastapi import APIRouter, FastAPI, Header
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio

from integrations_control_center import google_ads_automation_policy as policy
from integrations_control_center.google_ads_client import GoogleAdsClient

ACCOUNT = "1234567890"
CURRENT = datetime(2026, 10, 26, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def automation_mongo_uri():
    uri = os.getenv("GOOGLE_ADS_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Set GOOGLE_ADS_TEST_MONGO_URI to a disposable loopback MongoDB")
    parsed = urlsplit(uri)
    if (parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None or parsed.username is not None or parsed.password is not None):
        raise ValueError("Automation policy tests require a disposable loopback MongoDB")
    return uri


@pytest_asyncio.fixture
async def mongo_db(automation_mongo_uri):
    client = AsyncIOMotorClient(automation_mongo_uri, serverSelectionTimeoutMS=1000)
    name = "google_automation_policy_test_" + uuid.uuid4().hex
    database = client[name]
    try:
        await client.admin.command("ping")
        await database.users.insert_many([{"id": "owner-a", "role": "owner"}, {"id": "owner-b", "role": "owner"}])
        await database.mezan_integration_accounts_v2.insert_many([
            {"user_id": owner, "provider": "google_ads", "ad_account_id": ACCOUNT,
             "connection_status": "connected", "connection_provenance": "api_connection", "mezan_selected": True}
            for owner in ("owner-a", "owner-b")])
        yield database
    finally:
        # A random test-only database is the only cleanup target.
        await client.drop_database(name)
        client.close()


def request_body(**changes):
    return {"expected_revision": 0, "enabled": True, "campaign_ids": ["456"], "allowed_actions": ["pause"],
        "max_campaign_daily_budget_native": "40", "max_account_daily_budget_native": "55",
        "max_budget_change_percent": "10", "cooldown_seconds": 3600, "max_actions_per_day": 2,
        "expires_at": (CURRENT + timedelta(days=1)).isoformat(), **changes}


def mounted_app(monkeypatch, database):
    state = {"gate": None, "reads": 0, "writes": 0}

    async def provider(request):
        if not request.url.path.endswith("/googleAds:search"):
            state["writes"] += 1
            raise AssertionError("Standing policy must never dispatch a provider mutation")
        state["reads"] += 1
        if state["gate"] is not None:
            await state["gate"].wait()
        return httpx.Response(200, json={"results": [{"customer": {"id": ACCOUNT, "currencyCode": "SAR",
            "timeZone": "Asia/Riyadh", "descriptiveName": "Test account", "manager": False}}], "totalResultsCount": "1"})

    async def native_client(db, user_id, account_id, **kwargs):
        assert db is database and user_id in {"owner-a", "owner-b"} and account_id == ACCOUNT
        # Keep the real account_metadata decoder and identity verification seam.
        return GoogleAdsClient("test-only-token", {"ad_account_id": account_id},
            transport=httpx.MockTransport(provider), developer_token="test-only-developer-token"), "test-only-epoch"

    async def current_user(x_test_owner: str = Header(default="owner-a")):
        return {"id": x_test_owner}

    monkeypatch.setattr(policy, "account_client", native_client)
    monkeypatch.setattr(policy, "now", lambda: CURRENT)
    app, router = FastAPI(), APIRouter()
    policy.attach_google_ads_automation_policy_routes(router, database, current_user)
    app.include_router(router)
    return app, state


@pytest.mark.asyncio
async def test_real_unique_index_concurrent_initial_saves_have_one_winner(monkeypatch, mongo_db):
    app, state = mounted_app(monkeypatch, mongo_db)
    state["gate"] = asyncio.Barrier(2)
    path = f"/google_ads/automation/policy/{ACCOUNT}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        replies = await asyncio.wait_for(asyncio.gather(*(client.put(path, json=request_body()) for _ in range(2))), timeout=10)
    assert sorted(reply.status_code for reply in replies) == [200, 409]
    rejected = next(reply.json() for reply in replies if reply.status_code == 409)
    assert rejected["detail"]["code"] == "google_automation_policy_changed"
    rows = await mongo_db[policy.AUTOMATION_POLICIES].find({"user_id": "owner-a", "account_id": ACCOUNT}).to_list(10)
    assert len(rows) == 1 and rows[0]["revision"] == 1 and len(rows[0]["events"]) == 1
    indexes = await mongo_db[policy.AUTOMATION_POLICIES].index_information()
    assert indexes["google_automation_tenant_account"]["unique"] is True
    assert state["reads"] == 2 and state["writes"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("rival", ["update", "revoke"])
async def test_real_revision_cas_update_race_increments_once(monkeypatch, mongo_db, rival):
    app, state = mounted_app(monkeypatch, mongo_db)
    path = f"/google_ads/automation/policy/{ACCOUNT}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.put(path, json=request_body())).status_code == 200
        if rival == "update":
            # Both native pre-reads complete before either attempts the same CAS.
            state["gate"] = asyncio.Barrier(2)
            competitor = client.put(path, json=request_body(expected_revision=1, max_actions_per_day=3))
        else:
            competitor = client.post(path + "/revoke", json={"expected_revision": 1})
        replies = await asyncio.wait_for(asyncio.gather(
            client.put(path, json=request_body(expected_revision=1, max_actions_per_day=4)), competitor), timeout=10)
        assert sorted(reply.status_code for reply in replies) == [200, 409]
        rejected = next(reply.json() for reply in replies if reply.status_code == 409)
        assert rejected["detail"]["code"] == "google_automation_policy_changed"
        stored = await mongo_db[policy.AUTOMATION_POLICIES].find_one({"user_id": "owner-a", "account_id": ACCOUNT})
        assert stored["revision"] == 2
        assert [event["revision"] for event in stored["events"]] == [1, 2]
        assert stored["events"][-1]["enabled"] == stored["enabled"]
        # Neither a stale save nor stale revocation can win after revision2.
        state["gate"] = None
        assert (await client.post(path + "/revoke", json={"expected_revision": 1})).status_code == 409
    assert state["writes"] == 0


@pytest.mark.asyncio
async def test_real_compound_tenant_scope_hides_and_preserves_other_owner_policy(monkeypatch, mongo_db):
    app, state = mounted_app(monkeypatch, mongo_db)
    path = f"/google_ads/automation/policy/{ACCOUNT}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.put(path, json=request_body())).status_code == 200
        other = {"x-test-owner": "owner-b"}
        unseen = await client.get(path, headers=other)
        assert unseen.status_code == 200 and unseen.json()["configured"] is False
        assert (await client.post(path + "/revoke", json={"expected_revision": 1}, headers=other)).status_code == 409
        # Same native account id can have separate policies in distinct tenants.
        assert (await client.put(path, json=request_body(max_actions_per_day=3), headers=other)).status_code == 200
        assert (await client.post(path + "/revoke", json={"expected_revision": 1}, headers=other)).status_code == 200
    rows = await mongo_db[policy.AUTOMATION_POLICIES].find({"account_id": ACCOUNT}).to_list(10)
    by_owner = {row["user_id"]: row for row in rows}
    assert len(by_owner) == 2
    assert by_owner["owner-a"]["enabled"] is True and by_owner["owner-a"]["revision"] == 1
    assert by_owner["owner-b"]["enabled"] is False and by_owner["owner-b"]["revision"] == 2
    assert state["writes"] == 0
