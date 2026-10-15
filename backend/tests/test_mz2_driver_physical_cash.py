"""Real Mongo physical-cash evidence and explicit handover matching.

Only the external Salla transport is stubbed. Native financial writers, owner
guards and restricted operational transactions remain real. Every fixture uses
the delivered loopback-only, UUID-isolated Mongo fixture with a Legacy monitor.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from accounting_shipping_native_routes import BASE, install_shipping_native_routes
from accounting_shipping_native_contract import EVENTS
from store_delivery_cash_evidence import LINKS
from test_mz2_driver_pos_manual_review import manual, snapshot
from test_mz2_shipping_native import OWNER, AT, source, bind, settlement
from test_mz2_bank_evidence_adapters import imported
from test_mz2_driver_payment_review import driver_delivery
import store_delivery_driver_app_routes as driver_routes


CASH = BASE + "/driver-cash/driver-f"
DRIVER_CASH = "/store-delivery/app/accounts/physical-cash"
FINANCIAL_COLLECTIONS = (
    "accounting_journal_groups_v2", "accounting_general_ledger_v2", "accounting_audit_log_v2",
    "accounting_ledger_sequences_v2", "mz2_atomic_owners", "settings",
)


@pytest_asyncio.fixture
async def cash(manual, monkeypatch):
    db = manual.db
    await db.store_drivers.update_one({"user_id": OWNER, "id": "driver-f"}, {"$set": {
        "account_user_id": "cash-driver-user", "name": "Cash Fixture Driver"}})
    await db.users.insert_one({"id": "cash-driver-user", "role": "store_driver", "created_by": OWNER,
                              "is_active": True, "name": "Cash Fixture Driver"})
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    salla = AsyncMock(return_value={"slug": "delivered", "verified_slug": "delivered"})
    monkeypatch.setattr(driver_routes, "_push_salla_delivery_status", salla)
    monkeypatch.setattr(driver_routes, "_now", lambda: AT)
    await driver_routes.ensure_store_delivery_driver_app_indexes(db)
    actor = {"id": OWNER, "role": "owner"}
    driver = {"id": "cash-driver-user", "role": "store_driver", "created_by": OWNER,
              "_session_client": "amasi_mobile"}

    async def accountant():
        return actor

    async def driver_user():
        return driver

    app, router = FastAPI(), APIRouter()
    install_shipping_native_routes(router, db, accountant)
    app.include_router(router)
    mobile = FastAPI()
    mobile.include_router(driver_routes.make_store_delivery_driver_app_router(db, driver_user))
    async with AsyncClient(transport=ASGITransport(app), base_url="http://isolated-accountant") as http:
        async with AsyncClient(transport=ASGITransport(mobile), base_url="http://isolated-driver") as driver_http:
            yield SimpleNamespace(db=db, native=manual, http=http, driver_http=driver_http,
                                  actor=actor, driver=driver, salla=salla)


async def financial_snapshot(db):
    all_rows = await snapshot(db)
    result = {key: all_rows.get(key, []) for key in FINANCIAL_COLLECTIONS}
    # The restricted capability increments only the shared serialization token.
    # Every actual control field, activation flag and ledger document stays bound.
    owners = [json.loads(row) for row in result["mz2_atomic_owners"]]
    for row in owners:
        row.pop("revision", None)
    result["mz2_atomic_owners"] = sorted(json.dumps(row, sort_keys=True) for row in owners)
    return result


async def confirmed_fixture(cash, number="1", *, expected="500.00", actual="500.00"):
    """Seed the existing delivered shape and use the actual pure evidence builder.

    Matcher tests isolate that boundary; separate tests below exercise delivery
    HTTP capture itself. This helper never calls a financial writer.
    """
    from store_delivery_cash_evidence import build_cash_evidence
    assignment_id = await driver_delivery(cash.db, "cash", number)
    await source(cash.db, number, value=expected)
    await cash.db.store_delivery_assignments.update_one({"id": assignment_id, "user_id": OWNER},
        {"$set": {"active": True}})
    await cash.db.store_delivery_collections.update_one({"assignment_id": assignment_id, "user_id": OWNER},
        {"$set": {"amount": expected, "cod_custody_amount": expected, "collected_at": AT}})
    assignment = await cash.db.store_delivery_assignments.find_one({"id": assignment_id, "user_id": OWNER})
    collection = await cash.db.store_delivery_collections.find_one({"assignment_id": assignment_id, "user_id": OWNER})
    driver = await cash.db.store_drivers.find_one({"id": "driver-f", "user_id": OWNER})
    before = await snapshot(cash.db)
    evidence = build_cash_evidence(owner=OWNER, driver=driver, actor=cash.driver, assignment=assignment,
                                   collection=collection, actual_amount=actual, confirmed_at=AT)
    assert await snapshot(cash.db) == before, "Evidence builder performed a write"
    await cash.db.store_delivery_collections.update_one({"_id": collection["_id"], "user_id": OWNER},
        {"$set": {"physical_cash_evidence": evidence}})
    return {**collection, "physical_cash_evidence": evidence}


async def operational_handover(cash, key="operational-remittance-1", amount="500.00", **changes):
    # Existing stored operational contract. Do not invoke its Legacy account
    # selector or infer that this record constitutes a native bank settlement.
    row = {"id": key, "user_id": OWNER, "driver_id": "driver-f", "settlement_type": "cod_remittance",
        "amount": amount, "cod_settled_amount": amount, "delivery_fee_settled_amount": "0.00",
        "earning_offset": "0.00", "reference": "explicit-handover-proof", "created_at": "2026-09-04T12:00:00+00:00",
        "created_by": OWNER, "status": "posted", "posting_scope": "operational_balance",
        "accounting_status": "operational_only", "financial_handoff_status": "pending_mz2_driver_balance_link",
        "financial_source": "store_delivery_operational", "ledger_txn_group_id": None,
        "accounting_operation_id": None, "account_id": "existing-operational-account-reference",
        "account_name_snapshot": "Recorded operational handover destination", **changes}
    await cash.db.store_delivery_driver_settlements.insert_one(row)
    return row


def reconciliation(row, *, source_id="operational-remittance-1", amount="500.00", request_id="physical-link-request-1",
                   source_type="operational_cod_remittance"):
    return {"request_id": request_id, "source_type": source_type, "source_id": source_id,
            "allocations": [{"collection_id": row["id"], "amount": amount}],
            "reason": "Accountant explicitly matched observed cash to existing handover"}


async def match(cash, payload, expected=200):
    response = await cash.http.post(CASH + "/reconciliations", json=payload)
    assert response.status_code == expected, response.text
    return response.json()


async def prepare_delivery(cash, number="1", *, expected="500.00", method="cash"):
    await source(cash.db, number, value=expected)
    await cash.db.unified_orders.update_one({"user_id": OWNER, "order_number": number},
        {"$set": {"remaining_amount": expected}})
    assignment = "physical-assignment-" + number
    await cash.db.store_delivery_assignments.insert_one({"id": assignment, "user_id": OWNER,
        "driver_id": "driver-f", "driver_name_snapshot": "Cash Fixture Driver",
        "order_id": "salla-" + number, "order_number": number, "active": True,
        "status": "out_for_delivery", "delivery_fee_snapshot": "20.00"})
    for collection, token in ((driver_routes.DELIVERY_PROOFS, "physical-proof-" + number),
                              (driver_routes.CUSTOMER_CONVERSATION_EVIDENCE, "physical-conversation-" + number)):
        await cash.db[collection].insert_one({"user_id": OWNER, "driver_id": "driver-f",
            "assignment_id": assignment, "token": token, "status": "uploaded"})
    payload = {"barcode": number, "target_status": "delivered", "payment_method": method,
        "delivery_proof_reference": "physical-proof-" + number,
        "conversation_evidence_reference": "physical-conversation-" + number}
    if method in {"card_terminal", "bank_transfer"}:
        await cash.db.store_delivery_receipts.insert_one({"user_id": OWNER, "driver_id": "driver-f",
            "assignment_id": assignment, "token": "physical-receipt-" + number, "status": "uploaded"})
        payload["receipt_reference"] = "physical-receipt-" + number
        if method == "bank_transfer":
            await cash.db.mz2_financial_accounts.update_one({"user_id": OWNER, "id": "bank-f"},
                {"$set": {"account_type": "bank", "currency": "SAR", "status": "active"}}, upsert=True)
            payload["bank_account_id"] = "bank-f"
    return assignment, payload


async def delivered_cash(cash, number="1", *, expected="500.00", actual="500.00", confirmed=True):
    assignment, payload = await prepare_delivery(cash, number, expected=expected)
    payload.update({"physical_cash_amount": actual, "physical_cash_confirmed": confirmed})
    response = await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
    assert response.status_code == 200, response.text
    row = await cash.db.store_delivery_collections.find_one({"user_id": OWNER, "assignment_id": assignment})
    return assignment, payload, row, response.json()


async def view(cash, *, driver=False):
    response = await (cash.driver_http.get(DRIVER_CASH) if driver else cash.http.get(CASH))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["schema"] == "mz2.driver.physical_cash.v1"
    assert result["scope"] == "captured_delivered_cash_only"
    return result


@pytest.mark.asyncio
async def test_both_cash_read_routes_are_registered_and_do_not_write(cash):
    before = await snapshot(cash.db)
    accountant = await view(cash)
    driver = await view(cash, driver=True)
    assert accountant["totals"] == driver["totals"]
    assert accountant["totals"]["confirmed_cash"] == "0.00"
    assert accountant["coverage"]["complete"] is True
    assert accountant["coverage"]["missing_confirmation_collection_ids"] == []
    assert await snapshot(cash.db) == before


@pytest.mark.asyncio
async def test_driver_delivered_capture_freezes_actual_identity_without_changing_expected_cod(cash):
    before = await financial_snapshot(cash.db)
    assignment, _, collection, response = await delivered_cash(cash)
    evidence = collection["physical_cash_evidence"]
    assert response["status"] == "delivered"
    assert Decimal(str(collection["amount"])) == Decimal(str(collection["cod_custody_amount"])) == Decimal("500.00")
    assert evidence["physical_cash_amount"] == evidence["cod_amount"] == "500.00"
    assert evidence["variance"] == "0.00" and evidence["financial_effect"] == "none"
    assert evidence["driver_id"] == "driver-f" and evidence["driver_name"] == "Cash Fixture Driver"
    assert evidence["confirmation_actor"] == "cash-driver-user" and evidence["confirmed_at"] == AT
    assert evidence["assignment_id"] == assignment and evidence["collection_id"] == collection["id"]
    assert evidence["order_id"] == "salla-1" and evidence["order_number"] == "1"
    assert evidence["delivery_status"] == "delivered" and evidence["delivery_proof_reference"] == "physical-proof-1"
    assert await financial_snapshot(cash.db) == before
    data = await view(cash, driver=True)
    assert data["totals"]["confirmed_cash"] == data["totals"]["confirmed_cash_remaining"] == "500.00"
    assert data["totals"]["matched_handover"] == "0.00"
    assert len(data["items"]) == 1 and data["items"][0]["id"] == collection["id"]
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("actual,variance", [("450.00", "-50.00"), ("550.00", "50.00"), ("0.00", "-500.00")])
async def test_actual_short_or_excess_cash_is_observation_not_a_financial_adjustment(cash, actual, variance):
    before = await financial_snapshot(cash.db)
    row = await confirmed_fixture(cash, actual=actual)
    data = await view(cash)
    assert data["totals"]["confirmed_cash"] == actual
    assert data["totals"]["expected_cod"] == "500.00"
    assert data["totals"]["variance"] == variance
    assert data["totals"]["matched_handover"] == "0.00"
    stored = await cash.db.store_delivery_collections.find_one({"id": row["id"]})
    assert stored["amount"] == stored["cod_custody_amount"] == "500.00"
    assert stored["physical_cash_evidence"]["physical_cash_amount"] == actual
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
async def test_multiple_orders_and_missing_historical_confirmation_are_not_inferred(cash):
    before = await financial_snapshot(cash.db)
    first = await confirmed_fixture(cash, "1", expected="200.00", actual="200.00")
    second = await confirmed_fixture(cash, "2", expected="300.00", actual="300.00")
    await driver_delivery(cash.db, "cash", "historical-no-proof")
    await driver_delivery(cash.db, "card_terminal", "pos")
    await driver_delivery(cash.db, "bank_transfer", "bank")
    data = await view(cash)
    assert {row["id"] for row in data["items"]} == {first["id"], second["id"]}
    assert data["totals"] == {"confirmed_cash": "500.00", "eligible_confirmed_cash": "500.00",
        "expected_cod": "500.00", "variance": "0.00", "matched_handover": "0.00", "confirmed_cash_remaining": "500.00"}
    assert data["coverage"]["complete"] is False
    assert data["coverage"]["missing_confirmation_collection_ids"] == ["collection-historical-no-proof"]
    assert data["coverage"]["opening_physical_cash"] is None
    assert data["coverage"]["historical_cash_inferred"] is False
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"status": "cancelled"}, {"status": "out_for_delivery"}, {"active": False}])
async def test_later_assignment_state_preserves_cash_evidence_without_recounting(cash, changes):
    row = await confirmed_fixture(cash)
    original = deepcopy(row["physical_cash_evidence"])
    await cash.db.store_delivery_assignments.update_one({"id": row["assignment_id"], "user_id": OWNER}, {"$set": changes})
    before = await snapshot(cash.db)
    data = await view(cash)
    assert len(data["items"]) == 1 and data["items"][0]["eligible_for_reconciliation"] is False
    assert data["totals"]["confirmed_cash"] == "500.00" and data["totals"]["eligible_confirmed_cash"] == "0.00"
    assert (await cash.db.store_delivery_collections.find_one({"id": row["id"]}))["physical_cash_evidence"] == original
    assert await snapshot(cash.db) == before
    await operational_handover(cash)
    await match(cash, reconciliation(row), expected=409)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"physical_cash_confirmed": False}, {"physical_cash_confirmed": "true"},
    {"physical_cash_amount": None}, {"physical_cash_amount": 500},
    {"physical_cash_amount": "-1.00"}, {"physical_cash_amount": "500.001"},
    {"physical_cash_amount": "NaN"}, {"physical_cash_amount": "1000000.01"},
])
async def test_delivery_requires_explicit_exact_cash_confirmation_before_external_status(cash, changes):
    _, payload = await prepare_delivery(cash)
    payload.update({"physical_cash_amount": "500.00", "physical_cash_confirmed": True, **changes})
    before = await snapshot(cash.db)
    response = await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
    assert response.status_code in {409, 422}, response.text
    cash.salla.assert_not_awaited()
    assert await snapshot(cash.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method,expected", [("card_terminal", "500.00"), ("bank_transfer", "500.00"), ("cash", "0.00")])
async def test_noncash_or_zero_delivery_never_infers_physical_cash(cash, method, expected):
    assignment, payload = await prepare_delivery(cash, expected=expected, method=method)
    before = await financial_snapshot(cash.db)
    response = await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
    assert response.status_code == 200, response.text
    row = await cash.db.store_delivery_collections.find_one({"user_id": OWNER, "assignment_id": assignment})
    assert not row.get("physical_cash_evidence")
    assert (await view(cash))["totals"]["confirmed_cash"] == "0.00"
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method,expected", [("card_terminal", "500.00"), ("bank_transfer", "500.00"), ("cash", "0.00")])
async def test_noncash_or_zero_delivery_rejects_submitted_physical_cash(cash, method, expected):
    _, payload = await prepare_delivery(cash, expected=expected, method=method)
    payload.update({"physical_cash_amount": "500.00", "physical_cash_confirmed": True})
    before = await snapshot(cash.db)
    response = await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
    assert response.status_code == 422, response.text
    cash.salla.assert_not_awaited()
    assert await snapshot(cash.db) == before


@pytest.mark.asyncio
async def test_duplicate_delivery_confirmation_is_idempotent_and_cannot_overwrite(cash):
    _, payload, row, _ = await delivered_cash(cash)
    before = await snapshot(cash.db)
    repeat = await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
    assert repeat.status_code == 200, repeat.text
    after = await cash.db.store_delivery_collections.find_one({"id": row["id"]})
    assert after == row
    assert await cash.db.store_delivery_collections.count_documents({"user_id": OWNER}) == 1
    conflicting = await cash.driver_http.post("/store-delivery/app/deliveries/status",
        json={**payload, "physical_cash_amount": "400.00"})
    assert conflicting.status_code == 409, conflicting.text
    assert await snapshot(cash.db) == before


@pytest.mark.asyncio
async def test_concurrent_delivery_confirmation_records_once(cash):
    _, payload = await prepare_delivery(cash)
    payload.update({"physical_cash_amount": "500.00", "physical_cash_confirmed": True})
    before = await financial_snapshot(cash.db)
    responses = await asyncio.gather(*(cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload) for _ in range(3)))
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    assert await cash.db.store_delivery_collections.count_documents({"user_id": OWNER}) == 1
    assert await cash.db[driver_routes.DRIVER_EARNINGS].count_documents({"user_id": OWNER}) == 1
    assert (await view(cash))["totals"]["confirmed_cash"] == "500.00"
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
async def test_explicit_operational_handover_allocation_is_metadata_only_and_never_nets_fees(cash):
    first = await confirmed_fixture(cash, "1", expected="200.00", actual="200.00")
    second = await confirmed_fixture(cash, "2", expected="300.00", actual="300.00")
    await operational_handover(cash, amount="500.00")
    await cash.db.store_delivery_driver_earnings.insert_one({"id": "untouched-earning", "user_id": OWNER,
        "driver_id": "driver-f", "assignment_id": "untouched-assignment", "amount": "20.00", "status": "due"})
    payload = reconciliation(first, amount="200.00")
    payload["allocations"].append({"collection_id": second["id"], "amount": "300.00"})
    before = await financial_snapshot(cash.db)
    recorded = await match(cash, payload)
    assert recorded["financial_effect"] == "none" and recorded["state"] == "recorded"
    result = await view(cash)
    assert result["totals"]["matched_handover"] == "500.00"
    assert result["totals"]["confirmed_cash_remaining"] == "0.00"
    assert result["reconciliations"][0]["source"]["financial_proof"] == "operational_only_no_native_settlement_proof"
    assert result["reconciliations"][0]["source"]["txn_group_id"] is None
    assert (await cash.db.store_delivery_driver_earnings.find_one({"id": "untouched-earning"}))["amount"] == "20.00"
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
async def test_partial_exact_explicit_links_and_idempotent_concurrent_retry(cash):
    row = await confirmed_fixture(cash)
    await operational_handover(cash, amount="200.00")
    payload = reconciliation(row, amount="200.00")
    before = await financial_snapshot(cash.db)
    responses = await asyncio.gather(*(cash.http.post(CASH + "/reconciliations", json=payload) for _ in range(3)))
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    assert sum(response.json()["state"] == "recorded" for response in responses) == 1
    assert len({response.json()["id"] for response in responses}) == 1
    assert (await view(cash))["totals"]["confirmed_cash_remaining"] == "300.00"
    await operational_handover(cash, key="operational-remittance-2", amount="300.00")
    await match(cash, reconciliation(row, source_id="operational-remittance-2", amount="300.00", request_id="physical-link-request-2"))
    result = await view(cash)
    assert len(result["reconciliations"]) == 2
    assert result["totals"]["matched_handover"] == "500.00" and result["totals"]["confirmed_cash_remaining"] == "0.00"
    await match(cash, {**payload, "reason": "Changed request must conflict"}, expected=409)
    await match(cash, {**payload, "request_id": "duplicate-handover-new-key"}, expected=409)
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"user_id": "foreign-owner"}, {"driver_id": "other-driver"}, {"status": "draft"},
    {"settlement_type": "net_settlement"}, {"earning_offset": "20.00"},
    {"delivery_fee_settled_amount": "20.00"}, {"ledger_txn_group_id": "unsupported"},
    {"cod_settled_amount": "499.00"}, {"account_id": ""},
])
async def test_foreign_unconfirmed_net_or_financially_ambiguous_handover_is_rejected(cash, changes):
    row = await confirmed_fixture(cash)
    await operational_handover(cash, **changes)
    before = await financial_snapshot(cash.db)
    await match(cash, reconciliation(row), expected=409)
    # A malformed same-owner handover also makes the reader fail closed;
    # inspecting link storage avoids asking it to accept contradictory proof.
    assert await cash.db[LINKS].count_documents({"user_id": OWNER}) == 0
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
async def test_reader_and_matching_use_fresh_owner_and_accountant_authority(cash):
    row = await confirmed_fixture(cash)
    await operational_handover(cash)
    await cash.db.users.insert_one({"id": "cash-view-only", "role": "employee", "created_by": OWNER,
        "is_active": True, "accounting_permissions": ["accounting.shipping.view"]})
    cash.actor.update({"id": "cash-view-only", "role": "owner", "is_owner": True})
    assert len((await view(cash))["items"]) == 1
    await match(cash, reconciliation(row), expected=403)
    await cash.db.users.update_one({"id": "cash-view-only"}, {"$set": {"accounting_permissions": []}})
    response = await cash.http.get(CASH)
    assert response.status_code == 403, response.text
    cash.driver["id"] = "another-driver-user"
    assert (await cash.driver_http.get(DRIVER_CASH)).status_code == 403


@pytest.mark.asyncio
async def test_explicit_persisted_settlement_denial_overrides_accountant_role(cash):
    row = await confirmed_fixture(cash)
    await operational_handover(cash)
    await cash.db.users.update_one({"id": OWNER}, {"$set": {"denied_permissions": ["store_delivery.settlements.manage"]}})
    before = await financial_snapshot(cash.db)
    await match(cash, reconciliation(row), expected=403)
    assert await financial_snapshot(cash.db) == before


async def native_handover(cash, row, *, value="500.00"):
    from accounting_shipping_native import recognize_cod, settle
    await cash.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": False}})
    await cash.db.mz2_financial_accounts.update_one({"user_id": OWNER, "id": "bank-f"},
        {"$set": {"account_type": "bank", "currency": "SAR", "status": "active"}}, upsert=True)
    await recognize_cod(cash.db, owner=OWNER, actor_id=OWNER, assignment_id=row["assignment_id"])
    await bind(cash.db, "store_driver", "driver-f")
    movement = await imported(cash.db, OWNER, "bank-f", value=value, direction="in", day="2026-09-04")
    result = await settle(cash.db, owner=OWNER, actor_id=OWNER,
        payload=settlement(movement["id"], kind="store_driver", identity="driver-f"))
    event = await cash.db[EVENTS].find_one({"user_id": OWNER, "kind": "settlement", "txn_group_id": result["txn_group_id"]})
    assert event is not None
    await cash.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    return event


@pytest.mark.asyncio
async def test_native_handover_requires_verified_journal_and_explicit_link_without_second_settlement(cash):
    row = await confirmed_fixture(cash)
    event = await native_handover(cash, row)
    before = await financial_snapshot(cash.db)
    unlinked = await view(cash)
    assert unlinked["totals"]["confirmed_cash_remaining"] == "500.00"
    assert unlinked["coverage"]["complete"] is False
    assert unlinked["coverage"]["unmatched_handover_source_ids"] == [
        {"source_type": "native_cash_settlement", "source_id": event["_id"]}]
    candidate = unlinked["handover_candidates"][0]
    assert candidate["financial_proof"] == "verified_native_cash_settlement"
    assert candidate["txn_group_id"] == event["txn_group_id"]
    payload = reconciliation(row, source_type="native_cash_settlement", source_id=event["_id"])
    first = await match(cash, payload)
    replay = await match(cash, payload)
    assert replay["state"] == "already_recorded" and replay["id"] == first["id"]
    result = await view(cash)
    assert result["coverage"]["complete"] is True and result["handover_candidates"] == []
    assert result["totals"]["matched_handover"] == "500.00"
    assert result["totals"]["confirmed_cash_remaining"] == "0.00"
    assert await cash.db[LINKS].count_documents({"user_id": OWNER}) == 1
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["event_owner", "event_party", "event_metadata", "journal_owner", "journal_amount", "audit_missing"])
async def test_native_handover_tamper_or_foreign_identity_fails_closed(cash, case):
    row = await confirmed_fixture(cash)
    event = await native_handover(cash, row)
    if case.startswith("event_"):
        change = {"event_owner": {"user_id": "foreign-owner"}, "event_party": {"party_id": "foreign-driver"},
                  "event_metadata": {"reason": "Changed after journal"}}[case]
        await cash.db[EVENTS].update_one({"_id": event["_id"]}, {"$set": change})
    elif case == "journal_owner":
        await cash.db.accounting_journal_groups_v2.update_one({"txn_group_id": event["txn_group_id"]},
            {"$set": {"user_id": "foreign-owner"}})
    elif case == "journal_amount":
        await cash.db.accounting_general_ledger_v2.update_one({"txn_group_id": event["txn_group_id"]},
            {"$set": {"amount": "499.00"}})
    else:
        await cash.db.accounting_audit_log_v2.delete_many({"txn_group_id": event["txn_group_id"]})
    before = await financial_snapshot(cash.db)
    response = await cash.http.post(CASH + "/reconciliations",
        json=reconciliation(row, source_type="native_cash_settlement", source_id=event["_id"]))
    assert response.status_code in {403, 409}, response.text
    assert await cash.db[LINKS].count_documents({"user_id": OWNER}) == 0
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
async def test_reversed_native_handover_is_visible_but_cannot_be_newly_matched(cash):
    from accounting_atomic import atomic_owner
    from accounting_ledger_v2 import reverse_journal_v2
    row = await confirmed_fixture(cash)
    event = await native_handover(cash, row)
    await cash.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": False}})
    async def reverse(scoped):
        return await reverse_journal_v2(scoped._db, user_id=OWNER, actor_id=OWNER, actor_name=OWNER,
            original_txn_group_id=event["txn_group_id"], effective_at=datetime.now(timezone.utc).isoformat(),
            reason="Synthetic real native reversal", mongo_session=scoped._session)
    await atomic_owner(cash.db, OWNER, reverse)
    await cash.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    before = await financial_snapshot(cash.db)
    assert (await view(cash))["handover_candidates"][0]["journal_reversed"] is True
    await match(cash, reconciliation(row, source_type="native_cash_settlement", source_id=event["_id"]), expected=409)
    assert await cash.db[LINKS].count_documents({"user_id": OWNER}) == 0
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
async def test_later_reversal_marks_linked_financial_proof_incomplete_without_inventing_cash_return(cash):
    from accounting_atomic import atomic_owner
    from accounting_ledger_v2 import reverse_journal_v2
    row = await confirmed_fixture(cash)
    event = await native_handover(cash, row)
    await match(cash, reconciliation(row, source_type="native_cash_settlement", source_id=event["_id"]))
    assert (await view(cash))["coverage"]["complete"] is True
    await cash.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": False}})
    async def reverse(scoped):
        return await reverse_journal_v2(scoped._db, user_id=OWNER, actor_id=OWNER, actor_name=OWNER,
            original_txn_group_id=event["txn_group_id"], effective_at=datetime.now(timezone.utc).isoformat(),
            reason="Synthetic reversal after physical handover was matched", mongo_session=scoped._session)
    await atomic_owner(cash.db, OWNER, reverse)
    await cash.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    before = await snapshot(cash.db)
    result = await view(cash)
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["changed_financial_proof_source_ids"] == [event["_id"]]
    assert result["reconciliations"][0]["source"]["journal_reversed"] is True
    assert result["totals"]["matched_handover"] == "500.00"
    assert result["totals"]["confirmed_cash_remaining"] == "0.00"
    assert await snapshot(cash.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["after_insert", "caught_financial_escalation"])
async def test_real_reconciliation_transaction_rolls_back_insert_and_rejects_financial_escalation(cash, monkeypatch, failure):
    from accounting_atomic import atomic_owner
    from motor.motor_asyncio import AsyncIOMotorCollection
    row = await confirmed_fixture(cash)
    await operational_handover(cash)
    # Provisioning is not part of the transaction under test.
    await cash.db[LINKS].create_index([("user_id", 1), ("driver_id", 1)], name="ix_driver_cash_reconciliation_owner")
    before = await snapshot(cash.db)
    original = AsyncIOMotorCollection.insert_one
    injections = []
    async def fail_after_insert(collection, *args, **kwargs):
        result = await original(collection, *args, **kwargs)
        if collection.name == LINKS and collection.database.name == cash.db.name:
            injections.append(kwargs.get("session"))
            assert kwargs.get("session") is not None, "Append escaped the Mongo transaction"
            if failure == "after_insert":
                raise RuntimeError("synthetic after reconciliation insert")
            async def forbidden_financial_callback(scoped):
                pytest.fail("Operational capability reached financial callback")
            try:
                await atomic_owner(cash.db, OWNER, forbidden_financial_callback)
            except HTTPException as exc:
                assert exc.status_code == 409
            else:
                pytest.fail("Financial capability escalation was accepted")
        return result
    monkeypatch.setattr(AsyncIOMotorCollection, "insert_one", fail_after_insert)
    if failure == "after_insert":
        with pytest.raises(RuntimeError, match="after reconciliation insert"):
            await cash.http.post(CASH + "/reconciliations", json=reconciliation(row))
    else:
        await match(cash, reconciliation(row), expected=409)
    assert len(injections) == 1
    assert await snapshot(cash.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing_collection", "foreign_collection", "unconfirmed", "over_actual",
    "sum_mismatch", "before_collection", "duplicate_allocation", "zero_allocation", "fractional_cent"])
async def test_reconciliation_requires_exact_owned_confirmed_amounts_and_later_handover(cash, case):
    row = await confirmed_fixture(cash)
    await operational_handover(cash)
    payload = reconciliation(row)
    if case == "missing_collection":
        payload["allocations"][0]["collection_id"] = "missing-exact-collection"
    elif case == "foreign_collection":
        await cash.db.store_delivery_collections.update_one({"id": row["id"]}, {"$set": {"user_id": "foreign-owner"}})
    elif case == "unconfirmed":
        await cash.db.store_delivery_collections.update_one({"id": row["id"]}, {"$unset": {"physical_cash_evidence": ""}})
    elif case == "before_collection":
        await cash.db.store_delivery_driver_settlements.update_one({"id": "operational-remittance-1"},
            {"$set": {"created_at": "2026-09-02T12:00:00+00:00"}})
    elif case == "duplicate_allocation":
        payload["allocations"] *= 2
    else:
        payload["allocations"][0]["amount"] = {"over_actual": "501.00", "sum_mismatch": "499.00",
            "zero_allocation": "0.00", "fractional_cent": "500.001"}[case]
    before = await financial_snapshot(cash.db)
    response = await cash.http.post(CASH + "/reconciliations", json=payload)
    assert response.status_code in {409, 422}, response.text
    assert await cash.db[LINKS].count_documents({"user_id": OWNER}) == 0
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["evidence_amount", "resealed_evidence", "collection_expected",
    "link_amount", "link_source", "source_destination", "source_amount", "missing_collection"])
async def test_reader_detects_mutated_confirmation_binding_or_handover_after_explicit_link(cash, case):
    from accounting_shipping_native_contract import digest
    row = await confirmed_fixture(cash)
    await operational_handover(cash)
    await match(cash, reconciliation(row))
    if case in {"evidence_amount", "resealed_evidence"}:
        evidence = deepcopy(row["physical_cash_evidence"])
        evidence["physical_cash_amount"] = "600.00"
        if case == "resealed_evidence":
            evidence["variance"] = "100.00"
            evidence["seal"] = digest({k: v for k, v in evidence.items() if k != "seal"})
        await cash.db.store_delivery_collections.update_one({"id": row["id"]}, {"$set": {"physical_cash_evidence": evidence}})
    elif case == "collection_expected":
        await cash.db.store_delivery_collections.update_one({"id": row["id"]}, {"$set": {"amount": "600.00"}})
    elif case == "missing_collection":
        await cash.db.store_delivery_collections.delete_one({"id": row["id"]})
    elif case in {"link_amount", "link_source"}:
        field = "allocations.0.amount" if case == "link_amount" else "source.source_id"
        await cash.db[LINKS].update_one({"user_id": OWNER}, {"$set": {field: "changed"}})
    elif case == "source_destination":
        await cash.db.store_delivery_driver_settlements.update_one({"id": "operational-remittance-1"},
            {"$set": {"account_id": "different-recorded-destination"}})
    else:
        await cash.db.store_delivery_driver_settlements.update_one({"id": "operational-remittance-1"},
            {"$set": {"amount": "600.00", "cod_settled_amount": "600.00"}})
    before = await snapshot(cash.db)
    response = await cash.http.get(CASH)
    assert response.status_code == 409, response.text
    assert await snapshot(cash.db) == before


@pytest.mark.asyncio
async def test_cash_observation_does_not_change_existing_cod_and_fee_economics(cash):
    from accounting_shipping_native import statement
    await cash.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": False}})
    assignment, payload = await prepare_delivery(cash)
    payload.update({"physical_cash_amount": "450.00", "physical_cash_confirmed": True})
    assert await cash.db[EVENTS].count_documents({"user_id": OWNER, "kind": {"$in": ["cod", "fee"]}}) == 0
    response = await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
    assert response.status_code == 200, response.text
    report = await statement(cash.db, owner=OWNER, actor_id=OWNER, kind="store_driver", identity="driver-f")
    assert report["cod_receivable"] == "500.00" and report["payable"] == "20.00"
    assert report["collections"] == "0.00" and report["payments"] == "0.00"
    observation = await view(cash)
    assert observation["totals"]["confirmed_cash"] == "450.00"
    assert observation["totals"]["variance"] == "-50.00"
    groups = await cash.db.accounting_journal_groups_v2.find({"user_id": OWNER,
        "txn_type": {"$nin": ["opening_balance"]}}).to_list(None)
    assert not any(group["txn_type"] == "shipping_receive_cod" for group in groups)
    before = await financial_snapshot(cash.db)
    repeated = await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
    assert repeated.status_code == 200, repeated.text
    assert await financial_snapshot(cash.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["after_collection", "after_event", "caught_financial_escalation"])
async def test_delivery_capture_rolls_back_all_local_state_on_insert_failure_or_financial_escalation(cash, monkeypatch, failure):
    from accounting_atomic import atomic_owner
    from motor.motor_asyncio import AsyncIOMotorCollection
    _, payload = await prepare_delivery(cash)
    payload.update({"physical_cash_amount": "500.00", "physical_cash_confirmed": True})
    before = await snapshot(cash.db)
    original = AsyncIOMotorCollection.insert_one
    injections = []
    target = driver_routes.DRIVER_COLLECTIONS if failure == "after_collection" else driver_routes.EVENTS
    async def fault(collection, *args, **kwargs):
        result = await original(collection, *args, **kwargs)
        if collection.name == target and collection.database.name == cash.db.name:
            injections.append(kwargs.get("session"))
            assert kwargs.get("session") is not None
            if failure != "caught_financial_escalation":
                raise RuntimeError("synthetic after cash capture insert")
            async def forbidden(scoped):
                pytest.fail("Cash capture entered a financial transaction")
            try:
                await atomic_owner(cash.db, OWNER, forbidden)
            except HTTPException as exc:
                assert exc.status_code == 409
            else:
                pytest.fail("Cash capture accepted financial escalation")
        return result
    monkeypatch.setattr(AsyncIOMotorCollection, "insert_one", fault)
    if failure == "caught_financial_escalation":
        response = await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
        assert response.status_code == 409, response.text
    else:
        with pytest.raises(RuntimeError, match="after cash capture insert"):
            await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
    assert len(injections) == 1
    assert await snapshot(cash.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["amount", "driver_revoked", "proof_revoked"])
async def test_delivery_capture_rechecks_current_identity_amount_and_proof_after_external_response(cash, case):
    assignment, payload = await prepare_delivery(cash)
    payload.update({"physical_cash_amount": "500.00", "physical_cash_confirmed": True})
    before_finance = await financial_snapshot(cash.db)
    mutation_done = []
    async def external(*args, **kwargs):
        if case == "amount":
            await cash.db.unified_orders.update_one({"user_id": OWNER, "order_number": "1"},
                {"$set": {"remaining_amount": "400.00"}})
        elif case == "driver_revoked":
            await cash.db.users.update_one({"id": "cash-driver-user"}, {"$set": {"is_active": False}})
        else:
            await cash.db[driver_routes.DELIVERY_PROOFS].update_one({"user_id": OWNER, "assignment_id": assignment},
                {"$set": {"status": "revoked"}})
        mutation_done.append(True)
        return {"slug": "delivered", "verified_slug": "delivered"}
    cash.salla.side_effect = external
    response = await cash.driver_http.post("/store-delivery/app/deliveries/status", json=payload)
    expected_status = {"amount": 409, "driver_revoked": 403, "proof_revoked": 422}[case]
    assert response.status_code == expected_status, response.text
    assert mutation_done == [True]
    assert (await cash.db.store_delivery_assignments.find_one({"id": assignment}))["status"] == "out_for_delivery"
    assert await cash.db.store_delivery_collections.count_documents({"user_id": OWNER}) == 0
    assert await cash.db[driver_routes.DRIVER_EARNINGS].count_documents({"user_id": OWNER}) == 0
    assert await financial_snapshot(cash.db) == before_finance
