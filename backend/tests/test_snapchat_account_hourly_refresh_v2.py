from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from integrations_control_center import snapchat_account_hourly_refresh as hourly
from integrations_control_center.snapchat_account_hourly_refresh import (
    ACCOUNT_REFRESH_SOURCE_MODE,
    CAMPAIGN_FACTS_SCHEMA_VERSION,
    CAMPAIGN_FACTS_SOURCE_MODE,
    PROVIDER_BREAKDOWN,
    PROVIDER_GRANULARITY,
    _fetch_account_hours,
    aggregate_account_hours_by_riyadh_day,
    extract_account_hour_rows,
    snapchat_account_request_window,
    snapchat_hourly_request_window,
)
from integrations_control_center.snapchat_native_data_common import (
    SnapchatNativeSyncError,
)
from integrations_control_center.snapchat_dashboard_summary_routes import (
    summarize_snapchat_dashboard_rows,
)


def _metrics(*, spend: int, purchases: int = 0, value: int = 0):
    return {
        "impressions": 100,
        "swipes": 10,
        "spend": spend,
        "video_views": 50,
        "view_completion": 20,
        "conversion_purchases": purchases,
        "conversion_purchases_value": value,
    }


def _hour_payload(*campaigns, status="SUCCESS"):
    return {
        "request_status": "SUCCESS",
        "timeseries_stats": [
            {
                "sub_request_status": status,
                "timeseries_stat": {
                    "granularity": "HOUR",
                    "breakdown_stats": {
                        "campaign": [
                            {
                                "id": campaign_id,
                                "timeseries": points,
                            }
                            for campaign_id, points in campaigns
                        ]
                    },
                },
            }
        ],
    }


def test_los_angeles_full_riyadh_day_uses_native_account_timezone():
    window = snapchat_account_request_window(
        date(2026, 8, 2),
        date(2026, 8, 2),
        account_timezone="America/Los_Angeles",
        now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )

    assert window is not None
    assert window["business_start"].isoformat() == "2026-08-02T00:00:00+03:00"
    assert window["business_end"].isoformat() == "2026-08-03T00:00:00+03:00"
    assert window["provider_start"].isoformat() == "2026-08-01T14:00:00-07:00"
    assert window["provider_end"].isoformat() == "2026-08-02T14:00:00-07:00"


def test_los_angeles_current_riyadh_day_includes_open_hour():
    window = snapchat_account_request_window(
        date(2026, 8, 2),
        date(2026, 8, 2),
        account_timezone="America/Los_Angeles",
        now=datetime(2026, 8, 2, 13, 33, tzinfo=timezone.utc),
    )

    assert window is not None
    assert window["business_end"].isoformat() == "2026-08-02T17:00:00+03:00"
    assert window["provider_start"].isoformat() == "2026-08-01T14:00:00-07:00"
    assert window["provider_end"].isoformat() == "2026-08-02T07:00:00-07:00"


def test_completed_hour_fallback_is_aligned_in_account_timezone():
    window = snapchat_account_request_window(
        date(2026, 8, 2),
        date(2026, 8, 2),
        account_timezone="America/Los_Angeles",
        now=datetime(2026, 8, 2, 13, 33, tzinfo=timezone.utc),
        include_current_hour=False,
    )

    assert window is not None
    assert window["business_end"].isoformat() == "2026-08-02T16:00:00+03:00"
    assert window["provider_end"].isoformat() == "2026-08-02T06:00:00-07:00"


def test_los_angeles_winter_window_uses_dst_aware_offset():
    window = snapchat_account_request_window(
        date(2026, 1, 2),
        date(2026, 1, 2),
        account_timezone="America/Los_Angeles",
        now=datetime(2026, 1, 3, 12, 0, tzinfo=timezone.utc),
    )

    assert window is not None
    assert window["provider_start"].isoformat() == "2026-01-01T13:00:00-08:00"
    assert window["provider_end"].isoformat() == "2026-01-02T13:00:00-08:00"


