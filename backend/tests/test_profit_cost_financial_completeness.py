from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

import campaign_ai_profit_accounting_gate as gate
import dashboard_v2_routes as dashboard
import mezan_profit_engine as engine


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = [dict(row) for row in rows]
        self._index = 0

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows[:length]]

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self._rows):
            raise StopAsyncIteration
        row = dict(self._rows[self._index])
        self._index += 1
        return row


class _ReadOnlyCollection:
    def __init__(self, name: str, rows: list[dict[str, Any]], reads: list[str]):
        self._name = name
        self._rows = rows
        self._reads = reads

    def find(self, *_args, **_kwargs) -> _Cursor:
        self._reads.append(self._name)
        return _Cursor(self._rows)

    def __getattr__(self, name: str):
        if name in {
            "insert_one", "insert_many", "update_one", "update_many",
            "replace_one", "delete_one", "delete_many", "find_one_and_update",
        }:
            raise AssertionError(f"unexpected accounting/provider write: {self._name}.{name}")
        raise AttributeError(name)


class _ReadOnlyDB:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]):
        self.reads: list[str] = []
        self._collections = {
            name: _ReadOnlyCollection(name, values, self.reads)
            for name, values in rows.items()
        }

    def __getitem__(self, name: str) -> _ReadOnlyCollection:
        return self._collections.setdefault(
            name, _ReadOnlyCollection(name, [], self.reads)
        )

    def __getattr__(self, name: str) -> _ReadOnlyCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


def _product(
    product_id: str,
    *,
    salla_cost: float | None = None,
    variants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "user_id": "u1",
        "salla_product_id": product_id,
        "mezan_product_id": f"mezan-{product_id}",
        "name": product_id,
        "sku": f"SKU-{product_id}",
        "cost_price_from_salla": salla_cost,
        "variants": variants or [],
    }


def _order(*items: dict[str, Any], total_amount: float = 200.0) -> dict[str, Any]:
    return {
        "order_id": "order-1",
        "order_number": "1001",
        "order_status": "completed",
        "order_date": "2026-09-01T12:00:00+03:00",
        "total_amount": total_amount,
        "products": list(items),
    }


def _install_isolated_profit_io(
    monkeypatch: pytest.MonkeyPatch,
    *,
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    profiles: list[dict[str, Any]] | None = None,
    option_bindings: list[dict[str, Any]] | None = None,
    product_bindings: list[dict[str, Any]] | None = None,
    resources: list[dict[str, Any]] | None = None,
    ads: dict[str, Any] | None = None,
    matched: dict[str, Any] | None = None,
    shipping: dict[str, Any] | None = None,
    operating: dict[str, Any] | None = None,
    recurring: dict[str, Any] | None = None,
) -> _ReadOnlyDB:
    db = _ReadOnlyDB({
        dashboard.PRODUCTS: products,
        dashboard.COST_PROFILES: profiles or [],
        dashboard.BINDINGS: option_bindings or [],
        dashboard.PRODUCT_RESOURCE_BINDINGS: product_bindings or [],
        dashboard.RESOURCES: resources or [],
        "order_status_policy": [],
    })

    async def filtered(*_args, **_kwargs):
        return [dict(order) for order in orders]

    async def settings(*_args, **_kwargs):
        return {"payment_methods": [], "shipping_companies": []}

    async def company_configs(*_args, **_kwargs):
        return []

    async def ads_cost(*_args, **_kwargs):
        return dict(ads or {
            "total": 20.0,
            "bank_commissions": {"total_fee_sar": 0.0},
            "spend_quality": {"amount_complete": True},
            "source_contract": {"source": "isolated_fixture"},
        })

    async def operating_cost(*_args, **_kwargs):
        return dict(operating or {"salaries_total": 0.0})

    async def recurring_cost(*_args, **_kwargs):
        return dict(recurring or {"total": 0.0})

    monkeypatch.setattr(engine, "_filtered_orders", filtered)
    monkeypatch.setattr(engine, "ensure_user_settings", settings)
    monkeypatch.setattr(engine, "orders_to_parsed", lambda value: value)
    monkeypatch.setattr(
        engine,
        "match_settings",
        lambda *_args, **_kwargs: dict(
            matched if matched is not None else {"total_payment_fees": 0.0}
        ),
    )
    monkeypatch.setattr(engine, "get_company_configs", company_configs)
    monkeypatch.setattr(
        engine,
        "aggregate_breakdown",
        lambda *_args, **_kwargs: dict(
            shipping if shipping is not None else {"total_with_tax": 0.0}
        ),
    )
    monkeypatch.setattr(engine, "build_mezan_v2_ads", ads_cost)
    monkeypatch.setattr(engine, "compute_operating_expenses_for_range", operating_cost)
    monkeypatch.setattr(engine, "compute_recurring_obligations_for_range", recurring_cost)
    return db


