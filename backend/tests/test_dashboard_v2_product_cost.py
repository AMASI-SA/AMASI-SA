from dashboard_v2_routes import (
    _aggregate_provider_rows,
    _build_snapchat_account_summaries,
    _finalize_product_profit_rows,
    _index_products,
    _line_sales_total,
    _line_product,
    PRODUCT_COST_CATALOG_PROJECTION,
    calculate_mezan_v2_line_cost,
)
from order_option_cost_snapshot_routes import classify_base_unit_cost, resolve_base_unit_cost


def test_product_cost_catalog_projection_keeps_raw_salla_cost_aliases():
    assert PRODUCT_COST_CATALOG_PROJECTION["cost_price_from_salla"] == 1
    assert PRODUCT_COST_CATALOG_PROJECTION["cost_price"] == 1
    assert PRODUCT_COST_CATALOG_PROJECTION["cost"] == 1
    assert PRODUCT_COST_CATALOG_PROJECTION["variants"] == 1
    assert PRODUCT_COST_CATALOG_PROJECTION["raw_salla"] == 1
    assert PRODUCT_COST_CATALOG_PROJECTION["raw_salla_details"] == 1


def test_line_product_accepts_all_mezan_catalog_identifiers():
    product = {"id": "internal-1", "mezan_product_id": "mezan-1", "salla_product_id": "salla-1"}
    products_by_id, products_by_variant, products_by_sku = _index_products([product])

    for identity in products_by_id:
        resolved = _line_product(
            {"product_id": identity},
            products_by_id=products_by_id,
            products_by_variant=products_by_variant,
            products_by_sku=products_by_sku,
        )
        assert resolved["salla_product_id"] == "salla-1"


def test_dashboard_recovers_salla_cost_from_full_product_snapshot():
    products_by_id, products_by_variant, products_by_sku = _index_products([{
        "salla_product_id": "p-1",
        "name": "منتج بتكلفة سلة",
        "cost_price_from_salla": None,
        "raw_salla_details": {"cost_price": {"amount": "37.50"}},
        "variants": [],
    }])
    product = _line_product(
        {"source_product_id": "p-1", "quantity": 1},
        products_by_id=products_by_id,
        products_by_variant=products_by_variant,
        products_by_sku=products_by_sku,
    )
    result = calculate_mezan_v2_line_cost(
        {"source_product_id": "p-1", "quantity": 1},
        product=product,
        profile=None,
        product_bindings=[],
        option_bindings=[],
        resources={},
    )

    assert result["line_total"] == 37.5
    assert result["base_cost_source"] == "salla_product_fallback"
    assert result["calculation_cost_available"] is True
    assert result["mezan_cost_missing"] is True


def test_dashboard_resolves_historical_line_by_unique_name_but_not_duplicate_name():
    unique = {
        "salla_product_id": "p-unique",
        "name": "منتج تاريخي فريد",
        "cost_price_from_salla": 25,
    }
    duplicate_a = {"salla_product_id": "p-a", "name": "منتج مكرر"}
    duplicate_b = {"salla_product_id": "p-b", "name": "منتج مكرر"}
    products_by_id, products_by_variant, products_by_sku = _index_products([
        unique, duplicate_a, duplicate_b,
    ])

    assert _line_product(
        {"product_id": "old-id", "name": " منتج تاريخي فريد "},
        products_by_id=products_by_id,
        products_by_variant=products_by_variant,
        products_by_sku=products_by_sku,
    )["salla_product_id"] == "p-unique"
    assert _line_product(
        {"product_id": "old-id", "name": "منتج مكرر"},
        products_by_id=products_by_id,
        products_by_variant=products_by_variant,
        products_by_sku=products_by_sku,
    ) is None


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


def test_raw_salla_variant_cost_price_is_used_as_fallback():
    cost, source = resolve_base_unit_cost(
        {"variant_id": "v-raw", "sku": "RAW-1"},
        {},
        {
            "cost_price_from_salla": None,
            "variants": [
                {
                    "id": "v-raw",
                    "sku": "RAW-1",
                    "cost_price": {"amount": "41.50"},
                }
            ],
        },
    )

    assert (cost, source) == (41.5, "salla_variant_fallback")


def test_raw_salla_product_cost_price_is_used_as_fallback():
    cost, source = resolve_base_unit_cost(
        {"product_id": "319849177", "sku": "AMS13060"},
        {},
        {
            "id": "319849177",
            "sku": "AMS13060",
            "cost_price_from_salla": None,
            "cost_price": {"amount": "55.00"},
            "variants": [],
        },
    )

    assert (cost, source) == (55, "salla_product_fallback")


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
    assert result["calculation_cost_available"] is True
    assert result["mezan_cost_complete"] is False
    assert result["mezan_cost_missing"] is True
    assert result["uses_salla_fallback"] is True


def test_cost_semantics_keep_mezan_completeness_separate_from_calculation_readiness():
    cases = [
        (
            {"base_cost": 20},
            {"cost_price_from_salla": 30},
            {"mezan_cost_missing": False, "calculation_cost_available": True, "calculation_uses_salla_fallback": False},
        ),
        (
            {},
            {"cost_price_from_salla": 30},
            {"mezan_cost_missing": True, "calculation_cost_available": True, "calculation_uses_salla_fallback": True},
        ),
        (
            {},
            {},
            {"mezan_cost_missing": True, "calculation_cost_available": False, "calculation_uses_salla_fallback": False},
        ),
    ]

    for profile, product, expected in cases:
        status = classify_base_unit_cost({"product_id": "p-1"}, profile, product)
        assert {key: status[key] for key in expected} == expected


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


