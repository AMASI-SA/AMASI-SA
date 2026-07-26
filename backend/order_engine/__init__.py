"""Mezan OS Order Engine.

This package owns the canonical order contract used by future Mezan
workspaces and engines.

It must not expose MongoDB-shaped documents to frontend consumers.
"""

# Payment-method freshness policy
# -------------------------------
# Orders commonly start as ``pending_payment`` and later move to a real method
# such as Tamara, Tabby, card, or bank transfer. Qoyod automatic send performs
# an authoritative Salla resync immediately before posting, so the unified-order
# merge must let the newest payment method replace the value captured when the
# order was first created. Keep this policy next to Order Engine bootstrap so
# every consumer (Orders V2, manual sender, and Plan-B automatic sender) sees the
# same latest payment fact.
import orders_db as _orders_db

_orders_db.CRITICAL_FIELDS.add("payment_method")

# Plan-B automatic sends must copy the Salla-resynced payment/status facts from
# unified_orders into the exact legacy inbox row consumed by manual_send_one.
# This closes the stale creation-snapshot gap for orders that began as
# pending-payment and were later paid through Tamara/Tabby/card.
from integrations.qoyod_manual.payment_freshness_hotfix import (
    install_auto_send_payment_freshness_patch,
)

install_auto_send_payment_freshness_patch()

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

    FastAPI legitimately supports GET and POST on the same path. Comparing
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
    from warehouse_reset_routes import make_warehouse_reset_router

    # Product V2 sync hotfix: Salla returns only its default 15 products when
    # ``format=light`` is sent. Patch the module global before building the
    # router so POST /products-v2/sync uses the proven full-catalog request.
    import product_v2_routes as _product_v2_routes
    from product_v2_sync_hotfix import run_product_v2_sync_fixed

    _product_v2_routes.run_product_v2_sync = run_product_v2_sync_fixed
    make_product_v2_router = _product_v2_routes.make_product_v2_router

    child_routers = [
        make_warehouse_location_router(db, current_user),
        make_warehouse_location_v2_router(db, current_user),
        make_warehouse_room_router(db, current_user),
        make_warehouse_reset_router(db, current_user),
        make_product_v2_router(db, current_user),
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
