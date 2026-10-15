from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from snapchat_v2.client import SnapchatV2Client, split_hour_windows
from snapchat_v2.facts import (
    hourly_fact_identity,
    load_hourly_facts,
    normalize_hourly_fact,
    upsert_hourly_fact,
)
from snapchat_v2.lease import acquire_lease
from snapchat_v2.models import SNAPCHAT_PROVIDER
from snapchat_v2.projections import build_daily_projection, business_day_window
from snapchat_v2.reconciliation import calculate_cost_components
from snapchat_v2.sync_runs import complete_sync_run, new_sync_run


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, *, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload


class FakeHTTPClient:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.calls: list[tuple[str, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, *, headers, params):
        assert headers["Authorization"].startswith("Bearer ")
        self.calls.append((url, params))
        return self.responses.pop(0)


class FakeHTTPFactory:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.clients: list[FakeHTTPClient] = []

    def __call__(self, **_kwargs):
        client = FakeHTTPClient(self.responses)
        self.clients.append(client)
        return client


class FakeTokenStore:
    def __init__(self):
        self.calls: list[bool] = []

    async def get_access_token(self, _user_id: str, *, force_refresh: bool = False):
        self.calls.append(force_refresh)
        return "safe-test-token"


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def to_list(self, length=None):
        return self.rows[:length]


class ReadCollection:
    def __init__(self, *, one=None, rows=None):
        self.one = one
        self.rows = rows or []

    async def find_one(self, *_args, **_kwargs):
        return self.one

    def find(self, *_args, **_kwargs):
        return Cursor(self.rows)


class CostDB:
    def __init__(self, integration, settings):
        self.collections = {
            "mezan_integration_accounts_v2": ReadCollection(one=integration),
            "mezan_ad_account_cost_settings_v2": ReadCollection(rows=settings),
        }

    def __getitem__(self, name):
        return self.collections[name]


def _fact(**overrides):
    base = {
        "user_id": "u1",
        "ad_account_id": "a1",
        "campaign_id": "c1",
        "hour_start_utc": datetime(2026, 8, 22, 10, tzinfo=timezone.utc),
        "hour_end_utc": datetime(2026, 8, 22, 11, tzinfo=timezone.utc),
        "account_timezone": "America/Los_Angeles",
        "currency": "USD",
        "action_report_time": "conversion",
        "attribution_windows": {"swipe": "28_DAY", "view": "7_DAY"},
        "spend_native": 12.5,
        "impressions": 100,
        "swipes": 5,
        "video_views": 25,
        "purchases": 1,
        "purchase_value_native": 45,
        "sync_run_id": "run-1",
        "source": {"access_token": "must-not-survive", "api": "snapchat"},
    }
    base.update(overrides)
    return base


def test_hour_windows_never_exceed_seven_days_and_have_no_gaps():
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 1, tzinfo=timezone.utc)
    windows = split_hour_windows(
        start,
        end,
        account_timezone="America/Los_Angeles",
    )
    assert len(windows) == 5
    assert windows[0].start_utc == start
    assert windows[-1].end_utc == end
    assert all(window.end_utc - window.start_utc <= timedelta(days=7) for window in windows)
    assert all(left.end_utc == right.start_utc for left, right in zip(windows, windows[1:]))


def test_business_day_windows_preserve_los_angeles_dst():
    spring_start, spring_end = business_day_window(
        date(2026, 3, 8),
        "America/Los_Angeles",
    )
    fall_start, fall_end = business_day_window(
        date(2026, 11, 1),
        "America/Los_Angeles",
    )
    assert spring_end - spring_start == timedelta(hours=23)
    assert fall_end - fall_start == timedelta(hours=25)


def test_hourly_fact_is_utc_idempotent_and_scrubs_secrets():
    normalized = normalize_hourly_fact(_fact())
    assert normalized["provider"] == SNAPCHAT_PROVIDER
    assert normalized["entity_type"] == "campaign"
    assert normalized["spend_native"] == 12.5
    assert "access_token" not in normalized["source"]
    assert hourly_fact_identity(normalized) == hourly_fact_identity(
        normalize_hourly_fact(_fact())
    )


