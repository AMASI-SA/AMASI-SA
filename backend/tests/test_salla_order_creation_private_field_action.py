"""Synthetic only: signed private Chatwoot field envelope is not a Salla order."""

import json
from uuid import UUID

import pytest

from salla_order_creation.private_field_action import verify_private_field
from salla_order_creation.signature import SignatureDenied, sign_action

STAMP="1800000000"
DELIVERY=str(UUID("12345678-1234-4234-8234-123456789abc"))
SECRET=b"synthetic-private-fields-only-hmac-secret-00001"


def fields(**changes):
    base={
        "action":"salla.store_private_order_field",
        "account_id":1,"inbox_id":2,"conversation_id":3,
        "intent_id":"intent-0001",
        "source_message_id":501,"source_contact_id":77,
        "source_text_sha256":"b"*64,
        "field":"recipient_phone","value":"0500000000",
    }
    base.update(changes)
    return base


def signed(data):
    raw=json.dumps(data,sort_keys=True,separators=(",",":")).encode()
    return verify_private_field(
        raw,STAMP,DELIVERY,sign_action(raw,STAMP,DELIVERY,SECRET),
        SECRET,now=1800000000,
    )


def test_only_one_purpose_bound_private_field():
    out=signed(fields())
    assert out.source_message_id==501
    assert out.source_contact_id==77
    assert out.field=="recipient_phone"
    assert out.value=="0500000000"


@pytest.mark.parametrize("malicious",[
    {"action":"salla.create_pending_order"},
    {"action":"salla.mark_paid"},
    {"payment":{"status":"paid"}},
    {"salla_token":"synthetic-secret"},
    {"checkout_url":"https://evil.invalid"},
    {"total_sar":"1.00"},
    {"customer_id":999999},
    {"field":"payment_card_number"},
    {"source_message_id":0},
    {"source_contact_id":True},
    {"source_text_sha256":"x"*64},
])
def test_model_fields_paid_actions_and_wrong_origin_rejected(malicious):
    with pytest.raises(SignatureDenied):
        signed(fields(**malicious))


def test_tampered_field_value_rejected_by_mac():
    raw=json.dumps(fields()).encode()
    changed=raw.replace(b"0500000000",b"0555555555")
    with pytest.raises(SignatureDenied):
        verify_private_field(
            changed,STAMP,DELIVERY,
            sign_action(raw,STAMP,DELIVERY,SECRET),
            SECRET,now=1800000000,
        )


def test_bad_timestamp_or_wrong_purpose_secret_fails():
    raw=json.dumps(fields()).encode()
    with pytest.raises(SignatureDenied):
        verify_private_field(
            raw,STAMP,DELIVERY,
            sign_action(raw,STAMP,DELIVERY,SECRET),
            SECRET,now=1800000310,
        )


def test_empty_customer_field_denied():
    with pytest.raises(SignatureDenied):
        signed(fields(value=""))



def test_signed_personalized_option_field_is_accepted_only_for_numeric_id():
    signed_value=signed(fields(field="option_text_501",value="اسم هدية خاص"))
    assert signed_value.field=="option_text_501"
    with pytest.raises(SignatureDenied):
        signed(fields(field="option_text_abc",value="اسم هدية خاص"))


def test_signed_private_field_does_not_authorize_plaintext_checkout():
    with pytest.raises(SignatureDenied):
        signed(fields(field="checkout_url",value="https://evil.invalid"))
