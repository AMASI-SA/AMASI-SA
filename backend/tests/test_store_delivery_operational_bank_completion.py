"""Driver HTTP -> receipt -> delivery/fee -> original bank statement -> review.

The store starts with the observed phase shape: operational active, native paused,
canonical cash only, and explicit legacy/operational bank identities.
"""
from contextlib import asynccontextmanager
from io import BytesIO
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
import pytest

import store_delivery_noncash_retry as noncash
import store_delivery_driver_app_routes as delivery
import accounting_shipping_native_observer as native_observer
from operational_balance_store import STATES, initial, digest
from store_delivery_bank_identity import SNAPSHOT_FIELDS
from store_delivery_payment_review_routes import make_store_delivery_payment_review_router, ensure_store_delivery_payment_review_indexes
from .test_store_delivery_bank_transfer_completion import fixture, upload

START = "2026-10-31T21:30:01.284707+00:00"
BANK = "account:legacy-1"
SECOND_BANK = "operational-bank:operational-1"


@asynccontextmanager
async def live_shape(monkeypatch):
    actual_observer = native_observer.observe_driver_delivery
    async with fixture(monkeypatch) as (db, driver, provider):
        # Keep the real internal financial observer; only Salla is an external
        # boundary stub. Paused/unconfigured native accounting stays gated.
        monkeypatch.setattr(native_observer, "observe_driver_delivery", actual_observer)
        actor = {"id": "merchant", "role": "owner", "is_active": True}
        await db.users.insert_one(dict(actor))
        state = initial("merchant")
        state.update(status="active", started_at=START, revision=1)
        await db[STATES].insert_one(state)
        await db.mz2_atomic_owners.insert_one({"_id": "merchant", "writes_paused": True,
                                              "ledger_backend_state": "legacy_active", "revision": 0})
        await db.settings.insert_one({"user_id": "merchant", "mezan2_financial_cutover": {"status": "not_configured"}})
        await db.mz2_financial_accounts.delete_many({"account_type": "bank"})
        await db.mz2_financial_accounts.insert_one({"user_id": "merchant", "id": "cash-only",
            "name": "Synthetic cash", "account_type": "cash", "currency": "SAR", "status": "active"})
        for number in range(1, 4):
            await db.accounts.insert_one({"user_id": "merchant", "id": f"legacy-{number}",
                "name": f"Synthetic bank {number}", "account_type": "bank", "currency": "SAR", "status": "active"})
        await db.mz2_operational_bank_accounts.insert_one({"user_id": "merchant", "id": "operational-1",
            "name": "Synthetic operational bank", "account_type": "bank", "currency": "SAR", "status": "active"})
        await db.unified_orders.update_one({"user_id": "merchant", "order_number": "101"}, {"$set": {
            "raw_by_source.salla_direct": {"id": "order-101", "reference_id": "101", "created_at": "2026-10-01T00:00:00Z",
                "currency": "SAR", "total": {"amount": 70, "currency": "SAR"}, "payment": {"method": "cod"}}}})
        await ensure_store_delivery_payment_review_indexes(db)
        app = FastAPI()
        app.include_router(make_store_delivery_payment_review_router(db, lambda: actor))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as accountant:
            yield db, driver, accountant, provider


async def capture_payload(client, bank=BANK):
    return {"barcode": "101", "target_status": "delivered", "payment_method": "bank_transfer", "bank_account_id": bank,
        "delivery_proof_reference": await upload(client, "delivery-proof", "proof_reference"),
        "receipt_reference": await upload(client, "receipt", "receipt_reference"),
        "physical_cash_amount": None, "physical_cash_confirmed": False}


def original_statement():
    book = Workbook(); sheet = book.active
    sheet.append(["date", "amount", "direction", "description", "reference"])
    sheet.append(["2026-11-02", "70.00", "in", "Synthetic courier customer transfer", "SYNTHETIC-70"])
    result = BytesIO(); book.save(result)
    return result.getvalue()


