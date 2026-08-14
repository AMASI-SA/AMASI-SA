"""Fail-closed policy for password-only authentication in design Preview.

The bypass is intentionally impossible to enable with a single setting. It
requires an explicit opt-in, the Preview deployment environment, and an exact
allow-listed Host header. Production keeps the normal MFA/OTP flow even if one
of those settings is accidentally copied there.
"""
from __future__ import annotations

import os
from ipaddress import ip_address
from typing import Any


DEFAULT_PREVIEW_AUTH_HOSTS = {"salla-analytics.preview.emergentagent.com"}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _header_value(scope: dict[str, Any], wanted: str) -> str:
    for raw_name, raw_value in scope.get("headers") or []:
        name = raw_name.decode("latin-1") if isinstance(raw_name, bytes) else str(raw_name)
        if name.lower() != wanted:
            continue
        value = raw_value.decode("latin-1") if isinstance(raw_value, bytes) else str(raw_value)
        return value.strip()
    return ""


def _normalise_host(value: str) -> str:
    # X-Forwarded-Host may contain a proxy chain. The first value is the
    # original public host. Preview hosts are DNS names, so strip a development
    # port and a harmless trailing dot before exact allow-list comparison.
    return value.split(",", 1)[0].strip().lower().split(":", 1)[0].rstrip(".")


def _request_from_private_proxy(scope: dict[str, Any]) -> bool:
    client = scope.get("client") or ()
    if not client:
        return False
    try:
        address = ip_address(str(client[0]).strip())
    except ValueError:
        return False
    return bool(address.is_private or address.is_loopback)


def _scope_host(scope: dict[str, Any]) -> str:
    direct_host = _normalise_host(_header_value(scope, "host"))
    if direct_host in _allowed_hosts():
        return direct_host

    # Emergent's Preview gateway terminates TLS and rewrites Host before
    # forwarding to uvicorn. Trust X-Forwarded-Host only with a separate opt-in
    # and only when the immediate peer is a private/loopback proxy. Production
    # therefore cannot be opened by a client-supplied forwarded header.
    if not _truthy(os.environ.get("AUTH_PREVIEW_TRUST_PROXY")):
        return direct_host
    if not _request_from_private_proxy(scope):
        return direct_host
    return _normalise_host(_header_value(scope, "x-forwarded-host"))


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
