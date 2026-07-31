from __future__ import annotations

from datetime import date, timezone

from integrations_control_center.snapchat_native_performance_sync import (
    DAY_BOUNDARY_MODE,
    SOURCE_GRANULARITY,
    aggregate_hourly_rows_by_riyadh_day,
    riyadh_report_window,
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


def test_riyadh_report_window_starts_at_midnight_saudi_time():
    start, end = riyadh_report_window(
        date(2026, 7, 31),
        date(2026, 7, 31),
    )
    assert start.tzinfo == timezone.utc
    assert end.tzinfo == timezone.utc
    assert start.isoformat() == "2026-07-30T21:00:00+00:00"
    assert end.isoformat() == "2026-07-31T21:00:00+00:00"
    assert (end - start).total_seconds() == 24 * 60 * 60


def test_los_angeles_hours_are_grouped_into_riyadh_day_not_provider_day():
    # 14:00 PDT = 00:00 next day in Riyadh.  The final point at 14:00 PDT on
    # Jul 31 belongs to Aug 1 Riyadh and must not leak into Jul 31.
    rows = [
        {
            "external_id": "campaign-a",
            "start_time": "2026-07-30T14:00:00-07:00",
            "end_time": "2026-07-30T15:00:00-07:00",
            "metrics": _metrics(spend=1_000_000, purchases=1, value=3_000_000),
        },
        {
            "external_id": "campaign-a",
            "start_time": "2026-07-31T13:00:00-07:00",
            "end_time": "2026-07-31T14:00:00-07:00",
            "metrics": _metrics(spend=2_000_000, purchases=2, value=6_000_000),
        },
        {
            "external_id": "campaign-a",
            "start_time": "2026-07-31T14:00:00-07:00",
            "end_time": "2026-07-31T15:00:00-07:00",
            "metrics": _metrics(spend=9_000_000, purchases=9, value=9_000_000),
        },
    ]

    campaigns, accounts = aggregate_hourly_rows_by_riyadh_day(
        rows,
        start_date=date(2026, 7, 31),
        end_date=date(2026, 7, 31),
    )

    assert set(campaigns) == {("campaign-a", "2026-07-31")}
    campaign = campaigns[("campaign-a", "2026-07-31")]
    assert campaign["rows"] == 2
    assert campaign["sums"]["spend"] == 3_000_000
    assert campaign["sums"]["conversion_purchases"] == 3

    assert set(accounts) == {"2026-07-31"}
    account = accounts["2026-07-31"]
    assert account["rows"] == 1
    assert account["sums"]["spend"] == 3_000_000
    assert account["sums"]["conversion_purchases"] == 3


def test_two_campaigns_roll_up_once_at_account_level():
    rows = [
        {
            "external_id": "campaign-a",
            "start_time": "2026-07-31T00:00:00+03:00",
            "end_time": "2026-07-31T01:00:00+03:00",
            "metrics": _metrics(spend=1_000_000),
        },
        {
            "external_id": "campaign-b",
            "start_time": "2026-07-31T00:00:00+03:00",
            "end_time": "2026-07-31T01:00:00+03:00",
            "metrics": _metrics(spend=2_000_000),
        },
    ]

    campaigns, accounts = aggregate_hourly_rows_by_riyadh_day(
        rows,
        start_date=date(2026, 7, 31),
        end_date=date(2026, 7, 31),
    )

    assert len(campaigns) == 2
    assert accounts["2026-07-31"]["rows"] == 2
    assert accounts["2026-07-31"]["sums"]["spend"] == 3_000_000


def test_contract_uses_hourly_riyadh_boundary():
    assert SOURCE_GRANULARITY == "HOUR"
    assert DAY_BOUNDARY_MODE == "riyadh_midnight_hourly_aggregate"
