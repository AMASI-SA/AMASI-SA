"""Resolve order attribution campaign IDs to stored ad campaign names.

Read-only enrichment. It never calls an advertising API and never mutates the
order source records. The lookup uses campaign catalogs/metrics already stored
inside Mezan for the authenticated owner.
"""
from __future__ import annotations

import re
from typing import Any

from .models import OrderDTO


_UUID_LIKE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _looks_like_campaign_id(value: Any) -> bool:
    text = _text(value)
    if not text:
        return False
    return bool(_UUID_LIKE.fullmatch(text) or text.isdigit() or len(text) >= 24)


async def _lookup_campaign_name(db: Any, *, user_id: str, campaign_id: str) -> str | None:
    """Return the newest non-empty campaign name from Mezan-owned ad data."""

    lookups = (
        (
            "ads_v2_spend_raw",
            {"user_id": user_id, "dimension_keys.campaign_id": campaign_id},
            {"dimension_keys.campaign_name": 1, "fetched_at": 1},
            "dimension_keys.campaign_name",
            [("fetched_at", -1)],
        ),
        (
            "snapchat_account_daily",
            {"user_id": user_id, "campaign_id": campaign_id},
            {"campaign_name": 1, "updated_at": 1, "date": 1},
            "campaign_name",
            [("date", -1), ("updated_at", -1)],
        ),
        (
            "snapchat_ads_daily",
            {"user_id": user_id, "campaign_id": campaign_id},
            {"campaign_name": 1, "updated_at": 1, "date": 1},
            "campaign_name",
            [("date", -1), ("updated_at", -1)],
        ),
        (
            "meta_ads_daily",
            {"user_id": user_id, "campaign_id": campaign_id},
            {"campaign_name": 1, "updated_at": 1, "date": 1},
            "campaign_name",
            [("date", -1), ("updated_at", -1)],
        ),
        (
            "tiktok_ads_daily",
            {"user_id": user_id, "campaign_id": campaign_id},
            {"campaign_name": 1, "updated_at": 1, "date": 1},
            "campaign_name",
            [("date", -1), ("updated_at", -1)],
        ),
    )

    for collection_name, query, projection, name_path, sort in lookups:
        try:
            row = await db[collection_name].find_one(query, projection, sort=sort)
        except Exception:
            # One optional legacy collection/index must not break order details.
            continue
        if not row:
            continue
        value: Any = row
        for part in name_path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        name = _text(value)
        if name and name != campaign_id:
            return name
    return None


async def enrich_order_campaigns(
    db: Any,
    *,
    user_id: str,
    orders: list[OrderDTO],
) -> list[OrderDTO]:
    """Add campaign_id/campaign_name without replacing the original UTM fact."""

    cache: dict[str, str | None] = {}
    enriched: list[OrderDTO] = []

    for order in orders:
        source = order.source
        raw_campaign = _text(source.campaign_name or source.utm_campaign)
        campaign_id = _text(source.campaign_id)
        campaign_name = _text(source.campaign_name)

        if not campaign_id and _looks_like_campaign_id(raw_campaign):
            campaign_id = raw_campaign
        elif not campaign_name and raw_campaign and not _looks_like_campaign_id(raw_campaign):
            campaign_name = raw_campaign

        if campaign_id and not campaign_name:
            if campaign_id not in cache:
                cache[campaign_id] = await _lookup_campaign_name(
                    db,
                    user_id=str(user_id),
                    campaign_id=campaign_id,
                )
            campaign_name = cache[campaign_id]

        enriched.append(
            order.model_copy(
                update={
                    "source": source.model_copy(
                        update={
                            "campaign_id": campaign_id,
                            "campaign_name": campaign_name,
                        }
                    )
                }
            )
        )

    return enriched
