"""Normalize Salla base/sale prices and sale schedule fields."""
from __future__ import annotations

from typing import Any


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("amount", "value", "price", "total"):
            if key in value:
                parsed = _number(value.get(key))
                if parsed is not None:
                    return parsed
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def normalize_product_prices(raw: dict[str, Any]) -> dict[str, Any]:
    """Return distinct base price, sale price, and optional sale dates."""
    price_raw = raw.get("price")
    sale_raw = _first(raw, "sale_price", "discount_price", "offer_price", "special_price")

    if isinstance(price_raw, dict):
        base_value = _first(price_raw, "regular", "regular_price", "original", "before_discount", "amount", "value")
        nested_sale = _first(price_raw, "sale", "sale_price", "discounted", "after_discount", "special")
        base_price = _number(base_value)
        sale_price = _number(sale_raw if sale_raw is not None else nested_sale)
        currency = price_raw.get("currency")
    else:
        base_price = _number(price_raw)
        sale_price = _number(sale_raw)
        currency = None

    period = raw.get("sale_period") if isinstance(raw.get("sale_period"), dict) else {}
    discount = raw.get("discount") if isinstance(raw.get("discount"), dict) else {}
    starts_at = _first(
        raw, "sale_starts_at", "sale_start", "discount_start", "discount_starts_at",
        "offer_start", "special_price_start",
    ) or _first(period, "start", "starts_at", "from") or _first(discount, "start", "starts_at", "from")
    ends_at = _first(
        raw, "sale_ends_at", "sale_end", "discount_end", "discount_ends_at",
        "offer_end", "special_price_end",
    ) or _first(period, "end", "ends_at", "to") or _first(discount, "end", "ends_at", "to")

    return {
        "price": base_price,
        "sale_price": sale_price,
        "sale_starts_at": starts_at,
        "sale_ends_at": ends_at,
        "currency": currency,
    }
