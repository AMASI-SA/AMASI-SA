"""Synthetic provider-owned unpaid order intent writer, no Salla POST."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from salla_order_creation.intent_writer import (
    PrepareDenied, prepare_salla_unpaid_order_intent,
)
from salla_order_creation.service import quote_pricing_mode
from salla_order_creation.payload import PendingOrder

NOW = datetime.fromtimestamp(1_800_000_000, tz=timezone.utc)


class Rows:
    def __init__(self):
        self.rows=[]

    async def insert_one(self,row):
        if any(x["_id"]==row["_id"] for x in self.rows):
            raise ValueError("synthetic unique Mongo ID")
        self.rows.append(dict(row))

    async def find_one(self,query):
        for row in self.rows:
            if all(row.get(k)==v for k,v in query.items()):
                return dict(row)
        return None


class DB:
    def __init__(self):
        self.salla_ai_order_quotes=Rows()


def order(methods=None, quantity=1):
    return PendingOrder.model_validate({
        "customer":{"id":501},
        "receiver":{"name":"Synthetic Recipient","phone":"+966500000000","country_code":"SA"},
        "courier_id":"220",
        "ship_to":{
            "country":1,"city":2,"district":3,"block":None,
            "street_number":"11","address_line":"Synthetic street",
            "postal_code":"12345","short_address":"RHMA3184",
            "building_number":"11","additional_number":"22",
            "geo_coordinates":{"lat":24.7,"lng":46.7},
        },
        "accepted_methods":methods or ["mada"],
        "products":[{"identifier_type":"id","identifier":400,"quantity":quantity,
                     "options":[{"id":50,"value":["601"]}]}],
    })


def product():
    return {
        "source":"salla_product_details", "product_id":400,
        "can_use_as_final_quote":False, "taxed_base_price_sar":"98.00",
        "available_quantity":4,
        "options":[{
            "option_id":50,"type":"radio","required":True,
            "values":[{"value_id":601,"price_delta_sar":"0.00",
                       "out_of_stock":False}],
        }],
        "variants":[{
            "variant_id":700,"selected_option_value_ids":[601],
            "available_quantity":4,"provider_variant_price_sar":"98.00",
        }],
    }


def rate():
    return [{
        "rate_id":800,"shipping_company_id":220,
        "destination_country_id":1,"destination_city_id":2,
        "estimated_shipping_sar":"20.00",
        "cod_service_found":False,"cod_fee_estimate_sar":None,
        "source":"salla_shipping_rate_estimate",
        "is_final_cart_quote":False,
    }]


def methods(allowed=None):
    return {
        "source":"salla_payment_methods",
        "eligible_create_order_method_candidates":allowed or ["mada","credit_card"],
        "can_create_order":False,
        "verified_carrier_eligibility":False,
    }


async def read_product(db,owner,product_id):
    assert owner=="owner" and product_id==400
    return product()


async def read_rates(db,owner,**kwargs):
    assert kwargs["country_id"]==1 and kwargs["city_id"]==2
    return rate()


async def read_methods(db,owner):
    return methods()


def create(db=None,o=None,now=NOW,enabled=True,**kwargs):
    target=db or DB()
    async def invoke():
        return await prepare_salla_unpaid_order_intent(
            target,user_id="owner",intent_id="intent-1234",
            inbox_id="2",conversation_id="3",
            order=o or order(), selected_rate_id=800,
            enabled=enabled, product_reader=kwargs.get("product_reader",read_product),
            rate_reader=kwargs.get("rate_reader",read_rates),
            methods_reader=kwargs.get("methods_reader",read_methods),
            now=now,
        )
    return target,asyncio.run(invoke())


def test_verified_cart_input_without_invented_total():
    db,out=create()
    assert out["state"]=="ready_for_customer_confirmation"
    assert out["verified_total_sar"] is None
    assert out["checkout_url"] is None
    assert out["pricing_mode"]=="salla_post_create"
    assert "Synthetic" not in str(out)
    assert "+966" not in str(out)
    assert len(db.salla_ai_order_quotes.rows)==1
    persisted=db.salla_ai_order_quotes.rows[0]
    assert quote_pricing_mode(persisted)=="salla_post_create"
    assert persisted["pricing_verified"] is False
    assert persisted["shipping_price_verified"] is False
    assert persisted["carrier_eligibility_verified"] is True
    assert persisted["post_create_salla_total"] is True
    assert persisted["total_sar"] is None
    assert persisted["order_payload"]["payment"] if False else True
    assert persisted["purge_after"]>persisted["expires_at"]


def test_repeat_intent_returns_same_quote_ref():
    db=DB()
    _,first=create(db=db)
    _,second=create(db=db)
    assert first==second
    assert len(db.salla_ai_order_quotes.rows)==1


def test_changed_cart_cannot_reuse_previously_approved_intent():
    db=DB()
    create(db=db)
    with pytest.raises(PrepareDenied,match="different_cart"):
        create(db=db,o=order(quantity=2))


def test_expired_old_quote_requires_new_intent():
    db=DB()
    create(db=db)
    with pytest.raises(PrepareDenied,match="expired"):
        create(db=db,now=NOW+timedelta(minutes=12))


@pytest.mark.parametrize("method",["cod","bank"])
def test_bank_and_cod_have_no_auto_salla_post_create_pricing(method):
    with pytest.raises(PrepareDenied,match="online_only"):
        create(o=order(methods=[method]))


def test_disabled_quote_writer_does_no_provider_calls():
    async def unavailable(*args,**kwargs):
        raise AssertionError("provider must not be called")
    with pytest.raises(PrepareDenied,match="disabled"):
        create(enabled=False,product_reader=unavailable,
               rate_reader=unavailable,methods_reader=unavailable)


def test_customer_selected_payment_method_must_be_enabled():
    async def payment_disabled(db,owner):
        return methods(allowed=["credit_card"])
    with pytest.raises(PrepareDenied,match="method_not_enabled"):
        create(methods_reader=payment_disabled)


def test_product_stock_exhausted_rejects_quote():
    async def out_of_stock(db,owner,pid):
        p=product()
        p["variants"][0]["available_quantity"]=0
        return p
    with pytest.raises(PrepareDenied,match="salla_cart_inputs_unverified"):
        create(product_reader=out_of_stock)


def test_bad_courier_or_city_rejects_order_intent():
    async def wrong_city(db,owner,**kwargs):
        data=rate()
        data[0]["destination_city_id"]=99
        return data
    with pytest.raises(PrepareDenied,match="salla_cart_inputs_unverified"):
        create(rate_reader=wrong_city)


def test_failed_product_read_never_writes_verified_quote():
    async def outage(db,owner,pid):
        from salla_order_creation.readers import SourceUnavailable
        raise SourceUnavailable("synthetic merchant token missing products.read")
    db=DB()
    with pytest.raises(PrepareDenied,match="salla_cart_inputs_unverified"):
        create(db=db,product_reader=outage)
    assert db.salla_ai_order_quotes.rows==[]


def test_default_off_requires_explicit_owner_activation():
    with pytest.raises(PrepareDenied):
        create(enabled=False)
