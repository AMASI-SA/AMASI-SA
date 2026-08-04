"""Keep Snapchat campaign and Ad Squad delivery metadata current.

The five-minute performance scheduler reads campaign stats, but status and
current delivery reasons live on the Campaign and Ad Squad entities. This
installer refreshes both read-only catalogues before the unchanged performance
pull, allowing Ads Manager to distinguish:

* configured campaign ACTIVE/PAUSED state;
* actual delivery or no-delivery state;
* campaign budget exhaustion;
* campaigns with no active Ad Squad.

No provider, campaign, accounting, Salla, or Qoyod mutation is performed.
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

CAMPAIGN_CATALOG_SOURCE_MODE = "snapchat_campaign_adsquad_catalog_5m_v2"

ENTITY_SPECS = (
    ("campaign", "campaigns", "campaign", {}),
    ("ad_squad", "adsquads", "adsquad", {"return_placement_v2": "true"}),
)

AccountRefresh = Callable[..., Awaitable[dict[str, Any]]]


def _safe_text(value: Any, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


def _normalized_catalog_error(
    item: dict[str, Any],
    *,
    entity_type: str,
) -> dict[str, Any]:
    raw_code = _safe_text(item.get("error") or item.get("code"), 120)
    code = raw_code or f"snapchat_{entity_type}_catalog_partial"
    return {
        "kind": f"{entity_type}_catalog",
        "code": code,
        "error": code,
        "message": (
            f"Snapchat {entity_type} delivery metadata refresh was incomplete: "
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
    """Refresh Campaign and Ad Squad entities for one selected account."""
    counts: dict[str, dict[str, int]] = {}
    errors: list[dict[str, Any]] = []

    for entity_type, plural_key, singular_key, extra_params in ENTITY_SPECS:
        try:
            saved, observed, raw_errors = await _sync_entity_type(
                context,
                client,
                access_token,
                account,
                entity_type=entity_type,
                plural_key=plural_key,
                singular_key=singular_key,
                extra_params=extra_params,
            )
            counts[entity_type] = {
                "saved": int(saved),
                "observed": int(observed),
            }
            errors.extend(
                _normalized_catalog_error(item, entity_type=entity_type)
                for item in raw_errors
                if isinstance(item, dict)
            )
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_needs_reauth":
                raise
            counts[entity_type] = {"saved": 0, "observed": 0}
            errors.append({
                "kind": f"{entity_type}_catalog",
                "code": exc.code,
                "error": exc.code,
                "message": exc.message[:300],
                "retryable": bool(exc.retryable),
            })

    campaign = counts.get("campaign", {"saved": 0, "observed": 0})
    ad_squad = counts.get("ad_squad", {"saved": 0, "observed": 0})
    return {
        "source_mode": CAMPAIGN_CATALOG_SOURCE_MODE,
        "campaign_entities_saved": campaign["saved"],
        "campaign_entities_observed": campaign["observed"],
        "ad_squad_entities_saved": ad_squad["saved"],
        "ad_squad_entities_observed": ad_squad["observed"],
        "delivery_catalog_entities_saved": (
            campaign["saved"] + ad_squad["saved"]
        ),
        "delivery_catalog_entities_observed": (
            campaign["observed"] + ad_squad["observed"]
        ),
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
    """Refresh delivery metadata first, preserving performance on errors."""
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
            "ad_squad_entities_saved": 0,
            "ad_squad_entities_observed": 0,
            "delivery_catalog_entities_saved": 0,
            "delivery_catalog_entities_observed": 0,
            "errors_count": 1,
            "errors": [{
                "kind": "delivery_catalog",
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
    "ENTITY_SPECS",
    "install_snapchat_campaign_catalog_refresh",
    "refresh_snapchat_campaign_catalog",
]
