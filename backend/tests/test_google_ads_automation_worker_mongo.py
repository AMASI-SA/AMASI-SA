"""Real Mongo lease ownership for default-off Google automation cycles.

The cycle selector, claim CAS, ownership checks and final release use real
storage. Injected analysis returns no actionable result; no model, Google
client, proposal preview, provider mutation or credentials are used.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlsplit
import uuid

from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio

from integrations_control_center import google_ads_automation_worker as worker
from integrations_control_center.google_ads_automation_policy import AUTOMATION_POLICIES
from integrations_control_center.google_ads_management import ANALYSES, PROPOSALS

NOW = datetime(2026, 10, 26, 1, tzinfo=timezone.utc)
USER, ACCOUNT = "worker-test-owner", "1234567890"


@pytest.fixture(scope="session")
def worker_mongo_uri():
    uri = os.getenv("GOOGLE_ADS_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Set GOOGLE_ADS_TEST_MONGO_URI to a disposable loopback MongoDB")
    parsed = urlsplit(uri)
    if (parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None or parsed.username is not None or parsed.password is not None):
        raise ValueError("Worker lease tests require a disposable loopback MongoDB")
    return uri


@pytest_asyncio.fixture
async def worker_db(worker_mongo_uri):
    # Deliberately retain Motor's default naive-UTC decoding, as production
    # callers are not required to enable a custom tz_aware codec.
    client = AsyncIOMotorClient(worker_mongo_uri, serverSelectionTimeoutMS=1000)
    name = "google_automation_worker_test_" + uuid.uuid4().hex
    database = client[name]
    try:
        await client.admin.command("ping")
        await database.users.insert_one({"id": USER, "role": "owner"})
        await database[AUTOMATION_POLICIES].insert_one({
            "user_id": USER, "account_id": ACCOUNT, "schema_version": 1, "revision": 1,
            "enabled": True, "activation_version": 1, "activation_revision": 1,
            "activated_by": USER, "activated_at": NOW.isoformat(),
            "currency_code": "SAR", "time_zone": "Asia/Riyadh",
            "campaign_ids": ["456"], "allowed_actions": ["pause"],
            "max_campaign_daily_budget_native": "40", "max_account_daily_budget_native": "55",
            "max_budget_change_percent": "10", "cooldown_seconds": 3600,
            "max_actions_per_day": 3, "expires_at": (NOW + timedelta(days=1)).isoformat(),
        })
        yield database
    finally:
        # Only this randomly named disposable test database is removed.
        await client.drop_database(name)
        client.close()


def dependencies(monkeypatch, builder, *, clock=lambda: NOW, reconcile=None):
    monkeypatch.setattr(worker, "worker_enabled", lambda: True)
    monkeypatch.setattr(worker, "execution_enabled", lambda: True)
    async def forbidden(*args, **kwargs):
        raise AssertionError("Lease tests must not reach inventory, model, preview or provider execution")
    return {"analysis_builder": builder, "inventory_reader": forbidden,
        "previewer": forbidden, "executor": forbidden, "reconciler": reconcile or forbidden, "clock": clock}


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_real_two_replicas_have_one_claim_and_release_prevents_immediate_reclaim(monkeypatch, worker_db):
    entered, resume = asyncio.Event(), asyncio.Event()
    calls = []
    async def builder(*args, **kwargs):
        calls.append("analysis-seam"); entered.set()
        await resume.wait()
        return {}  # A non-actionable result stops before any native read/write.
    deps = dependencies(monkeypatch, builder)
    first = asyncio.create_task(worker.run_google_automation_cycle(worker_db, **deps))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        second = await asyncio.wait_for(worker.run_google_automation_cycle(worker_db, **deps), timeout=5)
        assert second["results"] == [{"account_id": ACCOUNT, "status": "not_due"}]
        running = await worker_db[worker.CYCLES].find_one({"user_id": USER, "account_id": ACCOUNT})
        assert running["state"] == "running" and len(calls) == 1
    finally:
        resume.set()
        first_result = await asyncio.wait_for(first, timeout=5)
    assert first_result["results"][0]["status"] == "analysis_unproved"
    rows = await worker_db[worker.CYCLES].find({"user_id": USER, "account_id": ACCOUNT}).to_list(2)
    assert len(rows) == 1
    released = rows[0]
    assert released["state"] == "idle" and released["last_status"] == "analysis_unproved"
    assert aware(released["next_run_at"]) == NOW + worker.INTERVAL
    third = await worker.run_google_automation_cycle(worker_db, **deps)
    assert third["results"][0]["status"] == "not_due" and len(calls) == 1
    assert await worker_db[ANALYSES].count_documents({}) == 0
    assert await worker_db[PROPOSALS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_real_expired_lease_reclaim_fences_old_cycle_release_and_next_run(monkeypatch, worker_db):
    entered, resume = asyncio.Event(), asyncio.Event()
    current, calls = [NOW], []
    async def builder(*args, **kwargs):
        calls.append("analysis-seam")
        if len(calls) == 1:
            entered.set(); await resume.wait()
        return {}
    deps = dependencies(monkeypatch, builder, clock=lambda: current[0])
    old = asyncio.create_task(worker.run_google_automation_cycle(worker_db, **deps))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        old_claim = await worker_db[worker.CYCLES].find_one({"user_id": USER, "account_id": ACCOUNT})
        assert await worker._owns_claim(worker_db, old_claim, NOW) is True
        current[0] = NOW + worker.LEASE_DURATION + timedelta(seconds=1)
        assert await worker._owns_claim(worker_db, old_claim, current[0]) is False
        winner = await asyncio.wait_for(worker.run_google_automation_cycle(worker_db, **deps), timeout=5)
        assert winner["results"][0]["status"] == "analysis_unproved"
        winner_record = await worker_db[worker.CYCLES].find_one({"_id": old_claim["_id"]})
        assert winner_record["claim_token"] != old_claim["claim_token"]
        assert winner_record["state"] == "idle"
        assert aware(winner_record["next_run_at"]) == current[0] + worker.INTERVAL
    finally:
        resume.set()
        old_result = await asyncio.wait_for(old, timeout=5)
    assert old_result["results"][0]["status"] == "claim_lost"
    # The stale finally clause must not overwrite the new owner's release,
    # scheduled time, token or result, even after its own analysis completes.
    assert await worker_db[worker.CYCLES].find_one({"_id": old_claim["_id"]}) == winner_record
    assert len(calls) == 2
    assert await worker_db[ANALYSES].count_documents({}) == 0
    assert await worker_db[PROPOSALS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_real_recovery_release_preserves_future_due_time_with_default_mongo_datetime_codec(monkeypatch, worker_db):
    future = NOW + worker.INTERVAL
    claim = await worker._claim(worker_db, USER, ACCOUNT, NOW)
    await worker_db[worker.CYCLES].update_one({"_id": claim["_id"]},
        {"$set": {"state": "idle", "next_run_at": future}})
    await worker_db[AUTOMATION_POLICIES].update_one({"user_id": USER, "account_id": ACCOUNT}, {"$set": {"enabled": False}})
    await worker_db[PROPOSALS].insert_one({"user_id": USER, "account_id": ACCOUNT,
        "proposal_id": "existing-pending", "status": "uncertain", "submission_started": True,
        "automation_source": {"kind": "standing_policy"}})
    async def forbidden_builder(*args, **kwargs):
        raise AssertionError("Recovery cannot call a model")
    readbacks = []
    async def reconcile(database, **kwargs):
        readbacks.append(kwargs)
        return {"status": "uncertain"}
    deps = dependencies(monkeypatch, forbidden_builder, clock=lambda: NOW + timedelta(minutes=1), reconcile=reconcile)
    monkeypatch.setattr(worker, "execution_enabled", lambda: False)
    result = await worker.run_google_automation_cycle(worker_db, **deps)
    assert result["status"] == "recovery_only"
    assert result["results"][0]["reconciled"] is True
    assert readbacks == [{"user_id": USER, "account_id": ACCOUNT, "proposal_id": "existing-pending"}]
    released = await worker_db[worker.CYCLES].find_one({"_id": claim["_id"]})
    assert released["state"] == "idle" and released["last_status"] == "uncertain"
    assert aware(released["next_run_at"]) == future
    assert await worker_db[ANALYSES].count_documents({}) == 0
