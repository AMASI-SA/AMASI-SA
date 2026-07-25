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
from . import routes as _routes
from .routes import OrderListResponse
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


_original_make_order_engine_router = _routes.make_order_engine_router


def make_order_engine_router(*args, **kwargs):
    """Build the Order Engine router and register independent operations engines.

    The main server imports ``make_order_engine_router`` directly from
    ``order_engine.routes``.  Replacing the symbol on that module here keeps
    registration isolated from the very large ``server.py`` while preserving
    the public API paths. Warehouse routes retain their own
    ``/warehouse-locations`` prefix and are exposed under the server's
    existing ``/api`` router.
    """
    router = _original_make_order_engine_router(*args, **kwargs)
    db = args[0] if args else kwargs["db"]
    current_user = args[1] if len(args) > 1 else kwargs["current_user"]

    from warehouse_location_routes import make_warehouse_location_router

    warehouse_router = make_warehouse_location_router(db, current_user)
    existing_paths = {getattr(route, "path", None) for route in router.routes}
    for route in warehouse_router.routes:
        if getattr(route, "path", None) not in existing_paths:
            router.routes.append(route)
    return router


# ``from order_engine.routes import make_order_engine_router`` happens after
# this package initializer, so expose the bridge on the routes module too.
_routes.make_order_engine_router = make_order_engine_router


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
    "OrderListResponse",
    "make_order_engine_router",
]
