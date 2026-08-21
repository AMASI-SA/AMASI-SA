"""Time-window safety semantics for Campaign AI reasoning and execution."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RIYADH = timezone(timedelta(hours=3))
SCALE_SNAPSHOT_MAX_AGE_MINUTES = 90
DEFENSIVE_SNAPSHOT_MAX_AGE_MINUTES = 5 * 60


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None


def _zone(value: Any):
    name = str(value or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return RIYADH


def window_quality(row: dict[str, Any] | None, *, now: datetime | None = None) -> dict[str, Any]:
    source = row if isinstance(row, dict) else {}
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    zone = _zone(source.get("account_timezone"))
    local_now = current.astimezone(zone)
    start = _parse_date(source.get("source_date_from"))
    end = _parse_date(source.get("source_date_to"))
    contains_open_day = bool(end is not None and end == local_now.date())
    requested_days = ((end - start).days + 1) if start and end and end >= start else None
    completed_days = None
    if requested_days is not None:
        completed_days = max(0, requested_days - (1 if contains_open_day else 0))
    elapsed_fraction = (
        round((local_now.hour * 3600 + local_now.minute * 60 + local_now.second) / 86400, 4)
        if contains_open_day
        else 1.0
    )
    return {
        "status": "partial_current_day" if contains_open_day else "completed_window",
        "contains_open_current_day": contains_open_day,
        "requested_days": requested_days,
        "completed_days": completed_days,
        "open_day_elapsed_fraction": elapsed_fraction,
        "safe_for_scale_comparison": not contains_open_day,
        "account_timezone": str(getattr(zone, "key", "Asia/Riyadh")),
    }


def completed_history_window(end: date, days: int) -> tuple[date, date]:
    days = max(1, int(days))
    history_end = end - timedelta(days=1)
    history_start = history_end - timedelta(days=days - 1)
    return history_start, history_end


def snapshot_max_age_minutes(action: Any) -> int:
    return (
        SCALE_SNAPSHOT_MAX_AGE_MINUTES
        if str(action or "").strip().lower() == "scale"
        else DEFENSIVE_SNAPSHOT_MAX_AGE_MINUTES
    )


__all__ = [
    "DEFENSIVE_SNAPSHOT_MAX_AGE_MINUTES",
    "SCALE_SNAPSHOT_MAX_AGE_MINUTES",
    "completed_history_window",
    "snapshot_max_age_minutes",
    "window_quality",
]
