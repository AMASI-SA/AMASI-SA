"""Unified advertising manager with native Mezan 2 account settings."""
from typing import Any, Callable

from integrations_control_center.routes import _require_owner

from .account_cost_settings import attach_account_cost_settings_routes
from .routes import make_ads_manager_router as _make_overview_router


def make_ads_manager_router(db: Any, current_user: Callable):
    """Compose Ads Manager, Campaign AI and the separate product watch.

    Campaign AI remains lazily imported because the Snapchat profitability
    engine imports account-cost helpers from this package. Heavy Campaign AI
    work still runs in its isolated subprocess every global five-hour cycle.

    Advertising Product Watch V3 is a separate read-only operational monitor.
    It launches an isolated child on a short cadence and only records objective
    product/page/inventory alerts; it never makes a marketing decision or writes
    to an ad platform/Salla.
    """
    from advertising_product_watch_routes_v3 import (
        attach_advertising_product_watch_routes,
    )
    from advertising_product_watch_scheduler_v3 import (
        attach_advertising_product_watch_scheduler,
    )
    from campaign_ai_monitor import attach_campaign_ai_routes
    from campaign_ai_unified_shadow_routes import (
        attach_campaign_ai_unified_shadow_routes,
    )
    from campaign_ai_monthly_profit_goal_v1 import (
        attach_monthly_profit_goal_routes,
    )
    from campaign_ai_public_guard import attach_campaign_ai_public_guard
    from campaign_ai_subprocess_scheduler import (
        attach_campaign_ai_subprocess_scheduler,
    )
    from decision_intelligence.routes import (
        attach_decision_intelligence_phase5_routes,
    )

    router = _make_overview_router(db, current_user)
    attach_account_cost_settings_routes(
        router,
        db,
        current_user,
        _require_owner,
    )
    attach_monthly_profit_goal_routes(
        router,
        db,
        current_user,
        _require_owner,
    )
    attach_campaign_ai_public_guard(router, db, current_user)
    attach_campaign_ai_routes(router, db, current_user, _require_owner)
    attach_campaign_ai_unified_shadow_routes(
        router,
        db,
        current_user,
        _require_owner,
    )
    attach_decision_intelligence_phase5_routes(
        router,
        db,
        current_user,
        _require_owner,
    )
    attach_advertising_product_watch_routes(router, db, current_user)
    attach_campaign_ai_subprocess_scheduler(router)
    attach_advertising_product_watch_scheduler(router)
    return router


__all__ = ["make_ads_manager_router"]
