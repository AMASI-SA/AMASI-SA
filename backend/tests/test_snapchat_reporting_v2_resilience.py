from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from snapchat_v2 import provider_total as provider_total_module
from snapchat_v2.client import SnapchatV2Client
from snapchat_v2.lease import recover_expired_leases
from snapchat_v2.projections import build_daily_projection, business_day_window
from snapchat_v2.reconciliation import reconcile_day
from snapchat_v2.sync_runs import recover_abandoned_sync_runs
from snapchat_v2.token_store import SnapchatTokenStore


class Result:
    def __init__(self, *, modified_count=0, matched_count=0, upserted_id=None):
        self.modified_count = modified_count
        self.matched_count = matched_count
        self.upserted_id = upserted_id


class QueueResponse:
    def __init__(self, status_code: int, payload: dict, *, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload


class QueueHTTPClient:
    def __init__(self, responses):
        self.responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, *_args, **_kwargs):
        return self.responses.pop(0)


class QueueHTTPFactory:
    def __init__(self, responses):
        self.responses = list(responses)

    def __call__(self, **_kwargs):
        return QueueHTTPClient(self.responses)


class RefreshingTokenStore:
    def __init__(self):
        self.calls = []

    async def get_access_token(self, _user_id, *, force_refresh=False):
        self.calls.append(force_refresh)
        return "refreshed" if force_refresh else "expired"


@pytest.mark.asyncio
async def test_provider_client_refreshes_once_after_401():
    tokens = RefreshingTokenStore()
    client = SnapchatV2Client(
        object(),
        "u1",
        token_store=tokens,
        client_factory=QueueHTTPFactory(
            [
                QueueResponse(401, {}),
                QueueResponse(200, {"request_status": "SUCCESS", "paging": {}}),
            ]
        ),
    )
    async with client.client_factory(timeout=1) as http_client:
        payload = await client._request_json(
            http_client,
            "https://adsapi.snapchat.com/v1/test",
            params=None,
        )
    assert payload["request_status"] == "SUCCESS"
    assert tokens.calls == [False, True]
    assert client.provider_calls == 2


@pytest.mark.asyncio
async def test_provider_client_retries_500(monkeypatch):
    async def no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("snapchat_v2.client.asyncio.sleep", no_sleep)
    client = SnapchatV2Client(
        object(),
        "u1",
        token_store=RefreshingTokenStore(),
        client_factory=QueueHTTPFactory(
            [
                QueueResponse(500, {}),
                QueueResponse(200, {"request_status": "SUCCESS", "paging": {}}),
            ]
        ),
    )
    async with client.client_factory(timeout=1) as http_client:
        payload = await client._request_json(
            http_client,
            "https://adsapi.snapchat.com/v1/test",
            params=None,
        )
    assert payload["request_status"] == "SUCCESS"
    assert client.provider_calls == 2


@pytest.mark.asyncio
async def test_provider_total_reads_account_and_riyadh_windows(monkeypatch):
    calls = []

    async def fake_window(_client, _account, *, start_utc, end_utc, **_kwargs):
        calls.append((start_utc, end_utc))
        spend = 90.0 if len(calls) == 1 else 100.0
        return {
            "provider_spend_native": spend,
            "window_start_utc": start_utc,
            "window_end_utc": end_utc,
            "coverage": {"status": "complete", "data_state": "confirmed_data"},
        }

    monkeypatch.setattr(provider_total_module, "_fetch_window_total", fake_window)
    fixed_now = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
    client = SimpleNamespace(now=lambda: fixed_now, provider_calls=2)
    start, end = business_day_window(date(2026, 8, 22), "Asia/Riyadh")
    result = await provider_total_module.fetch_provider_total(
        client,
        {
            "ad_account_id": "a1",
            "timezone": "America/Los_Angeles",
        },
        start_utc=start,
        end_utc=end,
    )
    assert len(calls) == 2
    assert calls[0] != calls[1]
    assert result["dashboard_provider_spend_native"] == 90.0
    assert result["account_day_provider_spend_native"] == 100.0


@pytest.mark.asyncio
async def test_dual_window_reconciliation_compares_each_projection_to_matching_total(
    monkeypatch,
):
    async def fake_costs(*_args, **_kwargs):
        return {
            "base_spend_sar": 337.5,
            "commission_sar": 20.25,
            "final_cost_sar": 357.75,
            "bank_commission_pct": 6.0,
            "apply_bank_commission": True,
            "exchange_rate_to_sar": 3.75,
            "cost_setting_configured": True,
            "cost_coverage": {"status": "complete"},
        }

    async def no_indexes(_db):
        return None

    monkeypatch.setattr(
        "snapchat_v2.reconciliation.calculate_cost_components",
        fake_costs,
    )
    monkeypatch.setattr(
        "snapchat_v2.reconciliation.ensure_reconciliation_indexes",
        no_indexes,
    )

    class Collection:
        async def update_one(self, *_args, **_kwargs):
            return Result(modified_count=1)

    class DB:
        def __getitem__(self, _name):
            return Collection()

    account_start, account_end = business_day_window(
        date(2026, 8, 22),
        "America/Los_Angeles",
    )
    riyadh_start, riyadh_end = business_day_window(
        date(2026, 8, 22),
        "Asia/Riyadh",
    )
    result = await reconcile_day(
        DB(),
        user_id="u1",
        account={
            "ad_account_id": "a1",
            "timezone": "America/Los_Angeles",
            "currency": "USD",
        },
        report_date=date(2026, 8, 22),
        provider_total={
            "provider_spend_native": 90.0,
            "coverage": {"status": "complete"},
            "dashboard_provider_spend_native": 90.0,
            "dashboard_coverage": {"status": "complete"},
            "account_day_provider_spend_native": 100.0,
            "account_day_coverage": {"status": "complete"},
            "account_day_window_start_utc": account_start,
            "account_day_window_end_utc": account_end,
            "window_start_utc": riyadh_start,
            "window_end_utc": riyadh_end,
        },
        snap_page_projection={
            "provider": "snapchat_ads",
            "ad_account_id": "a1",
            "action_report_time": "conversion",
            "window_start_utc": account_start,
            "window_end_utc": account_end,
            "base_spend_native": 100.0,
            "amount_complete": True,
            "coverage": {"status": "complete"},
        },
        dashboard_projection={
            "provider": "snapchat_ads",
            "ad_account_id": "a1",
            "projection_timezone": "Asia/Riyadh",
            "action_report_time": "conversion",
            "window_start_utc": riyadh_start,
            "window_end_utc": riyadh_end,
            "base_spend_native": 90.0,
            "amount_complete": True,
            "coverage": {"status": "complete"},
        },
    )
    assert result["reconciled"] is True
    assert result["difference_native"] == 0
    assert result["dashboard_difference_native"] == 0
    assert result["final_cost_sar"] == 357.75


