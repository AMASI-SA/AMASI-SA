"""Synthetic appearance-only bank receipt checks; NEVER payment verification."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from salla_order_creation.bank_receipt_review import (
    PrivateReceiptObservation,
    ReceiptReviewDenied,
    assess_bank_receipt_appearance,
)
from salla_order_creation.bank_review_queue import (
    BankReviewStateError, enqueue_bank_receipt_review,
)
from salla_order_creation.service import intent_storage_id

NOW=datetime(2026,11,27,12,0,tzinfo=timezone.utc)


def observation(**changes):
    values={
        "account_id":1,"inbox_id":2,"conversation_id":3,"contact_id":77,
        "message_id":501,"attachment_id":601,
        "attachment_source_hash":"a"*64,
        "inspector_source":"trusted_private_receipt_inspector_v1",
        "attachment_kind":"image","legible":True,
        "appears_bank_receipt":True,"visible_currency":"SAR",
        "visible_amount_sar":"165.00","transfer_date_iso":"2026-11-27",
        "transaction_reference_sha256":"b"*64,
        "recipient_bank_match":"match",
        "duplication_indicator":False,"screenshot_edit_warning":False,
    }
    values.update(changes)
    return PrivateReceiptObservation.model_validate(values)


def test_clean_visible_receipt_requires_human_bank_settlement_confirmation():
    row=assess_bank_receipt_appearance(
        observation(),expected_total_sar="165.00",now=NOW,
    )
    assert row["appearance"]=="looks_consistent"
    assert row["review_required"] is True
    assert row["payment_confirmed"] is False
    assert row["bank_settlement_confirmed"] is False
    assert row["may_post_gl"] is False
    assert row["may_mark_salla_paid"] is False
    assert "IBAN" not in str(row)


@pytest.mark.parametrize("change,reason",[
    ({"legible":False},"receipt_not_legible"),
    ({"appears_bank_receipt":False},"not_recognizable_bank_receipt"),
    ({"visible_currency":"other"},"receipt_currency_not_sar"),
    ({"visible_amount_sar":"164.98"},"receipt_amount_not_matching_order"),
    ({"visible_amount_sar":"NaN"},"receipt_amount_not_matching_order"),
    ({"recipient_bank_match":"mismatch"},"bank_recipient_not_verified"),
    ({"recipient_bank_match":"unverified"},"bank_recipient_not_verified"),
    ({"transaction_reference_sha256":None},"transaction_reference_missing"),
    ({"transfer_date_iso":"2026-01-01"},"transfer_date_outside_review_window"),
    ({"transfer_date_iso":"2026-12-15"},"transfer_date_outside_review_window"),
    ({"duplication_indicator":True},"possible_reused_receipt"),
    ({"screenshot_edit_warning":True},"image_integrity_warning"),
])
def test_appearance_anomalies_only_escalate_to_human(change,reason):
    row=assess_bank_receipt_appearance(
        observation(**change),expected_total_sar="165.00",now=NOW,
    )
    assert row["appearance"]=="needs_clarification"
    assert reason in row["issues"]
    assert row["payment_confirmed"] is False


def test_salla_total_not_yet_known_is_not_trusted_or_invented():
    row=assess_bank_receipt_appearance(
        observation(),expected_total_sar=None,now=NOW,
    )
    assert "salla_final_amount_not_yet_synced" in row["issues"]
    assert row["payment_confirmed"] is False


def test_invalid_source_inspector_is_denied_before_any_bank_decision():
    with pytest.raises(ValueError):
        observation(inspector_source="model_utterance")
    with pytest.raises(ValueError):
        observation(attachment_source_hash="unverified")


class Rows:
    def __init__(self,rows=None):
        self.rows=list(rows or [])

    async def find_one(self,query):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                return dict(row)
        return None

    async def insert_one(self,row):
        if any(x["_id"]==row["_id"] for x in self.rows):
            raise ValueError("synthetic duplicate _id")
        self.rows.append(dict(row))


class DB:
    def __init__(self):
        self.salla_ai_order_intents=Rows([{
            "_id":intent_storage_id("owner","intent-0001"),
            "user_id":"owner","intent_id":"intent-0001",
            "inbox_id":"2","conversation_id":"3",
            "state":"created_amount_unverified",
            "pricing_mode":"salla_bank_review",
            "salla_order_id":9876,"reference_id":302076044,
            "payload_digest":"c"*64,
        }])
        self.salla_ai_order_quotes=Rows([{
            "user_id":"owner","intent_id":"intent-0001",
            "inbox_id":"2","conversation_id":"3",
            "source":"mezan_verified_bank_review_intent_v1",
            "state":"authorized_for_bank_review_creation",
            "post_create_salla_total":True,
            "bank_review_required":True,"payment_claim_confirmed":False,
            "online_only":False,"pricing_verified":False,
            "shipping_price_verified":False,
            "total_sar":None,"products_verified":True,
            "carrier_eligibility_verified":True,
            "payment_methods_verified":True,
            "source_contact_id":77,"payload_digest":"c"*64,
        }])
        self.salla_ai_bank_receipt_reviews=Rows()


def enqueue(db=None,source=None,**args):
    db=db or DB()
    values={
        "owner_id":"owner","intent_id":"intent-0001",
        "account_id":1,"inbox_id":2,"conversation_id":3,
        "contact_id":77,
        "observation":source or observation(),
        "order_total_sar":"165.00","now":NOW,
    }
    values.update(args)
    return db,asyncio.run(enqueue_bank_receipt_review(db,**values))


def test_bank_receipt_appears_consistent_but_payment_stays_pending():
    db,result=enqueue()
    assert result["state"]=="awaiting_human_bank_confirmation"
    assert result["payment_confirmed"] is False
    assert result["appearance"]=="looks_consistent"
    row=db.salla_ai_bank_receipt_reviews.rows[0]
    assert row["status"]=="awaiting_human_bank_confirmation"
    assert row["bank_settlement_confirmed"] is False
    assert row["salla_paid"] is False
    assert row["gl_posted"] is False
    assert row["attachment_id"]==601
    assert "receipt_image_url" not in row
    assert "iban" not in str(row).lower()


def test_duplicate_message_is_idempotent_and_does_not_touch_human_review():
    db=DB()
    _,a=enqueue(db=db)
    db.salla_ai_bank_receipt_reviews.rows[0]["status"]="human_reviewed"
    _,b=enqueue(db=db)
    assert a["replayed"] is False
    assert b["replayed"] is True
    assert b["state"]=="human_reviewed"
    assert len(db.salla_ai_bank_receipt_reviews.rows)==1


def test_wrong_chatwoot_contact_cannot_attach_receipt_to_other_customer():
    db=DB()
    with pytest.raises(BankReviewStateError,match="context_required"):
        enqueue(db=db,source=observation(contact_id=99))
    assert db.salla_ai_bank_receipt_reviews.rows==[]


def test_unknown_or_nonbank_salla_order_cannot_be_queued():
    db=DB()
    db.salla_ai_order_intents.rows[0]["pricing_mode"]="salla_post_create"
    with pytest.raises(BankReviewStateError,match="confirmed_unpaid_bank_order"):
        enqueue(db=db)
    assert db.salla_ai_bank_receipt_reviews.rows==[]


def test_bank_quote_digest_or_contact_mismatch_denied():
    db=DB()
    db.salla_ai_order_quotes.rows[0]["source_contact_id"]=99
    with pytest.raises(BankReviewStateError,match="bank_quote_customer_mismatch"):
        enqueue(db=db)
    assert db.salla_ai_bank_receipt_reviews.rows==[]


def test_receipt_amount_difference_is_recorded_for_staff_not_paid():
    db,result=enqueue(source=observation(visible_amount_sar="100.00"))
    assert result["appearance"]=="needs_clarification"
    assert "receipt_amount_not_matching_order" in result["issues"]
    assert db.salla_ai_bank_receipt_reviews.rows[0]["status"]=="awaiting_human_bank_confirmation"
