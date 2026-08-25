from __future__ import annotations

from datetime import date

import pytest

from unified_marketing.dashboard_shadow import build_dashboard_unified_shadow
from unified_marketing.gateway import load_unified_marketing_account_report


def unified_report(*, spend_sar: float, complete: bool = True) -> dict:
    return {
        "contract_version": "unified-marketing-data-v1",
        "period": {
            "date_from": "2026-08-25",
            "date_to": "2026-08-25",
            "timezone": "Asia/Riyadh",
            "action_report_time": "conversion",
        },
        "totals": {
            "delivery": {"spend_sar": {"amount": spend_sar, "currency": "SAR"}},
            "platform_outcomes": {
                "conversions": 12,
                "revenue": {"amount": 450.0, "currency": "USD"},
            },
            "commerce_outcomes": {
                "status": "complete",
                "orders": 14,
                "revenue": {"amount": 3200.0, "currency": "SAR"},
            },
            "quality": {
                "coverage_status": "complete" if complete else "partial",
                "amount_complete": complete,
            },
        },
        "order_summary": {"matched_orders": 14},
    }


def test_dashboard_shadow_passes_only_exact_complete_spend():
    result = build_dashboard_unified_shadow(
        {"total_sar": 1000.0, "quality": {"amount_complete": True}},
        unified_report(spend_sar=1000.02),
    )

    assert result["shadow_passed"] is True
    assert result["cutover_ready"] is False
    assert result["comparison"]["spend_sar"]["match"] is True
    assert result["unified_summary"]["salla_orders"] == 14
    assert result["decision_eligibility"]["eligible"] is False
    assert result["accounting_write_reached"] is False


def test_dashboard_shadow_fails_closed_on_coverage_or_amount_drift():
    result = build_dashboard_unified_shadow(
        {"total_sar": 1000.0, "quality": {"amount_complete": True}},
        unified_report(spend_sar=1010.0, complete=False),
    )

    assert result["shadow_passed"] is False
    assert result["comparison"]["coverage_complete"] is False
    assert result["comparison"]["spend_sar"]["match"] is False


@pytest.mark.asyncio
async def test_gateway_rejects_unregistered_provider_before_any_read():
    with pytest.raises(ValueError, match="unsupported_unified_marketing_provider"):
        await load_unified_marketing_account_report(
            object(),
            "u1",
            provider="meta_ads",
            date_from=date(2026, 8, 25),
            date_to=date(2026, 8, 25),
            timezone_name="Asia/Riyadh",
        )
