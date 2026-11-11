"""Replica-set concurrency checks; isolated local DB and synthetic providers only."""
import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import uuid
from unittest.mock import AsyncMock

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio

import campaign_ai_shadow_jobs as jobs
from snapchat_v2 import cadence
import snapchat_v2.sync_pipeline as snap
from snapchat_v2.lease import ensure_lease_indexes
from tests.test_snapchat_memory_ordering import Connection


@pytest_asyncio.fixture
async def real_db():
    uri = os.environ.get("QOYOD_TEST_MONGO_URI", "")
    if not uri:
        pytest.skip("QOYOD_TEST_MONGO_URI required; never fall back to application Mongo")
    assert uri.startswith("mongodb://127.0.0.1:")
    client = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    assert (await client.admin.command("hello")).get("setName")
    db = client["campaign_jobs_test_" + uuid.uuid4().hex]
    await db.users.insert_one({"id": "owner", "role": "owner", "is_active": True})
    try:
        yield db
    finally:
        assert db.name.startswith("campaign_jobs_test_")
        await client.drop_database(db.name)
        client.close()


@pytest.mark.asyncio
async def test_two_processes_share_one_owner_slot_and_one_global_runner(real_db):
    db = real_db
    responses = await asyncio.gather(*[
        jobs.request_refresh(db, "owner", days=days, kind=kind)
        for days, kind in [(1, "comparison"), (14, "comparison"), (3, "monitor")] * 4
    ])
    assert len({result["job"]["request_id"] for result in responses}) == 1
    assert await db[jobs.COLLECTION].count_documents({"user_id": "owner"}) == 1
    await jobs.request_refresh(db, "other-owner")
    code = """
import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
import campaign_ai_shadow_jobs as jobs
async def main():
    name = os.environ['CAMPAIGN_TEST_DB']
    assert name.startswith('campaign_jobs_test_')
    client = AsyncIOMotorClient(os.environ['QOYOD_TEST_MONGO_URI'], tz_aware=True)
    db = client[name]
    await db.test_workers.insert_one({'pid': os.getpid(), 'ready': True})
    async with asyncio.timeout(10):
        while not await db.test_control.find_one({'_id': 'go'}):
            await asyncio.sleep(.02)
    row = await jobs.claim_next(db)
    await db.test_workers.update_one({'pid': os.getpid()}, {'$set': {'accepted': row is not None}})
    client.close()
asyncio.run(main())
"""
    children = []
    try:
        for _ in range(2):
            children.append(await asyncio.create_subprocess_exec(
                sys.executable, "-c", code, cwd=str(Path(jobs.__file__).parent),
                env={**os.environ, "CAMPAIGN_TEST_DB": db.name},
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            ))
        async with asyncio.timeout(12):
            while await db.test_workers.count_documents({"ready": True}) != 2:
                await asyncio.sleep(.02)
            await db.test_control.insert_one({"_id": "go"})
            for child in children:
                _, error = await child.communicate()
                assert child.returncode == 0, error.decode()
        records = await db.test_workers.find({}).to_list(length=2)
        assert len({record["pid"] for record in records}) == 2
        assert sum(record["accepted"] for record in records) == 1
        assert await db[jobs.COLLECTION].count_documents({"status": "running"}) == 1
    finally:
        for child in children:
            if child.returncode is None:
                child.kill()
            await child.wait()


async def expire(db, row):
    await db[jobs.COLLECTION].update_many({"_id": {"$in": [row["_id"], jobs.RUNNER_ID]}},
        {"$set": {"lease_until": datetime.now(timezone.utc) - timedelta(seconds=1)}})


@pytest.mark.asyncio
async def test_restart_recovers_same_request_and_old_child_cannot_publish(real_db):
    db = real_db
    await jobs.request_refresh(db, "owner")
    old = await jobs.claim_next(db)
    entered, proceed = asyncio.Event(), asyncio.Event()
    async def old_builder(*args, **kwargs):
        entered.set(); await proceed.wait()
        return {"shadow_passed": False, "source": "stale-child"}
    publishing = asyncio.create_task(jobs.execute_claim(db, old["_id"], old["lease_token"], builder=old_builder))
    await asyncio.wait_for(entered.wait(), timeout=3)
    await expire(db, old)
    recovered = await jobs.claim_next(db)
    assert recovered["request_id"] == old["request_id"]
    assert recovered["lease_token"] != old["lease_token"]
    assert recovered["attempts"] == 2
    proceed.set()
    assert await publishing is False
    new_builder = AsyncMock(return_value={"shadow_passed": True, "source": "recovered-child"})
    assert await jobs.execute_claim(db, recovered["_id"], recovered["lease_token"], builder=new_builder)
    cached = await jobs.read_status(db, "owner")
    assert cached["result"] == {"shadow_passed": True, "source": "recovered-child"}
    assert cached["job"]["status"] == "complete"
    assert (await jobs.read_status(db, "owner", days=14))["result"] is None
    assert (await jobs.read_status(db, "other-owner"))["result"] is None


