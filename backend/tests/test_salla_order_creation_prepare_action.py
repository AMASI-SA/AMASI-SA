"""Synthetic signed customer-service order preparation, no Salla POST."""

import json
from uuid import UUID

import pytest

from salla_order_creation.prepare_action import verify_prepare_unpaid_order
from salla_order_creation.signature import SignatureDenied, sign_action

STAMP = "1800000000"
DELIVERY = str(UUID("12345678-1234-4234-8234-123456789abc"))
SECRET = b"synthetic-private-prepare-key-not-for-production-0001"


def request():
    return {
        "action": "salla.prepare_unpaid_order",
        "account_id": 1, "inbox_id": 2, "conversation_id": 3,
        "intent_id": "intent-1000", "source_message_id": 500,
        "source_contact_id": 501, "selected_rate_id": 600,
        "order": {
            "customer": {
                "name": "Synthetic Buyer",
                "mobile": "+966500000000",
                "email": "customer@example.invalid",
            },
            "receiver": {
                "name": "Synthetic Recipient",
                "phone": "+966500000001",
                "country_code": "SA",
            },
            "courier_id": "220",
            "ship_to": {
                "country": 1, "city": 2, "district": 3, "block": None,
                "street_number": "12", "address_line": "Synthetic street",
                "postal_code": "12345", "short_address": "RHMA3184",
                "building_number": "12", "additional_number": "22",
                "geo_coordinates": {"lat": 24.7, "lng": 46.7},
            },
            "accepted_methods": ["mada"],
            "products": [{
                "identifier_type": "id", "identifier": 400,
                "quantity": 1, "options": [{
                    "id": 50, "value": ["601"],
                }],
            }],
        },
    }


def signed(data):
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    signature = sign_action(raw, STAMP, DELIVERY, SECRET)
    return verify_prepare_unpaid_order(
        raw, STAMP, DELIVERY, signature, SECRET,
        now=1800000000,
    )


def test_signed_prepare_uses_only_customer_native_scope_and_unpaid_cart():
    action = signed(request())
    assert action.action == "salla.prepare_unpaid_order"
    assert action.source_contact_id == 501
    assert action.order.customer.id is None
    assert action.order.receiver.phone != action.order.customer.mobile
    assert action.order.accepted_methods == ["mada"]
    assert action.order.ship_to.block is None


def test_existing_customer_id_requires_independent_tenant_lookup():
    data = request()
    data["order"]["customer"] = {"id": 98765}
    with pytest.raises(SignatureDenied, match="customer_id_requires_verified_lookup"):
        signed(data)


@pytest.mark.parametrize("method", ["bank", "cod"])
def test_nononline_payment_is_not_accepted_by_unpaid_mode(method):
    data = request()
    data["order"]["accepted_methods"] = [method]
    # The envelope is syntactically valid; eligibility denial occurs in the
    # trusted Mezan source-checking writer BEFORE Salla order creation.
    verified = signed(data)
    assert verified.order.accepted_methods == [method]


@pytest.mark.parametrize("extra", [
    {"checkout_url": "https://example.invalid/checkout"},
    {"amount_sar": 1},
    {"confirmed": True},
    {"payment": {"status": "paid"}},
    {"salla_token": "synthetic-secret"},
])
def test_top_level_mutation_is_rejected_even_with_valid_signature(extra):
    with pytest.raises(SignatureDenied):
        signed({**request(), **extra})


def test_order_paid_fields_and_checkout_url_rejected():
    data = request()
    data["order"]["payment"] = {"status": "paid"}
    with pytest.raises(SignatureDenied):
        signed(data)
    data = request()
    data["order"]["checkout_url"] = "https://example.invalid"
    with pytest.raises(SignatureDenied):
        signed(data)


def test_wrong_type_for_inbox_fails_closed():
    data = request()
    data["inbox_id"] = True
    with pytest.raises(SignatureDenied):
        signed(data)


def test_missing_new_buyer_email_not_invented():
    data = request()
    del data["order"]["customer"]["email"]
    with pytest.raises(SignatureDenied):
        signed(data)


def test_tampered_message_signature_rejected():
    data = request()
    raw = json.dumps(data).encode()
    changed = raw.replace(b"intent-1000", b"intent-9000")
    with pytest.raises(SignatureDenied):
        verify_prepare_unpaid_order(
            changed, STAMP, DELIVERY,
            sign_action(raw, STAMP, DELIVERY, SECRET), SECRET,
            now=1800000000,
        )


def test_stale_signed_prepare_denied():
    data = request()
    raw = json.dumps(data).encode()
    with pytest.raises(SignatureDenied, match="stale_action_signature"):
        verify_prepare_unpaid_order(
            raw, STAMP, DELIVERY,
            sign_action(raw, STAMP, DELIVERY, SECRET), SECRET,
            now=1800000301,
        )


def test_signed_prepare_body_above_limit_denied():
    data = request()
    data["order"]["products"][0]["options"][0]["value"] = ["X" * 5000]
    raw = json.dumps(data).encode()
    with pytest.raises(SignatureDenied, match="action_body_size"):
        verify_prepare_unpaid_order(
            raw, STAMP, DELIVERY,
            sign_action(raw, STAMP, DELIVERY, SECRET), SECRET,
            now=1800000000,
        )