def test_product_line_sales_prefers_source_total_then_uses_normalized_fallback():
    assert _line_sales_total({
        "total": 230,
        "price": 100,
        "quantity": 2,
        "tax": 30,
    }, 2) == 230
    assert _line_sales_total({
        "price": 100,
        "discount": 10,
        "tax": 30,
    }, 2) == 220


def test_product_profit_rows_show_complete_fallback_and_missing_costs_safely():
    rows, summary = _finalize_product_profit_rows({
        "complete": {
            "identity": "complete",
            "name": "منتج مكتمل",
            "units_sold": 2,
            "orders_count": 1,
            "total_sales": 200,
            "total_cost": 80,
            "mezan_cost_complete": True,
            "uses_salla_fallback": False,
            "missing_everywhere": False,
            "catalog_product_found": True,
            "cost_sources": {"mezan_v2_base"},
        },
        "fallback": {
            "identity": "fallback",
            "name": "تكلفة سلة فقط",
            "units_sold": 3,
            "orders_count": 2,
            "total_sales": 300,
            "total_cost": 90,
            "mezan_cost_complete": False,
            "uses_salla_fallback": True,
            "missing_everywhere": False,
            "catalog_product_found": True,
            "cost_sources": {"salla_product_fallback"},
        },
        "missing": {
            "identity": "missing",
            "name": "بدون تكلفة",
            "units_sold": 1,
            "orders_count": 1,
            "total_sales": 150,
            "total_cost": 5,
            "mezan_cost_complete": False,
            "uses_salla_fallback": False,
            "missing_everywhere": True,
            "catalog_product_found": True,
            "cost_sources": {"missing"},
        },
    })

    assert [row["units_sold"] for row in rows] == [3.0, 2.0, 1.0]
    by_status = {row["cost_status"]: row for row in rows}
    missing = by_status["missing"]
    fallback = by_status["salla_fallback"]
    complete = by_status["complete"]
    assert missing["average_unit_cost"] == 5
    assert missing["total_cost"] == 5
    assert missing["cost_is_partial"] is True
    assert missing["net_profit"] is None
    assert fallback["cost_is_partial"] is False
    assert fallback["average_unit_cost"] == 30
    assert fallback["net_profit"] == 210
    assert complete["average_unit_cost"] == 40
    assert complete["net_profit"] == 120
    assert summary == {
        "product_count": 3,
        "total_units": 6.0,
        "total_sales": 650.0,
        "total_cost": 175.0,
        "net_profit": None,
        "has_unpriced_products": True,
        "uses_salla_fallback": True,
    }


def test_product_profit_rows_keep_truly_missing_zero_cost_hidden():
    rows, summary = _finalize_product_profit_rows({
        "missing": {
            "identity": "missing",
            "name": "بدون أي تكلفة",
            "units_sold": 2,
            "orders_count": 1,
            "total_sales": 100,
            "total_cost": 0,
            "mezan_cost_complete": False,
            "uses_salla_fallback": False,
            "missing_everywhere": True,
            "catalog_product_found": True,
            "cost_sources": {"missing"},
        },
    })

    assert rows[0]["total_cost"] is None
    assert rows[0]["average_unit_cost"] is None
    assert rows[0]["net_profit"] is None
    assert rows[0]["cost_is_partial"] is False
    assert summary["total_cost"] == 0
    assert summary["net_profit"] is None


def test_snapchat_v2_cards_keep_today_and_month_metrics_separate_per_account():
    rows = [
        {
            "ad_account_id": "snap-a",
            "date": "2026-08-01",
            "spend_sar": 100,
            "purchases": 2,
            "purchase_value_sar": 300,
            "currency": "USD",
            "account_timezone": "America/Los_Angeles",
            "updated_at": "2026-08-01T10:00:00+00:00",
        },
        {
            "ad_account_id": "snap-a",
            "date": "2026-08-02",
            "spend_sar": 50,
            "purchases": 1,
            "purchase_value_sar": 120,
            "currency": "USD",
            "account_timezone": "America/Los_Angeles",
            "updated_at": "2026-08-02T10:00:00+00:00",
        },
        {
            "ad_account_id": "snap-b",
            "date": "2026-08-02",
            "spend_sar": 20,
            "purchases": 4,
            "purchase_value_sar": 200,
            "currency": "SAR",
            "account_timezone": "Asia/Riyadh",
            "updated_at": "2026-08-02T10:05:00+00:00",
        },
    ]
    accounts = _build_snapchat_account_summaries(
        rows,
        [
            {"ad_account_id": "snap-a", "display_name": "حساب أ"},
            {"ad_account_id": "snap-b", "display_name": "حساب ب"},
        ],
        month_start="2026-08-01",
        today="2026-08-02",
    )

    by_id = {row["id"]: row for row in accounts}
    account_a = by_id["snap-a"]
    account_b = by_id["snap-b"]
    assert account_a["today"]["spend"] == 50
    assert account_a["today"]["orders"] == 1
    assert account_a["month"]["spend"] == 150
    assert account_a["month"]["orders"] == 3
    assert account_b["today"]["spend"] == 20
    assert account_b["month"]["spend"] == 20
    assert account_b["month"]["orders"] == 4
    assert account_a["currency"] == "USD"
    assert account_b["timezone"] == "Asia/Riyadh"
    assert account_a["spend_share_pct"] == 88.24
    assert account_b["spend_share_pct"] == 11.76


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
