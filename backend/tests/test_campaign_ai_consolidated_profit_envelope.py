import pytest
from fastapi import HTTPException

import campaign_ai_profit_accounting_gate as gate
import mezan_campaign_profit_loader as loader_module
import mezan_profit_engine as engine


@pytest.mark.asyncio
async def test_consolidated_envelope_arithmetic_and_complete_quality(monkeypatch):
    async def filtered(*args, **kwargs):
        return [{"total_amount": 200.0}, {"total_amount": 100.0}]
    async def settings(*args, **kwargs):
        return {"payment_methods": [], "shipping_companies": []}
    async def company_configs(*args, **kwargs):
        return []
    async def product_cost(*args, **kwargs):
        return {"total": 80.0, "missing_products_count": 0, "incomplete_orders_count": 0, "source_contract": {"v": 1}}
    async def ads(*args, **kwargs):
        return {"total": 30.0, "bank_commissions": {"total_fee_sar": 2.0}, "source_contract": {"v": 2}}
    async def operating(*args, **kwargs):
        return {"salaries_total": 20.0}
    async def recurring(*args, **kwargs):
        return {"total": 10.0}

    monkeypatch.setattr(engine, "_filtered_orders", filtered)
    monkeypatch.setattr(engine, "ensure_user_settings", settings)
    monkeypatch.setattr(engine, "orders_to_parsed", lambda orders: orders)
    monkeypatch.setattr(engine, "match_settings", lambda *a, **k: {"total_payment_fees": 8.0})
    monkeypatch.setattr(engine, "get_company_configs", company_configs)
    monkeypatch.setattr(engine, "aggregate_breakdown", lambda *a, **k: {"total_with_tax": 15.0})
    monkeypatch.setattr(engine, "build_mezan_v2_product_cost", product_cost)
    monkeypatch.setattr(engine, "build_mezan_v2_ads", ads)
    monkeypatch.setattr(engine, "compute_operating_expenses_for_range", operating)
    monkeypatch.setattr(engine, "compute_recurring_obligations_for_range", recurring)

    envelope = await engine.build_mezan_profit_envelope(object(), "u1", from_date="2026-08-01", to_date="2026-08-02")
    assert envelope["contract_version"] == "mezan_profit_envelope_v1"
    assert envelope["quality"]["known"] is True
    assert envelope["quality"]["complete"] is True
    assert envelope["quality"]["scale_safe"] is True
    assert envelope["quality"]["unknown_is_zero"] is False
    assert envelope["totals"]["total_sales"] == 300.0
    assert envelope["totals"]["net_profit"] == 135.0


@pytest.mark.asyncio
async def test_missing_component_is_unknown_and_not_zero(monkeypatch):
    async def filtered(*args, **kwargs): return []
    async def settings(*args, **kwargs): return {"payment_methods": [], "shipping_companies": []}
    async def company_configs(*args, **kwargs): return []
    async def product_cost(*args, **kwargs): return {"total": 0, "missing_products_count": 0, "incomplete_orders_count": 0}
    async def ads(*args, **kwargs): return {}  # total absent => unknown, not proven zero
    async def operating(*args, **kwargs): return {"salaries_total": 0}
    async def recurring(*args, **kwargs): return {"total": 0}
    monkeypatch.setattr(engine, "_filtered_orders", filtered)
    monkeypatch.setattr(engine, "ensure_user_settings", settings)
    monkeypatch.setattr(engine, "orders_to_parsed", lambda orders: orders)
    monkeypatch.setattr(engine, "match_settings", lambda *a, **k: {"total_payment_fees": 0})
    monkeypatch.setattr(engine, "get_company_configs", company_configs)
    monkeypatch.setattr(engine, "aggregate_breakdown", lambda *a, **k: {"total_with_tax": 0})
    monkeypatch.setattr(engine, "build_mezan_v2_product_cost", product_cost)
    monkeypatch.setattr(engine, "build_mezan_v2_ads", ads)
    monkeypatch.setattr(engine, "compute_operating_expenses_for_range", operating)
    monkeypatch.setattr(engine, "compute_recurring_obligations_for_range", recurring)
    envelope = await engine.build_mezan_profit_envelope(object(), "u1", from_date="2026-08-01", to_date="2026-08-01")
    assert envelope["quality"]["known"] is False
    assert envelope["quality"]["complete"] is False
    assert "unknown_component:advertising" in envelope["quality"]["issues"]


def test_gate_quality_envelope_is_fail_closed_when_unknown():
    quality = gate.accounting_quality_from_envelope({"quality": {"known": False, "complete": True, "scale_safe": True}})
    assert quality["complete"] is False
    assert quality["scale_safe"] is False
    assert quality["unknown_is_zero"] is False


@pytest.mark.asyncio
async def test_scale_gate_uses_envelope_and_blocks_unknown(monkeypatch):
    async def envelope(*args, **kwargs):
        return {"source": "mezan_profit_engine_v2_read_only", "contract_version": "mezan_profit_envelope_v1", "quality": {"known": False, "complete": False, "scale_safe": False, "issues": ["unknown_component:advertising"]}}
    monkeypatch.setattr(gate, "build_mezan_profit_envelope", envelope)
    with pytest.raises(HTTPException) as exc:
        await gate.require_profit_accounting_complete_for_scale(object(), "u1", "scale")
    assert exc.value.status_code == 409
    assert exc.value.detail["unknown_is_zero"] is False


@pytest.mark.asyncio
async def test_defensive_action_does_not_require_profit_envelope(monkeypatch):
    async def should_not_run(*args, **kwargs):
        raise AssertionError("profit envelope should not be loaded for defensive action")
    monkeypatch.setattr(gate, "build_mezan_profit_envelope", should_not_run)
    result = await gate.require_profit_accounting_complete_for_scale(object(), "u1", "reduce")
    assert result == {"complete": True, "scale_gate_applied": False}


@pytest.mark.asyncio
async def test_campaign_loader_is_thin_adapter_over_envelope(monkeypatch):
    expected = {
        "contract_version": "mezan_profit_envelope_v1",
        "source": "mezan_profit_engine_v2_read_only",
        "totals": {"net_profit": 123.0},
        "quality": {"known": True, "complete": True, "scale_safe": True},
    }
    async def envelope(*args, **kwargs): return expected
    monkeypatch.setattr(loader_module, "build_mezan_profit_envelope", envelope)
    loader = loader_module.make_mezan_campaign_profit_loader(object())
    payload = await loader(user={"id": "u1"}, from_date="2026-08-01", to_date="2026-08-02")
    assert payload["totals"] == {"net_profit": 123.0}
    assert payload["profit_envelope"] is expected
    assert payload["source_only"] is True
