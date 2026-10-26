"""Strict source-instant conversion for future order-evidence readers.

This does not establish that a timestamp belongs to an order, prove source
coverage, read an order, or count sales. Callers must first bind source and
owner identity and must keep missing or unparseable timestamps unknown.
"""
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo

_INSTANT = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$"
)
_RIYADH = ZoneInfo("Asia/Riyadh")


def exact_source_instant(value):
    """Return a UTC instant for precise offset-bearing input, else None.

    RFC 3339's -00:00 denotes an unknown local offset, so it is deliberately
    not upgraded to a proven UTC instant. Date-only and naive times are not
    interpreted using local machine, Riyadh, or ingestion defaults.
    """
    if not isinstance(value, str) or not _INSTANT.fullmatch(value) or value.endswith("-00:00"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def exact_riyadh_order_date(value):
    instant = exact_source_instant(value)
    if instant is None:
        return None
    try:
        return instant.astimezone(_RIYADH).date()
    except (ValueError, OverflowError):
        return None
