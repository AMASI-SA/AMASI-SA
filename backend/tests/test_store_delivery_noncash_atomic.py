"""Real-replica regression for overlapping noncash delivery attempts."""
import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

from fastapi import HTTPException
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

import operational_atomic
import store_delivery_driver_app_routes as delivery
import store_delivery_noncash_retry as noncash
from .test_store_delivery_bank_transfer_completion import fixture, upload


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["bank_transfer", "card_terminal", None])
async def test_noncash_concurrent_attempt_never_erases_winning_capture(monkeypatch, method):
    async with fixture(monkeypatch) as (db, client, provider):
        if method is None:
            await db.unified_orders.update_one({}, {"$set": {
                "remaining_amount": 0, "paid_amount": 70, "payment_method": "credit_card",
                "payment_status": "paid",
            }})
        proof = await upload(client, "delivery-proof", "proof_reference")
        receipt = await upload(client, "receipt", "receipt_reference") if method else None
        payload = {"barcode": "101", "target_status": "delivered", "payment_method": method,
            "bank_account_id": "bank" if method == "bank_transfer" else None,
            "delivery_proof_reference": proof, "receipt_reference": receipt,
            "physical_cash_amount": None, "physical_cash_confirmed": False}
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def sync(*args, **kwargs):
            nonlocal calls
            assert operational_atomic._ACTIVE.get() is None
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            return {"slug": "delivered"}

        provider.side_effect = sync
        first = asyncio.create_task(client.post("/store-delivery/app/deliveries/status", json=payload))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await db.store_drivers.update_one({}, {"$set": {"delivery_fee": 999}})
            second = await client.post("/store-delivery/app/deliveries/status", json=payload)
        finally:
            release.set()
        try:
            original = await first
        except Exception as exc:
            original = exc
        counts = {name: await db[name].count_documents({}) for name in (
            "store_delivery_driver_earnings", "store_delivery_collections", "store_delivery_payment_reviews")}
        assert counts["store_delivery_driver_earnings"] == 1, counts
        assert counts["store_delivery_collections"] == 1, counts
        assert counts["store_delivery_payment_reviews"] == (1 if method else 0), counts
        assert not isinstance(original, Exception), repr(original)
        assert original.status_code == 200, original.text
        assert second.status_code == 503, second.text
        assert second.json()["detail"]["code"] == "driver_delivery_in_progress"
        assert calls == 1
        replay = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert replay.status_code == 200, replay.text
        assert calls == 1
        earning = await db.store_delivery_driver_earnings.find_one({})
        collection = await db.store_delivery_collections.find_one({})
        assert earning["amount"] == 20
        assert collection["cod_custody_amount"] == 0
        assert collection["review_status"] == ("pending_accountant_review" if method else "not_required")
        if method:
            assert (await db.store_delivery_payment_reviews.find_one({}))["status"] == "pending"
        assert await db.general_ledger.count_documents({}) == 0


async def payload_for(db, client, method):
    if method is None:
        await db.unified_orders.update_one({}, {"$set": {"remaining_amount": 0,
            "paid_amount": 70, "payment_method": "credit_card", "payment_status": "paid"}})
    proof = await upload(client, "delivery-proof", "proof_reference")
    receipt = await upload(client, "receipt", "receipt_reference") if method else None
    return {"barcode": "101", "target_status": "delivered", "payment_method": method,
        "bank_account_id": "bank" if method == "bank_transfer" else None,
        "delivery_proof_reference": proof, "receipt_reference": receipt,
        "physical_cash_amount": None, "physical_cash_confirmed": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["bank_transfer", "card_terminal", None])
async def test_assigned_shortcut_claim_prevents_late_provider_regression(monkeypatch, method):
    async with fixture(monkeypatch) as (db, client, provider):
        await db.store_delivery_assignments.update_one({}, {"$set": {"status": "assigned"}})
        await db.order_review_workflows.update_one({}, {"$set": {"stage": "store_courier"}})
        payload = await payload_for(db, client, method)
        entered, release = asyncio.Event(), asyncio.Event()
        provider_states = []
        calls = 0
        async def sync(*args, **kwargs):
            nonlocal calls
            assert operational_atomic._ACTIVE.get() is None
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            provider_states.append(kwargs["slug"])
            return {"slug": kwargs["slug"], "verified_slug": kwargs["slug"]}
        provider.side_effect = sync
        first = asyncio.create_task(client.post("/store-delivery/app/deliveries/status", json=payload))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            second = await client.post("/store-delivery/app/deliveries/status", json=payload)
            competing_pickup = await client.post("/store-delivery/app/deliveries/status", json={
                "barcode": "101", "target_status": "out_for_delivery"})
        finally:
            release.set()
        original = await first
        assert second.status_code == 503, second.text
        assert competing_pickup.status_code == 503, competing_pickup.text
        assert original.status_code == 200, original.text
        assert provider_states == ["delivering", "delivered"]
        assert (await db.store_delivery_assignments.find_one({}))["status"] == "delivered"
        assert (await db.order_review_workflows.find_one({}))["stage"] == "delivered"
        assert await db.store_delivery_driver_earnings.count_documents({}) == 1
        assert await db.store_delivery_collections.count_documents({}) == 1
        assert await db.store_delivery_events.count_documents({"event_type": "store_delivery_out_for_delivery"}) == 1
        assert await db.store_delivery_events.count_documents({"event_type": "store_delivery_delivered"}) == 1
        replay = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert replay.status_code == 200, replay.text
        assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_status", ["delivering", "delivered"])