@pytest.mark.asyncio
async def test_current_hour_is_provisional_and_future_hours_remain_unknown(monkeypatch):
    now = datetime(2026, 8, 23, 12, 30, tzinfo=timezone.utc)

    async def facts(*_args, **_kwargs):
        return [
            {
                "hour_start_utc": datetime(2026, 8, 23, 12, tzinfo=timezone.utc),
                "hour_end_utc": datetime(2026, 8, 23, 13, tzinfo=timezone.utc),
                "spend_native": 3.5,
                "impressions": 10,
                "swipes": 1,
                "video_views": 4,
                "purchases": 0,
                "purchase_value_native": 0,
                "provisional": True,
                "sync_run_id": "run-1",
                "updated_at": now,
            }
        ]

    monkeypatch.setattr("snapchat_v2.projections.load_hourly_facts", facts)
    result = await build_daily_projection(
        object(),
        user_id="u1",
        account={"ad_account_id": "a1", "timezone": "UTC", "currency": "USD"},
        report_date=date(2026, 8, 23),
        projection_timezone="UTC",
        coverage={"status": "complete", "data_state": "confirmed_data"},
        now=now,
    )
    current = next(row for row in result["hours"] if row["local_hour"] == "12:00")
    assert current["status"] == "provisional"
    future = [row for row in result["hours"] if row["status"] == "future"]
    assert future
    assert all(row["spend_native"] is None for row in future)


@pytest.mark.asyncio
async def test_stale_leases_and_runs_are_recovered():
    class Collection:
        def __init__(self):
            self.calls = []

        async def update_many(self, query, update):
            self.calls.append((query, update))
            return Result(modified_count=2)

    collections = {}

    class DB:
        def __getitem__(self, name):
            collections.setdefault(name, Collection())
            return collections[name]

    db = DB()
    assert await recover_expired_leases(db) == 2
    assert await recover_abandoned_sync_runs(db) == 2
    lease_update = collections["mezan_snapchat_leases_v2"].calls[0][1]
    run_update = collections["mezan_snapchat_sync_runs_v2"].calls[0][1]
    assert lease_update["$set"]["status"] == "abandoned"
    assert run_update["$set"]["status"] == "abandoned"


@pytest.mark.asyncio
async def test_refresh_rotation_race_uses_peer_token(monkeypatch):
    fixed_now = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)
    expired = fixed_now - timedelta(minutes=1)
    fresh = fixed_now + timedelta(hours=1)
    rows = [
        {
            "access_token_ciphertext": "old-access",
            "refresh_token_ciphertext": "refresh-1",
            "access_token_expires_at": expired,
        },
        {
            "access_token_ciphertext": "old-access",
            "refresh_token_ciphertext": "refresh-1",
            "access_token_expires_at": expired,
        },
        {
            "access_token_ciphertext": "peer-access",
            "refresh_token_ciphertext": "refresh-2",
            "access_token_expires_at": fresh,
        },
    ]

    class Collection:
        def __init__(self):
            self.update_calls = 0

        async def find_one(self, *_args, **_kwargs):
            return rows.pop(0)

        async def update_one(self, *_args, **_kwargs):
            self.update_calls += 1
            if self.update_calls == 1:
                return Result(modified_count=1)  # refresh lease
            if self.update_calls == 2:
                return Result(modified_count=0)  # CAS lost to peer
            return Result(modified_count=1)  # release

    collection = Collection()

    class DB:
        def __getitem__(self, _name):
            return collection

    store = SnapchatTokenStore(DB(), now=lambda: fixed_now)

    async def refreshed(_refresh_token):
        return {
            "access_token": "our-access",
            "refresh_token": "our-refresh",
            "expires_in": 3600,
        }

    monkeypatch.setattr(store, "_provider_refresh", refreshed)
    monkeypatch.setattr("snapchat_v2.token_store.decrypt_snapchat_token", lambda value: value)
    monkeypatch.setattr("snapchat_v2.token_store.encrypt_snapchat_token", lambda value: value)
    token = await store.get_access_token("u1")
    assert token == "peer-access"
