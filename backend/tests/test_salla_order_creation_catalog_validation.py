"""Offline exact Salla product-option and variant availability acceptance."""

import copy
from datetime import datetime, timedelta, timezone

import pytest

from salla_order_creation.catalog_validation import CartUnverified, validate_selected_cart
from salla_order_creation.payload import PendingOrder

NOW = datetime.fromtimestamp(1800000000, tz=timezone.utc)


def order():
    return PendingOrder.model_validate({
        "customer": {"id": 101},
        "receiver": {"name": "Synthetic Gift", "phone": "+966500000000", "country_code": "SA"},
        "courier_id": "202",
        "ship_to": {
            "country": 1, "city": 2, "district": 3, "block": None,
            "street_number": "11", "address_line": "Synthetic street",
            "postal_code": "12345", "short_address": "RHMA3184",
            "building_number": "11", "additional_number": "22",
            "geo_coordinates": {"lat": 24.7, "lng": 46.7},
        },
        "accepted_methods": ["mada"],
        "products": [{
            "identifier_type": "id", "identifier": 1234, "quantity": 2,
            "options": [{"id": 50, "value": ["601"]}],
        }],
    })


def catalog():
    return {
        "product_id": 1234, "name": "Synthetic gift",
        "source": "salla_product_details", "can_use_as_final_quote": False,
        "taxed_base_price_sar": "90.50", "available_quantity": 10,
        "options": [{
            "option_id": 50, "type": "radio", "required": True,
            "values": [
                {"value_id": 601, "out_of_stock": False, "price_delta_sar": "0.00"},
                {"value_id": 602, "out_of_stock": False, "price_delta_sar": "5.00"},
            ],
        }],
        "variants": [
            {"variant_id": 9001, "selected_option_value_ids": [601],
             "available_quantity": 4, "provider_variant_price_sar": "90.50"},
            {"variant_id": 9002, "selected_option_value_ids": [602],
             "available_quantity": 4, "provider_variant_price_sar": "95.50"},
        ],
    }


def checked(order_value=None, catalog_value=None, checked_at=None):
    return validate_selected_cart(
        order_value or order(),
        catalog_by_product_id={1234: catalog_value or catalog()},
        checked_at=checked_at or NOW,
        now=NOW,
    )


def test_exact_option_id_and_variant_stock_approved_as_evidence_only():
    row = checked()[0]
    assert row["product_id"] == 1234
    assert row["variant_id"] == 9001
    assert row["quantity"] == 2
    assert row["option_value_ids"] == [601]
    assert row["provider_list_price_sar"] == "90.50"
    assert row["is_final_cart_quote"] is False


@pytest.mark.parametrize("alter",[
    lambda p: p["options"][0].update(type="text"),
    lambda p: p["options"][0]["values"][0].update(out_of_stock=True),
    lambda p: p["options"][0]["values"][0].update(price_delta_sar=None),
    lambda p: p.update(source="model_estimate"),
    lambda p: p.update(available_quantity=0),
    lambda p: p["variants"][0].update(available_quantity=1),
    lambda p: p["variants"].append(copy.deepcopy(p["variants"][0])),
    lambda p: p.update(variants=[p["variants"][1]]),
])
def test_bad_or_unavailable_catalog_facts_rejected(alter):
    p=catalog()
    alter(p)
    with pytest.raises(CartUnverified):
        checked(catalog_value=p)


def test_required_option_missing_fails():
    o=order()
    o.products[0].options=[]
    with pytest.raises(CartUnverified,match="required_option_missing"):
        checked(order_value=o)


def test_unknown_option_id_rejected():
    o=order()
    o.products[0].options[0].id=99
    with pytest.raises(CartUnverified,match="unknown_product_option"):
        checked(order_value=o)


@pytest.mark.parametrize("value",["999","00601","M","0","-601"])
def test_invalid_option_value_id_fails(value):
    o=order()
    o.products[0].options[0].value=[value]
    with pytest.raises(CartUnverified):
        checked(order_value=o)


def test_repeated_variant_rows_must_respect_aggregate_stock():
    o=order()
    o.products.append(copy.deepcopy(o.products[0]))
    o.products[1].quantity=3
    with pytest.raises(CartUnverified,match="selected_variant_out_of_stock"):
        checked(order_value=o)


def test_aggregate_product_inventory_cannot_be_exceeded():
    p=catalog()
    p["available_quantity"]=3
    o=order()
    o.products.append(copy.deepcopy(o.products[0]))
    o.products[1].quantity=2
    with pytest.raises(CartUnverified):
        checked(order_value=o,catalog_value=p)


def test_stale_product_facts_cannot_enter_verified_quote():
    with pytest.raises(CartUnverified,match="fresh_authoritative_catalog_required"):
        checked(checked_at=NOW - timedelta(seconds=121))


def test_sku_only_input_needs_exact_product_resolution():
    o=order()
    o.products[0].identifier_type="sku"
    o.products[0].identifier="SKU-1234"
    with pytest.raises(CartUnverified,match="canonical_product_id_required"):
        checked(order_value=o)


def test_simple_product_without_options_or_variants():
    o=order()
    o.products[0].options=[]
    p=catalog()
    p["options"]=[]
    p["variants"]=[]
    evidence=checked(order_value=o,catalog_value=p)
    assert evidence[0]["variant_id"] is None
    assert evidence[0]["is_final_cart_quote"] is False


def test_out_of_stock_combination_rejected_even_when_product_unlimited():
    p=catalog()
    p["available_quantity"]=None
    p["variants"][0]["available_quantity"]=0
    with pytest.raises(CartUnverified,match="selected_variant_out_of_stock"):
        checked(catalog_value=p)