async def test_uncertain_pickup_reads_first_and_never_regresses_a_delivered_provider(monkeypatch, provider_status):
    async with fixture(monkeypatch) as (db, client, provider):
        await db.store_delivery_assignments.update_one({}, {"$set": {"status": "assigned"}})
        await db.order_review_workflows.update_one({}, {"$set": {"stage": "store_courier"}})
        payload = await payload_for(db, client, "bank_transfer")
        provider.side_effect = TimeoutError()
        first = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert first.status_code == 503
        assignment = await db.store_delivery_assignments.find_one({})
        assert assignment["status"] == "assigned"
        assert assignment[noncash.ATTEMPT]["phase"] == "pickup"
        assert assignment[noncash.ATTEMPT]["state"] == "uncertain"
        assert await db.store_delivery_events.count_documents({}) == 0
        assert await db.store_delivery_driver_earnings.count_documents({}) == 0
        assert (await db.order_review_workflows.find_one({}))["stage"] == "store_courier"
        async def recovered(*args, **kwargs):
            assert kwargs["slug"] == "delivered"
            return {"slug": "delivered"}
        provider.side_effect = recovered
        read = AsyncMock(return_value={"data": {"id": "order-101", "reference_id": "101",
            "status": {"slug": provider_status}}})
        monkeypatch.setattr(delivery, "_call_salla", read)
        result = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert result.status_code == 200, result.text
        assert provider.await_count == (2 if provider_status == "delivering" else 1)
        read.assert_awaited_once()
        assert read.await_args.args[2] == "GET"
        assert (await db.store_delivery_assignments.find_one({}))["status"] == "delivered"
        assert await db.store_delivery_driver_earnings.count_documents({}) == 1
        assert await db.store_delivery_events.count_documents({"event_type": "store_delivery_out_for_delivery"}) == 1


@pytest.mark.asyncio
async def test_pickup_commit_rolls_back_and_can_advance_without_repeating_pickup_post(monkeypatch):
    async with fixture(monkeypatch) as (db, client, provider):
        await db.store_delivery_assignments.update_one({}, {"$set": {"status": "assigned"}})
        await db.order_review_workflows.update_one({}, {"$set": {"stage": "store_courier"}})
        payload = await payload_for(db, client, "bank_transfer")
        async def synced(*args, **kwargs):
            return {"slug": kwargs["slug"]}
        provider.side_effect = synced
        persist = noncash._persist_pickup
        async def failed(*args, **kwargs):
            await persist(*args, **kwargs)
            raise RuntimeError("synthetic fault after pickup workflow and event")
        monkeypatch.setattr(noncash, "_persist_pickup", failed)
        with pytest.raises(RuntimeError, match="synthetic fault"):
            await client.post("/store-delivery/app/deliveries/status", json=payload)
        assignment = await db.store_delivery_assignments.find_one({})
        assert assignment["status"] == "assigned"
        assert assignment[noncash.ATTEMPT]["phase"] == "pickup"
        assert assignment[noncash.ATTEMPT]["state"] == "confirmed"
        assert "store_delivery_status" not in await db.unified_orders.find_one({})
        assert (await db.order_review_workflows.find_one({}))["stage"] == "store_courier"
        assert await db.store_delivery_events.count_documents({}) == 0
        assert await db.store_delivery_driver_earnings.count_documents({}) == 0
        monkeypatch.setattr(noncash, "_persist_pickup", persist)
        result = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert result.status_code == 200, result.text
        assert [call.kwargs["slug"] for call in provider.await_args_list] == ["delivering", "delivered"]
        assert await db.store_delivery_events.count_documents({"event_type": "store_delivery_out_for_delivery"}) == 1
        assert await db.store_delivery_events.count_documents({"event_type": "store_delivery_delivered"}) == 1


