from types import SimpleNamespace

from order_engine.mapper import map_salla_order
from preview_fulfillment_seed import (
    CREATE_CONFIRMATION,
    NECKLACE_QUANTITIES,
    PREVIEW_SEED_ID,
    RESET_CONFIRMATION,
    build_preview_seed_documents,
    make_preview_fulfillment_seed_router,
    preview_runtime_details,
)
from reviewed_products_catalog import aggregate_reviewed_products


def test_preview_runtime_requires_positive_preview_signal():
    assert preview_runtime_details({
        "FRONTEND_URL": "https://salla-analytics.preview.emergent.host",
    })["available"] is True
    assert preview_runtime_details({
        "MEZAN_RUNTIME_ENV": "development",
        "FRONTEND_URL": "http://localhost:3000",
    })["available"] is True
    assert preview_runtime_details({
        "FRONTEND_URL": "https://mezansalla.com",
    })["available"] is False
    assert preview_runtime_details({})["available"] is False


def test_seed_builds_three_products_twenty_orders_and_expected_stages():
    seed = build_preview_seed_documents("owner-1")

    assert len(seed["products"]) == 3
    assert len(seed["orders"]) == 20
    assert len(seed["workflows"]) == 18
    assert len(seed["reviewed_order_numbers"]) == 18
    assert len(seed["pending_order_numbers"]) == 2
    assert seed["summary"] == {
        "products": 3,
        "orders": 20,
        "reviewed_orders": 18,
        "pending_orders": 2,
        "reviewed_quantity": 62,
        "necklace_quantity": 50,
        "watch_quantity": 10,
        "bag_quantity": 2,
    }
    assert sum(NECKLACE_QUANTITIES) == 50
    assert all(row["preview_seed_id"] == PREVIEW_SEED_ID for row in seed["orders"])
    assert all(row["preview_seed_id"] == PREVIEW_SEED_ID for row in seed["products"])


def test_every_seed_order_maps_through_canonical_order_engine():
    seed = build_preview_seed_documents("owner-1")
    mapped = [
        map_salla_order(row["raw_by_source"]["salla_direct"])
        for row in seed["orders"]
    ]

    assert len(mapped) == 20
    assert all(order.order_number.startswith("99082") for order in mapped)
    assert all(order.items and order.items[0].image_url.startswith("data:image/png;base64,") for order in mapped)
    assert all(order.customer.name.startswith("عميل Preview") for order in mapped)
    assert mapped[0].items[0].custom_fields[0]["name"] == "الاسم"


def test_reviewed_catalog_aggregates_preview_quantities_and_categories():
    seed = build_preview_seed_documents("owner-1")
    order_by_number = {
        row["order_number"]: map_salla_order(row["raw_by_source"]["salla_direct"])
        for row in seed["orders"]
    }
    pairs = [
        (order_by_number[workflow["order_number"]], workflow)
        for workflow in seed["workflows"]
    ]

    catalog = aggregate_reviewed_products(pairs, seed["products"])
    by_product_id = {row["product_id"]: row for row in catalog["products"]}

    assert catalog["summary"]["reviewed_order_count"] == 18
    assert catalog["summary"]["unique_product_count"] == 3
    assert catalog["summary"]["total_quantity"] == 62
    assert by_product_id["990001"]["quantity"] == 50
    assert by_product_id["990002"]["quantity"] == 10
    assert by_product_id["990003"]["quantity"] == 2
    assert len(by_product_id["990001"]["source_lines"]) == 15
    category_ids = {row["id"] for row in catalog["categories"]}
    assert {"pv-accessories", "pv-necklaces", "pv-watches", "pv-fashion", "pv-bags"}.issubset(category_ids)


def test_seed_confirmations_and_router_contract():
    assert CREATE_CONFIRMATION == "CREATE_PREVIEW_TEST_DATA"
    assert RESET_CONFIRMATION == "DELETE_PREVIEW_TEST_DATA"

    router = make_preview_fulfillment_seed_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }
    assert ("/preview-fulfillment-seed-v1/status", "GET") in routes
    assert ("/preview-fulfillment-seed-v1/create", "POST") in routes
    assert ("/preview-fulfillment-seed-v1/reset", "DELETE") in routes
