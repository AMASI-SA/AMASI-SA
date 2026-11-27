"""Synthetic staff-only bank receipt triage never marks Salla paid."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from salla_order_creation.bank_review_staff import (
    StaffBankReviewDenied,StaffBankReceiptDecision,
    record_staff_receipt_triage,
)

RID="a"*64
NOW=datetime(2026,11,27,12,0,tzinfo=timezone.utc)


class Collection:
    def __init__(self):
        self.row={
            "_id":RID,"user_id":"owner",
            "status":"awaiting_human_bank_confirmation",
            "bank_settlement_confirmed":False,
            "salla_paid":False,"gl_posted":False,
            "salla_order_id":9876,"intent_id":"intent-0001",
        }
        self.writes=0

    async def update_one(self,query,update):
        if all(self.row.get(k)==v for k,v in query.items()):
            self.row.update(update["$set"])
            self.writes+=1
            return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def find_one(self,query):
        return dict(self.row) if all(self.row.get(k)==v
                                    for k,v in query.items()) else None


class DB:
    def __init__(self):
        self.salla_ai_bank_receipt_reviews=Collection()


def decide(db=None,kind="appearance_checked",code="appears_consistent",
           staff="staff-1",owner="owner",rid=RID):
    db=db or DB()
    result=asyncio.run(record_staff_receipt_triage(
        db,owner_id=owner,review_id=rid,staff_id=staff,
        decision=StaffBankReceiptDecision(
            decision=kind,review_note_code=code,
        ),now=NOW,
    ))
    return db,result


def test_staff_may_review_appearance_but_cannot_mark_paid():
    db,result=decide()
    assert result["state"]=="receipt_appearance_checked_awaiting_bank_credit"
    assert result["payment_confirmed"] is False
    assert result["bank_settlement_confirmed"] is False
    assert result["ready_for_fulfillment"] is False
    assert result["gl_posted"] is False
    assert db.salla_ai_bank_receipt_reviews.row["reviewed_by"]=="staff-1"
    assert db.salla_ai_bank_receipt_reviews.row["gl_posted"] is False


def test_request_clear_receipt_never_auto_declares_bank_credit():
    db,result=decide(kind="request_clearer_receipt",code="illegible")
    assert result["state"]=="receipt_clarification_required"
    assert result["payment_confirmed"] is False
    assert db.salla_ai_bank_receipt_reviews.row["salla_paid"] is False


def test_identical_staff_review_is_idempotent_no_duplicate_action():
    db=DB()
    decide(db=db)
    _,result=decide(db=db)
    assert result["state"]=="receipt_appearance_checked_awaiting_bank_credit"
    assert db.salla_ai_bank_receipt_reviews.writes==1


def test_different_staff_cannot_overwrite_review_decision():
    db=DB()
    decide(db=db)
    with pytest.raises(StaffBankReviewDenied,match="not_pending"):
        decide(db=db,staff="another-staff")


def test_wrong_owner_or_review_id_cannot_edit_order_review():
    db=DB()
    with pytest.raises(StaffBankReviewDenied):
        decide(db=db,owner="someone-else")
    with pytest.raises(StaffBankReviewDenied):
        decide(db=db,rid="b"*64)
    assert db.salla_ai_bank_receipt_reviews.writes==0


def test_invalid_staff_action_paid_or_refund_forbidden_by_schema():
    for fields in (
        {"decision":"mark_paid","review_note_code":"appears_consistent"},
        {"decision":"appearance_checked","review_note_code":"bank_received"},
        {"decision":"appearance_checked","review_note_code":"illegible"},
        {"decision":"appearance_checked","review_note_code":"appears_consistent",
         "gl_post":True},
    ):
        with pytest.raises(Exception):
            value=StaffBankReceiptDecision.model_validate(fields)
            asyncio.run(record_staff_receipt_triage(
                DB(),owner_id="owner",review_id=RID,
                staff_id="staff-1",decision=value,now=NOW,
            ))


def test_unreviewed_draft_status_is_required_for_human_triage():
    db=DB()
    db.salla_ai_bank_receipt_reviews.row["status"]="rejected"
    with pytest.raises(StaffBankReviewDenied):
        decide(db=db)
