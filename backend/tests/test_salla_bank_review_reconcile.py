"""Synthetic Mezan synced bank order and receipt amount discrepancy queue."""

import asyncio
from types import SimpleNamespace as Obj

import pytest

from salla_order_creation.bank_claims import (
    claim_storage_id, claim_reference,
)
from salla_order_creation.bank_review_reconcile import (
    BankReviewSyncDenied,reconcile_bank_review_from_mezan,
)
from salla_order_creation.service import intent_storage_id


class Collection:
    def __init__(self, rows=()):
        self.rows=[dict(r) for r in rows]

    async def find_one(self,query):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                return dict(row)
        return None

    async def update_one(self,query,data):
        for row in self.rows:
            accepted=True
            for key,value in query.items():
                if isinstance(value,dict) and "$in" in value:
                    accepted=accepted and row.get(key) in value["$in"]
                else:
                    accepted=accepted and row.get(key)==value
            if accepted:
                row.update(data["$set"])
                return Obj(modified_count=1)
        return Obj(modified_count=0)


class DB:
    def __init__(self, receipt_amount="150.00"):
        ref=claim_reference("owner","intent-0001")
        self.salla_ai_order_intents=Collection([{
            "_id":intent_storage_id("owner","intent-0001"),
            "user_id":"owner","intent_id":"intent-0001",
            "inbox_id":"2","conversation_id":"3",
            "state":"created_amount_unverified",
            "pricing_mode":"bank_receipt_review",
            "bank_review_ref":ref,
            "salla_order_id":99001,
            "reference_id":303303303,
        }])
        self.salla_ai_order_quotes=Collection([{
            "user_id":"owner","intent_id":"intent-0001",
            "inbox_id":"2","conversation_id":"3",
            "source_contact_id":77,
            "source":"mezan_verified_bank_receipt_review_v1",
            "state":"authorized_for_pending_creation",
            "post_create_salla_total":True,"online_only":False,
            "total_sar":None,"pricing_verified":False,
            "shipping_price_verified":False,
            "products_verified":True,"carrier_eligibility_verified":True,
            "payment_methods_verified":True,
            "receipt_visual_preflight":True,
            "bank_credit_verified":False,
            "bank_review_ref":ref,
        }])
        self.salla_ai_bank_receipt_reviews=Collection([{
            "_id":claim_storage_id("owner","intent-0001"),
            "user_id":"owner","intent_id":"intent-0001",
            "review_ref":ref,
            "inbox_id":2,"conversation_id":3,"contact_id":77,
            "state":"visual_preflight_accepted",
            "receipt_amount_sar":receipt_amount,
            "human_bank_credit_verified":False,
            "payment_marked_paid":False,
            "fulfillment_released":False,
            "salla_order_id":None,"salla_reference_id":None,
        }])


def synced(total=150,method="bank",status="payment_pending",paid=0):
    return Obj(
        order_id="99001",order_number="303303303",status=status,
        status_native="بانتظار الدفع",
        totals=Obj(total=total,currency="SAR"),
        payment=Obj(
            method=method,method_native="تحويل بنكي",
            status="pending_payment",collection_status="unpaid",
            paid_amount=paid,
        ),
    )


def check(db=None, order=None, **params):
    d=db or DB()
    expected=dict(
        user_id="owner",intent_id="intent-0001",
        inbox_id="2",conversation_id="3",contact_id=77,
    )
    expected.update(params)
    async def load(user,reference):
        assert user=="owner" and reference=="303303303"
        if order is False:
            raise LookupError("not synced")
        return synced() if order is None else order
    result=asyncio.run(reconcile_bank_review_from_mezan(
        d,load_order=load,**expected,
    ))
    return d,result


def test_matching_bank_receipt_reconciles_to_staff_review_not_paid():
    db,result=check()
    assert result["state"]=="awaiting_human_bank_review"
    assert result["receipt_amount_matches_order"] is True
    assert result["bank_credit_verified"] is False
    row=db.salla_ai_bank_receipt_reviews.rows[0]
    assert row["salla_order_id"]==99001
    assert row["salla_reference_id"]==303303303
    assert row["salla_actual_total_sar"]=="150.00"
    assert row["amount_matches_salla"] is True
    assert row["human_bank_credit_verified"] is False
    assert row["payment_marked_paid"] is False
    assert row["fulfillment_released"] is False
    assert check(db=db)[1]["replayed"] is True


def test_screenshot_amount_differs_from_salla_final_amount_goes_to_manual_review():
    db,result=check(db=DB(receipt_amount="140.00"))
    assert result["state"]=="amount_mismatch_review"
    assert result["receipt_amount_matches_order"] is False
    assert db.salla_ai_bank_receipt_reviews.rows[0]["salla_actual_total_sar"]=="150.00"
    assert db.salla_ai_bank_receipt_reviews.rows[0]["payment_marked_paid"] is False


def test_mezan_sync_not_yet_visible_does_not_fail_or_duplicate_order():
    db,result=check(order=False)
    assert result["state"]=="waiting_for_mezan_sync"
    assert db.salla_ai_bank_receipt_reviews.rows[0]["salla_order_id"] is None


def test_bank_intent_without_reference_waits_for_read_only_recovery():
    db=DB()
    db.salla_ai_order_intents.rows[0]["reference_id"]=None
    async def impossible(*args):
        raise AssertionError("must not query an unknown reference")
    result=asyncio.run(reconcile_bank_review_from_mezan(
        db,user_id="owner",intent_id="intent-0001",
        inbox_id="2",conversation_id="3",contact_id=77,
        load_order=impossible,
    ))
    assert result["state"]=="waiting_for_salla_reference"


@pytest.mark.parametrize("change",[
    {"method":"mada"},
    {"status":"in_progress"},
    {"paid":150},
    {"total":0},
    {"total":"NaN"},
])
def test_wrong_paid_or_nonbank_order_is_never_reconciled(change):
    obj=synced(**change)
    db=DB()
    with pytest.raises(BankReviewSyncDenied):
        check(db=db,order=obj)
    assert db.salla_ai_bank_receipt_reviews.rows[0]["salla_order_id"] is None


def test_cross_conversation_or_contact_cannot_link_receipt():
    for scope in ({"inbox_id":"9"},{"conversation_id":"9"},{"contact_id":99}):
        with pytest.raises(BankReviewSyncDenied):
            check(**scope)


def test_unknown_or_rejected_quote_prevents_review_creation():
    db=DB()
    db.salla_ai_order_quotes.rows[0]["bank_credit_verified"]=True
    with pytest.raises(BankReviewSyncDenied):
        check(db=db)
