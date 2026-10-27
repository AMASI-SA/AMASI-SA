from __future__ import annotations

from copy import deepcopy
import asyncio
import threading
from functools import wraps
from datetime import datetime, timezone
from bson import ObjectId
from bson.decimal128 import Decimal128
from order_status_policy import effective_product_cost

import pytest

from integrations_control_center import (
    snapchat_campaign_profitability_exact_reuse as module,
)


@pytest.fixture(autouse=True)
def stable_default_policy(monkeypatch):
    async def policy(db, user_id):
        return {}
    monkeypatch.setattr(module, "get_policy_map", policy, raising=False)


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
        return {"loaded": True, "policy": {}}

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
    revision = 3
    loads = 0

    async def cost_revision(db, user_id):
        return revision

    async def cost_context(db, user_id):
        nonlocal loads
        loads += 1
        return {"policy": {}}

    monkeypatch.setattr(module, "get_product_cost_revision", cost_revision)
    monkeypatch.setattr(module.profitability, "_load_cost_context", cost_context)

    kwargs = {
        "date_from": "2026-08-04",
        "date_to": "2026-08-04",
        "matched_orders": {},
        "campaign_spend": {},
    }
    await module.calculate_profitability_from_exact_matches(object(), "owner-1", **kwargs)
    revision = 4
    await module.calculate_profitability_from_exact_matches(object(), "owner-1", **kwargs)

    assert loads == 2


def test_source_policy_is_exact_and_read_only():
    assert module.SOURCE_MODE == "snapchat_salla_visible_matches_profitability_v2"
    assert module.CACHE_TTL_SECONDS == 0


@pytest.fixture
def calculation(monkeypatch):
    module._CACHE.clear()
    loads = []

    async def cost_revision(db, user_id):
        return 7

    async def cost_context(db, user_id):
        loads.append(user_id)
        return {"policy": {}}

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
async def test_unchanged_reordered_inputs_recompute_without_sharing_mutable_results(calculation):
    kwargs, loads = calculation
    first, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    first[("account-1", "campaign-1")]["sales_sar"] = 999
    kwargs["matched_orders"][("account-1", "campaign-1")].reverse()
    second, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert second[("account-1", "campaign-1")]["sales_sar"] == 150
    assert loads == ["owner", "owner"]


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


@pytest.mark.asyncio
async def test_bson_decimal_and_text_cannot_collide_in_cache(calculation):
    kwargs, loads = calculation
    orders = kwargs["matched_orders"][("account-1", "campaign-1")]
    orders[0]["metadata"] = Decimal128("100")
    await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    orders[0]["metadata"] = "100"
    await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert loads == ["owner", "owner"]


@pytest.mark.asyncio
async def test_supported_object_id_and_datetime_recompute_without_cache(calculation):
    kwargs, loads = calculation
    orders = kwargs["matched_orders"][("account-1", "campaign-1")]
    orders[0]["_id"] = ObjectId()
    orders[0]["updated_at"] = datetime(2026, 10, 27, tzinfo=timezone.utc)
    await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert loads == ["owner", "owner"]


@pytest.fixture
def policy_calculation(calculation, monkeypatch):
    kwargs, loads = calculation
    state = {"policy": {"custom": "confirmed"}, "on_load": None}

    async def policy(db, user_id):
        return deepcopy(state["policy"])

    async def context(db, user_id):
        loads.append(user_id)
        result = {"policy": deepcopy(state["policy"])}
        if state["on_load"]:
            state["on_load"]()
        return result

    original = module.profitability._order_cost_and_products
    def order_cost(order, cost_context):
        result = original(order, cost_context)
        result["product_cost_sar"] = effective_product_cost(
            {**order, "total_product_cost": result["product_cost_sar"]}, cost_context["policy"])
        return result

    monkeypatch.setattr(module, "get_policy_map", policy, raising=False)
    monkeypatch.setattr(module.profitability, "_load_cost_context", context)
    monkeypatch.setattr(module.profitability, "_order_cost_and_products", order_cost)
    for order in kwargs["matched_orders"][("account-1", "campaign-1")]:
        order["order_status"] = "custom"
    return kwargs, loads, state


