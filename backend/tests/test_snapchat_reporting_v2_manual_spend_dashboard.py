"""A spend-only click must change the actual dashboard without publishing finance.

The job controller, account authorization, HTTP parser, snapshot publication,
unified reader, portfolio and dashboard route are real. Only the provider/token
boundary and clock are synthetic. CI's disposable Mongo service also exercises
the job's cross-instance ownership; mongomock permits the parsing/read contract
to run without a local Mongo process.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import os
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
import httpx
import mongomock
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio

from ads_manager.account_cost_settings import COLLECTION as COST_SETTINGS
from integrations_control_center import dashboard_ads_platform_spend_routes as dashboard_routes
from integrations_control_center import snapchat_manual_spend_refresh as jobs_module
from integrations_control_center.ad_daily_close_proof import close_proof
from snapchat_v2.accounts import SNAPCHAT_ACCOUNTS_COLLECTION
from snapchat_v2.client import SnapchatV2Client
from snapchat_v2.projections import SNAPCHAT_DAILY_PROJECTIONS_COLLECTION as PROJECTIONS
from snapchat_v2.reconciliation import SNAPCHAT_RECONCILIATION_COLLECTION as RECONCILIATIONS
from snapchat_v2.sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION as SYNC_RUNS
from unified_marketing.readers import snapchat_v2 as reader


OWNER = "synthetic-spend-owner"
PROVIDER = "snapchat_ads"
DAY = date(2026, 11, 11)
NOW = datetime(2026, 11, 11, 17, 20, tzinfo=timezone.utc)
RIYADH = ZoneInfo("Asia/Riyadh")
ACCOUNT_SPECS = {
    "usd-account": ("USD", "America/Los_Angeles", 3.75, 2),
    "sar-account": ("SAR", "Asia/Riyadh", 1, 1),
}
PROTECTED_COLLECTIONS = (
    PROJECTIONS, RECONCILIATIONS, SYNC_RUNS, SNAPCHAT_ACCOUNTS_COLLECTION,
    "mezan_snapchat_hourly_facts_v2", "mezan_snapchat_daily_total_facts_v2",
    "mezan_snapchat_entity_facts_v2", "mezan_snapchat_entities_v2",
    "mezan_snapchat_performance_daily_v2", "mezan_integration_accounts_v2",
    "ad_account_ledger", "general_ledger",
)


class Clock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value


class Cursor:
    def __init__(self, raw):
        self.raw = raw

    def sort(self, *args):
        self.raw = self.raw.sort(*args)
        return self

    def limit(self, count):
        self.raw = self.raw.limit(count)
        return self

    async def to_list(self, length=None):
        rows = list(self.raw)
        return rows if length is None else rows[:length]


class Collection:
    """Motor-shaped adapter; Mongo itself is used for concurrency assertions."""
    def __init__(self, raw):
        self.raw = raw

    def find(self, *args, **kwargs):
        return Cursor(self.raw.find(*args, **kwargs))

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            return getattr(self.raw, name)(*args, **kwargs)
        return call


class Database:
    def __init__(self):
        self.raw = mongomock.MongoClient(tz_aware=True).db

    def __getitem__(self, name):
        return Collection(self.raw[name])

    def __getattr__(self, name):
        return self[name]


class Tokens:
    async def get_access_token(self, user_id, **_kwargs):
        assert user_id == OWNER
        return "synthetic-spend-token"


class SpendTransport:
    def __init__(self, clock):
        self.clock = clock
        self.amounts = {"usd-account": 12, "sar-account": 8}
        self.modes = {}
        self.calls = []
        self.violations = []
        self.entered = asyncio.Event()
        self.blocked = asyncio.Event()
        self.blocked_account = None
        self.release = asyncio.Event()
        self.release.set()

    def require(self, condition, message):
        if not condition:
            self.violations.append(message)
            raise AssertionError(message)

    async def handle(self, request):
        parts = request.url.path.strip("/").split("/")
        self.require(request.method == "GET" and request.url.host == "adsapi.snapchat.com",
                     "only read-only Snapchat stats requests are allowed")
        self.require(len(parts) == 4 and parts[:2] == ["v1", "adaccounts"]
                     and parts[-1] == "stats" and parts[2] in ACCOUNT_SPECS,
                     "catalogue, campaign, child and unauthorized-account requests are forbidden")
        account_id = parts[2]
        query = dict(request.url.params)
        self.require(query.get("fields") == "spend" and query.get("granularity") == "HOUR"
                     and "breakdown" not in query,
                     "manual refresh must request account HOUR spend only, without breakdown")
        start = datetime.fromisoformat(query["start_time"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(query["end_time"].replace("Z", "+00:00"))
        self.require(start.utcoffset() is not None and end.utcoffset() is not None
                     and start < end and end - start <= timedelta(days=7),
                     "account HOUR request must be aware and bounded to seven days")
        self.require(start.minute == end.minute == start.second == end.second == 0,
                     "provider windows must align to whole hours")
        self.calls.append({"account_id": account_id, "start": start.astimezone(timezone.utc),
                           "end": end.astimezone(timezone.utc), "query": query})
        self.entered.set()
        if not self.release.is_set() and self.blocked_account in {None, account_id}:
            self.blocked.set()
            await self.release.wait()
        mode = self.modes.get(account_id)
        if mode in {"forbidden", "unsupported"}:
            return httpx.Response(403 if mode == "forbidden" else 400, json={"request_status": "ERROR"})
        points = []
        hour = start.astimezone(timezone.utc)
        while hour < end:
            # Independent oracle: each Riyadh day has the chosen native spend
            # in its first hour; every other requested hour is explicit zero.
            amount = 0 if mode == "zero" else self.amounts[account_id]
            value = amount * 1_000_000 if hour.astimezone(RIYADH).hour == 0 else 0
            points.append({"start_time": hour.isoformat(),
                           "end_time": (hour + timedelta(hours=1)).isoformat(),
                           "stats": {"spend": value}})
            hour += timedelta(hours=1)
        if mode == "missing_hour":
            points.pop()
        elif mode == "missing_spend":
            points[0]["stats"] = {}
        return httpx.Response(200, json={"request_status": "SUCCESS", "paging": {},
            "timeseries_stats": [{"sub_request_status": "SUCCESS", "timeseries_stat": {
                "id": account_id, "type": "AD_ACCOUNT", "granularity": "HOUR",
                "start_time": start.isoformat(), "end_time": end.isoformat(), "timeseries": points,
            }}]})

    def client(self, db, user_id, **_kwargs):
        return SnapchatV2Client(db, user_id, token_store=Tokens(), now=self.clock,
            client_factory=lambda **options: httpx.AsyncClient(
                transport=httpx.MockTransport(self.handle), **options))


@pytest_asyncio.fixture
async def storage():
    port = os.environ.get("ADS_LEASE_TEST_MONGO_PORT")
    if not port and os.environ.get("SNAP_PERIOD_TEST_MONGO") == "1":
        port = "27017"
    if not port:
        yield SimpleNamespace(db=Database(), real_mongo=False)
        return
    # An environment flag selects a loopback port, never an arbitrary URI.
    client = AsyncIOMotorClient(f"mongodb://127.0.0.1:{int(port)}/?directConnection=true",
                               tz_aware=True, serverSelectionTimeoutMS=5000)
    name = "synthetic_manual_spend_" + uuid4().hex
    await client.admin.command("ping")
    try:
        yield SimpleNamespace(db=client[name], real_mongo=True)
    finally:
        await client.drop_database(name)
        client.close()


async def seed_account(db, account_id, *, owner=OWNER, enabled=True, selected=False):
    currency, zone, rate, fee = ACCOUNT_SPECS.get(account_id, ("SAR", "Asia/Riyadh", 1, 0))
    account = {"user_id": owner, "provider": PROVIDER, "ad_account_id": account_id,
        "external_account_id": account_id, "currency": currency, "timezone": zone,
        "display_name": account_id, "connection_status": "connected",
        "connection_provenance": "api_connection", "mezan_selected": enabled,
        "mezan_integration_account_id": "integration-" + account_id}
    await db.mezan_integration_accounts_v2.insert_one(deepcopy(account))
    await db[SNAPCHAT_ACCOUNTS_COLLECTION].insert_one({**account, "active": True, "selected": selected})
    await db[COST_SETTINGS].insert_one({"user_id": owner, "provider": PROVIDER,
        "external_account_id": account_id, "mezan_integration_account_id": "integration-" + account_id,
        "native_currency": currency, "exchange_rate_to_sar": rate,
        "bank_commission_pct": fee, "apply_bank_commission": True})
    return account


async def seed_canonical(db, account_id, day, amount, *, observed, closed=False):
    currency, zone, _rate, _fee = ACCOUNT_SPECS[account_id]
    run_id = "canonical-" + account_id + "-" + day.isoformat() + "-" + uuid4().hex
    start = datetime.combine(day, time.min, RIYADH).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    await db[SYNC_RUNS].insert_one({"user_id": OWNER, "provider": PROVIDER,
        "ad_account_id": account_id, "sync_run_id": run_id, "status": "complete",
        "financial_sync_status": "complete", "started_at": observed - timedelta(minutes=1),
        "completed_at": observed, "finished_at": observed,
        "request_window": {"date_from": day.isoformat(), "date_to": day.isoformat(),
                           "action_report_time": "conversion"}})
    identity = {"user_id": OWNER, "provider": PROVIDER, "ad_account_id": account_id,
        "report_date": day.isoformat(), "projection_timezone": "Asia/Riyadh",
        "action_report_time": "conversion"}
    hours = [{"sequence": i, "local_hour": f"{i:02}:00", "hour_start_utc": start + timedelta(hours=i),
        "hour_end_utc": start + timedelta(hours=i + 1), "spend_native": amount if i == 0 else 0,
        "status": "confirmed_data" if closed else "provisional"} for i in range(24)]
    await db[PROJECTIONS].update_one(identity, {"$set": {**identity,
        "account_timezone": zone, "currency": currency, "sync_run_id": run_id,
        "source_sync_run_ids": [run_id], "source_latest_updated_at": observed,
        "generated_at": observed, "updated_at": observed, "window_start_utc": start,
        "window_end_utc": end, "base_spend_native": amount, "amount_complete": True,
        "source_fact_count": 24, "data_state": "confirmed_data" if amount else "confirmed_zero",
        "coverage": {"status": "complete", "expected_requests": 1, "completed_requests": 1},
        "impressions": 42, "swipes": 3, "purchases": 1, "purchase_value_native": 29,
        "hours": hours, "source_close_proof": close_proof(
            account_id=account_id, business_date=day.isoformat(), timezone="Asia/Riyadh",
            currency=currency, spend=amount, provider_row_count=24, complete_response=closed,
            explicit_spend_present=True, identity_proven=True,
            source_mode="snapchat_v2_daily_projection", observed_at=observed.isoformat(),
            reason="synthetic-canonical-fixture"),
    }}, upsert=True)
    if closed:
        await db[RECONCILIATIONS].update_one({"user_id": OWNER, "ad_account_id": account_id,
            "report_date": day.isoformat(), "action_report_time": "conversion"}, {"$set": {
                "provider": PROVIDER, "sync_run_id": run_id, "reconciled": True,
                "projection_source_match": True, "checked_at": observed,
            }}, upsert=True)


@pytest_asyncio.fixture
async def harness(storage, monkeypatch):
    db, clock = storage.db, Clock()
    transport = SpendTransport(clock)
    await db.mezan_integrations_v2.insert_one({"user_id": OWNER, "provider": PROVIDER,
        "connection_status": "connected", "connection_provenance": "api_connection"})
    for account_id, amount in (("usd-account", 10), ("sar-account", 5)):
        await seed_account(db, account_id, selected=account_id == "usd-account")
        await seed_canonical(db, account_id, DAY, amount, observed=NOW - timedelta(minutes=30))
        await seed_canonical(db, account_id, DAY - timedelta(days=1), amount,
                             observed=NOW - timedelta(minutes=30), closed=True)
    await seed_account(db, "disabled-account", enabled=False)
    await seed_account(db, "foreign-account", owner="foreign-owner", selected=True)
    await seed_account(db, "usd-account", owner="foreign-owner")
    # Decoy old facts must never become the button's source or display result.
    for name in ("mezan_snapchat_performance_daily_v2", "mezan_snapchat_entities_v2",
                 "mezan_snapchat_hourly_facts_v2", "mezan_snapchat_daily_total_facts_v2",
                 "mezan_snapchat_entity_facts_v2", "ad_account_ledger", "general_ledger"):
        await db[name].insert_one({"user_id": OWNER, "provider": PROVIDER,
            "ad_account_id": "usd-account", "date": DAY.isoformat(),
            "spend_sar": 999_999, "marker": "immutable-financial-or-hierarchy-sentinel"})
    monkeypatch.setattr(reader, "_operational_now", clock)
    monkeypatch.setattr(reader, "_today_in_timezone", lambda zone: clock().astimezone(ZoneInfo(zone)).date())
    jobs = jobs_module.ManualSpendJobs(db, now=clock, client_factory=transport.client)
    value = SimpleNamespace(db=db, clock=clock, transport=transport, jobs=jobs,
                            real_mongo=storage.real_mongo)
    try:
        yield value
    finally:
        transport.release.set()
        await jobs.stop()


def period(start=DAY, end=DAY):
    return jobs_module.ManualSpendPeriod(date_from=start, date_to=end)


async def finished(harness, accepted):
    async def poll():
        while True:
            result = await harness.jobs.read(OWNER, accepted["run_id"])
            if result["status"] not in {"queued", "running"}:
                return result
            await asyncio.sleep(0.005)
    result = await asyncio.wait_for(poll(), timeout=5)
    assert harness.transport.violations == []
    return result


async def refresh(harness, requested=None):
    accepted = await harness.jobs.request(OWNER, requested or period())
    return await finished(harness, accepted)


async def dashboard(harness, start=DAY, end=DAY):
    return await reader.load_snapchat_v2_dashboard_spend(harness.db, OWNER,
        date_from=start, date_to=end, timezone_name="Asia/Riyadh")


def by_account(report):
    return {row["account_id"]: row for row in report["accounts"]}


def utc(value):
    return value.astimezone(timezone.utc) if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))


async def financial_state(db):
    return {name: await db[name].find({}).sort("_id", 1).to_list(length=1000)
            for name in PROTECTED_COLLECTIONS}


@pytest.mark.asyncio
async def test_real_spend_job_updates_dashboard_native_sar_observation_and_bank_fee_only(harness):
    before = await dashboard(harness)
    assert before["total_sar"] == 42.5  # 10 USD * 3.75 + 5 SAR.
    protected = await financial_state(harness.db)
    completed = await refresh(harness)
    assert completed["status"] == "complete"
    assert completed["scope"] == "snapchat_spend_only"
    assert set(completed["requested_account_ids"]) == set(ACCOUNT_SPECS)
    assert completed["refreshed_count"] == 2 and completed["failed_count"] == 0
    after = await dashboard(harness)
    assert after["total_sar"] == 53  # 12 USD * 3.75 + 8 SAR, without bank fee twice.
    accounts = by_account(after)
    assert (accounts["usd-account"]["total_native"], accounts["usd-account"]["total_sar"]) == (12, 45)
    assert (accounts["sar-account"]["total_native"], accounts["sar-account"]["total_sar"]) == (8, 8)
    assert accounts["usd-account"]["currency"] == "USD" and accounts["usd-account"]["timezone"] == "America/Los_Angeles"
    assert accounts["sar-account"]["currency"] == "SAR" and accounts["sar-account"]["timezone"] == "Asia/Riyadh"
    for account_id, row in accounts.items():
        assert utc(row["quality"]["observed_at"]) > utc(by_account(before)[account_id]["quality"]["observed_at"])
        assert row["quality"]["amount_complete"] is False and row["quality"]["provisional"] is True
    assert after["bank_commissions"]["total_fee_sar"] == 0.98  # 45 * 2% + 8 * 1%.
    assert all(row["impressions"] is row["clicks"] is row["purchases"] is None for row in after["rows"])
    assert after["quality"]["amount_available"] is True and after["quality"]["complete"] is False
    actual_route = await dashboard_routes.build_dashboard_platform_spend(harness.db, OWNER,
        date_from=DAY.isoformat(), date_to=DAY.isoformat())
    assert actual_route["providers"]["snapchat"]["total_sar"] == 53
    assert {row["account_id"] for row in actual_route["providers"]["snapchat"]["accounts"]} == set(ACCOUNT_SPECS)
    assert len(harness.transport.calls) == 2
    assert await financial_state(harness.db) == protected


@pytest.mark.asyncio
async def test_partial_refresh_retains_the_failed_accounts_last_real_observation(harness):
    assert (await refresh(harness))["status"] == "complete"
    prior = by_account(await dashboard(harness))
    harness.clock.value += timedelta(minutes=5)
    harness.transport.modes["usd-account"] = "forbidden"
    harness.transport.amounts["sar-account"] = 9
    result = await refresh(harness)
    assert result["status"] == "partial" and result["refreshed_count"] == result["failed_count"] == 1
    details = {row["account_id"]: row for row in result["account_results"]}
    assert details["usd-account"]["status"] == "failed"
    assert details["sar-account"]["status"] == "complete"
    report = await dashboard(harness)
    accounts = by_account(report)
    assert report["total_sar"] == 54
    assert accounts["usd-account"]["total_native"] == 12
    assert utc(accounts["usd-account"]["quality"]["observed_at"]) == utc(prior["usd-account"]["quality"]["observed_at"])
    assert accounts["sar-account"]["total_native"] == 9
    assert utc(accounts["sar-account"]["quality"]["observed_at"]) > utc(prior["sar-account"]["quality"]["observed_at"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected_status,expected_native", [
    ("zero", "complete", 0), ("missing_hour", "partial", 10), ("missing_spend", "partial", 10),
])
async def test_explicit_zero_is_displayed_but_unobserved_hours_never_become_zero(harness, mode, expected_status, expected_native):
    harness.transport.modes["usd-account"] = mode
    result = await refresh(harness)
    assert result["status"] == expected_status
    report = await dashboard(harness)
    usd = by_account(report)["usd-account"]
    assert usd["total_native"] == expected_native
    assert usd["total_sar"] == expected_native * 3.75
    assert usd["quality"]["amount_complete"] is False
    if mode != "zero":
        assert utc(usd["quality"]["observed_at"]) == NOW - timedelta(minutes=30)


@pytest.mark.asyncio
async def test_exact_period_and_enabled_accounts_are_frozen_and_requests_are_split_under_seven_days(harness):
    requested = period(DAY - timedelta(days=7), DAY)
    harness.transport.release.clear()
    accepted = await harness.jobs.request(OWNER, requested)
    await asyncio.wait_for(harness.transport.entered.wait(), timeout=2)
    duplicate = await harness.jobs.request(OWNER, requested)
    assert duplicate["run_id"] == accepted["run_id"]
    with pytest.raises(HTTPException) as different_period:
        await harness.jobs.request(OWNER, period())
    assert different_period.value.status_code == 409
    await harness.db.mezan_integration_accounts_v2.update_one(
        {"user_id": OWNER, "ad_account_id": "disabled-account"}, {"$set": {"mezan_selected": True}})
    with pytest.raises(HTTPException) as different_scope:
        await harness.jobs.request(OWNER, requested)
    assert different_scope.value.status_code == 409
    with pytest.raises(HTTPException) as foreign_read:
        await harness.jobs.read("foreign-owner", accepted["run_id"])
    assert foreign_read.value.status_code == 404
    harness.transport.release.set()
    result = await finished(harness, accepted)
    assert result["status"] == "complete"
    assert result["date_from"] == requested.date_from.isoformat() and result["date_to"] == requested.date_to.isoformat()
    assert set(result["requested_account_ids"]) == set(ACCOUNT_SPECS)
    expected_start = datetime.combine(requested.date_from, time.min, RIYADH).astimezone(timezone.utc)
    expected_end = NOW.replace(minute=0) + timedelta(hours=1)
    for account_id in ACCOUNT_SPECS:
        windows = sorted((row["start"], row["end"]) for row in harness.transport.calls if row["account_id"] == account_id)
        assert 2 <= len(windows) <= 8
        assert windows[0][0] == expected_start and windows[-1][1] == expected_end
        assert all(left[1] == right[0] for left, right in zip(windows, windows[1:]))
    assert all(row["account_id"] in ACCOUNT_SPECS for row in harness.transport.calls)


@pytest.mark.asyncio
async def test_riyadh_business_day_has_exact_hours_across_the_usd_accounts_dst_change(harness):
    dst_day = date(2026, 11, 1)
    result = await refresh(harness, period(dst_day, dst_day))
    assert result["status"] == "complete"
    report = await dashboard(harness, dst_day, dst_day)
    assert report["total_sar"] == 53
    usd = next(row for row in harness.transport.calls if row["account_id"] == "usd-account")
    # Riyadh's 24-hour reporting day crosses the Los Angeles clock change;
    # it must not accidentally become the native 25-hour November 1 day.
    assert usd["start"] == datetime(2026, 10, 31, 21, tzinfo=timezone.utc)
    assert usd["end"] == datetime(2026, 11, 1, 21, tzinfo=timezone.utc)
    assert datetime.fromisoformat(usd["query"]["start_time"]).utcoffset() == timedelta(hours=-7)
    assert datetime.fromisoformat(usd["query"]["end_time"]).utcoffset() == timedelta(hours=-8)
    assert len(by_account(report)["usd-account"]["hourly_sar"][dst_day.isoformat()]) == 24


@pytest.mark.asyncio
async def test_account_disabled_during_fetch_cannot_publish_a_spend_snapshot(harness):
    harness.transport.blocked_account = "usd-account"
    harness.transport.release.clear()
    accepted = await harness.jobs.request(OWNER, period())
    await asyncio.wait_for(harness.transport.blocked.wait(), timeout=2)
    await harness.db.mezan_integration_accounts_v2.update_one(
        {"user_id": OWNER, "ad_account_id": "usd-account"}, {"$set": {"mezan_selected": False}})
    harness.transport.release.set()
    result = await finished(harness, accepted)
    assert result["status"] == "partial" and result["refreshed_count"] == result["failed_count"] == 1
    usd = next(row for row in result["account_results"] if row["account_id"] == "usd-account")
    assert usd["error_code"] == "manual_spend_account_changed"
    assert await harness.db[jobs_module.spend.COLLECTION].count_documents(
        {"user_id": OWNER, "ad_account_id": "usd-account"}) == 0
    report = await dashboard(harness)
    assert set(by_account(report)) == {"sar-account"} and report["total_sar"] == 8


@pytest.mark.asyncio
async def test_aged_manual_observation_is_labelled_then_expires_without_another_provider_call(harness):
    day = DAY - timedelta(days=1)
    assert (await refresh(harness, period(day, day)))["status"] == "complete"
    harness.clock.value = NOW + timedelta(hours=2, seconds=1)
    stale = await dashboard(harness, day, day)
    assert stale["total_sar"] == 53
    assert "snapchat_manual_spend_observation_stale" in stale["quality"]["reason_codes"]
    assert all(utc(row["quality"]["observed_at"]) == NOW for row in stale["accounts"])
    harness.clock.value = NOW + timedelta(hours=24, seconds=1)
    expired = await dashboard(harness, day, day)
    assert expired["total_sar"] == 42.5
    assert "snapchat_manual_spend_observation" not in expired["quality"]["reason_codes"]
    assert len(harness.transport.calls) == 2


@pytest.mark.asyncio
async def test_newer_complete_canonical_evidence_supersedes_only_the_whole_requested_period(harness):
    start, end = DAY - timedelta(days=2), DAY - timedelta(days=1)
    for account_id in ACCOUNT_SPECS:
        await seed_canonical(harness.db, account_id, start, 3,
                             observed=NOW - timedelta(minutes=30), closed=True)
    assert (await refresh(harness, period(start, end)))["status"] == "complete"
    assert (await dashboard(harness, start, end))["total_sar"] == 106
    harness.clock.value += timedelta(minutes=5)
    for account_id in ACCOUNT_SPECS:
        await seed_canonical(harness.db, account_id, start, 2, observed=harness.clock(), closed=True)
    still_manual = await dashboard(harness, start, end)
    assert still_manual["total_sar"] == 106 and still_manual["quality"]["complete"] is False
    for account_id in ACCOUNT_SPECS:
        await seed_canonical(harness.db, account_id, end, 2, observed=harness.clock(), closed=True)
    canonical = await dashboard(harness, start, end)
    assert canonical["total_sar"] == 19  # Two days * (2 USD * 3.75 + 2 SAR).
    assert canonical["quality"]["amount_complete"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("incomplete_source", ["unreconciled_day", "mixed_source_runs"])
async def test_newer_canonical_source_cannot_hide_a_snapshot_before_it_is_displayable(harness, incomplete_source):
    day = DAY - timedelta(days=1)
    assert (await refresh(harness, period(day, day)))["status"] == "complete"
    assert (await dashboard(harness, day, day))["total_sar"] == 53
    harness.clock.value += timedelta(minutes=5)
    for account_id in ACCOUNT_SPECS:
        await seed_canonical(harness.db, account_id, day, 2,
                             observed=harness.clock(), closed=True)
        if incomplete_source == "unreconciled_day":
            await harness.db[RECONCILIATIONS].delete_many({"user_id": OWNER,
                "ad_account_id": account_id, "report_date": day.isoformat()})
        else:
            failed_run = "failed-financial-" + account_id
            await harness.db[SYNC_RUNS].insert_one({"user_id": OWNER, "provider": PROVIDER,
                "ad_account_id": account_id, "sync_run_id": failed_run, "status": "partial",
                "financial_sync_status": "partial", "finished_at": harness.clock()})
            await harness.db[PROJECTIONS].update_one({"user_id": OWNER,
                "ad_account_id": account_id, "report_date": day.isoformat()},
                {"$push": {"source_sync_run_ids": failed_run}})
    report = await dashboard(harness, day, day)
    assert report["total_sar"] == 53
    assert report["quality"]["amount_available"] is True
    assert report["quality"]["amount_complete"] is False


@pytest.mark.asyncio
async def test_unsupported_account_hour_never_falls_back_to_a_campaign_request(harness):
    harness.transport.modes["usd-account"] = "unsupported"
    result = await refresh(harness)
    assert result["status"] == "partial"
    usd = next(row for row in result["account_results"] if row["account_id"] == "usd-account")
    assert usd["error_code"] == "unsupported_account_hour"
    assert len([row for row in harness.transport.calls if row["account_id"] == "usd-account"]) == 1
    assert by_account(await dashboard(harness))["usd-account"]["total_native"] == 10


@pytest.mark.asyncio
async def test_real_mongo_duplicate_clicks_from_independent_controllers_have_one_producer(harness):
    if not harness.real_mongo:
        pytest.skip("set ADS_LEASE_TEST_MONGO_PORT or SNAP_PERIOD_TEST_MONGO=1 for atomic Mongo ownership")
    other = jobs_module.ManualSpendJobs(harness.db, now=harness.clock, client_factory=harness.transport.client)
    harness.transport.release.clear()
    try:
        first, second = await asyncio.wait_for(asyncio.gather(
            harness.jobs.request(OWNER, period()), other.request(OWNER, period())), timeout=2)
        assert first["run_id"] == second["run_id"]
        await asyncio.wait_for(harness.transport.entered.wait(), timeout=2)
        assert len(harness.transport.calls) == 1
        harness.transport.release.set()
        assert (await finished(harness, first))["status"] == "complete"
        assert len(harness.transport.calls) == 2
        assert (await dashboard(harness))["total_sar"] == 53
    finally:
        harness.transport.release.set()
        await other.stop()


@pytest.mark.asyncio
async def test_real_mongo_duplicate_executors_claim_once_and_unfinished_candidates_stay_invisible(harness, monkeypatch):
    if not harness.real_mongo:
        pytest.skip("set ADS_LEASE_TEST_MONGO_PORT or SNAP_PERIOD_TEST_MONGO=1 for atomic Mongo ownership")
    # Pause scheduling only, then deliver the same durable queued job twice.
    # The real executors and Mongo claim must decide which worker may fetch.
    monkeypatch.setattr(harness.jobs, "_schedule", lambda _row: None)
    accepted = await harness.jobs.request(OWNER, period())
    assert accepted["status"] == "queued"
    other = jobs_module.ManualSpendJobs(harness.db, now=harness.clock, client_factory=harness.transport.client)
    harness.transport.blocked_account = "usd-account"
    harness.transport.release.clear()
    workers = [asyncio.create_task(controller._execute(OWNER, accepted["run_id"]))
               for controller in (harness.jobs, other)]
    try:
        await asyncio.wait_for(harness.transport.blocked.wait(), timeout=2)
        done, pending = await asyncio.wait(workers, timeout=2, return_when=asyncio.FIRST_COMPLETED)
        assert len(done) == len(pending) == 1
        assert len(harness.transport.calls) == 2
        # The SAR candidate was already saved, but the USD fetch is pending.
        # The actual reader must wait for the owning job's terminal publication.
        assert (await harness.jobs.read(OWNER, accepted["run_id"]))["status"] == "running"
        assert (await dashboard(harness))["total_sar"] == 42.5
        harness.transport.release.set()
        await asyncio.wait_for(asyncio.gather(*workers), timeout=5)
        assert (await finished(harness, accepted))["status"] == "complete"
        assert len(harness.transport.calls) == 2
        assert (await dashboard(harness))["total_sar"] == 53
    finally:
        harness.transport.release.set()
        await asyncio.gather(*workers, return_exceptions=True)
        await other.stop()
