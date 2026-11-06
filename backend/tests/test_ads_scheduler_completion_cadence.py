"""Overrunning background cycles must leave a real cooldown across replicas."""
from datetime import timedelta
import os
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from integrations_control_center import ads_auto_sync_scheduler as scheduler


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["complete", "partial", "error"])
async def test_overrun_completion_leaves_server_timed_cooldown_for_other_replica(outcome, monkeypatch):
    port = os.environ.get("ADS_LEASE_TEST_MONGO_PORT")
    if not port:
        pytest.skip("set ADS_LEASE_TEST_MONGO_PORT for a disposable loopback Mongo")
    clients = [AsyncIOMotorClient(f"mongodb://127.0.0.1:{int(port)}/?directConnection=true",
                                 serverSelectionTimeoutMS=5000, tz_aware=True) for _ in range(2)]
    name = "synthetic_ads_cooldown_" + uuid4().hex
    db = [client[name] for client in clients]
    collection = db[0][scheduler.SCHEDULER_COLLECTION]
    record = {"_id": scheduler.SCHEDULER_ID}
    try:
        assert await scheduler._acquire_lease(db[0], worker_id="long-cycle")
        # Simulate the observed 20-minute cycle without waiting or calling providers.
        await collection.update_one(record, [{"$set": {
            "last_started_at": {"$subtract": ["$$NOW", 20 * 60_000]},
            "next_due_at": {"$subtract": ["$$NOW", 15 * 60_000]},
        }}])
        before = await collection.find_one(record)
        # Worker clock skew must not control the shared release or due time.
        monkeypatch.setattr(scheduler, "_utcnow", lambda: before["last_started_at"] + timedelta(days=2))
        result = {"status": outcome, "results": [{"provider": "snapchat_ads", "status": "partial"}]}
        if outcome == "error":
            await scheduler._release_lease(db[0], worker_id="long-cycle", error=RuntimeError("synthetic failure"))
        else:
            await scheduler._release_lease(db[0], worker_id="long-cycle", result=result)
        completed = await collection.find_one(record)
        assert completed["last_finished_at"] - before["last_started_at"] >= timedelta(minutes=20)
        assert completed["status"] == ("failed" if outcome == "error" else outcome)
        if outcome != "error":
            assert completed["last_result"] == result
        assert not await scheduler._acquire_lease(db[1], worker_id="next-replica"), (
            "An overrun must not allow another replica to start again immediately after completion"
        )
        assert completed["next_due_at"] - completed["last_finished_at"] == timedelta(seconds=scheduler.interval_seconds())
        assert await collection.find_one(record) == completed

        # Work can resume after the cooldown, with the ordinary ownership fence intact.
        await collection.update_one(record, [{"$set": {"next_due_at": {"$subtract": ["$$NOW", 1]}}}])
        assert await scheduler._acquire_lease(db[1], worker_id="next-replica")
        successor = await collection.find_one(record)
        with pytest.raises(scheduler._SchedulerLeaseUnconfirmed):
            await scheduler._release_lease(db[0], worker_id="long-cycle", result={"status": "complete"})
        assert await collection.find_one(record) == successor
    finally:
        try:
            await clients[0].drop_database(name)
        finally:
            for client in clients:
                client.close()