@pytest.mark.asyncio
async def test_policy_update_and_reset_invalidate_unchanged_cohort(policy_calculation):
    kwargs, loads, state = policy_calculation
    first, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    state["policy"] = {"custom": "cancelled"}
    second, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    state["policy"] = {}
    third, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert [rows[("account-1", "campaign-1")]["product_cost_sar"] for rows in (first, second, third)] == [60, 0, 60]


@pytest.mark.asyncio
async def test_policy_change_during_context_load_retries_current_policy(policy_calculation):
    kwargs, loads, state = policy_calculation
    def change():
        state["policy"] = {"custom": "cancelled"}
        state["on_load"] = None
    state["on_load"] = change
    rows, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert rows[("account-1", "campaign-1")]["product_cost_sar"] == 0
    assert loads == ["owner", "owner"]


@pytest.mark.asyncio
async def test_continuously_changing_policy_never_caches_mixed_snapshot(policy_calculation):
    kwargs, loads, state = policy_calculation
    state["on_load"] = lambda: state["policy"].update(custom="cancelled" if state["policy"]["custom"] == "confirmed" else "confirmed")
    with pytest.raises(ValueError, match="snapshot_unproven"):
        await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert module._CACHE == {}


@pytest.mark.asyncio
async def test_policy_change_between_reads_cannot_return_stale_result(policy_calculation, monkeypatch):
    kwargs, loads, state = policy_calculation
    await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    reads = 0
    async def policy(db, user_id):
        nonlocal reads
        reads += 1
        if reads == 2:
            state["policy"] = {"custom": "cancelled"}
        return deepcopy(state["policy"])
    monkeypatch.setattr(module, "get_policy_map", policy)
    rows, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert rows[("account-1", "campaign-1")]["product_cost_sar"] == 0
    assert loads == ["owner", "owner", "owner"]


@pytest.mark.asyncio
async def test_cost_revision_change_during_context_load_retries(policy_calculation, monkeypatch):
    kwargs, loads, state = policy_calculation
    revision = 7
    async def cost_revision(db, user_id):
        return revision
    def change():
        nonlocal revision
        revision += 1
        state["on_load"] = None
    monkeypatch.setattr(module, "get_product_cost_revision", cost_revision)
    state["on_load"] = change
    rows, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert rows[("account-1", "campaign-1")]["product_cost_sar"] == 60
    assert loads == ["owner", "owner"]


@pytest.mark.asyncio
async def test_snapshot_inputs_detach_from_caller_before_await(calculation, monkeypatch):
    kwargs, loads = calculation
    async def policy(db, user_id):
        kwargs["matched_orders"][("account-1", "campaign-1")][0]["total_amount"] = 999
        kwargs["campaign_spend"][("account-1", "campaign-1")] = 999
        return {}
    monkeypatch.setattr(module, "get_policy_map", policy)
    rows, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    row = rows[("account-1", "campaign-1")]
    assert row["sales_sar"] == 150
    assert row["ad_spend_sar"] == 30


@pytest.mark.asyncio
async def test_policy_change_during_calculation_is_not_cached(policy_calculation, monkeypatch):
    kwargs, loads, state = policy_calculation
    original = module.profitability._order_cost_and_products
    changed = False
    def calculate(order, context):
        nonlocal changed
        result = original(order, context)
        if not changed:
            state["policy"] = {"custom": "cancelled"}
            changed = True
        return result
    monkeypatch.setattr(module.profitability, "_order_cost_and_products", calculate)
    rows, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert rows[("account-1", "campaign-1")]["product_cost_sar"] == 0
    assert loads == ["owner", "owner"]
    assert module._CACHE == {}


