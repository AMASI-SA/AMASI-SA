from asyncio import gather
from copy import deepcopy
from decimal import Decimal

import pytest

from salla_paid_sar_evidence import FIELD, list_payment_evidence, matching_sar_payment
from salla_integration import paid_sar_sync
from test_operational_balance_integration import run


def pair(number=1):
    number = str(number)
    common = {"id":"order-"+number, "reference_id":number, "date":"2026-09-15T12:00:00Z",
              "status":{"slug":"in_progress"}, "payment_method":"mada",
              "payment_methods":[{"payment_method":"mada","amount":"10.00","transaction_reference":"transaction-"+number}]}
    def money(value, ccy):
        return {"amount":value, "currency":ccy}
    def actions(value, ccy):
        return {"remaining_action":{"paid_amount":money(value,ccy), "remaining_amount":None, "has_remaining_amount":False},
                "refund_action":{"paid_amount":money(value,ccy), "refund_amount":money(0,ccy),"refunded_transactions":[]}}
    list_row = {**deepcopy(common),"total":money("10.01","KWD"),"payment_actions":actions("121.14","SAR")}
    detail = {**deepcopy(common),"amounts":{"total":money("10.01","KWD")},"currency":"KWD",
              "updated_at":"2026-09-15T12:05:00Z","payment_actions":actions("10.00","KWD")}
    return list_row, detail


def test_exact_base_charge_survives_native_gross_discrepancy_without_inventing_rate_or_due():
    row, raw = pair()
    owner = {"user_id":"tenant","order_number":"1","order_id":"order-1"}
    owner[FIELD] = list_payment_evidence("tenant",row)
    assert matching_sar_payment(owner,raw,owner="tenant",native_paid="10.00") == Decimal("121.14")
    assert matching_sar_payment(owner,raw,owner="tenant",native_paid="10.01") is None
    assert matching_sar_payment({k:v for k,v in owner.items() if k != FIELD},raw,owner="tenant") is None
    assert matching_sar_payment(owner,row,owner="tenant") == Decimal("121.14")


@pytest.mark.parametrize("bad",["foreign_owner","id","ref","cancelled","declined","currency","created",
                                "tender","paid","refund","revision","watermark","salla_id"])
def test_changed_owner_or_source_does_not_reuse_stored_sar(bad):
    row, raw = pair()
    if bad == "revision":
        row["updated_at"] = "2026-09-14T12:02:00Z"
    proof = list_payment_evidence("tenant",row)
    source = {"user_id":"tenant","order_number":"1","order_id":"order-1",FIELD:proof}
    if bad == "foreign_owner": source[FIELD]["owner_id"] = "other"
    if bad == "id": raw["id"] = "different"
    if bad == "ref": raw["reference_id"] = "different"
    if bad == "salla_id": source["salla_order_id"] = "different"
    if bad == "cancelled": raw["status"]["slug"] = "cancelled"
    if bad == "declined": raw["payment_status"] = "declined"
    if bad == "watermark": source["g47_salla_snapshot"] = {"requires_authoritative_refresh":True}
    if bad == "currency": raw["currency"] = "BHD"
    if bad == "created": raw["date"] = "2026-09-16T12:02:00Z"
    if bad == "tender": raw["payment_methods"][0]["transaction_reference"] = "other"
    if bad == "paid": raw["payment_actions"]["remaining_action"]["paid_amount"]["amount"] = 9
    if bad == "refund": raw["payment_actions"]["refund_action"]["refund_amount"]["amount"] = 1
    assert matching_sar_payment(source,raw,owner="tenant") is None


def test_historical_lookup_is_bounded_and_scoped_and_repeat_receives_no_second_claim(monkeypatch):
    rows, requests, calls = {}, [], []
    async def provider(db, user, method, path, *, params):
        n = params["reference_id"]
        assert (user,method,path)==("owner","GET","/orders") and "format" not in params
        calls.append(n)
        return {"data":[rows[n]]}
    monkeypatch.setattr(paid_sar_sync,"call_salla",provider)
    async def scenario(db):
        for i in range(6):
            s, raw = pair(i)
            n = s["reference_id"]
            rows[n] = s; requests.append({"order_id":s["id"],"order_number":n})
            for user in ("owner","other"):
                await db.unified_orders.insert_one({"user_id":user,"order_number":n,"order_id":s["id"],
                    "raw_by_source":{"salla_direct":deepcopy(raw)}})
        await paid_sar_sync.acquire_missing(db,"owner",requests)
        assert len(calls)==4
        await gather(*(paid_sar_sync.acquire_missing(db,"owner",requests) for _ in range(2)))
        assert len(calls)==len(set(calls))==6
        async for doc in db.unified_orders.find({}):
            if doc["user_id"]=="other":
                assert FIELD not in doc and paid_sar_sync._RETRY not in doc
            else:
                assert matching_sar_payment(doc,doc["raw_by_source"]["salla_direct"],owner="owner")==Decimal("121.14")
        assert await db.unified_orders.count_documents({})==12
    run(scenario)
