"""Offline bank-review staff worklist security and review-only API behavior."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from salla_order_creation.bank_staff_worklist import (
    BankWorklistDenied, may_read_bank_review, list_pending_bank_reviews,
)

NOW=datetime(2026,11,27,17,50,tzinfo=timezone.utc)


def item(**changes):
    row={
        "_id":"synthetic-review-"+str(changes.get("intent_id","intent-0001")),
        "user_id":"owner-merchant",
        "intent_id":"intent-0001",
        "salla_order_id":123456,
        "salla_reference_id":302076044,
        "review_state":"awaiting_staff_bank_verification",
        "bank_credit_confirmed":False,
        "paid":False,
        "fulfillment_eligible":False,
        "receipt_amount_sar":"165.00",
        "source_kind":"image","source_message_id":501,
        "source_attachment_id":9001,
        "conversation_id":"3","inbox_id":"2",
        "created_at":NOW,
        # Deliberately sensitive/irrelevant fields: NEVER surface in response.
        "recipient_iban":"SA0380000000608010167519",
        "raw_receipt":"PRIVATE-SYNTHETIC-RECEIPT-BYTES",
        "receipt_reference_fingerprint":"a"*64,
        "source_contact_id":77,
    }
    row.update(changes)
    return row


class Cursor:
    def __init__(self,rows):
        self.rows=rows
        self.slices=[]

    def sort(self,keys):
        self.slices.append(("sort",keys))
        self.rows.sort(key=lambda r:(r["created_at"],r["_id"]),reverse=True)
        return self

    def skip(self,n):
        self.slices.append(("skip",n))
        self.rows=self.rows[n:]
        return self

    def limit(self,n):
        self.slices.append(("limit",n))
        self.rows=self.rows[:n]
        return self

    async def to_list(self,length):
        self.slices.append(("to_list",length))
        return list(self.rows[:length])


class Rows:
    def __init__(self,rows):
        self.rows=rows
        self.queries=[]
        self.cursors=[]

    def find(self,query):
        self.queries.append(dict(query))
        cursor=Cursor([
            dict(row) for row in self.rows
            if all(row.get(k)==v for k,v in query.items())
        ])
        self.cursors.append(cursor)
        return cursor


class DB:
    def __init__(self,rows):
        self.salla_ai_bank_staff_reviews=Rows(rows)


def read(db,**params):
    return asyncio.run(list_pending_bank_reviews(
        db,owner_id="owner-merchant",**params,
    ))


def test_queue_only_current_merchant_unpaid_staff_cases_not_private_bank_data():
    db=DB([
        item(),
        item(intent_id="intent-other",user_id="other-merchant"),
        item(intent_id="intent-paid",paid=True),
        item(intent_id="intent-done",review_state="confirmed"),
        item(intent_id="intent-confirmed",bank_credit_confirmed=True),
    ])
    out=read(db)
    assert len(out["items"])==1
    row=out["items"][0]
    assert row["intent_id"]=="intent-0001"
    assert row["receipt_amount_sar"]=="165.00"
    assert row["source_kind"]=="image"
    assert row["source_message_id"]==501
    assert row["source_attachment_id"]==9001
    assert row["paid"] is False
    assert row["bank_credit_confirmed"] is False
    assert row["fulfillment_eligible"] is False
    assert out["read_only"] is True
    for secret in (
        "recipient_iban","receipt_reference_fingerprint",
        "source_contact_id","raw_receipt","PRIVATE-SYNTHETIC",
        "SA0380000000608010167519",
    ):
        assert secret not in str(out)


def test_customer_text_claim_retains_no_attachment_and_no_payment_confirmation():
    db=DB([item(source_kind="text",source_attachment_id=0)])
    row=read(db)["items"][0]
    assert row["source_kind"]=="text"
    assert row["source_attachment_id"]==0
    assert row["paid"] is False


def test_mongo_naive_datetime_is_restored_as_utc_for_presentation():
    db=DB([item(created_at=NOW.replace(tzinfo=None))])
    row=read(db)["items"][0]
    assert row["created_at"].endswith("+00:00")


def test_pagination_is_strictly_bounded_and_sorted():
    db=DB([
        item(intent_id="intent-0001",salla_order_id=10001),
        item(intent_id="intent-0002",salla_order_id=10002),
    ])
    out=read(db,limit=1,offset=1)
    assert out["offset"]==1
    assert out["limit"]==1
    assert out["next_offset"]==2
    assert len(out["items"])==1
    operations=db.salla_ai_bank_staff_reviews.cursors[0].slices
    assert operations==[
        ("sort",[("created_at",-1),("_id",-1)]),
        ("skip",1),("limit",1),("to_list",1),
    ]


@pytest.mark.parametrize("params",[
    {"limit":0},{"limit":26},{"limit":True},
    {"offset":-1},{"offset":1001},{"offset":True},
])
def test_invalid_page_cannot_touch_database(params):
    db=DB([item()])
    with pytest.raises(BankWorklistDenied):
        read(db,**params)
    assert db.salla_ai_bank_staff_reviews.queries==[]


@pytest.mark.parametrize("changed",[
    {"salla_order_id":False},
    {"source_attachment_id":0},
    {"source_kind":"text","source_attachment_id":9001},
    {"source_kind":"pdf"},
    {"receipt_amount_sar":"NaN"},
    {"receipt_amount_sar":"0.00"},
    {"receipt_amount_sar":"165.0004"},
    {"conversation_id":"external"},
    {"paid":True},
])
def test_invalid_case_cannot_be_displayed_as_bank_review(changed):
    db=DB([item(**changed)])
    # Paid cases are excluded by the Mongo filter before projection.
    # Malformed still-unpaid records are denied, never displayed as safe.
    if changed.get("paid") is True:
        assert read(db)["items"] == []
    else:
        with pytest.raises(BankWorklistDenied):
            read(db)


def test_authorization_uses_canonical_server_permission_callback_only():
    owner={"id":"owner-merchant","role":"owner"}
    operator={"id":"staff-1","role":"operations"}
    viewer={"id":"staff-2","role":"viewer"}
    can=lambda u: u.get("role")=="operations"
    assert may_read_bank_review(owner,None)
    assert may_read_bank_review(operator,can)
    assert not may_read_bank_review(viewer,can)
    assert not may_read_bank_review({"id":"staff-3","permissions":["orders.manage"]},None)
    assert not may_read_bank_review({"id":"staff-4","role":"viewer"},lambda u:"yes")
    assert not may_read_bank_review(None,can)
    assert not may_read_bank_review(operator,lambda u:1/0)



def test_pagination_stops_before_invalid_next_offset():
    rows=[
        item(intent_id="intent-"+str(i).zfill(4),salla_order_id=10000+i)
        for i in range(1001)
    ]
    db=DB(rows)
    out=read(db,limit=1,offset=1000)
    assert len(out["items"])==1
    assert out["next_offset"] is None
