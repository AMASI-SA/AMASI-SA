"""Read-only TikTok Marketing API advertiser discovery."""
from __future__ import annotations

import json
from typing import Any

import httpx

from .tiktok_oauth_security import _tiktok_payload

TIKTOK_ADVERTISER_INFO_URL = (
    "https://business-api.tiktok.com/open_api/v1.3/advertiser/info/"
)


def _string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_advertiser(row: dict[str, Any], fallback_id: str) -> dict[str, Any]:
    advertiser_id = _string(row.get("advertiser_id")) or fallback_id
    return {
        "external_account_id": advertiser_id,
        "ad_account_id": advertiser_id,
        "display_name": (
            _string(row.get("name"))
            or _string(row.get("advertiser_name"))
            or f"TikTok Ads {advertiser_id}"
        ),
        "currency": _string(row.get("currency")),
        "timezone": _string(row.get("timezone")),
        "account_status": _string(row.get("status")),
    }


async def discover_tiktok_advertisers(
    access_token: str,
    advertiser_ids: list[str],
) -> list[dict[str, Any]]:
    ids = list(dict.fromkeys(str(item).strip() for item in advertiser_ids if str(item).strip()))
    if not ids:
        raise RuntimeError("tiktok_advertiser_discovery_no_authorized_accounts")

    discovered: list[dict[str, Any]] = []
    headers = {
        "Access-Token": access_token,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        for offset in range(0, len(ids), 100):
            batch = ids[offset : offset + 100]
            response = await client.get(
                TIKTOK_ADVERTISER_INFO_URL,
                headers=headers,
                params={
                    "advertiser_ids": json.dumps(batch, separators=(",", ":")),
                    "fields": json.dumps(
                        ["advertiser_id", "name", "currency", "timezone", "status"],
                        separators=(",", ":"),
                    ),
                },
            )
            data = _tiktok_payload(response, "tiktok_advertiser_info")
            rows = data.get("list") or data.get("advertisers") or []
            by_id = {
                str(row.get("advertiser_id") or "").strip(): row
                for row in rows
                if isinstance(row, dict)
            }
            for advertiser_id in batch:
                discovered.append(
                    _normalize_advertiser(by_id.get(advertiser_id) or {}, advertiser_id)
                )
    return discovered
