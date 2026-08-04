"""Read-only Dashboard advertising routes for Mezan 2.

The executive endpoint is a resilient transport fallback for the profit card.
Dashboard spend routes are attached lazily so focused integration modules stay
lightweight and unrelated workflows do not import provider reporting stacks.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends


async def build_dashboard_ads_executive_breakdown(
    db: Any,
    user_id: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    payment_methods: str | None = None,
    shipping_companies: str | None = None,
) -> dict[str, Any]:
    # Lazy imports avoid a circular dependency while server.py composes the
    # integrations router and the Dashboard V2 router.
    from dashboard_v2_ads_executive import build_salla_ads_executive_breakdown
    from dashboard_v2_routes import _filtered_orders, build_mezan_v2_ads

    orders = await _filtered_orders(
        db,
        user_id,
        from_date=from_date,
        to_date=to_date,
        payment_methods=payment_methods,
        shipping_companies=shipping_companies,
    )
    ads = await build_mezan_v2_ads(
        db,
        user_id,
        from_date=from_date,
        to_date=to_date,
    )
    breakdown = build_salla_ads_executive_breakdown(orders, ads)
    return {
        **breakdown,
        "transport": "dashboard_v2_inline_fallback",
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_dashboard_ads_executive_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get("/dashboard/ads-executive-breakdown")
    async def dashboard_ads_executive_breakdown(
        from_date: str | None = None,
        to_date: str | None = None,
        payment_methods: str | None = None,
        shipping_companies: str | None = None,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await build_dashboard_ads_executive_breakdown(
            db,
            str(owner["id"]),
            from_date=from_date,
            to_date=to_date,
            payment_methods=payment_methods,
            shipping_companies=shipping_companies,
        )

    # Keep the earlier Snapchat-only hourly endpoint for compatibility.
    from .dashboard_ads_hourly_spend_routes import (
        attach_dashboard_ads_hourly_spend_routes,
    )
    from .dashboard_ads_platform_spend_routes import (
        attach_dashboard_ads_platform_spend_routes,
    )

    attach_dashboard_ads_hourly_spend_routes(
        router,
        db,
        current_user,
        require_owner,
    )
    attach_dashboard_ads_platform_spend_routes(
        router,
        db,
        current_user,
        require_owner,
    )


__all__ = [
    "attach_dashboard_ads_executive_routes",
    "build_dashboard_ads_executive_breakdown",
]
