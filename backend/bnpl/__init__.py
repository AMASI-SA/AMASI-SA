"""BNPL Integration package (Iter-116)."""
from .routes import attach_bnpl_routes, ensure_bnpl_indexes
from .webhook_routes import attach_bnpl_webhook_routes

__all__ = ["attach_bnpl_routes", "ensure_bnpl_indexes",
           "attach_bnpl_webhook_routes"]
