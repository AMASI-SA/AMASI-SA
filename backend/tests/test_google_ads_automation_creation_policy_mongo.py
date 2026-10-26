"""Real Mongo CAS and unique-index races; only native HTTP transport is fake."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json

from fastapi import APIRouter, FastAPI, Header
import httpx
import pytest

from integrations_control_center import google_ads_automation_creation_policy as policy
from integrations_control_center.google_ads_client import GoogleAdsClient
from tests.test_google_ads_automation_policy_mongo import ACCOUNT, automation_mongo_uri, mongo_db
from tests.test_google_ads_automation_creation_policy import NOW, body
from tests.test_google_ads_automation_creation_contract import case


async def mounted(monkeypatch, database):
    product = case()["product"]
    await database.mezan_products_v2.insert_many([{**deepcopy(product), "user_id": owner} for owner in ("owner-a", "owner-b")])
    state = {"gate": None, "entered": asyncio.Event(), "mutations": 0}

    async def provider(request):
        payload = json.loads(request.content)
        if request.url.path.endswith("/googleAdsFields:search"):
            return httpx.Response(200, json={"results": [{"name": name, "selectable": True} for name in (
                "geo_target_constant.id", "geo_target_constant.name", "geo_target_constant.status")]})
        if not request.url.path.endswith("/googleAds:search"):
            state["mutations"] += 1
            raise AssertionError("creation settings never dispatch a native mutation")
        if "FROM customer" in payload["query"]:
            state["entered"].set()
            if state["gate"] is not None: await state["gate"].wait()
            return httpx.Response(200, json={"results": [{"customer": {"id": ACCOUNT, "currencyCode": "SAR",
                "timeZone": "Asia/Riyadh", "manager": False}}], "totalResultsCount": "1"})
        return httpx.Response(200, json={"results": [{"geoTargetConstant": {"id": "2682", "name": "Saudi Arabia", "status": "ENABLED"}}], "totalResultsCount": "1"})

    async def account_client(db, user_id, account_id, **kwargs):
        assert db is database and user_id in {"owner-a", "owner-b"} and account_id == ACCOUNT
        return GoogleAdsClient("test-only-token", {"ad_account_id": account_id}, developer_token="test-only-token",
            transport=httpx.MockTransport(provider)), "test-only-epoch"

    async def current_user(x_test_owner: str = Header(default="owner-a")): return {"id": x_test_owner}
    monkeypatch.setattr(policy, "account_client", account_client)
    monkeypatch.setattr(policy, "now", lambda: NOW)
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: True)
    target, router = FastAPI(), APIRouter()
    policy.attach_google_ads_automation_creation_policy_routes(router, database, current_user)
    target.include_router(router)
    return target, state


@pytest.mark.asyncio
async def test_real_creation_unique_index_has_one_concurrent_first_save_winner(monkeypatch, mongo_db):
    target, state = await mounted(monkeypatch, mongo_db)
    state["gate"] = asyncio.Barrier(2)
    path = f"/google_ads/automation/creation-policy/{ACCOUNT}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        responses = await asyncio.wait_for(asyncio.gather(*(client.put(path, json=body()) for _ in range(2))), timeout=15)
    assert sorted(response.status_code for response in responses) == [200, 409]
    rows = await mongo_db[policy.CREATION_POLICIES].find({"user_id": "owner-a", "account_id": ACCOUNT}).to_list(10)
    assert len(rows) == 1 and rows[0]["revision"] == 1 and rows[0]["enabled"] is False
    assert rows[0]["template"]["approved_by"] == "owner-a" and len(rows[0]["events"]) == 1
    indexes = await mongo_db[policy.CREATION_POLICIES].index_information()
    assert indexes["google_creation_tenant_account"]["unique"] is True and state["mutations"] == 0


@pytest.mark.asyncio
async def test_real_revoke_defeats_slow_save_without_erasing_creation_claim_or_ledger(monkeypatch, mongo_db):
    target, state = await mounted(monkeypatch, mongo_db)
    path = f"/google_ads/automation/creation-policy/{ACCOUNT}"
    scope = {"user_id": "owner-a", "account_id": ACCOUNT}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        assert (await client.put(path, json=body())).status_code == 200
        await mongo_db[policy.CREATION_POLICIES].update_one(scope, {"$set": {"_creation_claim": {"claim_id": "private"}, "_creation_ledger": {"private": True}, "_creation_generation": 8}})
        state["gate"], state["entered"] = asyncio.Event(), asyncio.Event()
        pending = asyncio.create_task(client.put(path, json=body(expected_revision=1)))
        await asyncio.wait_for(state["entered"].wait(), timeout=5)
        assert (await client.post(path + "/revoke", json={"expected_revision": 1})).status_code == 200
        state["gate"].set()
        assert (await pending).status_code == 409
    row = await mongo_db[policy.CREATION_POLICIES].find_one(scope)
    assert row["revision"] == 2 and row["enabled"] is False and row["_creation_claim"] == {"claim_id": "private"}
    assert row["_creation_ledger"] == {"private": True} and row["_creation_generation"] == 8 and state["mutations"] == 0


@pytest.mark.asyncio
async def test_real_activation_cas_and_tenant_scope_keep_approval_atomic(monkeypatch, mongo_db):
    target, state = await mounted(monkeypatch, mongo_db)
    path = f"/google_ads/automation/creation-policy/{ACCOUNT}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        assert (await client.put(path, json=body())).status_code == 200
        other = await client.get(path, headers={"x-test-owner": "owner-b"})
        assert other.status_code == 200 and other.json()["configured"] is False
        state["gate"] = asyncio.Barrier(2)
        payload = {"expected_revision": 1, "acknowledge_creation": True, "acknowledge_creative": True}
        responses = await asyncio.wait_for(asyncio.gather(*(client.post(path + "/activate", json=payload) for _ in range(2))), timeout=15)
    assert sorted(response.status_code for response in responses) == [200, 409]
    row = await mongo_db[policy.CREATION_POLICIES].find_one({"user_id": "owner-a", "account_id": ACCOUNT})
    assert row["revision"] == row["activation_revision"] == 2 and row["activated_by"] == "owner-a"
    assert row["template"]["approved_version"] == row["template_version"] == 1 and len(row["events"]) == 2
    assert state["mutations"] == 0
