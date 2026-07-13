"""Read-only Order Engine service.

Service responsibilities
------------------------
- Decode pagination cursors.
- Request discovery rows through OrderRepository.
- Convert Salla raw payloads into canonical OrderDTO objects.
- Skip invalid historical rows safely.

The service does not know:

- MongoDB collection names
- `unified_orders`
- Mongo query syntax
- FastAPI
- Salla HTTP
- Qoyod
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Optional

from .mapper import OrderMappingError, map_salla_order
from .models import OrderDTO
from .repository import OrderRepository


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


async def list_orders(
    repository: OrderRepository,
    *,
    user_id: str,
    limit: int = DEFAULT_LIMIT,
    cursor: Optional[str] = None,
) -> OrderPage:
    """Return newest Salla-backed orders using keyset pagination."""

    safe_limit = _normalise_limit(limit)

    before_order_date = None
    before_order_number = None

    if cursor:
        decoded = _decode_cursor(cursor)
        before_order_date = decoded["order_date"]
        before_order_number = decoded["order_number"]

    fetch_limit = min(
        MAX_LIMIT * 3,
        max(safe_limit * 3, safe_limit + 1),
    )

    rows = await repository.list_salla_orders(
        user_id=str(user_id),
        limit=fetch_limit,
        before_order_date=before_order_date,
        before_order_number=before_order_number,
    )

    items: list[OrderDTO] = []
    skipped_invalid = 0
    last_valid_order_date: Optional[str] = None
    last_valid_order_number: Optional[str] = None

    for row in rows:
        try:
            dto = map_salla_order(row.salla_raw)
        except OrderMappingError:
            skipped_invalid += 1
            continue

        items.append(dto)
        last_valid_order_date = row.order_date
        last_valid_order_number = row.order_number

        if len(items) >= safe_limit:
            break

    next_cursor = None

    if (
        len(items) == safe_limit
        and last_valid_order_date
        and last_valid_order_number
    ):
        next_cursor = _encode_cursor(
            last_valid_order_date,
            last_valid_order_number,
        )

    return OrderPage(
        items=items,
        next_cursor=next_cursor,
        skipped_invalid=skipped_invalid,
    )


async def get_order(
    repository: OrderRepository,
    *,
    user_id: str,
    order_number: str,
) -> OrderDTO:
    """Return one exact Salla-backed order."""

    normalized_order_number = str(order_number or "").strip()

    if not normalized_order_number:
        raise OrderNotFoundError("order not found")

    row = await repository.get_salla_order(
        user_id=str(user_id),
        order_number=normalized_order_number,
    )

    if row is None:
        raise OrderNotFoundError(
            f"order not found: {normalized_order_number}"
        )

    try:
        return map_salla_order(row.salla_raw)
    except OrderMappingError as exc:
        raise OrderNotFoundError(
            f"order payload invalid: {normalized_order_number}"
        ) from exc
