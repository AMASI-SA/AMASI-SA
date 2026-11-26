"""Synthetic, private-vault-backed order: customer PII never in bot action."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from salla_order_creation.private_fields import (
    save_private_order_field,
)
from salla_order_creation.private_order_builder import (
    PrivateOrderAssemblyDenied, assemble_pending_from_private_fields,
    prepare_from_private_vault,
)
from salla_order_creation.quote_cipher import decrypt_order_payload

NOW=datetime.fromtimestamp(1_800_000_000,tz=timezone.utc)


def fields():
    return {
        "buyer_name":"Synthetic Buyer",
        "buyer_mobile":"+966500000000",
        "buyer_email":"buyer@example.invalid",
        "recipient_name":"Synthetic Recipient",
        "recipient_phone":"+966500000001",
        "address_line":"Synthetic street",
        "street_number":"11",
        "postal_code":"12345",
        "short_address":"RHMA3184",
        "building_number":"11",
        "additional_number":"22",
        "geo_lat":"24.7000",
        "geo_lng":"46.7000",
    }


def selected_products():
    return [{"identifier_type":"id","identifier":400,"quantity":1}]


def params(**changes):
    p={
        "receiver_country_code":"SA",
        "country_id":1,"city_id":2,"district_id":3,"courier_id":220,
        "products":selected_products(),
        "accepted_methods":["mada"],
    }
    p.update(changes)
    return p


class Rows:
    def __init__(self,ttl=False):
        self.rows=[]
        self.index={
            "salla_assisted_private_fields_ttl_v1":{
                "key":[("purge_after",1)],"expireAfterSeconds":0,
            },
        } if ttl else {}

    async def index_information(self):
        return self.index

    async def find_one(self,query):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                return dict(row)
        return None

    async def insert_one(self,row):
        if any(x["_id"]==row["_id"] for x in self.rows):
            raise ValueError("duplicate")
        self.rows.append(dict(row))

    async def update_one(self,query,update):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                row.update(update.get("$set",{}))
                row["revision"] += update.get("$inc",{}).get("revision",0)
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


class DB:
    def __init__(self):
        self.salla_ai_private_order_fields=Rows(ttl=True)
        self.salla_ai_order_quotes=Rows()
        self.salla_ai_order_intents=Rows()


@pytest.fixture(autouse=True)
def mock_private_key(monkeypatch):
    monkeypatch.setenv(
        "MEZAN_ASSISTED_SALLA_PRIVATE_FIELDS_KEY",
        Fernet.generate_key().decode("ascii"),
    )


def vault(db):
    for idx,(field,value) in enumerate(fields().items(),start=101):
        asyncio.run(save_private_order_field(
            db,owner_id="owner",account_id=1,inbox_id=2,
            conversation_id=3,intent_id="intent-0001",
            contact_id=77,message_id=idx,
            field=field,value=value,now=NOW,
        ))


def test_assembler_from_complete_private_facts():
    o=assemble_pending_from_private_fields(fields(),**params())
    assert o.receiver.name=="Synthetic Recipient"
    assert o.receiver.phone=="+966500000001"
    assert o.customer.mobile=="+966500000000"
    assert o.ship_to.geo_coordinates.lat==24.7
    assert o.accepted_methods==["mada"]


def test_missing_email_or_2026_national_address_rejected():
    f=fields()
    f.pop("buyer_email")
    with pytest.raises(PrivateOrderAssemblyDenied,match="incomplete"):
        assemble_pending_from_private_fields(f,**params())
    f=fields()
    f.pop("geo_lat")
    with pytest.raises(PrivateOrderAssemblyDenied,match="coordinates_required"):
        assemble_pending_from_private_fields(f,**params())


def test_private_order_fields_never_come_from_signed_selection():
    for overrides in (
        {"buyer_mobile":"+966512345678"},
        {"customer":{"email":"other@example.invalid"}},
        {"checkout_url":"https://evil.invalid"},
        {"payment":{"status":"paid"}},
    ):
        with pytest.raises((TypeError,PrivateOrderAssemblyDenied)):
            assemble_pending_from_private_fields(fields(),**params(**overrides))


def test_catalog_personalized_text_requires_separate_private_option_vault():
    row=selected_products()[0].copy()
    row["options"]=[{"id":50,"value":["اسم هدية"]}]
    with pytest.raises(PrivateOrderAssemblyDenied,match="personalization"):
        assemble_pending_from_private_fields(
            fields(),**params(products=[row]),
        )


async def product_reader(db,owner,product_id):
    assert owner=="owner" and product_id==400
    return {
        "source":"salla_product_details","product_id":400,
        "can_use_as_final_quote":False,
        "taxed_base_price_sar":"80.00",
        "available_quantity":2,"options":[],"variants":[],
    }


async def rate_reader(db,owner,**args):
    assert args["country_id"]==1 and args["city_id"]==2
    return [{
        "rate_id":888,"shipping_company_id":220,
        "destination_country_id":1,"destination_city_id":2,
        "estimated_shipping_sar":"20.00",
        "cod_service_found":False,"cod_fee_estimate_sar":None,
        "source":"salla_shipping_rate_estimate","is_final_cart_quote":False,
    }]


async def methods_reader(db,owner):
    return {
        "source":"salla_payment_methods",
        "eligible_create_order_method_candidates":["mada"],
        "can_create_order":False,
    }


def test_end_to_end_encrypted_private_fields_to_unpriced_quote():
    db=DB()
    vault(db)
    data=asyncio.run(prepare_from_private_vault(
        db,owner_id="owner",account_id=1,inbox_id=2,
        conversation_id=3,intent_id="intent-0001",
        contact_id=77,current_message_id=900,
        selected_rate_id=888,enabled=True,
        **params(),now=NOW,
        product_reader=product_reader,rate_reader=rate_reader,
        methods_reader=methods_reader,
    ))
    assert data["state"]=="ready_for_customer_confirmation"
    assert data["verified_total_sar"] is None
    assert data["checkout_url"] is None
    assert data["pricing_mode"]=="salla_post_create"
    record=db.salla_ai_order_quotes.rows[0]
    assert "order_payload" not in record
    assert isinstance(record["encrypted_order_payload"],bytes)
    assert b"buyer@example.invalid" not in record["encrypted_order_payload"]
    o=decrypt_order_payload(
        record["encrypted_order_payload"],
        owner_id="owner",intent_id="intent-0001",
    )
    assert o.customer.email=="buyer@example.invalid"
    assert o.receiver.name=="Synthetic Recipient"
    assert record["source_contact_id"]==77
    assert record["source_message_id"]==900


def test_wrong_contact_cannot_read_private_vault_or_prepare_quote():
    db=DB()
    vault(db)
    with pytest.raises(PrivateOrderAssemblyDenied,match="fields_unavailable"):
        asyncio.run(prepare_from_private_vault(
            db,owner_id="owner",account_id=1,inbox_id=2,
            conversation_id=3,intent_id="intent-0001",
            contact_id=99,current_message_id=900,
            selected_rate_id=888,enabled=True,
            **params(),now=NOW,
            product_reader=product_reader,rate_reader=rate_reader,
            methods_reader=methods_reader,
        ))
    assert db.salla_ai_order_quotes.rows==[]


def test_default_off_private_quote_does_not_decrypt_or_read():
    db=DB()
    vault(db)
    with pytest.raises(PrivateOrderAssemblyDenied,match="disabled"):
        asyncio.run(prepare_from_private_vault(
            db,owner_id="owner",account_id=1,inbox_id=2,
            conversation_id=3,intent_id="intent-0001",
            contact_id=77,current_message_id=900,
            selected_rate_id=888,enabled=False,
            **params(),now=NOW,
            product_reader=product_reader,rate_reader=rate_reader,
            methods_reader=methods_reader,
        ))
    assert db.salla_ai_order_quotes.rows==[]



def test_selected_personalization_placeholder_is_resolved_from_private_vault_only():
    private=fields()
    private["option_text_50"]="اسم هدية خاص"
    selection=[{
        "identifier_type":"id","identifier":400,"quantity":1,
        "options":[{"id":50,"value":["private.option_text_50"]}],
    }]
    order=assemble_pending_from_private_fields(
        private,**params(products=selection),
    )
    assert order.products[0].options[0].value==["اسم هدية خاص"]
    assert "اسم هدية خاص" not in str(selection)


def test_missing_or_wrong_engraving_placeholder_does_not_guess_name():
    for ref in ("private.option_text_50","private.option_text_51"):
        selected=[{
            "identifier_type":"id","identifier":400,"quantity":1,
            "options":[{"id":50,"value":[ref]}],
        }]
        with pytest.raises(PrivateOrderAssemblyDenied):
            assemble_pending_from_private_fields(
                fields(),**params(products=selected),
            )
