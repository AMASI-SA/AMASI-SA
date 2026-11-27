"""Synthetic-only bank receipt appearance, duplicate and human review gates."""

import asyncio
from datetime import datetime,timedelta,timezone
from dataclasses import replace
from types import SimpleNamespace

import pytest

from salla_order_creation.bank_receipt import (
    MerchantBankFact, ReceiptVisionEvidence, ReceiptAppearanceDenied,
    validate_receipt_appearance,
)
from salla_order_creation.bank_claims import (
    BankReviewDenied,SCREENSHOT_UNIQUE,REVIEW_TTL,
    register_bank_receipt_claim,get_claim_for_order,
)

NOW=datetime(2026,11,27,2,0,0,tzinfo=timezone.utc)


def bank():
    return MerchantBankFact(
        user_id="owner",store_bank_id=4321,iban_last4="1234",
        active=True,source="salla_active_merchant_bank",
    )


def visual(**updates):
    sample=ReceiptVisionEvidence(
        source="approved_private_receipt_vision_v1",
        looks_like_bank_transfer_receipt=True,text_legible=True,
        amount_sar="150.00",currency="SAR",beneficiary_bank_id=4321,
        beneficiary_iban_last4="1234",
        transfer_at_utc=NOW-timedelta(hours=1),
        source_image_sha256="a"*64,bank_reference_sha256="b"*64,
    )
    return replace(sample,**updates)


class Collection:
    def __init__(self,valid_indexes=True):
        self.indexes={
            SCREENSHOT_UNIQUE:{
                "key":[("user_id",1),("image_sha256",1)],"unique":True,
            },
            REVIEW_TTL:{"key":[("purge_after",1)],"expireAfterSeconds":0},
        } if valid_indexes else {}
        self.rows=[]

    async def index_information(self):
        return self.indexes

    async def find_one(self,query):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                return dict(row)
        return None

    async def insert_one(self,row):
        if any(x["_id"]==row["_id"] or (
            x["user_id"]==row["user_id"] and
            x["image_sha256"]==row["image_sha256"]
        ) for x in self.rows):
            raise ValueError("synthetic unique index")
        self.rows.append(dict(row))


class DB:
    def __init__(self,valid_indexes=True):
        self.salla_ai_bank_receipt_reviews=Collection(valid_indexes)


def put(db=None,receipt=None,**args):
    data={
        "user_id":"owner","intent_id":"intent-0001",
        "account_id":1,"inbox_id":2,"conversation_id":3,
        "contact_id":77,"message_id":501,"attachment_id":9001,
        "bank":bank(),"visual":receipt or visual(),"now":NOW,
    }
    data.update(args)
    return asyncio.run(register_bank_receipt_claim(db or DB(),**data))


def test_visual_looks_plausible_but_never_proves_money_received():
    claim=validate_receipt_appearance(
        visual(),merchant_id="owner",verified_bank=bank(),now=NOW,
    )
    assert claim["appearance"]=="plausible_for_staff_review"
    assert claim["receipt_amount_sar"]=="150.00"
    assert claim["bank_credit_verified"] is False
    assert claim["eligible_to_mark_paid"] is False
    assert claim["eligible_for_fulfillment"] is False
    assert claim["human_bank_statement_check_required"] is True


