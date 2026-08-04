from __future__ import annotations

from integrations_control_center import (
    snapchat_campaign_current_catalog_cost as module,
)
from dashboard_v2_routes import calculate_mezan_v2_line_cost


def _no_match(*args, **kwargs):
    return None


def test_current_salla_base_cost_is_recovered_from_full_product_snapshot():
    product = module.enrich_current_salla_cost({
        "salla_product_id": "10060",
        "name": "تعليقة سيارة بالاسم شكل خيل",
        "sku": None,
        "cost_price_from_salla": None,
        "variants": [],
        "raw_salla_details": {"cost_price": 35},
    })

    assert product["cost_price_from_salla"] == 35
    result = calculate_mezan_v2_line_cost(
        {"quantity": 1},
        product=product,
        profile=None,
        product_bindings=[],
        option_bindings=[],
        resources={},
    )
    assert result["line_total"] == 35
    assert result["base_complete"] is True
    assert result["uses_salla_fallback"] is True
    assert result["base_cost_source"] == "salla_product_fallback"


def test_current_salla_variant_cost_is_recovered_and_used():
    product = module.enrich_current_salla_cost({
        "salla_product_id": "product-1",
        "cost_price_from_salla": 20,
        "variants": [{"id": "variant-1", "sku": "AMS10060"}],
        "raw_salla_details": {
            "variants": [
                {"id": "variant-1", "sku": "AMS10060", "cost_price": 35},
            ],
        },
    })

    assert product["variants"][0]["cost_price_from_salla"] == 35
    result = calculate_mezan_v2_line_cost(
        {"variant_id": "variant-1", "sku": "AMS10060", "quantity": 2},
        product=product,
        profile=None,
        product_bindings=[],
        option_bindings=[],
        resources={},
    )
    assert result["line_total"] == 70
    assert result["base_cost_source"] == "salla_variant_fallback"


def test_historical_line_resolves_by_exact_unique_product_name():
    product = {
        "salla_product_id": "10060",
        "name": "تعليقة سيارة بالاسم شكل خيل",
        "cost_price_from_salla": 35,
        "variants": [],
    }
    name = module.normalize_product_name(product["name"])
    resolved = module.resolve_campaign_line_product(
        {
            "product_id": "historical-variant-id",
            "name": "  تعليقة سيارة بالاسم شكل خيل  ",
            "sku": "",
        },
        products_by_id={"10060": product},
        products_by_variant={},
        products_by_sku={f"{module.NAME_ALIAS_PREFIX}{name}": product},
        base_resolver=_no_match,
    )

    assert resolved is product
    result = calculate_mezan_v2_line_cost(
        {"quantity": 1},
        product=resolved,
        profile=None,
        product_bindings=[],
        option_bindings=[],
        resources={},
    )
    assert result["line_total"] == 35
    assert result["base_complete"] is True


def test_duplicate_names_are_not_eligible_for_name_fallback():
    name = module.normalize_product_name("منتج مكرر")
    resolved = module.resolve_campaign_line_product(
        {"name": "منتج مكرر"},
        products_by_id={},
        products_by_variant={},
        products_by_sku={},
        base_resolver=_no_match,
    )

    assert name
    assert resolved is None


def test_alias_identifier_and_sku_resolution_precede_name():
    product = {"salla_product_id": "10060", "cost_price_from_salla": 35}
    by_id = module.resolve_campaign_line_product(
        {"source_product_id": "10060", "name": "اسم قديم"},
        products_by_id={"10060": product},
        products_by_variant={},
        products_by_sku={},
        base_resolver=_no_match,
    )
    by_sku = module.resolve_campaign_line_product(
        {"variant_sku": "AMS10060", "name": "اسم قديم"},
        products_by_id={},
        products_by_variant={},
        products_by_sku={"ams10060": product},
        base_resolver=_no_match,
    )

    assert by_id is product
    assert by_sku is product
