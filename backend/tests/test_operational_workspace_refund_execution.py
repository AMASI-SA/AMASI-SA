"""An accountant records an execution once without converting hints to money."""
import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException

from operational_balance_service import account_statement
from operational_balance_store import STATES, digest, mutate, read
from operational_financial_activity import cash_positions, operational_rows
from operational_workspace_refunds import recorded_bank_refund_match, recorded_provider_refund_match
from test_operational_balance_workspace import run, grant, resource
from test_operational_customer_adjustments import adjusted_order
from test_operational_workspace_refunds import BASE, EXECUTED_AT, bank_amount, add_fact, command


async def execution(client, **changes):
    number = changes.get("order_number", "1001")
    lookup = await client.get(BASE + "/order/" + number)
    assert lookup.status_code == 200, lookup.text
    return {"request_id": "execution-" + uuid4().hex, "expected_session_scope": digest(["owner", "owner"]),
            "expected_basis_hash": lookup.json()["basis_hash"], "order_number": number, "amount": "0.80",
            "method": "bank", "bank_id": "bank", "reason": "رد فرق تعديل الطلب للعميل",
            "reference": "refund-" + uuid4().hex, "executed_at": EXECUTED_AT, "confirmed": True, **changes}


def test_bank_record_retry_concurrency_and_new_intent_same_reference_debit_once(monkeypatch):
    async def scenario(db, client):
        await adjusted_order(db)
        payload = await execution(client)
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
        replies = await asyncio.gather(*[client.post(BASE + "/record-execution", json=payload) for _ in range(3)])
        assert all(reply.status_code == 200 for reply in replies), [r.text for r in replies]
        case_id = replies[0].json()["item"]["id"]
        assert all(reply.json()["item"]["id"] == case_id for reply in replies)
        state = await read(db, "owner")
        assert len(state["customer_returns"]) == 1 and state["movements"] == []
        assert bank_amount(state) == "999.20"
        assert state["customer_returns"][0]["execution_mode"] == "accountant_recorded"
        repeat = await client.post(BASE + "/record-execution", json={**payload, "request_id": "another-" + uuid4().hex})
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["item"]["id"] == case_id
        assert bank_amount(await read(db, "owner")) == "999.20"
        inbox = (await client.get(BASE.replace("/refunds", "/customer-adjustments"))).json()
        assert inbox["summary"]["refund_pending_amount"] == "0.00"
        assert inbox["summary"]["collection_pending_amount"] == "0.00"
        assert (await client.get(BASE, params={"status": "pending"})).json()["total"] == 0
        assert (await client.get(BASE, params={"status": "refunded"})).json()["total"] == 1
    run(scenario)


def test_refund_confirmation_alone_never_creates_customer_debt():
    async def scenario(db, client):
        await adjusted_order(db, total="200.00", hint="100.00")
        payload = await execution(client, amount="100.00")
        response = await client.post(BASE + "/record-execution", json=payload)
        assert response.status_code == 200, response.text
        result = (await client.get(BASE.replace("/refunds", "/customer-adjustments"))).json()
        assert result["summary"]["refund_pending_amount"] == "0.00"
        assert result["summary"]["collection_pending_amount"] == "0.00"
        assert bank_amount(await read(db, "owner")) == "900.00"
    run(scenario)


