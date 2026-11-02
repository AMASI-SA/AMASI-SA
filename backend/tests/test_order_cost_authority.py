"""Independent business oracles for historical, option-aware SAR order costs."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from mongomock_motor import AsyncMongoMockClient

from order_cost_authority import (
    CORRECTIONS, LIFECYCLES, SNAPSHOTS, calculate_catalog_line_cost,
    capture_reporting_costs, line_fingerprint, load_order_cost_authority,
    resolve_order_line_cost,
)
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS
from stock_component_consumption_service import PLANS, UNITS


def line(quantity=4, rose=True, embroidery=False, key="line"):
    return {"order_item_id": key, "source_item_id": key, "product_id": "shawl", "parent_product_id": None,
        "name": "Fixture shawl", "sku": "FIXTURE-SHAWL", "variant_id": None, "quantity": quantity,
        "options_raw": [{"option_id": "rose", "value_id": "yes" if rose else "no"},
                        {"option_id": "embroidery", "value_id": "yes" if embroidery else "no"}],
        "options_normalized": {}}


def catalog(item, base=15):
    return calculate_catalog_line_cost(item, product={"cost_price_from_salla": 70}, profile={"base_cost": base},
        product_bindings=[], option_bindings=[
            {"id": "rose-binding", "option_id": "rose", "value_id": "yes", "mode": "resource", "resource_id": "rose", "quantity": 1},
            {"id": "embroidery-binding", "option_id": "embroidery", "value_id": "yes", "mode": "direct", "direct_amount": 10},
        ], resources={"rose": {"unit_cost": 40}})


@pytest.mark.parametrize("item,expected", [(line(), 220), (line(rose=False), 60),
    (line(3), 165), (line(2, False), 30), (line(4, True, True), 260)])
def test_base_components_and_selected_options(item, expected):
    result = catalog(item)
    assert result["line_total"] == expected
    assert result["base_complete"] is True
    assert result["currency"] == "SAR"


def test_mixed_lines_and_quantities_are_not_cartesian_multiplied():
    assert sum(catalog(item)["line_total"] for item in [line(2), line(2, False)]) == 140
    assert sum(catalog(item)["line_total"] for item in [line(3), line(2, False)]) == 195
    assert sum(catalog(item)["line_total"] for item in [line(2), line(2, True, True)]) == 240


def test_duplicate_binding_identity_counts_once_but_distinct_rows_remain():
    binding = {"id": "r1", "resource_id": "rose", "quantity": 2}
    cost = calculate_catalog_line_cost(line(3), product={}, profile={"base_cost": 15},
        product_bindings=[binding, deepcopy(binding)], option_bindings=[], resources={"rose": {"unit_cost": 40}})
    assert cost["line_total"] == 285
    cost = calculate_catalog_line_cost(line(1), product={}, profile={"base_cost": 15},
        product_bindings=[{**binding, "quantity": 1}, {**binding, "id": "r2", "quantity": 1}],
        option_bindings=[], resources={"rose": {"unit_cost": 40}})
    assert cost["line_total"] == 95


def test_missing_resource_is_incomplete_explicit_zero_is_valid():
    for value, complete in [(None, False), (0, True), (float("nan"), False)]:
        result = calculate_catalog_line_cost(line(1), product={}, profile={"base_cost": 15},
            product_bindings=[{"id": "r", "resource_id": "r"}], option_bindings=[], resources={"r": {"unit_cost": value}})
        assert result["calculation_cost_available"] is complete
        assert result["line_total"] == 15


def test_sale_currency_never_converts_sar_cost_and_explicit_fx_applies_once():
    for currency in ["SAR", "QAR", "AED", "KWD"]:
        assert catalog({**line(), "currency": currency})["line_total"] == 220
    cost = calculate_catalog_line_cost(line(2), product={}, profile={"base_cost": 4, "cost_currency": "USD", "cost_fx_rate_to_sar": "3.75"},
        product_bindings=[{"id": "r", "resource_id": "r"}], option_bindings=[], resources={"r": {"unit_cost": 40}})
    assert cost["line_total"] == 110
    stored = {"lines": {("order", "line"): {"quantity": 2, "cost": cost, "line_fingerprint": line_fingerprint(line(2))}}}
    assert resolve_order_line_cost(line(2), order_number="order", catalog_cost=catalog(line(2), 999), authority=stored)["line_total"] == 110


@pytest.mark.asyncio
async def test_replaying_snapshot_preserves_first_recorded_cost():
    from order_option_cost_snapshot_routes import calculate_order_cost_snapshot
    db = AsyncMongoMockClient()["cost_replay"]
    await db[PRODUCTS].insert_one({"user_id": "owner", "salla_product_id": "shawl", "cost_price_from_salla": 70})
    await db[COST_PROFILES].insert_one({"user_id": "owner", "salla_product_id": "shawl", "base_cost": 15})
    order = SimpleNamespace(order_number="order", order_id="order-id", items=[SimpleNamespace(**line())])
    first = await calculate_order_cost_snapshot(db, user_id="owner", order=order)
    assert first["total_cost"] == 60
    await db[COST_PROFILES].update_one({"user_id": "owner"}, {"$set": {"base_cost": 70}})
    second = await calculate_order_cost_snapshot(db, user_id="owner", order=order)
    assert second["total_cost"] == 60
    assert await db[SNAPSHOTS].count_documents({}) == 1
    assert first["items"][0]["id"] == second["items"][0]["id"]


@pytest.mark.asyncio
async def test_frozen_bundle_overrides_live_parent_and_quantity_only_once():
    db = AsyncMongoMockClient()["cost_bundle"]
    item = line()
    snapshot = {"product_id": "shawl", "revision": 1, "estimated_cost": "55",
        "selected_options": [{"option_id": "rose", "value_id": "yes"}, {"option_id": "embroidery", "value_id": "no"}],
        "leaves": [{"kind": "product", "current_product_reference": True, "quantity": "1", "unit_cost": "15"},
                   {"kind": "product", "quantity": "1", "unit_cost": "40"}]}
    await db[LIFECYCLES].insert_one({"user_id": "owner", "order_number": "order", "bundle_intake_snapshots": {"line": snapshot}})
    context = await load_order_cost_authority(db, "owner", [{"order_number": "order", "products": [item]}])
    result = resolve_order_line_cost(item, order_number="order", catalog_cost=catalog(item, 999), authority=context)
    assert result["line_total"] == 220
    assert result["quantity"] == 4
    assert result["historical_cost_verified"] is True
    assert result["cost_authority"] == "bundle_snapshot"
    assert await db[SNAPSHOTS].count_documents({}) == 0
    assert await db[PLANS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_actual_complete_units_replace_expected_and_missing_units_fail_closed():
    db = AsyncMongoMockClient()["cost_actual"]
    await db[PLANS].insert_one({"_id": "plan", "user_id": "owner", "order_id": "order", "bundle_snapshots": {"line": {"product_id": "shawl", "estimated_cost": "55"}}})
    for index in range(1, 5):
        await db[UNITS].insert_one({"user_id": "owner", "plan_id": "plan", "order_line_id": "line", "unit_index": index,
            "state": "consumed", "bundle_leaves": [{"actual_cost": "15"}, {"actual_cost": "40"}]})
    orders = [{"order_number": "order", "products": [line()]}]
    context = await load_order_cost_authority(db, "owner", orders)
    result = resolve_order_line_cost(line(), order_number="order", catalog_cost=catalog(line(), 999), authority=context)
    assert result["line_total"] == 220 and result["cost_stage"] == "actual"
    await db[UNITS].delete_one({"unit_index": 4})
    context = await load_order_cost_authority(db, "owner", orders)
    assert resolve_order_line_cost(line(), order_number="order", catalog_cost=catalog(line()), authority=context)["calculation_cost_available"] is False


@pytest.mark.asyncio
async def test_new_ordinary_intake_stays_frozen_and_changed_line_is_unresolved():
    db = AsyncMongoMockClient()["cost_intake"]
    await db[PRODUCTS].insert_one({"user_id": "owner", "salla_product_id": "shawl"})
    await db[COST_PROFILES].insert_one({"user_id": "owner", "salla_product_id": "shawl", "base_cost": 15})
    captured = await capture_reporting_costs(db, "owner", [line()], {})
    await db[COST_PROFILES].update_one({"user_id": "owner"}, {"$set": {"base_cost": 70}})
    assert await capture_reporting_costs(db, "owner", [line()], captured) == captured
    await db[LIFECYCLES].insert_one({"user_id": "owner", "order_number": "order", "reporting_cost_snapshots": captured})
    context = await load_order_cost_authority(db, "owner", [{"order_number": "order", "products": [line()]}])
    result = resolve_order_line_cost(line(), order_number="order", catalog_cost=catalog(line(), 70), authority=context)
    assert result["line_total"] == 60
    changed = resolve_order_line_cost(line(3), order_number="order", catalog_cost=catalog(line(3)), authority=context)
    assert changed["calculation_cost_available"] is False


@pytest.mark.asyncio
async def test_owner_isolation_and_unmatched_composite_never_use_current_cost():
    from bundle_catalog_v2 import BUNDLES
    db = AsyncMongoMockClient()["cost_scope"]
    await db[BUNDLES].insert_one({"user_id": "owner", "product_id": "shawl", "product_type": "composite"})
    await db[SNAPSHOTS].insert_one({"user_id": "other", "order_number": "order", "order_item_id": "line", "quantity": 4, "line_cost": 220})
    context = await load_order_cost_authority(db, "owner", [{"order_number": "order", "products": [line()]}])
    result = resolve_order_line_cost(line(), order_number="order", catalog_cost=catalog(line()), authority=context)
    assert result["calculation_cost_available"] is False
    assert result["line_total"] == 0


@pytest.mark.asyncio
async def test_empty_cohort_requires_no_database():
    assert not (await load_order_cost_authority(object(), "owner", []))["lines"]