@pytest.mark.asyncio
async def test_hourly_upsert_does_not_overlap_set_and_set_on_insert():
    class Collection:
        async def update_one(self, identity, update, *, upsert):
            self.identity = identity
            self.update = update
            self.upsert = upsert
            return SimpleNamespace(upserted_id="new", matched_count=0, modified_count=0)

    collection = Collection()

    class DB:
        def __getitem__(self, _name):
            return collection

    result = await upsert_hourly_fact(DB(), _fact())
    assert result["inserted"] is True
    assert set(collection.update["$set"]).isdisjoint(collection.update["$setOnInsert"])
    assert collection.upsert is True


@pytest.mark.asyncio
async def test_hourly_read_falls_back_to_confirmed_previous_attribution_series():
    legacy = normalize_hourly_fact(
        _fact(attribution_windows={"swipe": "28_DAY", "view": "1_DAY"})
    )

    class Collection:
        def __init__(self):
            self.queries = []

        def find(self, query, _projection):
            self.queries.append(dict(query))
            if "attribution_key" in query:
                return Cursor([])
            return Cursor([legacy])

    collection = Collection()

    class DB:
        def __getitem__(self, _name):
            return collection

    rows = await load_hourly_facts(
        DB(),
        user_id="u1",
        ad_account_id="a1",
        start_utc=datetime(2026, 8, 22, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 23, tzinfo=timezone.utc),
        entity_type="campaign",
        action_report_time="conversion",
    )

    assert rows == [legacy]
    assert len(collection.queries) == 2
    assert "attribution_key" in collection.queries[0]
    assert "attribution_key" not in collection.queries[1]


@pytest.mark.asyncio
async def test_atomic_lease_update_has_no_conflicting_set_on_insert():
    class Collection:
        async def update_one(self, query, update):
            self.query = query
            self.update = update
            return SimpleNamespace(modified_count=1)

    collection = Collection()

    class DB:
        def __getitem__(self, _name):
            return collection

    acquired = await acquire_lease(DB(), "u1", "a1", "owner-1")
    assert acquired is True
    assert "$setOnInsert" not in collection.update
    assert collection.update["$set"]["owner_id"] == "owner-1"


@pytest.mark.asyncio
async def test_client_retries_429_and_completes_multi_page_entity_discovery(monkeypatch):
    monkeypatch.setattr("snapchat_v2.client.asyncio.sleep", lambda *_args: _noop())
    responses = [
        FakeResponse(429, {}, headers={"Retry-After": "0"}),
        FakeResponse(
            200,
            {
                "request_status": "SUCCESS",
                "campaigns": [{"campaign": {"id": "c1", "name": "One"}}],
                "paging": {"next_link": "https://adsapi.snapchat.com/v1/next"},
            },
        ),
        FakeResponse(
            200,
            {
                "request_status": "SUCCESS",
                "campaigns": [{"campaign": {"id": "c2", "name": "Two"}}],
                "paging": {},
            },
        ),
    ]
    factory = FakeHTTPFactory(responses)
    client = SnapchatV2Client(
        object(),
        "u1",
        token_store=FakeTokenStore(),
        client_factory=factory,
    )
    result = await client.fetch_entities("a1", "campaign")
    assert [row["external_id"] for row in result["rows"]] == ["c1", "c2"]
    assert result["coverage"]["completed_requests"] == 2
    assert client.provider_calls == 3


async def _noop():
    return None