@pytest.mark.asyncio
async def test_lost_final_response_after_pickup_never_repeats_either_post(monkeypatch):
    async with fixture(monkeypatch) as (db, client, provider):
        await db.store_delivery_assignments.update_one({}, {"$set": {"status": "assigned"}})
        payload = await payload_for(db, client, "bank_transfer")
        async def sync(*args, **kwargs):
            if kwargs["slug"] == "delivered":
                raise TimeoutError()
            return {"slug": "delivering"}
        provider.side_effect = sync
        first = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert first.status_code == 503
        assignment = await db.store_delivery_assignments.find_one({})
        assert assignment["status"] == "out_for_delivery"
        assert assignment[noncash.ATTEMPT]["phase"] == "delivery"
        assert assignment[noncash.ATTEMPT]["state"] == "uncertain"
        assert await db.store_delivery_driver_earnings.count_documents({}) == 0
        assert await db.store_delivery_events.count_documents({"event_type": "store_delivery_out_for_delivery"}) == 1
        read = AsyncMock(return_value={"data": {"id": "order-101", "reference_id": "101",
            "status": {"slug": "delivered"}}})
        monkeypatch.setattr(delivery, "_call_salla", read)
        completed = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert completed.status_code == 200, completed.text
        assert [call.kwargs["slug"] for call in provider.await_args_list] == ["delivering", "delivered"]
        read.assert_awaited_once()
        assert read.await_args.args[2] == "GET"
        assert await db.store_delivery_driver_earnings.count_documents({}) == 1
        assert await db.store_delivery_events.count_documents({"event_type": "store_delivery_out_for_delivery"}) == 1
        assert await db.store_delivery_events.count_documents({"event_type": "store_delivery_delivered"}) == 1


async def assert_uncaptured(db):
    for name in ("store_delivery_driver_earnings", "store_delivery_collections",
                 "store_delivery_payment_reviews", "store_delivery_events"):
        assert await db[name].count_documents({}) == 0, name
    assert (await db.store_delivery_assignments.find_one({}))["status"] == "out_for_delivery"
    assert "store_delivery_status" not in await db.unified_orders.find_one({})
    assert (await db.order_review_workflows.find_one({}))["stage"] == "delivering"
    assert (await db.store_delivery_delivery_proofs.find_one({}))["status"] == "uploaded"
    receipt = await db.store_delivery_receipts.find_one({})
    if receipt:
        assert receipt["status"] == "uploaded"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["bank_transfer", "card_terminal", None])
@pytest.mark.parametrize("failure_status", [401, 502])
async def test_unknown_salla_result_recovers_by_get_only_with_original_payload(monkeypatch, method, failure_status):
    async with fixture(monkeypatch) as (db, client, provider):
        payload = await payload_for(db, client, method)
        # Even 401 may be the GET failing after a successful POST in the old
        # provider adapter. It is not permission to dispatch the POST again.
        provider.side_effect = HTTPException(failure_status, detail={"code": "salla_delivery_status_update_failed"})
        first = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert first.status_code == 503
        assert first.json()["detail"]["code"] == "driver_delivery_provider_confirmation_pending"
        await assert_uncaptured(db)
        attempt = (await db.store_delivery_assignments.find_one({}))[noncash.ATTEMPT]
        assert attempt["state"] == "uncertain"
        reads = []
        observed = {"id": "order-101", "reference_id": "101", "status": {"slug": "delivering"}}

        async def read(database, owner, verb, path, **kwargs):
            assert operational_atomic._ACTIVE.get() is None
            assert database is db and owner == "merchant" and verb == "GET"
            assert path == "/orders/order-101"
            reads.append(path)
            return {"data": deepcopy(observed)}

        monkeypatch.setattr(delivery, "_call_salla", read)
        conflict = await client.post("/store-delivery/app/deliveries/status", json={
            **payload, "delivery_proof_reference": "different-proof"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "driver_delivery_idempotency_conflict"
        assert reads == []
        pending = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert pending.status_code == 503
        await assert_uncaptured(db)
        observed["status"]["slug"] = "delivered"
        # Provider delivery may have updated the outstanding balance already.
        await db.unified_orders.update_one({}, {"$set": {"remaining_amount": 0, "paid_amount": 70}})
        completed = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert completed.status_code == 200, completed.text
        assert completed.json()["collection"]["amount"] == (70 if method else 0)
        assert completed.json()["earning_amount"] == 20
        assert len(reads) == 2
        provider.assert_awaited_once()
        if method:
            assert (await db.store_delivery_payment_reviews.find_one({}))["status"] == "pending"
        replay = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert replay.status_code == 200, replay.text
        assert len(reads) == 2
        provider.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", [
    {}, {"id": "another-order", "status": {"slug": "delivered"}},
    {"id": "order-101", "reference_id": "another-number", "status": {"slug": "delivered"}},
    {"id": "order-101", "reference_id": "101", "status": {}},
])
async def test_uncertain_provider_identity_or_status_never_authorizes_local_commit(monkeypatch, observed):
    async with fixture(monkeypatch) as (db, client, provider):
        payload = await payload_for(db, client, "bank_transfer")
        provider.side_effect = TimeoutError()
        assert (await client.post("/store-delivery/app/deliveries/status", json=payload)).status_code == 503
        read = AsyncMock(return_value={"data": observed})
        monkeypatch.setattr(delivery, "_call_salla", read)
        retry = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert retry.status_code == 503
        await assert_uncaptured(db)
        provider.assert_awaited_once()
        assert all(call.args[2] == "GET" for call in read.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["bank_transfer", "card_terminal", None])
