"""Read-only Order Engine service."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Optional

from .mapper import OrderMappingError, map_salla_order
from .models import OrderDTO
from .repository import OrderRepository

DEFAULT_LIMIT = 15
MAX_LIMIT = 50
ALLOWED_STATUS_GROUPS = {
    "under_review",
    "processing",
    "completed",
    "shipping",
    "cancelled",
    "refunded",
}


class OrderNotFoundError(LookupError):
    """Raised when an exact Salla-backed order does not exist."""


class InvalidOrderCursorError(ValueError):
    """Raised when a list cursor is malformed."""


@dataclass(frozen=True)
class OrderPage:
    items: list[OrderDTO]
    next_cursor: Optional[str]
    skipped_invalid: int = 0


def _normalise_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _normalise_status_group(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in ALLOWED_STATUS_GROUPS else None


def _encode_cursor(order_date: str, order_number: str) -> str:
    payload = json.dumps(
        {"order_date": str(order_date), "order_number": str(order_number)},
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
    return {"order_date": order_date, "order_number": order_number}


def _bool_value(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "read", "seen"}:
        return True
    if text in {"false", "0", "no", "n", "unread", "new"}:
        return False
    return None


def _provider_is_new(raw: dict[str, Any]) -> bool:
    explicit_new = _bool_value(
        raw.get("is_new") if "is_new" in raw else raw.get("unread")
    )
    if explicit_new is not None:
        return explicit_new
    for key in ("is_read", "read", "is_seen", "seen"):
        if key not in raw:
            continue
        read_value = _bool_value(raw.get(key))
        if read_value is not None:
            return not read_value
    return False


def _map_row(raw: dict[str, Any]) -> OrderDTO:
    dto = map_salla_order(raw)
    return dto.model_copy(update={"is_new": _provider_is_new(raw)})


async def list_orders(
    repository: OrderRepository,
    *,
    user_id: str,
    limit: int = DEFAULT_LIMIT,
    cursor: Optional[str] = None,
    status_group: Optional[str] = None,
) -> OrderPage:
    safe_limit = _normalise_limit(limit)
    normalized_status_group = _normalise_status_group(status_group)

    before_order_date = None
    before_order_number = None
    if cursor:
        decoded = _decode_cursor(cursor)
        before_order_date = decoded["order_date"]
        before_order_number = decoded["order_number"]

    fetch_limit = min(MAX_LIMIT * 3, max(safe_limit * 3, safe_limit + 1))
    rows = await repository.list_salla_orders(
        user_id=str(user_id),
        limit=fetch_limit,
        before_order_date=before_order_date,
        before_order_number=before_order_number,
        status_group=normalized_status_group,
    )

    items: list[OrderDTO] = []
    skipped_invalid = 0
    last_valid_order_date: Optional[str] = None
    last_valid_order_number: Optional[str] = None

    for row in rows:
        try:
            dto = _map_row(row.salla_raw)
        except OrderMappingError:
            skipped_invalid += 1
            continue
        items.append(dto)
        last_valid_order_date = row.order_date
        last_valid_order_number = row.order_number
        if len(items) >= safe_limit:
            break

    next_cursor = None
    if len(items) == safe_limit and last_valid_order_date and last_valid_order_number:
        next_cursor = _encode_cursor(last_valid_order_date, last_valid_order_number)

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
    normalized_order_number = str(order_number or "").strip()
    if not normalized_order_number:
        raise OrderNotFoundError("order not found")

    row = await repository.get_salla_order(
        user_id=str(user_id),
        order_number=normalized_order_number,
    )
    if row is None:
        raise OrderNotFoundError(f"order not found: {normalized_order_number}")

    try:
        return _map_row(row.salla_raw)
    except OrderMappingError as exc:
        raise OrderNotFoundError(
            f"order payload invalid: {normalized_order_number}"
        ) from exc
