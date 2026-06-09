"""BNPL Integration package (Iter-116).

Provides direct API integrations with Tamara and Tabby as independent
data sources for orders, refunds and settlements.  Built on top of the
existing Fernet encryption helper used for Salla so secrets never land
in Mongo in plaintext.

Modules:
    crypto        — Fernet wrappers re-used from Salla integration.
    config_store  — read/write encrypted credentials + fee settings.
    clients/tabby — async httpx client for Tabby Merchant API.
    clients/tamara— async httpx client for Tamara Merchant API.
    routes        — FastAPI routes: settings, test-connection, sync.
    sync_service  — Tabby payments → unified_orders + payment_transactions.
"""
from .routes import attach_bnpl_routes, ensure_bnpl_indexes

__all__ = ["attach_bnpl_routes", "ensure_bnpl_indexes"]
