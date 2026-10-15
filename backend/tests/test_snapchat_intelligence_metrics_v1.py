from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from integrations_control_center import snapchat_ad_performance
from integrations_control_center import snapchat_account_timezone_manager
from integrations_control_center import snapchat_adsquad_performance
from integrations_control_center import snapchat_native_performance_sync as module
from integrations_control_center import snapchat_platform_source_integrity


def test_funnel_fields_are_requested_for_every_stats_window():
    assert {
        "conversion_view_content",
        "conversion_add_cart",
        "conversion_start_checkout",
        "conversion_add_billing",
        "conversion_purchases",
    } <= set(module.STAT_FIELDS)


def test_audience_fields_are_total_only_and_never_summed():
    assert "frequency" not in module.STAT_FIELDS
    assert "uniques" not in module.STAT_FIELDS
    assert {"frequency", "uniques"} <= set(module.TOTAL_STAT_FIELDS)

    exact = module._new_bucket(module.TOTAL_STAT_FIELDS)
    module._add_to_bucket(
        exact,
        {"spend": 1_000_000, "frequency": 1.7, "uniques": 100},
    )
    exact_metrics = module._finalize_bucket(exact)
    assert exact_metrics["frequency"] == 1.7
    assert exact_metrics["uniques"] == 100

    ambiguous = module._new_bucket(module.TOTAL_STAT_FIELDS)
    module._add_to_bucket(ambiguous, {"frequency": 1.5, "uniques": 100})
    module._add_to_bucket(ambiguous, {"frequency": 1.7, "uniques": 120})
    ambiguous_metrics = module._finalize_bucket(ambiguous)
    assert ambiguous_metrics["frequency"] is None
    assert ambiguous_metrics["uniques"] is None


def test_metric_provenance_states_frequency_is_exact_provider_window():
    metrics = {
        "conversion_view_content": 25,
        "conversion_add_cart": 8,
        "conversion_start_checkout": 4,
        "conversion_purchases": 2,
        "frequency": 1.4,
    }

    assert module._funnel_metrics(metrics) == {
        "conversion_view_content": 25,
        "conversion_add_cart": 8,
        "conversion_start_checkout": 4,
        "conversion_add_billing": None,
        "conversion_purchases": 2,
    }
    provenance = module._metric_provenance(
        metrics,
        provider_granularity="TOTAL",
        provider_breakdown="ad",
    )
    assert provenance["frequency_aggregation"] == "exact_provider_window"
    assert provenance["frequency_summed"] is False
    assert provenance["provider_granularity"] == "TOTAL"


def test_report_exposes_funnel_and_exact_one_day_audience_metrics():
    rows = [{
        "date": "2026-08-09",
        "metrics": {
            "conversion_view_content": 40,
            "conversion_add_cart": 12,
            "conversion_start_checkout": 6,
            "conversion_add_billing": 4,
            "uniques": 100,
            "frequency": 3.0,
        },
    }]

    exact = snapchat_account_timezone_manager._aggregate_rows(
        rows,
        requested_days=1,
    )
    assert exact["view_content"] == 40
    assert exact["add_to_cart"] == 12
    assert exact["start_checkout"] == 6
    assert exact["add_billing"] == 4
    assert exact["paid_reach"] == 100
    assert exact["paid_frequency"] == 3
    assert exact["reach_frequency_scope"] == "exact_one_day_total"

    overlapping_entities = snapchat_account_timezone_manager._aggregate_rows(
        [rows[0], rows[0]],
        requested_days=1,
    )
    assert overlapping_entities["paid_reach"] is None
    assert overlapping_entities["paid_frequency"] is None
    assert overlapping_entities["reach_frequency_scope"] == "exact_total_window_required"

    multi_day = snapchat_account_timezone_manager._aggregate_rows(
        rows,
        requested_days=7,
    )
    assert multi_day["view_content"] == 40
    assert multi_day["paid_reach"] is None
    assert multi_day["paid_frequency"] is None
    assert multi_day["reach_frequency_scope"] == "exact_total_window_required"


@pytest.mark.asyncio
async def test_every_total_entity_request_asks_for_exact_audience_metrics():
    class CaptureContext:
        def __init__(self):
            self.calls = []

        async def get_json(self, client, url, *, headers, params=None):
            self.calls.append(deepcopy(params or {}))
            breakdown = str((params or {}).get("breakdown") or "")
            return {
                "request_status": "SUCCESS",
                "total_stats": [
                    {
                        "sub_request_status": "SUCCESS",
                        "total_stat": {
                            "breakdown_stats": {breakdown: []},
                        },
                    }
                ]
            }

    context = CaptureContext()
    request_start = datetime(2026, 8, 9, tzinfo=timezone.utc)
    request_end = datetime(2026, 8, 10, tzinfo=timezone.utc)

    await snapchat_platform_source_integrity.fetch_account_total_campaign_rows(
        context,
        object(),
        "token",
        account_id="account-1",
        request_start=request_start,
        request_end=request_end,
    )
    await snapchat_adsquad_performance._fetch_campaign_adsquad_totals(
        context,
        object(),
        "token",
        campaign_id="campaign-1",
        request_start=request_start,
        request_end=request_end,
    )
    await snapchat_ad_performance._fetch_campaign_ad_totals(
        context,
        object(),
        "token",
        campaign_id="campaign-1",
        request_start=request_start,
        request_end=request_end,
    )

    assert len(context.calls) == 3
    for params in context.calls:
        fields = set(params["fields"].split(","))
        assert {"uniques", "frequency"} <= fields
        assert set(module.FUNNEL_STAT_FIELDS) <= fields
