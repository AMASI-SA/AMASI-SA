from dashboard_v2_routes import _aggregate_provider_rows, calculate_mezan_v2_line_cost
from order_option_cost_snapshot_routes import resolve_base_unit_cost


def test_mezan_base_wins_and_selected_components_are_added_once():
    item = {
        "product_id": "p-1",
        "quantity": 2,
        "options": [{"name": "التغليف", "value": "فاخر"}],
    }
    result = calculate_mezan_v2_line_cost(
        item,
        product={"cost_price_from_salla": 99},
        profile={"base_cost": 10},
        product_bindings=[
            {"id": "product-r1", "resource_id": "r1", "quantity": 2},
            {"id": "product-r1", "resource_id": "r1", "quantity": 2},
        ],
        option_bindings=[
            {
                "id": "selected",
                "option_name": "التغليف",
                "value_name": "فاخر",
                "mode": "resource",
                "resource_id": "r2",
                "quantity": 1,
            },
            {
                "id": "not-selected",
                "option_name": "التغليف",
                "value_name": "عادي",
                "mode": "direct",
                "direct_amount": 100,
            },
        ],
        resources={
            "r1": {"id": "r1", "unit_cost": 3},
            "r2": {"id": "r2", "unit_cost": 4},
        },
    )

    assert result["base_cost_source"] == "mezan_v2_base"
    assert result["base_total"] == 20
    assert result["product_components_total"] == 12
    assert result["selected_options_total"] == 8
    assert result["line_total"] == 40


def test_mezan_variant_wins_over_mezan_base_and_salla():
    cost, source = resolve_base_unit_cost(
        {"variant_id": "v-2", "sku": "SKU-2"},
        {"base_cost": 10, "variant_costs": {"v-2": 17}},
        {
            "cost_price_from_salla": 30,
            "variants": [{"id": "v-2", "sku": "SKU-2", "cost_price_from_salla": 35}],
        },
    )

    assert cost == 17
    assert source == "mezan_v2_variant"


def test_missing_mezan_cost_falls_back_to_salla_variant_then_product():
    variant_cost, variant_source = resolve_base_unit_cost(
        {"variant_id": "v-2"},
        {},
        {
            "cost_price_from_salla": 30,
            "variants": [{"id": "v-2", "cost_price_from_salla": 35}],
        },
    )
    product_cost, product_source = resolve_base_unit_cost(
        {"variant_id": "unknown"},
        {},
        {"cost_price_from_salla": 30, "variants": []},
    )

    assert (variant_cost, variant_source) == (35, "salla_variant_fallback")
    assert (product_cost, product_source) == (30, "salla_product_fallback")


def test_salla_fallback_calculates_cost_but_stays_missing_in_mezan():
    result = calculate_mezan_v2_line_cost(
        {"product_id": "p-1", "quantity": 1},
        product={"cost_price_from_salla": 30},
        profile=None,
        product_bindings=[],
        option_bindings=[],
        resources={},
    )

    assert result["line_total"] == 30
    assert result["base_complete"] is True
    assert result["mezan_cost_complete"] is False
    assert result["uses_salla_fallback"] is True


def test_explicit_zero_in_mezan_does_not_fall_back_to_salla():
    base_cost, base_source = resolve_base_unit_cost(
        {"variant_id": "unknown"},
        {"base_cost": 0},
        {"cost_price_from_salla": 50},
    )
    variant_cost, variant_source = resolve_base_unit_cost(
        {"variant_id": "v-0"},
        {"base_cost": 20, "variant_costs": {"v-0": 0}},
        {"cost_price_from_salla": 50},
    )

    assert (base_cost, base_source) == (0, "mezan_v2_base")
    assert (variant_cost, variant_source) == (0, "mezan_v2_variant")


def test_missing_everywhere_is_reported_instead_of_inventing_a_cost():
    cost, source = resolve_base_unit_cost(
        {"product_id": "p-404"},
        None,
        None,
    )

    assert cost is None
    assert source == "missing"


def test_snapchat_nested_v2_metrics_are_aggregated_without_entity_double_counting():
    result = _aggregate_provider_rows(
        [{
            "date": "2026-07-31",
            "spend_sar": 25,
            "purchase_value_sar": 100,
            "metrics": {
                "conversion_purchases": 4,
                "impressions": 1000,
                "swipes": 50,
            },
        }],
        "2026-07-31",
        "2026-07-31",
    )

    assert result["spend"] == 25
    assert result["orders"] == 4
    assert result["revenue"] == 100
    assert result["roas"] == 4
    assert result["cost_per_order"] == 6.25
