"""Normalize known Qoyod API origins without changing custom endpoints."""
from __future__ import annotations

import httpx


CANONICAL_QOYOD_API_BASE = "https://api.qoyod.com/2.0"
LEGACY_QOYOD_HOSTS = {"legacy.qoyod.com", "www.qoyod.com"}


def normalize_qoyod_api_base(value: str) -> str:
    """Return a usable base for Qoyod's official production hosts.

    Qoyod retired the legacy hosts and its canonical host requires the
    version segment.  A deployment value of ``https://api.qoyod.com`` is
    therefore syntactically valid but resolves ``/products`` to the provider's
    route-level ``URL not found`` response.  Normalize only the known v2
    spellings; explicit future versions and test/custom origins remain intact.
    """
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return raw

    try:
        parsed = httpx.URL(raw)
    except Exception:
        return raw

    host = (parsed.host or "").casefold()
    path = parsed.path.rstrip("/")
    if host in LEGACY_QOYOD_HOSTS:
        return CANONICAL_QOYOD_API_BASE
    if host == "api.qoyod.com" and path in {"", "/api/2.0", "/2.0"}:
        return CANONICAL_QOYOD_API_BASE
    return raw