def test_riyadh_account_keeps_riyadh_boundaries():
    window = snapchat_account_request_window(
        date(2026, 8, 2),
        date(2026, 8, 2),
        account_timezone="Asia/Riyadh",
        now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )

    assert window is not None
    assert window["provider_start"].isoformat() == "2026-08-02T00:00:00+03:00"
    assert window["provider_end"].isoformat() == "2026-08-03T00:00:00+03:00"


def test_los_angeles_hours_follow_riyadh_midnight():
    rows = [
        {
            "start_time": "2026-07-31T13:00:00-07:00",
            "end_time": "2026-07-31T14:00:00-07:00",
            "metrics": _metrics(
                spend=2_000_000,
                purchases=2,
                value=6_000_000,
            ),
        },
        {
            "start_time": "2026-07-31T14:00:00-07:00",
            "end_time": "2026-07-31T15:00:00-07:00",
            "metrics": _metrics(
                spend=9_000_000,
                purchases=9,
                value=9_000_000,
            ),
        },
    ]

    daily = aggregate_account_hours_by_riyadh_day(
        rows,
        start_date=date(2026, 7, 31),
        end_date=date(2026, 7, 31),
    )

    assert set(daily) == {"2026-07-31"}
    assert daily["2026-07-31"]["rows"] == 1
    assert daily["2026-07-31"]["sums"]["spend"] == 2_000_000


def test_extracts_and_aggregates_campaign_hour_rows():
    payload = _hour_payload(
        (
            "campaign-1",
            [
                {
                    "start_time": "2026-08-01T14:00:00-07:00",
                    "end_time": "2026-08-01T15:00:00-07:00",
                    "stats": _metrics(
                        spend=5_000_000,
                        purchases=2,
                        value=10_000_000,
                    ),
                }
            ],
        ),
        (
            "campaign-2",
            [
                {
                    "start_time": "2026-08-01T14:00:00-07:00",
                    "end_time": "2026-08-01T15:00:00-07:00",
                    "stats": _metrics(
                        spend=1_000_000,
                        purchases=1,
                        value=4_000_000,
                    ),
                }
            ],
        ),
    )

    rows, errors, successful = extract_account_hour_rows(payload)
    daily = aggregate_account_hours_by_riyadh_day(
        rows,
        start_date=date(2026, 8, 2),
        end_date=date(2026, 8, 2),
    )

    assert errors == []
    assert successful == 1
    assert len(rows) == 2
    assert daily["2026-08-02"]["sums"]["spend"] == 6_000_000
    assert daily["2026-08-02"]["sums"]["conversion_purchases"] == 3


class _CaptureContext:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def get_json(self, client, url, *, headers, params=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        return self.payload


def test_provider_request_uses_hour_campaign_breakdown_and_native_window():
    payload = _hour_payload(
        (
            "campaign-1",
            [
                {
                    "start_time": "2026-08-01T14:00:00-07:00",
                    "end_time": "2026-08-01T15:00:00-07:00",
                    "stats": _metrics(spend=5_000_000),
                }
            ],
        )
    )
    context = _CaptureContext(payload)

    fetched = asyncio.run(
        _fetch_account_hours(
            context,
            object(),
            "access-token",
            account_id="account-1",
            request_start=datetime.fromisoformat("2026-08-01T14:00:00-07:00"),
            request_end=datetime.fromisoformat("2026-08-02T14:00:00-07:00"),
        )
    )
    rows, errors = fetched

    params = context.calls[0]["params"]
    assert params["granularity"] == PROVIDER_GRANULARITY == "HOUR"
    assert params["breakdown"] == PROVIDER_BREAKDOWN == "campaign"
    assert params["start_time"] == "2026-08-01T14:00:00-07:00"
    assert params["end_time"] == "2026-08-02T14:00:00-07:00"
    assert len(rows) == 1
    assert errors == []
    assert fetched.coverage == {
        "status": "complete",
        "data_state": "confirmed_data",
        "expected_requests": 1,
        "completed_requests": 1,
    }


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"request_status": "SUCCESS"}, "snapchat_account_hour_timeseries_missing"),
        (
            {
                "request_status": "SUCCESS",
                "timeseries_stats": [{"sub_request_status": "SUCCESS"}],
            },
            "snapchat_account_hour_timeseries_stat_missing",
        ),
        (
            {
                "request_status": "SUCCESS",
                "timeseries_stats": [{
                    "sub_request_status": "SUCCESS",
                    "timeseries_stat": {},
                }],
            },
            "snapchat_account_hour_campaign_breakdown_missing",
        ),
    ],
)
def test_successful_http_with_malformed_timeseries_fails_closed(payload, code):
    context = _CaptureContext(payload)

    with pytest.raises(SnapchatNativeSyncError) as raised:
        asyncio.run(
            _fetch_account_hours(
                context,
                object(),
                "access-token",
                account_id="account-1",
                request_start=datetime.fromisoformat("2026-08-02T00:00:00+03:00"),
                request_end=datetime.fromisoformat("2026-08-03T00:00:00+03:00"),
            )
        )

    assert raised.value.code == code
    assert raised.value.result["coverage"]["status"] == "incomplete"
    assert raised.value.result["coverage"]["data_state"] == "unknown_incomplete"


