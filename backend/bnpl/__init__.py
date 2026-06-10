"""BNPL Integration package (Iter-116)."""
from .audit_routes import attach_bnpl_audit_routes
from .auto_sync_routes import attach_bnpl_auto_sync_routes
from .auto_sync_service import run_auto_sync_for_all_users
from .diagnostics_routes import attach_bnpl_diagnostics_routes
from .routes import attach_bnpl_routes, ensure_bnpl_indexes
from .webhook_routes import attach_bnpl_webhook_routes

__all__ = [
    "attach_bnpl_routes",
    "ensure_bnpl_indexes",
    "attach_bnpl_webhook_routes",
    "attach_bnpl_diagnostics_routes",
    "attach_bnpl_audit_routes",
    "attach_bnpl_auto_sync_routes",
    "run_auto_sync_for_all_users",
]