@pytest.mark.asyncio
async def test_salla_only_actual_cost_is_financially_complete_end_to_end(monkeypatch):
    orders = [_order({
        "product_id": "p-salla",
        "quantity": 2,
        "price": 100.0,
        "total": 200.0,
    })]
    db = _install_isolated_profit_io(
        monkeypatch,
        orders=orders,
        products=[_product("p-salla", salla_cost=30.0)],
    )

    product_cost = await dashboard.build_mezan_v2_product_cost(db, "u1", orders)
    assert product_cost["total"] == 60.0
    assert product_cost["missing_products_count"] == 1  # legacy Mezan setup alert
    assert product_cost["salla_fallback_products_count"] == 1

    envelope = await engine.build_mezan_profit_envelope(
        db, "u1", from_date="2026-09-01", to_date="2026-09-01"
    )
    assert envelope["totals"]["total_product_cost"] == 60.0
    assert envelope["totals"]["net_profit"] == 120.0
    assert envelope["quality"]["complete"] is True
    assert envelope["quality"]["scale_safe"] is True

    decision = await gate.require_profit_accounting_complete_for_scale(
        db, "u1", "scale"
    )
    assert decision["complete"] is True
    assert decision["scale_gate_applied"] is True
    assert len(db.reads) == 18  # six reads per real product-cost traversal


