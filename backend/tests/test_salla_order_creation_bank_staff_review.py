"""Synthetic bank staff case: image evidence is never bank credit."""

import asyncio
from datetime import datetime, timezone

import pytest

from salla_order_creation.bank_staff_review import (
    BankReviewDenied, assess_bank_order_total, ensure_staff_bank_review,
)

NOW=datetime(2026,11,27,15,0,tzinfo=timezone.utc)


class Reviews:
    def __init__(self):
        self.rows=[]

    async def insert_one(self,data):
        if any(row["_id"]==data["_id"] for row in self.rows):
            raise ValueError("duplicate synthetic")
        self.rows.append(dict(data))

    async def find_one(self,query):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                return dict(row)
        return None


class DB:
    def __init__(self):
        self.salla_ai_bank_staff_reviews=Reviews()


def receipt():
    return {
        "user_id":"owner","intent_id":"intent-0001",
        "inbox_id":"2","conversation_id":"3",
        "source_contact_id":77,"source_message_id":501,
        "source_attachment_id":9001,"bank_id":11,
        "reference_fingerprint":"a"*64,
        "state":"appearance_plausible",
        "credit_received":False,"receipt_amount_sar":"165.00",
    }


def stage(db=None,**override):
    db=db or DB()
    data={
        "user_id":"owner","intent_id":"intent-0001",
        "salla_order_id":90001,"reference_id":302076044,
        "receipt":receipt(),"now":NOW,
    }
    data.update(override)
    return db,asyncio.run(ensure_staff_bank_review(db,**data))


def test_created_order_has_one_unpaid_staff_review_case():
    db,result=stage()
    assert result["state"]=="awaiting_staff_bank_verification"
    assert result["staff_review_required"] is True
    assert result["bank_credit_confirmed"] is False
    row=db.salla_ai_bank_staff_reviews.rows[0]
    assert row["salla_order_id"]==90001
    assert row["source_message_id"]==501
    assert row["source_attachment_id"]==9001
    assert row["paid"] is False
    assert row["fulfillment_eligible"] is False
    assert row["receipt_appearance_only"] is True
    assert "iban" not in str(row).lower()


def test_case_duplicate_ack_is_read_only_idempotent():
    db=DB()
    first=stage(db)[1]
    second=stage(db)[1]
    assert second["replayed"] is True
    assert len(db.salla_ai_bank_staff_reviews.rows)==1
    assert first["salla_order_id"]==second["salla_order_id"]


def test_wrong_order_id_cannot_attach_existing_receipt():
    db=DB()
    stage(db)
    with pytest.raises(BankReviewDenied,match="intent_conflict"):
        stage(db,salla_order_id=90002)


def test_actual_amount_and_status_still_require_staff_bank_confirmation():
    db,_=stage()
    result=assess_bank_order_total(
        db.salla_ai_bank_staff_reviews.rows[0],
        actual_total_sar="165.00",actual_currency="SAR",
        actual_method="bank",is_pending_review=True,
    )
    assert result["state"]=="awaiting_staff_bank_verification"
    assert result["paid"] is False
    assert result["bank_credit_confirmed"] is False
    assert result["fulfillment_eligible"] is False


@pytest.mark.parametrize("changed",[
    {"actual_total_sar":"167.00"},
    {"actual_currency":"KWD"},
    {"actual_method":"mada"},
    {"is_pending_review":False},
])
def test_disagreement_never_books_bank_payment(changed):
    db,_=stage()
    p={
        "actual_total_sar":"165.00","actual_currency":"SAR",
        "actual_method":"bank","is_pending_review":True,
    }
    p.update(changed)
    value=assess_bank_order_total(db.salla_ai_bank_staff_reviews.rows[0],**p)
    assert value["state"]=="needs_staff_reconciliation"
    assert value["flags"]
    assert value["paid"] is False
    assert value["fulfillment_eligible"] is False


def test_unverified_receipt_cannot_enter_staff_case():
    bad=receipt()
    bad["credit_received"]=True
    with pytest.raises(BankReviewDenied):
        stage(receipt=bad)