def test_provider_manual_attestation_requires_reference_and_does_not_touch_bank():
    async def scenario(db, client):
        await adjusted_order(db)
        payload = await execution(client, method="original", bank_id=None)
        assert (await client.post(BASE + "/record-execution", json={**payload, "reference": ""})).status_code == 422
        assert not (await read(db, "owner")).get("customer_returns")
        response = await client.post(BASE + "/record-execution", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["item"]["source"]["id"] == "tamara"
        state = await read(db, "owner")
        assert state["customer_returns"][0]["payment_snapshot"]["gross"] == "200.00"
        assert not state.get("engine", {}).get("facts")
        assert bank_amount(state) == "1000.00"
        assert (await client.post(BASE + "/record-execution", json=payload)).json() == response.json()
    run(scenario)


def test_pending_provider_request_consumes_its_own_reservation_preserving_creator():
    async def scenario(db, client):
        await adjusted_order(db, total="0.00", hint="200.00")
        await grant(db, operations=["customer.refund_request"], resources=[resource("provider", "tamara")])
        requested = await client.post(BASE, headers={"x-test-user": "staff"}, json=command(
            expected_session_scope=digest(["owner", "staff"]), amount="200.00", method="original", bank_id=None))
        assert requested.status_code == 200, requested.text
        pending = requested.json()["item"]
        assert (await client.get(BASE + "/order/1001")).json()["available_amount"] == "0.00"
        payload = await execution(client, pending_case_id=pending["id"], method="original", bank_id=None, amount="200.00")
        denied = await client.post(BASE + "/record-execution", json={**payload, "amount": "199.00"})
        assert denied.status_code == 409
        payload["request_id"] = "confirm-pending-" + uuid4().hex
        responses = await asyncio.gather(*[client.post(BASE + "/record-execution", json=payload) for _ in range(2)])
        assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
        saved = responses[0].json()["item"]
        assert saved["id"] == pending["id"] and saved["recorded_by"] == pending["recorded_by"]
        assert saved["created_at"] == pending["created_at"] and saved["confirmed_by"]["id"] == "owner"
        state = await read(db, "owner")
        assert len(state["customer_returns"]) == 1 and state["customer_returns"][0]["created_by"] == "staff"
        assert (await client.get(BASE + "/order/1001")).json()["reserved_amount"] == "0.00"
        assert bank_amount(state) == "1000.00"
    run(scenario)


def test_existing_provider_fact_is_deduplicated_and_documentary_refund_is_linked():
    async def scenario(db, client):
        await adjusted_order(db, total="100.00", hint="100.00")
        await add_fact(db, "provider-refund", amount="100.00")
        payload = await execution(client, method="original", bank_id=None, amount="100.00", reference="provider-refund")
        before = await read(db, "owner")
        response = await client.post(BASE + "/record-execution", json=payload)
        assert response.status_code == 200, response.text
        after = await read(db, "owner")
        assert after["engine"]["facts"] == before["engine"]["facts"]
        assert bank_amount(after) == "1000.00"
        lookup = (await client.get(BASE + "/order/1001")).json()
        assert lookup["confirmed_refunded_amount"] == "100.00"
        assert lookup["available_amount"] == "100.00"
    run(scenario)


def test_provider_fact_before_claim_does_not_remove_the_manual_refund_bridge():
    async def scenario(db, client):
        await adjusted_order(db)
        async def original(state):
            state["engine"] = {"facts": {
                "capture:original": {"order_id": "order-1001", "provider": "tamara", "amount": "200.00"},
                "refund:tamara:late-refund": {"order_id": "order-1001", "amount": "0.80"}},
                "obligations": {"provider:order-1001": {"id": "provider:order-1001", "kind": "provider",
                    "party_type": "provider", "party_id": "tamara", "name": "تمارا", "currency": "SAR",
                    "direction": "receivable", "confirmed": "200.00", "expected": "0.00"}}}
        await mutate(db, "owner", original)
        response = await client.post(BASE + "/record-execution", json=await execution(client,
            method="original", bank_id=None, reference="late-refund"))
        assert response.status_code == 200, response.text
        state = await read(db, "owner")
        case = state["customer_returns"][0]
        assert case["status"] == "refunded"  # an unprojected fact is not a financial match
        assert recorded_provider_refund_match(state, case) is None
        state["engine"]["obligations"]["provider:order-1001"]["confirmed"] = "199.20"
        assert recorded_provider_refund_match(state, case) == "refund:tamara:late-refund"
    run(scenario)


@pytest.mark.parametrize("cash_leg,expected_status,before_amount,after_amount", [
    ("outgoing", "bank_matched", "999.20", "999.20"),
    ("noncash", "refunded", "1000.00", "999.20"),
    ("incoming_counterleg", "refunded", "1000.80", "1000.00"),
])
def test_only_an_existing_exact_outgoing_cash_leg_replaces_the_manual_debit(cash_leg, expected_status, before_amount, after_amount):
    async def scenario(db, client):
        await adjusted_order(db)
        async def add(state):
            movement = {"id": "existing-bank-refund", "order_id": "order-1001", "order_number": "1001",
                "party_type": "bank", "party_id": "bank", "party_name": "الإنماء", "currency": "SAR", "amount": "0.80",
                "kind": "refund", "direction": "outgoing", "bank_id": None, "reference": "bank-existing",
                "allocations": [], "actual_fee_amount": "0.00", "actor_id": "owner", "occurred_at": EXECUTED_AT}
            if cash_leg == "outgoing":
                movement.update(bank_id="bank", bank_kind="bank", bank_name="الإنماء", bank_currency="SAR", bank_amount="0.80")
            elif cash_leg == "incoming_counterleg":
                movement.update(kind="transfer", bank_id="cash", bank_kind="cash", bank_name="الصندوق", bank_currency="SAR", bank_amount="0.80")
            state["movements"].append(movement)
        await mutate(db, "owner", add)
        before = bank_amount(await read(db, "owner"))
        assert before == before_amount
        payload = await execution(client, reference="bank-existing")
        response = await client.post(BASE + "/record-execution", json=payload)
        assert response.status_code == 200, response.text
        state = await read(db, "owner")
        assert state["customer_returns"][0]["status"] == expected_status
        assert bank_amount(state) == after_amount
        assert (await client.get(BASE + "/order/1001")).json()["confirmed_refunded_amount"] == "0.80"
    run(scenario)


def test_new_execution_before_opening_period_is_rejected_without_debit():
    async def scenario(db, client):
        raw = await adjusted_order(db)
        raw.update(created_at="2026-10-01T00:00:00+00:00", updated_at="2026-10-02T00:00:00+00:00")
        raw["payment"]["paid_at"] = "2026-10-01T01:00:00+00:00"
        await db.unified_orders.update_one({"user_id": "owner", "order_number": "1001"},
                                          {"$set": {"raw_by_source.salla_direct": raw}})
        payload = await execution(client, executed_at="2026-10-03T00:00:00+00:00")
        response = await client.post(BASE + "/record-execution", json=payload)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "operational_refund_before_start"
        assert not (await read(db, "owner")).get("customer_returns")
        assert bank_amount(await read(db, "owner")) == "1000.00"
    run(scenario)


@pytest.mark.parametrize("counterleg", [False, True])
def test_late_exact_bank_refund_is_counted_once_in_balance_ledger_and_daily_activity(counterleg):
    async def scenario(db, client):
        await adjusted_order(db)
        payload = await execution(client)
        saved = await client.post(BASE + "/record-execution", json=payload)
        assert saved.status_code == 200, saved.text
        assert bank_amount(await read(db, "owner")) == "999.20"
        async def import_cash_evidence(state):
            movement = {"id": "late-bank-refund", "request_id": "late-source-refund", "order_id": "order-1001",
                "order_number": "1001", "reference": payload["reference"], "amount": "0.80", "currency": "SAR",
                "kind": "refund", "direction": "outgoing", "bank_kind": "bank", "bank_id": "bank",
                "bank_name": "الإنماء", "party_type": "external_person", "party_id": "external", "name": "العميل",
                "actor_id": "owner", "occurred_at": EXECUTED_AT, "allocations": [], "note": "إثبات الرد المنفذ"}
            if counterleg:
                movement.update(kind="transfer", direction="incoming", bank_kind="cash", bank_id="cash",
                                bank_name="الصندوق", party_type="bank", party_id="bank", name="الإنماء")
            state["movements"].append(movement)
        await mutate(db, "owner", import_cash_evidence)
        state = await read(db, "owner")
        case = state["customer_returns"][0]
        assert recorded_bank_refund_match(state, case) == "bank-movement:late-bank-refund"
        assert bank_amount(state) == "999.20"
        assert format(cash_positions(state, {("bank", "bank", "SAR")})[("bank", "bank", "SAR")]["actual"], ".2f") == "999.20"
        statement = account_statement(state, {"kind": "bank", "id": "bank", "currency": "SAR", "name": "الإنماء"})
        assert len(statement["items"]) == 1 and statement["items"][0]["amount"] == "0.80"
        ledger = await client.get(BASE.replace("/refunds", "/ledger/bank/bank"), params={"q": payload["reference"]})
        assert ledger.status_code == 200, ledger.text
        assert ledger.json()["total"] == 1 and ledger.json()["items"][0]["amount"] == "0.80"
        activity, _ = operational_rows(state)
        outgoing = [row for row in activity if row["account_id"] == "bank" and row["direction"] == "outgoing"]
        assert len(outgoing) == 1 and outgoing[0]["amount"] == "0.80"
        assert (await client.get(BASE + "/order/1001")).json()["confirmed_refunded_amount"] == "0.80"
        # Historical confirmations keep their existing contract.
        old_case = {key: value for key, value in case.items() if key != "execution_mode"}
        assert recorded_bank_refund_match(state, old_case) is None
    run(scenario)


@pytest.mark.parametrize("change", [
    {"direction": "incoming"}, {"bank_id": "other-bank"}, {"bank_kind": None, "bank_id": None},
])
def test_unrelated_or_positive_late_cash_evidence_does_not_suppress_manual_refund(change):
    case = {"status": "refunded", "execution_mode": "accountant_recorded", "financial_effect": "confirmed_bank_refund_debit",
        "refund_source_type": "bank", "refund_source_id": "bank", "order_id": "order", "currency": "SAR",
        "amount": "100.00", "refund_reference": "ref"}
    movement = {"id": "cash", "bank_kind": "bank", "bank_id": "bank", "currency": "SAR", "amount": "100.00",
        "direction": "outgoing", "kind": "refund", "order_id": "order", "reference": "ref", **change}
    assert recorded_bank_refund_match({"movements": [movement]}, case) is None
    with pytest.raises(HTTPException):
        recorded_bank_refund_match({"movements": [{**movement, "bank_kind": "bank", "bank_id": "bank",
            "direction": "outgoing", "order_id": "another-order"}]}, case)


def test_bank_statement_shows_customer_collection_once_before_and_after_canonical_match():
    from test_operational_workspace_customer_collections import BASE as COLLECTION_BASE, command as collection_command
    async def scenario(db, client):
        await adjusted_order(db, total="300.00", hint="0.00")
        payload = await collection_command(client)
        recorded = await client.post(COLLECTION_BASE, json=payload)
        assert recorded.status_code == 200, recorded.text
        account = {"kind": "bank", "id": "bank", "currency": "SAR", "name": "الإنماء"}
        before = account_statement(await read(db, "owner"), account)
        assert before["account"]["actual"] == "1040.00"
        assert len(before["items"]) == 1 and before["items"][0]["amount"] == "40.00"
        assert before["items"][0]["direction"] == "incoming"
        assert before["items"][0]["occurred_at"] == payload["executed_at"]
        assert before["items"][0]["reference"] == payload["reference"]
        async def match(state):
            state["movements"].append({"id": "late-bank-receipt", "request_id": "late-source-receipt",
                "order_id": "order-1001", "order_number": "1001", "reference": payload["reference"],
                "amount": "40.00", "currency": "SAR", "kind": "collection", "direction": "incoming",
                "bank_kind": "bank", "bank_id": "bank", "bank_name": "الإنماء", "party_type": "bank",
                "party_id": "bank", "name": "الإنماء", "actor_id": "owner", "occurred_at": payload["executed_at"],
                "allocations": [], "note": "دليل التحصيل"})
        await mutate(db, "owner", match)
        after = account_statement(await read(db, "owner"), account)
        assert after["account"]["actual"] == "1040.00"
        assert len(after["items"]) == 1 and after["items"][0]["amount"] == "40.00"
        assert after["items"][0]["id"] == "late-bank-receipt"
        assert after["items"][0]["direction"] == "incoming"
        assert after["items"][0]["reference"] == before["items"][0]["reference"]
    run(scenario)


def test_basis_changes_are_rejected_without_debit_and_receipt_is_order_actor_bound():
    async def scenario(db, client):
        await adjusted_order(db)
        await adjusted_order(db, number="1002")
        payload = await execution(client)
        upload = await client.post(BASE.replace("/refunds", "/refund-receipts"),
            data={"order_number": "1001", "expected_session_scope": digest(["owner", "owner"])},
            files={"file": ("receipt.png", b"\x89PNG\r\n\x1a\nproof", "image/png")})
        assert upload.status_code == 200, upload.text
        receipt_id = upload.json()["id"]
        assert (await client.get(BASE.replace("/refunds", "/refund-receipts/") + receipt_id)).status_code == 200
        other = await execution(client, order_number="1002", receipt_id=receipt_id)
        denied = await client.post(BASE + "/record-execution", json=other)
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == "operational_refund_receipt_invalid"
        await grant(db, operations=["customer.refund_execute"], resources=[resource("bank", "bank")])
        headers = {"x-test-user": "staff"}
        assert (await client.get(BASE.replace("/refunds", "/refund-receipts/") + receipt_id, headers=headers)).status_code == 404
        await db.unified_orders.update_one({"user_id": "owner", "order_number": "1001"},
            {"$set": {"raw_by_source.salla_direct.total": "199.00"}})
        denied = await client.post(BASE + "/record-execution", json={**payload, "receipt_id": receipt_id})
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == "operational_refund_order_changed"
        assert bank_amount(await read(db, "owner")) == "1000.00"
        fresh = await execution(client, receipt_id=receipt_id)
        saved = await client.post(BASE + "/record-execution", json=fresh)
        assert saved.status_code == 200, saved.text
        assert saved.json()["item"]["receipt_id"] == receipt_id
    run(scenario)
