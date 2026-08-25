from __future__ import annotations

from datetime import date

import pytest

import dashboard_v2_routes as module


@pytest.mark.asyncio
async def test_dashboard_observer_compares_without_enabling_decisions(monkeypatch):
    async def legacy(*_args, **_kwargs):
        return {
            "total_sar": 9123.45,
            "quality": {"amount_complete": True},
        }

    async def unified(*_args, **_kwargs):
        return {
            "contract_version": "unified-marketing-data-v1",
            "period": {
                "date_from": "2026-08-24",
                "date_to": "2026-08-24",
                "timezone": "Asia/Riyadh",
                "action_report_time": "conversion",
            },
            "totals": {
                "delivery": {
                    "spend_sar": {"amount": 9123.45, "currency": "SAR"},
                },
                "platform_outcomes": {
                    "conversions": 82,
                    "revenue": {"amount": 5284.58, "currency": "USD"},
                },
                "commerce_outcomes": {
                    "status": "complete",
                    "orders": 86,
                    "revenue": {"amount": 20241.48, "currency": "SAR"},
                },
                "quality": {
                    "coverage_status": "complete",
                    "amount_complete": True,
                    "reconciliation_status": "reconciled",
                },
            },
            "order_summary": {"matched_orders": 86},
        }

    monkeypatch.setattr(module, "load_snapchat_dashboard_spend", legacy)
    monkeypatch.setattr(module, "load_unified_marketing_account_report", unified)

    result = await module.build_dashboard_v2_unified_shadow(
        object(),
        "owner-1",
        from_date=date(2026, 8, 24),
        to_date=date(2026, 8, 24),
    )

    assert result["shadow_passed"] is True
    assert result["cutover_ready"] is True
    assert result["comparison"]["spend_sar"]["delta"] == 0.0
    assert result["decision_eligibility"]["eligible"] is False
    assert result["provider_write_reached"] is False
    assert result["accounting_write_reached"] is False


@pytest.mark.asyncio
async def test_dashboard_observer_fails_closed_without_touching_writes(monkeypatch):
    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(module, "load_snapchat_dashboard_spend", unavailable)

    result = await module.build_dashboard_v2_unified_shadow(
        object(),
        "owner-1",
        from_date=date(2026, 8, 24),
        to_date=date(2026, 8, 24),
    )

    assert result["shadow_passed"] is False
    assert result["reason"] == "RuntimeError"
    assert result["decision_eligibility"]["eligible"] is False
    assert result["provider_write_reached"] is False
    assert result["campaign_write_reached"] is False
    assert result["qoyod_write_reached"] is False
