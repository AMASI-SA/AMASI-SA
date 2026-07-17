"""Salla OAuth, Merchant API, webhook monitoring and synchronization."""

from .routes import attach_salla_routes as _attach_core_salla_routes, ensure_salla_indexes
from .webhook_monitor_routes import attach_salla_webhook_monitor_routes


def attach_salla_routes(api_router, db) -> None:
    """Attach the existing Salla routes plus read-only webhook diagnostics."""
    _attach_core_salla_routes(api_router, db)
    attach_salla_webhook_monitor_routes(api_router, db)


__all__ = ["attach_salla_routes", "ensure_salla_indexes"]
