"""Synthetic-only signed receipt and verified Salla receiving bank."""

import asyncio
import json
from uuid import UUID

import pytest

from salla_order_creation.bank_source import (
    ReceivingBankUnavailable,get_authorized_receiving_bank,
)
from salla_order_creation.bank_receipt_action import (
    verify_bank_receipt_screen,as_visual_evidence,
)
from salla_order_creation.signature import SignatureDenied,sign_action

STAMP="1800000000"
DELIVERY=str(UUID("12345678-1234-4234-8234-123456789abc"))
SECRET=b"synthetic-private-bank-vision-hmac-secret-0001"


def bank_reply(id=4321,iban="SA0380000000608010161234",status="active"):
    return {"success":True,"data":{
        "id":id,"iban_number":iban,"status":status,
    }}


async def fake_bank(db,uid,method,path):
    assert uid=="owner"
    assert method=="GET" and path=="/payment/banks/4321"
    return bank_reply()


def screen(**updates):
    d={
        "action":"salla.screen_bank_transfer_receipt",
        "account_id":1,"inbox_id":2,"conversation_id":3,
        "contact_id":77,"source_message_id":501,
        "source_attachment_id":9001,"intent_id":"intent-0001",
        "store_bank_id":4321,
        "looks_like_bank_transfer_receipt":True,
        "text_legible":True,"amount_sar":"150.00","currency":"SAR",
        "beneficiary_iban_last4":"1234",
        "source_image_sha256":"a"*64,
        "bank_reference_sha256":"b"*64,
        "transfer_at_iso":"2026-11-27T02:00:00+00:00",
        "flags":[],
        "visual_source":"approved_private_receipt_vision_v1",
    }
    d.update(updates)
    return d


def signed(row):
    raw=json.dumps(row,separators=(",",":")).encode()
    mac=sign_action(raw,STAMP,DELIVERY,SECRET)
    return verify_bank_receipt_screen(
        raw,STAMP,DELIVERY,mac,SECRET,now=1800000000,
    )


def test_approved_store_bank_fetched_by_exact_id_and_iban():
    verified=asyncio.run(get_authorized_receiving_bank(
        None,user_id="owner",store_bank_id=4321,
        request=fake_bank,
    ))
    assert verified.active is True
    assert verified.iban_last4=="1234"
    assert verified.source=="salla_active_merchant_bank"


@pytest.mark.parametrize("bad",[
    bank_reply(id=9999),bank_reply(status="disabled"),
    bank_reply(iban="unreadable"),
    {"success":False,"data":bank_reply()["data"]},
])
def test_bank_source_must_be_exact_active_salla_account(bad):
    async def get(*args):
        return bad
    with pytest.raises(ReceivingBankUnavailable):
        asyncio.run(get_authorized_receiving_bank(
            None,user_id="owner",store_bank_id=4321,request=get,
        ))


def test_signed_visual_receipt_only_claims_appearance_not_payment():
    receipt=as_visual_evidence(signed(screen()))
    assert receipt.amount_sar=="150.00"
    assert receipt.source_image_sha256=="a"*64
    assert receipt.source=="approved_private_receipt_vision_v1"
    assert not hasattr(receipt,"paid")


@pytest.mark.parametrize("bad",[
    {"action":"salla.mark_paid"},
    {"bank_credit_verified":True},
    {"paid":True},
    {"receipt_image_path":"https://evil.invalid"},
    {"attachment_bytes":"data:image/png;base64,evil"},
    {"bank_password":"secret"},
    {"source_message_id":0},
    {"store_bank_id":True},
    {"visual_source":"model_self_assertion"},
    {"source_image_sha256":"notsha"},
])
def test_bank_screenshot_signed_action_cannot_set_paid_or_inject_media(bad):
    with pytest.raises(SignatureDenied):
        signed(screen(**bad))


def test_wrong_mac_and_wrong_clock_are_denied():
    raw=json.dumps(screen()).encode()
    with pytest.raises(SignatureDenied):
        verify_bank_receipt_screen(
            raw.replace(b"150.00",b"999.00"),STAMP,DELIVERY,
            sign_action(raw,STAMP,DELIVERY,SECRET),
            SECRET,now=1800000000,
        )
    with pytest.raises(SignatureDenied):
        verify_bank_receipt_screen(
            raw,STAMP,DELIVERY,sign_action(raw,STAMP,DELIVERY,SECRET),
            SECRET,now=1800000350,
        )


def test_missing_timezone_fails_visual_timestamp_validation():
    row=signed(screen(transfer_at_iso="2026-11-27T02:00:00"))
    with pytest.raises(SignatureDenied,match="timezone_required"):
        as_visual_evidence(row)
