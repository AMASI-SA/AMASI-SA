"""Accounting dates shared by historical reports and posting-period controls."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

RIYADH = ZoneInfo("Asia/Riyadh")


def accounting_instant(row):
    """Prefer explicit economic dates; malformed authoritative dates never fall back.

    Existing recognition/settlement journals predate accounting_at. Audit time is
    used only for legacy journals without any economic date. BSON naive datetimes
    are UTC; timestamp strings must carry their offset.
    """
    meta = row.get("metadata") or {}
    for container, key in ((meta, "accounting_at"), (meta, "recognized_at"),
                           (meta, "statement_date"), (row, "posted_at"),
                           (row, "created_at")):
        if key not in container:
            continue
        value = container[key]
        if isinstance(value, datetime):
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        if not isinstance(value, str):
            raise ValueError("invalid_accounting_date")
        try:
            if len(value) == 10:
                return datetime.combine(date.fromisoformat(value), time.min, RIYADH).astimezone(timezone.utc)
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                raise ValueError("offset required")
            return instant.astimezone(timezone.utc)
        except ValueError as exc:
            raise ValueError("invalid_accounting_date") from exc
    raise ValueError("missing_accounting_date")


def report_cutoff(as_of):
    """Exclusive UTC boundary after the selected Saudi accounting day."""
    if not isinstance(as_of, str) or len(as_of) != 10:
        raise ValueError("as_of_requires_YYYY_MM_DD")
    try:
        day = date.fromisoformat(as_of)
        return datetime.combine(day + timedelta(days=1), time.min, RIYADH).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("as_of_requires_YYYY_MM_DD") from exc
