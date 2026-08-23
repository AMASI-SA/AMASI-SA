"""Recover missing Mezan V2 advertising scheduler targets.

The five-minute scheduler normally discovers merchants from the provider-level
``mezan_integrations_v2`` projection.  A projection can be absent while the
owner-selected account rows and encrypted OAuth credential remain healthy.  In
that state the scheduler previously reported ``targets=0`` and Meta spend froze.

This module augments the scheduler target list from selected, connected Meta
and Snapchat account projections.  It never reads access tokens and performs
no provider, campaign, accounting, or Qoyod writes.  The provider refresh path
still has to prove tenant credentials and account ownership before it may read
provider data or advance freshness.
"""
from __future__ import annotations

from typing import Any, Iterable

from .meta_oauth_security import META_PROVIDER_ID
from .snapchat_native_data_common import SNAPCHAT_PROVIDER_ID

RECOVERY_SOURCE_MODE = "meta_selected_account_scheduler_target_recovery_v1"
SNAPCHAT_RECOVERY_SOURCE_MODE = (
    "snapchat_selected_account_scheduler_target_recovery_v1"
)


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


async def selected_meta_scheduler_targets(db: Any) -> set[tuple[str, str]]:
    """Return users with an owner-selected connected Meta V2 account."""
    cursor = db.mezan_integration_accounts_v2.find(
        {
            "provider": META_PROVIDER_ID,
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "mezan_selected": True,
        },
        {"_id": 0, "user_id": 1},
    )
    targets: set[tuple[str, str]] = set()
    for row in await _to_list(cursor, 2000):
        user_id = str(row.get("user_id") or "").strip()
        if user_id:
            targets.add((user_id, META_PROVIDER_ID))
    return targets


async def selected_snapchat_scheduler_targets(db: Any) -> set[tuple[str, str]]:
    """Return users with an owner-selected connected Snapchat V2 account.

    Migrated Snapchat account projections can predate
    ``connection_provenance``.  The canonical scheduler account loader repeats
    the stronger decryptable-credential and organization-ownership proof before
    any provider request or fact write.
    """
    cursor = db.mezan_integration_accounts_v2.find(
        {
            "provider": SNAPCHAT_PROVIDER_ID,
            "connection_status": "connected",
            "mezan_selected": True,
        },
        {"_id": 0, "user_id": 1},
    )
    targets: set[tuple[str, str]] = set()
    for row in await _to_list(cursor, 2000):
        user_id = str(row.get("user_id") or "").strip()
        if user_id:
            targets.add((user_id, SNAPCHAT_PROVIDER_ID))
    return targets


async def augment_auto_sync_targets(
    db: Any,
    base_targets: Iterable[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Union normal targets with recoverable selected account projections."""
    targets = {
        (str(user_id).strip(), str(provider).strip())
        for user_id, provider in base_targets
        if str(user_id).strip() and str(provider).strip()
    }
    targets.update(await selected_meta_scheduler_targets(db))
    targets.update(await selected_snapchat_scheduler_targets(db))
    return sorted(targets)


def install_ads_auto_sync_target_recovery() -> None:
    """Wrap the scheduler target collector once at router composition time."""
    from . import ads_auto_sync_scheduler as scheduler

    original = scheduler._targets
    if getattr(original, "_mezan_ads_target_recovery_v2", False):
        return

    async def wrapped_targets(db: Any) -> list[tuple[str, str]]:
        return await augment_auto_sync_targets(db, await original(db))

    wrapped_targets._mezan_ads_target_recovery_v2 = True  # type: ignore[attr-defined]
    wrapped_targets._mezan_original_targets = original  # type: ignore[attr-defined]
    scheduler._targets = wrapped_targets


__all__ = [
    "RECOVERY_SOURCE_MODE",
    "SNAPCHAT_RECOVERY_SOURCE_MODE",
    "augment_auto_sync_targets",
    "install_ads_auto_sync_target_recovery",
    "selected_meta_scheduler_targets",
    "selected_snapchat_scheduler_targets",
]
