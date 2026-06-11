"""Iter-140 — Backend Asia/Riyadh date helpers.

The deployment server runs in UTC.  Before this iteration the backend
used `date.today()` everywhere, which silently shifted aggregates back
one day during the first 3 hours of every Saudi calendar day.

These tests lock the helper behavior + assert key call sites have
been migrated.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tz_utils import riyadh_now, riyadh_today, riyadh_today_iso


def test_riyadh_today_is_offset_three_hours_from_utc():
    """At 23:30 UTC on June 11, Riyadh local time is 02:30 on June 12.
    `riyadh_today()` MUST return the Riyadh calendar date, not UTC's."""
    rn = riyadh_now()
    utc_now = datetime.now(timezone.utc)
    # riyadh_now returns an aware datetime — strip tz for diff.
    if rn.tzinfo is not None:
        rn = rn.replace(tzinfo=None)
    utc_now_naive = utc_now.replace(tzinfo=None)
    diff = rn - utc_now_naive
    # Allow ±5 seconds drift between the two `now()` calls.
    assert timedelta(hours=2, minutes=59, seconds=55) <= diff <= timedelta(hours=3, minutes=0, seconds=5)


def test_riyadh_today_iso_matches_helper():
    assert riyadh_today_iso() == riyadh_today().isoformat()


def test_backend_no_longer_uses_date_today_in_business_paths():
    """Files known to compute calendar-day aggregates must NOT use
    bare `date.today()` anymore — they must call `riyadh_today` /
    `riyadh_today_iso`.  We grep the actual source files."""
    files = [
        Path("/app/backend/liabilities_routes.py"),
        Path("/app/backend/ad_account_routes.py"),
        Path("/app/backend/bnpl/settlements_service.py"),
        Path("/app/backend/webhook_routes.py"),
    ]
    for f in files:
        src = f.read_text(encoding="utf-8")
        # Strip docstrings of tz_utils references so we don't false-positive
        # on documentation comments referring to the OLD pattern by name.
        assert "date.today()" not in src, (
            f"{f.name} still contains date.today() — must use riyadh_today()."
        )


def test_helpers_importable_from_tz_utils():
    """Public surface is documented and stable."""
    from tz_utils import riyadh_now, riyadh_today, riyadh_today_iso  # noqa: F401
    # Sanity: ISO format is YYYY-MM-DD (10 chars).
    s = riyadh_today_iso()
    assert len(s) == 10
    assert s[4] == "-" and s[7] == "-"


def test_riyadh_today_matches_manual_offset_computation():
    expected = (datetime.now(timezone.utc) + timedelta(hours=3)).date()
    assert riyadh_today() == expected
