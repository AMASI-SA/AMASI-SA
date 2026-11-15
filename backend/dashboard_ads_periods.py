"""Strict, read-only Riyadh time evidence for the dashboard advertising card.

An order's report date does not prove its time of day.  These helpers never
use ingestion/update timestamps or turn a date-only value into midnight.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from snapchat_v2.salla_outcomes import _parse_datetime as _parse_source_timestamp


RIYADH_TIMEZONE = "Asia/Riyadh"
RIYADH_TZ = ZoneInfo(RIYADH_TIMEZONE)
SALLA_ORDER_TIMESTAMP_PROJECTION = {
    "raw_by_source.salla_direct.date": 1,
    "raw_by_source.salla_direct.created_at": 1,
}

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EXPLICIT_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}")


def _precise_timestamp(value: Any, *, allow_timezone_hint: bool) -> datetime | None:
    candidate = value
    if isinstance(candidate, dict):
        if not allow_timezone_hint:
            return None
        candidate = candidate.get("date") or candidate.get("value")
    if isinstance(candidate, datetime):
        pass
    elif not isinstance(candidate, str) or not _EXPLICIT_TIME.match(candidate.strip()):
        return None
    # The existing parser respects an explicit offset before a timezone hint
    # and rejects a naive timestamp when no valid hint was supplied.
    try:
        parsed = _parse_source_timestamp(value)
        return parsed.astimezone(RIYADH_TZ) if parsed is not None else None
    except (OverflowError, ValueError, TypeError):
        return None


def order_riyadh_timestamp(order: dict[str, Any]) -> datetime | None:
    """Return the source order time in Riyadh, or None without precise proof.

    Salla's native date object retains its timezone; the normalized
    order_date_raw often loses that hint and is accepted only when aware.
    Generic created_at/updated_at and the calendar-only order_date are not
    fallback evidence for a sale's time of day.
    """
    if not isinstance(order, dict):
        return None
    sources = order.get("raw_by_source")
    raw = sources.get("salla_direct") if isinstance(sources, dict) else None
    if isinstance(raw, dict):
        for field in ("date", "created_at"):
            parsed = _precise_timestamp(raw.get(field), allow_timezone_hint=True)
            if parsed is not None:
                return parsed
    return _precise_timestamp(order.get("order_date_raw"), allow_timezone_hint=False)


def _report_date(value: Any) -> date | None:
    if not isinstance(value, str) or not _ISO_DAY.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def dashboard_day_segments(
    date_from: str | None,
    date_to: str | None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Describe the two half-open segments of a single selected Riyadh day.

    end_at is always the nominal noon/midnight boundary.  cutoff_at is the
    observed clock clamped to each segment, so future time is never counted
    as elapsed time.  Invalid or non-single-day scopes remain unavailable.
    """
    rows = [
        {
            "id": identity,
            "date_from": date_from if isinstance(date_from, str) else None,
            "date_to": date_to if isinstance(date_to, str) else None,
            "timezone": RIYADH_TIMEZONE,
            "start_at": None,
            "end_at": None,
            "cutoff_at": None,
            "status": "unavailable",
            "reason_codes": [],
        }
        for identity in ("morning", "afternoon")
    ]

    def unavailable(reason: str) -> list[dict[str, Any]]:
        for row in rows:
            row["reason_codes"] = [reason]
        return rows

    if date_from is None or date_to is None or date_from == "" or date_to == "":
        return unavailable("missing_date_range")
    start_day, end_day = _report_date(date_from), _report_date(date_to)
    if start_day is None or end_day is None or end_day < start_day:
        return unavailable("invalid_date_range")
    if start_day != end_day:
        return unavailable("single_day_required")

    clock = datetime.now(timezone.utc) if now is None else now
    if not isinstance(clock, datetime):
        return unavailable("invalid_clock")
    if clock.tzinfo is None or clock.utcoffset() is None:
        return unavailable("clock_timezone_missing")
    try:
        clock = clock.astimezone(RIYADH_TZ)
        midnight = datetime.combine(start_day, time.min, tzinfo=RIYADH_TZ)
        noon = datetime.combine(start_day, time(12), tzinfo=RIYADH_TZ)
        next_midnight = datetime.combine(start_day + timedelta(days=1), time.min, tzinfo=RIYADH_TZ)
    except (OverflowError, ValueError):
        return unavailable("invalid_date_range")

    for row, start, end in zip(rows, (midnight, noon), (noon, next_midnight)):
        cutoff = max(start, min(clock, end))
        row.update({
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "cutoff_at": cutoff.isoformat(),
            "status": "not_started" if clock <= start else "completed" if clock >= end else "active",
        })
    return rows
