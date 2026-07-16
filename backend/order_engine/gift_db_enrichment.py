"""Enrich canonical orders from explicit gift fields stored in unified_orders.

The durable database snapshot may contain ``order_type = هدية`` even when the
Salla light/details payload retained under ``raw_by_source`` omits that fact.
This module performs one batched read and never infers gift status from product
names, notes, customers, or other weak signals.
"""
from __future__ import annotations

from typing import Any

from .models import OrderDTO


_GIFT_LABELS = {
    "gift",
    "gift order",
    "is gift",
    "هدية",
    "هديه",
    "طلب هدية",
    "طلب هديه",
    "طلب كهدية",
    "طلب كهديه",
    "إهداء",
    "اهداء",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").strip().casefold().split())


def _named_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("name", "label", "title", "value", "slug", "code", "type"):
        candidate = value.get(key)
        if candidate not in (None, "", [], {}):
            return candidate
    return None


def _explicit_is_gift(row: dict[str, Any]) -> bool:
    for key in ("is_gift", "gift", "gift_order"):
        value = _named_value(row.get(key))
        if isinstance(value, bool):
            if value:
                return True
            continue
        if isinstance(value, (int, float)) and value != 0:
            return True
        if _norm(value) in _GIFT_LABELS:
            return True

    return _norm(_named_value(row.get("order_type"))) in _GIFT_LABELS


async def enrich_order_gifts(
    db: Any,
    *,
    user_id: str,
    orders: list[OrderDTO],
) -> list[OrderDTO]:
    """Apply explicit stored gift facts to a page of canonical orders."""
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
            "is_gift": 1,
            "gift": 1,
            "gift_order": 1,
            "order_type": 1,
        },
    )

    explicit_by_number: dict[str, bool] = {}
    async for row in cursor:
        number = str(row.get("order_number") or "").strip()
        if number:
            explicit_by_number[number] = _explicit_is_gift(row)

    enriched: list[OrderDTO] = []
    for order in orders:
        stored_gift = explicit_by_number.get(str(order.order_number), False)
        if stored_gift and not order.is_gift:
            enriched.append(order.model_copy(update={"is_gift": True}))
        else:
            enriched.append(order)
    return enriched
