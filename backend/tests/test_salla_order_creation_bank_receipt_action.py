"""Synthetic signed bank receipt appearance envelope is NOT payment proof."""

import json
from uuid import UUID
from datetime import datetime,timezone

import pytest

from salla_order_creation.bank_receipt_action import verify_bank_receipt_action
from salla_order_creation.signature import SignatureDenied, sign_action

STAMP="1800000000"
DELIVERY=str(UUID("12345678-1234-4234-8234-123456789abc"))
SECRET=b"synthetic-bank-receipt-hmac-secret-for-offline-tests-0001"


def payload(**modified):
    base={
        "action":"salla.stage_bank_receipt_appearance",
        "account_id":1,"inbox_id":2,"conversation_id":3,
        "intent_id":"intent-0001",
        "source_contact_id":77,"source_message_id":501,
        "source_attachment_id":9001,"source_id_sha256":"a"*64,
        "selected_bank_id":11,
        "visible":{
            "readable":True,"bank_transfer_shown":True,
            "appears_edited_or_cropped":False,
            "payer_bank_name_visible":True,
            "recipient_bank_name":"Synthetic Bank",
            "recipient_account_name":"Synthetic Store",
            "recipient_iban":"SA0380000000608010167519",
            "amount":"165.00","currency":"SAR",
            "transfer_reference":"SYNTHETIC-R001",
            "timestamp":"2026-11-27T14:00:00+00:00",
        },
    }
    base.update(modified)
    return base


def signed(body):
    raw=json.dumps(body,sort_keys=True,separators=(",",":")).encode()
    return verify_bank_receipt_action(
        raw,STAMP,DELIVERY,sign_action(raw,STAMP,DELIVERY,SECRET),
        SECRET,now=1800000000,
    )


def test_image_only_signed_appearance_fields_cannot_mark_paid():
    out=signed(payload())
    assert out.source_message_id==501
    assert out.source_attachment_id==9001
    assert out.visible.bank_transfer_shown is True
    assert out.visible.amount=="165.00"
    assert out.action=="salla.stage_bank_receipt_appearance"


@pytest.mark.parametrize("extra",[
    {"payment_status":"paid"},
    {"paid":True},
    {"bank_credit_confirmed":True},
    {"fulfillment_ready":True},
    {"checkout_url":"https://example.invalid"},
    {"salla_token":"test"},
    {"source_attachment_id":True},
    {"source_message_id":0},
    {"action":"salla.mark_paid"},
])
def test_purpose_locked_action_never_authorizes_payment_or_wrong_image(extra):
    with pytest.raises(SignatureDenied):
        signed(payload(**extra))


def test_visual_model_cannot_assert_transaction_authenticity():
    body=payload()
    body["visible"]["is_bank_settled"]=True
    with pytest.raises(SignatureDenied):
        signed(body)


def test_tampered_visible_amount_invalidates_hmac():
    body=payload()
    raw=json.dumps(body).encode()
    other=raw.replace(b"165.00",b"185.00")
    with pytest.raises(SignatureDenied):
        verify_bank_receipt_action(
            other,STAMP,DELIVERY,
            sign_action(raw,STAMP,DELIVERY,SECRET),
            SECRET,now=1800000000,
        )



@pytest.mark.parametrize("kind,attachment",[
    ("image",9001), ("file",9001), ("text",0),
])
def test_signed_receipt_accepts_original_source_kind_not_payment(kind,attachment):
    value=signed(payload(
        source_kind=kind,source_attachment_id=attachment,
    ))
    assert value.source_kind==kind
    assert value.source_attachment_id==attachment
    assert value.visible.bank_transfer_shown is True


@pytest.mark.parametrize("kind,attachment",[
    ("image",0), ("file",0), ("text",9001),
    ("video",9001), ("text",True),
])
def test_receipt_kind_cannot_be_forged_or_cross_attached(kind,attachment):
    with pytest.raises(SignatureDenied):
        signed(payload(source_kind=kind,source_attachment_id=attachment))