def test_provider_rows_outside_requested_window_are_incomplete():
    context = _CaptureContext(
        _hour_payload(
            (
                "campaign-1",
                [
                    {
                        "start_time": "2026-08-01T00:00:00+03:00",
                        "end_time": "2026-08-01T01:00:00+03:00",
                        "stats": _metrics(spend=0),
                    }
                ],
            )
        )
    )

    with pytest.raises(SnapchatNativeSyncError) as raised:
        asyncio.run(
            _fetch_account_hours(
                context,
                object(),
                "access-token",
                account_id="account-1",
                request_start=datetime.fromisoformat("2026-08-02T00:00:00+03:00"),
                request_end=datetime.fromisoformat("2026-08-03T00:00:00+03:00"),
            )
        )

    assert raised.value.code == "snapchat_account_hour_window_mismatch"
    assert raised.value.result["coverage"] == {
        "status": "incomplete",
        "data_state": "unknown_incomplete",
        "expected_requests": 1,
        "completed_requests": 0,
        "reason": "snapchat_account_hour_window_mismatch",
    }


def test_empty_timeseries_without_result_envelope_is_incomplete():
    context = _CaptureContext({
        "request_status": "SUCCESS",
        "timeseries_stats": [],
    })

    with pytest.raises(SnapchatNativeSyncError) as raised:
        asyncio.run(
            _fetch_account_hours(
                context,
                object(),
                "access-token",
                account_id="account-1",
                request_start=datetime.fromisoformat("2026-08-02T00:00:00+03:00"),
                request_end=datetime.fromisoformat("2026-08-03T00:00:00+03:00"),
            )
        )

    assert raised.value.code == "snapchat_account_hour_result_envelope_missing"
    assert raised.value.result["coverage"]["status"] == "incomplete"


def test_valid_empty_campaign_breakdown_is_confirmed_no_data():
    context = _CaptureContext(_hour_payload())

    fetched = asyncio.run(
        _fetch_account_hours(
            context,
            object(),
            "access-token",
            account_id="account-1",
            request_start=datetime.fromisoformat("2026-08-02T00:00:00+03:00"),
            request_end=datetime.fromisoformat("2026-08-03T00:00:00+03:00"),
        )
    )

    assert fetched.rows == []
    assert fetched.errors == []
    assert fetched.coverage["data_state"] == "confirmed_no_data"


