from __future__ import annotations

from datetime import date

from integrations_control_center.snapchat_account_hourly_refresh import (
    aggregate_account_hours_by_riyadh_day,
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
