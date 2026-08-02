from __future__ import annotations

from datetime import date

import pytest
from fastapi import APIRouter

from integrations_control_center.snapchat_campaign_report_routes import (
    aggregate_report_rows,
    attach_snapchat_campaign_report_routes,
    resolve_report_dates,
)
from integrations_control_center.snapchat_native_data_common import (
    SnapchatNativeSyncError,
)


def test_campaign_report_route_matches_frontend_contract() -> None:
    router = APIRouter(prefix="/integrations-v2")

    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    attach_snapchat_campaign_report_routes(
        router,
        object(),
        current_user,
        lambda user: user,
    )

    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }
    assert (
        "/integrations-v2/snapchat_ads/campaign-report",
        "GET",
    ) in routes


def test_campaign_report_aggregates_local_snapchat_rows() -> None:
    rows = [
        {
            "date": "2026-08-01",
            "spend_sar": 100.0,
            "purchase_value_sar": 300.0,
            "purchases": 3,
            "metrics": {
                "impressions": 10_000,
                "swipes": 200,
                "video_views": 8_000,
            },
            "updated_at": "2026-08-01T22:00:00+00:00",
        },
        {
            "date": "2026-08-02",
            "spend_sar": 50.0,
            "purchase_value_sar": 100.0,
            "purchases": 1,
            "metrics": {
                "impressions": 5_000,
                "swipes": 100,
                "video_views": 4_000,
            },
            "updated_at": "2026-08-02T22:00:00+00:00",
        },
    ]

    result = aggregate_report_rows(rows, requested_days=2)

    assert result["spend_sar"] == 150.0
    assert result["sales_sar"] == 400.0
    assert result["orders"] == 4
    assert result["impressions"] == 15_000
    assert result["swipes"] == 300
    assert result["roas"] == pytest.approx(400 / 150)
    assert result["cpa_sar"] == 37.5
    assert result["cpc_sar"] == 0.5
    assert result["cpm_sar"] == 10.0
    assert result["ctr_pct"] == 2.0
    assert result["observed_days"] == 2
    assert result["data_complete"] is True


def test_zero_conversions_remain_zero_not_unavailable() -> None:
    result = aggregate_report_rows(
        [
            {
                "date": "2026-08-02",
                "spend_sar": 25.0,
                "purchase_value_sar": 0.0,
                "purchases": 0,
                "metrics": {
                    "conversion_purchases": 0,
                    "impressions": 1_000,
                    "swipes": 0,
                },
                "updated_at": "2026-08-02T22:00:00+00:00",
            }
        ],
        requested_days=1,
    )

    assert result["orders"] == 0
    assert result["sales_sar"] == 0.0
    assert result["roas"] == 0.0
    assert result["cpa_sar"] is None
    assert result["ctr_pct"] == 0.0


def test_report_date_range_rejects_future_dates() -> None:
    with pytest.raises(SnapchatNativeSyncError) as error:
        resolve_report_dates(
            "2026-08-02",
            "2026-08-04",
            today=date(2026, 8, 3),
        )

    assert error.value.code == "future_date_not_allowed"
    assert error.value.status_code == 400
