"""Static guard helpers for Snapchat V2 legacy isolation."""
from __future__ import annotations

from pathlib import Path

FORBIDDEN_SNAPCHAT_V2_PATTERNS = (
    ".snapchat_connections",
    '["snapchat_connections"]',
    ".snapchat_ad_accounts",
    '["snapchat_ad_accounts"]',
    ".snapchat_account_daily",
    '["snapchat_account_daily"]',
    "snapchat_analytics_backfill",
    "/api/snapchat/",
    "/snapchat-accounts",
)


def assert_snapchat_v2_is_legacy_independent(root: Path | None = None) -> None:
    """Fail when native Snapchat modules start reading old pages/collections."""
    base = root or Path(__file__).resolve().parent
    native_paths = (
        base / "snapchat_oauth_security.py",
        base / "snapchat_discovery.py",
        base / "snapchat_projection.py",
        base / "snapchat_connections.py",
        base / "snapchat_native_data_common.py",
        base / "snapchat_native_entities_sync.py",
        base / "snapchat_native_performance_sync.py",
        base / "snapchat_native_data_sync.py",
        base / "snapchat_native_data_routes.py",
    )
    violations = []
    for path in native_paths:
        source = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_SNAPCHAT_V2_PATTERNS:
            if forbidden in source:
                violations.append(
                    f"{path.name}: forbidden legacy dependency {forbidden}"
                )
    if violations:
        raise RuntimeError("\n".join(violations))
