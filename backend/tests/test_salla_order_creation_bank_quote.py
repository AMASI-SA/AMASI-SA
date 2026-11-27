"""Synthetic bank transfer quote requires a screened receipt + active Salla bank."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from salla_order_creation.intent_writer import (
    PrepareDenied, prepare_salla_unpaid_order_intent,
)
from salla_order_creation.service import quote_pricing_mode
from salla_order_creation.payload import PendingOrder

NOW=datetime.fromtimestamp(1_800_000_000,tz=timezone.utc)


class Rows:
    def __init__(self,rows=()):
        self.rows=[dict(x) for x in rows]

    async def find_one(self,query):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                return dict(row)
        return None

    async def insert_one(self,row):
        if any(x.get("_id")==row.get("_id") for x in self.rows):
            raise ValueError("synthetic duplicate")
        self.rows.append(dict(row))


class DB:
    def __init__(self,receipt=True):
        self.salla_ai_order_quotes=Rows()
        self.salla_ai_bank_receipt_evidence=Rows([screened_receipt()] if receipt else [])


def screened_receipt(**changes):
    data={
        "_id":"a"*64,"user_id":"owner","intent_id":"intent-0001",
        "inbox_id":"2","conversation_id":"3","source_contact_id":77,
        "bank_id":11,"state":"appearance_plausible",
        "source":"chatwoot_verified_image_bank_receipt_v1",
        "source_attachment_id":9001,"credit_received":False,
        "staff_review_required":True,"receipt_amount_sar":"165.00",
        "expires_at":NOW+timedelta(hours=1),
    }
    data.update(changes)
    return data


def order():
    return PendingOrder.model_validate({
        "customer":{"id":501},
        "receiver":{"name":"Synthetic","phone":"+966500000000",
                    "country_code":"SA"},
        "courier_id":"220",
        "ship_to":{
            "country":1,"city":2,"district":3,"block":None,
            "street_number":"11","address_line":"Synthetic street",
            "postal_code":"12345","short_address":"RHMA3184",
            "building_number":"11","additional_number":"22",
            "geo_coordinates":{"lat":24.7,"lng":46.7},
        },
        "accepted_methods":["bank"],
        "products":[{"identifier_type":"id","identifier":400,"quantity":1}],
    })


async def product_reader(*args,**kwargs):
    return {
        "source":"salla_product_details","product_id":400,
        "can_use_as_final_quote":False,"taxed_base_price_sar":"150.00",
        "available_quantity":5,"options":[],"variants":[],
    }


async def rate_reader(*args,**kwargs):
    return [{
        "rate_id":800,"shipping_company_id":220,
        "destination_country_id":1,"destination_city_id":2,
        "estimated_shipping_sar":"15.00",
        "cod_service_found":False,"cod_fee_estimate_sar":None,
        "source":"salla_shipping_rate_estimate","is_final_cart_quote":False,
    }]


async def methods_reader(*args,**kwargs):
    return {"source":"salla_payment_methods",
            "eligible_create_order_method_candidates":["bank"],
            "can_create_order":False}


async def bank_reader(*args,**kwargs):
    return {"source":"salla_store_payment_banks",
            "bank_id":11,"status":"active",
            "iban":"SA0380000000608010167519"}


@pytest.fixture(autouse=True)
def crypto(monkeypatch):
    monkeypatch.setenv("MEZAN_ASSISTED_SALLA_PRIVATE_FIELDS_KEY",
                       Fernet.generate_key().decode())



def prepare(db=None,o=None,bank_lookup=bank_reader,now=NOW):
    db=db or DB()
    return db,asyncio.run(prepare_salla_unpaid_order_intent(
        db,user_id="owner",intent_id="intent-0001",
        inbox_id="2",conversation_id="3",
        source_message_id=501,source_contact_id=77,
        order=o or order(),selected_rate_id=800,
        enabled=True,product_reader=product_reader,rate_reader=rate_reader,
        methods_reader=methods_reader,bank_reader=bank_lookup,now=now,
    ))


def test_bank_review_quote_never_claims_paid_or_final_amount():
    db,out=prepare()
    assert out["pricing_mode"]=="salla_bank_review"
    assert out["bank_review_required"] is True
    assert out["verified_total_sar"] is None
    assert out["checkout_url"] is None
    q=db.salla_ai_order_quotes.rows[0]
    assert quote_pricing_mode(q)=="salla_bank_review"
    assert q["source"]=="mezan_verified_unpaid_bank_review_intent_v1"
    assert q["staff_review_required"] is True
    assert q["bank_credit_confirmed"] is False
    assert q["receipt_ref"]=="receipt."+"a"*32
    assert q["bank_id"]==11
    assert q["total_sar"] is None
    assert "order_payload" not in q


def test_bank_quote_cannot_exist_without_current_receipt_appearance():
    with pytest.raises(PrepareDenied,match="independent_bank_receipt"):
        prepare(DB(receipt=False))


def test_expired_bank_receipt_cannot_back_create_order():
    db=DB()
    db.salla_ai_bank_receipt_evidence.rows[0]["expires_at"]=NOW-timedelta(seconds=1)
    with pytest.raises(PrepareDenied,match="expired"):
        prepare(db)


def test_bank_account_not_active_denies_quote():
    async def inactive(*args,**kwargs):
        return {"source":"salla_store_payment_banks",
                "bank_id":11,"status":"inactive"}
    with pytest.raises(PrepareDenied,match="active_bank_account"):
        prepare(bank_lookup=inactive)


def test_bank_review_requires_same_customer_contact():
    db=DB()
    db.salla_ai_bank_receipt_evidence.rows[0]["source_contact_id"]=99
    with pytest.raises(PrepareDenied,match="independent_bank_receipt"):
        prepare(db)


def test_bank_review_same_intent_and_receipt_is_immutable():
    db=DB()
    _,one=prepare(db)
    _,two=prepare(db)
    assert one==two
    assert len(db.salla_ai_order_quotes.rows)==1


def test_bank_review_receipt_change_cannot_reuse_intent():
    db=DB()
    prepare(db)
    db.salla_ai_bank_receipt_evidence.rows[0]["_id"]="b"*64
    with pytest.raises(PrepareDenied,match="different_cart"):
        prepare(db)


def test_bank_customer_cannot_choose_online_method_after_receipt_screen():
    x=order()
    x.accepted_methods=["mada"]
    db=DB()
    with pytest.raises(PrepareDenied):
        prepare(db,o=x)
    assert db.salla_ai_order_quotes.rows==[]
