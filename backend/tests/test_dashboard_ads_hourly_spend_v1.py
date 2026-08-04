from datetime import datetime, timezone

from integrations_control_center.dashboard_ads_hourly_spend_routes import (
    aggregate_riyadh_hourly_spend,
)


def test_aggregates_multiple_snapchat_accounts_into_riyadh_hours():
    rows = [
        {
            "ad_account_id": "riyadh-account",
            "hour_start_utc": "2026-08-03T21:00:00+00:00",
            "spend_sar": 100,
        },
        {
            "ad_account_id": "us-account",
            "hour_start_utc": "2026-08-03T21:00:00+00:00",
            "spend_sar": 25.25,
        },
        {
            "ad_account_id": "riyadh-account",
            "hour_start_utc": "2026-08-04T00:00:00+00:00",
            "spend_sar": 50,
        },
        {
            "ad_account_id": "outside-day",
            "hour_start_utc": "2026-08-04T21:00:00+00:00",
            "spend_sar": 999,
        },
    ]

    series = aggregate_riyadh_hourly_spend(
        rows,
        date_string="2026-08-04",
        now=datetime(2026, 8, 4, 9, 30, tzinfo=timezone.utc),
    )

    assert len(series) == 24
    assert series[0]["hour"] == "00:00"
    assert series[0]["snapchat"] == 125.25
    assert series[3]["snapchat"] == 50
    assert series[0]["observed"] is True
    assert series[1]["observed"] is False
    assert series[12]["is_future"] is False
    assert series[13]["is_future"] is True
    assert all(point["meta"] is None for point in series)
    assert all(point["tiktok"] is None for point in series)
    assert all("booked_ad_expense_sar" not in point for point in series)


def test_missing_hourly_facts_are_not_reported_as_zero_spend():
    series = aggregate_riyadh_hourly_spend(
        [],
        date_string="2026-08-03",
        now=datetime(2026, 8, 4, 9, 30, tzinfo=timezone.utc),
    )

    assert len(series) == 24
    assert all(point["snapchat"] is None for point in series)
    assert all(point["observed"] is False for point in series)
    assert all(point["is_future"] is False for point in series)
