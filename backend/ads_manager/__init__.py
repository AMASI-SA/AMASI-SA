"""Unified advertising manager with native Mezan 2 account settings."""
from typing import Any, Callable

from integrations_control_center.routes import _require_owner

from .account_cost_settings import attach_account_cost_settings_routes
from .routes import make_ads_manager_router as _make_overview_router


def make_ads_manager_router(db: Any, current_user: Callable):
    """Compose the frozen read-only overview with Mezan 2 account settings."""
    router = _make_overview_router(db, current_user)
    attach_account_cost_settings_routes(
        router,
        db,
        current_user,
        _require_owner,
    )
    return router


__all__ = ["make_ads_manager_router"]
