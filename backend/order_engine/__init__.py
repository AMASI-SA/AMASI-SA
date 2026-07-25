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


def _route_keys(route):
    """Return unique (path, method) keys for an APIRoute.

    FastAPI legitimately supports GET and POST on the same path.  Comparing
    paths alone silently dropped later methods, which caused GET warehouse
    endpoints to return 405 while POST remained registered.
    """
    path = getattr(route, "path", None)
    methods = getattr(route, "methods", None) or {None}
    return {(path, method) for method in methods}


def make_order_engine_router(*args, **kwargs):
    """Build Order Engine plus independent Mezan OS operations engines."""
    router = _original_make_order_engine_router(*args, **kwargs)
    db = args[0] if args else kwargs["db"]
    current_user = args[1] if len(args) > 1 else kwargs["current_user"]

    from warehouse_location_routes import make_warehouse_location_router
    from warehouse_location_v2_routes import make_warehouse_location_v2_router
    from warehouse_room_routes import make_warehouse_room_router

    child_routers = [
        make_warehouse_location_router(db, current_user),
        make_warehouse_location_v2_router(db, current_user),
        make_warehouse_room_router(db, current_user),
    ]
    existing_keys = set()
    for route in router.routes:
        existing_keys.update(_route_keys(route))

    for child_router in child_routers:
        for route in child_router.routes:
            route_keys = _route_keys(route)
            if route_keys.isdisjoint(existing_keys):
                router.routes.append(route)
                existing_keys.update(route_keys)
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
