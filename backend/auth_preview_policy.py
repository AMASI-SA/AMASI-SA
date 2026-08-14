"""Fail-closed policy for password-only authentication in design Preview.

The bypass is intentionally impossible to enable with a single setting. It
requires an explicit opt-in, the Preview deployment environment, and an exact
allow-listed Host header. Production keeps the normal MFA/OTP flow even if one
of those settings is accidentally copied there.
"""
from __future__ import annotations

import os
from typing import Any


DEFAULT_PREVIEW_AUTH_HOSTS = {"salla-analytics.preview.emergentagent.com"}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _scope_host(scope: dict[str, Any]) -> str:
    for raw_name, raw_value in scope.get("headers") or []:
        name = raw_name.decode("latin-1") if isinstance(raw_name, bytes) else str(raw_name)
        if name.lower() != "host":
            continue
        value = raw_value.decode("latin-1") if isinstance(raw_value, bytes) else str(raw_value)
        # Preview hosts are DNS names. Strip a development port and a harmless
        # trailing dot before exact allow-list comparison.
        return value.strip().lower().split(":", 1)[0].rstrip(".")
    return ""


def _allowed_hosts() -> set[str]:
    raw = os.environ.get("AUTH_PREVIEW_ALLOWED_HOSTS")
    if raw is None:
        return set(DEFAULT_PREVIEW_AUTH_HOSTS)
    return {
        item.strip().lower().split(":", 1)[0].rstrip(".")
        for item in raw.split(",")
        if item.strip()
    }


def preview_password_only_enabled(scope: dict[str, Any]) -> bool:
    """Return true only for an explicitly approved Preview HTTP request."""
    if not _truthy(os.environ.get("AUTH_PREVIEW_PASSWORD_ONLY")):
        return False
    if str(os.environ.get("MEZAN_ENVIRONMENT") or "").strip().lower() != "preview":
        return False
    host = _scope_host(scope)
    return bool(host and host in _allowed_hosts())


__all__ = [
    "DEFAULT_PREVIEW_AUTH_HOSTS",
    "preview_password_only_enabled",
]