@pytest.mark.asyncio
async def test_hourly_fetch_builds_account_and_campaign_facts():
    payload = {
        "request_status": "SUCCESS",
        "timeseries_stats": [
            {
                "sub_request_status": "SUCCESS",
                "timeseries_stat": {
                    "granularity": "HOUR",
                    "breakdown_stats": {
                        "campaign": [
                            {
                                "id": "c1",
                                "timeseries": [
                                    {
                                        "start_time": "2026-08-22T10:00:00+00:00",
                                        "end_time": "2026-08-22T11:00:00+00:00",
                                        "stats": {
                                            "spend": 1_250_000,
                                            "impressions": 100,
                                            "swipes": 5,
                                            "video_views": 20,
                                            "conversion_purchases": 1,
                                            "conversion_purchases_value": 5_000_000,
                                        },
                                    }
                                ],
                            },
                            {
                                "id": "c2",
                                "timeseries": [
                                    {
                                        "start_time": "2026-08-22T10:00:00+00:00",
                                        "end_time": "2026-08-22T11:00:00+00:00",
                                        "stats": {
                                            "spend": 750_000,
                                            "impressions": 50,
                                            "swipes": 2,
                                            "video_views": 10,
                                            "conversion_purchases": 0,
                                            "conversion_purchases_value": 0,
                                        },
                                    }
                                ],
                            },
                        ]
                    },
                },
            }
        ],
        "paging": {},
    }
    client = SnapchatV2Client(
        object(),
        "u1",
        token_store=FakeTokenStore(),
        client_factory=FakeHTTPFactory([FakeResponse(200, payload)]),
    )
    result = await client.fetch_hourly_facts(
        {
            "ad_account_id": "a1",
            "timezone": "UTC",
            "currency": "USD",
        },
        start_utc=datetime(2026, 8, 22, 10, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 22, 11, tzinfo=timezone.utc),
        sync_run_id="run-1",
    )
    assert len(result["campaign_rows"]) == 2
    assert len(result["account_rows"]) == 1
    assert result["account_rows"][0]["spend_native"] == 2.0
    assert result["account_rows"][0]["impressions"] == 150
    assert result["coverage"]["status"] == "complete"


@pytest.mark.asyncio
async def test_future_projection_hours_are_unknown_not_zero(monkeypatch):
    async def no_facts(*_args, **_kwargs):
        return []

    monkeypatch.setattr("snapchat_v2.projections.load_hourly_facts", no_facts)
    now = datetime(2026, 8, 23, 12, 30, tzinfo=timezone.utc)
    result = await build_daily_projection(
        object(),
        user_id="u1",
        account={
            "ad_account_id": "a1",
            "timezone": "UTC",
            "currency": "USD",
        },
        report_date=date(2026, 8, 23),
        projection_timezone="UTC",
        coverage={"status": "incomplete", "data_state": "unknown_incomplete"},
        now=now,
    )
    future = [row for row in result["hours"] if row["status"] == "future"]
    assert future
    assert all(row["spend_native"] is None for row in future)
    assert result["amount_complete"] is False


@pytest.mark.asyncio
async def test_configured_six_percent_commission_is_separate_from_base_spend():
    integration = {
        "user_id": "u1",
        "provider": SNAPCHAT_PROVIDER,
        "external_account_id": "a1",
        "ad_account_id": "a1",
        "mezan_integration_account_id": "identity-1",
        "currency": "USD",
        "display_name": "Amasi",
    }
    setting = {
        "user_id": "u1",
        "provider": SNAPCHAT_PROVIDER,
        "external_account_id": "a1",
        "mezan_integration_account_id": "identity-1",
        "native_currency": "USD",
        "exchange_rate_to_sar": 3.75,
        "bank_commission_pct": 6.0,
        "apply_bank_commission": True,
    }
    result = await calculate_cost_components(
        CostDB(integration, [setting]),
        user_id="u1",
        account={
            "ad_account_id": "a1",
            "currency": "USD",
            "timezone": "America/Los_Angeles",
        },
        spend_native=100,
    )
    assert result["base_spend_sar"] == 375.0
    assert result["commission_sar"] == 22.5
    assert result["final_cost_sar"] == 397.5


@pytest.mark.asyncio
async def test_financial_success_is_partial_not_failed_when_ad_level_is_partial():
    run = new_sync_run("u1", "a1")
    run.update(
        {
            "financial_sync_status": "complete",
            "campaign_sync_status": "complete",
            "ad_squad_sync_status": "partial",
            "ad_sync_status": "partial",
            "identity_sync_status": "complete",
        }
    )

    class Collection:
        async def find_one(self, *_args, **_kwargs):
            return run

        async def update_one(self, query, update):
            self.query = query
            self.update = update
            return SimpleNamespace(modified_count=1)

    collection = Collection()

    class DB:
        def __getitem__(self, _name):
            return collection

    await complete_sync_run(DB(), run["sync_run_id"])
    assert collection.update["$set"]["status"] == "partial"
