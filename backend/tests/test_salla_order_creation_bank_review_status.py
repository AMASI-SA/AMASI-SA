"""Synthetic no-network Salla bank review transition, without payment/GL."""

import asyncio

import pytest

from salla_order_creation.bank_review_status import (
    BankStatusDenied, ensure_bank_order_under_review,
)
from salla_order_creation.service import intent_storage_id


class Rows:
    def __init__(self,items=None):
        self.rows=list(items or [])

    async def find_one(self,query):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                return dict(row)
        return None

    async def insert_one(self,row):
        if any(r["_id"]==row["_id"] for r in self.rows):
            raise ValueError("duplicate")
        self.rows.append(dict(row))

    async def update_one(self,query,update):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                row.update(update["$set"])
                return
        raise AssertionError("not found")


class DB:
    def __init__(self):
        self.salla_ai_order_intents=Rows([{
            "_id":intent_storage_id("owner","intent-0001"),
            "user_id":"owner","intent_id":"intent-0001",
            "state":"created_amount_unverified",
            "pricing_mode":"salla_bank_review",
            "salla_order_id":9876,
        }])
        self.salla_ai_bank_receipt_reviews=Rows([{
            "_id":"review-1","user_id":"owner",
            "intent_id":"intent-0001","salla_order_id":9876,
            "status":"awaiting_human_bank_confirmation",
            "bank_settlement_confirmed":False,
            "salla_paid":False,
        }])
        self.salla_ai_bank_status_deliveries=Rows()


def order(status="payment_pending",method="bank",paid=0):
    return {"success":True,"data":{
        "id":9876,"status":{"slug":status},
        "payment_method":method,
        "accepted_payment_methods":["bank"],
        "payment_actions":{
            "refund_action":{"paid_amount":{"amount":paid,"currency":"SAR"}}
        },
    }}


def call(db=None, response=None, *, enabled=True):
    db=db or DB()
    calls=[]
    async def get(*args,**kwargs):
        calls.append(("GET",args,kwargs))
        return response or order()
    async def post(*args,**kwargs):
        calls.append(("POST",args,kwargs))
        return {"success":True,"data":{"message":"ok"}}
    data=asyncio.run(ensure_bank_order_under_review(
        db,owner_id="owner",intent_id="intent-0001",
        review_id="review-1",
        get_salla=get,set_salla_status=post,
        enabled=enabled,
    ))
    return db,data,calls


def test_unpaid_bank_order_enters_under_review_not_paid():
    db,out,calls=call()
    assert out["state"]=="awaiting_status_readback"
    assert out["paid"] is False
    assert len([x for x in calls if x[0]=="POST"])==1
    name,args,kwargs=[x for x in calls if x[0]=="POST"][0]
    assert args[2:] == ("POST","/orders/9876/status")
    assert kwargs=={"json":{"slug":"under_review","send_status_sms":False}}
    assert "payment" not in kwargs["json"]
    assert "paid" not in str(kwargs["json"])
    assert db.salla_ai_bank_status_deliveries.rows[0]["state"]=="provider_acknowledged"


def test_under_review_status_from_salla_is_idempotent_no_status_post():
    db,out,calls=call(response=order(status="under_review"))
    assert out["state"]=="under_review"
    assert out["replayed"] is True
    assert len([x for x in calls if x[0]=="POST"])==0


def test_second_request_does_not_repeat_status_post_after_ack():
    db=DB()
    _,first,calls1=call(db)
    _,second,calls2=call(db)
    assert first["state"]=="awaiting_status_readback"
    assert second["state"]=="unknown_status_transition"
    assert len([x for x in calls2 if x[0]=="POST"])==0


@pytest.mark.parametrize("method",["mada","credit_card","cod"])
def test_payment_method_not_bank_cannot_be_stolen_by_status_change(method):
    db=DB()
    async def get(*args,**kwargs):
        return order(method=method)
    async def no(*args,**kwargs):
        raise AssertionError("never POST")
    with pytest.raises(BankStatusDenied,match="payment_method_not_bank"):
        asyncio.run(ensure_bank_order_under_review(
            db,owner_id="owner",intent_id="intent-0001",
            review_id="review-1",get_salla=get,
            set_salla_status=no,enabled=True,
        ))


def test_paid_amount_blocks_status_change():
    db=DB()
    async def get(*args,**kwargs):
        return order(paid=100)
    async def never(*args,**kwargs):
        raise AssertionError("never POST")
    with pytest.raises(BankStatusDenied,match="already_has_paid_amount"):
        asyncio.run(ensure_bank_order_under_review(
            db,owner_id="owner",intent_id="intent-0001",
            review_id="review-1",get_salla=get,
            set_salla_status=never,enabled=True,
        ))


def test_disabled_status_change_does_not_access_salla():
    db=DB()
    async def never(*args,**kwargs):
        raise AssertionError("network forbidden")
    with pytest.raises(BankStatusDenied,match="disabled"):
        asyncio.run(ensure_bank_order_under_review(
            db,owner_id="owner",intent_id="intent-0001",
            review_id="review-1",get_salla=never,
            set_salla_status=never,enabled=False,
        ))


def test_human_review_required_before_salla_status_change():
    db=DB()
    db.salla_ai_bank_receipt_reviews.rows[0]["status"]="rejected"
    async def never(*args,**kwargs):
        raise AssertionError("network forbidden")
    with pytest.raises(BankStatusDenied,match="pending_human_review"):
        asyncio.run(ensure_bank_order_under_review(
            db,owner_id="owner",intent_id="intent-0001",
            review_id="review-1",get_salla=never,
            set_salla_status=never,enabled=True,
        ))


def test_wrong_order_identity_prevents_status_update():
    db=DB()
    async def mismatch(*args,**kwargs):
        row=order()
        row["data"]["id"]=9877
        return row
    async def never(*args,**kwargs):
        raise AssertionError("network forbidden")
    with pytest.raises(BankStatusDenied,match="identity_mismatch"):
        asyncio.run(ensure_bank_order_under_review(
            db,owner_id="owner",intent_id="intent-0001",
            review_id="review-1",get_salla=mismatch,
            set_salla_status=never,enabled=True,
        ))


def test_uncertain_provider_post_not_retried_or_marked_paid():
    db=DB()
    calls=[]
    async def get(*args,**kwargs):
        return order()
    async def post(*args,**kwargs):
        calls.append(1)
        raise TimeoutError("synthetic Salla status write unknown")
    async def run():
        return await ensure_bank_order_under_review(
            db,owner_id="owner",intent_id="intent-0001",
            review_id="review-1",get_salla=get,
            set_salla_status=post,enabled=True,
        )
    first=asyncio.run(run())
    second=asyncio.run(run())
    assert first["state"]=="unknown_status_transition"
    assert second["state"]=="unknown_status_transition"
    assert calls==[1]
    assert db.salla_ai_bank_status_deliveries.rows[0]["state"]=="unknown"
