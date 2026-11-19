"""Financial refund workflow over isolated HTTP + canonical aggregate CAS."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest

from operational_balance_service import report
from operational_balance_store import digest, mutate, read
from operational_workspace_refunds import validate_provider_match
from test_operational_balance_workspace import run, grant, resource


BASE = "/api/operational-balances/workspace/refunds"
EXECUTED_AT = "2026-11-10T12:00:00+00:00"


async def order_fixture(db, *, method="tamara", paid="300.00", total="300.00", number="1001"):
    raw = {"id": "order-" + number, "reference_id": number, "currency": "SAR", "status": "delivered",
           "created_at": "2026-11-05T09:00:00+00:00", "updated_at": "2026-11-05T10:00:00+00:00",
           "total": total, "items": [{"id": "product-a", "name": "Private product", "quantity": 3}],
           "customer": {"name": "Private customer", "mobile": "private-phone"},
           "payment": {"method": method, "status": "paid", "reference": "capture-" + number,
                       "paid_amount": paid, "paid_at": "2026-11-05T09:01:00+00:00"}}
    if method == "bank":
        raw["payment"]["remaining_amount"] = str(float(total) - float(paid))
    await db.unified_orders.insert_one({"user_id": "owner", "order_number": number,
                                       "order_id": raw["id"], "raw_by_source": {"salla_direct": raw}})
    return raw


def command(**updates):
    return {"request_id": "refund-" + uuid4().hex, "expected_session_scope": digest(["owner", "owner"]),
            "order_number": "1001", "amount": "100.00", "method": "bank", "bank_id": "bank",
            "reason": "استرجاع مبلغ بالاتفاق مع العميل", **updates}


def confirm(**updates):
    return {"request_id": "confirm-" + uuid4().hex, "expected_session_scope": digest(["owner", "owner"]),
            "executed_at": EXECUTED_AT, "confirmed": True, **updates}


async def created(client, **updates):
    response = await client.post(BASE, json=command(**updates))
    assert response.status_code == 200, response.text
    return response.json()["item"]


def bank_amount(state):
    return next(row["actual"] for row in report(state)["parties"] if row["party_type"] == "bank" and row["party_id"] == "bank")


async def add_fact(db, reference="provider-refund-1", *, amount="100.00", provider="tamara", order_id="order-1001"):
    key = "refund:" + provider + ":" + reference
    fact = {"order_id": order_id, "amount": amount}
    async def apply(state):
        state.setdefault("engine", {}).setdefault("facts", {})[key] = deepcopy(fact)
    await mutate(db, "owner", apply)
    return {"evidence_id": key, "expected_evidence_hash": digest(fact)}


def test_bank_request_reserves_and_confirmation_without_reference_posts_once():
    async def scenario(db, client):
        await order_fixture(db)
        before_order = await db.unified_orders.find_one({"user_id": "owner"})
        request = command()
        replies = await asyncio.gather(*[client.post(BASE, json=request) for _ in range(3)])
        assert all(reply.status_code == 200 for reply in replies)
        case = replies[0].json()["item"]
        assert all(reply.json()["item"]["id"] == case["id"] for reply in replies)
        assert case["status"] == "pending" and case["can_confirm"] is True
        state = await read(db, "owner")
        assert len(state["customer_returns"]) == 1 and state["movements"] == []
        assert bank_amount(state) == "1000.00"
        lookup = await client.get(BASE + "/order/1001")
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["available_amount"] == "200.00"
        assert lookup.json()["reserved_amount"] == "100.00"
        assert "Private customer" not in lookup.text and "private-phone" not in lookup.text and "Private product" not in lookup.text
        assert [item["id"] for item in lookup.json()["methods"]] == ["bank", "original"]
        event = confirm()
        confirmed = await asyncio.gather(*[client.post(BASE + "/" + case["id"] + "/confirm", json=event) for _ in range(3)])
        assert all(reply.status_code == 200 for reply in confirmed), [reply.text for reply in confirmed]
        state = await read(db, "owner")
        row = state["customer_returns"][0]
        assert row["status"] == "refunded" and row["refund_reference"] == ""
        assert row["internal_confirmation_id"] and row["confirmed_by_snapshot"] == {"id": "owner", "name": "محمد عبدالله"}
        assert bank_amount(state) == "900.00" and state["movements"] == []
        assert not state.get("engine", {}).get("facts")
        assert await db.unified_orders.find_one({"user_id": "owner"}) == before_order
        assert (await client.get(BASE + "/order/1001")).json()["reserved_amount"] == "0.00"
        # Replaying request creation after execution returns its present status.
        assert (await client.post(BASE, json=request)).json()["item"]["status"] == "refunded"
        changed = await client.post(BASE + "/" + case["id"] + "/confirm", json=confirm(reference="changed"))
        assert changed.status_code == 409 and changed.json()["detail"]["not_applied"] is True
        assert bank_amount(await read(db, "owner")) == "900.00"
    run(scenario)


def test_original_is_pending_and_no_local_confirmation_or_bank_effect():
    async def scenario(db, client):
        await order_fixture(db)
        case = await created(client, method="original", bank_id=None)
        assert case["source"]["id"] == "tamara" and case["status"] == "pending"
        assert case["can_confirm"] is False and case["can_reconcile"] is False
        response = await client.post(BASE + "/" + case["id"] + "/confirm", json=confirm(reference="invented-reference"))
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "operational_refund_provider_execution_pending"
        assert bank_amount(await read(db, "owner")) == "1000.00"
        assert (await client.get(BASE + "/order/1001")).json()["reserved_amount"] == "100.00"
    run(scenario)


def test_provider_fact_match_closes_request_without_posting_or_double_counting():
    async def scenario(db, client):
        await order_fixture(db)
        case = await created(client, method="original", bank_id=None)
        evidence = await add_fact(db)
        before = await read(db, "owner")
        detail = await client.get(BASE + "/" + case["id"])
        assert detail.json()["can_reconcile"] is True
        assert detail.json()["provider_evidence"][0]["id"] == evidence["evidence_id"]
        # Before explicit matching, ambiguity is conservatively reserved.
        lookup = (await client.get(BASE + "/order/1001")).json()
        assert lookup["refunded_amount"] == lookup["reserved_amount"] == "100.00"
        request = {"request_id": "match-" + uuid4().hex, "expected_session_scope": digest(["owner", "owner"]), **evidence}
        response = await client.post(BASE + "/" + case["id"] + "/reconcile", json=request)
        assert response.status_code == 200, response.text
        assert response.json()["item"]["status"] == "refunded"
        assert response.json()["item"]["executed_at"] is None
        assert response.json()["item"]["reference"] == "provider-refund-1"
        assert (await client.post(BASE + "/" + case["id"] + "/reconcile", json=request)).json() == response.json()
        after = await read(db, "owner")
        linked = after["customer_returns"][0]
        assert linked["status"] == "provider_matched"
        assert linked["matched_by_snapshot"] == {"id": "owner", "name": "محمد عبدالله"}
        validate_provider_match(after, linked)
        assert after["engine"]["facts"] == before["engine"]["facts"]
        assert after["engine"].get("obligations") == before["engine"].get("obligations")
        assert after["movements"] == before["movements"] == []
        assert bank_amount(after) == bank_amount(before) == "1000.00"
        lookup = (await client.get(BASE + "/order/1001")).json()
        assert lookup["refunded_amount"] == "100.00" and lookup["reserved_amount"] == "0.00"
        assert lookup["available_amount"] == "200.00"
    run(scenario)


def test_proof_cannot_be_reused_by_two_pending_requests_or_predate_request():
    async def scenario(db, client):
        await order_fixture(db)
        old = await add_fact(db, "old-refund", amount="50.00")
        historical = await created(client, amount="50.00", method="original", bank_id=None)
        assert (await client.get(BASE + "/" + historical["id"])).json()["provider_evidence"] == []
        denied = await client.post(BASE + "/" + historical["id"] + "/reconcile", json={
            "request_id": "old-match-" + uuid4().hex, "expected_session_scope": digest(["owner", "owner"]), **old})
        assert denied.status_code == 409 and denied.json()["detail"]["not_applied"] is True
        first = await created(client, amount="50.00", method="original", bank_id=None)
        second = await created(client, amount="50.00", method="original", bank_id=None)
        evidence = await add_fact(db, "fresh-refund", amount="50.00")
        replies = await asyncio.gather(*[client.post(BASE + "/" + case["id"] + "/reconcile", json={
            "request_id": "match-" + uuid4().hex, "expected_session_scope": digest(["owner", "owner"]), **evidence})
            for case in (first, second)])
        assert sorted(reply.status_code for reply in replies) == [200, 409]
        assert sum(case["status"] == "provider_matched" for case in (await read(db, "owner"))["customer_returns"]) == 1
    run(scenario)


@pytest.mark.parametrize("changes", [
    {"amount": "90.00"}, {"provider": "tabby"}, {"order_id": "another-order"},
])
def test_wrong_provider_order_or_amount_never_matches(changes):
    async def scenario(db, client):
        await order_fixture(db)
        case = await created(client, method="original", bank_id=None)
        evidence = await add_fact(db, **changes)
        assert (await client.get(BASE + "/" + case["id"])).json()["provider_evidence"] == []
        reply = await client.post(BASE + "/" + case["id"] + "/reconcile", json={
            "request_id": "match-" + uuid4().hex, "expected_session_scope": digest(["owner", "owner"]), **evidence})
        assert reply.status_code == 409
        assert (await read(db, "owner"))["customer_returns"][0]["status"] == "pending"
    run(scenario)


def test_fact_change_after_selection_is_terminal_and_missing_matched_fact_fails_closed():
    async def scenario(db, client):
        await order_fixture(db)
        case = await created(client, method="original", bank_id=None)
        evidence = await add_fact(db)
        request = {"request_id": "match-" + uuid4().hex, "expected_session_scope": digest(["owner", "owner"]), **evidence}
        await add_fact(db, amount="99.00")
        denied = await client.post(BASE + "/" + case["id"] + "/reconcile", json=request)
        assert denied.status_code == 409 and denied.json()["detail"]["not_applied"] is True
        await add_fact(db)
        assert (await client.post(BASE + "/" + case["id"] + "/reconcile", json=request)).status_code == 409
        request["request_id"] = "fresh-match-" + uuid4().hex
        assert (await client.post(BASE + "/" + case["id"] + "/reconcile", json=request)).status_code == 200
        async def remove(state):
            del state["engine"]["facts"][evidence["evidence_id"]]
        await mutate(db, "owner", remove)
        assert (await client.get(BASE + "/" + case["id"])).status_code == 409
        assert (await client.get(BASE + "/order/1001")).status_code == 409
    run(scenario)


def test_concurrent_reservations_cap_is_paid_not_order_total_and_denial_is_stable():
    async def scenario(db, client):
        await order_fixture(db, paid="200.00")
        left, right = command(amount="150.00"), command(amount="150.00")
        replies = await asyncio.gather(client.post(BASE, json=left), client.post(BASE, json=right))
        assert sorted(reply.status_code for reply in replies) == [200, 409]
        loser = next(index for index, reply in enumerate(replies) if reply.status_code == 409)
        assert replies[loser].json()["detail"]["not_applied"] is True
        saved = next(reply.json()["item"] for reply in replies if reply.status_code == 200)
        cancelled = await client.post(BASE + "/" + saved["id"] + "/cancel", json={
            "request_id": "cancel-" + uuid4().hex, "expected_session_scope": digest(["owner", "owner"]), "reason": "تعديل طلب العميل"})
        assert cancelled.status_code == 200
        # The old rejected intent must not turn into a new refund after space is freed.
        assert (await client.post(BASE, json=[left, right][loser])).status_code == 409
        assert (await client.post(BASE, json=command(amount="200.00"))).status_code == 200
        assert bank_amount(await read(db, "owner")) == "1000.00"
    run(scenario)


@pytest.mark.parametrize("patch", [
    {"method": ""}, {"method": "cash"}, {"method": "provider"},
    {"method": "original", "bank_id": "bank"}, {"method": "bank", "bank_id": None},
    {"provider_id": "tabby"}, {"customer_iban": "forbidden"}, {"recorded_by": {"name": "Fake"}},
])
def test_invalid_and_silent_fields_never_create_financial_request(patch):
    async def scenario(db, client):
        await order_fixture(db)
        response = await client.post(BASE, json=command(**patch))
        assert response.status_code == 422, response.text
        assert not (await read(db, "owner")).get("customer_returns")
        assert bank_amount(await read(db, "owner")) == "1000.00"
    run(scenario)


def test_bank_only_order_still_requires_explicit_method_and_request_reference_is_optional():
    async def scenario(db, client):
        await order_fixture(db, method="bank")
        lookup = await client.get(BASE + "/order/1001")
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["methods"] == [{"id": "bank", "label": "تحويل بنكي"}]
        no_method = command()
        del no_method["method"]
        assert (await client.post(BASE, json=no_method)).status_code == 422
        assert (await client.post(BASE, json=command(method="original", bank_id=None))).status_code == 422
        assert (await client.post(BASE, json=command())).status_code == 200
    run(scenario)


def test_optional_bank_reference_deduplicates_when_present_and_bank_funds_are_checked():
    async def scenario(db, client):
        await order_fixture(db)
        first, second = await created(client), await created(client)
        assert (await client.post(BASE + "/" + first["id"] + "/confirm", json=confirm(reference="bank-ref-1"))).status_code == 200
        duplicate = await client.post(BASE + "/" + second["id"] + "/confirm", json=confirm(reference="bank-ref-1"))
        assert duplicate.status_code == 409 and duplicate.json()["detail"]["not_applied"] is True
        async def reduce_bank(state):
            state["openings"]["bank:bank"]["amount"] = "100.00"
        await mutate(db, "owner", reduce_bank)
        request = confirm()
        rejected = await client.post(BASE + "/" + second["id"] + "/confirm", json=request)
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "operational_refund_source_insufficient"
        assert rejected.json()["detail"]["not_applied"] is True
        async def replenish(state):
            state["openings"]["bank:bank"]["amount"] = "1000.00"
        await mutate(db, "owner", replenish)
        assert (await client.post(BASE + "/" + second["id"] + "/confirm", json=request)).status_code == 409
        assert bank_amount(await read(db, "owner")) == "900.00"
    run(scenario)


def test_staff_operation_resource_and_fresh_revoke_checks_cover_all_refund_actions(monkeypatch):
    async def scenario(db, client):
        import operational_workspace_refunds as refunds
        await order_fixture(db)
        staff = {"x-test-user": "staff", "x-test-mobile": "yes"}
        request = command(expected_session_scope=digest(["owner", "staff"]))
        await grant(db, operations=["customer.refund_request"], resources=[])
        assert (await client.post(BASE, headers=staff, json=request)).status_code == 403
        await grant(db, operations=["customer.refund_request"], resources=[resource("bank", "bank")], version=2)
        response = await client.post(BASE, headers=staff, json=request)
        assert response.status_code == 200, response.text
        case = response.json()["item"]
        assert case["recorded_by"] == {"id": "staff", "name": "خالد أحمد"}
        assert case["can_confirm"] is False
        action = confirm(expected_session_scope=digest(["owner", "staff"]))
        assert (await client.post(BASE + "/" + case["id"] + "/confirm", headers=staff, json=action)).status_code == 403
        await grant(db, operations=["customer.refund_request", "customer.refund_execute"], resources=[resource("bank", "bank")], version=3)
        original = refunds.entity
        async def revoke_after_lookup(*args, **kwargs):
            account = await original(*args, **kwargs)
            await grant(db, operations=["customer.refund_request", "customer.refund_execute"], resources=[], version=4)
            return account
        monkeypatch.setattr(refunds, "entity", revoke_after_lookup)
        response = await client.post(BASE + "/" + case["id"] + "/confirm", headers=staff, json=action)
        assert response.status_code == 403, response.text
        assert (await read(db, "owner"))["customer_returns"][0]["status"] == "pending"
        assert bank_amount(await read(db, "owner")) == "1000.00"
        assert (await client.get(BASE + "/" + case["id"], headers=staff)).status_code == 404
        assert (await client.get(BASE + "/" + case["id"], headers={"x-test-user": "other"})).status_code == 404
    run(scenario)


def test_legacy_refund_cannot_consume_workspace_reservation_or_confirm_financial_case():
    async def scenario(db, client):
        await order_fixture(db)
        case = await created(client, amount="250.00")
        legacy = {"request_id": "legacy-" + uuid4().hex, "expected_session_scope": digest(["owner", "owner"]),
                  "order_number": "1001", "items": [{"id": "product-a", "quantity": 1}],
                  "status": "refunded", "amount": "100.00", "refund_source_type": "bank", "refund_source_id": "bank",
                  "refund_reference": "legacy-bank-ref", "refunded_at": EXECUTED_AT,
                  "shipping_kind": "none", "shipment_reference": "", "shipment_completed": False, "note": "طلب قديم"}
        denied = await client.post("/api/operational-balances/customer-returns", json=legacy)
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == "operational_refund_reserved_amount"
        bypass = await client.post("/api/operational-balances/customer-returns/" + case["id"] + "/confirm",
                                   json={**legacy, "request_id": "bypass-" + uuid4().hex, "items": []})
        assert bypass.status_code == 409 and bypass.json()["detail"]["code"] == "operational_refund_workspace_required"
        assert bank_amount(await read(db, "owner")) == "1000.00"
    run(scenario)


def test_saved_recorder_and_confirmer_names_do_not_follow_profile_changes():
    async def scenario(db, client):
        await order_fixture(db)
        case = await created(client)
        await db.users.update_one({"id": "owner"}, {"$set": {"name": "Changed owner"}})
        response = await client.post(BASE + "/" + case["id"] + "/confirm", json=confirm())
        assert response.status_code == 200, response.text
        assert response.json()["item"]["recorded_by"]["name"] == "محمد عبدالله"
        assert response.json()["item"]["confirmed_by"]["name"] == "Changed owner"
        await db.users.update_one({"id": "owner"}, {"$set": {"name": "Changed again"}})
        saved = (await client.get(BASE + "/" + case["id"])).json()
        assert saved["recorded_by"]["name"] == "محمد عبدالله" and saved["confirmed_by"]["name"] == "Changed owner"
    run(scenario)


def test_list_is_paginated_and_no_status_changes_on_reads():
    async def scenario(db, client):
        await order_fixture(db)
        for _ in range(6):
            await created(client, amount="10.00")
        before = await read(db, "owner")
        first, second = await client.get(BASE), await client.get(BASE + "?page=2&page_size=5")
        assert len(first.json()["items"]) == 5 and len(second.json()["items"]) == 1
        assert first.json()["total"] == 6 and first.json()["total_pages"] == 2
        assert await read(db, "owner") == before
    run(scenario)


@pytest.mark.parametrize("number", ["١٠٠١", "۱۰۰۱"])
def test_arabic_and_persian_order_digits_match_canonical_identity(number):
    async def scenario(db, client):
        await order_fixture(db)
        lookup = await client.get(BASE + "/order/" + number)
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["order_number"] == "1001"
        saved = await client.post(BASE, json=command(order_number=number))
        assert saved.status_code == 200 and saved.json()["item"]["order_number"] == "1001"
    run(scenario)


def test_reconciler_keeps_own_action_visibility_after_all_to_own_change():
    async def scenario(db, client):
        await order_fixture(db)
        case = await created(client, method="original", bank_id=None)
        proof = await add_fact(db)
        await grant(db, operations=["customer.refund_execute"], resources=[resource("provider", "tamara", movements="all")])
        staff = {"x-test-user": "staff"}
        action = {"request_id": "staff-match-" + uuid4().hex, "expected_session_scope": digest(["owner", "staff"]), **proof}
        response = await client.post(BASE + "/" + case["id"] + "/reconcile", json=action, headers=staff)
        assert response.status_code == 200, response.text
        await grant(db, operations=["customer.refund_execute"], resources=[resource("provider", "tamara", movements="own")], version=2)
        detail = await client.get(BASE + "/" + case["id"], headers=staff)
        assert detail.status_code == 200 and detail.json()["confirmed_by"]["id"] == "staff"
        assert [row["id"] for row in (await client.get(BASE, headers=staff)).json()["items"]] == [case["id"]]
    run(scenario)


def test_external_history_requires_canonical_match_and_never_imports_another_debit():
    async def scenario(db, client):
        await order_fixture(db)
        await db.payment_refunds.insert_many([
            {"user_id": "other", "order_reference_id": "1001", "provider": "tamara", "provider_refund_id": "foreign", "amount": 25, "currency": "SAR"},
            {"user_id": "owner", "order_reference_id": "another-order", "provider": "tamara", "provider_refund_id": "unrelated", "amount": 25, "currency": "SAR"},
        ])
        assert (await client.get(BASE + "/order/1001")).status_code == 200
        await db.payment_refunds.insert_one({"user_id": "owner", "order_reference_id": "1001",
            "provider": "tamara", "provider_refund_id": "external-source", "amount": 50, "currency": "SAR"})
        unknown = await client.get(BASE + "/order/1001")
        assert unknown.status_code == 409
        request = command()
        failed = await client.post(BASE, json=request)
        assert failed.status_code == 409 and failed.json()["detail"]["not_applied"] is True
        assert not (await read(db, "owner")).get("customer_returns")
        await add_fact(db, "external-source", amount="50.00")
        matched = await client.get(BASE + "/order/1001")
        assert matched.status_code == 200, matched.text
        assert matched.json()["refunded_amount"] == "50.00" and matched.json()["available_amount"] == "250.00"
        assert (await client.post(BASE, json=request)).status_code == 409
        assert (await client.post(BASE, json=command(amount="250.00"))).status_code == 200
        state = await read(db, "owner")
        assert bank_amount(state) == "1000.00" and state["movements"] == []
    run(scenario)


def test_posted_external_bank_refund_without_canonical_case_blocks_optional_reference_bypass():
    async def scenario(db, client):
        await order_fixture(db)
        await db.mz2_customer_refund_payments.insert_one({"user_id": "owner", "order_number": "1001",
            "status": "posted", "execution_channel": "bank", "bank_account_id": "bank", "bank_reference": "existing-bank-ref", "amount": "100.00"})
        response = await client.post(BASE, json=command())
        assert response.status_code == 409 and response.json()["detail"]["code"] == "operational_refund_external_history_review"
        assert bank_amount(await read(db, "owner")) == "1000.00"
    run(scenario)


def test_external_refund_arriving_after_preview_is_rechecked_before_cas(monkeypatch):
    async def scenario(db, client):
        import operational_workspace_refunds as refunds
        await order_fixture(db)
        original = refunds._external_history
        inserted = False
        async def new_evidence_after_read(*args, **kwargs):
            nonlocal inserted
            fingerprint = await original(*args, **kwargs)
            if not inserted:
                inserted = True
                await db.payment_refunds.insert_one({"user_id": "owner", "order_reference_id": "1001",
                    "provider": "tamara", "provider_refund_id": "late-source", "amount": 200, "currency": "SAR"})
            return fingerprint
        monkeypatch.setattr(refunds, "_external_history", new_evidence_after_read)
        response = await client.post(BASE, json=command())
        assert response.status_code == 409, response.text
        # This failure occurred in the final source guard, not as a committed
        # terminal decision. Keep the same command for a later safe retry.
        assert "not_applied" not in response.json()["detail"]
        state = await read(db, "owner")
        assert not state.get("customer_returns") and state["movements"] == []
        assert bank_amount(state) == "1000.00"
    run(scenario)


@pytest.mark.parametrize("patch", [{"amount": "20"}, {"bank_id": "cash"}, {"method": "original"}, {"confirmed": False}])
def test_confirmation_rejects_hidden_changes_and_requires_explicit_execution(patch):
    async def scenario(db, client):
        await order_fixture(db)
        case = await created(client)
        response = await client.post(BASE + "/" + case["id"] + "/confirm", json=confirm(**patch))
        assert response.status_code == 422
        assert (await read(db, "owner"))["customer_returns"][0]["status"] == "pending"
        assert bank_amount(await read(db, "owner")) == "1000.00"
    run(scenario)


def test_review_history_is_optional_scoped_to_order_and_paginated():
    async def scenario(db, client):
        await order_fixture(db, number="901")
        await order_fixture(db, number="902")
        for _ in range(10):
            await created(client, order_number="901", amount="1.00")
        other = await created(client, order_number="902", amount="1.00")
        old = await client.get(BASE + "/order/901")
        assert old.status_code == 200 and "history" not in old.json()
        lookup = await client.get(BASE + "/order/٩٠١", params={"include_history": True})
        assert lookup.status_code == 200, lookup.text
        first = lookup.json()["history"]
        assert first["total"] == 10 and first["page_size"] == 8 and len(first["items"]) == 8
        second = await client.get(BASE, params={"order_number": "901", "page": 2, "page_size": 8})
        assert second.status_code == 200, second.text
        two = second.json()
        assert len(two["items"]) == 2 and two["total_pages"] == 2
        ids = {item["id"] for item in first["items"] + two["items"]}
        assert len(ids) == 10 and other["id"] not in ids
    run(scenario)
