"""Synthetic-only bank receipt staging: no paid status, no raw IBAN."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from salla_order_creation.bank_receipt_review import ReceiptVisibleFields
from salla_order_creation.bank_receipt_stage import (
    stage_bank_receipt, BankReceiptStageDenied,
)

NOW=datetime(2026,11,27,14,0,tzinfo=timezone.utc)
SECRET=b"synthetic-bank-reference-hmac-key-for-offline-tests-0001"
IBAN="SA0380000000608010167519"


class Rows:
    def __init__(self):
        self.rows=[]

    async def insert_one(self,record):
        if any(item["_id"]==record["_id"] for item in self.rows):
            raise ValueError("duplicate")
        self.rows.append(dict(record))

    async def find_one(self,query):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                return dict(row)
        return None


class DB:
    def __init__(self):
        self.salla_ai_bank_receipt_refs=Rows()
        self.salla_ai_bank_receipt_evidence=Rows()


async def bank_reader(db,owner,bank_id):
    assert owner=="owner" and bank_id==11
    return {
        "source":"salla_store_payment_banks","bank_id":11,
        "status":"active","account_name":"مؤسسة أماسي الخليج التجارية",
        "iban":IBAN,
    }


def evidence(**changes):
    obj={
        "readable":True,"bank_transfer_shown":True,
        "appears_edited_or_cropped":False,"payer_bank_name_visible":True,
        "recipient_bank_name":"Synthetic Bank",
        "recipient_account_name":"مؤسسة أماسي الخليج التجارية",
        "recipient_iban":IBAN,"amount":"165.00","currency":"SAR",
        "transfer_reference":"SYNTHETIC-R0001",
        "timestamp":NOW-timedelta(minutes=5),
    }
    obj.update(changes)
    return ReceiptVisibleFields.model_validate(obj)


def stage(db=None,look=None,**changed):
    db=db or DB()
    opts={
        "user_id":"owner","intent_id":"intent-0001",
        "account_id":1,"inbox_id":2,"conversation_id":3,
        "contact_id":77,"message_id":501,"attachment_id":9001,
        "source_id_sha256":"a"*64,
        "selected_bank_id":11,"visible":look or evidence(),
        "reference_secret":SECRET,"bank_reader":bank_reader,
        "enabled":True,"now":NOW,
    }
    opts.update(changed)
    return db,asyncio.run(stage_bank_receipt(db,**opts))


def test_stage_only_provisional_bank_review():
    db,out=stage()
    assert out["state"]=="appearance_plausible"
    assert out["receipt_ref"].startswith("receipt.")
    assert out["receipt_amount_sar"]=="165.00"
    assert out["paid"] is False
    assert out["bank_credit_confirmed"] is False
    assert out["staff_review_required"] is True
    assert out["order_created"] is False
    record=db.salla_ai_bank_receipt_evidence.rows[0]
    assert record["credit_received"] is False
    assert record["source_attachment_id"]==9001
    assert "iban" not in str(record).lower()
    assert IBAN not in str(record)
    assert "SYNTHETIC-R0001" not in str(record)


def test_repeat_same_intent_and_receipt_is_idempotent():
    db=DB()
    first=stage(db)[1]
    second=stage(db)[1]
    assert first==second
    assert len(db.salla_ai_bank_receipt_evidence.rows)==1
    assert len(db.salla_ai_bank_receipt_refs.rows)==1


def test_same_reference_other_order_is_rejected():
    db=DB()
    stage(db)
    with pytest.raises(BankReceiptStageDenied,match="reference_already_claimed"):
        stage(db,intent_id="intent-0002")


def test_different_receipt_cannot_replace_original_intent():
    db=DB()
    stage(db)
    with pytest.raises(BankReceiptStageDenied,match="conflicting_receipt"):
        stage(db,look=evidence(transfer_reference="SYNTHETIC-R0002"),
              message_id=502,attachment_id=9002)


def test_bad_photo_asks_for_clearer_receipt_and_never_stages():
    db,out=stage(look=evidence(readable=False))
    assert out["state"]=="needs_customer_clarification"
    assert out["order_created"] is False
    assert db.salla_ai_bank_receipt_evidence.rows==[]
    assert db.salla_ai_bank_receipt_refs.rows==[]


def test_default_disabled_never_reads_bank_or_stores():
    db=DB()
    async def forbidden(*args):
        raise AssertionError("bank reader must not run")
    with pytest.raises(BankReceiptStageDenied,match="disabled"):
        stage(db,enabled=False,bank_reader=forbidden)
    assert db.salla_ai_bank_receipt_evidence.rows==[]


def test_foreign_chatwoot_contact_or_missing_image_metadata_denied():
    for problem in (
        {"contact_id":0},{"attachment_id":None},{"message_id":True},
        {"source_id_sha256":"not-a-sha"},
    ):
        with pytest.raises(BankReceiptStageDenied):
            stage(**problem)


def test_no_customer_receipt_credibility_claim_ever_confirms_money():
    db,out=stage()
    for check in (out,db.salla_ai_bank_receipt_evidence.rows[0]):
        assert check.get("paid") is not True
        assert check.get("credit_received") is not True
        assert check.get("bank_credit_confirmed") is not True
