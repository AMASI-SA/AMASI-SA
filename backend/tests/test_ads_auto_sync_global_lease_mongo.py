"""Integration contract for the native ad reporting lease, using disposable Mongo only."""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from integrations_control_center import ads_auto_sync_scheduler as scheduler


@pytest.mark.asyncio
async def test_contended_first_claim_renewal_and_takeover_use_mongo_time(monkeypatch):
    port = os.environ.get("ADS_LEASE_TEST_MONGO_PORT")
    if not port:
        pytest.skip("set ADS_LEASE_TEST_MONGO_PORT for a loopback test server")
    uri = f"mongodb://127.0.0.1:{int(port)}/?directConnection=true"
    clients = [AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True) for _ in range(2)]
    name = "synthetic_ads_lease_" + uuid4().hex
    db = [client[name] for client in clients]
    tokens = ["claim-a", "claim-b"]
    collection = db[0][scheduler.SCHEDULER_COLLECTION]
    record = {"_id": scheduler.SCHEDULER_ID}
    try:
        claims = await asyncio.gather(*(
            scheduler._acquire_lease(database, worker_id=token) for database, token in zip(db, tokens)
        ))
        assert sorted(claims) == [False, True]
        i = claims.index(True)
        j = 1 - i
        original = await collection.find_one(record)
        assert original["lease_owner"] == tokens[i]
        assert original["lease_expires_at"] - original["last_started_at"] == scheduler.LEASE_TTL

        # A fast client cannot steal a valid lease even if its next run is already due.
        future = datetime.now(timezone.utc) + timedelta(days=2)
        monkeypatch.setattr(scheduler, "_utcnow", lambda: future)
        await collection.update_one(record, [{"$set": {"next_due_at": "$$NOW",
            "lease_expires_at": {"$add": ["$$NOW", 60000]}}}])
        shortened = await collection.find_one(record)
        assert not await scheduler._acquire_lease(db[j], worker_id=tokens[j])
        assert await collection.find_one(record) == shortened
        await scheduler._renew_lease(db[i], tokens[i])
        renewed = await collection.find_one(record)
        assert renewed["lease_owner"] == tokens[i]
        assert renewed["lease_expires_at"] > shortened["lease_expires_at"]
        assert renewed["lease_expires_at"] - renewed["updated_at"] == scheduler.LEASE_TTL

        # A slow client can acquire at actual database expiry; the former owner cannot revive or release it.
        past = datetime.now(timezone.utc) - timedelta(days=2)
        monkeypatch.setattr(scheduler, "_utcnow", lambda: past)
        await collection.update_one(record, [{"$set": {"lease_expires_at": {"$subtract": ["$$NOW", 1]}}}])
        with pytest.raises(scheduler._SchedulerLeaseUnconfirmed):
            await scheduler._renew_lease(db[i], tokens[i])
        assert await scheduler._acquire_lease(db[j], worker_id=tokens[j])
        successor = await collection.find_one(record)
        with pytest.raises(scheduler._SchedulerLeaseUnconfirmed):
            await scheduler._release_lease(db[i], worker_id=tokens[i], result={"status": "complete"})
        assert await collection.find_one(record) == successor
        await scheduler._release_lease(db[j], worker_id=tokens[j], result={"status": "complete"})
        assert (await collection.find_one(record))["status"] == "complete"

        # Legacy empty leases are valid, but a corrupted deadline is never treated as a past date.
        for invalid in ({"lease_expires_at": "2020-01-01"}, {"next_due_at": "2020-01-01"},
                        {"next_due_at": None}):
            row = {**record, **invalid}
            await collection.replace_one(record, row)
            assert not await scheduler._acquire_lease(db[i], worker_id="invalid-deadline")
            assert await collection.find_one(record) == row
        await collection.replace_one(record, {**record, "lease_expires_at": None})
        assert await scheduler._acquire_lease(db[i], worker_id="valid-empty")
    finally:
        try:
            await clients[0].drop_database(name)
        finally:
            for client in clients:
                client.close()
