"""Courier bank identities follow the active phase without creating accounts."""
from copy import deepcopy

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from operational_balance_store import STATES, initial
from store_delivery_payment_review_routes import make_store_delivery_payment_review_router
from store_delivery_bank_identity import collection_bank_accounts, find_collection_bank, require_collection_bank_snapshot
from fastapi import HTTPException

OWNER = "synthetic-bank-owner"
START = "2026-10-31T21:30:01.284707+00:00"


async def seed_operational(db):
    actor = {"id": OWNER, "role": "owner", "is_active": True}
    await db.users.insert_one(deepcopy(actor))
    state = initial(OWNER)
    state.update(status="active", started_at=START, revision=1)
    await db[STATES].insert_one(state)
    await db.mz2_atomic_owners.insert_one({"_id": OWNER, "writes_paused": True, "revision": 0,
                                          "ledger_backend_state": "legacy_active"})
    await db.settings.insert_one({"user_id": OWNER, "mezan2_financial_cutover": {"status": "not_configured"}})
    await db.mz2_financial_accounts.insert_one({"user_id": OWNER, "id": "cash-only", "account_type": "cash",
                                              "name": "Synthetic cash", "currency": "SAR", "status": "active"})
    for number in range(1, 4):
        await db.accounts.insert_one({"user_id": OWNER, "id": f"legacy-{number}", "account_type": "bank",
                                      "name": f"Synthetic bank {number}", "currency": "SAR", "status": "active"})
    await db.mz2_operational_bank_accounts.insert_one({"user_id": OWNER, "id": "operational-1", "account_type": "bank",
        "name": "Synthetic operational bank", "currency": "SAR", "status": "active"})
    return actor


@pytest.mark.asyncio
async def test_accountant_picker_in_operational_phase_uses_existing_namespaced_banks_without_writes():
    db = AsyncMongoMockClient()["synthetic_bank_catalog"]
    actor = await seed_operational(db)
    names = await db.list_collection_names()
    before = {name: await db[name].find({}).to_list(None) for name in names}
    app = FastAPI()
    app.include_router(make_store_delivery_payment_review_router(db, lambda: actor))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/store-delivery/payment-review/bank-accounts")
    assert response.status_code == 200, response.text
    body = response.json()
    assert {item["id"] for item in body["items"]} == {
        "account:legacy-1", "account:legacy-2", "account:legacy-3", "operational-bank:operational-1"}
    assert body["financial_source"] == "operational"
    assert body["source"] == "operational_bank_catalog"
    assert body["started_at"] == START
    assert {name: await db[name].find({}).to_list(None) for name in names} == before


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["native", "unavailable"])
async def test_canonical_picker_stays_available_for_pending_evidence_outside_operational_phase(phase):
    db = AsyncMongoMockClient()["synthetic_canonical_compatibility"]
    await seed_operational(db)
    await db[STATES].update_one({"_id": OWNER}, {"$set": {"status": "draft"}})
    if phase == "native":
        await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"ledger_backend_state": "v2_active"}})
    await db.mz2_financial_accounts.insert_one({"user_id": OWNER, "id": "canonical-bank", "account_type": "bank",
        "name": "Canonical display name", "currency": "SAR", "status": "active"})
    result = await collection_bank_accounts(db, OWNER)
    assert result["financial_source"] == phase and result["source"] == "mz2_financial_accounts"
    assert [row["id"] for row in result["items"]] == ["canonical-bank"]
    assert result["items"][0]["bank_identity_source"] == "mz2_financial_accounts"
    assert result["items"][0]["bank_identity_version"] == 1
    assert await find_collection_bank(db, OWNER, "account:legacy-1") is None
    assert (await db.mz2_atomic_owners.find_one({"_id": OWNER}))["writes_paused"] is True


@pytest.mark.asyncio
async def test_operational_catalog_filters_currency_owner_flags_and_preserves_full_identity():
    db = AsyncMongoMockClient()["synthetic_scoped_catalog"]
    await seed_operational(db)
    for index, changes in enumerate(({"currency": "USD"}, {"user_id": "foreign"}, {"status": "inactive"},
                                     {"archived": True}, {"is_active": False}, {"account_type": "cash"},
                                     {"currency": None})):
        await db.accounts.insert_one({"user_id": OWNER, "id": "unselectable-" + str(index),
            "name": "Synthetic excluded bank", "account_type": "bank", "status": "active", "currency": "SAR", **changes})
    choices = await collection_bank_accounts(db, OWNER)
    assert choices["total"] == 4
    assert await find_collection_bank(db, OWNER, "legacy-1") is None
    selected = await find_collection_bank(db, OWNER, "account:legacy-1")
    assert selected["bank_identity_source"] == "accounts"
    assert not set(selected) & {"balance", "opening_balance", "current_balance", "iban", "account_number"}


@pytest.mark.asyncio
async def test_new_delivery_claim_detects_phase_change_before_external_submission():
    db = AsyncMongoMockClient()["synthetic_phase_change"]
    await seed_operational(db)
    selected = await find_collection_bank(db, OWNER, "account:legacy-1")
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"ledger_backend_state": "v2_active"}})
    with pytest.raises(HTTPException) as error:
        await require_collection_bank_snapshot(db, OWNER, selected)
    assert error.value.status_code == 409 and error.value.detail["code"] == "driver_payment_bank_context_changed"