def test_unfinished_pagination_is_incomplete(monkeypatch):
    payload = _hour_payload()
    payload["paging"] = {
        "next_link": "https://adsapi.snapchat.com/v1/adaccounts/account-1/stats?page=2"
    }
    context = _CaptureContext(payload)
    monkeypatch.setattr(hourly, "MAX_PAGES", 1)

    with pytest.raises(SnapchatNativeSyncError) as raised:
        asyncio.run(
            _fetch_account_hours(
                context,
                object(),
                "access-token",
                account_id="account-1",
                request_start=datetime.fromisoformat("2026-08-02T00:00:00+03:00"),
                request_end=datetime.fromisoformat("2026-08-03T00:00:00+03:00"),
            )
        )

    assert raised.value.code == "snapchat_account_hour_pagination_incomplete"
    assert raised.value.result["coverage"] == {
        "status": "incomplete",
        "data_state": "unknown_incomplete",
        "expected_requests": 2,
        "completed_requests": 1,
        "reason": "snapchat_account_hour_pagination_incomplete",
    }


class _PerformanceCollection:
    def __init__(self):
        self.updates = []

    async def update_one(self, query, update, upsert=False):
        self.updates.append(
            {
                "query": deepcopy(query),
                "update": deepcopy(update),
                "upsert": upsert,
            }
        )


class _PerformanceDB:
    def __init__(self):
        self.performance = _PerformanceCollection()

    def __getitem__(self, name):
        assert name == "mezan_snapchat_performance_daily_v2"
        return self.performance


class _RefreshContext:
    def __init__(self):
        self.user_id = "tenant-1"
        self.db = _PerformanceDB()
        self.provider_calls = 1

    def now_iso(self):
        return "2026-08-03T12:00:00+00:00"

    async def to_sar(self, value, currency):
        return value


def test_refresh_persists_two_campaign_days_and_exact_account_total(
    monkeypatch,
):
    fetch_calls = []
    rows = [
        {
            "campaign_id": "campaign-1",
            "start_time": "2026-08-01T14:00:00-07:00",
            "end_time": "2026-08-01T15:00:00-07:00",
            "metrics": _metrics(
                spend=5_000_000,
                purchases=2,
                value=10_000_000,
            ),
        },
        {
            "campaign_id": "campaign-1",
            "start_time": "2026-08-01T15:00:00-07:00",
            "end_time": "2026-08-01T16:00:00-07:00",
            "metrics": _metrics(
                spend=2_000_000,
                purchases=1,
                value=4_000_000,
            ),
        },
        {
            "campaign_id": "campaign-2",
            "start_time": "2026-08-01T14:00:00-07:00",
            "end_time": "2026-08-01T15:00:00-07:00",
            "metrics": _metrics(
                spend=1_000_000,
                purchases=1,
                value=3_000_000,
            ),
        },
    ]

    async def fake_fetch(*args, **kwargs):
        fetch_calls.append(deepcopy(kwargs))
        return hourly.AccountHourFetchResult(
            rows=rows,
            errors=[],
            coverage={
                "status": "complete",
                "data_state": "confirmed_data",
                "expected_requests": 1,
                "completed_requests": 1,
            },
        )

    monkeypatch.setattr(hourly, "_fetch_account_hours", fake_fetch)
    context = _RefreshContext()
    result = asyncio.run(
        hourly.refresh_snapchat_account_hours(
            context,
            object(),
            "access-token",
            {
                "ad_account_id": "account-1",
                "mezan_integration_account_id": "integration-account-1",
                "timezone": "America/Los_Angeles",
                "currency": "USD",
            },
            start_date=date(2026, 8, 2),
            end_date=date(2026, 8, 2),
            now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        )
    )

    assert result["rows_saved"] == 3
    assert result["campaign_rows_saved"] == 2
    assert result["source_mode"] == ACCOUNT_REFRESH_SOURCE_MODE
    assert result["campaign_facts_source_mode"] == CAMPAIGN_FACTS_SOURCE_MODE
    assert result["campaign_facts_schema_version"] == (
        CAMPAIGN_FACTS_SCHEMA_VERSION
    )
    assert fetch_calls == [{
        "account_id": "account-1",
        "request_start": datetime.fromisoformat("2026-08-01T14:00:00-07:00"),
        "request_end": datetime.fromisoformat("2026-08-02T14:00:00-07:00"),
        "action_report_time": hourly.ACTION_REPORT_TIME,
        "swipe_attribution_window": hourly.SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": hourly.VIEW_ATTRIBUTION_WINDOW,
    }]
    assert CAMPAIGN_FACTS_SCHEMA_VERSION == 4
    assert len(context.db.performance.updates) == 3

    by_identity = {
        (
            item["query"]["entity_type"],
            item["query"]["external_id"],
        ): item
        for item in context.db.performance.updates
    }
    assert set(by_identity) == {
        ("campaign", "campaign-1"),
        ("campaign", "campaign-2"),
        ("ad_account", "account-1"),
    }
    for item in by_identity.values():
        assert item["query"]["user_id"] == "tenant-1"
        assert item["query"]["ad_account_id"] == "account-1"
        assert item["query"]["date"] == "2026-08-02"
        assert item["upsert"] is True
        document = item["update"]["$set"]
        expected_source = (
            CAMPAIGN_FACTS_SOURCE_MODE
            if item["query"]["entity_type"] == "campaign"
            else ACCOUNT_REFRESH_SOURCE_MODE
        )
        assert document["source_mode"] == expected_source
        assert document["provider_granularity"] == "HOUR"
        assert document["provider_breakdown"] == "campaign"

    campaign_one = by_identity[("campaign", "campaign-1")]["update"]["$set"]
    assert campaign_one["metrics"]["spend"] == 7_000_000
    assert campaign_one["metrics"]["conversion_purchases"] == 3
    assert campaign_one["metrics"]["conversion_purchases_value"] == 14_000_000
    assert campaign_one["provider_window_start"] == (
        "2026-08-01T14:00:00-07:00"
    )
    assert campaign_one["provider_window_end"] == (
        "2026-08-01T16:00:00-07:00"
    )

    account_total = by_identity[("ad_account", "account-1")]["update"]["$set"]
    assert account_total["metrics"]["spend"] == 8_000_000
    assert account_total["metrics"]["conversion_purchases"] == 4
    assert account_total["metrics"]["conversion_purchases_value"] == 17_000_000


