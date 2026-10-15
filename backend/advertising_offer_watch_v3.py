"""Fast sale/offer consistency watch for products under active ad spend.

Runs alongside Product Watch. It emits objective alerts only; it never extends a
promotion, edits Salla, pauses ads, or chooses a marketing action.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from advertising_product_watch_v3 import (
    ALERT_COLLECTION,
    PRODUCT_V2_COLLECTION,
    _campaign_spend,
    _effective_verified_links,
    _record_alert,
)
from campaign_ai_offer_schedule_v3 import build_offer_schedule_evidence


OFFER_ALERT_CODES = (
    "SALE_EXPIRING_WHILE_AD_SPEND",
    "SALE_EXPIRED_WHILE_AD_SPEND",
    "EXPIRED_PROMOTION_COPY_WHILE_AD_SPEND",
    "PROMOTION_COPY_WITHOUT_ACTIVE_SALE",
)
PRE_EXPIRY_HOURS = 24


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _resolve_stale_alerts(
    db: Any,
    user_id: str,
    active_keys: set[str],
    current: datetime,
) -> None:
    selector: dict[str, Any] = {
        "user_id": user_id,
        "status": "active",
        "code": {"$in": list(OFFER_ALERT_CODES)},
    }
    if active_keys:
        selector["alert_key"] = {"$nin": sorted(active_keys)}
    await db[ALERT_COLLECTION].update_many(
        selector,
        {"$set": {
            "status": "resolved",
            "resolved_at": current,
            "updated_at": current,
        }},
    )


async def scan_user_offer_watch(
    db: Any,
    user_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or _utcnow()).astimezone(timezone.utc)
    links = await _effective_verified_links(db, user_id, current)
    spend_cache: dict[tuple[str, str, str], dict[str, Any] | None] = {}
    product_cache: dict[str, dict[str, Any]] = {}
    active_keys: set[str] = set()
    watched = 0

    for link in links:
        spend_key = (
            str(link.get("provider") or ""),
            str(link.get("account_id") or ""),
            str(link.get("campaign_id") or ""),
        )
        if spend_key not in spend_cache:
            spend_cache[spend_key] = await _campaign_spend(
                db,
                user_id,
                link,
                current.date(),
            )
        spend = spend_cache.get(spend_key)
        if not spend:
            continue

        product_id = str(link.get("product_id") or "")
        if not product_id:
            continue
        if product_id not in product_cache:
            product_cache[product_id] = await db[PRODUCT_V2_COLLECTION].find_one(
                {"user_id": user_id, "salla_product_id": product_id},
                {
                    "_id": 0,
                    "salla_product_id": 1,
                    "name": 1,
                    "description": 1,
                    "short_description": 1,
                    "price": 1,
                    "sale_price": 1,
                    "sale_starts_at": 1,
                    "sale_ends_at": 1,
                },
            ) or {}
        product = product_cache[product_id]
        if not product:
            continue
        watched += 1

        offer = build_offer_schedule_evidence(
            product,
            now=current,
            expiring_hours=PRE_EXPIRY_HOURS,
        )
        state = str(offer.get("state") or "")
        promotion_copy = offer.get("promotion_copy") or {}
        alerts: list[tuple[str, str, dict[str, Any]]] = []

        if state == "expiring":
            alerts.append((
                "SALE_EXPIRING_WHILE_AD_SPEND",
                "high",
                {
                    "offer_schedule": offer,
                    "pre_expiry_hours": PRE_EXPIRY_HOURS,
                    "operator_note": "promotion is near its configured end while linked campaign is spending",
                },
            ))

        if state == "expired":
            alerts.append((
                "SALE_EXPIRED_WHILE_AD_SPEND",
                "critical",
                {
                    "offer_schedule": offer,
                    "operator_note": "configured sale end has passed while linked campaign still has spend",
                },
            ))
            if promotion_copy.get("detected"):
                alerts.append((
                    "EXPIRED_PROMOTION_COPY_WHILE_AD_SPEND",
                    "critical",
                    {
                        "offer_schedule": offer,
                        "operator_note": "product copy still contains promotion wording after configured sale expiry",
                    },
                ))
        elif state == "no_schedule" and promotion_copy.get("detected") and not offer.get("discounted_price_verified"):
            alerts.append((
                "PROMOTION_COPY_WITHOUT_ACTIVE_SALE",
                "medium",
                {
                    "offer_schedule": offer,
                    "limitation": "a coupon or external promotion may exist and must be verified before declaring the copy stale",
                },
            ))

        for code, severity, evidence in alerts:
            alert_key = await _record_alert(
                db,
                user_id=user_id,
                link=link,
                spend=spend,
                product=product,
                code=code,
                severity=severity,
                evidence=evidence,
                now=current,
            )
            active_keys.add(alert_key)

    await _resolve_stale_alerts(db, user_id, active_keys, current)
    return {
        "user_id": user_id,
        "watched_offer_links": watched,
        "active_offer_alerts": len(active_keys),
        "pre_expiry_hours": PRE_EXPIRY_HOURS,
        "marketing_decision": False,
        "provider_write_reached": False,
        "salla_write_reached": False,
    }


__all__ = [
    "OFFER_ALERT_CODES",
    "PRE_EXPIRY_HOURS",
    "scan_user_offer_watch",
]