async def pending_snapshot(db):
    names = ("store_delivery_assignments", "store_delivery_collections", "store_delivery_driver_earnings",
             "store_delivery_payment_reviews", "store_delivery_receipts", "store_delivery_delivery_proofs",
             "unified_orders", "order_review_workflows", "store_delivery_events")
    return {name: await db[name].find({}).to_list(None) for name in names}


async def change_context(db, change):
    if change == "inactive_bank":
        await db.accounts.update_one({"user_id": "merchant", "id": "legacy-1"}, {"$set": {"status": "inactive"}})
    elif change == "native_phase":
        await db.mz2_atomic_owners.update_one({"_id": "merchant"},
            {"$set": {"ledger_backend_state": "v2_active"}, "$inc": {"revision": 1}})
    elif change == "unavailable_phase":
        await db[STATES].update_one({"_id": "merchant"}, {"$set": {"status": "draft"}})
    elif change == "new_period":
        await db[STATES].update_one({"_id": "merchant"}, {"$set": {"started_at": "2026-11-01T21:00:00+00:00"}})
    else:
        raise AssertionError(change)


async def assert_frozen_pending_capture(db):
    snapshot = await pending_snapshot(db)
    assignment = snapshot["store_delivery_assignments"][0]
    assert assignment["status"] == "delivered"
    assert len(snapshot["store_delivery_driver_earnings"]) == 1
    assert snapshot["store_delivery_driver_earnings"][0]["amount"] == 20
    assert len(snapshot["store_delivery_collections"]) == len(snapshot["store_delivery_payment_reviews"]) == 1
    collection, review = snapshot["store_delivery_collections"][0], snapshot["store_delivery_payment_reviews"][0]
    for row in (collection, review):
        assert row["bank_account_id"] == BANK and row["bank_identity_source"] == "accounts"
        assert row["bank_financial_source"] == "operational" and row["bank_operational_started_at"] == START
        assert row["bank_identity_version"] == 2 and row["amount"] == 70
    assert collection["review_status"] == "pending_accountant_review" and review["status"] == "pending"
    assert collection["collected_at"] == assignment["delivered_at"] == review["submitted_at"]
    assert (await db[STATES].find_one({"_id": "merchant"}))["movements"] == []
    assert (await db.mz2_atomic_owners.find_one({"_id": "merchant"}))["writes_paused"] is True
    assert await db.accounting_general_ledger_v2.count_documents({}) == await db.general_ledger.count_documents({}) == 0
    return snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("bank", [BANK, SECOND_BANK])
