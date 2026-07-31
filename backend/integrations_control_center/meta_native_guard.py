"""Static guard helpers for Meta V2 legacy and accounting isolation."""
from __future__ import annotations

from pathlib import Path

FORBIDDEN_META_V2_PATTERNS = (
    ".meta_connections",
    '["meta_connections"]',
    ".meta_ads_daily",
    '["meta_ads_daily"]',
    ".ads_daily",
    '["ads_daily"]',
    ".general_ledger",
    '["general_ledger"]',
    ".qoyod_invoices",
    '["qoyod_invoices"]',
    "/api/meta/",
    "/meta-settings",
)


def assert_meta_v2_is_legacy_independent(root: Path | None = None) -> None:
    """Fail when native Meta modules depend on legacy or protected write paths."""
    base = root or Path(__file__).resolve().parent
    native_paths = (
        base / "meta_oauth_security.py",
        base / "meta_discovery.py",
        base / "meta_projection.py",
        base / "meta_connections.py",
        base / "meta_account_selection.py",
        base / "meta_native_reporting.py",
        base / "meta_native_reporting_routes.py",
    )
    violations = []
    for path in native_paths:
        source = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_META_V2_PATTERNS:
            if forbidden in source:
                violations.append(
                    f"{path.name}: forbidden legacy dependency {forbidden}"
                )
    if violations:
        raise RuntimeError("\n".join(violations))
