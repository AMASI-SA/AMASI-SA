"""Synthetic Salla bank status under_review, no paid method or GL."""

import asyncio

import pytest

from salla_order_creation.bank_claims import claim_storage_id
from salla_order_creation.bank_review_status import (
    BankReviewStatusDenied,ensure_salla_bank_order_under_review,
)


class Reviews:
    def __init__(self):
        self.row={
            "_id":claim_storage_id("owner","intent-0001"),
            "user_id":"owner","intent_id":"intent-0001",
            "state":"awaiting_human_bank_review",
            "salla_order_id":99001,
            "human_bank_credit_verified":False,
            "payment_marked_paid":False,
            "fulfillment_released":False,
            "salla_review_status":None,
        }
        self.calls=[]

    async def find_one(self,query):
        return dict(self.row) if all(self.row.get(k)==v for k,v in query.items()) else None

    async def update_one(self,query,data):
        if not all(self.row.get(k)==v for k,v in query.items()):
            return type("Result",(),{"modified_count":0})()
        self.calls.append(data)
        self.row.update(data["$set"])
        return type("Result",(),{"modified_count":1})()


class DB:
    def __init__(self):
        self.salla_ai_bank_receipt_reviews=Reviews()


def response(status="payment_pending", method="bank", paid="pending_payment"):
    return {
        "success":True,
        "data":{
            "id":99001,
            "status":{"slug":status},
            "payment_method":method,
            "payment":{"status":paid},
        },
    }


def run(db=None, get=None, post=None):
    d=db or DB()
    calls=[]
    current={"status":"payment_pending"}
    async def read(db,owner,method,path,**kw):
        calls.append((method,path))
        return response(current["status"]) if get is None else await get(
            db,owner,method,path,**kw
        )
    async def write(db,owner,method,path,**kw):
        calls.append((method,path,kw))
        if post is not None:
            return await post(db,owner,method,path,**kw)
        current["status"]="under_review"
        return {"success":True,"data":{"message":"status updated"}}
    result=asyncio.run(ensure_salla_bank_order_under_review(
        d,owner_id="owner",intent_id="intent-0001",
        get_order=read,post_status=write,
    ))
    return d,result,calls


def test_pending_bank_order_moves_to_salla_under_review_without_marking_paid():
    db,result,calls=run()
    assert result["state"]=="salla_under_review"
    assert result["bank_credit_verified"] is False
    post=[c for c in calls if c[0]=="POST"]
    assert len(post)==1
    assert post[0][1]=="/orders/99001/status"
    assert post[0][2]["json"]=={
        "slug":"under_review","send_status_sms":False,
    }
    assert "paid" not in post[0][2]["json"]
    assert db.salla_ai_bank_receipt_reviews.row["payment_marked_paid"] is False
    assert db.salla_ai_bank_receipt_reviews.row["fulfillment_released"] is False
    assert db.salla_ai_bank_receipt_reviews.row["salla_review_status"]=="under_review"


def test_provider_already_reviewing_is_read_only_no_post():
    async def already(*args,**kw):
        return response("under_review")
    db,result,calls=run(get=already)
    assert result["state"]=="salla_under_review"
    assert [x[0] for x in calls]==["GET"]


def test_unknown_status_write_is_quarantined_never_replayed():
    db=DB()
    writes=[]
    async def timeout(*args,**kw):
        writes.append(1)
        raise TimeoutError("simulated Salla accepted but no reply")
    d,result,calls=run(db=db,post=timeout)
    assert result["state"]=="salla_review_status_write_uncertain"
    assert db.salla_ai_bank_receipt_reviews.row["salla_review_status"]=="request_result_unknown"
    d,again,calls=run(db=db,post=timeout)
    assert again["state"]=="salla_review_status_requires_manual_reconciliation"
    assert writes==[1]


def test_untrusted_paid_bank_order_never_changes_status():
    async def paid(*args,**kw):
        return response(paid="paid")
    db=DB()
    async def no_post(*args,**kwargs):
        raise AssertionError("no status POST")
    with pytest.raises(BankReviewStatusDenied,match="not_unpaid"):
        asyncio.run(ensure_salla_bank_order_under_review(
            db,owner_id="owner",intent_id="intent-0001",
            get_order=paid,post_status=no_post,
        ))


@pytest.mark.parametrize("bad",[
    {"state":"rejected"},
    {"payment_marked_paid":True},
    {"human_bank_credit_verified":True},
    {"fulfillment_released":True},
    {"salla_order_id":None},
])
def test_ineligible_claim_cannot_promote_to_review(bad):
    db=DB()
    db.salla_ai_bank_receipt_reviews.row.update(bad)
    async def forbidden(*args,**kwargs):
        raise AssertionError("provider must not be called")
    with pytest.raises(BankReviewStatusDenied):
        asyncio.run(ensure_salla_bank_order_under_review(
            db,owner_id="owner",intent_id="intent-0001",
            get_order=forbidden,post_status=forbidden,
        ))


def test_wrong_order_id_or_payment_method_not_status_mutated():
    for result in (
        {"success":True,"data":{
            "id":99002,"payment_method":"bank",
            "status":{"slug":"payment_pending"},
        }},
        response(method="mada"),
        response(status="in_progress"),
    ):
        async def wrong(*args,**kw):
            return result
        db=DB()
        with pytest.raises(BankReviewStatusDenied):
            asyncio.run(ensure_salla_bank_order_under_review(
                db,owner_id="owner",intent_id="intent-0001",
                get_order=wrong,
                post_status=lambda *args,**kw:None,
            ))
