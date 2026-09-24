from sold_products_report_v2 import aggregate_sold_products, order_matches_status, summarize


def test_registered_mezan_cost_and_missing_cost_sort_before_popular_priced_product():
    products = [
        {"salla_product_id": "p1", "mezan_product_id": "mpv2_p1", "sku": "SKU1", "name": "الأول", "main_image": "https://example.com/p1.jpg", "cost_price_from_salla": 99},
        {"salla_product_id": "p2", "mezan_product_id": "mpv2_p2", "sku": "SKU2", "name": "الثاني"},
    ]
    orders = [{"products": [
        {"product_id": "p1", "sku": "SKU1", "quantity": 30},
        {"product_id": "p2", "sku": "SKU2", "quantity": 1},
    ]}]
    rows = aggregate_sold_products(orders, products, [{"salla_product_id": "p1", "base_cost": 15}])
    assert [row["sku"] for row in rows] == ["SKU2", "SKU1"]
    assert rows[0]["total_cost"] is None
    assert rows[0]["mezan_product_id"] == "mpv2_p2"
    assert rows[1]["image_url"] == "https://example.com/p1.jpg"
    assert rows[1]["unit_cost"] == 15
    assert rows[1]["cost_source"] == "mezan"
    assert rows[1]["total_cost"] == 450
    assert summarize(rows) == {
        "units_sold": 31.0, "average_unit_cost": None, "total_cost": None,
        "known_cost_total": 450.0, "known_cost_units": 30.0,
    }


def test_salla_cost_is_used_only_when_mezan_cost_is_not_registered():
    products = [{"salla_product_id": "p1", "sku": "SKU1", "cost_price_from_salla": 25}]
    orders = [{"products": [{"product_id": "p1", "sku": "SKU1", "quantity": 3}]}]
    fallback = aggregate_sold_products(orders, products, [])
    assert fallback[0]["cost_source"] == "salla"
    assert fallback[0]["unit_cost"] == 25
    assert fallback[0]["total_cost"] == 75
    mezan = aggregate_sold_products(orders, products, [{"salla_product_id": "p1", "base_cost": 10}])
    assert mezan[0]["cost_source"] == "mezan"
    assert mezan[0]["total_cost"] == 30


def test_selected_order_options_do_not_change_registered_unit_cost():
    rows = aggregate_sold_products([{"products": [
        {"product_id": "p1", "sku": "SKU1", "quantity": 2},
        {"product_id": "p1", "sku": "SKU1", "quantity": 1,
         "options": [{"name": "التغليف", "value": "فاخر"}]},
    ]}], [{"salla_product_id": "p1", "sku": "SKU1"}], [{"salla_product_id": "p1", "base_cost": 15}])
    assert rows[0]["units_sold"] == 3
    assert rows[0]["total_cost"] == 45
    assert summarize(rows)["average_unit_cost"] == 15


def test_statuses_default_exclude_cancelled_and_custom_selection_can_include_multiple():
    pending = {"order_status": "بإنتظار المراجعة"}
    reviewed = {"order_status": "تم المراجعة"}
    cancelled = {"order_status": "ملغي"}
    assert order_matches_status(pending, ["default"], [])
    assert order_matches_status(reviewed, ["pending_review", "reviewed"], [])
    assert not order_matches_status(cancelled, ["default"], [])
    assert order_matches_status(cancelled, ["default"], ["ملغي"])
    assert not order_matches_status(reviewed, ["default"], ["ملغي"])
    assert not order_matches_status(cancelled, ["pending_review", "reviewed"], [])
