"""Read-only Order Engine service.

Sprint 001 bridge
-----------------
`unified_orders` is used only as a temporary discovery index.

Authoritative order facts come from:
    raw_by_source.salla_direct
        → map_salla_order()
        → OrderDTO

This module performs:
- No database writes
- No Salla HTTP calls
- No Qoyod calls
- No operational workflow mutations
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Optional

from .mapper import OrderMappingError, map_salla_order
from .models import OrderDTO


DEFAULT_LIMIT = 15
MAX_LIMIT = 50


class OrderNotFoundError(LookupError):
    """Raised when an exact Salla-backed order does not exist."""


class InvalidOrderCursorError(ValueError):
    """Raised when a list cursor is malformed."""


@dataclass(frozen=True)
class OrderPage:
    """Internal service result for keyset-paginated order lists."""

    items: list[OrderDTO]
    next_cursor: Optional[str]
    skipped_invalid: int = 0


def _normalise_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = DEFAULT_LIMIT

    return max(1, min(value, MAX_LIMIT))


def _encode_cursor(order_date: str, order_number: str) -> str:
    payload = json.dumps(
        {
            "order_date": str(order_date),
            "order_number": str(order_number),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> dict[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(cursor + padding)
        payload = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise InvalidOrderCursorError("invalid orders cursor") from exc

    order_date = str(payload.get("order_date") or "").strip()
    order_number = str(payload.get("order_number") or "").strip()

    if not order_date or not order_number:
        raise InvalidOrderCursorError("invalid orders cursor")

    return {
        "order_date": order_date,
        "order_number": order_number,
    }


def _salla_raw(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    raw_by_source = row.get("raw_by_source")
    if not isinstance(raw_by_source, dict):
        return None

    raw = raw_by_source.get("salla_direct")
    return raw if isinstance(raw, dict) else None


def _cursor_query(cursor: Optional[str]) -> dict[str, Any]:
    if not cursor:
        return {}

    decoded = _decode_cursor(cursor)
    order_date = decoded["order_date"]
    order_number = decoded["order_number"]

    return {
        "$or": [
            {"order_date": {"$lt": order_date}},
            {
                "order_date": order_date,
                "order_number": {"$lt": order_number},
            },
        ]
    }


async def list_orders(
    db: Any,
    *,
    user_id: str,
    limit: int = DEFAULT_LIMIT,
    cursor: Optional[str] = None,
) -> OrderPage:
    """Return newest Salla-backed orders using keyset pagination.

    `order_date` and `order_number` are used only for discovery pagination.
    The DTO's exact creation timestamp comes from the Salla raw payload.
    """

    safe_limit = _normalise_limit(limit)

    query: dict[str, Any] = {
        "user_id": str(user_id),
        "raw_by_source.salla_direct": {"$exists": True},
    }
    query.update(_cursor_query(cursor))

    projection = {
        "_id": 0,
        "order_number": 1,
        "order_date": 1,
        "raw_by_source.salla_direct": 1,
    }

    # Over-fetch so malformed historical rows do not unnecessarily shorten
    # the page. The public result is still capped at safe_limit.
    fetch_limit = min(MAX_LIMIT * 3, max(safe_limit * 3, safe_limit + 1))

    cursor_obj = (
        db.unified_orders.find(query, projection)
        .sort([("order_date", -1), ("order_number", -1)])
        .limit(fetch_limit)
    )

    items: list[OrderDTO] = []
    skipped_invalid = 0
    last_valid_row: Optional[dict[str, Any]] = None

    async for row in cursor_obj:
        raw = _salla_raw(row)
        if raw is None:
            skipped_invalid += 1
            continue

        try:
            dto = map_salla_order(raw)
        except OrderMappingError:
            skipped_invalid += 1
            continue

        items.append(dto)
        last_valid_row = row

        if len(items) >= safe_limit:
            break

    next_cursor = None
    if len(items) == safe_limit and last_valid_row is not None:
        order_date = str(last_valid_row.get("order_date") or "").strip()
        order_number = str(
            last_valid_row.get("order_number")
            or items[-1].order_number
        ).strip()

        if order_date and order_number:
            next_cursor = _encode_cursor(order_date, order_number)

    return OrderPage(
        items=items,
        next_cursor=next_cursor,
        skipped_invalid=skipped_invalid,
    )


async def get_order(
    db: Any,
    *,
    user_id: str,
    order_number: str,
) -> OrderDTO:
    """Return one exact Salla-backed order."""

    normalized_order_number = str(order_number or "").strip()
    if not normalized_order_number:
        raise OrderNotFoundError("order not found")

    row = await db.unified_orders.find_one(
        {
            "user_id": str(user_id),
            "order_number": normalized_order_number,
            "raw_by_source.salla_direct": {"$exists": True},
        },
        {
            "_id": 0,
            "raw_by_source.salla_direct": 1,
        },
    )

    raw = _salla_raw(row or {})
    if raw is None:
        raise OrderNotFoundError(
            f"order not found: {normalized_order_number}"
        )

    try:
        return map_salla_order(raw)
    except OrderMappingError as exc:
        raise OrderNotFoundError(
            f"order payload invalid: {normalized_order_number}"
        ) from exc