async def test_local_commit_failure_rolls_back_every_document_and_retries_without_salla(monkeypatch, method):
    async with fixture(monkeypatch) as (db, client, provider):
        payload = await payload_for(db, client, method)
        persist = noncash._persist_delivery

        async def fail_after_all_writes(*args, **kwargs):
            await persist(*args, **kwargs)
            raise RuntimeError("synthetic fault after workflow and delivery event")

        monkeypatch.setattr(noncash, "_persist_delivery", fail_after_all_writes)
        with pytest.raises(RuntimeError, match="synthetic fault"):
            await client.post("/store-delivery/app/deliveries/status", json=payload)
        await assert_uncaptured(db)
        assert (await db.store_delivery_assignments.find_one({}))[noncash.ATTEMPT]["state"] == "confirmed"
        monkeypatch.setattr(noncash, "_persist_delivery", persist)
        monkeypatch.setattr(delivery, "_call_salla", AsyncMock(side_effect=AssertionError("No provider IO after confirmation")))
        response = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["earning_amount"] == 20
        assert (await db.order_review_workflows.find_one({}))["stage"] == "delivered"
        assert await db.store_delivery_events.count_documents({"event_type": "store_delivery_delivered"}) == 1
        assert noncash.ATTEMPT not in await db.store_delivery_assignments.find_one({})
        provider.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("forbidden", ["financial_post", "payment_approval", "delete_capture", "unpause"])
async def test_noncash_profile_cannot_post_approve_delete_or_unpause(monkeypatch, forbidden):
    async with fixture(monkeypatch) as (db, client, provider):
        await db.mz2_atomic_owners.insert_one({"_id": "merchant", "writes_paused": True, "revision": 0})
        async def rejected(scoped):
            if forbidden == "financial_post":
                await scoped.general_ledger.insert_one({"user_id": "merchant", "amount": 70})
            elif forbidden == "payment_approval":
                await scoped.store_delivery_payment_reviews.update_one({"user_id": "merchant", "assignment_id": "assignment"},
                    {"$set": {"status": "approved"}})
            elif forbidden == "delete_capture":
                await scoped.store_delivery_collections.delete_one({"user_id": "merchant", "assignment_id": "assignment"})
            else:
                await scoped.mz2_atomic_owners.update_one({"_id": "merchant"}, {"$set": {"writes_paused": False}})
        with pytest.raises(HTTPException) as error:
            await operational_atomic.operational_owner(db, "merchant", rejected, profile=noncash.PROFILE)
        assert error.value.status_code == 409
        assert await db.mz2_atomic_owners.find_one({"_id": "merchant"}) == {
            "_id": "merchant", "writes_paused": True, "revision": 0}
        assert await db.general_ledger.count_documents({}) == 0
        provider.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("readback", [
    {}, {"data": {}}, {"data": {"id": "different", "status": {"slug": "delivered"}}},
    {"data": {"id": "order-101", "reference_id": "different", "status": {"slug": "delivered"}}},
    {"data": {"id": "order-101", "reference_id": "101", "status": {"slug": "delivering"}}},
])
async def test_first_dispatch_requires_matching_delivered_readback(monkeypatch, readback):
    actual_push = delivery._push_salla_delivery_status
    async with fixture(monkeypatch) as (db, client, provider):
        payload = await payload_for(db, client, "bank_transfer")
        calls = []

        async def transport(database, owner, verb, path, **kwargs):
            assert operational_atomic._ACTIVE.get() is None
            calls.append(verb)
            if verb == "POST":
                assert kwargs["single_post_attempt"] is True
                return {"success": True}
            return readback

        monkeypatch.setattr(delivery, "_push_salla_delivery_status", actual_push)
        monkeypatch.setattr(delivery, "_call_salla", transport)
        result = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert result.status_code == 503, result.text
        await assert_uncaptured(db)
        assert calls == ["POST", "GET"]
        assert (await db.store_delivery_assignments.find_one({}))[noncash.ATTEMPT]["state"] == "uncertain"


