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
    """Return distinct base price, sale price, and optional sale dates.

    Salla may expose the currently charged price in ``price`` while keeping the
    pre-discount value in a separate regular/original-price field.  Explicit
    regular-price fields therefore always win over the generic ``price`` value.
    """
    price_raw = raw.get("price")
    discount = raw.get("discount") if isinstance(raw.get("discount"), dict) else {}
    period = raw.get("sale_period") if isinstance(raw.get("sale_period"), dict) else {}

    explicit_base = _first(
        raw,
        "regular_price", "original_price", "base_price", "list_price",
        "price_before_discount", "before_discount", "compare_at_price",
    ) or _first(
        discount,
        "regular_price", "original_price", "base_price", "before_discount",
    )
    explicit_sale = _first(
        raw,
        "sale_price", "discount_price", "offer_price", "special_price",
        "final_price", "price_after_discount", "after_discount",
    ) or _first(
        discount,
        "sale_price", "discount_price", "final_price", "after_discount", "price",
    )

    currency = None
    nested_base = None
    nested_sale = None
    if isinstance(price_raw, dict):
        nested_base = _first(
            price_raw,
            "regular", "regular_price", "original", "original_price",
            "before_discount", "base", "list",
        )
        nested_sale = _first(
            price_raw,
            "sale", "sale_price", "discounted", "discount_price",
            "after_discount", "special", "final",
        )
        generic_price = _first(price_raw, "amount", "value", "price", "total")
        currency = price_raw.get("currency")
    else:
        generic_price = price_raw

    base_price = _number(explicit_base if explicit_base is not None else nested_base)
    sale_price = _number(explicit_sale if explicit_sale is not None else nested_sale)
    generic_amount = _number(generic_price)

    # A generic price is the base price only when no explicit regular price was
    # provided.  If an explicit sale exists and the generic price equals it, do
    # not duplicate the discounted price into the base-price field.
    if base_price is None:
        if sale_price is None or generic_amount != sale_price:
            base_price = generic_amount

    # Some Salla responses return the current discounted amount in ``price`` and
    # the old price in ``regular_price``.  In that shape the generic amount is
    # the sale price when no explicit sale field exists.
    if sale_price is None and base_price is not None and generic_amount is not None and generic_amount != base_price:
        sale_price = generic_amount

    # Never expose an identical base/sale pair.  An equal pair means there is no
    # separate discount value in the response.
    if sale_price is not None and base_price is not None and sale_price == base_price:
        sale_price = None

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
