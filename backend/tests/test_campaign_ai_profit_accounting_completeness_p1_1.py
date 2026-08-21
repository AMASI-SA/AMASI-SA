import pytest
from fastapi import HTTPException

import campaign_ai_profit_accounting_gate as gate
import campaign_ai_monthly_profit_goal_v1 as goal


def test_p1_1_accounting_quality_requires_zero_missing_and_incomplete():
    assert gate.accounting_quality_from_totals({
        "missing_product_cost_count": 0,
        "incomplete_profit_orders_count": 0,
    })["complete"] is True
    assert gate.accounting_quality_from_totals({
        "missing_product_cost_count": 1,
        "incomplete_profit_orders_count": 0,
    })["complete"] is False
    assert gate.accounting_quality_from_totals({
        "missing_product_cost_count": 0,
        "incomplete_profit_orders_count": 2,
    })["complete"] is False
    assert gate.accounting_quality_from_totals({})["complete"] is False


@pytest.mark.asyncio
async def test_p1_1_reduce_and_pause_are_not_blocked(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("profit loader must not run for defensive action")
    monkeypatch.setattr(gate, "build_mezan_profit_totals", forbidden)
    assert (await gate.require_profit_accounting_complete_for_scale(object(), "u", "reduce"))["scale_gate_applied"] is False
    assert (await gate.require_profit_accounting_complete_for_scale(object(), "u", "pause"))["scale_gate_applied"] is False


@pytest.mark.asyncio
async def test_p1_1_scale_fails_closed_when_profit_inputs_incomplete(monkeypatch):
    async def loader(*args, **kwargs):
        return {
            "missing_product_cost_count": 1,
            "incomplete_profit_orders_count": 3,
            "profit_source": "mezan_profit_engine_v2_read_only",
        }
    monkeypatch.setattr(gate, "build_mezan_profit_totals", loader)
    with pytest.raises(HTTPException) as caught:
        await gate.require_profit_accounting_complete_for_scale(object(), "u", "scale")
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "campaign_ai_profit_accounting_incomplete"
    assert caught.value.detail["recovery_action"] == "complete_missing_profit_inputs_then_refresh_recommendation"


@pytest.mark.asyncio
async def test_p1_1_scale_allowed_only_when_profit_inputs_complete(monkeypatch):
    async def loader(*args, **kwargs):
        return {
            "missing_product_cost_count": 0,
            "incomplete_profit_orders_count": 0,
            "profit_source": "mezan_profit_engine_v2_read_only",
        }
    monkeypatch.setattr(gate, "build_mezan_profit_totals", loader)
    result = await gate.require_profit_accounting_complete_for_scale(object(), "u", "scale")
    assert result["complete"] is True
    assert result["scale_gate_applied"] is True


def test_p1_1_goal_context_stays_numeric_but_blocks_expansion_when_accounting_incomplete():
    result = goal._derive_goal_progress(
        goal={"minimum_net_profit_sar": 100000.0},
        month_to_date={
            "net_profit": 40000.0,
            "missing_product_cost_count": 1,
            "incomplete_profit_orders_count": 2,
        },
        end=__import__("datetime").date(2026, 8, 21),
    )
    assert result["progress_available"] is True
    assert result["net_profit_to_date_sar"] == 40000.0
    assert result["profit_accounting_complete"] is False
    assert result["scale_execution_allowed_by_profit_accounting"] is False
    assert result["status"] == "profit_accounting_incomplete"
    assert result["phase"] == "protect_data_quality"
