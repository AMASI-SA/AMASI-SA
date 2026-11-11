"""Owner-only spend observations with real transport parsing and disposable Mongo."""
import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import os
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Header, HTTPException
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio

from integrations_control_center import ads_auto_sync_scheduler as scheduler
from integrations_control_center import snapchat_manual_spend_refresh as routes
from snapchat_v2 import manual_spend as spend
from snapchat_v2.accounts import SNAPCHAT_ACCOUNTS_COLLECTION
from snapchat_v2.client import SnapchatV2Client

PATH = "/api/integrations-v2/ads-auto-sync/snapchat/refresh"
PERIOD = {"date_from": "2026-11-04", "date_to": "2026-11-06"}
NOW = datetime(2026, 11, 11, 17, 20, tzinfo=timezone.utc)


def _mongo_uri():
    port = os.environ.get("ADS_LEASE_TEST_MONGO_PORT")
    uri = f"mongodb://127.0.0.1:{int(port)}/?directConnection=true" if port else os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("disposable loopback Mongo is required")
    assert urlparse(uri).hostname in {"127.0.0.1", "localhost", "::1"}
    return uri


class Tokens:
    async def get_access_token(self, user_id, **kwargs):
        return "synthetic-token"


def _payload(request, *, micro=1_000_000):
    start = datetime.fromisoformat(request.url.params["start_time"])
    end = datetime.fromisoformat(request.url.params["end_time"])
    account_id = request.url.path.split("/")[-2]
    hours = int((end - start).total_seconds() // 3600)
    return {"request_status": "SUCCESS", "timeseries_stats": [{"sub_request_status": "SUCCESS",
        "timeseries_stat": {"id": account_id, "type": "AD_ACCOUNT", "granularity": "HOUR",
            "start_time": start.isoformat(), "end_time": end.isoformat(),
            "timeseries": [{"start_time": (start + timedelta(hours=index)).isoformat(),
                "end_time": (start + timedelta(hours=index + 1)).isoformat(),
                "stats": {"spend": micro}} for index in range(hours)]}}]}


def _factory(transport):
    def factory(db, user_id, *, now):
        return SnapchatV2Client(db, user_id, now=now, token_store=Tokens(),
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(transport), **kwargs))
    return factory


async def _seed_accounts(db):
    for account_id, currency, zone, selected in [
        ("a-usd", "USD", "America/Los_Angeles", True),
        ("b-sar", "SAR", "Asia/Riyadh", False),
    ]:
        row = {"user_id": "owner-1", "provider": "snapchat_ads", "ad_account_id": account_id,
            "external_account_id": account_id, "currency": currency, "timezone": zone,
            "connection_status": "connected", "active": True, "selected": selected,
            "mezan_selected": True, "access_token": "synthetic-private-must-not-leak"}
        await db[SNAPCHAT_ACCOUNTS_COLLECTION].insert_one(deepcopy(row))
        await db["mezan_integration_accounts_v2"].insert_one(deepcopy(row))