@pytest.mark.asyncio
async def test_late_first_response_after_lease_takeover_remains_retryable(monkeypatch):
    async with fixture(monkeypatch) as (db, client, provider):
        payload = await payload_for(db, client, "bank_transfer")
        sync_entered, sync_release = asyncio.Event(), asyncio.Event()
        read_entered, read_release = asyncio.Event(), asyncio.Event()

        async def sync(*args, **kwargs):
            sync_entered.set()
            await sync_release.wait()
            return {"slug": "delivered"}

        async def read(*args, **kwargs):
            read_entered.set()
            await read_release.wait()
            return {"data": {"id": "order-101", "reference_id": "101", "status": {"slug": "delivered"}}}

        provider.side_effect = sync
        monkeypatch.setattr(delivery, "_call_salla", read)
        first = asyncio.create_task(client.post("/store-delivery/app/deliveries/status", json=payload))
        second = None
        try:
            await asyncio.wait_for(sync_entered.wait(), 5)
            await db.store_delivery_assignments.update_one({}, {"$set": {f"{noncash.ATTEMPT}.lease_until": "2000-01-01T00:00:00+00:00"}})
            second = asyncio.create_task(client.post("/store-delivery/app/deliveries/status", json=payload))
            await asyncio.wait_for(read_entered.wait(), 5)
            sync_release.set()
            original = await first
            assert original.status_code == 503, original.text
            await assert_uncaptured(db)
        finally:
            sync_release.set()
            read_release.set()
        assert second is not None
        recovered = await second
        assert recovered.status_code == 200, recovered.text
        assert await db.store_delivery_driver_earnings.count_documents({}) == 1
        assert await db.store_delivery_collections.count_documents({}) == 1
        provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_conversation_evidence_commits_and_replays_with_noncash_delivery(monkeypatch):
    async with fixture(monkeypatch) as (db, client, provider):
        payload = await payload_for(db, client, "bank_transfer")
        token = await upload(client, "customer-conversation", "evidence_reference")
        payload["conversation_evidence_reference"] = token
        first = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert first.status_code == 200, first.text
        replay = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert replay.status_code == 200, replay.text
        assert (await db.store_delivery_customer_conversation_evidence.find_one({}))["status"] == "bound"
        assert await db.store_delivery_events.count_documents({"event_type": "store_delivery_status_evidence"}) == 1
        assert await db.store_delivery_driver_earnings.count_documents({}) == 1
        provider.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["raw_only", "numeric_flat", "numeric_raw"])
async def test_canonical_identity_types_and_raw_only_records_complete_and_replay(monkeypatch, shape):
    async with fixture(monkeypatch) as (db, client, provider):
        await db.store_delivery_assignments.update_one({}, {"$set": {"order_id": "42"}})
        patch = {"$set": {"order_id": 42 if shape == "numeric_flat" else "42"}}
        if shape == "raw_only":
            patch = {"$set": {"raw_by_source.salla_direct.id": "42"}, "$unset": {"order_id": ""}}
        elif shape == "numeric_raw":
            patch["$set"]["raw_by_source.salla_direct.id"] = 42
        await db.unified_orders.update_one({}, patch)
        payload = await payload_for(db, client, "bank_transfer")
        for _ in range(2):
            result = await client.post("/store-delivery/app/deliveries/status", json=payload)
            assert result.status_code == 200, result.text
            assert result.json()["earning_amount"] == 20
        assert await db.store_delivery_driver_earnings.count_documents({}) == 1
        assert await db.store_delivery_collections.count_documents({}) == 1
        provider.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [
    {"order_id": "another-order"}, {"raw_by_source.salla_direct.id": "another-order"},
    {"order_number": "another-number"},
])
async def test_canonical_alias_collision_is_rejected_before_claim_or_provider(monkeypatch, changed):
    async with fixture(monkeypatch) as (db, client, provider):
        payload = await payload_for(db, client, "bank_transfer")
        await db.unified_orders.update_one({}, {"$set": changed})
        result = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert result.status_code == 409, result.text
        assert result.json()["detail"]["code"] == "canonical_order_identity_conflict"
        await assert_uncaptured(db)
        assert noncash.ATTEMPT not in await db.store_delivery_assignments.find_one({})
        provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_balance_legacy_cash_selection_replays_without_cash_capture(monkeypatch):
    async with fixture(monkeypatch) as (db, client, provider):
        payload = await payload_for(db, client, None)
        payload["payment_method"] = "cash"
        for _ in range(2):
            result = await client.post("/store-delivery/app/deliveries/status", json=payload)
            assert result.status_code == 200, result.text
            assert result.json()["collection"]["payment_method"] is None
        collection = await db.store_delivery_collections.find_one({})
        assert collection["amount"] == 0 and collection["cod_custody_amount"] == 0
        assert "physical_cash_evidence" not in collection
        assert await db.store_delivery_driver_earnings.count_documents({}) == 1
        provider.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["bank_transfer", "card_terminal"])
