"""Suppress resolved Google discovery banners without deleting audit history."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_GOOGLE_PROVIDER_IDS = frozenset(
    {
        "google_analytics_4",
        "google_search_console",
        "google_merchant_center",
        "google_ads",
    }
)


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def google_discovery_error_is_resolved(snapshot: dict[str, Any]) -> bool:
    """Return true when a later successful Google discovery supersedes an error."""
    if snapshot.get("provider") not in _GOOGLE_PROVIDER_IDS:
        return False
    if not snapshot.get("has_data"):
        return False
    latest_error = snapshot.get("latest_error")
    if not isinstance(latest_error, dict):
        return False
    if not str(latest_error.get("code") or "").startswith("google_discovery_"):
        return False
    error_at = _as_utc(latest_error.get("occurred_at"))
    success_at = _as_utc(snapshot.get("last_sync_at"))
    return bool(error_at and success_at and error_at <= success_at)


def install_google_stale_error_filter() -> None:
    """Hide superseded Google discovery errors from provider cards.

    The underlying error row remains in the audit collection and is still
    available through the bounded errors endpoint. Only the current card state
    stops presenting a prior failure after a newer successful discovery.
    """
    from . import service as service_module

    service_class = service_module.IntegrationsControlCenterService
    original = service_class._v2_snapshot
    if getattr(original, "_mezan_google_stale_error_filter", False):
        return

    async def wrapped_v2_snapshot(self: Any, user_id: str, definition: Any):
        result = await original(self, user_id, definition)
        if not result:
            return result
        snapshot, health = result
        if google_discovery_error_is_resolved(snapshot):
            snapshot = dict(snapshot)
            snapshot["latest_error"] = None
        return snapshot, health

    wrapped_v2_snapshot._mezan_google_stale_error_filter = True  # type: ignore[attr-defined]
    service_class._v2_snapshot = wrapped_v2_snapshot