@pytest_asyncio.fixture
async def manual_app(monkeypatch):
    mongo = AsyncIOMotorClient(_mongo_uri(), tz_aware=True)
    name = "synthetic_manual_snap_" + uuid4().hex
    db = mongo[name]
    finished = asyncio.Event()
    entered = asyncio.Event()
    calls = []
    await _seed_accounts(db)

    async def transport(request):
        assert request.method == "GET"
        assert request.url.path in {"/v1/adaccounts/a-usd/stats", "/v1/adaccounts/b-sar/stats"}
        assert request.url.params["fields"] == "spend"
        assert "breakdown" not in request.url.params
        calls.append(request.url.path)
        entered.set()
        await finished.wait()
        return httpx.Response(200, json=_payload(request))

    def forbidden(*args, **kwargs):
        pytest.fail("Manual spend must never enter the legacy full reporting writer")
    monkeypatch.setattr(scheduler, "_refresh_snapchat", forbidden)
    jobs = routes.ManualSpendJobs(db, now=lambda: NOW, client_factory=_factory(transport))
    monkeypatch.setattr(routes, "ManualSpendJobs", lambda database: jobs)
    monkeypatch.setattr(spend, "utcnow", lambda: NOW)

    async def user(x_test_user: str = Header("owner-1")):
        return {"id": x_test_user, "role": "owner" if x_test_user.startswith("owner") else "employee"}

    def owner(current):
        if current["role"] != "owner":
            raise HTTPException(status_code=403, detail="owner_required")
        return current

    app = FastAPI()
    router = APIRouter(prefix="/api/integrations-v2")
    routes.attach_manual_spend_routes(router, db, user, owner)
    app.include_router(router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            yield client, db, finished, entered, calls, jobs
    finally:
        finished.set()
        for stop in router.on_shutdown:
            await stop()
        await mongo.drop_database(name)
        mongo.close()


@pytest.mark.asyncio
async def test_manual_job_returns_before_provider_finishes_and_duplicate_clicks_join(manual_app):
    client, db, finished, entered, calls, jobs = manual_app
    response = await asyncio.wait_for(client.post(PATH, json=PERIOD), timeout=2)
    assert response.status_code == 202
    first = response.json()
    assert first["run_id"]
    assert first["scope"] == "snapchat_spend_only"
    assert first["requested_account_ids"] == ["a-usd", "b-sar"]
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert len(calls) == 1
    assert not finished.is_set()
    again = await client.post(PATH, json=PERIOD)
    assert again.status_code == 202
    assert again.json()["run_id"] == first["run_id"]
    assert len(calls) == 1
    status = await client.get(f"{PATH}/{first['run_id']}")
    assert status.status_code == 200
    assert status.json()["status"] == "running"
    assert "synthetic-private-must-not-leak" not in repr(status.json())
    assert "user_id" not in status.json()
    finished.set()
    for _ in range(100):
        status = await client.get(f"{PATH}/{first['run_id']}")
        if status.json()["status"] == "complete":
            break
        await asyncio.sleep(0.01)
    assert status.json()["status"] == "complete"
    assert status.json()["refreshed_count"] == 2 and status.json()["failed_count"] == 0
    assert len(calls) == 2
    assert all(row["snapshot_job_id"] == first["run_id"] for row in status.json()["account_results"])
    assert await db[scheduler.RUNS_COLLECTION].count_documents({}) == 0
    assert await db[spend.COLLECTION].count_documents({}) == 2


@pytest.mark.asyncio
async def test_manual_run_lookup_is_owner_scoped_and_employee_cannot_start(manual_app):
    client, db, finished, entered, calls, jobs = manual_app
    rejected = await client.post(PATH, json=PERIOD, headers={"X-Test-User": "employee-1"})
    assert rejected.status_code == 403
    assert calls == []
    accepted = await client.post(PATH, json=PERIOD)
    assert accepted.status_code == 202
    foreign = await client.get(f"{PATH}/{accepted.json()['run_id']}", headers={"X-Test-User": "owner-2"})
    assert foreign.status_code == 404
    employee = await client.get(f"{PATH}/{accepted.json()['run_id']}", headers={"X-Test-User": "employee-1"})
    assert employee.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("period", [
    {"date_from": "invalid", "date_to": "2026-11-06"},
    {"date_from": "2026-11-06", "date_to": "2026-11-04"},
    {"date_from": "2026-01-01", "date_to": "2026-11-04"},
    {"date_from": "2026-11-12", "date_to": "2026-11-12"},
])
async def test_invalid_period_never_dispatches_provider_work(manual_app, period):
    client, db, finished, entered, calls, jobs = manual_app
    response = await client.post(PATH, json=period)
    assert response.status_code == 422
    assert calls == []


@pytest.mark.asyncio
async def test_incompatible_period_and_changed_account_scope_cannot_join(manual_app):
    client, db, finished, entered, calls, jobs = manual_app
    accepted = await client.post(PATH, json=PERIOD)
    assert accepted.status_code == 202
    await asyncio.wait_for(entered.wait(), timeout=2)
    mismatch = await client.post(PATH, json={"date_from": "2026-11-05", "date_to": "2026-11-06"})
    assert mismatch.status_code == 409
    await db["mezan_integration_accounts_v2"].update_one({"ad_account_id": "b-sar"}, {"$set": {"mezan_selected": False}})
    mismatch = await client.post(PATH, json=PERIOD)
    assert mismatch.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("days,requests", [(1, 1), (7, 1), (8, 2), (93, 14)])
async def test_spend_only_transport_splits_exact_riyadh_period(days, requests):
    calls = []
    async def transport(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/v1/adaccounts/a-usd/stats"
        assert set(request.url.params) == {"granularity", "fields", "omit_empty", "start_time", "end_time"}
        assert request.url.params["fields"] == "spend"
        start, end = (datetime.fromisoformat(request.url.params[key]) for key in ("start_time", "end_time"))
        assert end - start <= timedelta(days=7)
        return httpx.Response(200, json=_payload(request, micro=0))
    last = NOW.date() - timedelta(days=1)
    first = last - timedelta(days=days - 1)
    snapshot = await spend.fetch_manual_spend(None, user_id="owner-1",
        account={"ad_account_id": "a-usd", "currency": "USD", "timezone": "America/Los_Angeles"},
        date_from=first, date_to=last, job_id="synthetic", requested_at=NOW, now=lambda: NOW,
        client_factory=_factory(transport))
    assert len(calls) == requests
    assert len(snapshot["hours"]) == days * 24
    assert all(row["spend_native"] == 0 for row in snapshot["hours"])


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["empty", "missing_spend", "null", "bool", "nan", "negative", "wrong_account", "duplicate_hour", "pagination", "missing_status", "wrong_window"])
async def test_incomplete_provider_evidence_never_becomes_zero(invalid):
    async def transport(request):
        payload = _payload(request)
        stat = payload["timeseries_stats"][0]["timeseries_stat"]
        if invalid == "empty": stat["timeseries"] = []
        elif invalid == "missing_spend": stat["timeseries"][0]["stats"] = {}
        elif invalid in {"null", "bool", "nan", "negative"}:
            stat["timeseries"][0]["stats"]["spend"] = {"null": None, "bool": False, "nan": "NaN", "negative": -1}[invalid]
        elif invalid == "wrong_account": stat["id"] = "foreign-account"
        elif invalid == "duplicate_hour": stat["timeseries"][1] = deepcopy(stat["timeseries"][0])
        elif invalid == "pagination": payload["paging"] = {"next_link": "https://adsapi.snapchat.com/v1/next"}
        elif invalid == "missing_status": payload.pop("request_status")
        elif invalid == "wrong_window": stat["start_time"] = NOW.isoformat()
        return httpx.Response(200, json=payload)
    with pytest.raises(ValueError):
        await spend.fetch_manual_spend(None, user_id="owner-1",
            account={"ad_account_id": "a-usd", "currency": "USD", "timezone": "America/Los_Angeles"},
            date_from=NOW.date(), date_to=NOW.date(), job_id="synthetic", requested_at=NOW,
            now=lambda: NOW, client_factory=_factory(transport))


@pytest.mark.asyncio
async def test_unsupported_account_hour_has_no_campaign_fallback():
    calls = []
    async def transport(request):
        calls.append(request)
        assert "breakdown" not in request.url.params
        return httpx.Response(400, json={})
    with pytest.raises(spend.SnapchatClientError) as exc:
        await spend.fetch_manual_spend(None, user_id="owner-1",
            account={"ad_account_id": "a-usd", "currency": "USD", "timezone": "America/Los_Angeles"},
            date_from=NOW.date(), date_to=NOW.date(), job_id="synthetic", requested_at=NOW,
            now=lambda: NOW, client_factory=_factory(transport))
    assert exc.value.code == "unsupported_account_hour"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pressure", [False, True])
async def test_real_canonical_manual_refresh_bypasses_only_cadence_and_retains_distributed_exclusion(monkeypatch, pressure):
    from contextlib import asynccontextmanager
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
    admission_waiting = asyncio.Event()
    allow_admission = asyncio.Event()
    owner = "synthetic-owner"

    async def selected(*args, **kwargs):
        return [{"ad_account_id": "synthetic-account", "currency": "SAR", "timezone": "Asia/Riyadh"}]

    async def access_token(*args, **kwargs):
        return "synthetic-token"

    async def provider_read(*args, **kwargs):
        calls.append(1)
        entered.set()
        await asyncio.Event().wait()

    @asynccontextmanager
    async def admission(*args, **kwargs):
        admission_waiting.set()
        await allow_admission.wait()
        if pressure:
            raise scheduler.ResourcePressure("resource_pressure")
        yield None

    async def unexpected_unhealthy(*args, **kwargs):
        pytest.fail("Resource admission/cancellation must not invalidate provider connection health")

    monkeypatch.setattr(scheduler, "snapchat_oauth_configured", lambda: True)
    monkeypatch.setattr(scheduler, "snapchat_native_sync_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_load_canonical_scheduler_accounts", selected)
    monkeypatch.setattr(scheduler.SnapchatSyncContext, "access_token", access_token)
    monkeypatch.setattr(scheduler.snapchat_hourly, "refresh_snapchat_account_hours", provider_read)
    monkeypatch.setattr(scheduler.governor, "heavy", admission)
    monkeypatch.setattr(scheduler, "_mark_snapchat_sync_unhealthy", unexpected_unhealthy)
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
        await asyncio.wait_for(admission_waiting.wait(), timeout=10)
        assert len(registered) == 1
        assert calls == []  # HTTP can acknowledge the durable run while capacity is busy.
        allow_admission.set()
        if pressure:
            result = await task
            assert result["code"] == "resource_pressure"
            assert calls == []
        else:
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
        assert run["error"]["code"] == ("resource_pressure" if pressure else "snapchat_sync_cancelled")
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
