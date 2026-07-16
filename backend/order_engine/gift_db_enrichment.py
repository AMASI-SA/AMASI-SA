"""Apply the single approved gift-order rule to canonical orders.

Business rule
-------------
An order is a gift if and only if Salla's stored order source is exactly
``buy_as_gift``. Product names, notes, tags, recipients and legacy gift fields
must not influence the badge.
"""
from __future__ import annotations

from typing import Any

from .models import OrderDTO


_GIFT_SOURCE = "buy_as_gift"


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _named_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("source", "name", "label", "title", "value", "slug", "code", "type"):
        candidate = value.get(key)
        if candidate not in (None, "", [], {}):
            return candidate
    return None


def _nested(row: dict[str, Any], *path: str) -> Any:
    current: Any = row
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _stored_source(row: dict[str, Any]) -> str:
    """Read the provider source from known durable Salla snapshot paths."""
    candidates = (
        row.get("source"),
        row.get("source_native"),
        row.get("utm_source"),
        row.get("order_source"),
        _nested(row, "raw_by_source", "salla_direct", "source"),
        _nested(row, "raw_by_source", "salla_direct", "source_native"),
        _nested(row, "raw_by_source", "salla_direct", "utm_source"),
        _nested(row, "raw_by_source", "salla_direct", "utm", "source"),
        _nested(row, "raw_by_source", "salla_direct", "marketing", "utm_source"),
        _nested(row, "raw_by_source", "salla_direct", "attribution", "utm_source"),
    )
    for value in candidates:
        normalized = _norm(_named_value(value))
        if normalized:
            return normalized
    return ""


async def enrich_order_gifts(
    db: Any,
    *,
    user_id: str,
    orders: list[OrderDTO],
) -> list[OrderDTO]:
    """Force is_gift from the single approved ``buy_as_gift`` condition."""
    if not orders:
        return orders

    order_numbers = [str(order.order_number) for order in orders]
    cursor = db.unified_orders.find(
        {
            "user_id": str(user_id),
            "order_number": {"$in": order_numbers},
        },
        {
            "_id": 0,
            "order_number": 1,
            "source": 1,
            "source_native": 1,
            "utm_source": 1,
            "order_source": 1,
            "raw_by_source.salla_direct.source": 1,
            "raw_by_source.salla_direct.source_native": 1,
            "raw_by_source.salla_direct.utm_source": 1,
            "raw_by_source.salla_direct.utm.source": 1,
            "raw_by_source.salla_direct.marketing.utm_source": 1,
            "raw_by_source.salla_direct.attribution.utm_source": 1,
        },
    )

    gift_by_number: dict[str, bool] = {}
    async for row in cursor:
        number = str(row.get("order_number") or "").strip()
        if number:
            gift_by_number[number] = _stored_source(row) == _GIFT_SOURCE

    return [
        order.model_copy(
            update={"is_gift": gift_by_number.get(str(order.order_number), False)}
        )
        for order in orders
    ]