@pytest.mark.parametrize("bad,expected",[
    ({"looks_like_bank_transfer_receipt":False},"receipt_not_visually_clear"),
    ({"text_legible":False},"receipt_not_visually_clear"),
    ({"currency":"USD"},"receipt_currency_not_sar"),
    ({"beneficiary_bank_id":4322},"recipient_bank_does_not_match_store"),
    ({"beneficiary_iban_last4":"9999"},"recipient_iban_does_not_match_store"),
    ({"source_image_sha256":"nothex"},"receipt_image_or_reference_unverified"),
    ({"bank_reference_sha256":""},"receipt_image_or_reference_unverified"),
    ({"amount_sar":"0.00"},"receipt_amount_invalid"),
    ({"amount_sar":"NaN"},"receipt_amount_invalid"),
    ({"flags":("possible_edit",)},"receipt_requires_clearer_image_or_staff"),
    ({"transfer_at_utc":NOW+timedelta(days=1)},"receipt_date_outside_allowed_window"),
    ({"transfer_at_utc":NOW-timedelta(days=40)},"receipt_date_outside_allowed_window"),
])
def test_bad_or_suspicious_receipt_never_passes_visual_screen(bad,expected):
    with pytest.raises(ReceiptAppearanceDenied,match=expected):
        validate_receipt_appearance(
            visual(**bad),merchant_id="owner",verified_bank=bank(),now=NOW,
        )


def test_unapproved_bank_or_other_merchant_cannot_validate_receipt():
    with pytest.raises(ReceiptAppearanceDenied,match="verified_receiving_bank_required"):
        validate_receipt_appearance(
            visual(),merchant_id="owner",
            verified_bank=replace(bank(),user_id="other"),
            now=NOW,
        )
    with pytest.raises(ReceiptAppearanceDenied):
        validate_receipt_appearance(
            visual(),merchant_id="owner",
            verified_bank=replace(bank(),active=False),
            now=NOW,
        )


def test_claim_contains_no_receipt_bytes_full_iban_or_bank_credit():
    db=DB()
    row=put(db)
    assert row["state"]=="awaiting_order_creation"
    assert row["bank_credit_verified"] is False
    stored=db.salla_ai_bank_receipt_reviews.rows[0]
    assert stored["source_message_id"]==501
    assert stored["source_attachment_id"]==9001
    assert stored["receipt_amount_sar"]=="150.00"
    assert stored["bank_reference_sha256"]=="b"*64
    assert stored["human_bank_credit_verified"] is False
    assert stored["payment_marked_paid"] is False
    assert stored["fulfillment_released"] is False
    for dangerous in ("receipt_image_bytes","public_receipt_url",
                      "iban_number","password","paid_at","bank_transaction"):
        assert dangerous not in stored


def test_replay_exact_claim_returns_same_reference_no_duplicate_review():
    db=DB()
    a=put(db)
    b=put(db)
    assert a["review_ref"]==b["review_ref"]
    assert b["replayed"] is True
    assert len(db.salla_ai_bank_receipt_reviews.rows)==1


def test_duplicate_screenshot_on_another_order_intent_denied():
    db=DB()
    put(db)
    with pytest.raises(BankReviewDenied,match="duplicate"):
        put(db,intent_id="intent-0002")
    assert len(db.salla_ai_bank_receipt_reviews.rows)==1


def test_changed_photo_or_customer_same_intent_denied():
    db=DB()
    put(db)
    with pytest.raises(BankReviewDenied,match="intent_conflict"):
        put(db,receipt=visual(source_image_sha256="c"*64))
    with pytest.raises(BankReviewDenied,match="intent_conflict"):
        put(db,contact_id=78)


def test_missing_operator_indexes_blocks_receipt_storage():
    db=DB(valid_indexes=False)
    with pytest.raises(BankReviewDenied,match="duplicate_guard_missing"):
        put(db)
    assert db.salla_ai_bank_receipt_reviews.rows==[]


def test_claim_is_scope_readable_for_original_chat_contact_only():
    db=DB()
    claim=put(db)
    async def read(contact=77,inbox="2"):
        return await get_claim_for_order(
            db,user_id="owner",intent_id="intent-0001",
            review_ref=claim["review_ref"],
            inbox_id=inbox,conversation_id="3",contact_id=contact,
        )
    assert asyncio.run(read())["store_bank_id"]==4321
    for other_contact,other_inbox in ((78,"2"),(77,"9")):
        with pytest.raises(BankReviewDenied):
            asyncio.run(read(other_contact,other_inbox))
