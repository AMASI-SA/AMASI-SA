"""Synthetic exact Salla merchant customized review status; never paid."""

import asyncio
from types import SimpleNamespace

import pytest

from salla_order_creation.bank_status_guard import (
    BankStatusDenied, exact_active_review_status,
    ensure_bank_review_custom_status,
)

REVIEW_ID=780246489
OTHER_ID=1539366206


def statuses():
    return [
        {"id":REVIEW_ID,"name":"بإنتظار المراجعة",
         "slug":"under_review","is_active":True,
         "original":{"id":566146469,"name":"بإنتظار المراجعة"}},
        {"id":OTHER_ID,"name":"تم المراجعة",
         "slug":"under_review","is_active":True,
         "original":{"id":566146469,"name":"بإنتظار المراجعة"}},
    ]


def order(customized=OTHER_ID,slug="under_review"):
    return {"id":90001,"payment_method":"bank","paid_amount":0,
            "status":{"slug":slug,
                      "customized":{"id":customized}}}


class Rows:
    def __init__(self,items):
        self.items=[dict(x) for x in items]

    async def find_one(self,query):
        for r in self.items:
            if all(r.get(k)==v for k,v in query.items()):
                return dict(r)
        return None

    async def insert_one(self,item):
        if any(x["_id"]==item["_id"] for x in self.items):
            raise ValueError("duplicate synthetic")
        self.items.append(dict(item))


class DB:
    def __init__(self):
        self.salla_ai_order_intents=Rows([{
            "user_id":"owner","intent_id":"intent-0001",
            "salla_order_id":90001,"pricing_mode":"salla_bank_review",
            "state":"created_bank_review_pending",
        }])
        self.salla_ai_bank_staff_reviews=Rows([{
            "user_id":"owner","intent_id":"intent-0001",
            "salla_order_id":90001,
            "review_state":"awaiting_staff_bank_verification",
            "bank_credit_confirmed":False,
        }])
        self.salla_ai_bank_status_intents=Rows([])


def run(db=None,*,source=None,enabled=True):
    db=db or DB()
    calls=[]
    initial=source or order()
    original=initial.copy()
    seen=[0]

    async def request(db_arg,user,method,path,**kwargs):
        calls.append((method,path,kwargs))
        if method=="GET" and path=="/orders/statuses":
            return {"success":True,"data":statuses()}
        if method=="GET" and path=="/orders/90001":
            seen[0]+=1
            if seen[0]>1 and len([c for c in calls if c[0]=="POST"])==1:
                return {"success":True,"data":order(customized=REVIEW_ID)}
            return {"success":True,"data":original}
        if method=="POST" and path=="/orders/90001/status":
            assert kwargs["json"]=={
                "status_id":REVIEW_ID,"send_status_sms":False,
            }
            return {"success":True,"data":{"code":201}}
        raise AssertionError("unexpected provider operation")

    result=asyncio.run(ensure_bank_review_custom_status(
        db,user_id="owner",intent_id="intent-0001",
        salla_order_id=90001,request=request,enabled=enabled,
    ))
    return db,result,calls


def test_exact_custom_status_not_generic_under_review_slug():
    assert exact_active_review_status(statuses())==REVIEW_ID
    assert REVIEW_ID!=OTHER_ID


def test_one_once_only_update_status_with_send_sms_disabled():
    db,result,calls=run()
    assert result["state"]=="confirmed_under_review"
    assert result["bank_credit_confirmed"] is False
    assert result["review_status_id"]==REVIEW_ID
    assert result["status_post_performed"] is True
    assert [(m,p) for m,p,_ in calls]==[
        ("GET","/orders/90001"),
        ("GET","/orders/statuses"),
        ("POST","/orders/90001/status"),
        ("GET","/orders/90001"),
    ]
    assert len(db.salla_ai_bank_status_intents.items)==1


def test_already_correct_custom_status_never_reposts():
    db,result,calls=run(source=order(customized=REVIEW_ID))
    assert result["state"]=="confirmed_under_review"
    assert result["status_post_performed"] is False
    assert all(m!="POST" for m,p,k in calls)
    assert db.salla_ai_bank_status_intents.items==[]


def test_same_under_review_slug_different_custom_status_must_update():
    _,result,calls=run(source=order(customized=OTHER_ID))
    assert result["review_status_id"]==REVIEW_ID
    assert any(method=="POST" for method,_,_ in calls)


def test_ambiguous_duplicate_exact_names_deny_before_update():
    values=statuses()
    values.append(dict(values[0],id=111))
    with pytest.raises(BankStatusDenied,match="unique_active"):
        exact_active_review_status(values)


def test_untrusted_review_status_with_wrong_canonical_name_denied():
    values=statuses()
    values[0]["original"]={"id":5,"name":"تم التنفيذ"}
    with pytest.raises(BankStatusDenied,match="unique_active"):
        exact_active_review_status(values)


def test_disabled_does_not_read_or_write_salla():
    db=DB()
    async def never(*args,**kwargs):
        raise AssertionError("no external call")
    with pytest.raises(BankStatusDenied,match="disabled"):
        asyncio.run(ensure_bank_review_custom_status(
            db,user_id="owner",intent_id="intent-0001",
            salla_order_id=90001,request=never,enabled=False,
        ))


def test_wrong_merchant_or_missing_case_denies_before_network():
    db=DB()
    async def never(*args,**kwargs):
        raise AssertionError("no external call")
    with pytest.raises(BankStatusDenied):
        asyncio.run(ensure_bank_review_custom_status(
            db,user_id="other",intent_id="intent-0001",
            salla_order_id=90001,request=never,enabled=True,
        ))
    db.salla_ai_bank_staff_reviews.items=[]
    with pytest.raises(BankStatusDenied):
        asyncio.run(ensure_bank_review_custom_status(
            db,user_id="owner",intent_id="intent-0001",
            salla_order_id=90001,request=never,enabled=True,
        ))


@pytest.mark.parametrize("change",[
    {"payment_method":"mada"},
    {"paid_amount":165},
    {"status":{"slug":"completed","customized":{"id":OTHER_ID}}},
    {"id":90099},
])
def test_paid_or_other_order_cannot_change_bank_review_status(change):
    with pytest.raises(BankStatusDenied):
        run(source={**order(),**change})


def test_duplicate_attempt_is_fenced_not_repeated():
    db=DB()
    _,first,calls=run(db=db)
    assert first["status_post_performed"] is True
    _,second,calls2=run(db=db)
    assert second["state"]=="status_update_previously_attempted"
    assert second["status_post_performed"] is False
    assert all(method!="POST" for method,_,_ in calls2)


def test_unknown_post_result_is_quarantined():
    db=DB()
    calls=[]
    async def ambiguous(db_arg,owner,method,path,**kwargs):
        calls.append(method)
        if method=="GET" and path=="/orders/90001":
            return {"success":True,"data":order()}
        if method=="GET" and path=="/orders/statuses":
            return {"success":True,"data":statuses()}
        raise TimeoutError("synthetic unknown update result")
    result=asyncio.run(ensure_bank_review_custom_status(
        db,user_id="owner",intent_id="intent-0001",
        salla_order_id=90001,request=ambiguous,enabled=True,
    ))
    assert result["state"]=="status_update_needs_reconciliation"
    assert calls==["GET","GET","POST"]