@pytest.mark.asyncio
async def test_timeout_stops_owned_child_and_keeps_both_cached_results(real_db, monkeypatch):
    db = real_db
    now = datetime.now(timezone.utc)
    await db[jobs.COLLECTION].insert_one({
        "_id": jobs._key("owner", 1), "user_id": "owner", "kind": "monitor", "status": "complete",
        "requested_at": now - timedelta(hours=3), "cached_results": {
            "comparison": {"days": 1, "result": {"shadow_passed": True}, "generated_at": now,
                           "expires_at": now - timedelta(hours=1)},
            "monitor": {"days": 3, "result": {"snapshot_id": "saved-advice"}, "generated_at": now,
                        "expires_at": now - timedelta(hours=1)},
        },
    })
    await jobs.request_refresh(db, "owner")
    row = await jobs.claim_next(db)
    children = []
    async def launcher(*args, **kwargs):
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", "while True: sum(range(50000))", **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(jobs, "MAX_JOB_SECONDS", .1)
    monkeypatch.setattr(jobs, "HEARTBEAT_SECONDS", .02)
    await jobs.run_claim(db, row, launcher=launcher)
    assert children[0].returncode is not None
    status = await jobs.read_status(db, "owner")
    assert status["job"]["status"] == "failed"
    assert status["job"]["error_code"] == "analysis_timeout"
    assert status["result"] == {"shadow_passed": True}
    assert status["stale"] is True
    manual = await jobs.read_status(db, "owner", days=3, kind="monitor")
    assert manual["result"] == {"snapshot_id": "saved-advice"}


@pytest.mark.asyncio
async def test_canonical_monitor_snapshot_and_claim_publish_atomically(real_db):
    from campaign_ai_monitor_legacy import RECOMMENDATION_COLLECTION
    db = real_db
    await jobs.request_refresh(db, "owner", days=3, kind="monitor")
    stale = await jobs.claim_next(db)
    await expire(db, stale)
    live = await jobs.claim_next(db)
    with pytest.raises(jobs.ClaimLost):
        await jobs._publish_monitor_snapshot(db, stale, {"user_id": "owner", "snapshot_id": "stale"})
    assert await db[RECOMMENDATION_COLLECTION].count_documents({}) == 0
    assert (await db[jobs.COLLECTION].find_one({"_id": live["_id"]})).get("monitor_snapshot_id") is None
    await jobs._publish_monitor_snapshot(db, live, {"user_id": "owner", "snapshot_id": "live", "recommendations": []})
    stored = await db[RECOMMENDATION_COLLECTION].find_one({"snapshot_id": "live"})
    assert stored["trigger"] == "manual"
    assert stored["next_run_at"] is None
    assert stored["manual_job_request_id"] == live["request_id"]
    # Recovery uses the existing source snapshot, never another billed model call.
    refreshed = await db[jobs.COLLECTION].find_one({"_id": live["_id"]})
    result = await jobs._build_monitor(db, refreshed)
    assert result["snapshot_id"] != "live"
    assert result["manual_job_source_snapshot_id"] == "live"
    assert result["manual_job_status"] == "partial"
    assert "manual_worker_interrupted_after_core_analysis" in result["limitations"]
    assert (await db[RECOMMENDATION_COLLECTION].find_one({"snapshot_id": "live"}))["manual_job_status"] == "building"
    assert (await db[RECOMMENDATION_COLLECTION].find_one({"snapshot_id": result["snapshot_id"]}))["manual_job_status"] == "building"


@pytest.mark.asyncio
async def test_owner_revocation_blocks_queued_analysis_before_heavy_work(real_db):
    db = real_db
    await jobs.request_refresh(db, "owner")
    row = await jobs.claim_next(db)
    await db.users.update_one({"id": "owner"}, {"$set": {"role": "employee"}})
    builder = AsyncMock(side_effect=AssertionError("revoked owner cannot compute"))
    assert not await jobs.execute_claim(db, row["_id"], row["lease_token"], builder=builder)
    builder.assert_not_awaited()
    assert (await jobs.read_status(db, "owner"))["job"]["error_code"] == "owner_access_unavailable"


