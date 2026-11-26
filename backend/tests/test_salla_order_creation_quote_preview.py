"""Synthetic safe Salla order review: no invented payable total or checkout URL."""

from datetime import datetime, timedelta, timezone
from copy import deepcopy

import pytest

from salla_order_creation.catalog_validation import CartUnverified
from salla_order_creation.payload import PendingOrder
from salla_order_creation.quote_preview import preview_assisted_order
from salla_order_creation.shipping_selection import ShippingSelectionUnverified

NOW = datetime.fromtimestamp(1_800_000_000, tz=timezone.utc)


def order():
    return PendingOrder.model_validate({
        "customer": {"id": 1234},
        "receiver": {
            "name": "Synthetic Recipient", "phone": "+966500000000", "country_code": "SA",
        },
        "courier_id": "201",
        "ship_to": {
            "country": 1, "city": 2, "district": 3, "block": None,
            "street_number": "11", "address_line": "Synthetic St",
            "postal_code": "12345", "short_address": "RHMA3184",
            "building_number": "11", "additional_number": "22",
            "geo_coordinates": {"lat": 24.7, "lng": 46.7},
        },
        "accepted_methods": ["mada"],
        "products": [
            {
                "identifier_type": "id", "identifier": 400,
                "quantity": 2, "options": [{"id": 50, "value": ["601"]}],
            },
        ],
    })


def product():
    return {
        "source": "salla_product_details",
        "can_use_as_final_quote": False,
        "product_id": 400, "available_quantity": 4,
        "taxed_base_price_sar": "95.00",
        "options": [{
            "option_id": 50, "type": "radio", "required": True,
            "values": [{"value_id": 601, "price_delta_sar": "0.00",
                        "out_of_stock": False}],
        }],
        "variants": [{
            "variant_id": 9001, "selected_option_value_ids": [601],
            "available_quantity": 2, "provider_variant_price_sar": "95.00",
        }],
    }


def shipping():
    return {
        "rate_id": 501, "shipping_company_id": 201,
        "destination_country_id": 1, "destination_city_id": 2,
        "estimated_shipping_sar": "23.00",
        "cod_service_found": True, "cod_fee_estimate_sar": "5.00",
        "source": "salla_shipping_rate_estimate",
        "is_final_cart_quote": False,
    }


def preview(o=None, p=None, r=None, *, coupon=None, product_time=NOW, carrier_time=NOW):
    return preview_assisted_order(
        o or order(),
        catalog_by_product_id={400: p or product()},
        selected_rate_id=501,
        shipping_rate_candidates=[r or shipping()],
        catalog_checked_at=product_time,
        carrier_checked_at=carrier_time,
        now=NOW,
        discount_code=coupon,
    )


def test_preview_shows_real_known_fields_but_never_promises_total():
    item = preview()
    assert item["state"] == "awaiting_authoritative_final_quote"
    assert item["item_lines"] == [{
        "product_id": 400, "variant_id": 9001, "quantity": 2,
        "option_value_ids": [601],
        "provider_list_price_sar": "95.00",
        "is_final_price": False,
    }]
    assert item["carrier_id"] == 201
    assert item["shipping_estimate_sar"] == "23.00"
    assert item["verified_grand_total_sar"] is None
    assert item["can_create_order"] is False
    assert item["is_final_cart_quote"] is False
    assert "checkout_url" not in str(item)


def test_coupon_is_requested_but_never_assumed_discounted():
    row = preview(coupon="am")
    assert row["discount_code_requested"] is True
    assert "coupon_checkout_eligibility_unverified" in row["reasons"]
    assert "discounted_price_sar" not in row
    assert row["verified_grand_total_sar"] is None


def test_unavailable_variant_cannot_be_promised():
    p = product()
    p["variants"][0]["available_quantity"] = 0
    with pytest.raises(CartUnverified):
        preview(p=p)


def test_stale_source_cannot_be_shown_as_current():
    with pytest.raises(CartUnverified):
        preview(product_time=NOW-timedelta(seconds=121))
    with pytest.raises(ShippingSelectionUnverified):
        preview(carrier_time=NOW-timedelta(seconds=121))


def test_other_destination_carrier_is_rejected():
    bad = shipping()
    bad["destination_city_id"] = 10
    with pytest.raises(ShippingSelectionUnverified):
        preview(r=bad)


def test_cod_not_supported_on_carrier_is_rejected():
    o = order()
    o.accepted_methods = ["cod"]
    r = shipping()
    r["cod_service_found"] = False
    with pytest.raises(ShippingSelectionUnverified):
        preview(o=o, r=r)


def test_applicant_does_not_control_authoritative_scope():
    p = product()
    p["source"] = "language_model_suggestion"
    with pytest.raises(CartUnverified):
        preview(p=p)