def test_scheduler_mode_defers_canonical_account_fact_until_flush(monkeypatch):
    rows = [{
        "campaign_id": "campaign-1",
        "start_time": "2026-08-02T00:00:00+03:00",
        "end_time": "2026-08-02T01:00:00+03:00",
        "metrics": _metrics(spend=5_000_000, purchases=2, value=10_000_000),
    }]

    async def fake_fetch(*args, **kwargs):
        return hourly.AccountHourFetchResult(
            rows=rows,
            errors=[],
            coverage={
                "status": "complete",
                "data_state": "confirmed_data",
                "expected_requests": 1,
                "completed_requests": 1,
            },
        )

    monkeypatch.setattr(hourly, "_fetch_account_hours", fake_fetch)
    context = _RefreshContext()
    context.defer_financial_fact_writes = True
    context.deferred_financial_fact_writes = []

    result = asyncio.run(
        hourly.refresh_snapchat_account_hours(
            context,
            object(),
            "access-token",
            {
                "ad_account_id": "account-1",
                "timezone": "Asia/Riyadh",
                "currency": "SAR",
            },
            start_date=date(2026, 8, 2),
            end_date=date(2026, 8, 2),
            now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        )
    )

    assert result["coverage"]["status"] == "complete"
    assert [
        write["query"]["entity_type"]
        for write in context.db.performance.updates
    ] == ["campaign"]
    assert len(context.deferred_financial_fact_writes) == 1

    assert asyncio.run(
        hourly.flush_deferred_financial_fact_writes(context)
    ) == 1
    assert [
        write["query"]["entity_type"]
        for write in context.db.performance.updates
    ] == ["campaign", "ad_account"]
    assert context.deferred_financial_fact_writes == []


