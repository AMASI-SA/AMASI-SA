"""Synthetic immediate Salla under_review for bank orders, never paid."""

import asyncio

import pytest

from salla_order_creation.bank_order_initial_status import (
    put_new_bank_order_under_review,
)
from salla_order_creation.bank_review_status import BankStatusDenied
from salla_order_creation.service import intent_storage_id


class Collection:
    def __init__(self,rows=None):
        self.rows=list(rows or [])

    async def find_one(self,query):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                return dict(row)
        return None

    async def insert_one(self,doc):
        if any(x["_id"]==doc["_id"] for x in self.rows):
            raise ValueError("duplicate fence")
        self.rows.append(dict(doc))

    async def update_one(self,query,update):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                row.update(update["$set"])
                return
        raise AssertionError("unknown fence")


class DB:
    def __init__(self):
        self.salla_ai_order_intents=Collection([{
            "_id":intent_storage_id("owner","intent-0001"),
            "user_id":"owner","intent_id":"intent-0001",
            "inbox_id":"2","conversation_id":"3",
            "state":"created_amount_unverified","pricing_mode":"salla_bank_review",
            "payload_digest":"c"*64,"salla_order_id":9876,
        }])
        self.salla_ai_order_quotes=Collection([{
            "user_id":"owner","intent_id":"intent-0001",
            "inbox_id":"2","conversation_id":"3",
            "source":"mezan_verified_bank_review_intent_v1",
            "state":"authorized_for_bank_review_creation",
            "post_create_salla_total":True,"bank_review_required":True,
            "payment_claim_confirmed":False,"online_only":False,
            "pricing_verified":False,"shipping_price_verified":False,
            "total_sar":None,"products_verified":True,
            "carrier_eligibility_verified":True,
            "payment_methods_verified":True,"payload_digest":"c"*64,
        }])
        self.salla_ai_bank_status_deliveries=Collection()


def bank_order(status="payment_pending",paid=0,method="bank"):
    return {"success":True,"data":{
        "id":9876,"status":{"slug":status},
        "payment_method":method,
        "accepted_payment_methods":["bank"],
        "payment_status":"pending_payment",
        "payment_actions":{"refund_action":{
            "paid_amount":{"amount":paid},
        }},
    }}


def action(db=None,remote=None,fail_post=False,enabled=True):
    db=db or DB()
    calls=[]
    async def get(*args,**kwargs):
        calls.append(("GET",args,kwargs))
        return remote or bank_order()
    async def post(*args,**kwargs):
        calls.append(("POST",args,kwargs))
        if fail_post:
            raise TimeoutError("Salla status unknown")
        return {"success":True,"data":{}}
    result=asyncio.run(put_new_bank_order_under_review(
        db,owner_id="owner",intent_id="intent-0001",
        get_salla=get,set_salla_status=post,enabled=enabled,
    ))
    return db,result,calls


def test_bank_order_held_under_review_immediately_before_receipt():
    db,result,calls=action()
    assert result["state"]=="awaiting_status_readback"
    assert result["paid"] is False
    assert len([x for x in calls if x[0]=="POST"])==1
    assert [x for x in calls if x[0]=="POST"][0][2]=={
        "json":{"slug":"under_review","send_status_sms":False},
    }
    assert len(db.salla_ai_bank_status_deliveries.rows)==1
    assert db.salla_ai_bank_status_deliveries.rows[0]["state"]=="provider_acknowledged"
    assert db.salla_ai_bank_status_deliveries.rows[0]["review_id"]=="bank_review_at_create"


def test_status_already_under_review_is_idempotent_without_post():
    db,row,calls=action(remote=bank_order(status="under_review"))
    assert row["state"]=="under_review"
    assert row["paid"] is False
    assert [x for x in calls if x[0]=="POST"]==[]


def test_repeated_bank_creation_ack_does_not_repeat_status_post():
    db,first,calls1=action()
    db,second,calls2=action(db=db)
    assert second["state"]=="status_attempt_already_recorded"
    assert [x for x in calls2 if x[0]=="POST"]==[]


def test_status_timeout_quarantined_and_never_replayed():
    db,first,calls=action(fail_post=True)
    assert first["state"]=="unknown_status_transition"
    db,second,calls2=action(db=db)
    assert [x for x in calls2 if x[0]=="POST"]==[]
    assert second["state"]=="status_attempt_already_recorded"


@pytest.mark.parametrize("payment",["mada","credit_card","cod"])
def test_card_or_cod_cannot_be_moved_to_bank_review(payment):
    with pytest.raises(BankStatusDenied,match="not_bank_transfer"):
        action(remote=bank_order(method=payment))


def test_bank_quote_must_be_verified_and_unpaid():
    db=DB()
    db.salla_ai_order_quotes.rows[0]["source"]="model_estimate"
    with pytest.raises(BankStatusDenied,match="verified_bank_only_quote"):
        action(db=db)
    db=DB()
    with pytest.raises(BankStatusDenied,match="already_paid"):
        action(db=db,remote={**bank_order(),"data":{
            **bank_order()["data"],"payment_status":"paid",
        }})


def test_create_status_transition_is_independently_disabled():
    db=DB()
    with pytest.raises(BankStatusDenied,match="disabled"):
        action(db=db,enabled=False)
    assert db.salla_ai_bank_status_deliveries.rows==[]


def test_missing_salla_order_read_does_not_claim_status_updated():
    db=DB()
    calls=[]
    async def fail(*args,**kwargs):
        calls.append(1)
        raise TimeoutError("test read unavailable")
    result=asyncio.run(put_new_bank_order_under_review(
        db,owner_id="owner",intent_id="intent-0001",
        get_salla=fail,set_salla_status=fail,enabled=True,
    ))
    assert result["state"]=="awaiting_salla_bank_order_readback"
    assert calls==[1]
    assert db.salla_ai_bank_status_deliveries.rows==[]