async def test_atomic_delivery_remains_eligible_for_real_accountant_review(monkeypatch, method):
    from operational_balance_store import STATES, initial
    import operational_driver_payment_review as review_service

    async with fixture(monkeypatch) as (db, client, provider):
        await db.users.insert_one({"id": "merchant", "role": "owner", "is_active": True})
        state = initial("merchant")
        state.update(status="active", started_at="2026-01-01T00:00:00+00:00", revision=1)
        await db[STATES].insert_one(state)
        await db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct": {
            "id": "order-101", "reference_id": "101", "created_at": "2026-02-01T00:00:00+00:00",
            "currency": "SAR", "total": {"amount": 70, "currency": "SAR"}, "payment": {"method": "cod"}}}})
        payload = await payload_for(db, client, method)
        # A deliberate provider delay across clocks exposes a captured-at versus
        # delivered-at mismatch in the real payment-review evidence consumer.
        clock = ["2027-01-01T00:00:00+00:00"]
        monkeypatch.setattr(delivery, "_now", lambda: clock[0])
        async def delayed_confirmation(*args, **kwargs):
            clock[0] = "2027-01-02T00:00:00+00:00"
            return {"slug": "delivered"}
        provider.side_effect = delayed_confirmation
        delivered = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert delivered.status_code == 200, delivered.text
        assignment = await db.store_delivery_assignments.find_one({})
        collection = await db.store_delivery_collections.find_one({})
        earning = await db.store_delivery_driver_earnings.find_one({})
        review = await db.store_delivery_payment_reviews.find_one({})
        assert assignment["delivered_at"] == collection["collected_at"] == earning["earned_at"] == review["submitted_at"]
        result = await review_service.options(db, owner="merchant", actor="merchant", assignment_id="assignment",
            clock="2027-01-03T00:00:00+00:00")
        assert result["can_approve"] is True, result
        assert result["amount"] == "70.00"
        assert review["status"] == "pending"
        assert await db[STATES].find_one({}) == state
        assert await db.general_ledger.count_documents({}) == 0
        provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_replay_reads_bound_result_before_current_carrier_guard(monkeypatch):
    from store_delivery_domain import carrier_assignment_snapshot
    async with fixture(monkeypatch) as (db, client, provider):
        snapshot = carrier_assignment_snapshot(driver={"id": "driver", "delivery_fee": 20, "shipping_company": "مندوب الرياض"},
            shipping_company="مندوب الرياض")
        await db.store_delivery_assignments.update_one({}, {"$set": snapshot})
        await db.unified_orders.update_one({}, {"$set": {"shipping_company": "مندوب الرياض",
            "salla_shipping_current.company_name": "مندوب الرياض", "salla_shipping_current.company_code": "mandoob-riyadh"}})
        payload = await payload_for(db, client, "bank_transfer")
        result = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert result.status_code == 200, result.text
        await db.unified_orders.update_one({}, {"$set": {"salla_shipping_current.company_name": "iMile للتوصيل",
            "salla_shipping_current.company_code": "imile"}})
        replay = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["earning_amount"] == 20
        assert await db.store_delivery_driver_earnings.count_documents({}) == 1
        provider.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["assigned", "out_for_delivery"])
