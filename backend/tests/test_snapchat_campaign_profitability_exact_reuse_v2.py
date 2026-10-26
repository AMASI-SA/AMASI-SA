from __future__ import annotations

from copy import deepcopy

import pytest

from integrations_control_center import (
    snapchat_campaign_profitability_exact_reuse as module,
)


@pytest.mark.asyncio
async def test_capture_reuses_the_same_campaign_matcher(monkeypatch):
    orders = [
        {"id": "order-1", "campaign_id": "campaign-1", "total_amount": 100},
        {"id": "order-2", "campaign_id": "campaign-2", "total_amount": 200},
        {"id": "order-3", "campaign_id": "unknown", "total_amount": 300},
    ]

    async def filtered_orders(*args, **kwargs):
        return deepcopy(orders)

    monkeypatch.setattr(module, "_filtered_orders", filtered_orders)

    identities = [
        {
            "account_id": "account-1",
            "campaign_id": "campaign-1",
            "campaign_name": "حملة 1",
        },
        {
            "account_id": "account-1",
            "campaign_id": "campaign-2",
            "campaign_name": "حملة 2",
        },
    ]

    matched = await module.capture_exact_matched_orders(
        object(),
        "owner-1",
        date_from="2026-08-04",
        date_to="2026-08-04",
        identities=identities,
    )

    assert [order["id"] for order in matched[("account-1", "campaign-1")]] == [
        "order-1"
    ]
    assert [order["id"] for order in matched[("account-1", "campaign-2")]] == [
        "order-2"
    ]
    assert sum(len(rows) for rows in matched.values()) == 2


@pytest.mark.asyncio
async def test_profitability_uses_exact_orders_and_campaign_spend(monkeypatch):
    module._CACHE.clear()

    async def cost_revision(db, user_id):
        return 0

    async def cost_context(db, user_id):
        return {"loaded": True}

    def order_cost(order, context):
        return {
            "order_sales_sar": float(order["total_amount"]),
            "product_cost_sar": float(order["product_cost"]),
            "allocated_product_sales_sar": float(order["total_amount"]),
            "unallocated_sales_sar": 0.0,
            "missing_everywhere": False,
            "uses_salla_fallback": False,
            "mezan_cost_complete": True,
            "no_products": False,
            "lines": [],
        }

    monkeypatch.setattr(module.profitability, "_load_cost_context", cost_context)
    monkeypatch.setattr(module.profitability, "_order_cost_and_products", order_cost)
    monkeypatch.setattr(module, "get_product_cost_revision", cost_revision)

    by_campaign, totals = await module.calculate_profitability_from_exact_matches(
        object(),
        "owner-1",
        date_from="2026-08-04",
        date_to="2026-08-04",
        matched_orders={
            ("account-1", "campaign-1"): [
                {"total_amount": 100.0, "product_cost": 40.0},
                {"total_amount": 50.0, "product_cost": 20.0},
            ]
        },
        campaign_spend={("account-1", "campaign-1"): 30.0},
    )

    row = by_campaign[("account-1", "campaign-1")]
    assert row["orders"] == 2
    assert row["sales_sar"] == 150.0
    assert row["product_cost_sar"] == 60.0
    assert row["ad_spend_sar"] == 30.0
    assert row["contribution_profit_sar"] == 60.0
    assert row["profit_margin_pct"] == 40.0
    assert totals["orders"] == 2
    assert totals["product_cost_sar"] == 60.0
    assert totals["contribution_profit_sar"] == 60.0


@pytest.mark.asyncio
async def test_profitability_cache_is_invalidated_by_mezan_cost_revision(monkeypatch):
    module._CACHE.clear()
    revisions = iter((3, 4))
    loads = 0

    async def cost_revision(db, user_id):
        return next(revisions)

    async def cost_context(db, user_id):
        nonlocal loads
        loads += 1
        return {}

    monkeypatch.setattr(module, "get_product_cost_revision", cost_revision)
    monkeypatch.setattr(module.profitability, "_load_cost_context", cost_context)

    kwargs = {
        "date_from": "2026-08-04",
        "date_to": "2026-08-04",
        "matched_orders": {},
        "campaign_spend": {},
    }
    await module.calculate_profitability_from_exact_matches(object(), "owner-1", **kwargs)
    await module.calculate_profitability_from_exact_matches(object(), "owner-1", **kwargs)

    assert loads == 2


