"""Pure future-cutover merge policy, exercised while V3 is still shadow-only."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional


def _timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, dict):
        value = value.get("date") or value.get("value")
    if isinstance(value, datetime):
        parsed = value
    elif value not in (None, ""):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _newer(incoming: Any, existing: Any) -> bool:
    new_time = _timestamp(incoming)
    old_time = _timestamp(existing)
    if new_time is None:
        return old_time is None
    return old_time is None or new_time >= old_time


def decide_shadow_merge(
    existing: dict[str, Any],
    incoming: dict[str, Any],
    *,
    items_sync_status: str,
    items_payload_valid: bool,
) -> dict[str, Any]:
    """Plan a stale-safe merge without performing any database operation."""
    previous = deepcopy(existing or {})
    candidate = deepcopy(incoming or {})
    base_is_current = _newer(
        candidate.get("provider_updated_at"),
        previous.get("provider_updated_at"),
    )

    merged = deepcopy(previous)
    if base_is_current:
        for key, value in candidate.items():
            if key in {
                "products",
                "items_sync_status",
                "items_payload_valid",
                "items_count",
                "items_synced_at",
                "items_sync_error",
            }:
                continue
            if value not in (None, "", [], {}):
                merged[key] = deepcopy(value)

    items_are_current = _newer(
        candidate.get("items_synced_at"),
        previous.get("items_synced_at"),
    )
    if (
        items_sync_status == "succeeded"
        and items_payload_valid
        and items_are_current
    ):
        merged["products"] = deepcopy(candidate.get("products") or [])
        merged["items_synced_at"] = candidate.get("items_synced_at")
        merged["items_sync_status"] = "succeeded"
        merged["items_payload_valid"] = True
        merged["items_sync_error"] = None
        merged["items_count"] = len(candidate.get("products") or [])
    elif items_are_current:
        merged["items_sync_status"] = items_sync_status
        merged["items_payload_valid"] = False
        if candidate.get("items_sync_error"):
            merged["items_sync_error"] = candidate.get("items_sync_error")
        if previous.get("products"):
            merged["products"] = deepcopy(previous["products"])

    merged.setdefault("products", [])

    previous_revision = int(previous.get("sync_revision") or 0)
    candidate_revision = int(candidate.get("sync_revision") or 0)
    merged["sync_revision"] = max(previous_revision + 1, candidate_revision)
    return merged
