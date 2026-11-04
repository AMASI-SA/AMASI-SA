from copy import deepcopy

import pytest
from fastapi import HTTPException

from operational_balance_engine import reconcile
from operational_balance_sources import collect_sources
from operational_balance_store import STATES, digest, initial, read
from operational_historical_shipping_service import approve, preview
from test_operational_balance_delivery_cutover import active, courier_data, fee_total, START, NOW
from test_operational_balance_integration import run
from test_operational_balance_verified_shipping_fees import fixture as driver_fixture


async def prepare(db, kind):
    state = {**initial("owner"), **active(), "revision": 1, "engine": {"as_of": NOW}}
    await db[STATES].insert_one(state)
    data = courier_data() if kind == "courier" else driver_fixture()
    for name, docs in data.items():
        if docs:
            await db[name].insert_many(docs)
    return state


def consent(p):
    return {"request_id": "shipping-review-1", "order_number": p["order_number"],
            "expected_session_scope": p["session_scope"], "expected_preview_hash": p["preview_hash"],
            "evidence_ref": "synthetic opening and unpaid statement reconciliation",
            "confirm_opening_and_payouts": True}


async def refreshed(db, before):
    sources = await collect_sources(db, "owner", START, NOW, operational_state=before)
    return reconcile(before, sources, NOW)


@pytest.mark.parametrize("kind", ["courier", "store_driver"])
def test_review_approval_and_reread_release_only_base_fee(kind):
    async def scenario(db):
        original = await prepare(db, kind)
        baseline = await refreshed(db, original)
        assert fee_total(baseline) == (12 if kind == "courier" else 10)
        p = await preview(db, "owner", "owner", "10", clock=NOW)
        assert p["can_approve"], p
        assert p["candidate"]["amount"] == "10.00" and p["candidate"]["party_type"] == kind
        assert p["candidate"]["obligation_id"] == "shipping:100:base_shipping"
        assert original == await read(db, "owner")
        req = consent(p)
        result = await approve(db, "owner", "owner", req, clock=NOW)
        assert result["pending_reconciliation"] is True
        assert (await approve(db, "owner", "owner", req, clock=NOW)) == result
        after = await read(db, "owner")
        for field in ("engine", "movements", "openings"):
            assert after[field] == original[field]
        assert len(after["audit"]) == 1
        assert not (await preview(db, "owner", "owner", "10", clock=NOW))["can_approve"]
        current = await refreshed(db, after)
        assert fee_total(current) == 10, current["engine"].get("issues")
        assert current["engine"]["obligations"]["shipping:100:base_shipping"]["confirmed"] == "10.00"
        assert "shipping:100:cod_commission" not in current["engine"]["obligations"]
        if kind == "store_driver":
            assert current["engine"]["obligations"] == baseline["engine"]["obligations"]
            assert current["engine"]["cod_reports"] == baseline["engine"]["cod_reports"]
        else:
            assert set(current["engine"]["obligations"]) == {"shipping:100:base_shipping"}
            assert current["engine"]["cod_reports"] == {}
        again = await refreshed(db, current)
        assert again["engine"]["obligations"] == current["engine"]["obligations"]
        assert again["movements"] == after["movements"] and again["openings"] == original["openings"]
        # Settling later cannot cause another payout or a different base fee key.
        paid = deepcopy(current)
        paid["movements"].append({"id":"paid", "party_type":kind, "party_id":p["candidate"]["party_id"],
            "currency":"SAR", "direction":"outgoing", "kind":"payment", "amount":"10.00",
            "allocations":[{"obligation_id":"shipping:100:base_shipping","amount":"10.00"}]})
        rechecked = await refreshed(db, paid)
        assert rechecked["engine"]["obligations"] == paid["engine"]["obligations"]
        assert rechecked["movements"] == paid["movements"]
    run(scenario)


@pytest.mark.parametrize("kind", ["courier", "store_driver"])
def test_changed_evidence_between_preview_approval_or_refresh_fails_closed(kind):
    async def scenario(db):
        await prepare(db, kind)
        p = await preview(db, "owner", "owner", "10", clock=NOW)
        req = consent(p)
        for actor, expected in [("staff", 403), ("owner", 422)]:
            with pytest.raises(HTTPException) as exc:
                await approve(db, "owner", actor, {**req, "confirm_opening_and_payouts":False}, clock=NOW)
            assert exc.value.status_code == expected
        with pytest.raises(HTTPException):
            await approve(db, "owner", "owner", {**req, "expected_session_scope":digest(["other"])}, clock=NOW)
        bad = {"id": "unallocated", "party_type":kind, "party_id":p["candidate"]["party_id"], "currency":"SAR",
               "direction":"outgoing", "kind":"payment", "amount":"10.00", "allocations":[]}
        await db[STATES].update_one({"_id":"owner"}, {"$inc":{"revision":1}, "$push":{"movements":bad}})
        with pytest.raises(HTTPException):
            await approve(db,"owner","owner",req,clock=NOW)
        blocked = await preview(db,"owner","owner","10",clock=NOW)
        assert not blocked["can_approve"]
        await db[STATES].update_one({"_id":"owner"}, {"$set":{"movements":[]}, "$inc":{"revision":1}})
        req = consent(await preview(db,"owner","owner","10",clock=NOW))
        await approve(db,"owner","owner",req,clock=NOW)
        state = await read(db,"owner")
        if kind == "courier":
            await db.mz2_shipping_setup_v2.update_one({"user_id":"owner"},{"$set":{"contracts.0.delivery_fee":"20"}})
        else:
            await db.store_delivery_driver_earnings.update_one({"user_id":"owner"},{"$set":{"amount":"20"}})
        first = await refreshed(db,state)
        assert fee_total(first) == 0
        assert any(i["code"]=="historical_shipping_fee_source_changed" for i in first["engine"]["issues"])
        assert first["movements"] == [] and first["openings"] == state["openings"]
    run(scenario)


