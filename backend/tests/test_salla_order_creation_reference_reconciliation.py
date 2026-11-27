"""Synthetic Salla GET-only order-reference recovery."""

import asyncio
from types import SimpleNamespace

import pytest

from salla_order_creation.reference_reconciliation import (
    ReferenceUnverified, recover_salla_reference,
)
from salla_order_creation.service import intent_storage_id


class Intents:
    def __init__(self, **override):
        self.row={
            "_id":intent_storage_id("owner","intent-1234"),
            "user_id":"owner","intent_id":"intent-1234",
            "state":"created_amount_unverified","salla_order_id":90001,
            "reference_id":None,
        }
        self.row.update(override)

    async def find_one(self,query):
        if all(self.row.get(k)==v for k,v in query.items()):
            return dict(self.row)
        return None

    async def update_one(self,query,mutation):
        for k,v in query.items():
            if k=="state" and isinstance(v,dict) and "$in" in v:
                if self.row.get(k) not in v["$in"]:
                    return SimpleNamespace(modified_count=0)
            elif self.row.get(k)!=v:
                return SimpleNamespace(modified_count=0)
        self.row.update(mutation["$set"])
        return SimpleNamespace(modified_count=1)


class DB:
    def __init__(self,**kwargs):
        self.salla_ai_order_intents=Intents(**kwargs)


def test_existing_reference_does_not_make_provider_request():
    db=DB(reference_id=302076044)
    async def forbidden(*args,**kwargs):
        raise AssertionError("no provider request needed")
    result=asyncio.run(recover_salla_reference(
        db,user_id="owner",intent_id="intent-1234",provider_get=forbidden,
    ))
    assert result["state"]=="reference_known"
    assert result["reference_id"]==302076044
    assert not result["provider_get_used"]


def test_missing_reference_is_recovered_with_get_only():
    db=DB()
    calls=[]
    async def provider(db_arg,owner,method,path,**kwargs):
        calls.append((owner,method,path,kwargs))
        return {"success":True,"data":{"id":90001,"reference_id":302076044}}
    result=asyncio.run(recover_salla_reference(
        db,user_id="owner",intent_id="intent-1234",provider_get=provider,
    ))
    assert result["state"]=="reference_recovered"
    assert db.salla_ai_order_intents.row["reference_id"]==302076044
    assert calls==[("owner","GET","/orders/90001",{})]


def test_recovered_ref_second_read_is_fully_idempotent():
    db=DB()
    calls=[]
    async def provider(*args,**kwargs):
        calls.append(1)
        return {"success":True,"data":{"id":90001,"reference_id":302076044}}
    first=asyncio.run(recover_salla_reference(
        db,user_id="owner",intent_id="intent-1234",provider_get=provider))
    second=asyncio.run(recover_salla_reference(
        db,user_id="owner",intent_id="intent-1234",provider_get=provider))
    assert first["state"]=="reference_recovered"
    assert second["state"]=="reference_known"
    assert calls==[1]


def test_provider_network_failure_does_not_create_second_order():
    db=DB()
    calls=[]
    async def outage(db_arg,owner,method,path):
        calls.append(method)
        raise TimeoutError("synthetic failed GET")
    result=asyncio.run(recover_salla_reference(
        db,user_id="owner",intent_id="intent-1234",provider_get=outage))
    assert result["state"]=="reference_not_yet_available"
    assert calls==["GET"]
    assert db.salla_ai_order_intents.row["reference_id"] is None


def test_wrong_salla_id_denied():
    db=DB()
    async def mismatch(*args,**kwargs):
        return {"success":True,"data":{"id":90002,"reference_id":302076044}}
    with pytest.raises(ReferenceUnverified,match="provider_order_identity_mismatch"):
        asyncio.run(recover_salla_reference(
            db,user_id="owner",intent_id="intent-1234",provider_get=mismatch))


def test_reference_missing_from_detail_response_is_retryable():
    db=DB()
    async def reply(*args,**kwargs):
        return {"success":True,"data":{"id":90001}}
    result=asyncio.run(recover_salla_reference(
        db,user_id="owner",intent_id="intent-1234",provider_get=reply))
    assert result["state"]=="reference_not_yet_available"
    assert db.salla_ai_order_intents.row["reference_id"] is None


@pytest.mark.parametrize("state",["unknown","sending","cancelled"])
def test_unconfirmed_post_never_looks_up_customer_order(state):
    db=DB(state=state)
    async def forbidden(*args,**kwargs):
        raise AssertionError("no provider GET")
    with pytest.raises(ReferenceUnverified,match="salla_creation_not_confirmed"):
        asyncio.run(recover_salla_reference(
            db,user_id="owner",intent_id="intent-1234",provider_get=forbidden))


def test_other_merchant_cannot_recover_reference():
    db=DB()
    async def forbidden(*args,**kwargs):
        raise AssertionError("no provider GET")
    with pytest.raises(ReferenceUnverified,match="unknown_order_intent"):
        asyncio.run(recover_salla_reference(
            db,user_id="someone_else",intent_id="intent-1234",
            provider_get=forbidden))


def test_salla_false_response_not_trusted():
    db=DB()
    async def no(*args,**kwargs):
        return {"success":False,"data":{"id":90001,"reference_id":302076044}}
    result=asyncio.run(recover_salla_reference(
        db,user_id="owner",intent_id="intent-1234",provider_get=no))
    assert result["state"]=="reference_not_yet_available"
