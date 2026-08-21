from pathlib import Path

import pytest

import mezan_campaign_profit_loader as profit_loader


@pytest.mark.asyncio
async def test_mezan_profit_loader_uses_mezan_pnl_sources(monkeypatch):
    async def fake_orders(*args, **kwargs):
        return [
            {"total_amount": 120.0},
            {"total_amount": 180.0},
        ]

    async def fake_settings(*args, **kwargs):
        return {}

    async def fake_company_configs(*args, **kwargs):
        return {}

    async def fake_product_cost(*args, **kwargs):
        return {
            "total": 100.0,
            "missing_products_count": 0,
            "incomplete_orders_count": 0,
            "source_contract": {"source": "mezan_products_v2"},
        }

    async def fake_ads(*args, **kwargs):
        return {
            "total": 30.0,
            "bank_commissions": {"total_fee_sar": 5.0},
            "source_contract": {"source": "mezan_ads_v2"},
        }

    async def fake_operating(*args, **kwargs):
        return {"salaries_total": 10.0}

    async def fake_recurring(*args, **kwargs):
        return {"total": 20.0}

    monkeypatch.setattr(profit_loader, "_filtered_orders", fake_orders)
    monkeypatch.setattr(profit_loader, "ensure_user_settings", fake_settings)
    monkeypatch.setattr(profit_loader, "orders_to_parsed", lambda rows: {"rows": rows})
    monkeypatch.setattr(
        profit_loader,
        "match_settings",
        lambda *args, **kwargs: {"total_payment_fees": 15.0},
    )
    monkeypatch.setattr(profit_loader, "get_company_configs", fake_company_configs)
    monkeypatch.setattr(
        profit_loader,
        "aggregate_breakdown",
        lambda orders, configs: {"total_with_tax": 20.0},
    )
    monkeypatch.setattr(profit_loader, "build_mezan_v2_product_cost", fake_product_cost)
    monkeypatch.setattr(profit_loader, "build_mezan_v2_ads", fake_ads)
    monkeypatch.setattr(profit_loader, "compute_operating_expenses_for_range", fake_operating)
    monkeypatch.setattr(profit_loader, "compute_recurring_obligations_for_range", fake_recurring)

    totals = await profit_loader.build_mezan_profit_totals(
        object(),
        "owner-1",
        from_date="2026-08-01",
        to_date="2026-08-21",
    )

    assert totals["total_sales"] == 300.0
    assert totals["total_orders"] == 2
    assert totals["total_payment_fees"] == 20.0
    assert totals["total_shipping_cost"] == 20.0
    assert totals["total_product_cost"] == 100.0
    assert totals["total_ads_cost"] == 30.0
    assert totals["operating_expenses_total"] == 30.0
    assert totals["net_profit"] == 100.0
    assert totals["profit_source"] == "mezan_profit_engine_v2_read_only"


@pytest.mark.asyncio
async def test_campaign_loader_contract_returns_totals(monkeypatch):
    async def fake_totals(*args, **kwargs):
        return {"net_profit": 1234.5, "total_sales": 5000.0}

    monkeypatch.setattr(profit_loader, "build_mezan_profit_totals", fake_totals)
    loader = profit_loader.make_mezan_campaign_profit_loader(object())
    payload = await loader(
        user={"id": "owner-1"},
        from_date="2026-08-01",
        to_date="2026-08-21",
        payment_methods=None,
        shipping_companies=None,
        include_legacy_analyses=False,
        allow_self_heal=False,
    )

    assert payload["totals"]["net_profit"] == 1234.5
    assert payload["dashboard_source"] == "mezan_profit_engine_v2_read_only"
    assert payload["accounting_write_reached"] is False
    assert payload["qoyod_write_reached"] is False


def test_isolated_worker_wires_profit_loader_into_campaign_ai():
    source = (
        Path(__file__).resolve().parents[1] / "campaign_ai_worker_runner.py"
    ).read_text(encoding="utf-8")
    assert "make_mezan_campaign_profit_loader" in source
    assert "profit_loader = make_mezan_campaign_profit_loader(db)" in source
    assert "business_context_loader=profit_loader" in source
