"""Owner-only manual reporting job contract, with disposable Mongo and fake provider work."""
import asyncio
from datetime import datetime, timezone
import os
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Header, HTTPException
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio

from integrations_control_center import ads_auto_sync_scheduler as scheduler

PATH = "/api/integrations-v2/ads-auto-sync/snapchat/refresh"
PERIOD = {"date_from": "2026-11-04", "date_to": "2026-11-06"}


@pytest_asyncio.fixture
async def manual_app(monkeypatch):
    port = os.environ.get("ADS_LEASE_TEST_MONGO_PORT")
    if not port:
        pytest.skip("set ADS_LEASE_TEST_MONGO_PORT for disposable loopback Mongo")
    mongo = AsyncIOMotorClient(f"mongodb://127.0.0.1:{int(port)}/?directConnection=true", tz_aware=True)
    name = "synthetic_manual_snap_" + uuid4().hex
    db = mongo[name]
    finished = asyncio.Event()
    calls = []

    async def provider_operation(database, *, user_id, start_date, end_date, now,
                                 manual=False, on_started=None, **kwargs):
        assert manual is True
        run_id = str(uuid4())
        calls.append(run_id)
        await database[scheduler.RUNS_COLLECTION].insert_one({
            "run_id": run_id, "user_id": user_id, "provider": scheduler.SNAPCHAT_PROVIDER_ID,
            "run_type": scheduler.SNAP_RUN_TYPE, "trigger": scheduler.TRIGGER,
            "status": "running", "source_mode": scheduler.ACCOUNT_REFRESH_SOURCE_MODE,
            "started_at": now.isoformat(), "created_at": now.isoformat(),
            "summary": {"date_from": start_date.isoformat(), "date_to": end_date.isoformat()},
            "private_credential_field": "synthetic-value-must-not-be-exposed",
        })
        on_started(run_id)
        await finished.wait()
        await database[scheduler.RUNS_COLLECTION].update_one({"run_id": run_id}, {"$set": {
            "status": "complete", "finished_at": datetime.now(timezone.utc).isoformat(),
        }})
        return {"run_id": run_id, "status": "complete"}

    monkeypatch.setattr(scheduler, "_refresh_snapchat", provider_operation)
    # The job controller's admission boundary is tested elsewhere; host pressure
    # must not make the HTTP/auth/lifecycle contract dependent on this machine.
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def admission(*args, **kwargs):
        yield None
    monkeypatch.setattr(scheduler.governor, "heavy", admission)

    async def user(x_test_user: str = Header("owner-1")):
        return {"id": x_test_user, "role": "owner" if x_test_user.startswith("owner") else "employee"}

    def owner(current):
        if current["role"] != "owner":
            raise HTTPException(status_code=403, detail="owner_required")
        return current

    app = FastAPI()
    router = APIRouter(prefix="/api/integrations-v2")
    scheduler.attach_ads_auto_sync_scheduler(router, db, user, owner)
    app.include_router(router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            yield client, db, finished, calls
    finally:
        finished.set()
        for stop in router.on_shutdown:
            await stop()
        await mongo.drop_database(name)
        mongo.close()


@pytest.mark.asyncio
async def test_manual_job_returns_before_provider_finishes_and_duplicate_clicks_join(manual_app):
    client, db, finished, calls = manual_app
    response = await asyncio.wait_for(client.post(PATH, json=PERIOD), timeout=2)
    assert response.status_code == 202
    first = response.json()
    assert first["run_id"]
    assert len(calls) == 1
    assert not finished.is_set()
    again = await client.post(PATH, json=PERIOD)
    assert again.status_code == 202
    assert again.json()["run_id"] == first["run_id"]
    assert len(calls) == 1
    status = await client.get(f"{PATH}/{first['run_id']}")
    assert status.status_code == 200
    assert status.json()["status"] == "running"
    assert "private_credential_field" not in status.json()
    assert "user_id" not in status.json()
    finished.set()
    for _ in range(20):
        status = await client.get(f"{PATH}/{first['run_id']}")
        if status.json()["status"] == "complete":
            break
        await asyncio.sleep(0.01)
    assert status.json()["status"] == "complete"


@pytest.mark.asyncio
async def test_manual_run_lookup_is_owner_scoped_and_employee_cannot_start(manual_app):
    client, db, finished, calls = manual_app
    rejected = await client.post(PATH, json=PERIOD, headers={"X-Test-User": "employee-1"})
    assert rejected.status_code == 403
    assert calls == []
    accepted = await client.post(PATH, json=PERIOD)
    assert accepted.status_code == 202
    foreign = await client.get(f"{PATH}/{accepted.json()['run_id']}", headers={"X-Test-User": "owner-2"})
    assert foreign.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("period", [
    {"date_from": "invalid", "date_to": "2026-11-06"},
    {"date_from": "2026-11-06", "date_to": "2026-11-04"},
])
async def test_invalid_period_never_dispatches_provider_work(manual_app, period):
    client, db, finished, calls = manual_app
    response = await client.post(PATH, json=period)
    assert response.status_code == 422
    assert calls == []


@pytest.mark.asyncio
async def test_real_canonical_manual_refresh_bypasses_only_cadence_and_retains_distributed_exclusion(monkeypatch):
    from datetime import date, timedelta
    port = os.environ.get("ADS_LEASE_TEST_MONGO_PORT")
    if not port:
        pytest.skip("set ADS_LEASE_TEST_MONGO_PORT for disposable loopback Mongo")
    client = AsyncIOMotorClient(f"mongodb://127.0.0.1:{int(port)}/?directConnection=true", tz_aware=True)
    name = "synthetic_manual_canonical_" + uuid4().hex
    db = client[name]
    current = datetime.now(timezone.utc)
    entered = asyncio.Event()
    calls = []
    registered = []
    owner = "synthetic-owner"

    async def selected(*args, **kwargs):
        return [{"ad_account_id": "synthetic-account", "currency": "SAR", "timezone": "Asia/Riyadh"}]

    async def access_token(*args, **kwargs):
        return "synthetic-token"

    async def provider_read(*args, **kwargs):
        calls.append(1)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(scheduler, "snapchat_oauth_configured", lambda: True)
    monkeypatch.setattr(scheduler, "snapchat_native_sync_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_load_canonical_scheduler_accounts", selected)
    monkeypatch.setattr(scheduler.SnapchatSyncContext, "access_token", access_token)
    monkeypatch.setattr(scheduler.snapchat_hourly, "refresh_snapchat_account_hours", provider_read)
    task = None
    try:
        await db[scheduler.RUNS_COLLECTION].insert_one({
            "user_id": owner, "provider": scheduler.SNAPCHAT_PROVIDER_ID,
            "run_type": scheduler.SNAP_RUN_TYPE, "status": "complete",
            "started_at": (current - timedelta(minutes=40)).isoformat(),
            "finished_at": (current - timedelta(minutes=30)).isoformat(),
        })
        options = {"user_id": owner, "start_date": date(2026, 11, 4),
                   "end_date": date(2026, 11, 6), "now": current}
        automatic = await scheduler._refresh_snapchat(db, **options)
        assert automatic["reason"] == "cadence_gate_2h"
        assert calls == []
        task = asyncio.create_task(scheduler._refresh_snapchat(db, **options,
            manual=True, on_started=registered.append))
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert len(registered) == 1
        duplicate = await scheduler._refresh_snapchat(db, **options, manual=True)
        assert duplicate["reason"] == "running_elsewhere"
        assert calls == [1]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        run = await db[scheduler.RUNS_COLLECTION].find_one({"user_id": owner, "run_id": registered[0]})
        assert run["requested_by"] == "owner_manual"
        assert run["status"] == "failed"
        assert run["error"]["code"] == "snapchat_sync_cancelled"
        # Cancellation releases only this owned lease, allowing a later explicit retry.
        successor = await scheduler.acquire_snapchat_lease(db, user_id=owner, now=scheduler._utcnow())
        assert successor is not None
        await scheduler.release_snapchat_lease(db, successor, final_status="failed", now=scheduler._utcnow())
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await client.drop_database(name)
        client.close()
