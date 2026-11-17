"""Real Mongo manual-runner fencing; isolated local DB and synthetic sends only."""
import asyncio
from contextlib import suppress
from datetime import timedelta, timezone
import os
from urllib.parse import urlsplit
import uuid

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio

from integrations.qoyod_manual import runner_lease
from integrations.qoyod_manual import send_jobs as jobs
from integrations.qoyod_manual.client import ManualQoyodClient


pytestmark = pytest.mark.asyncio
OWNER = {"id": "runner-test-owner", "role": "owner"}
COLLECTION = "qoyod_manual_send_runner_leases"


@pytest_asyncio.fixture
async def real_dbs(monkeypatch):
    uri = os.environ.get("MEZAN_TEST_MONGO_URI", "")
    if not uri:
        pytest.skip("MEZAN_TEST_MONGO_URI required; never use application Mongo")
    parsed = urlsplit(uri)
    assert parsed.scheme == "mongodb" and parsed.hostname == "127.0.0.1"
    assert parsed.username is None and parsed.password is None
    assert parsed.port and parsed.netloc == f"127.0.0.1:{parsed.port}"
    # Keep test clients pinned to the explicit loopback server, including
    # when CI supplies a replica-set URI. Never discover another host.
    clients = [AsyncIOMotorClient(uri, tz_aware=True, directConnection=True,
               serverSelectionTimeoutMS=3000, connectTimeoutMS=3000) for _ in range(2)]
    name = "qoyod_manual_runner_test_" + uuid.uuid4().hex
    dbs = [client[name] for client in clients]
    monkeypatch.setattr(jobs.governor, "decision", lambda: ("normal", None))
    for key in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(key, raising=False)
    try:
        await asyncio.gather(*(client.admin.command("ping") for client in clients))
        await dbs[0].users.insert_one(dict(OWNER))
        await dbs[0].qoyod_settings.insert_one({
            "user_id": "main", "security_owner_id": OWNER["id"],
        })
        await jobs.ensure_indexes(dbs[0])
        yield dbs
    finally:
        try:
            assert name.startswith("qoyod_manual_runner_test_")
            await clients[0].drop_database(name)
        finally:
            for client in clients:
                client.close()


async def expire_runner(db):
    # Move only this test DB's runner into the past using Mongo's clock;
    # avoid sleeps, wall-clock skew, and replacing the atomic claim itself.
    await db[COLLECTION].update_one({"_id": "main"},
        [{"$set": {"lease_until": {"$subtract": ["$$NOW", 1]}}}])


async def enqueue(db, number):
    response = await jobs.enqueue(db, user=OWNER, order_number=str(number))
    return response["job"]["id"]


async def test_runner_claim_race_across_independent_clients_has_one_winner(real_dbs):
    tokens = [uuid.uuid4().hex for _ in range(24)]
    accepted = await asyncio.gather(*(
        runner_lease.claim(real_dbs[index % 2], token, 90)
        for index, token in enumerate(tokens)
    ))
    assert sum(accepted) == 1
    winner = tokens[accepted.index(True)]
    assert all(await asyncio.gather(*(
        runner_lease.is_current(db, winner) for db in real_dbs
    )))
    assert await real_dbs[0][COLLECTION].count_documents({}) == 1
    assert await runner_lease.claim(real_dbs[1], "later-contender", 90) is False


async def test_runner_uses_server_clock_and_does_not_touch_automatic_slot(real_dbs):
    db, other = real_dbs
    automatic = {"_id": "main", "owner": "existing-automatic-owner", "sentinel": True}
    await db.qoyod_manual_auto_worker_leases.insert_one(dict(automatic))
    # Command replies use the driver's default (naive UTC) codec even when
    # collection reads use tz_aware=True. Compare the same UTC representation.
    before = (await db.command("hello"))["localTime"].replace(tzinfo=timezone.utc)
    assert await runner_lease.claim(db, "manual-owner", 30)
    after = (await db.command("hello"))["localTime"].replace(tzinfo=timezone.utc)
    claimed = await db[COLLECTION].find_one({"_id": "main"})
    assert before <= claimed["acquired_at"] <= after
    assert claimed["lease_until"] - claimed["acquired_at"] == timedelta(seconds=30)
    assert await runner_lease.renew(other, "manual-owner", 90)
    renewed = await db[COLLECTION].find_one({"_id": "main"})
    assert renewed["lease_until"] > claimed["lease_until"]
    assert await runner_lease.renew(other, "wrong-owner", 90) is False
    assert await db[COLLECTION].find_one({"_id": "main"}) == renewed
    await runner_lease.release(other, "manual-owner")
    assert await runner_lease.is_current(db, "manual-owner") is False
    assert await runner_lease.claim(db, "next-owner", 90)
    assert await db.qoyod_manual_auto_worker_leases.find_one({"_id": "main"}) == automatic


