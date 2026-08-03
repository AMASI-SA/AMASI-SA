"""Keep Snapchat campaign names and active/paused state current.

The five-minute performance scheduler already reads a campaign breakdown for
spend and conversions, but those rows only contain campaign IDs.  Names and
status come from the separate campaign entity catalogue.  This installer wraps
the existing account refresh so the campaign catalogue is refreshed first on
every selected account, then the unchanged performance refresh runs.

All provider calls are read-only.  The module does not modify campaigns,
accounting, Salla, or Qoyod.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import httpx

from . import snapchat_account_hourly_refresh as hourly
from .snapchat_native_data_common import (
    SnapchatNativeSyncError,
    SnapchatSyncContext,
)
from .snapchat_native_entities_sync import _sync_entity_type

CAMPAIGN_CATALOG_SOURCE_MODE = "snapchat_campaign_catalog_5m_v1"
CAMPAIGN_ENTITY_TYPE = "campaign"
CAMPAIGN_PLURAL_KEY = "campaigns"
CAMPAIGN_SINGULAR_KEY = "campaign"

AccountRefresh = Callable[..., Awaitable[dict[str, Any]]]


def _safe_text(value: Any, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


def _normalized_catalog_error(item: dict[str, Any]) -> dict[str, Any]:
    raw_code = _safe_text(item.get("error") or item.get("code"), 120)
    code = raw_code or "snapchat_campaign_catalog_partial"
    return {
        "kind": "campaign_catalog",
        "code": code,
        "error": code,
        "message": (
            "Snapchat campaign names/status refresh was incomplete: "
            f"{code}"
        ),
        "retryable": code not in {
            "entity_row_limit_reached",
            "entity_page_limit_reached",
            "entity_paging_untrusted",
        },
    }


async def refresh_snapchat_campaign_catalog(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
) -> dict[str, Any]:
    """Refresh only campaign entities for one selected Snapchat account."""
    saved, observed, raw_errors = await _sync_entity_type(
        context,
        client,
        access_token,
        account,
        entity_type=CAMPAIGN_ENTITY_TYPE,
        plural_key=CAMPAIGN_PLURAL_KEY,
        singular_key=CAMPAIGN_SINGULAR_KEY,
        extra_params={},
    )
    errors = [
        _normalized_catalog_error(item)
        for item in raw_errors
        if isinstance(item, dict)
    ]
    return {
        "source_mode": CAMPAIGN_CATALOG_SOURCE_MODE,
        "campaign_entities_saved": int(saved),
        "campaign_entities_observed": int(observed),
        "errors_count": len(errors),
        "errors": errors,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def _refresh_with_campaign_catalog(
    base_refresh: AccountRefresh,
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Refresh metadata first, while preserving performance on metadata errors."""
    try:
        catalogue = await refresh_snapchat_campaign_catalog(
            context,
            client,
            access_token,
            account,
        )
    except SnapchatNativeSyncError as exc:
        if exc.code == "snapchat_needs_reauth":
            raise
        catalogue = {
            "source_mode": CAMPAIGN_CATALOG_SOURCE_MODE,
            "campaign_entities_saved": 0,
            "campaign_entities_observed": 0,
            "errors_count": 1,
            "errors": [{
                "kind": "campaign_catalog",
                "code": exc.code,
                "error": exc.code,
                "message": exc.message[:300],
                "retryable": bool(exc.retryable),
            }],
            "source_only": True,
            "provider_write_reached": False,
            "campaign_write_reached": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }

    result = await base_refresh(
        context,
        client,
        access_token,
        account,
        *args,
        **kwargs,
    )
    output = dict(result or {})
    output["campaign_catalog"] = catalogue

    catalogue_errors = list(catalogue.get("errors") or [])
    if catalogue_errors:
        combined = [
            item for item in list(output.get("errors") or [])
            if isinstance(item, dict)
        ]
        combined.extend(catalogue_errors)
        output["errors"] = combined
        output["errors_count"] = len(combined)
    return output


def install_snapchat_campaign_catalog_refresh() -> None:
    """Install an idempotent wrapper before the scheduler imports the callable."""
    current = hourly.refresh_snapchat_account_hours
    if getattr(current, "_mezan_campaign_catalog_refresh", False):
        return

    async def wrapped(
        context: SnapchatSyncContext,
        client: httpx.AsyncClient,
        access_token: str,
        account: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return await _refresh_with_campaign_catalog(
            current,
            context,
            client,
            access_token,
            account,
            *args,
            **kwargs,
        )

    wrapped._mezan_campaign_catalog_refresh = True  # type: ignore[attr-defined]
    wrapped._mezan_campaign_catalog_base = current  # type: ignore[attr-defined]
    hourly.refresh_snapchat_account_hours = wrapped


__all__ = [
    "CAMPAIGN_CATALOG_SOURCE_MODE",
    "install_snapchat_campaign_catalog_refresh",
    "refresh_snapchat_campaign_catalog",
]
