from product_v2_details_routes import _details_patch
from campaign_ai_product_intelligence_v3 import _variant_rows, _option_price_context
from campaign_ai_evidence_v3 import _compact_product


def test_zero_option_price_and_inventory_survive_conflicting_fallbacks():
    product = _details_patch({"id": 1, "options": [{"values": [{
        "price": 0, "additional_price": 10, "quantity": 0, "stock_quantity": 9,
    }]}]}, user_id="owner")
    value = product["options"][0]["values"][0]
    assert value["price"] == 0
    assert value["quantity"] == 0
    assert value["price_source_field"] == "price"
    assert value["additional_price"] == 10


def test_additional_price_is_preserved_without_claiming_selected_total():
    product = _details_patch({"id": 1, "price": 139, "options": [{"values": [
        {"name": "children", "additional_price": 0},
        {"name": "adult", "additional_price": 10},
    ]}]}, user_id="owner")
    values = product["options"][0]["values"]
    assert [v["price"] for v in values] == [0, 10]
    assert all(v["price_source_field"] == "additional_price" for v in values)
    context = _option_price_context(product)
    assert context["selected_final_price_verified"] is False
    assert context["independent_option_addition_verified"] is False
    assert _compact_product({"option_price_context": context})["option_price_context"] == context


def test_zero_variant_stock_and_sale_price_survive_catalog_to_analysis():
    product = _details_patch({"id": 1, "variants": [{
        "id": "adult", "price": 149, "sale_price": 0, "discount_price": 139,
        "quantity": 0, "stock_quantity": 8,
    }]}, user_id="owner")
    variant = _variant_rows(product)[0]
    assert variant["quantity"] == 0
    assert variant["sale_price"] == 0
    assert variant["price"] == 149


def test_legacy_prices_are_explicitly_ambiguous():
    product = {"options": [{"values": [{"price": 10}]}]}
    context = _option_price_context(product)
    assert context["source_fields_complete"] is False
    assert context["selected_final_price_verified"] is False


def test_missing_primary_values_keep_existing_alias_support():
    product = _details_patch({"id": 1, "variants": [{
        "quantity": None, "stock_quantity": 4, "sale_price": "", "discount_price": 129,
    }]}, user_id="owner")
    variant = _variant_rows(product)[0]
    assert variant["quantity"] == 4
    assert variant["sale_price"] == 129