@pytest.mark.asyncio
async def test_variant_mezan_and_mixed_actual_sources_share_financial_contract(monkeypatch):
    orders = [_order(
        {
            "product_id": "p-product",
            "quantity": 2,
            "price": 30.0,
            "total": 60.0,
        },
        {
            "product_id": "p-variant",
            "variant_id": "v-salla",
            "quantity": 1,
            "price": 40.0,
            "total": 40.0,
        },
        {
            "product_id": "p-mezan",
            "quantity": 3,
            "price": 50.0,
            "total": 150.0,
        },
        total_amount=250.0,
    )]
    db = _install_isolated_profit_io(
        monkeypatch,
        orders=orders,
        products=[
            _product("p-product", salla_cost=10.0),
            _product(
                "p-variant",
                variants=[{
                    "id": "v-salla",
                    "sku": "SKU-v-salla",
                    "cost_price_from_salla": 12.0,
                }],
            ),
            _product("p-mezan", salla_cost=99.0),
        ],
        profiles=[{
            "user_id": "u1",
            "salla_product_id": "p-mezan",
            "base_cost": 20.0,
        }],
    )

    cost = await dashboard.build_mezan_v2_product_cost(db, "u1", orders)
    assert cost["total"] == 92.0
    assert cost["breakdown"]["salla_product_fallback"] == 20.0
    assert cost["breakdown"]["salla_variant_fallback"] == 12.0
    assert cost["breakdown"]["mezan_v2_base"] == 60.0
    assert cost["missing_products_count"] == 2
    assert cost["mezan_setup_missing_products_count"] == 2
    assert cost["mezan_setup_missing_lines_count"] == 2
    assert cost["mezan_setup_incomplete_orders_count"] == 1
    assert cost["financial_cost_missing_products_count"] == 0
    assert cost["financial_cost_missing_lines_count"] == 0
    assert cost["financially_incomplete_orders_count"] == 0

    envelope = await engine.build_mezan_profit_envelope(
        db, "u1", from_date="2026-09-01", to_date="2026-09-01"
    )
    assert envelope["totals"]["total_product_cost"] == 92.0
    assert envelope["totals"]["net_profit"] == 138.0
    assert envelope["quality"]["complete"] is True
    assert envelope["quality"]["counter_source"] == "financial_cost_contract"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("product", "profiles", "item", "expected_source", "setup_missing"),
    [
        (
            _product(
                "p-variant-only",
                variants=[{
                    "id": "v-only",
                    "cost_price_from_salla": 14.0,
                }],
            ),
            [],
            {"product_id": "p-variant-only", "variant_id": "v-only", "quantity": 2},
            "salla_variant_fallback",
            1,
        ),
        (
            _product("p-mezan-only", salla_cost=99.0),
            [{
                "user_id": "u1",
                "salla_product_id": "p-mezan-only",
                "base_cost": 14.0,
            }],
            {"product_id": "p-mezan-only", "quantity": 2},
            "mezan_v2_base",
            0,
        ),
    ],
)
async def test_single_actual_cost_source_is_financially_complete(
    monkeypatch, product, profiles, item, expected_source, setup_missing
):
    orders = [_order({**item, "total": 100.0}, total_amount=100.0)]
    db = _install_isolated_profit_io(
        monkeypatch,
        orders=orders,
        products=[product],
        profiles=profiles,
    )
    cost = await dashboard.build_mezan_v2_product_cost(db, "u1", orders)
    assert cost["total"] == 28.0
    assert cost["source_lines"] == {expected_source: 1}
    assert cost["mezan_setup_missing_products_count"] == setup_missing
    assert cost["financial_cost_missing_products_count"] == 0
    assert cost["financially_incomplete_orders_count"] == 0
    envelope = await engine.build_mezan_profit_envelope(
        db, "u1", from_date="2026-09-01", to_date="2026-09-01"
    )
    assert envelope["quality"]["complete"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("with_partial_component", [False, True])
async def test_missing_base_cost_remains_financially_incomplete(
    monkeypatch, with_partial_component
):
    product_bindings = []
    resources = []
    expected_total = 0.0
    if with_partial_component:
        product_bindings = [{
            "id": "partial-service",
            "user_id": "u1",
            "salla_product_id": "p-missing",
            "resource_id": "service-1",
            "quantity": 2,
        }]
        resources = [{
            "id": "service-1",
            "user_id": "u1",
            "kind": "service",
            "unit_cost": 5.0,
        }]
        expected_total = 30.0
    orders = [_order({
        "product_id": "p-missing",
        "quantity": 3,
        "price": 40.0,
        "total": 120.0,
    }, total_amount=120.0)]
    db = _install_isolated_profit_io(
        monkeypatch,
        orders=orders,
        products=[_product("p-missing")],
        product_bindings=product_bindings,
        resources=resources,
    )

    cost = await dashboard.build_mezan_v2_product_cost(db, "u1", orders)
    assert cost["total"] == expected_total
    assert cost["financial_cost_missing_products_count"] == 1
    assert cost["financial_cost_missing_lines_count"] == 1
    assert cost["financially_incomplete_orders_count"] == 1

    envelope = await engine.build_mezan_profit_envelope(
        db, "u1", from_date="2026-09-01", to_date="2026-09-01"
    )
    assert envelope["totals"]["total_product_cost"] == expected_total
    assert envelope["quality"]["complete"] is False
    assert "missing_product_cost" in envelope["quality"]["issues"]
    with pytest.raises(HTTPException) as caught:
        await gate.require_profit_accounting_complete_for_scale(db, "u1", "scale")
    assert getattr(caught.value, "status_code", None) == 409


@pytest.mark.asyncio
async def test_order_without_calculable_product_lines_is_incomplete(monkeypatch):
    orders = [_order(total_amount=80.0)]
    db = _install_isolated_profit_io(
        monkeypatch,
        orders=orders,
        products=[],
    )

    cost = await dashboard.build_mezan_v2_product_cost(db, "u1", orders)
    assert cost["financial_cost_missing_products_count"] == 0
    assert cost["financial_cost_missing_lines_count"] == 0
    assert cost["financially_incomplete_orders_count"] == 1
    envelope = await engine.build_mezan_profit_envelope(
        db, "u1", from_date="2026-09-01", to_date="2026-09-01"
    )
    assert envelope["quality"]["complete"] is False
    assert "incomplete_profit_orders" in envelope["quality"]["issues"]


@pytest.mark.asyncio
async def test_explicit_zero_cost_is_available_for_mezan_and_salla(monkeypatch):
    orders = [_order(
        {"product_id": "p-mezan-zero", "quantity": 1, "total": 50.0},
        {"product_id": "p-salla-zero", "quantity": 1, "total": 50.0},
        total_amount=100.0,
    )]
    db = _install_isolated_profit_io(
        monkeypatch,
        orders=orders,
        products=[
            _product("p-mezan-zero", salla_cost=99.0),
            _product("p-salla-zero", salla_cost=0.0),
        ],
        profiles=[{
            "user_id": "u1",
            "salla_product_id": "p-mezan-zero",
            "base_cost": 0.0,
        }],
    )

    cost = await dashboard.build_mezan_v2_product_cost(db, "u1", orders)
    assert cost["total"] == 0.0
    assert cost["source_lines"] == {
        "mezan_v2_base": 1,
        "salla_product_fallback": 1,
    }
    assert cost["mezan_setup_missing_products_count"] == 1
    assert cost["financial_cost_missing_products_count"] == 0
    assert cost["financially_incomplete_orders_count"] == 0


@pytest.mark.asyncio
async def test_components_services_options_and_quantity_amounts_do_not_change(monkeypatch):
    orders = [_order({
        "product_id": "p-components",
        "quantity": 2,
        "total": 120.0,
        "options": [{"name": "التغليف", "value": "فاخر"}],
    }, total_amount=120.0)]
    duplicate_material = {
        "id": "material-binding",
        "user_id": "u1",
        "salla_product_id": "p-components",
        "resource_id": "material",
        "quantity": 2,
    }
    db = _install_isolated_profit_io(
        monkeypatch,
        orders=orders,
        products=[_product("p-components", salla_cost=99.0)],
        profiles=[{
            "user_id": "u1",
            "salla_product_id": "p-components",
            "base_cost": 10.0,
        }],
        product_bindings=[
            duplicate_material,
            dict(duplicate_material),
            {
                "id": "service-binding",
                "user_id": "u1",
                "salla_product_id": "p-components",
                "resource_id": "service",
                "quantity": 1,
            },
        ],
        option_bindings=[
            {
                "id": "direct-option",
                "user_id": "u1",
                "salla_product_id": "p-components",
                "option_name": "التغليف",
                "value_name": "فاخر",
                "mode": "direct",
                "direct_amount": 5.0,
            },
            {
                "id": "resource-option",
                "user_id": "u1",
                "salla_product_id": "p-components",
                "option_name": "التغليف",
                "value_name": "فاخر",
                "mode": "resource",
                "resource_id": "option-resource",
                "quantity": 1,
            },
        ],
        resources=[
            {"id": "material", "unit_cost": 2.0, "kind": "material"},
            {"id": "service", "unit_cost": 3.0, "kind": "service"},
            {"id": "option-resource", "unit_cost": 4.0, "kind": "material"},
        ],
    )

    cost = await dashboard.build_mezan_v2_product_cost(db, "u1", orders)
    # Per unit: 10 base + (2*2 material) + 3 service + 5 direct + 4 option.
    assert cost["total"] == 52.0
    assert cost["breakdown"] == {
        "mezan_v2_base": 20.0,
        "mezan_v2_variant": 0.0,
        "salla_product_fallback": 0.0,
        "salla_variant_fallback": 0.0,
        "product_components": 14.0,
        "selected_options": 18.0,
    }
    assert cost["financially_incomplete_orders_count"] == 0


def _quality_for(product_cost: dict[str, Any]) -> dict[str, Any]:
    return engine._accounting_quality(
        matched={"total_payment_fees": 0.0},
        shipping={"total_with_tax": 0.0},
        product_cost=product_cost,
        ads={"total": 0.0, "spend_quality": {"amount_complete": True}},
        operating={"salaries_total": 0.0},
        recurring={"total": 0.0},
    )


@pytest.mark.parametrize(
    "product_cost",
    [
        {
            "total": 0.0,
            "financial_cost_contract_version": dashboard.FINANCIAL_COST_COMPLETENESS_VERSION,
            "financial_cost_missing_products_count": 0,
            # Required financial_cost_missing_lines_count is absent.
            "financially_incomplete_orders_count": 0,
            "missing_products_count": 0,
            "incomplete_orders_count": 0,
        },
        {
            "total": 0.0,
            "financial_cost_contract_version": dashboard.FINANCIAL_COST_COMPLETENESS_VERSION,
            "financial_cost_missing_products_count": "0",
            "financial_cost_missing_lines_count": 0,
            "financially_incomplete_orders_count": 0,
            "missing_products_count": 0,
            "incomplete_orders_count": 0,
        },
        {
            "total": None,
            "financial_cost_contract_version": dashboard.FINANCIAL_COST_COMPLETENESS_VERSION,
            "financial_cost_missing_products_count": 0,
            "financial_cost_missing_lines_count": 0,
            "financially_incomplete_orders_count": 0,
        },
        {
            "total": 0.0,
            "financial_cost_contract_version": dashboard.FINANCIAL_COST_COMPLETENESS_VERSION,
            "financial_cost_missing_products_count": 1,
            "financial_cost_missing_lines_count": 0,
            "financially_incomplete_orders_count": 0,
        },
        {"total": 0.0, "missing_products_count": 0},
    ],
)
def test_absent_partial_or_invalid_financial_contract_fails_closed(product_cost):
    quality = _quality_for(product_cost)
    assert quality["known"] is False
    assert quality["complete"] is False
    assert quality["scale_safe"] is False


def test_complete_legacy_contract_keeps_conservative_compatibility():
    quality = _quality_for({
        "total": 0.0,
        "missing_products_count": 0,
        "incomplete_orders_count": 0,
    })
    assert quality["known"] is True
    assert quality["complete"] is True
    assert quality["counter_source"] == "legacy_mezan_setup_conservative"


def test_gate_does_not_mix_partial_new_totals_or_envelope_with_legacy_zeros():
    partial = {
        "financial_cost_contract_version": dashboard.FINANCIAL_COST_COMPLETENESS_VERSION,
        "financial_cost_missing_products_count": 0,
        "financially_incomplete_orders_count": 0,
        "missing_product_cost_count": 0,
        "incomplete_profit_orders_count": 0,
    }
    assert gate.accounting_quality_from_totals(partial)["complete"] is False
    assert gate.accounting_quality_from_envelope({
        "quality": {**partial, "known": True, "complete": True, "scale_safe": True}
    })["complete"] is False


@pytest.mark.asyncio
async def test_salla_only_stays_blocked_when_advertising_is_incomplete(monkeypatch):
    orders = [_order({"product_id": "p-salla", "quantity": 1, "total": 100.0})]
    db = _install_isolated_profit_io(
        monkeypatch,
        orders=orders,
        products=[_product("p-salla", salla_cost=30.0)],
        ads={
            "total": 20.0,
            "spend_quality": {"amount_complete": False},
            "source_contract": {"source": "isolated_fixture"},
        },
    )
    envelope = await engine.build_mezan_profit_envelope(
        db, "u1", from_date="2026-09-01", to_date="2026-09-01"
    )
    assert envelope["quality"]["component_known"]["product_cost"] is True
    assert envelope["quality"]["component_known"]["advertising"] is False
    assert envelope["quality"]["complete"] is False
    with pytest.raises(HTTPException) as caught:
        await gate.require_profit_accounting_complete_for_scale(db, "u1", "scale")
    assert getattr(caught.value, "status_code", None) == 409


@pytest.mark.asyncio
async def test_salla_only_stays_blocked_when_another_accounting_component_is_missing(
    monkeypatch,
):
    orders = [_order({"product_id": "p-salla", "quantity": 1, "total": 100.0})]
    db = _install_isolated_profit_io(
        monkeypatch,
        orders=orders,
        products=[_product("p-salla", salla_cost=30.0)],
        shipping={},
    )
    envelope = await engine.build_mezan_profit_envelope(
        db, "u1", from_date="2026-09-01", to_date="2026-09-01"
    )
    assert envelope["quality"]["component_known"]["product_cost"] is True
    assert envelope["quality"]["component_known"]["shipping"] is False
    assert envelope["quality"]["complete"] is False


@pytest.mark.asyncio
async def test_defensive_actions_never_enter_profit_or_provider_io(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("profit/provider I/O must not run for defensive actions")

    monkeypatch.setattr(gate, "build_mezan_profit_envelope", forbidden)
    assert (await gate.require_profit_accounting_complete_for_scale(
        object(), "u1", "pause"
    ))["scale_gate_applied"] is False
    assert (await gate.require_profit_accounting_complete_for_scale(
        object(), "u1", "reduce"
    ))["scale_gate_applied"] is False
