from ai_store_operations_foundation import PERMISSIONS, ROLE_CATALOG, product_readiness


def test_owner_role_has_all_permissions():
    assert set(ROLE_CATALOG["owner"]) == PERMISSIONS


def test_supplier_invoice_price_and_service_changes_are_explicit_permissions():
    governed = {
        "supplier_receiving.product_price.edit",
        "supplier_receiving.service_price.edit",
        "supplier_receiving.service.add",
    }
    assert governed <= PERMISSIONS
    assert governed.isdisjoint(ROLE_CATALOG["warehouse_operator"])


def test_ai_role_cannot_publish_or_delete_media_by_default():
    permissions = set(ROLE_CATALOG["ai_product_optimizer"])
    assert "products.ai.recommend" in permissions
    assert "products.media.ai_generate" in permissions
    assert "products.media.publish" not in permissions
    assert "products.media.delete" not in permissions
    assert "products.ai.execute_high_risk" not in permissions


def test_product_readiness_is_100_when_all_required_data_exists():
    product = {
        "sku": "AMS10001",
        "images": [{"url": "https://example.com/a.jpg"}],
        "description_html": "<p>وصف</p>",
        "categories": [{"id": "1", "name": "فئة"}],
        "details_loaded": True,
        "options_count": 0,
    }
    readiness = product_readiness(product, {"base_cost": 25})
    assert readiness["score"] == 100
    assert readiness["ready"] is True
    assert readiness["blockers"] == []


def test_product_readiness_prioritizes_missing_sku_and_cost():
    product = {
        "images": [{"url": "https://example.com/a.jpg"}],
        "description": "وصف",
        "categories": [{"id": "1"}],
        "details_loaded": True,
        "options_count": 0,
    }
    readiness = product_readiness(product, {})
    assert readiness["ready"] is False
    assert readiness["score"] == 60
    assert readiness["blockers"][:2] == ["has_sku", "has_base_cost"]
    assert readiness["recommended_action"] == "has_sku"


def test_products_with_options_need_option_cost_links():
    product = {
        "sku": "AMS10002",
        "main_image": "https://example.com/a.jpg",
        "description": "وصف",
        "categories": [{"id": "1"}],
        "details_loaded": True,
        "options_count": 2,
        "option_cost_links_count": 0,
    }
    readiness = product_readiness(product, {"base_cost": 10})
    assert readiness["checks"]["option_costs_ready"] is False
    assert readiness["score"] == 90
