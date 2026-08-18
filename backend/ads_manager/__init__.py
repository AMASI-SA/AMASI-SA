"""Unified advertising manager with native Mezan 2 account settings."""
from typing import Any, Callable

from integrations_control_center.routes import _require_owner

from .account_cost_settings import attach_account_cost_settings_routes
from .routes import make_ads_manager_router as _make_overview_router


def make_ads_manager_router(db: Any, current_user: Callable):
    """Compose the frozen read-only overview with Mezan 2 account settings.

    Campaign AI is imported lazily here because the Snapchat profitability
    engine imports account-cost helpers from this package. Importing Campaign AI
    at package-import time creates a cycle:

    campaign_ai_monitor -> snapchat_campaign_profitability ->
    dashboard_v2_ad_costs -> ads_manager -> campaign_ai_monitor.

    Route composition happens after module import, so the lazy import keeps the
    same public router contract without coupling the reporting modules to the AI
    bootstrap order.
    """
    from campaign_ai_monitor import attach_campaign_ai_routes

    router = _make_overview_router(db, current_user)
    attach_account_cost_settings_routes(
        router,
        db,
        current_user,
        _require_owner,
    )
    attach_campaign_ai_routes(router, db, current_user, _require_owner)
    return router


__all__ = ["make_ads_manager_router"]
