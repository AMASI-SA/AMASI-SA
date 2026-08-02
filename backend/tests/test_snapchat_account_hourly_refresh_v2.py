from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from integrations_control_center.snapchat_account_hourly_refresh import (
    PROVIDER_BREAKDOWN,
    PROVIDER_GRANULARITY,
    _fetch_account_day_total,
    aggregate_account_hours_by_riyadh_day,
    aggregate_account_total_rows,
    extract_account_total_rows,
    snapchat_hourly_request_window,
    snapchat_total_request_window,
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


def test_los_angeles_hours_follow_riyadh_midnight():
    # 14:00 PDT = 00:00 next day in Riyadh. The Jul-31 14:00 PDT point is
    # Aug 1 in Riyadh and must not leak into Jul 31.
    rows = [
        {
            "start_time": "2026-07-30T14:00:00-07:00",
            "end_time": "2026-07-30T15:00:00-07:00",
            "metrics": _metrics(
                spend=1_000_000,
                purchases=1,
                value=3_000_000,
            ),
        },
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
    bucket = daily["2026-07-31"]
    assert bucket["rows"] == 2
    assert bucket["sums"]["spend"] == 3_000_000
    assert bucket["sums"]["conversion_purchases"] == 3
    assert bucket["sums"]["conversion_purchases_value"] == 9_000_000


def test_dashboard_returns_both_selected_accounts_and_nested_purchases():
    result = summarize_snapchat_dashboard_rows(
        [
            {
                "ad_account_id": "usd-account",
                "date": "2026-07-31",
                "spend_sar": 375.0,
                "spend_native": 100.0,
                "metrics": {
                    "conversion_purchases": 4,
                },
                "purchase_value_sar": 1500.0,
                "updated_at": "2026-07-31T18:00:00+00:00",
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
        today=date(2026, 7, 31),
    )

    assert result["today"]["orders"] == 4
    assert result["today"]["spend"] == 375.0
    assert result["today"]["revenue"] == 1500.0
    assert result["selected_account_count"] == 2
    assert result["business_timezone"] == "Asia/Riyadh"
    assert result["day_start"] == "00:00"
    assert result["day_end"] == "23:59"

    assert len(result["accounts"]) == 2
    assert result["accounts"][0]["today"]["spend_sar"] == 375.0
    assert result["accounts"][1]["today"]["spend_sar"] == 0.0
    assert result["accounts"][0]["report_timezone"] == "Asia/Riyadh"
    assert result["accounts"][1]["day_start"] == "00:00"


def test_current_riyadh_day_ends_at_last_completed_hour():
    start, end = snapchat_hourly_request_window(
        date(2026, 8, 1),
        date(2026, 8, 2),
        now=datetime(2026, 8, 2, 12, 31, tzinfo=timezone.utc),
    )

    assert start.isoformat() == "2026-08-01T00:00:00+03:00"
    assert end.isoformat() == "2026-08-02T15:00:00+03:00"
    assert end <= datetime(2026, 8, 2, 12, 31, tzinfo=timezone.utc).astimezone(end.tzinfo)


def test_midnight_window_does_not_request_the_following_day():
    start, end = snapchat_hourly_request_window(
        date(2026, 8, 1),
        date(2026, 8, 2),
        now=datetime(2026, 8, 1, 21, 10, tzinfo=timezone.utc),
    )

    assert start.isoformat() == "2026-08-01T00:00:00+03:00"
    assert end.isoformat() == "2026-08-02T00:00:00+03:00"


def test_historical_range_keeps_full_riyadh_days():
    start, end = snapchat_hourly_request_window(
        date(2026, 7, 30),
        date(2026, 7, 31),
        now=datetime(2026, 8, 2, 12, 31, tzinfo=timezone.utc),
    )

    assert start.isoformat() == "2026-07-30T00:00:00+03:00"
    assert end.isoformat() == "2026-08-01T00:00:00+03:00"



def _total_payload(*campaign_stats, status="SUCCESS"):
    return {
        "request_status": "SUCCESS",
        "total_stats": [{
            "sub_request_status": status,
            "total_stat": {
                "start_time": "2026-08-02T00:00:00+03:00",
                "end_time": "2026-08-02T15:31:00+03:00",
                "breakdown_stats": {
                    "campaign": [
                        {"id": f"campaign-{index}", "stats": stats}
                        for index, stats in enumerate(campaign_stats, start=1)
                    ],
                },
            },
        }],
    }


def test_total_window_uses_full_history_and_current_instant():
    historical = snapchat_total_request_window(
        date(2026, 8, 1),
        now=datetime(2026, 8, 2, 12, 31, tzinfo=timezone.utc),
    )
    current = snapchat_total_request_window(
        date(2026, 8, 2),
        now=datetime(2026, 8, 2, 12, 31, tzinfo=timezone.utc),
    )
    future = snapchat_total_request_window(
        date(2026, 8, 3),
        now=datetime(2026, 8, 2, 12, 31, tzinfo=timezone.utc),
    )

    assert historical[0].isoformat() == "2026-08-01T00:00:00+03:00"
    assert historical[1].isoformat() == "2026-08-02T00:00:00+03:00"
    assert current[0].isoformat() == "2026-08-02T00:00:00+03:00"
    assert current[1].isoformat() == "2026-08-02T15:31:00+03:00"
    assert future is None


def test_campaign_total_breakdown_aggregates_full_account_metrics():
    start = datetime.fromisoformat("2026-08-02T00:00:00+03:00")
    end = datetime.fromisoformat("2026-08-02T15:31:00+03:00")
    rows, errors, successful = extract_account_total_rows(
        _total_payload(
            _metrics(spend=5_000_000, purchases=2, value=10_000_000),
            _metrics(spend=1_000_000, purchases=1, value=4_000_000),
        ),
        request_start=start,
        request_end=end,
    )
    metrics = aggregate_account_total_rows(rows)

    assert errors == []
    assert successful == 1
    assert metrics["spend"] == 6_000_000
    assert metrics["impressions"] == 200
    assert metrics["swipes"] == 20
    assert metrics["conversion_purchases"] == 3
    assert metrics["conversion_purchases_value"] == 14_000_000


def test_campaign_total_breakdown_keeps_missing_conversion_unknown():
    start = datetime.fromisoformat("2026-08-02T00:00:00+03:00")
    end = datetime.fromisoformat("2026-08-02T15:31:00+03:00")
    complete = _metrics(spend=5_000_000, purchases=2, value=10_000_000)
    missing = _metrics(spend=1_000_000)
    missing.pop("conversion_purchases")
    missing.pop("conversion_purchases_value")
    rows, _, _ = extract_account_total_rows(
        _total_payload(complete, missing),
        request_start=start,
        request_end=end,
    )
    metrics = aggregate_account_total_rows(rows)

    assert metrics["spend"] == 6_000_000
    assert metrics["conversion_purchases"] is None
    assert metrics["conversion_purchases_value"] is None


class _CaptureContext:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def get_json(self, client, url, *, headers, params=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        return self.payload


@pytest.mark.asyncio
async def test_five_minute_request_uses_total_campaign_breakdown():
    context = _CaptureContext(
        _total_payload(_metrics(spend=5_000_000, purchases=2, value=10_000_000))
    )
    start = datetime.fromisoformat("2026-08-02T00:00:00+03:00")
    end = datetime.fromisoformat("2026-08-02T15:31:00+03:00")

    metrics, errors = await _fetch_account_day_total(
        context,
        object(),
        "access-token",
        account_id="account-1",
        request_start=start,
        request_end=end,
    )

    params = context.calls[0]["params"]
    assert params["granularity"] == PROVIDER_GRANULARITY == "TOTAL"
    assert params["breakdown"] == PROVIDER_BREAKDOWN == "campaign"
    assert params["start_time"] == "2026-08-02T00:00:00+03:00"
    assert params["end_time"] == "2026-08-02T15:31:00+03:00"
    assert metrics["spend"] == 5_000_000
    assert metrics["conversion_purchases"] == 2
    assert errors == []


def test_subrequest_failure_preserves_provider_error_details():
    start = datetime.fromisoformat("2026-08-02T00:00:00+03:00")
    end = datetime.fromisoformat("2026-08-02T15:31:00+03:00")
    payload = {
        "request_status": "SUCCESS",
        "total_stats": [{
            "sub_request_status": "ERROR",
            "error_code": "E_INVALID_FIELDS",
            "error_message": "Unsupported metrics for this request.",
        }],
    }
    rows, errors, successful = extract_account_total_rows(
        payload,
        request_start=start,
        request_end=end,
    )

    assert rows == []
    assert successful == 0
    assert errors[0]["code"] == "E_INVALID_FIELDS"
    assert errors[0]["message"] == "Unsupported metrics for this request."