async def test_operational_bank_full_http_journey_captures_once_then_requires_actual_statement_review(monkeypatch, bank):
    async with live_shape(monkeypatch) as (db, driver, accountant, provider):
        choices = await driver.get("/store-delivery/app/bank-accounts")
        assert choices.status_code == 200, choices.text
        assert {row["id"] for row in choices.json()["items"]} == {
            "account:legacy-1", "account:legacy-2", "account:legacy-3", SECOND_BANK}
        assert choices.json()["financial_source"] == "operational"
        payload = await capture_payload(driver, bank)
        await db.store_drivers.update_one({"id": "driver"}, {"$set": {"delivery_fee": 999}})
        delivered = await driver.post("/store-delivery/app/deliveries/status", json=payload)
        assert delivered.status_code == 200, delivered.text
        assert delivered.json()["earning_amount"] == 20
        snapshot = await pending_snapshot(db)
        collection = snapshot["store_delivery_collections"][0]
        review = snapshot["store_delivery_payment_reviews"][0]
        assert collection["bank_account_id"] == review["bank_account_id"] == bank
        assert collection["bank_financial_source"] == review["bank_financial_source"] == "operational"
        assert all(collection[field] == review[field] for field in SNAPSHOT_FIELDS)
        assert review["status"] == "pending" and collection["review_status"] == "pending_accountant_review"
        assert collection["cod_custody_amount"] == 0
        assert snapshot["store_delivery_driver_earnings"][0]["amount"] == 20
        replay = await driver.post("/store-delivery/app/deliveries/status", json=payload)
        assert replay.status_code == 200, replay.text
        assert await pending_snapshot(db) == snapshot
        assert (await db[STATES].find_one({"_id": "merchant"}))["movements"] == []
        options = await accountant.get("/store-delivery/payment-review/assignment/approval-options")
        assert options.status_code == 200 and options.json()["can_approve"], options.text
        assert options.json()["bank_movements"] == []
        statement = await accountant.post("/store-delivery/payment-review/bank-statements/upload", data={
            "assignment_id": "assignment", "bank_account_id": bank, "request_id": "full-http-original-statement",
            "expected_started_at": START, "expected_session_scope": digest(["merchant", "merchant"])},
            files={"file": ("original.xlsx", original_statement(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert statement.status_code == 200, statement.text
        assert await pending_snapshot(db) == snapshot
        assert (await db[STATES].find_one({"_id": "merchant"}))["movements"] == []
        options = (await accountant.get("/store-delivery/payment-review/assignment/approval-options")).json()
        movement = options["bank_movements"][0]
        assert movement["bank_account_id"] == bank and movement["amount"] == "70.00"
        decision = {"decision": "approved", "note": "Synthetic original statement matched", "destination_financial_id": bank,
            "settlement_reference": movement["id"], "expected_financial_source": "operational",
            "expected_review_revision": options["expected_review_revision"], "expected_session_scope": options["session_scope"]}
        approved = await accountant.post("/store-delivery/payment-review/assignment", json=decision)
        assert approved.status_code == 200, approved.text
        repeated = await accountant.post("/store-delivery/payment-review/assignment", json=decision)
        assert repeated.status_code == 200 and repeated.json() == approved.json()
        state = await db[STATES].find_one({"_id": "merchant"})
        assert len(state["movements"]) == 1 and state["movements"][0]["amount"] == "70.00"
        assert state["movements"][0]["bank_id"] == bank
        assert (await db.store_delivery_driver_earnings.find_one({}))["amount"] == 20
        assert await db.store_delivery_driver_earnings.count_documents({}) == 1
        assert await db.store_delivery_collections.count_documents({}) == 1
        assert (await db.mz2_atomic_owners.find_one({"_id": "merchant"}))["writes_paused"] is True
        assert await db.accounting_general_ledger_v2.count_documents({}) == await db.general_ledger.count_documents({}) == 0
        assert await db.mz2_financial_accounts.count_documents({"account_type": "bank"}) == 0
        provider.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["inactive_bank", "native_phase", "unavailable_phase", "new_period"])
async def test_bank_identity_is_rechecked_in_first_claim_before_any_salla_request(monkeypatch, change):
    async with live_shape(monkeypatch) as (db, driver, _, provider):
        payload = await capture_payload(driver)
        before = await pending_snapshot(db)
        reserve = noncash._reserve

        async def changed_before_claim(*args, **kwargs):
            # Route-level selection passed; the transaction must still validate
            # the exact phase/period/source before an external delivery write.
            await change_context(db, change)
            return await reserve(*args, **kwargs)

        monkeypatch.setattr(noncash, "_reserve", changed_before_claim)
        result = await driver.post("/store-delivery/app/deliveries/status", json=payload)
        assert result.status_code == 409, result.text
        assert result.json()["detail"]["code"] == "driver_payment_bank_context_changed"
        assert await pending_snapshot(db) == before
        provider.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["inactive_bank", "native_phase", "unavailable_phase", "new_period"])
async def test_bank_change_during_salla_preserves_real_delivery_fee_and_original_pending_identity(monkeypatch, change):
    async with live_shape(monkeypatch) as (db, driver, accountant, provider):
        payload = await capture_payload(driver)

        async def changed_during_provider(*args, **kwargs):
            await change_context(db, change)
            return {"slug": "delivered"}

        provider.side_effect = changed_during_provider
        result = await driver.post("/store-delivery/app/deliveries/status", json=payload)
        assert result.status_code == 200, result.text
        snapshot = await assert_frozen_pending_capture(db)
        repeated = await driver.post("/store-delivery/app/deliveries/status", json=payload)
        assert repeated.status_code == 200, repeated.text
        assert await pending_snapshot(db) == snapshot
        options = await accountant.get("/store-delivery/payment-review/assignment/approval-options")
        if change in {"native_phase", "unavailable_phase"}:
            assert options.status_code == 409, options.text
            assert options.json()["detail"]["code"] == "driver_payment_bank_context_changed"
        else:
            assert options.status_code == 200, options.text
            assert not options.json()["can_approve"] and options.json()["blocker"]
        assert await pending_snapshot(db) == snapshot
        provider.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["inactive_bank", "native_phase", "unavailable_phase", "new_period"])
async def test_restart_after_lost_provider_response_reuses_original_bank_snapshot_after_context_change(monkeypatch, change):
    async with live_shape(monkeypatch) as (db, driver, _, provider):
        payload = await capture_payload(driver)
        provider.side_effect = TimeoutError()
        first = await driver.post("/store-delivery/app/deliveries/status", json=payload)
        assert first.status_code == 503, first.text
        del payload
        await change_context(db, change)
        await db.unified_orders.update_one({"user_id": "merchant", "order_number": "101"},
            {"$set": {"order_status_slug": "delivered", "remaining_amount": 0, "paid_amount": 70}})
        before = await pending_snapshot(db)
        async with AsyncClient(transport=ASGITransport(app=driver._transport.app), base_url="http://restarted") as restarted:
            home = await restarted.get("/store-delivery/app/deliveries/home")
            assert home.status_code == 200, home.text
            item = home.json()["items"][0]
            assert noncash.ATTEMPT not in item
            restored = item["pending_delivery_completion"]
            assert restored["collection_amount"] == "70.00" and restored["payload"]["bank_account_id"] == BANK
            assert restored["bank_name_snapshot"] == "Synthetic bank 1"
            assert await pending_snapshot(db) == before
            read = AsyncMock(return_value={"data": {"id": "order-101", "reference_id": "101", "status": {"slug": "delivered"}}})
            monkeypatch.setattr(delivery, "_call_salla", read)
            result = await restarted.post("/store-delivery/app/deliveries/status", json=restored["payload"])
            assert result.status_code == 200, result.text
            snapshot = await assert_frozen_pending_capture(db)
            repeated = await restarted.post("/store-delivery/app/deliveries/status", json=restored["payload"])
            assert repeated.status_code == 200, repeated.text
            assert await pending_snapshot(db) == snapshot
            provider.assert_awaited_once()
            read.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["native", "unavailable"])
