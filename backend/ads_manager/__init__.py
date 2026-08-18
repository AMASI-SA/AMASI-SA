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

    The public read guard is registered before the legacy compatibility routes.
    That ordering is deliberate: even if an old/overlapping worker writes a
    historical ``mezan_fallback`` snapshot, the dashboard can never surface it
    as an AI recommendation.

    The periodic timer is also attached here, but heavy provider/OpenAI work is
    launched in a short-lived child process.  This preserves the Production
    stability rule that long-running analysis never executes in FastAPI's web
    process while still giving Campaign AI an automatic first run after deploy
    and a new run every five hours.
    """
    from campaign_ai_monitor import attach_campaign_ai_routes
    from campaign_ai_public_guard import attach_campaign_ai_public_guard
    from campaign_ai_subprocess_scheduler import (
        attach_campaign_ai_subprocess_scheduler,
    )

    router = _make_overview_router(db, current_user)
    attach_account_cost_settings_routes(
        router,
        db,
        current_user,
        _require_owner,
    )
    attach_campaign_ai_public_guard(router, db, current_user)
    attach_campaign_ai_routes(router, db, current_user, _require_owner)
    attach_campaign_ai_subprocess_scheduler(router)
    return router


__all__ = ["make_ads_manager_router"]
