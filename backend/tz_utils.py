"""Iter-140 — Asia/Riyadh date utilities for the backend.

The deployment server runs in UTC.  `date.today()` therefore returns
yesterday's date during the first 3 hours of a Saudi calendar day
(00:00–03:00 KSA = 21:00–24:00 UTC of the previous day).  That bug
silently shifted:
  • daily expense / financial position aggregations
  • salary accrual day counts
  • ad-account half-hour sync target date
  • weekly BNPL settlement period end
to the wrong day for users entering data after midnight.

Use `riyadh_today()` everywhere a calendar day is needed.  For full
timestamps (audit logs, _now()) keep using
`datetime.now(timezone.utc)` — only the *date slice* needs the offset.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# Saudi Arabia has no DST, the offset is a constant +03:00.
RIYADH_UTC_OFFSET = timedelta(hours=3)


def riyadh_now() -> datetime:
    """Current wall-clock instant in Asia/Riyadh as a naive datetime
    (offset already applied).  Suitable for `.date()` slicing."""
    return datetime.now(timezone.utc) + RIYADH_UTC_OFFSET


def riyadh_today() -> date:
    """Calendar date in Asia/Riyadh — replaces `date.today()`."""
    return riyadh_now().date()


def riyadh_today_iso() -> str:
    """YYYY-MM-DD calendar date in Asia/Riyadh — replaces
    `date.today().isoformat()`."""
    return riyadh_today().isoformat()
