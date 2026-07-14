"""Mezan OS Order Item Engine.

The Order Item Engine owns the permanent operational identity of each
individual item purchased inside an order.

It does not own supplier, inventory, preparation, receiving, marketing,
accounting or AI state. Those capabilities belong to separate engines.
"""

from .mapper import (
    OrderItemMappingError,
    map_order_item_identities,
    map_order_item_identity,
)

from .service import (
    InvalidOrderItemRequestError,
    OrderItemService,
    OrderItemServiceNotFoundError,
)

from .repository import (
    OrderEngineItemRepository,
    OrderItemNotFoundError,
    OrderItemPage,
    OrderItemRepository,
)

from .models import (
    OrderItemIdentityDTO,
    OrderItemOptionDTO,
    OrderItemSourceDTO,
)

__all__ = [
    "OrderItemIdentityDTO",
    "OrderItemOptionDTO",
    "OrderItemSourceDTO",
    "OrderItemMappingError",
    "map_order_item_identities",
    "map_order_item_identity",
    "OrderEngineItemRepository",
    "OrderItemNotFoundError",
    "OrderItemPage",
    "OrderItemRepository",
    "InvalidOrderItemRequestError",
    "OrderItemService",
    "OrderItemServiceNotFoundError",
]
