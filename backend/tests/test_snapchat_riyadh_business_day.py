from datetime import date

from integrations_control_center.snapchat_native_performance_sync import (
    riyadh_business_window,
    riyadh_date_for_point,
)


def test_business_window_is_midnight_to_midnight_in_riyadh():
    start, end = riyadh_business_window(
        date(2026, 7, 31),
        date(2026, 7, 31),
    )

    assert start.isoformat() == "2026-07-31T00:00:00+03:00"
    assert end.isoformat() == "2026-08-01T00:00:00+03:00"


def test_los_angeles_hours_are_bucketed_into_riyadh_date():
    # July uses PDT (UTC-7). 14:00 on July 30 is exactly midnight in Riyadh.
    assert riyadh_date_for_point(
        "2026-07-30T14:00:00-07:00"
    ) == "2026-07-31"
    assert riyadh_date_for_point(
        "2026-07-31T13:59:59-07:00"
    ) == "2026-07-31"

    # The next Los Angeles hour starts the next Riyadh business day.
    assert riyadh_date_for_point(
        "2026-07-31T14:00:00-07:00"
    ) == "2026-08-01"


def test_riyadh_account_keeps_its_native_midnight_date():
    assert riyadh_date_for_point(
        "2026-07-31T00:00:00+03:00"
    ) == "2026-07-31"
