"""Creation-date authority for Mezan Product V2 ordering.

The workspace label "المضافة حديثًا" means the product creation timestamp in
Salla, never the last edit/sync timestamp. This module extracts that timestamp
from the known Salla product payload shapes and exposes a stable sort contract.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, dict):
        for key in ("date", "datetime", "created_at", "value"):
            parsed = _parse_datetime(value.get(key))
            if parsed is not None:
                return parsed
        return None
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def salla_product_created_at(raw: dict[str, Any]) -> datetime | None:
    """Return the original Salla creation timestamp, never an update timestamp."""
    date_node = raw.get("date") if isinstance(raw.get("date"), dict) else {}
    for candidate in (
        raw.get("created_at"),
        raw.get("date_created"),
        raw.get("created"),
        date_node.get("created_at"),
        date_node.get("date"),
    ):
        parsed = _parse_datetime(candidate)
        if parsed is not None:
            return parsed
    return None


NEWEST_CREATION_SORT = [
    ("source_created_at", -1),
    ("salla_product_id", -1),
]

OLDEST_CREATION_SORT = [
    ("source_created_at", 1),
    ("salla_product_id", 1),
]