@pytest.mark.asyncio
async def test_catalog_identity_change_without_cost_revision_uses_current_resolution(monkeypatch):
    module._CACHE.clear()
    state = {"sku": "old"}
    async def revision(db, user_id):
        return 1
    async def context(db, user_id):
        products = [{"id": "p", "salla_product_id": "p", "sku": state["sku"],
                     "name": "item", "cost_price_from_salla": 50, "variants": []}]
        by_id, by_variant, by_sku = module.profitability._index_products(products)
        return {"products_by_id": by_id, "products_by_variant": by_variant,
                "products_by_sku": by_sku, "profile_map": {}, "option_map": {},
                "product_binding_map": {}, "resources": {}, "policy": {}}
    monkeypatch.setattr(module, "get_product_cost_revision", revision)
    monkeypatch.setattr(module.profitability, "_load_cost_context", context)
    kwargs = {"date_from": "2026-10-25", "date_to": "2026-10-25",
              "matched_orders": {("a", "c"): [{"total_amount": 150, "products": [{"sku": "old", "quantity": 1}]}]},
              "campaign_spend": {("a", "c"): 10}}
    first, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert first[("a", "c")]["product_cost_sar"] == 50
    state["sku"] = "new"
    second, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert second[("a", "c")]["product_cost_sar"] is None
    assert second[("a", "c")]["cost_status"] == "missing"


@pytest.mark.asyncio
async def test_component_cost_change_without_revision_recalculates_actual_cost(monkeypatch):
    module._CACHE.clear()
    state = {"unit_cost": 5}
    async def revision(db, user_id):
        return 1
    async def context(db, user_id):
        product = {"id": "p", "salla_product_id": "p", "sku": "old", "name": "item", "cost_price_from_salla": 50, "variants": []}
        by_id, by_variant, by_sku = module.profitability._index_products([product])
        return {"products_by_id": by_id, "products_by_variant": by_variant, "products_by_sku": by_sku,
                "profile_map": {}, "option_map": {}, "product_binding_map": {"p": [{"resource_id": "r", "quantity": 1}]},
                "resources": {"r": {"id": "r", "unit_cost": state["unit_cost"]}}, "policy": {}}
    monkeypatch.setattr(module, "get_product_cost_revision", revision)
    monkeypatch.setattr(module.profitability, "_load_cost_context", context)
    kwargs = {"date_from": "2026-10-25", "date_to": "2026-10-25",
              "matched_orders": {("a", "c"): [{"total_amount": 150, "products": [{"sku": "old", "quantity": 1}]}]},
              "campaign_spend": {("a", "c"): 10}}
    first, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    state["unit_cost"] = 15
    second, _ = await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert first[("a", "c")]["product_cost_sar"] == 55
    assert second[("a", "c")]["product_cost_sar"] == 65
    assert module._CACHE == {}


@pytest.mark.asyncio
async def test_uncopyable_cohort_fails_with_controlled_snapshot_error(calculation):
    kwargs, loads = calculation
    class Unsafe:
        def __deepcopy__(self, memo):
            raise TypeError("private metadata must not escape")
    kwargs["matched_orders"][("account-1", "campaign-1")][0]["metadata"] = Unsafe()
    with pytest.raises(ValueError, match="^campaign_profitability_snapshot_unproven$"):
        await module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)
    assert module._CACHE == {}
    assert loads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("producer", ["base", "salla"])
