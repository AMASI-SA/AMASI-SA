from zoneinfo import ZoneInfo

from snapchat_v2.salla_outcomes import _localized_order_period_date


def test_real_timestamp_wins_over_store_order_date_for_new_york_boundary():
    order = {
        "created_at": "2026-08-27T03:30:00+00:00",
        "order_date": "2026-08-27",
    }

    local_date, local_created_at, source = _localized_order_period_date(
        order,
        zone=ZoneInfo("America/New_York"),
    )

    assert local_date == "2026-08-26"
    assert local_created_at.startswith("2026-08-26T23:30:00")
    assert source == "created_at_localized_to_account_timezone"


def test_same_timestamp_is_next_day_in_riyadh_but_snapchat_uses_account_timezone():
    order = {
        "created_at": "2026-08-27T03:30:00+00:00",
        "order_date": "2026-08-27",
    }

    new_york_date, _, _ = _localized_order_period_date(
        order,
        zone=ZoneInfo("America/New_York"),
    )
    riyadh_date, _, _ = _localized_order_period_date(
        order,
        zone=ZoneInfo("Asia/Riyadh"),
    )

    assert new_york_date == "2026-08-26"
    assert riyadh_date == "2026-08-27"


def test_order_date_is_only_fallback_when_timestamp_is_unavailable():
    local_date, local_created_at, source = _localized_order_period_date(
        {"order_date": "2026-08-27"},
        zone=ZoneInfo("America/New_York"),
    )

    assert local_date == "2026-08-27"
    assert local_created_at == "2026-08-27"
    assert source == "order_date_fallback"
