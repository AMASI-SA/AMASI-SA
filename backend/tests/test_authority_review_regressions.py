"""Independent regressions for stale evidence and real provider option shapes.

The business oracle is deliberately independent of the current implementation:
changed economic identity cannot reuse a frozen cost, and an actual cost must
cover each leaf of the frozen recipe exactly once.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

from bundle_fulfillment_v2 import leaf_key
from order_cost_authority import (
    SNAPSHOTS, calculate_catalog_line_cost, line_fingerprint,
    load_order_cost_authority, resolve_order_line_cost,
)
from order_engine.mapper import _map_item
from order_option_cost_snapshot_routes import calculate_order_cost_snapshot
from salla_orders_v3.compatibility import _legacy_product
from salla_orders_v3.normalizer import normalize_order_item
from stock_component_consumption_service import PLANS, UNITS, selection_tokens


def _line(**changes):
    return {
        "order_item_id": "line", "source_item_id": "line", "product_id": "shawl",
        "quantity": 1, "options_raw": [], "options_normalized": {}, **changes,
    }


def _catalog(item, bindings=None):
    return calculate_catalog_line_cost(
        item, product={}, profile={"base_cost": 15}, product_bindings=[],
        option_bindings=bindings or [], resources={},
    )


async def _resolve(db, item):
    authority = await load_order_cost_authority(
        db, "owner", [{"order_number": "order", "products": [item]}],
    )
    return resolve_order_line_cost(
        item, order_number="order", product_id=item["product_id"],
        catalog_cost=_catalog(item), authority=authority,
    )


def _snapshot(item, *, leaves=None, estimated="15", fingerprint=True):
    snapshot = {
        "product_id": "shawl", "sale_quantity": item["quantity"],
        "selected_options": [], "estimated_cost": estimated,
        "leaves": deepcopy(leaves or []), "revision": 1,
    }
    if fingerprint:
        snapshot["line_fingerprint"] = line_fingerprint(item)
    return snapshot


async def _plan(db, item, snapshot, *, keep_original_line=True):
    plan = {
        "_id": "plan", "user_id": "owner", "order_id": "order",
        "bundle_snapshots": {"line": snapshot},
    }
    if keep_original_line:
        plan["lines"] = [{
            **deepcopy(item), "order_line_id": "line",
            "selected_context": selection_tokens(item),
        }]
    await db[PLANS].insert_one(plan)


async def _unit(db, *, index=0, leaves=None, state="reserved"):
    await db[UNITS].insert_one({
        "user_id": "owner", "plan_id": "plan", "order_line_id": "line",
        "unit_index": index, "state": state, "bundle_leaves": deepcopy(leaves or []),
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"product_id": "another-product"},
    {"options_raw": [{"option_id": "rose", "value_id": "yes"}]},
    {"quantity": 2},
    {"variant_id": "another-variant"},
], ids=["product", "option", "quantity", "variant"])
async def test_plan_proof_is_not_rebound_to_a_changed_current_line(changes):
    db = AsyncMongoMockClient().plan_identity_review
    original = _line()
    await _plan(db, original, _snapshot(original))
    await _unit(db)

    result = await _resolve(db, {**original, **changes})

    assert result["calculation_cost_available"] is False
    assert result["historical_cost_verified"] is False


@pytest.mark.asyncio
async def test_legacy_plan_empty_selected_options_do_not_mean_any_options():
    db = AsyncMongoMockClient().legacy_plan_options_review
    original = _line()
    await _plan(db, original, _snapshot(original, fingerprint=False), keep_original_line=False)
    await _unit(db)
    changed = {**original, "options_raw": [{"option_id": "rose", "value_id": "yes"}]}

    result = await _resolve(db, changed)

    assert result["calculation_cost_available"] is False
    assert "bundle_selected_options_mismatch" in result["missing_cost_reasons"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"quantity": 3},
    {"product_id": "another-product"},
    {"options_raw": [{"option_id": "rose", "value_id": "yes"}]},
], ids=["quantity", "product", "empty-options-to-rose"])
async def test_snapshot_post_rejects_stale_legacy_proof_without_rewriting_it(changes):
    db = AsyncMongoMockClient().legacy_snapshot_post_review
    original = _line(quantity=4)
    saved = {
        "id": "original-proof", "user_id": "owner", "order_number": "order",
        "order_item_id": "line", "salla_product_id": "shawl", "quantity": 4,
        "base_unit_cost": 15, "line_cost": 60, "currency": "SAR",
        "cost_complete": True, "selected_options": [],
    }
    await db[SNAPSHOTS].insert_one(deepcopy(saved))
    order = SimpleNamespace(
        order_number="order", order_id="provider-order", items=[{**original, **changes}],
    )

    with pytest.raises(HTTPException) as error:
        await calculate_order_cost_snapshot(db, user_id="owner", order=order)

    assert error.value.status_code == 409
    assert await db[SNAPSHOTS].find_one({"id": "original-proof"}, {"_id": 0}) == saved
    assert await db[SNAPSHOTS].count_documents({}) == 1


@pytest.mark.asyncio
async def test_removed_historical_lines_do_not_overflow_current_order_scope():
    db = AsyncMongoMockClient().removed_lines_review
    for key in ["line", *[f"removed-{i}" for i in range(5)]]:
        await db[SNAPSHOTS].insert_one({
            "user_id": "owner", "order_number": "order", "order_item_id": key,
            "salla_product_id": "shawl", "quantity": 1, "base_unit_cost": 15,
            "line_cost": 15, "currency": "SAR", "cost_complete": True,
        })

    result = await _resolve(db, _line())

    assert result["calculation_cost_available"] is True
    assert result["line_total"] == 15
    assert await db[SNAPSHOTS].count_documents({}) == 6


@pytest.mark.asyncio
async def test_reduced_quantity_with_old_units_is_unresolved_without_read_overflow():
    db = AsyncMongoMockClient().reduced_units_review
    original = _line(quantity=5)
    await _plan(db, original, _snapshot(original))
    for index in range(5):
        await _unit(db, index=index)

    result = await _resolve(db, _line(quantity=1))

    assert result["calculation_cost_available"] is False
    assert await db[UNITS].count_documents({}) == 5


def _recipe_leaves():
    return [{
        "path": ["shawl", "base" if product == "shawl" else "rose"],
        "kind": "product", "product_id": product, "quantity": "1",
        "unit_cost": amount, "sourcing_mode": "stock", "required": True,
    } for product, amount in [("shawl", "15"), ("bouquet", "40")]]


def _consumed_leaves(leaves):
    return [{
        **deepcopy(leaf), "leaf_id": leaf_key(leaf), "state": "consumed",
        "consumed_quantity": "1", "actual_cost": leaf["unit_cost"],
    } for leaf in leaves]


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [
    "missing-rose", "duplicate-base", "wrong-product", "partial-required-quantity",
    "required-leaf-not-consumed",
])
async def test_consumed_unit_requires_each_frozen_leaf_exactly_once(damage):
    db = AsyncMongoMockClient().actual_leaf_coverage_review
    item = _line()
    recipe = _recipe_leaves()
    await _plan(db, item, _snapshot(item, leaves=recipe, estimated="55"))
    actual = _consumed_leaves(recipe)
    if damage == "missing-rose":
        actual = actual[:1]
    elif damage == "duplicate-base":
        actual = [actual[0], deepcopy(actual[0])]
    elif damage == "wrong-product":
        actual[1]["product_id"] = "different-component"
    elif damage == "partial-required-quantity":
        actual[1].update(quantity="0.5", consumed_quantity="0.5", actual_cost="20")
    elif damage == "required-leaf-not-consumed":
        actual[1].update(state="reserved", consumed_quantity="0")
    await _unit(db, leaves=actual, state="consumed")

    result = await _resolve(db, item)

    # Incomplete physical evidence cannot certify actual cost. A reader may
    # retain the frozen expected55, or return explicitly unavailable.
    if result["calculation_cost_available"]:
        assert result["cost_stage"] == "expected"
        assert result["line_total"] == 55
    else:
        assert result["historical_cost_verified"] is False


@pytest.mark.asyncio
async def test_complete_actual_leaves_count_split_source_children_once():
    db = AsyncMongoMockClient().actual_split_source_review
    item = _line()
    recipe = _recipe_leaves()
    await _plan(db, item, _snapshot(item, leaves=recipe, estimated="55"))
    actual = _consumed_leaves(recipe)
    actual[0]["sourcing_segments"] = [
        {"leaf_id": "base-stock-part", "actual_cost": "7", "state": "consumed"},
        {"leaf_id": "base-supplier-part", "actual_cost": "8", "state": "consumed"},
    ]
    await _unit(db, leaves=actual, state="consumed")

    result = await _resolve(db, item)

    assert result["calculation_cost_available"] is True
    assert result["cost_stage"] == "actual"
    assert result["line_total"] == 55


def _representations(extra):
    raw = {
        "id": "111", "name": "Shawl", "quantity": 4,
        "product": {"id": "shawl", "name": "Shawl", "sku": "AMS13281"},
        **deepcopy(extra),
    }
    canonical = _map_item(order_number="123", item=raw, index=0).model_dump(mode="python")
    projected = _legacy_product(None, normalize_order_item(raw, order_number="123", index=0))
    return canonical, projected


@pytest.mark.parametrize("option", [
    {"id": "rose", "name": "Rose", "value": {"id": "yes", "name": "Yes"}},
    {"id": "rose", "name": "Rose", "values": [{"id": "yes", "name": "Yes"}]},
], ids=["nested-value", "plural-values"])
def test_provider_nested_selections_keep_economic_identity_and_selected_cost(option):
    canonical, projected = _representations({"options": [option]})
    binding = {"id": "rose-cost", "option_id": "rose", "value_id": "yes",
               "mode": "direct", "direct_amount": 40}

    assert line_fingerprint(canonical) == line_fingerprint(projected)
    assert _catalog(canonical, [binding])["line_total"] == 220
    assert _catalog(projected, [binding])["line_total"] == 220


@pytest.mark.parametrize("source,field", [
    ("options", {"id": "name", "name": "Name", "value": "Layla"}),
    ("options", {"id": "date", "name": "Date", "value": "2026-11-03"}),
    ("custom_fields", {"id": "name", "name": "Name", "value": "Layla"}),
    ("custom_fields", {"id": "date", "name": "Date", "value": "2026-11-03"}),
], ids=["text-option", "date-option", "text-field", "date-field"])
def test_text_and_date_preserve_projection_identity_but_freeze_actual_value(source, field):
    canonical, projected = _representations({source: [field]})
    changed_canonical, changed_projected = _representations({source: [{**field, "value": "changed-value"}]})

    assert line_fingerprint(canonical) == line_fingerprint(projected)
    assert line_fingerprint(changed_canonical) == line_fingerprint(changed_projected)
    assert line_fingerprint(canonical) != line_fingerprint(changed_canonical)


@pytest.mark.parametrize("extra", [
    {"options": [{"option_id": "rose", "value_id": "yes", "name": "Rose", "value": "Yes"}],
     "customer_options": [{"option_id": "name", "name": "Name", "value": "Layla"}]},
    {"options": [{"option_id": "rose", "value_id": "yes", "name": "Rose", "value": "Yes"}],
     "product": {"id": "shawl", "name": "Shawl", "options": [
         {"option_id": "name", "name": "Name", "value": "Layla"}]}},
    {"custom_fields": [{"id": "name", "name": "Name", "value": "Layla"}],
     "customizations": [{"id": "date", "name": "Date", "value": "2026-11-03"}]},
], ids=["options-and-customer-options", "options-and-product-options", "fields-and-customizations"])
def test_economic_projection_preserves_all_provider_selection_sources(extra):
    canonical, projected = _representations(extra)

    assert line_fingerprint(canonical) == line_fingerprint(projected)


@pytest.fixture
def runtime_field_cost_support(monkeypatch):
    """Apply the real runtime hook and restore every modified symbol afterwards."""
    import order_option_cost_snapshot_routes as snapshots
    import product_option_cost_routes as option_costs
    import product_v2_details_routes as details
    import product_v2_routes as products
    import supplier_receiving_routes as suppliers
    from product_field_cost_support import install_product_field_cost_support

    for module, name in [
        (snapshots, "selected_option_tokens"), (option_costs, "_option_value"),
        (details, "_details_patch"), (products, "make_product_v2_router"),
        (suppliers, "_supplier_product_reference_price"), (suppliers, "_recent_session_events"),
    ]:
        monkeypatch.setattr(module, name, getattr(module, name))
    install_product_field_cost_support()


@pytest.mark.parametrize("source,field_id", [
    ("options", "date"), ("custom_fields", "field:date"),
])
@pytest.mark.parametrize("value,expected", [("2026-11-03", 100), ("", 60), ("   ", 60)])
def test_runtime_date_field_cost_applies_only_when_filled(
    runtime_field_cost_support, source, field_id, value, expected,
):
    binding = {"id": "date-cost", "option_id": field_id, "value_id": "filled",
               "mode": "direct", "direct_amount": 10}
    canonical, projected = _representations({source: [{"id": "date", "name": "Date", "value": value}]})

    assert _catalog(canonical, [binding])["line_total"] == expected
    assert _catalog(projected, [binding])["line_total"] == expected


def test_option_level_cost_is_applied_once_for_a_present_option(runtime_field_cost_support):
    binding = {"id": "option-cost", "option_id": "name", "value_id": "__option__",
               "mode": "direct", "direct_amount": 10}
    canonical, projected = _representations({
        "options": [{"id": "name", "name": "Name", "value": "Layla"}],
    })
    assert _catalog(canonical, [binding])["line_total"] == 100
    assert _catalog(projected, [binding])["line_total"] == 100
    absent_canonical, absent_projected = _representations({})
    assert _catalog(absent_canonical, [binding])["line_total"] == 60
    assert _catalog(absent_projected, [binding])["line_total"] == 60