@pytest.mark.parametrize("event,kind", [
    ({"kind":"courier_fee","facts":{"order_number":"10"}}, "courier"),
    ({"kind":"courier_fee","facts":{"order_number":"another","waybill":"TEST-AWB"}}, "courier"),
    ({"kind":"store_driver_cod","facts":{"assignment_id":"assignment","delivery_fee":"10.00"}}, "store_driver"),
    ({"kind":"shipping_settlement","facts":{"counterparty_type":"courier","counterparty_id":"carrier",
        "settlement_type":"net_settlement","offset_amount":"5.00"}}, "courier"),
    ({"kind":"shipping_settlement","facts":{"counterparty_type":"store_driver","counterparty_id":"driver",
        "settlement_type":"fee_payment","offset_amount":"0.00"}}, "store_driver"),
])
def test_native_shipping_fee_or_pooled_offset_blocks_release_without_mutating_the_native_source(event, kind):
    async def scenario(db):
        state = await prepare(db, kind)
        await db.mz2_shipping_accounting_events.insert_one({**event,"user_id":"other","status":"posted"})
        p = await preview(db,"owner","owner","10",clock=NOW)
        assert p["can_approve"]
        await db.mz2_shipping_accounting_events.insert_one({**event,"user_id":"owner","status":"posting"})
        assert not (await preview(db,"owner","owner","10",clock=NOW))["can_approve"]
        with pytest.raises(HTTPException):
            await approve(db,"owner","owner",consent(p),clock=NOW)
        assert await read(db,"owner") == state
        await db.mz2_shipping_accounting_events.delete_one({"user_id":"owner"})
        await approve(db,"owner","owner",consent(p),clock=NOW)
        state = await read(db,"owner")
        await db.mz2_shipping_accounting_events.insert_one({**event,"user_id":"owner","status":"posted"})
        result = await refreshed(db, state)
        assert fee_total(result) == 0 and result["movements"] == []
        assert any(i["code"]=="historical_shipping_fee_native_overlap" for i in result["engine"]["issues"])
        assert await db.mz2_shipping_accounting_events.count_documents({"user_id":"owner","status":"posted"}) == 1
    run(scenario)


@pytest.mark.parametrize("e", [
    {"kind":"fee","order_number":"10","party_type":"courier","party_id":"another"},
    {"kind":"settlement","party_type":"courier","party_id":"carrier","action":"pay_fee"},
])
def test_v2_shipping_posting_or_settlement_blocks_same_fee(e):
    async def scenario(db):
        await prepare(db,"courier")
        await db.mz2_shipping_native_events_v2.insert_one({**e,"user_id":"owner"})
        assert not (await preview(db,"owner","owner","10",clock=NOW))["can_approve"]
    run(scenario)


@pytest.mark.parametrize('kind', ['courier', 'store_driver'])
def test_unapproved_poststart_fee_checks_native_postings_and_recovers_once(kind):
    async def scenario(db):
        state = await prepare(db, kind)
        original = await db.mz2_shipping_native_events_v2.insert_one({
            'user_id':'owner', 'kind':'fee', 'order_number':'10', 'party_type':kind,
            'party_id':'carrier' if kind == 'courier' else 'driver'})
        held = await refreshed(db, state)
        assert fee_total(held) == 0
        assert held['engine']['obligations']['cod:100']['confirmed'] == '200.00'
        code = 'courier_fee_native_overlap' if kind == 'courier' else 'driver_fee_native_overlap'
        assert any(issue['code'] == code for issue in held['engine']['issues'])
        assert await db.mz2_shipping_native_events_v2.count_documents({'user_id':'owner'}) == 1
        await db.mz2_shipping_native_events_v2.delete_one({'_id':original.inserted_id})
        current = await refreshed(db, held)
        assert fee_total(current) == (12 if kind == 'courier' else 10)
        assert current['movements'] == [] and current['openings'] == state['openings']
        again = await refreshed(db, current)
        assert again['engine']['obligations'] == current['engine']['obligations']
        assert again['engine']['facts'] == current['engine']['facts']
    run(scenario)