@pytest.mark.asyncio
async def test_manual_job_reaches_existing_policy_and_publishes_saved_advice(real_db, monkeypatch):
    import campaign_ai_monitor as monitor
    import campaign_ai_policy_v2 as policy
    import campaign_ai_google_periodic as google
    import campaign_ai_tiktok_periodic as tiktok
    db = real_db
    # Only external/evidence boundaries are replaced. The actual monitor,
    # policy, snapshot publisher, queue, transaction and cache all execute.
    for module, name, value in [
        (policy, "_campaign_entities", []), (policy, "_snapchat_child_entities", []),
        (policy._legacy, "_meta_child_entities", []),
        (policy._legacy, "_campaign_history_context", {}),
        (policy, "_experiment_outcomes_context", {}),
        (policy._legacy, "_business_profit_context", {"available": False}),
    ]:
        monkeypatch.setattr(module, name, AsyncMock(return_value=value))
    async def unchanged(_db, _user, document, **kwargs):
        return document
    monkeypatch.setattr(google, "append_google_periodic_recommendations", unchanged)
    monkeypatch.setattr(tiktok, "append_tiktok_periodic_recommendations", unchanged)
    model = AsyncMock(side_effect=AssertionError("empty evidence must not call OpenAI"))
    monkeypatch.setattr(policy, "_ask_openai", model)
    await jobs.request_refresh(db, "owner", days=3, kind="monitor")
    row = await jobs.claim_next(db)
    assert await jobs.execute_claim(db, row["_id"], row["lease_token"])
    saved = await jobs.read_status(db, "owner", days=3, kind="monitor")
    assert saved["job"]["status"] == "complete"
    assert saved["result"]["manual_job_status"] == "complete"
    assert saved["result"]["next_run_at"] is None
    assert "execution_targets" not in saved["result"]
    assert "_id" not in saved["result"]
    stored = await db[monitor.RECOMMENDATION_COLLECTION].find_one({"user_id": "owner"})
    assert stored["snapshot_id"] == saved["result"]["snapshot_id"]
    assert stored["manual_job_request_id"] == row["request_id"]
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_lost_child_after_core_cannot_publish_or_mutate_recovered_visible_advice(real_db, monkeypatch):
    import campaign_ai_monitor as monitor
    import campaign_ai_monitor_legacy as legacy
    import campaign_ai_google_periodic as google
    import campaign_ai_tiktok_periodic as tiktok
    import campaign_ai_public_guard as public
    import campaign_ai_execution_quality_gate as quality
    db = real_db
    now = datetime.now(timezone.utc)
    await db[legacy.RECOMMENDATION_COLLECTION].insert_one({
        "user_id": "owner", "snapshot_id": "previous", "generated_at": (now - timedelta(minutes=1)).isoformat(),
        "summary": "previous verified advice", "recommendation_source": "openai", "recommendations": [],
    })
    await jobs.request_refresh(db, "owner", days=3, kind="monitor")
    old = await jobs.claim_next(db)
    await db[jobs.COLLECTION].update_one({"_id": old["_id"]}, {"$set": {"cached_results.monitor": {
        "days": 3, "result": {"snapshot_id": "previous"}, "generated_at": now,
        "expires_at": now + timedelta(hours=1),
    }}})
    entered, proceed = asyncio.Event(), asyncio.Event()
    async def core(_db, user, **kwargs):
        assert kwargs["refresh_meta"] is False
        document = {"user_id": user, "snapshot_id": "old-hidden-core", "generated_at": now.isoformat(),
            "recommendation_source": "openai", "summary": "core advice", "limitations": [],
            "recommendations": [{"recommendation_id": "candidate", "approval_available": True}],
            "execution_targets": {"candidate": {"provider": "snapchat"}}}
        await kwargs["snapshot_publisher"](document)
        return {key: value for key, value in document.items() if key != "user_id"}
    async def google_after_core(_db, user, document, **kwargs):
        entered.set(); await proceed.wait()
        await _db[legacy.RECOMMENDATION_COLLECTION].update_one(
            {"user_id": user, "snapshot_id": document["snapshot_id"]},
            {"$set": {"google_periodic": {"late_old_child": True}}})
        return {**document, "google_periodic": {"late_old_child": True}}
    async def tiktok_after_loss(_db, user, document, **kwargs):
        await _db[legacy.RECOMMENDATION_COLLECTION].update_one(
            {"user_id": user, "snapshot_id": document["snapshot_id"]},
            {"$set": {"tiktok_periodic": {"late_old_child": True}}})
        return {**document, "tiktok_periodic": {"late_old_child": True}}
    monkeypatch.setattr(monitor, "_base_run_campaign_ai_monitor", core)
    monkeypatch.setattr(google, "append_google_periodic_recommendations", google_after_core)
    monkeypatch.setattr(tiktok, "append_tiktok_periodic_recommendations", tiktok_after_loss)
    app, router = FastAPI(), APIRouter()
    actor = lambda: {"id": "owner", "role": "owner"}
    public.attach_campaign_ai_public_guard(router, db, actor)
    legacy.attach_campaign_ai_routes(router, db, actor, lambda user: user)
    app.include_router(router)
    task = asyncio.create_task(jobs.execute_claim(db, old["_id"], old["lease_token"]))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/ai-monitor/latest")).json()["snapshot_id"] == "previous"
            assert [item["snapshot_id"] for item in (await client.get("/ai-monitor/history")).json()["items"]] == ["previous"]
            denied = await client.post("/ai-monitor/recommendations/candidate/approve", json={"snapshot_id": "old-hidden-core"})
            assert denied.status_code == 409
            assert (await jobs.read_status(db, "owner", days=3, kind="monitor"))["result"]["snapshot_id"] == "previous"
            await expire(db, old)
            recovered = await jobs.claim_next(db)
            assert await jobs.execute_claim(db, recovered["_id"], recovered["lease_token"])
            visible = (await client.get("/ai-monitor/latest")).json()
            assert visible["snapshot_id"] not in {"previous", "old-hidden-core"}
            assert visible["manual_job_status"] == "partial"
            assert visible["manual_job_source_snapshot_id"] == "old-hidden-core"
            # The old child now finishes both appenders and the real wrapper's
            # final metadata write after recovery has already published.
            proceed.set()
            assert await task is False
            assert (await client.get("/ai-monitor/latest")).json() == visible
            hidden = await db[legacy.RECOMMENDATION_COLLECTION].find_one({"snapshot_id": "old-hidden-core"})
            assert hidden["manual_job_status"] == "building"
            assert hidden["google_periodic"]["late_old_child"] is True
            assert hidden["tiktok_periodic"]["late_old_child"] is True
            history = (await client.get("/ai-monitor/history")).json()["items"]
            assert "old-hidden-core" not in {item["snapshot_id"] for item in history}
            assert (await client.post("/ai-monitor/recommendations/candidate/approve",
                json={"snapshot_id": "old-hidden-core"})).status_code == 409
            with pytest.raises(quality.ExecutionQualityBlocked):
                await quality.preflight_approved_execution(db, recommendation_collection=legacy.RECOMMENDATION_COLLECTION,
                    user_id="owner", snapshot_id="old-hidden-core", recommendation_id="candidate")
            cached = await jobs.read_status(db, "owner", days=3, kind="monitor")
            assert cached["result"]["snapshot_id"] == visible["snapshot_id"]
            assert "google_periodic" not in cached["result"]
    finally:
        proceed.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_snapchat_two_workers_and_restart_share_persisted_two_hour_due(real_db, monkeypatch):
    db = real_db
    now = datetime.now(timezone.utc)
    entered, release = asyncio.Event(), asyncio.Event()
    provider_calls = []
    class Governor:
        async def acquire(self, *args, **kwargs):
            return object(), None
        async def release(self, token):
            pass
        def safe_checkpoint(self):
            pass
    class Provider:
        async def fetch_hourly_facts(self, *args, **kwargs):
            provider_calls.append("synthetic-read")
            entered.set(); await release.wait()
            raise RuntimeError("synthetic provider failure")
    monkeypatch.setattr(snap, "governor", Governor())
    monkeypatch.setenv(cadence.INTERVAL_ENV, "300")
    def worker():
        return snap.SnapchatV2SyncPipeline(db, now=lambda: now,
            connection_manager=Connection(), client_factory=lambda *_: Provider())
    first = asyncio.create_task(worker().run("owner", "a", automatic=True))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        competitor = await worker().run("owner", "a", automatic=True)
        assert competitor["reason"] == "lease_unavailable"
    finally:
        release.set()
    assert (await first)["status"] == "failed"
    restarted = await worker().run("owner", "a", automatic=True)
    assert restarted["reason"] == "cadence_gate_2h"
    assert abs(restarted["next_due_at"] - (now + timedelta(hours=2))) <= timedelta(milliseconds=1)
    assert provider_calls == ["synthetic-read"]
    assert await db[cadence.SNAPCHAT_SYNC_RUNS_COLLECTION].count_documents({}) == 1


@pytest.mark.asyncio
async def test_recent_manual_completion_blocks_automatic_provider_work_after_restart(real_db, monkeypatch):
    db = real_db
    now = datetime.now(timezone.utc)
    await ensure_lease_indexes(db)
    await db[cadence.SNAPCHAT_SYNC_RUNS_COLLECTION].insert_one({
        "sync_run_id": "manual", "user_id": "owner", "ad_account_id": "a", "run_type": "manual",
        "status": "complete", "started_at": now - timedelta(minutes=20), "finished_at": now,
    })
    monkeypatch.setattr(snap.governor, "acquire", AsyncMock(return_value=(object(), None)))
    monkeypatch.setattr(snap.governor, "release", AsyncMock())
    def forbidden(*args):
        raise AssertionError("recent manual result must not call provider")
    worker = snap.SnapchatV2SyncPipeline(db, now=lambda: now, connection_manager=Connection(), client_factory=forbidden)
    assert (await worker.run("owner", "a", automatic=True))["reason"] == "cadence_gate_2h"
