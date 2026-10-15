"""Resolve stale Snapchat card errors after a newer successful synchronization.

The append-only error collection remains the audit log.  The integrations card
must not keep showing an old provider error after a later complete refresh has
proved that the selected accounts and reporting data are healthy again.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .snapchat_native_data_common import SNAPCHAT_PROVIDER_ID

_SUCCESS_DATA_QUALITY = {"complete", "good", "healthy"}
_STALE_HEALTH_STATUSES = {"degraded", "unhealthy", "error"}
_AUTH_ERROR_FRAGMENTS = (
    "needs_reauth",
    "authorization",
    "credential",
    "token",
    "oauth",
)


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_auth_error(code: Any) -> bool:
    normalized = str(code or "").strip().lower()
    return any(fragment in normalized for fragment in _AUTH_ERROR_FRAGMENTS)


def resolve_snapchat_stale_operational_state(
    snapshot: dict[str, Any],
    health: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return card state with only errors unresolved by a later complete sync.

    The error row itself is never deleted.  It remains available in the
    synchronization and error activity log.
    """
    safe_snapshot = dict(snapshot or {})
    safe_health = dict(health) if isinstance(health, dict) else health

    if str(safe_snapshot.get("provider") or "") != SNAPCHAT_PROVIDER_ID:
        return safe_snapshot, safe_health
    if str(safe_snapshot.get("connection_status") or "") != "connected":
        return safe_snapshot, safe_health
    if str(safe_snapshot.get("data_quality") or "").lower() not in _SUCCESS_DATA_QUALITY:
        return safe_snapshot, safe_health

    success_at = _as_utc(safe_snapshot.get("last_sync_at"))
    if success_at is None:
        return safe_snapshot, safe_health

    latest_error = safe_snapshot.get("latest_error")
    if isinstance(latest_error, dict) and not _is_auth_error(latest_error.get("code")):
        error_at = _as_utc(latest_error.get("occurred_at"))
        if error_at is not None and error_at <= success_at:
            safe_snapshot["latest_error"] = None

    if isinstance(safe_health, dict):
        health_at = _as_utc(safe_health.get("checked_at"))
        health_status = str(
            safe_health.get("health_status") or safe_health.get("status") or ""
        ).lower()
        if (
            health_at is not None
            and health_at <= success_at
            and health_status in _STALE_HEALTH_STATUSES
        ):
            safe_health = None

    return safe_snapshot, safe_health


def install_snapchat_operational_error_resolution() -> None:
    """Install a read-only presentation filter on the V2 integration snapshot."""
    from . import service as service_module

    service_class = service_module.IntegrationsControlCenterService
    current = service_class._v2_snapshot
    if getattr(current, "_mezan_snapchat_operational_error_resolution_v3", False):
        return

    async def wrapped_v2_snapshot(self: Any, user_id: str, definition: Any):
        result = await current(self, user_id, definition)
        if not result or definition.provider != SNAPCHAT_PROVIDER_ID:
            return result
        snapshot, health = result
        return resolve_snapchat_stale_operational_state(snapshot, health)

    wrapped_v2_snapshot._mezan_snapchat_operational_error_resolution_v3 = True  # type: ignore[attr-defined]
    service_class._v2_snapshot = wrapped_v2_snapshot


__all__ = [
    "install_snapchat_operational_error_resolution",
    "resolve_snapchat_stale_operational_state",
]
