"""Synthetic Amasi jewelry personalization and conditional option enforcement.

Real Amasi products can have required `text` engraving options, and optional
or required text fields visible only if a specific radio choice was selected.
This is NOT a live Salla API creation acceptance test.
"""

from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from salla_order_creation.catalog_validation import (
    CartUnverified, validate_selected_cart,
)
from salla_order_creation.payload import PendingOrder

NOW = datetime.fromtimestamp(1_800_000_000, tz=timezone.utc)


def source():
    return {
        "source": "salla_product_details",
        "product_id": 400,
        "can_use_as_final_quote": False,
        "taxed_base_price_sar": "110.00",
        "available_quantity": None,
        "variants": [],
        "options": [
            {
                "option_id": 1001, "type": "text", "required": True,
                "visibility": "always", "characters_limit": None,
                "values": [],
            },
            {
                "option_id": 1002, "type": "radio", "required": True,
                "visibility": "always",
                "values": [
                    {"value_id": 2001, "out_of_stock": False,
                     "price_delta_sar": "0.00"},
                    {"value_id": 2002, "out_of_stock": False,
                     "price_delta_sar": "10.00"},
                ],
            },
            {
                "option_id": 1003, "type": "text", "required": True,
                "visibility": "on_condition",
                "visibility_condition_type": "=",
                "visibility_condition_option": 1002,
                "visibility_condition_value": 2001,
                "characters_limit": 40,
                "values": [],
            },
        ],
    }


def order(option_values=None):
    if option_values is None:
        option_values = [
            {"id": 1001, "value": ["اسم تجريبي"]},
            {"id": 1002, "value": ["2001"]},
            {"id": 1003, "value": ["نقش إضافي"]},
        ]
    return PendingOrder.model_validate({
        "customer": {"id": 1234},
        "receiver": {"name": "Synthetic Recipient", "phone": "+966500000000",
                     "country_code": "SA"},
        "courier_id": "200",
        "ship_to": {
            "country": 1, "city": 2, "district": 3, "block": None,
            "street_number": "11", "address_line": "Synthetic street",
            "postal_code": "12345", "short_address": "RHMA3184",
            "building_number": "11", "additional_number": "22",
            "geo_coordinates": {"lat": 24.7, "lng": 46.7},
        },
        "accepted_methods": ["mada"],
        "products": [{
            "identifier_type": "id", "identifier": 400, "quantity": 1,
            "options": option_values,
        }],
    })


def check(o=None, source_value=None):
    return validate_selected_cart(
        o or order(),
        catalog_by_product_id={400: source_value or source()},
        checked_at=NOW, now=NOW,
    )


def test_personalization_text_and_conditional_choice_can_be_selected():
    row = check()[0]
    assert row["product_id"] == 400
    assert row["variant_id"] is None
    assert row["option_value_ids"] == [2001]
    assert row["selected_text_option_ids"] == [1001, 1003]
    assert "اسم تجريبي" not in str(row)
    assert row["is_final_cart_quote"] is False


def test_other_radio_value_disables_required_conditional_text():
    o = order([
        {"id": 1001, "value": ["هدية تجريبية"]},
        {"id": 1002, "value": ["2002"]},
    ])
    row = check(o)[0]
    assert row["option_value_ids"] == [2002]
    assert row["selected_text_option_ids"] == [1001]


def test_hidden_conditional_personalization_must_not_be_sent():
    o = order([
        {"id": 1001, "value": ["مبروك"]},
        {"id": 1002, "value": ["2002"]},
        {"id": 1003, "value": ["نقش كان مخفيًا"]},
    ])
    with pytest.raises(CartUnverified, match="hidden_option_submitted"):
        check(o)


def test_visible_required_personalization_cannot_be_omitted():
    o = order([
        {"id": 1001, "value": ["هدية"]},
        {"id": 1002, "value": ["2001"]},
    ])
    with pytest.raises(CartUnverified, match="required_option_missing"):
        check(o)


def test_unconditional_required_personalization_cannot_be_omitted():
    o = order([
        {"id": 1002, "value": ["2001"]},
        {"id": 1003, "value": ["زخرفة"]},
    ])
    with pytest.raises(CartUnverified, match="required_option_missing"):
        check(o)


@pytest.mark.parametrize("bad_text", [
    "", "   ", "<script>", "سارة\nرنا", "أ" * 121, "ح" * 41,
])
def test_personalization_rejects_empty_unsafe_and_oversized_values(bad_text):
    # Empty input is already rejected by the strict PendingOrder model;
    # valid-shape but unsafe/oversized text is rejected at catalog preflight.
    with pytest.raises((ValidationError, CartUnverified)):
        o = order([
            {"id": 1001, "value": ["هدية عائلية"]},
            {"id": 1002, "value": ["2001"]},
            {"id": 1003, "value": [bad_text]},
        ])
        check(o)


def test_text_fallback_limit_is_conservative_when_salla_limit_null():
    o = order([
        {"id": 1001, "value": ["ب" * 121]},
        {"id": 1002, "value": ["2001"]},
        {"id": 1003, "value": ["نقش"]},
    ])
    with pytest.raises(CartUnverified, match="too_long"):
        check(o)


def test_conditional_option_unknown_reference_is_denied():
    p = source()
    p["options"][2]["visibility_condition_option"] = 999999
    with pytest.raises(CartUnverified, match="unsupported_conditional_option"):
        check(source_value=p)


def test_unsupported_visibility_operation_denied():
    p = source()
    p["options"][2]["visibility_condition_type"] = ">"
    with pytest.raises(CartUnverified, match="unsupported_conditional_option"):
        check(source_value=p)


def test_untrusted_date_option_requires_separate_adapter():
    p = source()
    p["options"][0]["type"] = "date"
    with pytest.raises(CartUnverified, match="unsupported_product_option"):
        check(source_value=p)


def test_user_personalization_preserved_in_create_order_payload():
    from salla_order_creation.payload import build_pending_order_payload
    raw = build_pending_order_payload(order())
    opts = raw["products"][0]["options"]
    assert opts[0] == {"id": 1001, "value": ["اسم تجريبي"]}
    assert opts[2] == {"id": 1003, "value": ["نقش إضافي"]}
    assert raw["payment"]["status"] == "pending_payment"
    assert "checkout_url" not in str(raw)
