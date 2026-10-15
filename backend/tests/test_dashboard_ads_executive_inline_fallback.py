from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from fastapi import APIRouter

from integrations_control_center.dashboard_ads_executive_routes import (
    attach_dashboard_ads_executive_routes,
    build_dashboard_ads_executive_breakdown,
)


@pytest.mark.asyncio
async def test_fallback_builder_preserves_filters_and_is_read_only(monkeypatch):
    calls = {}

    async def fake_filtered_orders(
        db,
        user_id,
        *,
        from_date,
        to_date,
        payment_methods,
        shipping_companies,
        include_marketing_attribution=False,
    ):
        calls["orders"] = {
            "db": db,
            "user_id": user_id,
            "from_date": from_date,
            "to_date": to_date,
            "payment_methods": payment_methods,
            "shipping_companies": shipping_companies,
            "include_marketing_attribution": include_marketing_attribution,
        }
        return [{"order_number": "A-1", "utm_source": "meta", "total_amount": 250}]

    async def fake_ads(db, user_id, *, from_date, to_date):
        calls["ads"] = {
            "db": db,
            "user_id": user_id,
            "from_date": from_date,
            "to_date": to_date,
        }
        return {"providers": {}, "breakdown": {}, "total": 100}

    def fake_breakdown(orders, ads):
        calls["breakdown"] = {"orders": orders, "ads": ads}
        return {
            "providers": {"meta": {"spend_sar": 100, "salla_orders": 1}},
            "total": {"spend_sar": 100, "salla_orders": 1},
            "coverage": {},
        }

    monkeypatch.setitem(
        sys.modules,
        "dashboard_v2_routes",
        SimpleNamespace(
            _filtered_orders=fake_filtered_orders,
            build_mezan_v2_ads=fake_ads,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "dashboard_v2_ads_executive",
        SimpleNamespace(build_salla_ads_executive_breakdown=fake_breakdown),
    )

    db = object()
    result = await build_dashboard_ads_executive_breakdown(
        db,
        "owner-1",
        from_date="2026-08-01",
        to_date="2026-08-03",
        payment_methods="tabby,tamara",
        shipping_companies="smsa",
    )

    assert calls["orders"] == {
        "db": db,
        "user_id": "owner-1",
        "from_date": "2026-08-01",
        "to_date": "2026-08-03",
        "payment_methods": "tabby,tamara",
        "shipping_companies": "smsa",
        "include_marketing_attribution": True,
    }
    assert calls["ads"]["from_date"] == "2026-08-01"
    assert calls["ads"]["to_date"] == "2026-08-03"
    assert result["providers"]["meta"]["spend_sar"] == 100
    assert result["transport"] == "dashboard_v2_inline_fallback"
    assert result["source_only"] is True
    assert result["provider_write_reached"] is False
    assert result["campaign_write_reached"] is False
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False


def test_fallback_route_is_registered_under_integrations_v2_router():
    router = APIRouter(prefix="/integrations-v2")

    async def current_user():
        return {"id": "owner-1"}

    attach_dashboard_ads_executive_routes(
        router,
        object(),
        current_user,
        lambda user: user,
    )

    paths = {route.path for route in router.routes}
    assert "/integrations-v2/dashboard/ads-executive-breakdown" in paths
