"""Privacy-safe abandoned-cart evidence for Campaign AI Decision Intelligence V3.

The source is the existing Salla abandoned-cart analytics collection.  Plaintext
customer identity/address data is intentionally not decrypted for OpenAI.
Campaign/ad identifiers are used only when the cart itself carries verified or
linked attribution; otherwise the evidence is explicitly store-level
corroboration and must never become fabricated campaign revenue.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from salla_integration.abandoned_carts import ABANDONED_CART_COLLECTION


MAX_CARTS = 5000
MAX_PRODUCTS = 25


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed == parsed and abs(parsed) != float("inf") else 0.0


def _entity_key(row: dict[str, Any]) -> str:
    return "|".join((
        str(row.get("provider") or ""),
        str(row.get("entity_level") or ""),
        str(row.get("account_id") or ""),
        str(row.get("entity_id") or ""),
    ))


def _campaign_id(row: dict[str, Any]) -> str:
    if row.get("entity_level") == "campaign":
        return str(row.get("entity_id") or "")
    return str(row.get("campaign_id") or "")


def _cart_day(cart: dict[str, Any]) -> str:
    value = cart.get("cart_updated_at") or cart.get("updated_at") or cart.get("cart_created_at")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date().isoformat()
    return str(value or "")[:10]


def _cart_platform(cart: dict[str, Any]) -> str | None:
    attribution = cart.get("attribution") if isinstance(cart.get("attribution"), dict) else {}
    platform = _text(attribution.get("platform") or attribution.get("utm_source"), 80).casefold()
    if not platform:
        return None
    if "snap" in platform:
        return "snapchat"
    if platform in {"meta", "facebook", "instagram"}:
        return "meta"
    if "tiktok" in platform:
        return "tiktok"
    if "google" in platform:
        return "google"
    return platform


def _cart_matches_campaign(cart: dict[str, Any], row: dict[str, Any]) -> bool:
    attribution = cart.get("attribution") if isinstance(cart.get("attribution"), dict) else {}
    campaign = str(attribution.get("campaign_id") or "")
    expected = _campaign_id(row)
    if not campaign or not expected or campaign != expected:
        return False
    platform = _cart_platform(cart)
    return platform is None or platform == str(row.get("provider") or "")


def _summarize(carts: list[dict[str, Any]], *, scope: str) -> dict[str, Any]:
    abandoned = [cart for cart in carts if not cart.get("purchased")]
    purchased = [cart for cart in carts if cart.get("purchased")]
    products: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "carts": 0,
        "units": 0.0,
        "value_sar": 0.0,
        "name": None,
        "variants": Counter(),
        "options": Counter(),
    })
    total_value = 0.0
    subtotal = 0.0
    discount = 0.0
    coupon_carts = 0
    attributed_platforms = Counter()
    for cart in abandoned:
        total_value += _number(cart.get("total"))
        subtotal += _number(cart.get("subtotal"))
        discount += _number(cart.get("discount"))
        coupon_carts += int(bool(cart.get("coupon_present")))
        platform = _cart_platform(cart)
        if platform:
            attributed_platforms[platform] += 1
        seen_products: set[str] = set()
        for item in cart.get("items") or []:
            if not isinstance(item, dict):
                continue
            product_id = _text(item.get("product_id"), 160) or _text(item.get("sku"), 160)
            if not product_id:
                continue
            row = products[product_id]
            if product_id not in seen_products:
                row["carts"] += 1
                seen_products.add(product_id)
            row["units"] += _number(item.get("quantity")) or 1.0
            row["value_sar"] += _number(item.get("total_price"))
            row["name"] = row["name"] or _text(item.get("name"), 300) or None
            variant_id = _text(item.get("variant_id"), 160)
            if variant_id:
                row["variants"][variant_id] += 1
            for option in item.get("options") or []:
                if not isinstance(option, dict):
                    continue
                label = "=".join(filter(None, (
                    _text(option.get("name"), 100),
                    _text(option.get("value"), 160),
                )))
                if label:
                    row["options"][label] += 1
    product_rows = []
    for product_id, row in products.items():
        product_rows.append({
            "product_id": product_id,
            "name": row["name"],
            "abandoned_carts": row["carts"],
            "units": round(row["units"], 2),
            "value_sar": round(row["value_sar"], 2),
            "top_variants": row["variants"].most_common(5),
            "top_options": row["options"].most_common(8),
        })
    product_rows.sort(key=lambda item: (-item["abandoned_carts"], -item["value_sar"]))
    return {
        "scope": scope,
        "cart_snapshots": len(carts),
        "abandoned_count": len(abandoned),
        "purchased_or_recovered_count": len(purchased),
        "abandoned_value_sar": round(total_value, 2),
        "abandoned_subtotal_sar": round(subtotal, 2),
        "discount_sar": round(discount, 2),
        "coupon_present_count": coupon_carts,
        "top_products": product_rows[:MAX_PRODUCTS],
        "attributed_platform_counts": dict(attributed_platforms),
        "payment_method_data_available": False,
        "shipping_cost_data_available": False,
        "city_country_data_sent_to_openai": False,
        "privacy_note": "plaintext customer identity and address are not decrypted for AI analysis",
    }


async def build_abandoned_cart_evidence(
    db: Any,
    user_id: str,
    candidates: list[dict[str, Any]],
    *,
    end: date,
) -> dict[str, Any]:
    start = end - timedelta(days=29)
    cursor = db[ABANDONED_CART_COLLECTION].find(
        {
            "user_id": user_id,
            "$or": [
                {"cart_updated_at": {"$gte": start.isoformat(), "$lte": end.isoformat() + "T23:59:59.999999+00:00"}},
                {"cart_created_at": {"$gte": start.isoformat(), "$lte": end.isoformat() + "T23:59:59.999999+00:00"}},
            ],
        },
        {
            "_id": 0,
            "user_id": 0,
            "private_cart_context_encrypted": 0,
            "customer_identity_id": 0,
            "customer_private_profile_encrypted": 0,
        },
    ).limit(MAX_CARTS)
    carts = await cursor.to_list(length=MAX_CARTS)

    windows = {
        "today": (end, end),
        "yesterday": (end - timedelta(days=1), end - timedelta(days=1)),
        "day_minus_2": (end - timedelta(days=2), end - timedelta(days=2)),
        "last_7d": (end - timedelta(days=6), end),
        "last_30d": (start, end),
    }
    store_windows: dict[str, Any] = {}
    for label, (window_start, window_end) in windows.items():
        selected = [
            cart for cart in carts
            if window_start.isoformat() <= _cart_day(cart) <= window_end.isoformat()
        ]
        store_windows[label] = _summarize(selected, scope="store_level_corroborating_evidence")

    entities: dict[str, Any] = {}
    for row in candidates:
        attributed = [cart for cart in carts if _cart_matches_campaign(cart, row)]
        entities[_entity_key(row)] = {
            "campaign_id": _campaign_id(row) or None,
            "direct_campaign_attribution_available": bool(attributed),
            "campaign_attributed_last_30d": (
                _summarize(attributed, scope="cart_attribution_campaign_match")
                if attributed
                else None
            ),
            "store_level_windows": store_windows,
            "causality_guard": (
                "store-level carts corroborate checkout friction only; they are not campaign revenue "
                "unless the cart itself carries matching campaign attribution"
            ),
        }
    return {
        "schema_version": "campaign_ai_abandoned_cart_evidence_v3",
        "entities": entities,
        "limitations": [
            "shipping_cost_not_present_in_current_abandoned_cart_analytics_contract",
            "payment_method_not_present_in_current_abandoned_cart_analytics_contract",
            "customer_city_country_kept_private_until_safe_aggregate_is_available",
        ],
    }


__all__ = ["build_abandoned_cart_evidence"]
