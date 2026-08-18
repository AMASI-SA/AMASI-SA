"""Salla OAuth, Merchant API, webhook monitoring and synchronization."""

from .routes import attach_salla_routes as _attach_core_salla_routes, ensure_salla_indexes
from .webhook_monitor_routes import attach_salla_webhook_monitor_routes
from .cart_order_reconciliation import install_verified_order_cart_reconciliation


def attach_salla_routes(api_router, db) -> None:
    """Attach the existing Salla routes plus read-only webhook diagnostics."""
    # Install before routes.py imports ``dispatch_event`` inside the Easy Mode
    # endpoint so verified order webhooks can atomically retire their converted
    # abandoned-cart snapshot without changing the public webhook contract.
    install_verified_order_cart_reconciliation()
    _attach_core_salla_routes(api_router, db)
    attach_salla_webhook_monitor_routes(api_router, db)


__all__ = ["attach_salla_routes", "ensure_salla_indexes"]
