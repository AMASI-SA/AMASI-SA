"""Synthetic-only schema acceptance for Mezan→Salla pending orders."""

import copy

import pytest
from pydantic import ValidationError

from salla_order_creation.payload import PendingOrder, build_pending_order_payload


@pytest.fixture
def request_data():
    return {
        "customer": {"id": 123},
        "receiver": {"name": "Synthetic Recipient", "phone": "+966500000000", "country_code": "SA"},
        "branch_id": 100,
        "courier_id": 200,
        "ship_to": {"country": 1, "city": 2, "district": 3, "block": None,
                    "street_number": "10", "address_line": "Sample street",
                    "postal_code": "12345", "short_address": "ABCD1234",
                    "building_number": "10", "additional_number": "20",
                    "geo_coordinates": {"lat": 24.7, "lng": 46.7}},
        "accepted_methods": ["mada", "credit_card"],
        "products": [{"identifier_type": "id", "identifier": 400, "quantity": 2,
                      "options": [{"id": 500, "value": ["M"]}]}],
    }


def test_pending_order_uses_only_official_payment_schema(request_data):
    payload = build_pending_order_payload(PendingOrder.model_validate(request_data))
    assert payload["payment"] == {"status": "pending_payment", "accepted_methods": ["mada", "credit_card"]}
    assert payload["receiver"]["notify"] is False
    assert payload["products"] == [{"identifier_type": "id", "identifier": 400,
                                    "quantity": 2, "options": [{"id": 500, "value": ["M"]}]}]
    assert "checkout_url" not in str(payload)
    assert "price" not in str(payload)
    assert "total" not in str(payload)


@pytest.mark.parametrize("bad", [
    {"field": "payment", "value": {"status": "paid"}},
    {"field": "checkout_url", "value": "https://example.invalid/checkout"},
    {"field": "amount", "value": 10},
])
def test_rejects_untrusted_unsafe_order_fields(request_data, bad):
    data = copy.deepcopy(request_data)
    data[bad["field"]] = bad["value"]
    with pytest.raises(ValidationError):
        PendingOrder.model_validate(data)


def test_rejects_untrusted_payment_method(request_data):
    request_data["accepted_methods"] = ["not_a_method"]
    with pytest.raises(ValidationError):
        PendingOrder.model_validate(request_data)


def test_rejects_duplicate_methods(request_data):
    request_data["accepted_methods"] = ["mada", "mada"]
    with pytest.raises(ValidationError):
        PendingOrder.model_validate(request_data)


def test_rejects_negative_quantity(request_data):
    request_data["products"][0]["quantity"] = -1
    with pytest.raises(ValidationError):
        PendingOrder.model_validate(request_data)


def test_rejects_unverified_sku_shape(request_data):
    request_data["products"][0]["identifier_type"] = "sku"
    with pytest.raises(ValidationError):
        PendingOrder.model_validate(request_data)


def test_rejects_missing_address(request_data):
    del request_data["ship_to"]["postal_code"]
    with pytest.raises(ValidationError):
        PendingOrder.model_validate(request_data)


def test_rejects_duplicate_option(request_data):
    request_data["products"][0]["options"].append({"id": 500, "value": ["L"]})
    with pytest.raises(ValidationError):
        PendingOrder.model_validate(request_data)


def test_requires_complete_new_customer_identity(request_data):
    request_data["customer"] = {"name": "Amasi Guest", "mobile": "+966500000000"}
    with pytest.raises(ValidationError):
        PendingOrder.model_validate(request_data)


def test_accepts_new_customer_with_email(request_data):
    request_data["customer"] = {"name": "Amasi Guest", "mobile": "+966500000000", "email": "guest@example.invalid"}
    payload = build_pending_order_payload(PendingOrder.model_validate(request_data))
    assert payload["customer"]["email"] == "guest@example.invalid"


def test_supports_sku_with_no_option(request_data):
    request_data["products"] = [{"identifier_type": "sku", "identifier": "SKU-123", "quantity": 1}]
    payload = build_pending_order_payload(PendingOrder.model_validate(request_data))
    assert payload["products"] == [{"identifier_type": "sku", "identifier": "SKU-123", "quantity": 1}]


def test_requires_2026_sa_national_address(request_data):
    request_data["ship_to"]["short_address"] = None
    with pytest.raises(ValidationError):
        PendingOrder.model_validate(request_data)


def test_preserves_required_nullable_salla_address_keys(request_data):
    payload = build_pending_order_payload(PendingOrder.model_validate(request_data))
    assert "block" in payload["ship_to"] and payload["ship_to"]["block"] is None
    assert payload["ship_to"]["short_address"] == "ABCD1234"
    assert payload["courier_id"] == "200"


def test_non_sa_address_does_not_require_sa_extra_fields(request_data):
    request_data["receiver"]["country_code"] = "KW"
    request_data["ship_to"].update(
        short_address=None, building_number=None, additional_number=None,
        geo_coordinates=None,
    )
    payload = build_pending_order_payload(PendingOrder.model_validate(request_data))
    assert "short_address" not in payload["ship_to"]


def test_cannot_embed_paid_or_manual_checkout_url_in_address(request_data):
    request_data["ship_to"]["checkout_url"] = "https://example.invalid/pay"
    with pytest.raises(ValidationError):
        PendingOrder.model_validate(request_data)


def test_duplicate_product_option_values_are_rejected(request_data):
    request_data["products"][0]["options"][0]["value"] = [""]
    with pytest.raises(ValidationError):
        PendingOrder.model_validate(request_data)