async def test_expired_owner_cannot_renew_or_release_replacement(real_dbs):
    db, other = real_dbs
    assert await runner_lease.claim(db, "expired-owner", 90)
    await expire_runner(other)
    assert await runner_lease.is_current(db, "expired-owner") is False
    assert await runner_lease.renew(db, "expired-owner", 90) is False
    assert await runner_lease.claim(other, "replacement", 90)
    await runner_lease.release(db, "expired-owner")
    assert await runner_lease.is_current(other, "replacement")
    assert await runner_lease.renew(db, "expired-owner", 90) is False
    assert await db[COLLECTION].count_documents({}) == 1


async def test_storage_errors_never_grant_ownership_or_release_live_holder(real_dbs):
    db, _ = real_dbs
    assert await runner_lease.claim(db, "live-owner", 90)
    closed = AsyncIOMotorClient(os.environ["MEZAN_TEST_MONGO_URI"], directConnection=True)
    unavailable = closed[db.name]
    closed.close()
    assert await runner_lease.claim(unavailable, "unknown-owner", 90) is False
    assert await runner_lease.renew(unavailable, "live-owner", 90) is False
    assert await runner_lease.is_current(unavailable, "live-owner") is False
    assert await runner_lease.release(unavailable, "live-owner") is None
    assert await runner_lease.is_current(db, "live-owner")


async def test_distinct_queued_orders_execute_one_at_a_time_and_all_progress(real_dbs):
    db, other = real_dbs
    for number in (101, 102, 103):
        await enqueue(db, number)
    entered, proceed = asyncio.Event(), asyncio.Event()
    active = peak = 0
    executed = []

    async def execute(_db, row):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        executed.append(row["order_number"])
        entered.set()
        try:
            await proceed.wait()
            return {"invoice_id": "synthetic-" + row["order_number"]}
        finally:
            active -= 1

    tasks = [asyncio.create_task(jobs.run_once(worker_db, executor=execute)) for worker_db in real_dbs]
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        done, pending = await asyncio.wait(tasks, timeout=3, return_when=asyncio.FIRST_COMPLETED)
        assert len(done) == 1 and len(pending) == 1
        assert next(iter(done)).result() is False
        assert active == peak == 1
        assert await db.qoyod_manual_send_jobs.count_documents({"status": "running"}) == 1
        assert await db.qoyod_manual_send_jobs.count_documents({"status": "queued"}) == 2
    finally:
        proceed.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=3)
    assert sorted(results) == [False, True]
    assert await jobs.run_once(other, executor=execute)
    assert await jobs.run_once(db, executor=execute)
    assert peak == 1 and set(executed) == {"101", "102", "103"} and len(executed) == 3
    assert await db.qoyod_manual_send_jobs.count_documents({"status": "succeeded"}) == 3


async def test_idle_worker_does_not_create_or_update_runner_slot(real_dbs):
    db, other = real_dbs

    async def forbidden(*args):
        pytest.fail("idle worker must not execute a send")

    assert await jobs.run_once(db, executor=forbidden) is False
    assert await db[COLLECTION].count_documents({}) == 0
    assert await runner_lease.claim(other, "existing-owner", 90)
    before = await db[COLLECTION].find_one({"_id": "main"})
    assert await jobs.run_once(db, executor=forbidden) is False
    assert await db[COLLECTION].find_one({"_id": "main"}) == before


