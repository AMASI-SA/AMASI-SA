"""Iter-177 — Asia/Riyadh timezone utilities tests.

Validates the helpers that map Riyadh wall-clock boundaries to UTC
datetimes (used by MongoDB range queries). Saudi Arabia is fixed at
UTC+3 (no DST), so we can hard-code expected offsets.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone

import pytest

sys.path.insert(0, "/app/backend")
from tz_utils import (  # noqa: E402
    RIYADH_TZ,
    riyadh_end_of_day_utc,
    riyadh_end_of_month_utc,
    riyadh_end_of_year_utc,
    riyadh_last_n_days_range_utc,
    riyadh_now_aware,
    riyadh_start_of_day_utc,
    riyadh_start_of_month_utc,
    riyadh_start_of_year_utc,
    riyadh_this_month_range_utc,
    riyadh_this_year_range_utc,
    riyadh_today,
    riyadh_today_iso,
    riyadh_today_range_utc,
    riyadh_yesterday_range_utc,
    utc_to_riyadh,
    utc_to_riyadh_iso,
)


def test_riyadh_today_is_calendar_day_in_ksa():
    """``riyadh_today()`` must reflect the calendar date a merchant
    in Riyadh experiences right now."""
    d = riyadh_today()
    iso = riyadh_today_iso()
    # Riyadh = UTC+3; the date in Riyadh is never more than 1 day
    # ahead of UTC date.
    delta_days = (d - datetime.now(timezone.utc).date()).days
    assert delta_days in (0, 1), (
        f"Riyadh date {d} is too far from UTC today: delta={delta_days}"
    )
    assert iso == d.isoformat()


def test_start_of_day_utc_is_midnight_in_riyadh():
    """00:00 Riyadh on 2026-02-14 = 21:00 UTC on 2026-02-13."""
    start = riyadh_start_of_day_utc("2026-02-14")
    assert start.tzinfo is not None
    assert start.astimezone(timezone.utc).isoformat() == \
        "2026-02-13T21:00:00+00:00"


def test_end_of_day_utc_is_just_before_next_riyadh_midnight():
    """23:59:59.999999 Riyadh on 2026-02-14 = 20:59:59.999999 UTC
    on 2026-02-14."""
    end = riyadh_end_of_day_utc("2026-02-14")
    assert end.astimezone(timezone.utc).isoformat() == \
        "2026-02-14T20:59:59.999999+00:00"


def test_start_and_end_of_day_form_24h_window():
    start = riyadh_start_of_day_utc(date(2026, 2, 14))
    end = riyadh_end_of_day_utc(date(2026, 2, 14))
    diff_seconds = (end - start).total_seconds()
    # 24h minus 1 microsecond
    assert 86399.0 < diff_seconds < 86400.0


def test_riyadh_start_of_day_accepts_string_and_date_and_none():
    today = riyadh_today()
    via_none = riyadh_start_of_day_utc()
    via_date = riyadh_start_of_day_utc(today)
    via_str = riyadh_start_of_day_utc(today.isoformat())
    assert via_none == via_date == via_str


def test_start_of_month_utc_starts_first_day_at_riyadh_midnight():
    start = riyadh_start_of_month_utc(2026, 3)
    # 00:00 Riyadh on 2026-03-01 = 21:00 UTC on 2026-02-28
    assert start.astimezone(timezone.utc).isoformat() == \
        "2026-02-28T21:00:00+00:00"


def test_end_of_month_utc_for_february_2026():
    """February 2026 has 28 days (non-leap year)."""
    end = riyadh_end_of_month_utc(2026, 2)
    # 23:59:59.999999 Riyadh on 2026-02-28 = 20:59:59.999999 UTC
    assert end.astimezone(timezone.utc).isoformat() == \
        "2026-02-28T20:59:59.999999+00:00"


def test_end_of_month_utc_for_december_rolls_into_next_year():
    end = riyadh_end_of_month_utc(2026, 12)
    assert end.astimezone(timezone.utc).isoformat() == \
        "2026-12-31T20:59:59.999999+00:00"


def test_end_of_month_utc_for_leap_year_february():
    """February 2024 has 29 days (leap year)."""
    end = riyadh_end_of_month_utc(2024, 2)
    assert end.astimezone(timezone.utc).isoformat() == \
        "2024-02-29T20:59:59.999999+00:00"


def test_start_and_end_of_year_utc():
    start = riyadh_start_of_year_utc(2026)
    end = riyadh_end_of_year_utc(2026)
    assert start.astimezone(timezone.utc).isoformat() == \
        "2025-12-31T21:00:00+00:00"
    assert end.astimezone(timezone.utc).isoformat() == \
        "2026-12-31T20:59:59.999999+00:00"


def test_today_range_matches_start_and_end_of_day():
    s, e = riyadh_today_range_utc()
    assert s == riyadh_start_of_day_utc(riyadh_today())
    assert e == riyadh_end_of_day_utc(riyadh_today())


def test_yesterday_range_is_yesterday_in_riyadh():
    from datetime import timedelta as td
    s, e = riyadh_yesterday_range_utc()
    yesterday = riyadh_today() - td(days=1)
    assert s == riyadh_start_of_day_utc(yesterday)
    assert e == riyadh_end_of_day_utc(yesterday)


def test_last_n_days_range_covers_n_full_days():
    s, e = riyadh_last_n_days_range_utc(7)
    # 7 days = 7*24h - 1 microsecond
    diff_seconds = (e - s).total_seconds()
    assert 604799.0 < diff_seconds < 604800.0


def test_last_n_days_range_rejects_invalid_n():
    with pytest.raises(ValueError):
        riyadh_last_n_days_range_utc(0)


def test_this_month_and_this_year_ranges_match_helpers():
    today = riyadh_today()
    sm, em = riyadh_this_month_range_utc()
    assert sm == riyadh_start_of_month_utc(today.year, today.month)
    assert em == riyadh_end_of_month_utc(today.year, today.month)
    sy, ey = riyadh_this_year_range_utc()
    assert sy == riyadh_start_of_year_utc(today.year)
    assert ey == riyadh_end_of_year_utc(today.year)


def test_riyadh_now_aware_has_riyadh_offset():
    n = riyadh_now_aware()
    assert n.tzinfo is not None
    assert n.utcoffset().total_seconds() == 3 * 3600


def test_utc_to_riyadh_handles_naive_and_aware():
    utc_naive = datetime(2026, 2, 14, 0, 0, 0)
    utc_aware = datetime(2026, 2, 14, 0, 0, 0, tzinfo=timezone.utc)
    r_naive = utc_to_riyadh(utc_naive)
    r_aware = utc_to_riyadh(utc_aware)
    # Both must produce the same Riyadh wall-clock = 03:00
    assert r_naive.hour == 3
    assert r_aware.hour == 3
    assert r_naive == r_aware
    iso = utc_to_riyadh_iso(utc_aware)
    assert "+03:00" in iso


def test_window_query_correctness_around_midnight_ksa():
    """At 23:30 UTC, a Riyadh merchant is already in the NEXT day
    (02:30 KSA). The 'today range' must include the merchant's
    actual entries from after midnight KSA."""
    # We can't freeze time without freezegun, so just assert that
    # the today range covers a 24h window AROUND now and that
    # ``riyadh_now_aware()`` falls within it.
    start, end = riyadh_today_range_utc()
    now_utc = datetime.now(timezone.utc)
    assert start <= now_utc <= end
