"""Read-only Salla merchant bank details from verified payments.read scope."""

import asyncio

import pytest

from salla_order_creation.bank_source import read_active_salla_payment_bank
from salla_order_creation.readers import SourceUnavailable

IBAN="SA0380000000608010167519"


async def integration(db, owner):
    return {"status":"connected","scope":"orders.read_write payments.read"}


def source(**modifications):
    data={
        "id":11,"status":"active","bank_name":"Synthetic bank",
        "account_name":"مؤسسة أماسي الخليج التجارية","iban":IBAN,
    }
    data.update(modifications)
    return {"success":True,"data":data}


def get(result=None,scope=integration):
    seen=[]
    async def provider(db,owner,method,path,**kwargs):
        seen.append((owner,method,path))
        return result if result is not None else source()
    try:
        value=asyncio.run(read_active_salla_payment_bank(
            None,"owner",11,request=provider,integration_reader=scope,
        ))
        return seen,value
    except Exception:
        assert len(seen)<=1
        raise


def test_only_active_bank_from_current_store():
    seen,row=get()
    assert seen==[("owner","GET","/payment/banks/11")]
    assert row["bank_id"]==11
    assert row["status"]=="active"
    assert row["source"]=="salla_store_payment_banks"
    assert row["iban"]==IBAN


def test_no_payment_scope_no_bank_read():
    async def no_scope(db,owner):
        return {"status":"connected","scope":"orders.read_write"}
    with pytest.raises(SourceUnavailable,match="payments.read_not_granted"):
        get(scope=no_scope)


@pytest.mark.parametrize("changes",[
    {"id":12},{"status":"inactive"},{"iban":None},
    {"iban":"SA0000000000000000000000"},{"account_name":""},
])
def test_untrusted_bank_metadata_denied(changes):
    with pytest.raises(SourceUnavailable):
        get(result=source(**changes))


def test_api_failed_response_not_accepted():
    with pytest.raises(SourceUnavailable):
        get(result={"success":False,"data":source()["data"]})


def test_pseudo_bank_id_does_not_trigger_get():
    async def bad(*args,**kwargs):
        raise AssertionError("no network")
    with pytest.raises(SourceUnavailable):
        asyncio.run(read_active_salla_payment_bank(
            None,"owner",True,request=bad,integration_reader=integration,
        ))
