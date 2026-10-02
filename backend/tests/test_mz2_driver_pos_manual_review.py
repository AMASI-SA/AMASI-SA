"""Real manual POS routes with no mocked destination or processor evidence."""
import asyncio
import hashlib
import os
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs
from uuid import uuid4
from bson import json_util
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio
from test_mz2_shipping_native import OWNER, CUT, NoLegacy, courier, rate, movement
from test_mz2_driver_payment_review import driver_delivery
from mz2_native_fixture import provision_native_opening
from accounting_shipping_native import recognize_cod, _rows, _balance
from accounting_shipping_native_contract import EVENTS
from accounting_shipping_native_setup import save_setup, ensure_shipping_native_indexes
from accounting_sales_tax_service import save_policy
from accounting_onboarding_ssot import create_typed_fact, TypedFactCreate
from store_delivery_payment_review_routes import make_store_delivery_payment_review_router
import accounting_driver_payment_review as module

@pytest_asyncio.fixture
async def manual():
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    parsed = urlparse(uri)
    assert parsed.scheme == "mongodb" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    assert not parsed.username and not parsed.password and parse_qs(parsed.query).get("replicaSet")
    monitor = NoLegacy()
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, event_listeners=[monitor])
    db = client["mz2_pos_manual_" + uuid4().hex]
    try:
        assert (await client.admin.command("hello"))["setName"] == parse_qs(parsed.query)["replicaSet"][0]
        await db.users.insert_one({"id": OWNER, "role": "owner", "is_active": True})
        await db.store_drivers.insert_one({"id": "driver-f", "user_id": OWNER, "status": "active", "name": "Synthetic driver"})
        fact = await create_typed_fact(db, OWNER, OWNER, TypedFactCreate(category="other_receivable", display_name="Synthetic manual POS", reference="signed-pos-opening", amount="0.00", currency="SAR", cutover_date="2026-09-01", evidence="signed-owner-pos-document"))
        alternative = await create_typed_fact(db, OWNER, OWNER, TypedFactCreate(category="other_receivable", display_name="Alternative documented POS", reference="other-signed-pos-opening", amount="0.00", currency="SAR", cutover_date="2026-09-01", evidence="signed-other-pos-document"))
        await provision_native_opening(db, owner=OWNER, cutover=CUT, bank_balances={"bank-f": "1000.00"}, zero_accounts=[("asset", alternative["id"], "other_receivable"), ("asset", fact["id"], "other_receivable"), ("store_driver", "driver-f", "cod_receivable"), ("store_driver", "driver-f", "delivery_fee_payable"), ("courier", "smsa", "cod_receivable"), ("courier", "smsa", "payable")])
        await db.settings.update_one({"user_id": OWNER}, {"$set": {"mezan2_financial_cutover.p02_shipping_cod_enabled": True, "mezan2_financial_cutover.p02_shipping_cod_activation_ref": "SYN-TEST-ONLY"}})
        await ensure_shipping_native_indexes(db)
        await save_setup(db, OWNER, OWNER, courier())
        await save_setup(db, OWNER, OWNER, rate())
        await save_setup(db, OWNER, OWNER, rate(2, kind="store_driver", identity="driver-f", delivery_fee="20.00"))
        await save_policy(db, owner=OWNER, actor_id=OWNER, rate="15", effective_at=CUT, revision=0, reason="synthetic tax")
        app = FastAPI()
        async def actor(): return {"id": OWNER, "role": "owner"}
        app.include_router(make_store_delivery_payment_review_router(db, actor))
        async with AsyncClient(transport=ASGITransport(app), base_url="http://synthetic") as http:
            yield SimpleNamespace(db=db, fact=fact, alternative=alternative, http=http)
        assert monitor.accesses == []
    finally:
        await client.drop_database(db.name)
        client.close()

