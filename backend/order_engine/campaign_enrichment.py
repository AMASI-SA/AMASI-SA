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
            "mezan_snapchat_entity_facts_v2",
            {"user_id": user_id, "entity_type": "campaign", "external_id": campaign_id},
            {"name": 1, "updated_at": 1},
            "name",
            [("updated_at", -1)],
        ),
        (
            "mezan_meta_campaign_performance_daily_v2",
            {"user_id": user_id, "campaign_id": campaign_id},
            {"campaign_name": 1, "updated_at": 1, "date": 1},
            "campaign_name",
            [("date", -1), ("updated_at", -1)],
        ),
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


async def _lookup_snapchat_entity_name(
    db: Any, *, user_id: str, entity_type: str, entity_id: str,
) -> str | None:
    try:
        row = await db["mezan_snapchat_entity_facts_v2"].find_one(
            {"user_id": str(user_id), "entity_type": entity_type, "external_id": entity_id},
            {"name": 1, "updated_at": 1}, sort=[("updated_at", -1)],
        )
    except Exception:
        return None
    name = _text((row or {}).get("name"))
    return name if name and name != entity_id else None


async def enrich_order_campaigns(
    db: Any,
    *,
    user_id: str,
    orders: list[OrderDTO],
) -> list[OrderDTO]:
    """Add campaign identity while preserving the original UTM provider fact.

    Until Mezan has the campaign catalog/name, the visible campaign value falls
    back to the campaign ID instead of showing an unavailable placeholder.
    """

    cache: dict[str, str | None] = {}
    entity_cache: dict[tuple[str, str], str | None] = {}
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

        # Salla commonly places the numeric campaign ID in utm_campaign.
        # canonical_order_source preserves that provider value as a possible
        # campaign name, so normalize it back to identity before catalog lookup.
        if campaign_name and _looks_like_campaign_id(campaign_name):
            campaign_id = campaign_id or campaign_name
            campaign_name = None

        if campaign_id and not campaign_name:
            if campaign_id not in cache:
                cache[campaign_id] = await _lookup_campaign_name(
                    db,
                    user_id=str(user_id),
                    campaign_id=campaign_id,
                )
            campaign_name = cache[campaign_id] or campaign_id

        squad_name = source.ad_squad_name
        if source.ad_squad_id and not squad_name:
            key = ("ad_squad", source.ad_squad_id)
            if key not in entity_cache:
                entity_cache[key] = await _lookup_snapchat_entity_name(
                    db, user_id=str(user_id), entity_type=key[0], entity_id=key[1],
                )
            squad_name = entity_cache[key] or source.ad_squad_id
        ad_name = source.ad_name
        if source.ad_id and not ad_name:
            key = ("ad", source.ad_id)
            if key not in entity_cache:
                entity_cache[key] = await _lookup_snapchat_entity_name(
                    db, user_id=str(user_id), entity_type=key[0], entity_id=key[1],
                )
            ad_name = entity_cache[key] or source.ad_id

        enriched.append(
            order.model_copy(
                update={
                    "source": source.model_copy(
                        update={
                            "campaign_id": campaign_id,
                            "campaign_name": campaign_name,
                            "ad_squad_name": squad_name,
                            "ad_name": ad_name,
                        }
                    )
                }
            )
        )

    return enriched