@pytest.mark.parametrize("problem", ["different_assignment", "missing_link", "different_driver"])
async def test_workflow_binding_conflict_is_rejected_before_any_provider_call(monkeypatch, status, problem):
    async with fixture(monkeypatch) as (db, client, provider):
        await db.store_delivery_assignments.update_one({}, {"$set": {"status": status}})
        if problem == "missing_link":
            await db.order_review_workflows.update_one({}, {"$unset": {"store_delivery_assignment_id": ""}})
        else:
            key = "store_delivery_assignment_id" if problem == "different_assignment" else "store_courier_driver_profile_id"
            await db.order_review_workflows.update_one({}, {"$set": {key: "another-identity"}})
        before = await db.order_review_workflows.find_one({})
        payload = await payload_for(db, client, "bank_transfer")
        refused = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == ("driver_delivery_workflow_assignment_missing" if problem == "missing_link"
            else "driver_delivery_workflow_assignment_conflict")
        assert await db.order_review_workflows.find_one({}) == before
        assert noncash.ATTEMPT not in await db.store_delivery_assignments.find_one({})
        assert await db.store_delivery_collections.count_documents({}) == 0
        assert await db.store_delivery_driver_earnings.count_documents({}) == 0
        provider.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["assigned", "out_for_delivery"])
@pytest.mark.parametrize("change", ["delete", "rebind"])
async def test_lost_pinned_workflow_aborts_local_phase_and_recovers_when_restored(monkeypatch, status, change):
    async with fixture(monkeypatch) as (db, client, provider):
        await db.store_delivery_assignments.update_one({}, {"$set": {"status": status}})
        original = await db.order_review_workflows.find_one({})
        payload = await payload_for(db, client, "bank_transfer")
        async def changed(*args, **kwargs):
            if change == "delete":
                await db.order_review_workflows.delete_one({"_id": original["_id"]})
            else:
                await db.order_review_workflows.update_one({"_id": original["_id"]},
                    {"$set": {"store_delivery_assignment_id": "another-assignment"}})
            return {"slug": kwargs["slug"]}
        provider.side_effect = changed
        refused = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == ("driver_delivery_workflow_changed" if change == "delete"
            else "driver_delivery_workflow_assignment_conflict")
        assignment = await db.store_delivery_assignments.find_one({})
        assert assignment["status"] == status
        assert assignment[noncash.ATTEMPT]["state"] == "confirmed"
        for name in ("store_delivery_collections", "store_delivery_driver_earnings", "store_delivery_payment_reviews", "store_delivery_events"):
            assert await db[name].count_documents({}) == 0, name
        assert (await db.store_delivery_delivery_proofs.find_one({}))["status"] == "uploaded"
        assert "store_delivery_status" not in await db.unified_orders.find_one({})
        await db.order_review_workflows.replace_one({"_id": original["_id"]}, original, upsert=True)
        async def synced(*args, **kwargs):
            return {"slug": kwargs["slug"]}
        provider.side_effect = synced
        completed = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert completed.status_code == 200, completed.text
        assert (await db.order_review_workflows.find_one({}))["stage"] == "delivered"
        assert provider.await_count == (2 if status == "assigned" else 1)
        assert await db.store_delivery_driver_earnings.count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["assigned", "out_for_delivery"])
async def test_legacy_without_workflow_is_explicitly_supported_without_inventing_one(monkeypatch, status):
    async with fixture(monkeypatch) as (db, client, provider):
        await db.order_review_workflows.delete_many({})
        await db.store_delivery_assignments.update_one({}, {"$set": {"status": status}})
        payload = await payload_for(db, client, "bank_transfer")
        async def synced(*args, **kwargs):
            return {"slug": kwargs["slug"]}
        provider.side_effect = synced
        result = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert result.status_code == 200, result.text
        assert await db.order_review_workflows.count_documents({}) == 0
        assert await db.store_delivery_driver_earnings.count_documents({}) == 1


async def capture_snapshot(db):
    return {name: await db[name].find({}).to_list(None) for name in (
        "store_delivery_assignments", "store_delivery_driver_earnings", "store_delivery_collections",
        "store_delivery_payment_reviews", "store_delivery_receipts", "store_delivery_delivery_proofs",
        "unified_orders", "order_review_workflows", "store_delivery_events", "mz2_atomic_owners")}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["assigned", "out_for_delivery"])
