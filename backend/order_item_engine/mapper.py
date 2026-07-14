"""Pure OrderDTO → OrderItemIdentityDTO mapper.

Responsibilities
----------------
- Convert canonical Order Engine items into permanent operational identities.
- Preserve customer-selected options and personalization.
- Preserve the commercial snapshot supplied by the Order Engine.
- Assign the item's stable line index inside the order.

This module performs no:
- Database access
- HTTP calls
- FastAPI operations
- Supplier assignment
- Inventory mutation
- Preparation workflow
- Cost calculation
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Optional

from order_engine.models import OrderDTO, OrderItemDTO

from .models import (
    OrderItemIdentityDTO,
    OrderItemOptionDTO,
    OrderItemSourceDTO,
)


class OrderItemMappingError(ValueError):
    """Raised when an OrderDTO cannot produce valid item identities."""


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _option_name(option: dict[str, Any]) -> Optional[str]:
    return _text(
        _first(
            option.get("name"),
            option.get("label"),
            option.get("key"),
            option.get("option"),
        )
    )


def _option_value(option: dict[str, Any]) -> Any:
    return _first(
        option.get("value"),
        option.get("selected"),
        option.get("choice"),
        option.get("text"),
    )


def _map_options(
    item: OrderItemDTO,
) -> list[OrderItemOptionDTO]:
    """Preserve provider options without inventing missing values."""

    options: list[OrderItemOptionDTO] = []
    seen: set[tuple[str, str]] = set()

    for raw_option in item.options_raw:
        if not isinstance(raw_option, dict):
            continue

        name = _option_name(raw_option)
        value = _option_value(raw_option)

        if not name or value is None:
            continue

        key = (name.casefold(), repr(value))

        if key in seen:
            continue

        seen.add(key)
        options.append(
            OrderItemOptionDTO(
                name=name,
                value=deepcopy(value),
            )
        )

    # Some historical payloads may contain only normalized options.
    # Use them only when the equivalent raw option was not already preserved.
    for raw_name, raw_value in item.options_normalized.items():
        name = _text(raw_name)

        if not name or raw_value is None:
            continue

        key = (name.casefold(), repr(raw_value))

        if key in seen:
            continue

        seen.add(key)
        options.append(
            OrderItemOptionDTO(
                name=name,
                value=deepcopy(raw_value),
            )
        )

    return options


def map_order_item_identity(
    *,
    order: OrderDTO,
    item: OrderItemDTO,
    line_index: int,
) -> OrderItemIdentityDTO:
    """Convert one canonical order item into its immutable identity."""

    if line_index < 0:
        raise OrderItemMappingError("line_index cannot be negative")

    order_item_id = _text(item.order_item_id)

    if not order_item_id:
        raise OrderItemMappingError(
            f"order {order.order_number} contains an item without identity"
        )

    return OrderItemIdentityDTO(
        order_item_id=order_item_id,
        order_id=order.order_id,
        order_number=order.order_number,
        order_created_at=order.created_at,
        line_index=line_index,
        source=OrderItemSourceDTO(
            provider=order.source.provider,
            source_order_id=_text(order.source.source_order_id),
            source_order_item_id=_text(item.source_item_id),
            source_product_id=_text(item.product_id),
            source_variant_id=_text(item.variant_id),
        ),
        product_id=_text(item.product_id),
        parent_product_id=_text(item.parent_product_id),
        variant_id=_text(item.variant_id),
        sku=_text(item.sku),
        barcode=_text(item.barcode),
        name=item.name,
        quantity=item.quantity,
        image_url=_text(item.image_url),
        image_urls=deepcopy(item.image_urls),
        product_url=_text(item.product_url),
        color=_text(item.color),
        size=_text(item.size),
        material=_text(item.material),
        options=_map_options(item),
        custom_fields=deepcopy(item.custom_fields),
        currency=order.totals.currency,
        unit_price=item.unit_price,
        discount=item.discount,
        tax_reported_by_source=item.tax_reported_by_source,
        total=item.total,
    )


def map_order_item_identities(
    order: OrderDTO,
) -> list[OrderItemIdentityDTO]:
    """Convert all items in one OrderDTO into independent identities."""

    if not isinstance(order, OrderDTO):
        raise OrderItemMappingError("order must be an OrderDTO")

    identities: list[OrderItemIdentityDTO] = []
    seen_ids: set[str] = set()

    for line_index, item in enumerate(order.items):
        order_item_id = _text(item.order_item_id)

        if not order_item_id:
            raise OrderItemMappingError(
                f"order {order.order_number} contains an item without identity"
            )

        if order_item_id in seen_ids:
            raise OrderItemMappingError(
                f"duplicate order_item_id in order "
                f"{order.order_number}: {order_item_id}"
            )

        seen_ids.add(order_item_id)

        identities.append(
            map_order_item_identity(
                order=order,
                item=item,
                line_index=line_index,
            )
        )

    return identities
