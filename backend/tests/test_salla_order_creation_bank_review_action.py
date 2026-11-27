"""Signed bank receipt queue rejects AI payment, bank GL and cross-tenant claims."""

import json
from uuid import UUID

import pytest

from salla_order_creation.bank_review_action import verify_bank_receipt_review
from salla_order_creation.signature import SignatureDenied,sign_action

SECRET=b"synthetic-purpose-bank-queue-hmac-secret-000001"
STAMP="1800000000"
DELIVERY=str(UUID("12345678-1234-4234-8234-123456789abc"))


def action(**changes):
    observation={
        "account_id":1,"inbox_id":2,"conversation_id":3,
        "contact_id":77,"message_id":501,"attachment_id":601,
        "attachment_source_hash":"a"*64,
        "inspector_source":"trusted_private_receipt_inspector_v1",
        "attachment_kind":"image","legible":True,
        "appears_bank_receipt":True,"visible_currency":"SAR",
        "visible_amount_sar":"165.00",
        "transfer_date_iso":"2026-11-27",
        "transaction_reference_sha256":"b"*64,
        "recipient_bank_match":"match",
        "duplication_indicator":False,
        "screenshot_edit_warning":False,
    }
    data={
        "action":"salla.queue_bank_receipt_for_review",
        "account_id":1,"inbox_id":2,"conversation_id":3,
        "intent_id":"intent-0001","observation":observation,
    }
    data.update(changes)
    return data


def signed(value):
    raw=json.dumps(value,sort_keys=True,separators=(",",":")).encode()
    return verify_bank_receipt_review(
        raw,STAMP,DELIVERY,sign_action(raw,STAMP,DELIVERY,SECRET),
        SECRET,now=1800000000,
    )


def test_private_inspector_evidence_can_only_be_queued_for_review():
    result=signed(action())
    assert result.action=="salla.queue_bank_receipt_for_review"
    assert result.observation.contact_id==77
    assert result.observation.attachment_id==601


@pytest.mark.parametrize("bad",[
    {"action":"salla.mark_paid"},
    {"status":"paid"},
    {"payment":{"status":"paid"}},
    {"gl_posted":True},
    {"checkout_url":"https://evil.invalid"},
    {"recipient_ib an":"SA0000000000000000000000"},
    {"bank_statement_amount":"999"},
])
def test_signed_receipt_review_cannot_claim_paid_status_or_bank_data(bad):
    with pytest.raises(SignatureDenied):
        signed(action(**bad))


def test_cross_conversation_receipt_cannot_be_queued():
    data=action()
    data["observation"]["conversation_id"]=99
    with pytest.raises(SignatureDenied,match="scope_mismatch"):
        signed(data)


def test_fake_inspector_source_rejected():
    data=action()
    data["observation"]["inspector_source"]="raw_model_text"
    with pytest.raises(SignatureDenied):
        signed(data)


def test_tampered_bank_amount_does_not_pass_mac():
    data=action()
    raw=json.dumps(data).encode()
    edited=raw.replace(b"165.00",b"999.00")
    with pytest.raises(SignatureDenied):
        verify_bank_receipt_review(
            edited,STAMP,DELIVERY,
            sign_action(raw,STAMP,DELIVERY,SECRET),
            SECRET,now=1800000000,
        )