def test_refresh_does_not_replace_stale_fact_with_unproven_zero(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return hourly.AccountHourFetchResult(
            rows=[],
            errors=[],
            coverage={
                "status": "complete",
                "data_state": "confirmed_no_data",
                "expected_requests": 1,
                "completed_requests": 1,
            },
        )

    monkeypatch.setattr(hourly, "_fetch_account_hours", fake_fetch)
    context = _RefreshContext()
    result = asyncio.run(
        hourly.refresh_snapchat_account_hours(
            context,
            object(),
            "access-token",
            {
                "ad_account_id": "account-1",
                "timezone": "Asia/Riyadh",
                "currency": "SAR",
            },
            start_date=date(2026, 8, 2),
            end_date=date(2026, 8, 2),
            now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        )
    )

    assert result["rows_saved"] == 0
    assert result["campaign_rows_saved"] == 0
    assert result["coverage"]["data_state"] == "confirmed_no_data"
    assert context.db.performance.updates == []


def test_refresh_persists_provider_confirmed_zero(monkeypatch):
    rows = [{
        "campaign_id": "campaign-zero",
        "start_time": "2026-08-02T00:00:00+03:00",
        "end_time": "2026-08-02T01:00:00+03:00",
        "metrics": {key: 0 for key in hourly.STAT_FIELDS},
    }]

    async def fake_fetch(*args, **kwargs):
        return hourly.AccountHourFetchResult(
            rows=rows,
            errors=[],
            coverage={
                "status": "complete",
                "data_state": "confirmed_zero",
                "expected_requests": 1,
                "completed_requests": 1,
            },
        )

    monkeypatch.setattr(hourly, "_fetch_account_hours", fake_fetch)
    context = _RefreshContext()
    result = asyncio.run(
        hourly.refresh_snapchat_account_hours(
            context,
            object(),
            "access-token",
            {
                "ad_account_id": "account-1",
                "timezone": "Asia/Riyadh",
                "currency": "SAR",
            },
            start_date=date(2026, 8, 2),
            end_date=date(2026, 8, 2),
            now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        )
    )

    assert result["coverage"]["data_state"] == "confirmed_zero"
    assert result["rows_saved"] == 2
    assert len(context.db.performance.updates) == 2
    assert all(
        value == 0
        for write in context.db.performance.updates
        for value in write["update"]["$set"]["metrics"].values()
    )


def test_current_riyadh_day_completed_window_does_not_cross_now():
    start, end = snapchat_hourly_request_window(
        date(2026, 8, 1),
        date(2026, 8, 2),
        now=datetime(2026, 8, 2, 13, 33, tzinfo=timezone.utc),
    )

    assert start.isoformat() == "2026-08-01T00:00:00+03:00"
    assert end.isoformat() == "2026-08-02T16:00:00+03:00"


def test_dashboard_returns_both_selected_accounts_and_nested_purchases():
    result = summarize_snapchat_dashboard_rows(
        [
            {
                "ad_account_id": "usd-account",
                "date": "2026-08-02",
                "spend_sar": 375.0,
                "spend_native": 100.0,
                "metrics": {"conversion_purchases": 4},
                "purchase_value_sar": 1500.0,
                "updated_at": "2026-08-02T13:00:00+00:00",
            }
        ],
        selected_accounts=[
            {
                "ad_account_id": "usd-account",
                "display_name": "متجر أماسي Self Service",
                "currency": "USD",
                "timezone": "America/Los_Angeles",
            },
            {
                "ad_account_id": "sar-account",
                "display_name": "متجر أماسي سعودي",
                "currency": "SAR",
                "timezone": "Asia/Riyadh",
            },
        ],
        snapshot={"connection_status": "connected"},
        today=date(2026, 8, 2),
    )

    assert result["today"]["orders"] == 4
    assert result["today"]["spend"] == 375.0
    assert result["selected_account_count"] == 2
    assert result["business_timezone"] == "Asia/Riyadh"
    assert result["day_start"] == "00:00"
    assert result["day_end"] == "23:59"
    assert len(result["accounts"]) == 2
    assert result["accounts"][0]["timezone"] == "America/Los_Angeles"
    assert result["accounts"][0]["report_timezone"] == "Asia/Riyadh"
    assert result["accounts"][1]["today"]["spend_sar"] == 0.0