@pytest.mark.parametrize("pressure", ["blocked", "cancel"])
async def test_memory_pressure_leaves_job_queued_without_claiming_runner(real_dbs, monkeypatch, pressure):
    db, _ = real_dbs
    job_id = await enqueue(db, 201)
    monkeypatch.setattr(jobs.governor, "decision", lambda: (pressure, None))
    executed = []

    async def execute(_db, row):
        executed.append(row["order_number"])
        return {"invoice_id": "synthetic-pressure-recovered"}

    assert await jobs.run_once(db, executor=execute) is False
    assert executed == []
    assert await db[COLLECTION].count_documents({}) == 0
    row = await db.qoyod_manual_send_jobs.find_one({"_id": job_id})
    assert row["status"] == "queued" and "claim_token" not in row
    monkeypatch.setattr(jobs.governor, "decision", lambda: ("normal", None))
    assert await jobs.run_once(db, executor=execute)
    assert executed == ["201"]
    assert (await jobs.read_job(db, user=OWNER, job_id=job_id))["job"]["status"] == "succeeded"


async def test_runner_loss_fences_next_provider_write_and_stale_release(real_dbs, monkeypatch):
    db, other = real_dbs
    job_id = await enqueue(db, 301)
    requests = []

    async def synthetic_request(_http, method, url, **kwargs):
        requests.append((method, str(url)))
        return httpx.Response(200, json={"id": "synthetic-invoice"})

    monkeypatch.setattr(httpx.AsyncClient, "request", synthetic_request)

    async def execute(_db, _row):
        client = ManualQoyodClient(api_key="synthetic-key", base_url="https://qoyod.invalid/2.0")
        await client._request("POST", "/invoices", json_body={})
        await expire_runner(other)
        assert await runner_lease.claim(other, "replacement", 90)
        await client._request("POST", "/invoice_payments", json_body={})
        return {"invoice_id": "must-not-publish"}

    assert await jobs.run_once(db, executor=execute)
    assert requests == [("POST", "https://qoyod.invalid/2.0/invoices")]
    saved = (await jobs.read_job(db, user=OWNER, job_id=job_id))["job"]
    assert saved["status"] == "needs_review" and "result" not in saved
    assert await runner_lease.is_current(other, "replacement")
    assert await db.qoyod_manual_auto_quarantines.find_one({"order_number": "301"})


async def test_runner_heartbeat_loss_stops_execution_and_preserves_replacement(real_dbs, monkeypatch):
    db, other = real_dbs
    job_id = await enqueue(db, 401)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(jobs, "HEARTBEAT_SECONDS", 0.01)

    async def execute(_db, _row):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(jobs.run_once(db, executor=execute))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        await expire_runner(other)
        assert await runner_lease.claim(other, "replacement", 90)
        assert await asyncio.wait_for(task, timeout=3) is True
        assert not task.cancelled() and cancelled.is_set()
        assert (await jobs.read_job(db, user=OWNER, job_id=job_id))["job"]["status"] == "needs_review"
        assert await runner_lease.is_current(other, "replacement")
    finally:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_delayed_claim_acknowledgement_cannot_start_provider_work(real_dbs, monkeypatch):
    db, other = real_dbs
    job_id = await enqueue(db, 501)
    original_claim = runner_lease.claim
    executed = []

    async def delayed_claim(worker_db, token, lease_seconds):
        accepted = await original_claim(worker_db, token, lease_seconds)
        assert accepted
        # The atomic claim really succeeded, but its acknowledgement reaches
        # the worker only after the lease is no longer current.
        await expire_runner(other)
        return accepted

    async def forbidden_executor(_db, row):
        executed.append(row["order_number"])
        return {"invoice_id": "must-not-execute"}

    monkeypatch.setattr(runner_lease, "claim", delayed_claim)
    assert await jobs.run_once(db, executor=forbidden_executor)
    assert executed == []
    saved = (await jobs.read_job(db, user=OWNER, job_id=job_id))["job"]
    assert saved["status"] == "needs_review" and "result" not in saved
    assert await db.qoyod_manual_auto_quarantines.find_one({"order_number": "501"})
