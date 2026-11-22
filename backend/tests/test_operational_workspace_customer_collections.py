"""Executed additional receipts: cap, identity, permissions and late matching."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException

from operational_balance_service import report
from operational_balance_store import STATES, digest, mutate, read
from operational_customer_collection_effects import bank_movement_effect, collection_match
from operational_financial_activity import operational_rows
from operational_workspace_customer_collections import _fee_quote
from test_operational_balance_workspace import grant, resource, run
from test_operational_workspace_refunds import order_fixture


BASE = "/api/operational-balances/workspace/customer-collections"
EXECUTED = "2026-11-10T12:00:00+00:00"


def quote_body(**updates):
    return {"order_number": "1001", "amount": "40.00", "source_type": "bank", "source_id": "bank",
            "payment_method": None, "fee_mode": "new_transaction", "original_reference": None,
            "reference": "receipt-" + uuid4().hex, "executed_at": EXECUTED, **updates}


async def command(client, body=None):
    body = body or quote_body()
    response = await client.post(BASE + "/quote", json=body)
    assert response.status_code == 200, response.text
    quoted = response.json()
    return {**body, "request_id": "collection-" + uuid4().hex,
            "expected_session_scope": digest(["owner", "owner"]),
            "expected_basis_hash": quoted["expected_basis_hash"], "expected_quote_hash": quoted["quote_hash"],
            "reason": "تحصيل قيمة المنتجات المضافة", "confirmed": True}


def bank_amount(state):
    return next(row["actual"] for row in report(state)["parties"]
                if row["party_type"] == "bank" and row["party_id"] == "bank")


def test_partial_collection_once_and_original_paid_stays_proved(monkeypatch):
    async def scenario(db, client):
        await order_fixture(db, paid="200.00", total="300.00")
        original_order = await db.unified_orders.find_one({"user_id": "owner"})
        lookup = await client.get(BASE + "/order/1001", params={"include_history": True})
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["paid_amount"] == "200.00"
        assert lookup.json()["pending_collection_amount"] == "100.00"
        payload = await command(client)
        # Force two writers to reach the same revision-conditioned commit.
        # This exercises replay after a lost CAS, not sequential HTTP retries.
        collection_type = type(db[STATES])
        original_replace = collection_type.replace_one
        ready, calls = asyncio.Event(), 0
        async def racing_replace(collection, *args, **kwargs):
            nonlocal calls
            if collection.name == STATES and calls < 2:
                calls += 1
                if calls == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 5)
            return await original_replace(collection, *args, **kwargs)
        monkeypatch.setattr(collection_type, "replace_one", racing_replace)
        replies = await asyncio.gather(*[client.post(BASE, json=payload) for _ in range(3)])
        assert all(reply.status_code == 200 for reply in replies), [reply.text for reply in replies]
        assert len({reply.json()["item"]["id"] for reply in replies}) == 1
        state = await read(db, "owner")
        assert len(state["customer_collections"]) == 1 and state["movements"] == []
        assert bank_amount(state) == "1040.00"
        assert not state.get("engine", {}).get("facts")
        assert await db.unified_orders.find_one({"user_id": "owner"}) == original_order
        after = (await client.get(BASE + "/order/1001")).json()
        assert after["pending_collection_amount"] == "60.00" and after["paid_amount"] == "240.00"
        saved = replies[0].json()["item"]
        assert saved["fee_amount"] == "0.00" and saved["net_amount"] == "40.00"
        assert saved["recorded_by"] == {"id": "owner", "name": "محمد عبدالله"}
        ledger = await client.get("/api/operational-balances/workspace/ledger/bank/bank")
        assert ledger.status_code == 200, ledger.text
        assert any(item["id"] == "customer_collection:" + saved["id"] and item["amount"] == "40.00"
                   for item in ledger.json()["items"])
        directory = await client.get("/api/operational-balances/workspace/parties", params={"kind": "bank"})
        assert directory.status_code == 200, directory.text
        assert next(item for item in directory.json()["items"] if item["id"] == "bank")["balance"]["amount"] == "1040.00"
    run(scenario)


def test_concurrent_independent_collections_cannot_exceed_remainder():
    async def scenario(db, client):
        await order_fixture(db, paid="200.00", total="300.00")
        first = await command(client, quote_body(amount="60.00"))
        second = await command(client, quote_body(amount="60.00"))
        replies = await asyncio.gather(client.post(BASE, json=first), client.post(BASE, json=second))
        assert sorted(reply.status_code for reply in replies) == [200, 409], [reply.text for reply in replies]
        assert bank_amount(await read(db, "owner")) == "1060.00"
        assert (await client.get(BASE + "/order/1001")).json()["pending_collection_amount"] == "40.00"
    run(scenario)


def test_changed_order_and_rejected_oversize_intent_do_not_later_apply():
    async def scenario(db, client):
        await order_fixture(db, paid="200.00", total="300.00")
        too_much = await command(client, quote_body(amount="101.00"))
        denied = await client.post(BASE, json=too_much)
        assert denied.status_code == 409 and denied.json()["detail"]["not_applied"] is True
        valid = await command(client)
        await db.unified_orders.update_one({"user_id": "owner"}, {"$set": {"raw_by_source.salla_direct.total": "400.00"}})
        changed = await client.post(BASE, json=valid)
        assert changed.status_code == 409 and changed.json()["detail"]["code"] == "operational_collection_basis_changed"
        retried = await client.post(BASE, json=too_much)
        assert retried.status_code == 409 and retried.json()["detail"]["code"] == "operational_collection_amount_exceeded"
        assert bank_amount(await read(db, "owner")) == "1000.00"
    run(scenario)


def test_permission_revocation_and_foreign_receipt_are_rejected():
    async def scenario(db, client):
        await order_fixture(db, paid="200.00", total="300.00")
        assert (await client.get(BASE + "/order/1001", headers={"x-test-user": "staff"})).status_code == 403
        await grant(db, operations=["customer.collection"], resources=[resource("bank", "bank")])
        payload = await command(client)
        payload["expected_session_scope"] = digest(["owner", "staff"])
        await grant(db, operations=[], resources=[], version=2)
        denied = await client.post(BASE, json=payload, headers={"x-test-user": "staff"})
        assert denied.status_code == 403
        owner_payload = await command(client)
        owner_payload["receipt_id"] = "a" * 64
        denied = await client.post(BASE, json=owner_payload)
        assert denied.status_code == 409
        assert bank_amount(await read(db, "owner")) == "1000.00"
    run(scenario)


def test_late_bank_movement_matches_once_but_customer_coverage_is_retained():
    async def scenario(db, client):
        await order_fixture(db, paid="200.00", total="300.00")
        payload = await command(client)
        response = await client.post(BASE, json=payload)
        assert response.status_code == 200, response.text
        async def add_statement(state):
            state["movements"].append({"id": "source-bank-receipt", "request_id": "statement-receipt",
                "kind": "collection", "direction": "incoming", "party_type": "bank", "party_id": "bank",
                "bank_id": "bank", "bank_kind": "bank", "bank_name": "الإنماء", "name": "الإنماء",
                "order_id": "order-1001", "order_number": "1001", "currency": "SAR", "amount": "40.00",
                "reference": payload["reference"], "occurred_at": EXECUTED, "actor_id": "owner",
                "allocations": [], "note": "إثبات الحوالة الوارد"})
        await mutate(db, "owner", add_statement)
        state = await read(db, "owner")
        assert collection_match(state, state["customer_collections"][0]) == "bank-movement:source-bank-receipt"
        assert bank_amount(state) == "1040.00"
        activity, _ = operational_rows(state)
        incoming = [row for row in activity if row["account_id"] == "bank" and row["direction"] == "incoming"]
        assert len(incoming) == 1 and incoming[0]["amount"] == "40.00"
        directory = await client.get("/api/operational-balances/workspace/parties", params={"kind": "bank"})
        assert directory.status_code == 200, directory.text
        assert next(item for item in directory.json()["items"] if item["id"] == "bank")["balance"]["amount"] == "1040.00"
        lookup = (await client.get(BASE + "/order/1001")).json()
        assert lookup["paid_amount"] == "240.00" and lookup["pending_collection_amount"] == "60.00"
    run(scenario)


@pytest.mark.parametrize("movement,expected", [
    ({"kind": "collection", "direction": "incoming"}, "0.00"),
    ({"kind": "transfer", "direction": "incoming", "bank_kind": "bank", "bank_id": "another-bank"}, "-40.00"),
    ({"kind": "transfer", "direction": "outgoing", "bank_kind": "bank", "bank_id": "another-bank"}, "40.00"),
    ({"kind": "collection", "direction": "incoming", "bank_kind": "bank", "bank_id": "bank"}, "40.00"),
])
def test_bank_match_requires_actual_signed_cash_leg(movement, expected):
    row = {"party_type": "bank", "party_id": "bank", "currency": "SAR", "amount": "40.00", **movement}
    assert format(bank_movement_effect(row, "bank"), ".2f") == expected
    state = {"engine": {}, "movements": [{"id": "candidate", "order_id": "one", "reference": "ref", **row}]}
    case = {"id": "manual", "order_id": "one", "currency": "SAR", "amount": "40.00", "status": "confirmed",
            "destination_type": "bank", "destination_id": "bank", "reference": "ref"}
    assert bool(collection_match(state, case)) == (expected == "40.00")


def test_new_transaction_fee_and_same_transaction_delta_do_not_repeat_fixed_fee():
    settings = {"payment_methods": [{"name": "tamara", "commission_percent": "5", "fixed_fee": "1", "vat_percent": "15"}]}
    state = {"engine": {"provider_fee_inputs": {"settings": settings}, "facts": {
        "capture:original": {"order_id": "order-1001", "provider": "tamara", "amount": "200.00"}}}}
    basis = {"order_id": "order-1001", "provider": "tamara"}
    body = quote_body(source_type="provider", source_id="tamara", amount="100.00", payment_method="tamara")
    new = _fee_quote(state, basis, body, "2026-11-22T00:00:00+00:00")
    same = _fee_quote(state, basis, {**body, "fee_mode": "same_transaction", "original_reference": "original"},
                      "2026-11-22T00:00:00+00:00")
    assert new["fee_amount"] == "6.90" and same["fee_amount"] == "5.75"
    assert new["net_amount"] == "93.10" and same["net_amount"] == "94.25"
    assert new["fee_status"] == same["fee_status"] == "estimated"


def test_same_transaction_cannot_switch_salla_payment_rail_to_change_fee():
    state = {"engine": {"provider_fee_inputs": {"orders": {"order-1001": {"payment": {"payment_method": "mada"}}}},
                        "facts": {"capture:original": {"order_id": "order-1001", "provider": "salla", "amount": "200.00"}}}}
    payload = quote_body(source_type="provider", source_id="salla", amount="100.00", payment_method="visa",
                         fee_mode="same_transaction", original_reference="original")
    with pytest.raises(HTTPException) as caught:
        _fee_quote(state, {"order_id": "order-1001", "provider": "salla"}, payload, "2026-11-22T00:00:00+00:00")
    assert caught.value.detail["code"] == "operational_collection_original_method_required"


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity", "1.001"])
def test_invalid_collection_amount_never_writes(amount):
    async def scenario(db, client):
        await order_fixture(db, paid="200.00", total="300.00")
        response = await client.post(BASE + "/quote", json=quote_body(amount=amount))
        assert response.status_code == 422
        assert not (await read(db, "owner")).get("customer_collections")
    run(scenario)
