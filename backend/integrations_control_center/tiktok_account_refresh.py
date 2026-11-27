"""Reconcile every TikTok advertiser authorized for the existing Marketing API OAuth.

The listing endpoint is TikTok's native v1.3 oauth2/advertiser/get. Never use
an OAuth callback's historical advertiser_ids as a permanent allow-list. This
module updates only integration metadata; it does not write spend or accounting.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .tiktok_discovery import discover_tiktok_advertisers
from .tiktok_oauth_security import (
    TIKTOK_CAPABILITY_EVIDENCE,
    TIKTOK_CREDENTIALS_COLLECTION,
    TIKTOK_PERMISSION_ALIAS,
    TIKTOK_PROVIDER_ID,
    TIKTOK_SOURCE_MODE,
    _tiktok_payload,
)

TIKTOK_AUTHORIZED_ADVERTISERS_URL = (
    "https://business-api.tiktok.com/open_api/v1.3/oauth2/advertiser/get/"
)
MAX_DISCOVERED_ADVERTISERS = 200


class TikTokAccountRefreshError(RuntimeError):
    """A failed discovery must never erase previously linked accounts."""


async def authorized_advertiser_ids(access_token: str) -> list[str]:
    """Ask TikTok for the full *current* set authorized to the stored token."""
    app_id = os.environ.get("TIKTOK_MARKETING_APP_ID", "").strip()
    secret = os.environ.get("TIKTOK_MARKETING_APP_SECRET", "").strip()
    if not access_token or not app_id or not secret:
        raise TikTokAccountRefreshError("tiktok_discovery_auth_missing")
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.get(
            TIKTOK_AUTHORIZED_ADVERTISERS_URL,
            headers={"Access-Token": access_token},
            params={"app_id": app_id, "secret": secret},
        )
    try:
        data = _tiktok_payload(response, "tiktok_authorized_advertisers")
    except Exception as exc:
        raise TikTokAccountRefreshError("tiktok_discovery_provider_unavailable") from exc
    entries = data.get("list")
    if entries is None:
        entries = data.get("advertisers")
    if not isinstance(entries, list):
        raise TikTokAccountRefreshError("tiktok_discovery_response_incomplete")
    # This OAuth listing endpoint has no pagination parameters. If the provider
    # explicitly indicates additional pages, reject an incomplete inventory.
    page_info = data.get("page_info")
    if isinstance(page_info, dict) and page_info.get("has_more") is True:
        raise TikTokAccountRefreshError("tiktok_discovery_pagination_incomplete")
    ids: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise TikTokAccountRefreshError("tiktok_discovery_invalid_advertiser")
        account_id = str(entry.get("advertiser_id") or "").strip()
        if not account_id:
            raise TikTokAccountRefreshError("tiktok_discovery_missing_advertiser_id")
        if account_id not in seen:
            seen.add(account_id)
            ids.append(account_id)
    if not ids:
        raise TikTokAccountRefreshError("tiktok_discovery_no_authorized_accounts")
    if len(ids) > MAX_DISCOVERED_ADVERTISERS:
        raise TikTokAccountRefreshError("tiktok_discovery_account_limit")
    return ids


async def refresh_tiktok_authorized_accounts(
    db: Any,
    user_id: str,
    access_token: str,
    observed_at: str,
) -> dict[str, Any]:
    """Non-destructively upsert authorized advertisers, retaining prior links.

    Never delete old accounts, owner choices, funding bindings, data, or their
    financial provenance if the API is temporarily down or an account vanishes
    from the current authorization. Revocation is a separate review workflow.
    """
    ids = await authorized_advertiser_ids(access_token)
    try:
        details = await discover_tiktok_advertisers(access_token, ids)
    except Exception as exc:
        raise TikTokAccountRefreshError("tiktok_discovery_profile_unavailable") from exc
    profiles = {
        str(item.get("external_account_id") or "").strip(): item
        for item in details if isinstance(item, dict)
    }
    if set(profiles) != set(ids):
        raise TikTokAccountRefreshError("tiktok_discovery_account_list_mismatch")
    accounts = db.mezan_integration_accounts_v2
    for account_id in ids:
        details_row = profiles[account_id]
        patch: dict[str, Any] = {
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "last_authorized_at": observed_at,
            "last_observed_at": observed_at,
        }
        for key in ("display_name", "currency", "timezone", "account_status"):
            value = details_row.get(key)
            if isinstance(value, str) and value.strip():
                patch[key] = value.strip()
        await accounts.update_one(
            {
                "user_id": user_id,
                "provider": TIKTOK_PROVIDER_ID,
                "external_account_id": account_id,
            },
            {
                "$set": patch,
                "$setOnInsert": {
                    "user_id": user_id,
                    "provider": TIKTOK_PROVIDER_ID,
                    "mezan_integration_account_id": str(
                        uuid.uuid5(uuid.NAMESPACE_URL, f"mezan-tiktok:{user_id}:{account_id}")
                    ),
                    "external_account_id": account_id,
                    "ad_account_id": account_id,
                    "permissions": [TIKTOK_PERMISSION_ALIAS],
                    "permissions_observed": True,
                    "capability_evidence": list(TIKTOK_CAPABILITY_EVIDENCE),
                    "has_data": False,
                    "health_score": None,
                    "last_sync_at": None,
                    "tiktok_backfill_next_date": (
                        datetime.now(timezone.utc).date() - timedelta(days=29)
                    ).isoformat(),
                    "source_mode": TIKTOK_SOURCE_MODE,
                    "created_at": observed_at,
                },
            },
            upsert=True,
        )
    # Diagnostic inventory only: do not mutate the token or its encryption.
    await db[TIKTOK_CREDENTIALS_COLLECTION].update_one(
        {"user_id": user_id, "provider": TIKTOK_PROVIDER_ID},
        {"$set": {"advertiser_ids": sorted(ids), "authorized_checked_at": observed_at}},
    )
    return {
        "status": "complete",
        "authorized_accounts": len(ids),
        "account_ids": ids,
    }


__all__ = [
    "MAX_DISCOVERED_ADVERTISERS",
    "TikTokAccountRefreshError",
    "authorized_advertiser_ids",
    "refresh_tiktok_authorized_accounts",
]