async def delivered(ctx, number="1"):
    assignment = await driver_delivery(ctx.db, "card_terminal", number)
    await recognize_cod(ctx.db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    return assignment

def approval(ctx, **changes):
    return {"decision":"approved", "note":"Accountant inspected actual bound receipt", "destination_financial_id":ctx.fact["id"], "pos_reviewed_amount":"500.00", **changes}

async def post(ctx, assignment, payload):
    return await ctx.http.post("/store-delivery/payment-review/" + assignment, json=payload)

async def balances(ctx):
    rows = await _rows(ctx.db, OWNER)
    return {"driver": _balance(rows, ("store_driver", "driver-f", "cod_receivable")), "pos": _balance(rows, ("asset", ctx.fact["id"], "other_receivable")), "bank": _balance(rows, ("bank", "bank-f", "main"))}

async def snapshot(db):
    return {name: sorted(json_util.dumps(x, sort_keys=True) for x in await db[name].find({}).to_list(None)) for name in await db.list_collection_names()}

@pytest.mark.asyncio
async def test_manual_pos_real_adapter_approval_replay_and_provenance(manual):
    assignment = await delivered(manual)
    response = await post(manual, assignment, approval(manual))
    assert response.status_code == 200, response.text
    assert await balances(manual) == {"driver": Decimal(0), "pos": Decimal(500), "bank": Decimal(1000)}
    replay = await post(manual, assignment, approval(manual))
    assert replay.status_code == 200 and replay.json()["state"] == "already_posted", replay.text
    event = await manual.db[EVENTS].find_one({"kind": "driver_payment_review", "user_id": OWNER})
    destination = event["destination"]
    assert destination["verification"] == "accountant_pos_review_approved"
    assert destination["source_namespace"] == "manual_pos_receipt"
    assert destination["receipt_hash"] == hashlib.sha256(b"verified receipt 1").hexdigest()
    assert destination["display_name"] == manual.fact["display_name"]
    assert destination["identity_source"] == "mz2_opening_facts_v2"
    assert destination["reviewed_by"] == OWNER and destination["reviewed_amount"] == "500.00"
    assert destination["receipt_reference"] == "receipt-1" and destination["transaction_reference"] is None
    from accounting_ledger_v2 import read_verified_journal_metadata_v2
    metadata = await read_verified_journal_metadata_v2(manual.db, user_id=OWNER, txn_group_id=event["txn_group_id"], require_unreversed=True)
    assert metadata["destination"] == destination
    assert (destination["entity_type"], destination["entity_id"], destination["sub_account"]) == ("asset", manual.fact["id"], "other_receivable")
    assert await manual.db[EVENTS].count_documents({"kind": "driver_payment_review"}) == 1

@pytest.mark.asyncio
async def test_manual_rejection_has_no_financial_effect(manual):
    assignment = await delivered(manual)
    before = await balances(manual)
    response = await post(manual, assignment, {"decision":"rejected", "note":"Receipt not accepted"})
    assert response.status_code == 200, response.text
    assert await balances(manual) == before
    event = await manual.db[EVENTS].find_one({"kind":"driver_payment_review"})
    assert event["txn_group_id"] is None

@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing_receipt", "corrupt_receipt", "foreign_receipt", "wrong_amount", "missing_amount", "foreign_identity", "inactive_identity", "undocumented_identity", "wrong_type", "wrong_currency", "forged_identity", "missing_source", "opening_missing", "paused"])
async def test_manual_invalid_evidence_identity_and_gate_leave_no_changes(manual, case):
    assignment = await delivered(manual)
    payload = approval(manual)
    if case == "missing_receipt":
        await manual.db.store_delivery_receipts.delete_many({})
    elif case == "corrupt_receipt":
        await manual.db.store_delivery_receipts.update_one({}, {"$set":{"content":b"changed"}})
    elif case == "foreign_receipt":
        await manual.db.store_delivery_receipts.update_one({}, {"$set":{"user_id":"foreign"}})
    elif case == "wrong_amount": payload["pos_reviewed_amount"] = "499.99"
    elif case == "missing_amount": payload.pop("pos_reviewed_amount")
    elif case == "missing_source":
        await manual.db.mz2_opening_facts_v2.update_one({"id":manual.fact["id"]}, {"$unset":{"source":""}})
    elif case == "opening_missing":
        await manual.db.settings.update_one({"user_id":OWNER}, {"$pull":{"mezan2_financial_cutover.opening_balance_zero_accounts":{"entity_id":manual.fact["id"]}}})
    elif case == "paused":
        await manual.db.mz2_atomic_owners.update_one({"_id":OWNER}, {"$set":{"writes_paused":True}})
    else:
        changes = {"foreign_identity":{"user_id":"foreign"}, "inactive_identity":{"status":"inactive"}, "undocumented_identity":{"evidence":""}, "wrong_type":{"category":"other_payable"}, "wrong_currency":{"currency":"USD"}, "forged_identity":{"reference":"changed-ref"}}[case]
        await manual.db.mz2_opening_facts_v2.update_one({"id":manual.fact["id"]}, {"$set":changes})
    before = await snapshot(manual.db)
    response = await post(manual, assignment, payload)
    assert response.status_code == (423 if case == "paused" else 409), response.text
    assert await snapshot(manual.db) == before

@pytest.mark.asyncio
async def test_manual_concurrent_review_has_one_journal(manual):
    assignment = await delivered(manual)
    responses = await asyncio.gather(*(post(manual, assignment, approval(manual)) for _ in range(3)))
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    assert sum(r.json()["state"] == "posted" for r in responses) == 1
    assert await manual.db[EVENTS].count_documents({"kind":"driver_payment_review"}) == 1
    assert await balances(manual) == {"driver":Decimal(0), "pos":Decimal(500), "bank":Decimal(1000)}

@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate", ["reference", "image", "image_new_reference", "reference_new_identity"])
async def test_manual_receipt_or_reference_cannot_discharge_second_order(manual, duplicate):
    first, second = await delivered(manual), await delivered(manual, "2")
    payload = approval(manual, settlement_reference="manual-ref-1") if duplicate != "image" else approval(manual)
    assert (await post(manual, first, payload)).status_code == 200
    if duplicate not in {"reference", "reference_new_identity"}:
        original = await manual.db.store_delivery_receipts.find_one({"assignment_id":first})
        await manual.db.store_delivery_receipts.update_one({"assignment_id":second}, {"$set":{"content":original["content"], "sha256":original["sha256"]}})
    if duplicate == "image_new_reference": payload["settlement_reference"] = "different-ref"
    if duplicate == "reference_new_identity": payload["destination_financial_id"] = manual.alternative["id"]
    before = await snapshot(manual.db)
    response = await post(manual, second, payload)
    assert response.status_code == 409, response.text
    assert await snapshot(manual.db) == before
    assert await balances(manual) == {"driver":Decimal(500), "pos":Decimal(500), "bank":Decimal(1000)}

@pytest.mark.asyncio
async def test_manual_approval_failure_rolls_back_journal_review_and_reservations(manual, monkeypatch):
    assignment = await delivered(manual)
    before = await snapshot(manual.db)
    original = module._post
    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic after-journal failure")
    monkeypatch.setattr(module, "_post", fail)
    with pytest.raises(RuntimeError, match="after-journal"):
        await post(manual, assignment, approval(manual))
    assert await snapshot(manual.db) == before
    monkeypatch.setattr(module, "_post", original)
    assert (await post(manual, assignment, approval(manual))).status_code == 200

async def bank_post(ctx, assignment, key, movement_id):
    return await ctx.http.post('/store-delivery/payment-review/'+assignment+'/pos-bank-settlement', json={"request_id":key, "movement_id":movement_id, "note":"Actual later bank arrival"})

@pytest.mark.asyncio
async def test_manual_pos_later_bank_partial_full_replay_and_movement_reuse(manual):
    assignment = await delivered(manual)
    assert (await post(manual, assignment, approval(manual))).status_code == 200
    await movement(manual.db, "pos-partial", "200.00", "in")
    first = await bank_post(manual, assignment, "partial-key-001", "pos-partial")
    assert first.status_code == 200, first.text
    assert await balances(manual) == {"driver":Decimal(0), "pos":Decimal(300), "bank":Decimal(1200)}
    replay = await bank_post(manual, assignment, "partial-key-001", "pos-partial")
    assert replay.status_code == 200 and replay.json()["txn_group_id"] == first.json()["txn_group_id"]
    reused = await bank_post(manual, assignment, "different-key-001", "pos-partial")
    assert reused.status_code == 409, reused.text
    await movement(manual.db, "pos-over", "301.00", "in")
    assert (await bank_post(manual, assignment, "over-key-001", "pos-over")).status_code == 409
    await movement(manual.db, "pos-full", "300.00", "in")
    final = await bank_post(manual, assignment, "full-key-001", "pos-full")
    assert final.status_code == 200, final.text
    assert await balances(manual) == {"driver":Decimal(0), "pos":Decimal(0), "bank":Decimal(1500)}
    assert first.json()["txn_group_id"] != final.json()["txn_group_id"]
    assert await manual.db[EVENTS].count_documents({"kind":"pos_bank_settlement"}) == 2

@pytest.mark.asyncio
async def test_manual_bank_failure_rolls_back_and_owner_pause_denies(manual, monkeypatch):
    assignment = await delivered(manual)
    assert (await post(manual, assignment, approval(manual))).status_code == 200
    await movement(manual.db, "pos-later", "500.00", "in")
    before = await snapshot(manual.db)
    original = module._post
    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic bank after-journal failure")
    monkeypatch.setattr(module, "_post", fail)
    with pytest.raises(RuntimeError, match="after-journal"):
        await bank_post(manual, assignment, "bank-failure-key", "pos-later")
    assert await snapshot(manual.db) == before
    monkeypatch.setattr(module, "_post", original)
    await manual.db.mz2_atomic_owners.update_one({"_id":OWNER}, {"$set":{"writes_paused":True}})
    response = await bank_post(manual, assignment, "bank-failure-key", "pos-later")
    assert response.status_code == 423, response.text
