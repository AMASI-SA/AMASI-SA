"""Mezan OS Order Engine.

This package owns the canonical order contract used by future Mezan
workspaces and engines.
It must not expose MongoDB-shaped documents to frontend consumers.
"""

# Payment-method freshness policy
import orders_db as _orders_db

_orders_db.CRITICAL_FIELDS.add("payment_method")

from .mapper import OrderMappingError, map_salla_order
from .repository import MongoOrderRepository, OrderDiscoveryRow, OrderRepository
from . import routes as _routes
from .routes import OrderListResponse
from .service import InvalidOrderCursorError, OrderNotFoundError, OrderPage, get_order, list_orders
from .models import (
    AddressDTO, CustomerDTO, MoneyTotalsDTO, OrderDTO, OrderItemDTO,
    OrderSourceDTO, PaymentDTO, ShippingDTO,
)

_original_make_order_engine_router = _routes.make_order_engine_router


def _route_keys(route):
    """Return unique (path, method) keys for an APIRoute."""
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
    from warehouse_reset_routes import make_warehouse_reset_router
    from product_v2_workspace_routes import make_product_v2_workspace_router
    from product_v2_creation_order_routes import make_product_v2_creation_order_router
    from product_v2_details_routes import make_product_v2_details_router
    from product_v2_source_authority import install_product_source_authority
    from product_field_cost_support import install_product_field_cost_support
    from product_v2_recent_sync_routes import make_product_v2_recent_sync_router
    from component_edit_routes import make_component_edit_router
    from product_option_cost_routes import make_product_option_cost_router
    from order_option_cost_snapshot_routes import make_order_option_cost_snapshot_router
    from product_control_center_routes import make_product_control_center_router

    import product_v2_routes as _product_v2_routes
    from product_v2_sync_hotfix import run_product_v2_sync_fixed

    _product_v2_routes.run_product_v2_sync = run_product_v2_sync_fixed
    install_product_source_authority()
    install_product_field_cost_support()
    make_product_v2_router = _product_v2_routes.make_product_v2_router

    child_routers = [
        make_warehouse_location_router(db, current_user),
        make_warehouse_location_v2_router(db, current_user),
        make_warehouse_room_router(db, current_user),
        make_warehouse_reset_router(db, current_user),
        make_product_v2_router(db, current_user),
        make_product_v2_recent_sync_router(db, current_user),
        make_product_v2_creation_order_router(db, current_user),
        make_product_v2_workspace_router(db, current_user),
        make_product_v2_details_router(db, current_user),
        make_product_control_center_router(db, current_user),
        make_component_edit_router(db, current_user),
        make_product_option_cost_router(db, current_user),
        make_order_option_cost_snapshot_router(db, current_user),
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


_routes.make_order_engine_router = make_order_engine_router

__all__ = [
    "AddressDTO", "CustomerDTO", "MoneyTotalsDTO", "OrderDTO", "OrderItemDTO",
    "OrderSourceDTO", "PaymentDTO", "ShippingDTO", "OrderMappingError",
    "map_salla_order", "InvalidOrderCursorError", "OrderNotFoundError",
    "OrderPage", "get_order", "list_orders", "MongoOrderRepository",
    "OrderDiscoveryRow", "OrderRepository", "OrderListResponse",
    "make_order_engine_router",
]
