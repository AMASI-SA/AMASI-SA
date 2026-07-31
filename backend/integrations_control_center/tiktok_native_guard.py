"""Static guard helpers for TikTok V2 legacy isolation."""
from __future__ import annotations

from pathlib import Path

FORBIDDEN_TIKTOK_V2_SOURCES = (
    "tiktok_connections",
    "tiktok_ads_daily",
    "/webhook/tiktok/",
)


def assert_tiktok_v2_is_legacy_independent(root: Path | None = None) -> None:
    """Fail when native TikTok modules start depending on Make/legacy sources."""
    base = root or Path(__file__).resolve().parent
    native_paths = (
        base / "tiktok_oauth_security.py",
        base / "tiktok_discovery.py",
        base / "tiktok_projection.py",
        base / "tiktok_connections.py",
        base / "tiktok_native_reporting.py",
        base / "tiktok_native_reporting_routes.py",
    )
    violations = []
    for path in native_paths:
        source = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_TIKTOK_V2_SOURCES:
            if forbidden in source:
                violations.append(f"{path.name}: forbidden legacy dependency {forbidden}")
    if violations:
        raise RuntimeError("\n".join(violations))
