"""Synthetic-only signature boundary tests; no sockets or real secrets."""

import json
from uuid import UUID

import pytest

from salla_order_creation.signature import (
    SignatureDenied, sign_action, verify_action,
)

SECRET = b"synthetic-test-secret-please-never-deploy-0001"
OTHER = b"synthetic-wrong-secret-please-never-deploy-0002"
STAMP = "1800000000"
DELIVERY = str(UUID("12345678-1234-4234-8234-123456789abc"))


def body():
    return {
        "action": "salla.create_pending_order",
        "account_id": 1, "inbox_id": 2, "conversation_id": 3,
        "intent_id": "intent-1234", "quote_ref": "quote.0001",
        "quote_digest": "b" * 64, "quote_version": 4,
        "confirmation_message_id": 501,
    }


def signed(raw):
    return sign_action(raw, STAMP, DELIVERY, SECRET)


def verify(raw, signature=None, **changes):
    return verify_action(
        raw, STAMP, DELIVERY,
        signed(raw) if signature is None else signature,
        SECRET, now=1800000000, **changes,
    )


def test_valid_signed_action_roundtrip():
    raw = json.dumps(body(), sort_keys=True).encode()
    checked = verify(raw)
    assert checked.action == "salla.create_pending_order"
    assert checked.confirmation_message_id == 501


def test_valid_signature_does_not_authorize_paid_mutation():
    unsafe = dict(body(), action="salla.mark_paid")
    raw = json.dumps(unsafe).encode()
    with pytest.raises(SignatureDenied):
        verify(raw)


@pytest.mark.parametrize("extra", [
    {"payment": {"status": "paid"}},
    {"checkout_url": "https://example.invalid"},
    {"owner_id": "other-tenant"},
    {"salla_token": "test"},
])
def test_extra_fields_rejected_even_if_message_is_signed(extra):
    raw = json.dumps({**body(), **extra}).encode()
    with pytest.raises(SignatureDenied):
        verify(raw)


def test_bad_signature_rejected():
    raw = json.dumps(body()).encode()
    with pytest.raises(SignatureDenied):
        verify(raw, signature=sign_action(raw, STAMP, DELIVERY, OTHER))


def test_changed_body_signature_rejected():
    raw = json.dumps(body()).encode()
    changed = raw.replace(b"quote.0001", b"quote.9999")
    with pytest.raises(SignatureDenied):
        verify(changed, signature=signed(raw))


def test_out_of_window_timestamp_rejected():
    raw = json.dumps(body()).encode()
    with pytest.raises(SignatureDenied):
        verify_action(raw, STAMP, DELIVERY, signed(raw), SECRET, now=1800000301)


def test_invalid_delivery_uuid_rejected():
    raw = json.dumps(body()).encode()
    with pytest.raises(SignatureDenied):
        verify_action(raw, STAMP, "not-a-uuid", signed(raw), SECRET, now=1800000000)


def test_duplicate_json_key_rejected():
    raw = b'{"action":"salla.create_pending_order","action":"salla.mark_paid"}'
    with pytest.raises(SignatureDenied):
        verify(raw)


def test_untrusted_types_rejected():
    data = body()
    data["account_id"] = True
    raw = json.dumps(data).encode()
    with pytest.raises(SignatureDenied):
        verify(raw)


def test_untrusted_reference_path_rejected():
    data = body()
    data["quote_ref"] = "../secret"
    raw = json.dumps(data).encode()
    with pytest.raises(SignatureDenied):
        verify(raw)


def test_missing_or_short_secret_rejected():
    raw = json.dumps(body()).encode()
    with pytest.raises(SignatureDenied):
        sign_action(raw, STAMP, DELIVERY, b"short")


def test_oversized_signed_action_rejected():
    raw = json.dumps(body()).encode() + b" " * 5000
    with pytest.raises(SignatureDenied):
        verify_action(raw, STAMP, DELIVERY, signed(raw), SECRET, now=1800000000)