@pytest.mark.parametrize("empty", [False, True])
async def test_report_uses_same_matched_snapshot_without_second_order_query(calculation, monkeypatch, producer, empty):
    import dashboard_v2_routes
    from integrations_control_center import snapchat_salla_campaign_outcomes as salla
    kwargs, loads = calculation
    first_order = {"id": "o1", "utm_campaign": "c", "source": "snapchat", "total_amount": 100,
                   "products": [{"quantity": 1, "cost": 40}], "order_date": "2026-10-25"}
    state = {"orders": [] if empty else [first_order], "reads": 0}
    async def filtered(*args, **kwargs):
        state["reads"] += 1
        return deepcopy(state["orders"])
    async def no_second_query(*args, **kwargs):
        raise AssertionError("a second attribution query is not the same snapshot")
    original_outcomes = module.routes._salla_outcomes if producer == "base" else salla.salla_campaign_outcomes
    monkeypatch.setattr(module.routes, "_salla_outcomes", original_outcomes)
    async def report(db, user_id, **kwargs):
        campaigns, _, _, _ = await module.routes._salla_outcomes(
            db, user_id, date_from="2026-10-25", date_to="2026-10-25",
            identities=[{"account_id": "a", "campaign_id": "c", "campaign_name": "Campaign"}])
        # A later DB query would return a different cohort, but this report
        # must retain the exact row used above, even with equal order counts.
        state["orders"] = [{**first_order, "id": "o2", "total_amount": 999}]
        return {"date_from": "2026-10-25", "date_to": "2026-10-25",
                "campaigns": [{"account_id": "a", "campaign_id": "c", "spend_sar": 10,
                               "salla_results": campaigns.get(("a", "c"), {"orders": 0, "sales_sar": 0}),
                               "profitability": {"sales_sar": 999, "product_cost_sar": 500}}]}
    monkeypatch.setattr(dashboard_v2_routes, "_filtered_orders", filtered)
    monkeypatch.setattr(module, "_filtered_orders", no_second_query)
    monkeypatch.setattr(module.routes, "build_snapchat_result_source_report", report)
    module.install_exact_salla_profitability_reuse()
    result = await module.routes.build_snapchat_result_source_report(object(), "owner")
    campaign = result["campaigns"][0]
    assert state["reads"] == 1
    assert campaign["salla_results"]["sales_sar"] == campaign["profitability"]["sales_sar"] == (0 if empty else 100)
    assert campaign["profitability"]["product_cost_sar"] == (0 if empty else 40)
    assert result["source"]["campaign_profitability"]["cache_reuse_enabled"] is False


@pytest.mark.asyncio
async def test_missing_report_snapshot_cannot_retain_unproven_older_profit(monkeypatch):
    async def no_outcomes(*args, **kwargs):
        return {}, {}, {}, {}
    async def report(*args, **kwargs):
        return {"campaigns": [{"account_id": "a", "campaign_id": "c", "profitability": {"product_cost_sar": 50}}]}
    monkeypatch.setattr(module.routes, "_salla_outcomes", no_outcomes)
    monkeypatch.setattr(module.routes, "build_snapchat_result_source_report", report)
    module.install_exact_salla_profitability_reuse()
    with pytest.raises(ValueError, match="^campaign_profitability_snapshot_unproven$"):
        await module.routes.build_snapchat_result_source_report(object(), "owner")


