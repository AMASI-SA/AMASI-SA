"""Offline purpose-signed bank receipt + bank quote, no paid/GL mutations."""

import json
from uuid import UUID

import pytest

from salla_order_creation.bank_receipt_action import verify_bank_receipt_action
from salla_order_creation.bank_vault_action import verify_bank_vault_prepare
from salla_order_creation.signature import SignatureDenied, sign_action

STAMP = "1800000000"
DELIVERY = str(UUID("12345678-1234-4234-8234-123456789abc"))
RECEIPT_KEY = b"synthetic-private-bank-receipt-vision-key-0001"
PREPARE_KEY = b"synthetic-private-bank-prepare-key-0001"


def receipt():
    return {
        "action": "salla.bank_receipt_preliminary_review",
        "account_id": 1, "inbox_id": 2, "conversation_id": 3,
        "intent_id": "intent-0001",
        "source_contact_id": 77, "source_message_id": 501,
        "attachment_id": 901, "image_sha256": "a" * 64,
        "observation": {
            "source": "approved_private_receipt_vision_v1",
            "image_sha256": "a" * 64,
            "document_kind": "bank_transfer_receipt",
            "text_legible": True,
            "transfer_marked_completed": True,
            "suspicious_editing_visible": False,
            "currency": "SAR", "amount": "200.00",
            "transfer_date": "2026-11-27",
            "receiving_store_bank_id": 123,
            "beneficiary_name_visible": "Synthetic Merchant",
            "transaction_reference_visible": "ABC1234500",
        },
    }


def prepare():
    return {
        "action": "salla.prepare_bank_pending_review",
        "account_id": 1, "inbox_id": 2, "conversation_id": 3,
        "intent_id": "intent-0001",
        "source_contact_id": 77, "source_message_id": 600,
        "receiver_country_code": "SA",
        "country_id": 1, "city_id": 2, "district_id": 3,
        "courier_id": 220, "selected_rate_id": 888,
        "products": [{"identifier_type": "id", "identifier": 400, "quantity": 1}],
    }


def signed(value, key, verifier):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return verifier(
        raw, STAMP, DELIVERY, sign_action(raw, STAMP, DELIVERY, key),
        key, now=1800000000,
    )


def test_valid_receipt_visual_observation_requires_private_service_hmac():
    action = signed(receipt(), RECEIPT_KEY, verify_bank_receipt_action)
    assert action.observation.amount == "200.00"
    assert action.observation.source == "approved_private_receipt_vision_v1"
    assert action.source_contact_id == 77
    assert "bank_credit_confirmed" not in action.model_dump()


def test_valid_bank_prepare_has_only_numeric_cart_and_source_scope():
    action = signed(prepare(), PREPARE_KEY, verify_bank_vault_prepare)
    assert action.action == "salla.prepare_bank_pending_review"
    assert action.products[0].identifier == 400
    assert "payment" not in action.model_dump()
    assert "customer" not in action.model_dump()
    assert "checkout_url" not in action.model_dump()


@pytest.mark.parametrize("extra", [
    {"payment": {"status": "paid"}},
    {"checkout_url": "https://evil.invalid"},
    {"bank_credit_confirmed": True},
    {"receipt_image_path": "https://evil.invalid/receipt"},
    {"store_bank_id": 999},
    {"salla_token": "synthetic"},
])
def test_receipt_observation_rejects_any_paid_or_financial_mutation(extra):
    with pytest.raises(SignatureDenied):
        signed({**receipt(), **extra}, RECEIPT_KEY, verify_bank_receipt_action)


@pytest.mark.parametrize("changed", [
    {"image_sha256": "b" * 64},
    {"source": "model_fake"},
])
def test_receipt_source_digest_or_private_vision_attestation_required(changed):
    value = receipt()
    value["observation"].update(changed)
    with pytest.raises(SignatureDenied):
        signed(value, RECEIPT_KEY, verify_bank_receipt_action)


@pytest.mark.parametrize("extra", [
    {"customer": {"name": "Synthetic"}},
    {"receiver": {"phone": "+966500000000"}},
    {"payment": {"status": "paid"}},
    {"checkout_url": "https://evil.invalid"},
    {"receipt_image_path": "https://evil.invalid/receipt"},
    {"bank_statement_confirmed": True},
    {"source_contact_id": True},
    {"source_message_id": 0},
])
def test_bank_prepare_never_accepts_customer_pii_or_early_payment(extra):
    with pytest.raises(SignatureDenied):
        signed({**prepare(), **extra}, PREPARE_KEY, verify_bank_vault_prepare)


def test_receipt_secret_cannot_sign_prepare_action():
    raw = json.dumps(prepare()).encode()
    with pytest.raises(SignatureDenied):
        verify_bank_vault_prepare(
            raw, STAMP, DELIVERY,
            sign_action(raw, STAMP, DELIVERY, RECEIPT_KEY),
            PREPARE_KEY, now=1800000000,
        )


def test_signed_receipt_tampering_must_fail():
    raw = json.dumps(receipt()).encode()
    bad = raw.replace(b"intent-0001", b"intent-0002")
    with pytest.raises(SignatureDenied):
        verify_bank_receipt_action(
            bad, STAMP, DELIVERY,
            sign_action(raw, STAMP, DELIVERY, RECEIPT_KEY),
            RECEIPT_KEY, now=1800000000,
        )
