"""Provider-neutral entry point for Unified Marketing consumers."""
from __future__ import annotations

from datetime import date
from typing import Any

from .readers.snapchat_v2 import (
    load_snapchat_v2_dashboard_spend,
    load_snapchat_v2_entity_report,
    load_snapchat_v2_entity_readiness_evidence,
    load_snapchat_v2_entity_daily_series,
    load_snapchat_v2_entity_metadata,
)
from .readers.snapchat_v2_decision_evidence import (
    load_snapchat_v2_account_identity,
    load_snapchat_v2_account_identities,
    load_snapchat_v2_account_report,
)

from .providers import PROVIDERS, get_reader

SUPPORTED_PROVIDERS = tuple(PROVIDERS)


async def load_unified_marketing_account_identities(
    db: Any, user_id: str, *, provider: str,
) -> list[dict[str, Any]]:
    return await get_reader(provider, "account_identities")(db, str(user_id))


async def load_unified_marketing_account_identity(
    db: Any,
    user_id: str,
    *,
    provider: str,
    account_id: str | None = None,
) -> dict[str, Any] | None:
    return await get_reader(provider, "account_identity")(
        db, str(user_id), **({"account_id": account_id} if account_id is not None else {}),
    )


async def load_unified_marketing_entity_metadata(
    db: Any,
    user_id: str,
    *,
    provider: str,
    account_id: str | None = None,
    entity_level: str,
    entity_id: str,
) -> dict[str, Any]:
    return await get_reader(provider, "entity_metadata")(
        db,
        str(user_id),
        **({"account_id": account_id} if account_id is not None else {}),
        entity_level=entity_level,
        entity_id=entity_id,
    )


async def load_unified_marketing_account_report(
    db: Any,
    user_id: str,
    *,
    provider: str,
    account_id: str | None = None,
    date_from: date,
    date_to: date,
    timezone_name: str,
) -> dict[str, Any]:
    return await get_reader(provider, "account_report")(
        db,
        str(user_id),
        **({"account_id": account_id} if account_id is not None else {}),
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
    )


async def load_unified_marketing_dashboard_spend(
    db: Any,
    user_id: str,
    *,
    provider: str,
    account_id: str | None = None,
    date_from: date,
    date_to: date,
    timezone_name: str,
) -> dict[str, Any]:
    return await get_reader(provider, "dashboard_spend")(
        db,
        str(user_id),
        **({"account_id": account_id} if account_id is not None else {}),
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
    )


async def load_unified_marketing_entity_report(
    db: Any,
    user_id: str,
    *,
    provider: str,
    account_id: str | None = None,
    entity_level: str,
    date_from: date,
    date_to: date,
    timezone_name: str,
    include_stale: bool = True,
) -> dict[str, Any]:
    return await get_reader(provider, "entity_report")(
        db,
        str(user_id),
        **({"account_id": account_id} if account_id is not None else {}),
        entity_level=entity_level,
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
        include_stale=include_stale,
    )


async def load_unified_marketing_entity_readiness_evidence(
    db: Any,
    user_id: str,
    *,
    provider: str,
    account_id: str | None = None,
    entity_level: str,
    date_from: date,
    date_to: date,
    timezone_name: str,
) -> dict[str, Any]:
    return await get_reader(provider, "entity_readiness_evidence")(
        db,
        str(user_id),
        **({"account_id": account_id} if account_id is not None else {}),
        entity_level=entity_level,
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
    )


async def load_unified_marketing_entity_daily_series(
    db: Any,
    user_id: str,
    *,
    provider: str,
    account_id: str | None = None,
    entity_level: str,
    entity_ids: list[str],
    date_from: date,
    date_to: date,
    timezone_name: str,
) -> dict[str, Any]:
    return await get_reader(provider, "entity_daily_series")(
        db,
        str(user_id),
        **({"account_id": account_id} if account_id is not None else {}),
        entity_level=entity_level,
        entity_ids=entity_ids,
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
    )


__all__ = [
    "SUPPORTED_PROVIDERS",
    "load_unified_marketing_account_identities",
    "load_unified_marketing_account_report",
    "load_unified_marketing_account_identity",
    "load_unified_marketing_entity_metadata",
    "load_unified_marketing_dashboard_spend",
    "load_unified_marketing_entity_report",
    "load_unified_marketing_entity_readiness_evidence",
    "load_unified_marketing_entity_daily_series",
]