@pytest.mark.asyncio
async def test_repeated_runtime_composition_and_overlapping_reports_keep_context_isolated(calculation, monkeypatch):
    import dashboard_v2_routes
    from integrations_control_center import snapchat_salla_campaign_outcomes as salla
    kwargs, loads = calculation
    legacy_calls = []
    async def legacy_profitability(db, user_id, **kwargs):
        legacy_calls.append(user_id)
        raise AssertionError("the direct legacy profitability layer must not run twice")
    async def filtered(db, user_id, **kwargs):
        await asyncio.sleep(0)
        return [{"id": user_id, "utm_campaign": "c", "source": "snapchat",
                 "total_amount": 100 if user_id == "first" else 200,
                 "products": [{"quantity": 1, "cost": 40}], "order_date": "2026-10-25"}]
    async def report(db, user_id, **kwargs):
        campaigns, _, _, coverage = await module.routes._salla_outcomes(
            db, user_id, date_from="2026-10-25", date_to="2026-10-25",
            identities=[{"account_id": "a", "campaign_id": "c", "campaign_name": "Campaign"}])
        await asyncio.sleep(0)
        return {"date_from": "2026-10-25", "date_to": "2026-10-25", "result_source": "salla",
                "source": {"salla_attribution": coverage}, "totals": {"spend_sar": 10},
                "campaigns": [{"account_id": "a", "campaign_id": "c", "spend_sar": 10,
                               "salla_results": campaigns[("a", "c")]}]}
    monkeypatch.setattr(dashboard_v2_routes, "_filtered_orders", filtered)
    monkeypatch.setattr(module.routes, "_salla_outcomes", salla.salla_campaign_outcomes)
    monkeypatch.setattr(module.routes, "build_snapchat_result_source_report", report)
    monkeypatch.setattr(module.routes, "_selected_metrics", module.routes._selected_metrics)
    monkeypatch.setattr(module.profitability, "build_campaign_profitability", legacy_profitability)
    salla.install_snapchat_salla_campaign_outcomes()
    module.profitability.install_snapchat_campaign_profitability()
    module.install_exact_salla_profitability_reuse()
    installed = module.routes.build_snapchat_result_source_report
    first = await installed(object(), "first", result_source="salla")
    assert first["campaigns"][0]["profitability"]["sales_sar"] == 100
    for _ in range(3):
        salla.install_snapchat_salla_campaign_outcomes()
        module.profitability.install_snapchat_campaign_profitability()
        module.install_exact_salla_profitability_reuse()
        assert module.routes.build_snapchat_result_source_report is installed
    results = await asyncio.gather(
        installed(object(), "first", result_source="salla"),
        installed(object(), "second", result_source="salla"),
    )
    assert [result["campaigns"][0]["profitability"]["sales_sar"] for result in results] == [100, 200]
    assert module._MATCHED_ORDERS.get() is None
    assert legacy_calls == []
    assert first["source"]["campaign_profitability"]["legacy_profitability_bypassed"] is True
    assert first["policy"] == {
        "campaign_profitability_read_only": True,
        "product_ad_spend_is_allocated_not_provider_fact": True,
        "profitability_provider_write_reached": False,
        "profitability_accounting_write_reached": False,
        "profitability_qoyod_write_reached": False,
    }
    assert first["source"]["campaign_profitability"]["excluded_allocations"] == [
        "payment_gateway_fees", "bnpl_fees", "merchant_shipping_cost", "operating_expenses"]


@pytest.mark.asyncio
async def test_large_calculations_do_not_block_loop_or_exceed_admission_on_cancel(calculation, monkeypatch):
    kwargs, loads = calculation
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    all_copied = asyncio.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = peak = 0
    original = module._calculate_current_cohort
    limiter = module._work_limiter()
    assert limiter._value == 2
    loop_thread = threading.get_ident()
    copies = 0
    original_copy = module._copy_inputs
    def snapshot_before_wait(*args):
        nonlocal copies
        assert threading.get_ident() == loop_thread
        result = original_copy(*args)
        copies += 1
        if copies == 3:
            all_copied.set()
        return result
    def blocking_calculation(*args):
        nonlocal active, peak
        assert threading.get_ident() != loop_thread
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                loop.call_soon_threadsafe(started.set)
        try:
            if not release.wait(timeout=10):
                raise TimeoutError("test worker was not released")
            return original(*args)
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(module, "_calculate_current_cohort", blocking_calculation)
    monkeypatch.setattr(module, "_copy_inputs", snapshot_before_wait)
    tasks = [asyncio.create_task(module.calculate_profitability_from_exact_matches(object(), "owner", **kwargs)) for _ in range(3)]
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.wait_for(all_copied.wait(), timeout=5)
        assert len(loads) == 2
        assert limiter._value == 0
        # The third request is waiting for admission. Its exported mutable
        # arguments were already captured and cannot be silently replaced.
        kwargs["matched_orders"][("account-1", "campaign-1")][0]["total_amount"] = 999
        kwargs["campaign_spend"][("account-1", "campaign-1")] = 999
        # A loop heartbeat runs while both calculation threads are paused.
        assert await asyncio.wait_for(asyncio.sleep(0, result="responsive"), timeout=1) == "responsive"
        tasks[0].cancel()
        await asyncio.sleep(0)
        tasks[0].cancel()
        await asyncio.sleep(0)
        assert not tasks[0].done()
        assert len(loads) == 2  # cancelled work still owns admission
        assert limiter._value == 0
    finally:
        release.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)
    assert isinstance(results[0], asyncio.CancelledError)
    assert all(not isinstance(result, BaseException) for result in results[1:])
    for result in results[1:]:
        assert result[0][("account-1", "campaign-1")]["sales_sar"] == 150
        assert result[0][("account-1", "campaign-1")]["ad_spend_sar"] == 30
    assert peak == module.MAX_CALCULATION_WORKERS == 2
    assert len(loads) == 3
    assert limiter._value == 2