def test_source_policy_is_exact_and_read_only():
    assert module.SOURCE_MODE == "snapchat_salla_visible_matches_profitability_v2"
    assert module.CACHE_TTL_SECONDS == 5 * 60


@pytest.fixture
def calculation(monkeypatch):
    module._CACHE.clear()
    loads = []

    async def cost_revision(db, user_id):
        return 7

    async def cost_context(db, user_id):
        loads.append(user_id)
        return {}

    def order_cost(order, context):
        cost = sum(item["quantity"] * item["cost"] for item in order["products"])
        sales = order["total_amount"]
        return {"order_sales_sar": sales, "product_cost_sar": cost,
                "allocated_product_sales_sar": sales, "unallocated_sales_sar": 0,
                "missing_everywhere": False, "uses_salla_fallback": False,
                "mezan_cost_complete": True, "no_products": False, "lines": []}

    monkeypatch.setattr(module, "get_product_cost_revision", cost_revision)
    monkeypatch.setattr(module.profitability, "_load_cost_context", cost_context)
    monkeypatch.setattr(module.profitability, "_order_cost_and_products", order_cost)
    kwargs = {"date_from": "2026-10-25", "date_to": "2026-10-25",
              "matched_orders": {("account-1", "campaign-1"): [
                  {"id": "o1", "total_amount": 100, "products": [{"quantity": 1, "cost": 40}]},
                  {"id": "o2", "total_amount": 50, "products": [{"quantity": 1, "cost": 20}]},
              ]}, "campaign_spend": {("account-1", "campaign-1"): 30}}
    return kwargs, loads


@pytest.mark.asyncio
@pytest.mark.parametrize(("change", "expected_orders", "expected_sales", "expected_cost", "expected_spend", "expected_profit"), [
    ("sales", 2, 170, 60, 30, 80),
    ("line_quantity", 2, 150, 100, 30, 20),
    ("added_order", 3, 200, 80, 30, 90),
    ("removed_order", 1, 100, 40, 30, 30),
    ("spend", 2, 150, 60, 50, 40),
])
async def test_changed_cohort_or_spend_cannot_reuse_old_economics(calculation, change, expected_orders, expected_sales, expected_cost, expected_spend, expected_profit):
    kwargs, loads = calculation
    await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    orders = kwargs["matched_orders"][("account-1", "campaign-1")]
    if change == "sales":
        orders[0]["total_amount"] = 120
    elif change == "line_quantity":
        orders[0]["products"][0]["quantity"] = 2
    elif change == "added_order":
        orders.append({"id": "o3", "total_amount": 50, "products": [{"quantity": 1, "cost": 20}]})
    elif change == "removed_order":
        orders.pop()
    else:
        kwargs["campaign_spend"][("account-1", "campaign-1")] = 50
    rows, totals = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    row = rows[("account-1", "campaign-1")]
    assert (row["orders"], row["sales_sar"], row["product_cost_sar"], row["ad_spend_sar"], row["contribution_profit_sar"]) == (expected_orders, expected_sales, expected_cost, expected_spend, expected_profit)
    assert totals["contribution_profit_sar"] == expected_profit
    assert loads == ["owner", "owner"]


@pytest.mark.asyncio
async def test_unchanged_reordered_inputs_reuse_cache_without_sharing_mutable_results(calculation):
    kwargs, loads = calculation
    first, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    first[("account-1", "campaign-1")]["sales_sar"] = 999
    kwargs["matched_orders"][("account-1", "campaign-1")].reverse()
    second, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert second[("account-1", "campaign-1")]["sales_sar"] == 150
    assert loads == ["owner"]


@pytest.mark.asyncio
async def test_changed_campaign_account_binding_cannot_return_previous_scope(calculation):
    kwargs, loads = calculation
    await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    kwargs["matched_orders"] = {("account-2", "campaign-2"): kwargs["matched_orders"][("account-1", "campaign-1")]}
    kwargs["campaign_spend"] = {("account-2", "campaign-2"): 30}
    rows, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert set(rows) == {("account-2", "campaign-2")}
    assert loads == ["owner", "owner"]


@pytest.mark.asyncio
async def test_unserializable_input_bypasses_cache_instead_of_reusing_unknown_revision(calculation):
    kwargs, loads = calculation
    kwargs["matched_orders"][("account-1", "campaign-1")][0]["non_financial_metadata"] = float("nan")
    await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert loads == ["owner", "owner"]
    assert module._CACHE == {}
