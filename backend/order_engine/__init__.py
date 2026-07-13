"""Mezan OS Order Engine.

This package owns the canonical order contract used by future Mezan
workspaces and engines.

It must not expose MongoDB-shaped documents to frontend consumers.
"""

from .mapper import OrderMappingError, map_salla_order
from .repository import (
    MongoOrderRepository,
    OrderDiscoveryRow,
    OrderRepository,
)
from .service import (
    InvalidOrderCursorError,
    OrderNotFoundError,
    OrderPage,
    get_order,
    list_orders,
)

from .models import (
    AddressDTO,
    CustomerDTO,
    MoneyTotalsDTO,
    OrderDTO,
    OrderItemDTO,
    OrderSourceDTO,
    PaymentDTO,
    ShippingDTO,
)

__all__ = [
    "AddressDTO",
    "CustomerDTO",
    "MoneyTotalsDTO",
    "OrderDTO",
    "OrderItemDTO",
    "OrderSourceDTO",
    "PaymentDTO",
    "ShippingDTO",
    "OrderMappingError",
    "map_salla_order",
    "InvalidOrderCursorError",
    "OrderNotFoundError",
    "OrderPage",
    "get_order",
    "list_orders",
    "MongoOrderRepository",
    "OrderDiscoveryRow",
    "OrderRepository",
]