@pytest.mark.asyncio
async def test_private_owned_capture_copies_outside_loop(calculation, monkeypatch):
    kwargs, loads = calculation
    loop_thread = threading.get_ident()
    original = module._copy_inputs
    copies = []
    def copy(*args):
        copies.append(threading.get_ident())
        return original(*args)
    monkeypatch.setattr(module, "_copy_inputs", copy)
    result = await module._calculate_owned_profitability(object(), "owner", **kwargs)
    assert result[0][("account-1", "campaign-1")]["sales_sar"] == 150
    assert copies and all(thread != loop_thread for thread in copies)


@pytest.fixture
def runtime_report(calculation, monkeypatch):
    import dashboard_v2_routes
    from integrations_control_center import snapchat_salla_campaign_outcomes as salla
    calls = []
    async def filtered(db, user_id, **kwargs):
        return [{"id": user_id, "utm_campaign": "c", "source": "snapchat",
                 "total_amount": 100 if user_id == "first" else 200,
                 "products": [{"quantity": 1, "cost": 40}], "order_date": "2026-10-25"}]
    async def report(db, user_id, **kwargs):
        campaigns, _, _, coverage = await module.routes._salla_outcomes(
            db, user_id, date_from="2026-10-25", date_to="2026-10-25",
            identities=[{"account_id": "a", "campaign_id": "c", "campaign_name": "Campaign"}])
        return {"date_from": "2026-10-25", "date_to": "2026-10-25", "result_source": "salla",
                "source": {"salla_attribution": coverage}, "totals": {"spend_sar": 10},
                "campaigns": [{"account_id": "a", "campaign_id": "c", "spend_sar": 10,
                               "salla_results": campaigns[("a", "c")]}]}
    async def legacy(db, user_id, **kwargs):
        calls.append(user_id)
        return {"by_campaign": {}, "totals": {}, "coverage": {}}
    monkeypatch.setattr(dashboard_v2_routes, "_filtered_orders", filtered)
    monkeypatch.setattr(module.routes, "_salla_outcomes", salla.salla_campaign_outcomes)
    monkeypatch.setattr(module.routes, "build_snapchat_result_source_report", report)
    monkeypatch.setattr(module.routes, "_selected_metrics", module.routes._selected_metrics)
    monkeypatch.setattr(module.profitability, "build_campaign_profitability", legacy)
    def install(*, unknown_outer=False):
        salla.install_snapchat_salla_campaign_outcomes()
        module.profitability.install_snapchat_campaign_profitability()
        if unknown_outer:
            previous = module.routes.build_snapchat_result_source_report
            @wraps(previous)
            async def middleware(*args, **kwargs):
                result = await previous(*args, **kwargs)
                result["middleware_contract_preserved"] = True
                return result
            module.routes.build_snapchat_result_source_report = middleware
        module.install_exact_salla_profitability_reuse()
        return module.routes.build_snapchat_result_source_report
    return install, calls


