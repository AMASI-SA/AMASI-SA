"""Read-only Order Engine service."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Optional

from salla_marketing_attribution import canonical_order_source

from .mapper import OrderMappingError, map_salla_order
from .models import OrderDTO
from .repository import OrderRepository

DEFAULT_LIMIT = 15
MAX_LIMIT = 50
ALLOWED_STATUS_GROUPS = {
    "under_review",
    "reviewed",
    "processing",
    "completed",
    "shipping",
    "cancelled",
    "refunded",
}
_REVIEW_PARENT_VALUES = {
    "under review",
    "waiting review",
    "pending review",
    "بإنتظار المراجعة",
    "بانتظار المراجعة",
    "انتظار المراجعة",
}


class OrderNotFoundError(LookupError):
    pass


class InvalidOrderCursorError(ValueError):
    pass


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


def _normalise_status_exact(value: Optional[str]) -> Optional[str]:
    normalized = " ".join(str(value or "").replace("_", " ").strip().casefold().split())
    return normalized or None


def _status_key(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").strip().casefold().split())


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
    # A Mezan-local read marker always wins.  It never changes Salla status.
    if raw.get("mezan_read_at"):
        return False
    explicit_new = _bool_value(raw.get("is_new") if "is_new" in raw else raw.get("unread"))
    if explicit_new is not None:
        return explicit_new
    for key in ("is_read", "read", "is_seen", "seen"):
        if key in raw:
            read_value = _bool_value(raw.get(key))
            if read_value is not None:
                return not read_value
    return False


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _named_value(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key in ("name", "label", "title", "value", "slug", "type"):
            text = _text(value.get(key))
            if text:
                return text
        return None
    return _text(value)


def _url_value(value: Any) -> Optional[str]:
    if isinstance(value, str):
        text = value.strip()
        return text if text.startswith(("https://", "http://")) else None
    if isinstance(value, dict):
        for key in ("url", "original", "medium", "thumbnail", "image"):
            candidate = _url_value(value.get(key))
            if candidate:
                return candidate
    return None


def _customer_avatar(raw: dict[str, Any]) -> Optional[str]:
    customer = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
    for candidate in (
        customer.get("avatar_url"), customer.get("avatar"), customer.get("profile_image"),
        customer.get("image"), customer.get("photo"), raw.get("customer_avatar"),
    ):
        url = _url_value(candidate)
        if url:
            return url
    return None


def _customer_gender(raw: dict[str, Any]) -> Optional[str]:
    customer = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
    value = _text(customer.get("gender") or raw.get("customer_gender"))
    if not value:
        return None
    normalized = value.lower()
    if normalized in {"male", "m", "man", "ذكر"}:
        return "male"
    if normalized in {"female", "f", "woman", "أنثى", "انثى"}:
        return "female"
    return None


def _provider_status_native(
    raw: dict[str, Any],
    *,
    current_status: Optional[str] = None,
) -> Optional[str]:
    status = raw.get("status")
    customized = None
    parent = None
    if isinstance(status, dict):
        customized = _named_value(status.get("customized"))
        parent = _named_value(status.get("name")) or _named_value(status.get("slug"))
    else:
        parent = _named_value(status)

    current = _text(current_status)
    if current and _status_key(current) not in _REVIEW_PARENT_VALUES:
        return current
    return customized or current or parent


def _provider_is_gift(raw: dict[str, Any], tags: list[str]) -> bool:
    for key in ("is_gift", "gift", "gift_order"):
        if key not in raw:
            continue
        value = raw.get(key)
        if isinstance(value, dict):
            enabled = _bool_value(value.get("enabled") or value.get("is_gift"))
            if enabled is not None:
                return enabled
            explicit_type = _named_value(value)
            if explicit_type and _is_gift_label(explicit_type):
                return True
        else:
            enabled = _bool_value(value)
            if enabled is not None:
                return enabled
            if _is_gift_label(value):
                return True

    # Salla's order details UI exposes this fact as "نوع الطلب: طلب كهدية".
    # Accept only explicit provider order-type fields; never infer from products,
    # notes, customer names or other weak signals.
    for key in ("order_type", "type"):
        value = _named_value(raw.get(key))
        if value and _is_gift_label(value):
            return True

    normalized_tags = {str(tag).strip().lower() for tag in tags if str(tag).strip()}
    return bool(normalized_tags.intersection({"gift", "gift order", "هدية", "طلب كهدية", "إهداء", "اهداء"}))


def _is_gift_label(value: Any) -> bool:
    normalized = " ".join(str(value or "").replace("_", " ").strip().casefold().split())
    return normalized in {
        "gift",
        "gift order",
        "is gift",
        "هدية",
        "طلب هدية",
        "طلب كهدية",
        "إهداء",
        "اهداء",
    }


def _provider_marketing_source(raw: dict[str, Any]) -> dict[str, Optional[str]]:
    return canonical_order_source(raw)


def _map_row(raw: dict[str, Any], *, current_status: Optional[str] = None) -> OrderDTO:
    dto = map_salla_order(raw)
    customer = dto.customer.model_copy(update={"avatar_url": _customer_avatar(raw), "gender": _customer_gender(raw)})
    source = dto.source.model_copy(update=_provider_marketing_source(raw))
    return dto.model_copy(
        update={
            "status_native": _provider_status_native(raw, current_status=current_status) or dto.status_native,
            "is_new": _provider_is_new(raw),
            "is_gift": _provider_is_gift(raw, dto.tags),
            "source": source,
            "customer": customer,
        }
    )


async def list_orders(
    repository: OrderRepository,
    *,
    user_id: str,
    limit: int = DEFAULT_LIMIT,
    cursor: Optional[str] = None,
    status_group: Optional[str] = None,
    status_exact: Optional[str] = None,
) -> OrderPage:
    safe_limit = _normalise_limit(limit)
    normalized_status_group = _normalise_status_group(status_group)
    normalized_status_exact = _normalise_status_exact(status_exact)
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
        status_exact=normalized_status_exact,
    )

    items: list[OrderDTO] = []
    skipped_invalid = 0
    last_valid_order_date: Optional[str] = None
    last_valid_order_number: Optional[str] = None
    for row in rows:
        try:
            dto = _map_row(row.salla_raw, current_status=row.current_status)
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
    return OrderPage(items=items, next_cursor=next_cursor, skipped_invalid=skipped_invalid)


async def get_order(repository: OrderRepository, *, user_id: str, order_number: str) -> OrderDTO:
    normalized_order_number = str(order_number or "").strip()
    if not normalized_order_number:
        raise OrderNotFoundError("order not found")
    row = await repository.get_salla_order(user_id=str(user_id), order_number=normalized_order_number)
    if row is None:
        raise OrderNotFoundError(f"order not found: {normalized_order_number}")
    try:
        return _map_row(row.salla_raw, current_status=row.current_status)
    except OrderMappingError as exc:
        raise OrderNotFoundError(f"order payload invalid: {normalized_order_number}") from exc


async def get_orders(
    repository: OrderRepository,
    *,
    user_id: str,
    order_numbers: list[str],
) -> dict[str, OrderDTO]:
    """Return exact orders by number without an N+1 database read path."""
    normalized = list(dict.fromkeys(
        str(value or "").strip()
        for value in order_numbers
        if str(value or "").strip()
    ))
    if not normalized:
        return {}
    rows = await repository.get_salla_orders(
        user_id=str(user_id),
        order_numbers=normalized,
    )
    result: dict[str, OrderDTO] = {}
    for row in rows:
        try:
            result[row.order_number] = _map_row(
                row.salla_raw,
                current_status=row.current_status,
            )
        except OrderMappingError:
            continue
    return result
