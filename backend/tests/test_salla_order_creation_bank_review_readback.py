"""Offline merchant bank-order review readback, still unpaid."""

import asyncio
from types import SimpleNamespace as Obj

import pytest

from salla_order_creation.bank_review_readback import read_synced_bank_review
from salla_order_creation.service import intent_storage_id


class Rows:
    def __init__(self,rows):
        self.rows=[dict(x) for x in rows]

    async def find_one(self,query):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                return dict(row)
        return None


class DB:
    def __init__(self):
        self.salla_ai_order_intents=Rows([{
            "_id":intent_storage_id("owner","intent-0001"),
            "user_id":"owner","intent_id":"intent-0001",
            "pricing_mode":"salla_bank_review",
            "state":"created_bank_review_pending",
            "salla_order_id":90001,"reference_id":302076044,
        }])
        self.salla_ai_bank_staff_reviews=Rows([{
            "user_id":"owner","intent_id":"intent-0001",
            "salla_order_id":90001,
            "review_state":"awaiting_staff_bank_verification",
            "bank_credit_confirmed":False,"paid":False,
            "receipt_amount_sar":"165.00",
            "source_message_id":501,"source_attachment_id":9001,
        }])


def order(**change):
    obj=Obj(
        order_id="90001",order_number="302076044",
        status="under_review",status_native="بإنتظار المراجعة",
        status_customized_id=780246489,
        status_customized_name="بإنتظار المراجعة",
        payment=Obj(method="bank",method_native="bank",
                    status="pending_payment",collection_status="unpaid",
                    paid_amount=0.0),
        totals=Obj(total=165.00,currency="SAR"),
    )
    for key,val in change.items():
        setattr(obj,key,val)
    return obj


def check(db=None,existing=None,missing=False):
    async def load(owner,reference):
        assert owner=="owner" and reference=="302076044"
        if missing:
            raise LookupError("order not synced")
        return order() if existing is None else existing
    return asyncio.run(read_synced_bank_review(
        db or DB(),user_id="owner",intent_id="intent-0001",
        load_order=load,expected_review_customized_id=780246489,
    ))


def test_bank_salla_order_stays_under_review_and_unpaid():
    result=check()
    assert result["state"]=="awaiting_staff_bank_verification"
    assert result["salla_order_id"]==90001
    assert result["reference_id"]==302076044
    assert result["review_owner"]=="customer_service"
    assert result["receipt_message_id"]==501
    assert result["receipt_attachment_id"]==9001
    assert result["checkout_url"] is None
    assert result["paid"] is False
    assert result["bank_credit_confirmed"] is False
    assert result["fulfillment_eligible"] is False


def test_not_yet_synced_bank_order_does_not_change_status():
    row=check(missing=True)
    assert row["state"]=="waiting_for_mezan_sync"
    assert row["paid"] is False


def test_wrong_bank_order_id_cannot_attach_receipt():
    row=check(existing=order(order_id="99999"))
    assert row["state"]=="synced_bank_order_id_mismatch"


def test_bank_total_disagreement_routes_to_staff_reconciliation():
    broken=order()
    broken.totals.total=175
    row=check(existing=broken)
    assert row["state"]=="needs_staff_reconciliation"
    assert "receipt_amount_differs_from_salla_order" in row["flags"]
    assert row["paid"] is False


def test_wrong_payment_method_or_status_never_marks_received():
    broken=order()
    broken.payment.method="mada"
    broken.payment.method_native="mada"
    row=check(existing=broken)
    assert row["state"]=="salla_payment_method_not_bank"
    broken=order()
    broken.status="completed"
    broken.status_native="تم التنفيذ"
    row=check(existing=broken)
    assert row["state"]=="needs_staff_reconciliation"
    assert "salla_order_not_in_review_status" in row["flags"]


def test_paid_order_must_not_be_approached_as_pending_review():
    broken=order()
    broken.payment.paid_amount=165.00
    row=check(existing=broken)
    assert row["state"]=="payment_not_pending_bank_review"
    assert row["bank_credit_confirmed"] is False


def test_case_missing_never_announces_review_success():
    db=DB()
    db.salla_ai_bank_staff_reviews.rows=[]
    result=check(db=db)
    assert result["state"]=="staff_review_case_missing"
    assert result["paid"] is False


def test_bank_order_intent_without_reference_waits_safely():
    db=DB()
    db.salla_ai_order_intents.rows[0]["reference_id"]=None
    result=check(db=db)
    assert result["state"]=="waiting_for_salla_reference"



def test_text_only_bank_claim_readback_exposes_kind_not_customer_text():
    db=DB()
    row=db.salla_ai_bank_staff_reviews.rows[0]
    row["source_kind"]="text"
    row["source_attachment_id"]=0
    result=check(db=db)
    assert result["receipt_source_kind"]=="text"
    assert result["customer_text_claim_only"] is True
    assert result["receipt_message_id"]==501
    assert result["receipt_attachment_id"]==0
    assert result["paid"] is False
    assert result["bank_credit_confirmed"] is False
    assert result["fulfillment_eligible"] is False



def test_same_generic_under_review_slug_does_not_mistake_reviewed_for_waiting():
    # Real Salla merchant statuses "تم المراجعة" and "بإنتظار المراجعة"
    # share the same generic slug. Only the exact bank review name is safe.
    already_reviewed=order(
        status="under_review",
        status_native="بإنتظار المراجعة",  # CANONICAL looks correct!
        status_customized_id=1539366206,
        status_customized_name="تم المراجعة",
    )
    state=check(existing=already_reviewed)
    assert state["state"]=="needs_staff_reconciliation"
    assert "salla_order_not_in_review_status" in state["flags"]
    assert state["bank_credit_confirmed"] is False
    assert state["fulfillment_eligible"] is False



def test_canonical_top_level_name_never_masks_wrong_customized_id():
    # Salla may return "بإنتظار المراجعة" as the canonical name while
    # "تم المراجعة" is the ACTUAL merchant custom child.
    wrong=order(
        status="under_review",
        status_native="بإنتظار المراجعة",
        status_customized_id=1539366206,
        status_customized_name="تم المراجعة",
    )
    result=check(existing=wrong)
    assert result["state"]=="needs_staff_reconciliation"
    assert "salla_order_not_in_review_status" in result["flags"]
    assert result["paid"] is False


def test_missing_synced_customized_status_identity_is_fail_closed():
    for broken in (
        order(status_customized_id=None),
        order(status_customized_name=None),
        order(status_customized_id=780246489,status_customized_name="تم المراجعة"),
        order(status_customized_id=1539366206,status_customized_name="بإنتظار المراجعة"),
    ):
        result=check(existing=broken)
        assert result["state"]=="needs_staff_reconciliation"
        assert "salla_order_not_in_review_status" in result["flags"]
        assert result["bank_credit_confirmed"] is False


def test_no_live_merchant_customized_status_id_cannot_mark_review_ready():
    async def load(owner,reference):
        return order()
    result=asyncio.run(read_synced_bank_review(
        DB(),user_id="owner",intent_id="intent-0001",load_order=load,
        expected_review_customized_id=None,
    ))
    assert result["state"]=="needs_staff_reconciliation"
    assert "salla_order_not_in_review_status" in result["flags"]
    assert result["paid"] is False
