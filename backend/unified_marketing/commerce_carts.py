"""Provider-neutral, privacy-safe abandoned-cart evidence for marketing reports."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any

MAX_CARTS = 10_000
MAX_PRODUCTS = 20
ABANDONED_CART_COLLECTION = "salla_abandoned_carts_v1"


def _text(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed == parsed and abs(parsed) != float("inf") else 0.0


def _day(cart: dict[str, Any]) -> str:
    value = cart.get("cart_updated_at") or cart.get("cart_created_at") or cart.get("updated_at")
    return value.date().isoformat() if hasattr(value, "date") else _text(value, 40)[:10]


def _platform(cart: dict[str, Any]) -> str | None:
    attribution = cart.get("attribution") if isinstance(cart.get("attribution"), dict) else {}
    raw = _text(attribution.get("platform") or attribution.get("utm_source"), 80).casefold()
    if not raw:
        return None
    if "snap" in raw:
        return "snapchat_ads"
    if raw in {"meta", "facebook", "instagram"}:
        return "meta_ads"
    if "tiktok" in raw:
        return "tiktok_ads"
    if "google" in raw:
        return "google_ads"
    return raw


def _campaign_id(cart: dict[str, Any]) -> str:
    attribution = cart.get("attribution") if isinstance(cart.get("attribution"), dict) else {}
    return _text(attribution.get("campaign_id"), 160)


def _summary(carts: list[dict[str, Any]], *, scope: str) -> dict[str, Any]:
    abandoned = [cart for cart in carts if not cart.get("purchased")]
    recovered = [cart for cart in carts if cart.get("purchased")]
    products: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"name": None, "carts": 0, "units": 0.0, "value_sar": 0.0}
    )
    for cart in abandoned:
        seen: set[str] = set()
        for item in cart.get("items") or []:
            if not isinstance(item, dict):
                continue
            product_id = _text(item.get("product_id") or item.get("sku"), 160)
            if not product_id:
                continue
            product = products[product_id]
            product["name"] = product["name"] or _text(item.get("name")) or None
            product["units"] += _number(item.get("quantity")) or 1.0
            product["value_sar"] += _number(item.get("total_price"))
            if product_id not in seen:
                product["carts"] += 1
                seen.add(product_id)
    product_rows = [
        {
            "product_id": product_id,
            "name": value["name"],
            "abandoned_carts": int(value["carts"]),
            "units": round(value["units"], 2),
            "value_sar": round(value["value_sar"], 2),
        }
        for product_id, value in products.items()
    ]
    product_rows.sort(key=lambda row: (-row["abandoned_carts"], -row["value_sar"]))
    return {
        "status": "complete",
        "scope": scope,
        "cart_snapshots": len(carts),
        "abandoned_carts": len(abandoned),
        "recovered_carts": len(recovered),
        "abandoned_value_sar": round(sum(_number(cart.get("total")) for cart in abandoned), 2),
        "top_products": product_rows[:MAX_PRODUCTS],
    }


async def load_abandoned_cart_outcomes(
    db: Any,
    user_id: str,
    *,
    provider: str,
    campaign_ids: list[str],
    date_from: date,
    date_to: date,
) -> dict[str, Any]:
    projection = {
        "_id": 0,
        "cart_id": 1,
        "purchased": 1,
        "total": 1,
        "items": 1,
        "attribution": 1,
        "cart_created_at": 1,
        "cart_updated_at": 1,
    }
    cursor = db[ABANDONED_CART_COLLECTION].find({"user_id": str(user_id)}, projection).limit(MAX_CARTS + 1)
    carts = await cursor.to_list(length=MAX_CARTS + 1)
    if len(carts) > MAX_CARTS:
        carts = carts[:MAX_CARTS]
        truncated = True
    else:
        truncated = False
    start = date_from.isoformat()
    end = date_to.isoformat()
    carts = [cart for cart in carts if start <= _day(cart) <= end]
    expected = {_text(value, 160) for value in campaign_ids if _text(value, 160)}
    by_campaign: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unattributed: list[dict[str, Any]] = []
    foreign: Counter[str] = Counter()
    for cart in carts:
        campaign_id = _campaign_id(cart)
        platform = _platform(cart)
        if campaign_id in expected and platform in {None, provider}:
            by_campaign[campaign_id].append(cart)
        elif platform and platform != provider:
            foreign[platform] += 1
        else:
            unattributed.append(cart)
    return {
        "by_campaign": {
            campaign_id: _summary(rows, scope="exact_cart_campaign_id_match")
            for campaign_id, rows in by_campaign.items()
        },
        "store_level": _summary(unattributed, scope="store_level_corroborating_only"),
        "coverage": {
            "status": "complete",
            "source_collection": ABANDONED_CART_COLLECTION,
            "attribution_policy": "exact_campaign_id_and_compatible_platform_only",
            "store_level_is_not_campaign_revenue": True,
            "foreign_platform_counts": dict(foreign),
            "truncated": truncated,
            "privacy_safe": True,
            "read_only": True,
        },
    }


__all__ = ["load_abandoned_cart_outcomes"]
