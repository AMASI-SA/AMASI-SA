import pytest

import mezan_profit_engine as engine


@pytest.mark.asyncio
async def test_profit_engine_keeps_incomplete_advertising_unknown(monkeypatch):
    async def filtered_orders(*args, **kwargs):
        return [{"total_amount": 100}]

    async def settings(*args, **kwargs):
        return {}

    async def company_configs(*args, **kwargs):
        return []

    async def product_cost(*args, **kwargs):
        return {
            "total": 20,
            "missing_products_count": 0,
            "incomplete_orders_count": 0,
            "source_contract": {},
        }

    async def ads(*args, **kwargs):
        return {
            "total": None,
            "known_subtotal_sar": 30,
            "spend_quality": {
                "status": "incomplete",
                "amount_complete": False,
            },
            "bank_commissions": None,
            "source_contract": {},
        }

    async def operating(*args, **kwargs):
        return {"salaries_total": 5}

    async def recurring(*args, **kwargs):
        return {"total": 2}

    monkeypatch.setattr(engine, "_filtered_orders", filtered_orders)
    monkeypatch.setattr(engine, "ensure_user_settings", settings)
    monkeypatch.setattr(engine, "orders_to_parsed", lambda rows: {})
    monkeypatch.setattr(engine, "match_settings", lambda *args, **kwargs: {"total_payment_fees": 10})
    monkeypatch.setattr(engine, "get_company_configs", company_configs)
    monkeypatch.setattr(engine, "aggregate_breakdown", lambda *args, **kwargs: {"total_with_tax": 5})
    monkeypatch.setattr(engine, "build_mezan_v2_product_cost", product_cost)
    monkeypatch.setattr(engine, "build_mezan_v2_ads", ads)
    monkeypatch.setattr(engine, "compute_operating_expenses_for_range", operating)
    monkeypatch.setattr(engine, "compute_recurring_obligations_for_range", recurring)

    result = await engine.build_mezan_profit_envelope(
        object(),
        "owner-1",
        from_date="2026-08-01",
        to_date="2026-08-23",
    )

    assert result["quality"]["component_known"]["advertising"] is False
    assert result["quality"]["known"] is False
    assert "unknown_component:advertising" in result["quality"]["issues"]
    assert result["quality"]["scale_safe"] is False
    assert result["totals"]["total_ads_cost"] is None
    assert result["totals"]["net_profit"] is None
    assert result["totals"]["profit_before_unknown_advertising_sar"] == 58
    assert result["components"]["advertising"]["amount_sar"] is None
    assert result["components"]["advertising"]["known"] is False
    assert result["components"]["advertising"]["known_subtotal_sar"] == 30


@pytest.mark.asyncio
async def test_profit_engine_calculates_net_profit_when_ad_amount_is_complete(monkeypatch):
    async def filtered_orders(*args, **kwargs):
        return [{"total_amount": 100}]

    async def settings(*args, **kwargs):
        return {}

    async def company_configs(*args, **kwargs):
        return []

    async def product_cost(*args, **kwargs):
        return {
            "total": 20,
            "missing_products_count": 0,
            "incomplete_orders_count": 0,
            "source_contract": {},
        }

    async def ads(*args, **kwargs):
        return {
            "total": 15,
            "known_subtotal_sar": 15,
            "spend_quality": {
                "status": "complete",
                "amount_complete": True,
            },
            "bank_commissions": {"total_fee_sar": 2},
            "source_contract": {},
        }

    async def operating(*args, **kwargs):
        return {"salaries_total": 5}

    async def recurring(*args, **kwargs):
        return {"total": 2}

    monkeypatch.setattr(engine, "_filtered_orders", filtered_orders)
    monkeypatch.setattr(engine, "ensure_user_settings", settings)
    monkeypatch.setattr(engine, "orders_to_parsed", lambda rows: {})
    monkeypatch.setattr(engine, "match_settings", lambda *args, **kwargs: {"total_payment_fees": 10})
    monkeypatch.setattr(engine, "get_company_configs", company_configs)
    monkeypatch.setattr(engine, "aggregate_breakdown", lambda *args, **kwargs: {"total_with_tax": 5})
    monkeypatch.setattr(engine, "build_mezan_v2_product_cost", product_cost)
    monkeypatch.setattr(engine, "build_mezan_v2_ads", ads)
    monkeypatch.setattr(engine, "compute_operating_expenses_for_range", operating)
    monkeypatch.setattr(engine, "compute_recurring_obligations_for_range", recurring)

    result = await engine.build_mezan_profit_envelope(
        object(),
        "owner-1",
        from_date="2026-08-01",
        to_date="2026-08-23",
    )

    assert result["quality"]["component_known"]["advertising"] is True
    assert result["quality"]["known"] is True
    assert result["quality"]["complete"] is True
    assert result["totals"]["total_ads_cost"] == 15
    assert result["totals"]["total_payment_fees"] == 12
    assert result["totals"]["net_profit"] == 41
    assert result["totals"]["profit_before_unknown_advertising_sar"] is None
    assert result["components"]["advertising"]["amount_sar"] == 15
    assert result["components"]["advertising"]["known"] is True