@pytest.mark.parametrize("method", ["bank_transfer", "card_terminal", None])
async def test_new_app_session_restores_original_payload_read_only_after_provider_webhook(monkeypatch, status, method):
    async with fixture(monkeypatch) as (db, client, provider):
        await db.store_delivery_assignments.update_one({}, {"$set": {"status": status}})
        payload = await payload_for(db, client, method)
        provider.side_effect = TimeoutError()
        assert (await client.post("/store-delivery/app/deliveries/status", json=payload)).status_code == 503
        del payload  # A terminated client no longer has its React state.
        await db.unified_orders.update_one({}, {"$set": {"order_status_slug": "delivered", "remaining_amount": 0, "paid_amount": 70}})
        before = await capture_snapshot(db)
        assignment = await db.store_delivery_assignments.find_one({})
        # Other existing assignment APIs exclude only the top-level Mongo id.
        json.dumps({key: value for key, value in assignment.items() if key != "_id"})
        async with AsyncClient(transport=ASGITransport(app=client._transport.app), base_url="http://new-app-session") as restarted:
            for path in ("/store-delivery/app/deliveries", "/store-delivery/app/deliveries/home", "/store-delivery/app/deliveries/search/101"):
                response = await restarted.get(path)
                assert response.status_code == 200, response.text
                item = response.json()["item"] if "item" in response.json() else response.json()["items"][0]
                assert noncash.ATTEMPT not in item
                assert assignment[noncash.ATTEMPT]["token"] not in response.text
                assert assignment[noncash.ATTEMPT]["fingerprint"] not in response.text
                restored = item["pending_delivery_completion"]
                assert restored["version"] == 1 and restored["assignment_id"] == "assignment"
                assert restored["collection_amount"] == ("70.00" if method else "0.00")
                assert restored["bank_name_snapshot"] == ("Synthetic bank" if method == "bank_transfer" else None)
                assert item["outstanding_amount"] == 0
            assert await capture_snapshot(db) == before
            read = AsyncMock(return_value={"data": {"id": "order-101", "reference_id": "101", "status": {"slug": "delivered"}}})
            monkeypatch.setattr(delivery, "_call_salla", read)
            refused = await restarted.post("/store-delivery/app/deliveries/status", json={**restored["payload"],
                "delivery_proof_reference": "new-upload-is-not-the-original-attempt"})
            assert refused.status_code == 409
            assert await capture_snapshot(db) == before
            completed = await restarted.post("/store-delivery/app/deliveries/status", json=restored["payload"])
            assert completed.status_code == 200, completed.text
            assert completed.json()["collection"]["amount"] == (70 if method else 0)
            assert await db.store_delivery_driver_earnings.count_documents({}) == 1
            provider.assert_awaited_once()
            read.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("other_owner", [False, True])
async def test_restart_payload_never_crosses_driver_or_owner_identity(monkeypatch, other_owner):
    async with fixture(monkeypatch) as (db, client, provider):
        payload = await payload_for(db, client, "bank_transfer")
        provider.side_effect = TimeoutError()
        assert (await client.post("/store-delivery/app/deliveries/status", json=payload)).status_code == 503
        owner = "another-merchant" if other_owner else "merchant"
        actor = {"id": "another-account", "role": "store_driver", "created_by": owner, "_session_client": "amasi_mobile"}
        await db.users.insert_one(actor)
        await db.store_drivers.insert_one({"user_id": owner, "id": "another-driver", "account_user_id": actor["id"], "status": "active"})
        app = FastAPI()
        app.include_router(delivery.make_store_delivery_driver_app_router(db, lambda: actor))
        before = await capture_snapshot(db)
        async with AsyncClient(transport=ASGITransport(app), base_url="http://other-driver") as other:
            assert (await other.get("/store-delivery/app/deliveries")).json()["items"] == []
            assert (await other.get("/store-delivery/app/deliveries/home")).json()["items"] == []
            assert (await other.get("/store-delivery/app/deliveries/search/101")).status_code == 404
            assert (await other.post("/store-delivery/app/deliveries/status", json=payload)).status_code == 404
        assert await capture_snapshot(db) == before
        provider.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("request_payload.bank_account_id", "other-bank"), ("fingerprint", "0" * 64),
    ("collection.user_id", "another-merchant"), ("collection.driver_id", "another-driver"),
])
async def test_tampered_restart_journal_is_hidden_and_cannot_authorize_another_capture(monkeypatch, field, value):
    async with fixture(monkeypatch) as (db, client, provider):
        payload = await payload_for(db, client, "bank_transfer")
        provider.side_effect = TimeoutError()
        assert (await client.post("/store-delivery/app/deliveries/status", json=payload)).status_code == 503
        await db.store_delivery_assignments.update_one({}, {"$set": {f"{noncash.ATTEMPT}.{field}": value}})
        before = await capture_snapshot(db)
        response = await client.get("/store-delivery/app/deliveries/home")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert noncash.ATTEMPT not in item and "pending_delivery_completion" not in item
        refused = await client.post("/store-delivery/app/deliveries/status", json=payload)
        assert refused.status_code == 409, refused.text
        assert await capture_snapshot(db) == before
        provider.assert_awaited_once()