@pytest.mark.asyncio
async def test_unknown_outer_contract_is_never_unwrapped(runtime_report):
    install, legacy_calls = runtime_report
    report = install(unknown_outer=True)
    result = await report(object(), "first", result_source="salla")
    assert result["middleware_contract_preserved"] is True
    assert result["campaigns"][0]["profitability"]["product_cost_sar"] == 40
    assert result["source"]["campaign_profitability"]["legacy_profitability_bypassed"] is False
    assert legacy_calls == ["first"]


@pytest.mark.asyncio
async def test_report_calculation_error_restores_capture_context(runtime_report, monkeypatch):
    install, legacy_calls = runtime_report
    report = install()
    original = module._calculate_current_cohort
    def fail(*args):
        raise ValueError("test calculation failed")
    monkeypatch.setattr(module, "_calculate_current_cohort", fail)
    with pytest.raises(ValueError, match="test calculation failed"):
        await report(object(), "first", result_source="salla")
    assert module._MATCHED_ORDERS.get() is None
    monkeypatch.setattr(module, "_calculate_current_cohort", original)
    result = await report(object(), "second", result_source="salla")
    assert result["campaigns"][0]["profitability"]["sales_sar"] == 200
    assert legacy_calls == []


@pytest.mark.asyncio
async def test_report_cancellation_retains_worker_slot_and_restores_capture(runtime_report, monkeypatch):
    install, legacy_calls = runtime_report
    report = install()
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    captured_after = []
    original = module._calculate_current_cohort
    limiter = module._work_limiter()
    assert limiter._value == 2
    def wait(*args):
        loop.call_soon_threadsafe(started.set)
        if not release.wait(timeout=10):
            raise TimeoutError("test worker not released")
        return original(*args)
    monkeypatch.setattr(module, "_calculate_current_cohort", wait)
    async def request():
        try:
            return await report(object(), "first", result_source="salla")
        finally:
            captured_after.append(module._MATCHED_ORDERS.get())
    task = asyncio.create_task(request())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert await asyncio.sleep(0, result="responsive") == "responsive"
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert limiter._value == 1
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert captured_after == [None]
    assert module._MATCHED_ORDERS.get() is None
    assert limiter._value == 2
    monkeypatch.setattr(module, "_calculate_current_cohort", original)
    result = await report(object(), "second", result_source="salla")
    assert result["campaigns"][0]["profitability"]["sales_sar"] == 200
    assert legacy_calls == []


@pytest.mark.asyncio
async def test_salla_aggregation_yields_and_cancellation_discards_partial_result(monkeypatch):
    import dashboard_v2_routes
    from integrations_control_center import snapchat_salla_campaign_outcomes as salla
    orders = [{"utm_campaign": "c", "source": "snapchat", "total_amount": 10,
               "order_date": "2026-10-25"} for _ in range(20000)]
    async def filtered(*args, **kwargs):
        return orders
    monkeypatch.setattr(dashboard_v2_routes, "_filtered_orders", filtered)
    kwargs = dict(date_from="2026-10-25", date_to="2026-10-25",
                  identities=[{"account_id": "a", "campaign_id": "c"}])
    capture, ticks = {}, []
    stop = asyncio.Event()
    async def heartbeat():
        while not stop.is_set():
            ticks.append(len(capture.get(("a", "c"), [])))
            await asyncio.sleep(0)
    beat = asyncio.create_task(heartbeat())
    try:
        result = await salla.salla_campaign_outcomes(object(), "u", matched_orders_out=capture, **kwargs)
    finally:
        stop.set()
        await beat
    assert result[0][("a", "c")] == {"orders": 20000, "sales_sar": 200000.0}
    assert len(capture[("a", "c")]) == 20000
    assert len(ticks) >= 150
    assert max(b - a for a, b in zip(ticks, ticks[1:])) <= 128
    partial = {}
    task = asyncio.create_task(salla.salla_campaign_outcomes(object(), "u", matched_orders_out=partial, **kwargs))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(partial.get(("a", "c"), [])) <= 128
    result = await salla.salla_campaign_outcomes(object(), "u", **kwargs)
    assert result[0][("a", "c")]["orders"] == 20000