async def test_canonical_pending_bank_capture_remains_available_outside_operational_while_native_writes_paused(monkeypatch, phase):
    async with live_shape(monkeypatch) as (db, driver, _, provider):
        await change_context(db, "native_phase" if phase == "native" else "unavailable_phase")
        await db.mz2_financial_accounts.insert_one({"user_id": "merchant", "id": "canonical-bank",
            "name": "Synthetic canonical bank", "account_type": "bank", "currency": "SAR", "status": "active"})
        choices = await driver.get("/store-delivery/app/bank-accounts")
        assert choices.status_code == 200, choices.text
        assert choices.json()["source"] == "mz2_financial_accounts" and choices.json()["financial_source"] == phase
        assert [row["id"] for row in choices.json()["items"]] == ["canonical-bank"]
        payload = await capture_payload(driver, "canonical-bank")
        result = await driver.post("/store-delivery/app/deliveries/status", json=payload)
        assert result.status_code == 200, result.text
        for name in ("store_delivery_collections", "store_delivery_payment_reviews"):
            row = await db[name].find_one({"assignment_id": "assignment"})
            assert row["bank_account_id"] == "canonical-bank" and row["bank_identity_source"] == "mz2_financial_accounts"
            assert row["bank_identity_version"] == 1 and row["bank_financial_source"] == phase
            assert row["bank_operational_started_at"] is None
        assert (await db.store_delivery_payment_reviews.find_one({}))["status"] == "pending"
        assert (await db.store_delivery_driver_earnings.find_one({}))["amount"] == 20
        assert (await db.mz2_atomic_owners.find_one({"_id": "merchant"}))["writes_paused"] is True
        assert await db.accounting_general_ledger_v2.count_documents({}) == await db.general_ledger.count_documents({}) == 0
        provider.assert_awaited_once()
