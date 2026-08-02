from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from integrations_control_center.snapchat_account_hourly_refresh import (
    PROVIDER_BREAKDOWN,
    PROVIDER_GRANULARITY,
    _fetch_account_hours,
    aggregate_account_hours_by_riyadh_day,
    extract_account_hour_rows,
    snapchat_account_request_window,
    snapchat_hourly_request_window,
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

    rows, errors = asyncio.run(
        _fetch_account_hours(
            context,
            object(),
            "access-token",
            account_id="account-1",
            request_start=datetime.fromisoformat("2026-08-01T14:00:00-07:00"),
            request_end=datetime.fromisoformat("2026-08-02T14:00:00-07:00"),
        )
    )

    params = context.calls[0]["params"]
    assert params["granularity"] == PROVIDER_GRANULARITY == "HOUR"
    assert params["breakdown"] == PROVIDER_BREAKDOWN == "campaign"
    assert params["start_time"] == "2026-08-01T14:00:00-07:00"
    assert params["end_time"] == "2026-08-02T14:00:00-07:00"
    assert len(rows) == 1
    assert errors == []


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
