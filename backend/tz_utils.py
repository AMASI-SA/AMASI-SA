"""Asia/Riyadh date / time utilities for the backend.

Background
==========
The deployment server runs in UTC. Bare ``date.today()`` therefore
returns yesterday's date during the first 3 hours of a Saudi
calendar day (00:00–03:00 KSA = 21:00–24:00 UTC of the previous
day). Bare ``datetime.utcnow()`` or ``datetime.now()`` confuses
"today" with "today UTC" for every date-bucketed report.

The MEZAN merchant explicitly required (Iter-177): every UI date,
every "today / yesterday / this month" filter, and every
cron-job-execution time MUST be Asia/Riyadh. Storage in MongoDB
stays UTC (the canonical instant), but any range computation that
maps an instant to a *calendar* day must use this module.

Public API
==========
Calendar helpers
----------------
``riyadh_today()``       — calendar date in Riyadh.
``riyadh_today_iso()``   — ``YYYY-MM-DD`` string in Riyadh.
``riyadh_now()``         — naive datetime with the Riyadh wall clock
                           (legacy; prefer ``riyadh_now_aware()``).
``riyadh_now_aware()``   — timezone-aware datetime in Riyadh.

UTC instant helpers (for MongoDB range queries)
-----------------------------------------------
``riyadh_start_of_day_utc(d)``   — 00:00 Riyadh of ``d`` expressed
                                   as a UTC ``datetime``.
``riyadh_end_of_day_utc(d)``     — 23:59:59.999999 Riyadh of ``d``.
``riyadh_start_of_month_utc(y, m)`` / ``..._end_of_month_utc(y, m)``
``riyadh_start_of_year_utc(y)``  / ``..._end_of_year_utc(y)``

Pre-rolled ranges
-----------------
``riyadh_today_range_utc()``         → ``(start_utc, end_utc)``
``riyadh_yesterday_range_utc()``
``riyadh_last_n_days_range_utc(n)``
``riyadh_this_month_range_utc()``
``riyadh_this_year_range_utc()``

Conversion
----------
``utc_to_riyadh(dt)``  — UTC ``datetime`` → tz-aware Riyadh.
``utc_to_riyadh_iso(dt)`` — UTC ``datetime`` → ISO string with
                            ``+03:00`` offset.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Tuple, Union

# ── Timezone definition ─────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    RIYADH_TZ = ZoneInfo("Asia/Riyadh")
except Exception:  # pragma: no cover — fallback if tzdata is missing
    RIYADH_TZ = timezone(timedelta(hours=3))

# Saudi Arabia observes no DST; the offset is a constant +03:00.
RIYADH_UTC_OFFSET = timedelta(hours=3)

DEFAULT_TIMEZONE = "Asia/Riyadh"


# ────────────────────────────────────────────────────────────────
# Calendar helpers
# ────────────────────────────────────────────────────────────────
def riyadh_now_aware() -> datetime:
    """Current instant rendered in Asia/Riyadh as a tz-aware
    datetime. Prefer this for new code."""
    return datetime.now(timezone.utc).astimezone(RIYADH_TZ)


def riyadh_now() -> datetime:
    """Current wall-clock instant in Asia/Riyadh as a NAIVE
    datetime (the +03:00 offset has already been applied).
    Kept for backwards compatibility — new code should use
    ``riyadh_now_aware()``."""
    return (datetime.now(timezone.utc) + RIYADH_UTC_OFFSET).replace(
        tzinfo=None
    )


def riyadh_today() -> date:
    """Calendar date in Asia/Riyadh — replaces ``date.today()``."""
    return riyadh_now_aware().date()


def riyadh_today_iso() -> str:
    """YYYY-MM-DD calendar date in Riyadh — replaces
    ``date.today().isoformat()``."""
    return riyadh_today().isoformat()


# ────────────────────────────────────────────────────────────────
# UTC instant helpers — convert Riyadh wall-clock boundaries into
# UTC ``datetime`` objects for MongoDB range queries.
# ────────────────────────────────────────────────────────────────
def _coerce_date(d: Union[str, date, datetime, None]) -> date:
    """Accept ``YYYY-MM-DD`` / ``date`` / ``datetime`` / ``None``
    and return a ``date``. ``None`` resolves to Riyadh today."""
    if d is None:
        return riyadh_today()
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d))


def riyadh_start_of_day_utc(d: Union[str, date, None] = None) -> datetime:
    """00:00 Riyadh of ``d`` (default = Riyadh today) expressed
    as a UTC tz-aware ``datetime``."""
    cal = _coerce_date(d)
    riyadh_midnight = datetime.combine(cal, time(0, 0), tzinfo=RIYADH_TZ)
    return riyadh_midnight.astimezone(timezone.utc)


def riyadh_end_of_day_utc(d: Union[str, date, None] = None) -> datetime:
    """23:59:59.999999 Riyadh of ``d`` as a UTC tz-aware
    ``datetime`` (inclusive end-of-day for ``$lte`` queries)."""
    cal = _coerce_date(d)
    riyadh_eod = datetime.combine(
        cal, time(23, 59, 59, 999_999), tzinfo=RIYADH_TZ
    )
    return riyadh_eod.astimezone(timezone.utc)


def riyadh_start_of_month_utc(
    year: Optional[int] = None, month: Optional[int] = None
) -> datetime:
    """00:00 Riyadh on the 1st of ``year``-``month`` as UTC."""
    today = riyadh_today()
    y = year if year is not None else today.year
    m = month if month is not None else today.month
    return riyadh_start_of_day_utc(date(y, m, 1))


def riyadh_end_of_month_utc(
    year: Optional[int] = None, month: Optional[int] = None
) -> datetime:
    """23:59:59.999999 Riyadh on the LAST day of ``year``-``month``
    as UTC."""
    today = riyadh_today()
    y = year if year is not None else today.year
    m = month if month is not None else today.month
    # First day of next month, minus 1 day → last day of this month.
    if m == 12:
        ny, nm = y + 1, 1
    else:
        ny, nm = y, m + 1
    last_day = date(ny, nm, 1) - timedelta(days=1)
    return riyadh_end_of_day_utc(last_day)


def riyadh_start_of_year_utc(year: Optional[int] = None) -> datetime:
    """00:00 Riyadh on Jan 1 of ``year`` (default current) as UTC."""
    y = year if year is not None else riyadh_today().year
    return riyadh_start_of_day_utc(date(y, 1, 1))


def riyadh_end_of_year_utc(year: Optional[int] = None) -> datetime:
    """23:59:59.999999 Riyadh on Dec 31 of ``year`` as UTC."""
    y = year if year is not None else riyadh_today().year
    return riyadh_end_of_day_utc(date(y, 12, 31))


# ────────────────────────────────────────────────────────────────
# Pre-rolled ranges — common report buckets.
# Each returns a ``(start_utc, end_utc)`` tuple ready for
# ``{"$gte": start_utc, "$lte": end_utc}`` queries.
# ────────────────────────────────────────────────────────────────
def riyadh_today_range_utc() -> Tuple[datetime, datetime]:
    today = riyadh_today()
    return riyadh_start_of_day_utc(today), riyadh_end_of_day_utc(today)


def riyadh_yesterday_range_utc() -> Tuple[datetime, datetime]:
    yesterday = riyadh_today() - timedelta(days=1)
    return (
        riyadh_start_of_day_utc(yesterday),
        riyadh_end_of_day_utc(yesterday),
    )


def riyadh_last_n_days_range_utc(n: int) -> Tuple[datetime, datetime]:
    """Window from N-1 days ago (00:00 Riyadh) through today
    (23:59:59 Riyadh) — i.e. exactly N calendar days."""
    if n < 1:
        raise ValueError("n must be >= 1")
    today = riyadh_today()
    start_day = today - timedelta(days=n - 1)
    return (
        riyadh_start_of_day_utc(start_day),
        riyadh_end_of_day_utc(today),
    )


def riyadh_this_month_range_utc() -> Tuple[datetime, datetime]:
    today = riyadh_today()
    return (
        riyadh_start_of_month_utc(today.year, today.month),
        riyadh_end_of_month_utc(today.year, today.month),
    )


def riyadh_this_year_range_utc() -> Tuple[datetime, datetime]:
    today = riyadh_today()
    return riyadh_start_of_year_utc(today.year), riyadh_end_of_year_utc(today.year)


# ────────────────────────────────────────────────────────────────
# Conversion helpers
# ────────────────────────────────────────────────────────────────
def utc_to_riyadh(dt: datetime) -> datetime:
    """Convert any datetime (naive=treated as UTC) to a tz-aware
    Riyadh datetime."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(RIYADH_TZ)


def utc_to_riyadh_iso(dt: datetime) -> str:
    """ISO-8601 string of ``dt`` in Riyadh time (``+03:00`` suffix)."""
    return utc_to_riyadh(dt).isoformat()


def riyadh_date_from_utc(dt: datetime) -> date:
    """Calendar date in Riyadh corresponding to a UTC instant."""
    return utc_to_riyadh(dt).date()


__all__ = [
    "DEFAULT_TIMEZONE",
    "RIYADH_TZ",
    "RIYADH_UTC_OFFSET",
    "riyadh_date_from_utc",
    "riyadh_end_of_day_utc",
    "riyadh_end_of_month_utc",
    "riyadh_end_of_year_utc",
    "riyadh_last_n_days_range_utc",
    "riyadh_now",
    "riyadh_now_aware",
    "riyadh_start_of_day_utc",
    "riyadh_start_of_month_utc",
    "riyadh_start_of_year_utc",
    "riyadh_this_month_range_utc",
    "riyadh_this_year_range_utc",
    "riyadh_today",
    "riyadh_today_iso",
    "riyadh_today_range_utc",
    "riyadh_yesterday_range_utc",
    "utc_to_riyadh",
    "utc_to_riyadh_iso",
]
