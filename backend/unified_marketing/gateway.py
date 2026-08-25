"""Provider-neutral entry point for Unified Marketing consumers."""
from __future__ import annotations

from datetime import date
from typing import Any

from .readers.snapchat_v2 import (
    load_snapchat_v2_account_report,
    load_snapchat_v2_account_identity,
    load_snapchat_v2_dashboard_spend,
    load_snapchat_v2_entity_report,
)

SUPPORTED_PROVIDERS = ("snapchat_ads",)


async def load_unified_marketing_account_identity(
    db: Any,
    user_id: str,
    *,
    provider: str,
) -> dict[str, Any] | None:
    provider_key = str(provider or "").strip().lower()
    if provider_key == "snapchat_ads":
        return await load_snapchat_v2_account_identity(db, str(user_id))
    raise ValueError(f"unsupported_unified_marketing_provider:{provider_key}")


async def load_unified_marketing_account_report(
    db: Any,
    user_id: str,
    *,
    provider: str,
    date_from: date,
    date_to: date,
    timezone_name: str,
) -> dict[str, Any]:
    provider_key = str(provider or "").strip().lower()
    if provider_key == "snapchat_ads":
        return await load_snapchat_v2_account_report(
            db,
            str(user_id),
            date_from=date_from,
            date_to=date_to,
            timezone_name=timezone_name,
        )
    raise ValueError(f"unsupported_unified_marketing_provider:{provider_key}")


async def load_unified_marketing_dashboard_spend(
    db: Any,
    user_id: str,
    *,
    provider: str,
    date_from: date,
    date_to: date,
    timezone_name: str,
) -> dict[str, Any]:
    provider_key = str(provider or "").strip().lower()
    if provider_key == "snapchat_ads":
        return await load_snapchat_v2_dashboard_spend(
            db,
            str(user_id),
            date_from=date_from,
            date_to=date_to,
            timezone_name=timezone_name,
        )
    raise ValueError(f"unsupported_unified_marketing_provider:{provider_key}")


async def load_unified_marketing_entity_report(
    db: Any,
    user_id: str,
    *,
    provider: str,
    entity_level: str,
    date_from: date,
    date_to: date,
    timezone_name: str,
    include_stale: bool = True,
) -> dict[str, Any]:
    provider_key = str(provider or "").strip().lower()
    if provider_key == "snapchat_ads":
        return await load_snapchat_v2_entity_report(
            db,
            str(user_id),
            entity_level=entity_level,
            date_from=date_from,
            date_to=date_to,
            timezone_name=timezone_name,
            include_stale=include_stale,
        )
    raise ValueError(f"unsupported_unified_marketing_provider:{provider_key}")


__all__ = [
    "SUPPORTED_PROVIDERS",
    "load_unified_marketing_account_report",
    "load_unified_marketing_account_identity",
    "load_unified_marketing_dashboard_spend",
    "load_unified_marketing_entity_report",
]
