"""Optional disposable loopback Mongo tests; no provider/model/worker execution.

Only direct shared-cycle CAS, durable counter and token-fenced release execute.
The randomized database name is below MongoDB's 64-byte database-name limit.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlsplit
import uuid

from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio
from tests.test_google_ads_automation_policy_mongo import guard_mongo_contract, set_guard_time

import google_ads_automation_cycles as cycles
from integrations_control_center import google_ads_automation_creation_worker as creation
from integrations_control_center import google_ads_automation_worker as management

NOW = datetime(2026, 10, 26, 9, tzinfo=timezone.utc)
SCOPE = {"user_id": "test-owner", "account_id": "123"}


@pytest_asyncio.fixture
async def creation_cycle_db():
    uri = os.getenv("GOOGLE_ADS_TEST_MONGO_URI")
    if not uri: pytest.skip("Set GOOGLE_ADS_TEST_MONGO_URI to disposable loopback MongoDB")
    parsed = urlsplit(uri)
    if (parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None or parsed.username is not None or parsed.password is not None
        or "," in parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Creation cycle tests require a disposable loopback MongoDB")
    name = "gcreate_cycle_"+uuid.uuid4().hex
    assert len(name.encode()) <= 63
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=1000)
    try:
        await client.admin.command("ping")
        yield client[name]
    finally:
        await client.drop_database(name)
        client.close()


@pytest.mark.asyncio
async def test_real_management_and_creation_share_one_account_mutex(creation_cycle_db):
    db = creation_cycle_db
    assert creation.CYCLES == management.CYCLES
    first = await creation._claim(db, SCOPE, NOW, False)
    assert first is not None
    assert await management._claim(db, SCOPE["user_id"], SCOPE["account_id"], NOW) is None
    await creation._release(db, first, {"status": "completed"}, NOW, False)
    second = await management._claim(db, SCOPE["user_id"], SCOPE["account_id"], NOW)
    assert second is not None and second["_id"] == first["_id"]
    assert await creation._claim(db, SCOPE, NOW, True) is None
    assert await db[creation.CYCLES].count_documents({}) == 1


@pytest.mark.asyncio
async def test_real_stale_release_cannot_overwrite_new_owner_or_management_cadence(creation_cycle_db):
    db = creation_cycle_db
    old = await creation._claim(db, SCOPE, NOW, False)
    due = NOW+timedelta(hours=6)
    await db[creation.CYCLES].update_one({"_id": old["_id"]}, {"$set": {"next_run_at": due}})
    new = await creation._claim(db, SCOPE, NOW+creation.LEASE_DURATION+timedelta(seconds=1), True)
    assert new["claim_token"] != old["claim_token"]
    before = await db[creation.CYCLES].find_one({"_id": old["_id"]})
    await creation._release(db, old, {"status": "failed"}, NOW, False)
    assert await db[creation.CYCLES].find_one({"_id": old["_id"]}) == before
    await creation._release(db, new, {"status": "completed"}, NOW+creation.LEASE_DURATION, True)
    row = await db[creation.CYCLES].find_one({"_id": old["_id"]})
    assert row["next_run_at"].replace(tzinfo=timezone.utc) == due


@pytest.mark.asyncio
async def test_real_atomic_rotation_concurrent_replicas_and_restart(creation_cycle_db):
    db = creation_cycle_db
    priorities = await asyncio.gather(*(cycles._creation_first(db) for _ in range(8)))
    assert priorities.count(True) == priorities.count(False) == 4
    row = await db[cycles.CONTROL].find_one({"_id": cycles.ROTATION_ID})
    assert row["sequence"] == 8 and await db[cycles.CONTROL].count_documents({}) == 1
    # A later invocation shares no in-memory turn counter.
    assert await cycles._creation_first(db) is True
    assert (await db[cycles.CONTROL].find_one({"_id": cycles.ROTATION_ID}))["sequence"] == 9
