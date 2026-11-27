"""One Salla bank pending-payment order, never mark paid from screenshot."""

import asyncio
from datetime import datetime,timedelta,timezone

import pytest

from salla_order_creation.bank_claims import claim_storage_id,claim_reference
from salla_order_creation.payload import PendingOrder
from salla_order_creation.service import (
    CreationDenied,create_confirmed_salla_order,order_digest,
    quote_pricing_mode,
)
class FakeCollection:
    def __init__(self, rows=()):
        self.rows=[dict(r) for r in rows]

    async def find_one(self,query):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                return dict(row)
        return None

    async def insert_one(self,doc):
        if any(row.get("_id")==doc.get("_id") for row in self.rows):
            raise ValueError("duplicate synthetic _id")
        self.rows.append(dict(doc))

    async def update_one(self,query,update):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                row.update(update.get("$set",{}))
                return
        raise ValueError("synthetic intent update missing")


class FakeDB:
    def __init__(self,quotes=(),confirmations=()):
        self.salla_ai_order_quotes=FakeCollection(quotes)
        self.salla_ai_order_confirmations=FakeCollection(confirmations)
        self.salla_ai_order_intents=FakeCollection()


def bank_order(methods=None):
    return PendingOrder.model_validate({
        "customer":{"id":123},
        "receiver":{"name":"Synthetic recipient","phone":"+966500000000",
                    "country_code":"SA"},
        "courier_id":"202",
        "ship_to":{
            "country":1,"city":2,"district":3,"block":None,
            "street_number":"11","address_line":"Synthetic address",
            "postal_code":"12345","short_address":"ABCD1234",
            "building_number":"11","additional_number":"22",
            "geo_coordinates":{"lat":24.7,"lng":46.7},
        },
        "accepted_methods":methods or ["bank"],
        "products":[{"identifier_type":"id","identifier":400,"quantity":1}],
    })


def fixture(order=None, quote_changes=None, claim_changes=None):
    order=order or bank_order()
    now=datetime.now(timezone.utc)
    ref=claim_reference("owner","intent-0001")
    common={
        "user_id":"owner","intent_id":"intent-0001",
        "inbox_id":"2","conversation_id":"3",
        "payload_digest":order_digest(order),
    }
    quote={
        **common,"source":"mezan_verified_bank_receipt_review_v1",
        "state":"authorized_for_pending_creation",
        "post_create_salla_total":True,
        "online_only":False,"total_sar":None,
        "pricing_verified":False,"shipping_price_verified":False,
        "products_verified":True,"carrier_eligibility_verified":True,
        "payment_methods_verified":True,
        "receipt_visual_preflight":True,"bank_credit_verified":False,
        "bank_review_ref":ref,"store_bank_id":4321,
        "source_contact_id":77,
        "version":1,
        "verified_at":now-timedelta(seconds=60),
        "expires_at":now+timedelta(minutes=5),
    }
    quote.update(quote_changes or {})
    confirm={
        **common,"source":"chatwoot_customer_inbound_v1",
        "confirmed":True,"quote_version":1,
        "source_contact_id":77,"message_id":"501",
        "confirmed_at":now-timedelta(seconds=30),
    }
    claim={
        "_id":claim_storage_id("owner","intent-0001"),
        "user_id":"owner","intent_id":"intent-0001",
        "review_ref":ref,
        "inbox_id":2,"conversation_id":3,"contact_id":77,
        "state":"visual_preflight_accepted","store_bank_id":4321,
        "human_bank_credit_verified":False,
        "payment_marked_paid":False,"fulfillment_released":False,
    }
    claim.update(claim_changes or {})
    db=FakeDB([quote],[confirm])
    db.salla_ai_bank_receipt_reviews=FakeCollection([claim])
    return db,order


async def create(db,order,provider):
    return await create_confirmed_salla_order(
        db,user_id="owner",actor_id="trusted-native-service",
        intent_id="intent-0001",inbox_id="2",conversation_id="3",
        order=order,post_salla=provider,enabled=True,
    )


def test_bank_visual_claim_and_customer_consent_create_only_pending_bank_order():
    db,order=fixture()
    sent=[]
    async def provider(db,owner,method,path,**kwargs):
        sent.append((method,path,kwargs["json"]))
        return {"success":True,"data":{
            "id":99001,"reference_id":303303303,
            "amounts":{"total":{"amount":"150.00","currency":"SAR"}},
        }}
    one=asyncio.run(create(db,order,provider))
    replay=asyncio.run(create(db,order,provider))
    assert one["pricing_mode"]=="bank_receipt_review"
    assert one["state"]=="created_amount_unverified"
    assert one["amount_confirmed"] is False
    assert one["salla_order_id"]==99001
    assert replay["replayed"] is True
    assert replay["salla_order_id"]==99001
    assert sent[0][:2]==("POST","/orders")
    assert len(sent)==1
    payload=sent[0][2]
    assert payload["payment"]=={
        "status":"pending_payment","accepted_methods":["bank"],
    }
    assert "receipt_image_path" not in str(payload)
    assert '"paid"' not in str(payload)
    assert "checkout_url" not in str(payload)
    assert db.salla_ai_bank_receipt_reviews.rows[0]["human_bank_credit_verified"] is False


@pytest.mark.parametrize("bad",[
    {"receipt_visual_preflight":False},
    {"bank_credit_verified":True},
    {"bank_review_ref":None},
    {"store_bank_id":9999},
    {"source":"model_receipt"},
    {"online_only":True},
])
def test_unverified_bank_quote_never_calls_salla(bad):
    db,order=fixture(quote_changes=bad)
    async def forbidden(*args,**kwargs):
        raise AssertionError("Salla POST forbidden")
    with pytest.raises(CreationDenied):
        asyncio.run(create(db,order,forbidden))
    assert db.salla_ai_order_intents.rows==[]


@pytest.mark.parametrize("bad",[
    {"state":"rejected"},
    {"state":"awaiting_human_bank_review"},
    {"store_bank_id":1000},
    {"payment_marked_paid":True},
    {"human_bank_credit_verified":True},
    {"contact_id":88},
])
def test_incorrect_or_reused_receipt_cannot_create_bank_order(bad):
    db,order=fixture(claim_changes=bad)
    async def forbidden(*args,**kwargs):
        raise AssertionError("no order POST")
    with pytest.raises(CreationDenied):
        asyncio.run(create(db,order,forbidden))


def test_bank_quote_never_allows_online_method_or_cod():
    db,order=fixture()
    async def forbidden(*args,**kwargs):
        raise AssertionError("no order POST")
    for method in (["mada"],["credit_card"],["cod"]):
        wrong=bank_order(method)
        # For the same quote digest, a different order is rejected before POST.
        with pytest.raises(CreationDenied):
            asyncio.run(create(db,wrong,forbidden))


def test_fake_claim_ref_cannot_authorize_bank_order():
    db,order=fixture(quote_changes={"bank_review_ref":"bank-review."+"f"*32})
    async def forbidden(*args,**kwargs):
        raise AssertionError("no Salla POST")
    with pytest.raises(CreationDenied):
        asyncio.run(create(db,order,forbidden))
