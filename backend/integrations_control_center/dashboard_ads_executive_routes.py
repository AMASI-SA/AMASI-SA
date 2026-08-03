"""Read-only fallback route for the Mezan 2 advertising executive table.

The main Dashboard V2 response already carries this payload.  This focused
endpoint exists as a resilient transport fallback so the Mezan 2 profit card
never falls back to the legacy advertising-cost modal when an intermediate
adapter or cached response omits ``ads_v2.executive_breakdown``.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends


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
        user_id = str(owner["id"])

        # Lazy imports avoid a circular dependency while server.py composes
        # the integrations router and the Dashboard V2 router.
        from dashboard_v2_ads_executive import (
            build_salla_ads_executive_breakdown,
        )
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


__all__ = ["attach_dashboard_ads_executive_routes"]
