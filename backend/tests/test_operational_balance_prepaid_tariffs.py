"""Owner-approved prepaid tariffs bind an exact cashbox without moving money."""
import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from mongomock_motor import AsyncMongoMockClient

import operational_courier_tariffs as tariffs
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES, initial, read

START = "2026-10-05T00:00:00+00:00"
NEXT = "2026-10-06T00:00:00+00:00"
NOW = "2026-11-05T12:00:00+00:00"
API = "/operational-balances/courier-tariffs"
COMPANY = {"name": "Aramex", "payment_mode": "prepaid", "is_deferred": False,
           "cost_per_order": 30, "vat_percent": 15, "cod_fee_percent": 0,
           "cod_fee_fixed_per_order": 0, "cod_fee_vat_percent": 15}
CASH = {"user_id": "owner", "id": "cash-one", "name": "محفظة سله",
        "account_type": "cash", "status": "active", "currency": "SAR"}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(tariffs, "now", lambda: NOW)


async def seed(*, company=None):
    db = AsyncMongoMockClient()["synthetic_prepaid_tariffs"]
    await db.settings.insert_one({"user_id": "owner", "shipping_companies": [deepcopy(company or COMPANY)]})
    await db.mz2_financial_accounts.insert_many([
        deepcopy(CASH), {**CASH, "id": "cash-two"},
        {**CASH, "id": "foreign", "user_id": "other"},
        {**CASH, "id": "bank", "account_type": "bank"},
        {**CASH, "id": "usd", "currency": "USD"},
        {**CASH, "id": "inactive", "status": "inactive"},
        {**CASH, "id": "unready", "ready": False},
        {**CASH, "id": "unnamed", "name": ""},
    ])
    return db


async def request(db, **changes):
    preview = await tariffs.catalog(db, "owner")
    item, = preview["items"]
    return {"party_id": item["id"], "source_hash": item["source_hash"], "effective_from": START,
            "reason": "Owner reviewed the prepaid cashbox", "confirmed": True,
            "request_id": "synthetic-approval", "cash_account_id": "cash-one", **changes}


def test_catalog_lists_only_exact_ready_owner_cash_ids_without_choosing_by_name():
    async def scenario():
        db = await seed()
        preview = await tariffs.catalog(db, "owner")
        assert preview["cash_accounts"] == [
            {"id": identity, "name": CASH["name"], "kind": "cash", "currency": "SAR", "ready": True}
            for identity in ("cash-one", "cash-two")]
        candidate, = preview["items"]
        assert candidate["payment_mode"] == "prepaid"
        assert candidate["cash_account_id"] is None
        assert candidate["current_from"] is None and candidate["same_terms"] is False
        assert await db[STATES].count_documents({}) == 0
    asyncio.run(scenario())


def test_saved_prepaid_boolean_remains_eligible_when_payment_mode_is_read_time_metadata():
    async def scenario():
        company = {key: value for key, value in COMPANY.items() if key != "payment_mode"}
        db = await seed(company=company)
        candidate, = (await tariffs.catalog(db, "owner"))["items"]
        assert candidate["payment_mode"] == "prepaid" and candidate["cash_account_id"] is None
        await tariffs.approve(db, "owner", "owner", await request(db))
        assert (await read(db, "owner"))["courier_tariffs"][0]["payment_mode"] == "prepaid"
    asyncio.run(scenario())


@pytest.mark.parametrize("is_deferred, mode", [(False, "deferred"), (True, "prepaid")])
def test_conflicting_saved_payment_modes_do_not_authorize_a_contract(is_deferred, mode):
    async def scenario():
        db = await seed(company={**COMPANY, "is_deferred": is_deferred, "payment_mode": mode})
        assert (await tariffs.catalog(db, "owner"))["items"] == []
        assert await db[STATES].count_documents({}) == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("cash_id", [None, "foreign", "bank", "usd", "inactive", "unready", "محفظة سله"])
def test_prepaid_approval_requires_a_selected_native_owner_cashbox(cash_id):
    async def scenario():
        db = await seed()
        with pytest.raises(HTTPException):
            await tariffs.approve(db, "owner", "owner", await request(db, cash_account_id=cash_id))
        assert await db[STATES].count_documents({}) == 0
    asyncio.run(scenario())


def test_binding_change_creates_a_later_immutable_version_and_replays_without_money():
    async def scenario():
        db = await seed()
        payload = await request(db)
        first = await tariffs.approve(db, "owner", "owner", payload)
        state = await read(db, "owner")
        original = deepcopy(state["courier_tariffs"][0])
        assert await tariffs.approve(db, "owner", "owner", payload) == first
        preview = await tariffs.catalog(db, "owner")
        assert preview["items"][0]["same_terms"] is True
        assert preview["items"][0]["cash_account_id"] == "cash-one"
        with pytest.raises(HTTPException) as duplicate:
            await tariffs.approve(db, "owner", "owner", await request(db, request_id="same-binding", effective_from=NEXT))
        assert duplicate.value.detail["code"] == "courier_tariff_period_conflict"
        with pytest.raises(HTTPException) as overlap:
            await tariffs.approve(db, "owner", "owner", await request(db, request_id="same-start", cash_account_id="cash-two"))
        assert overlap.value.detail["code"] == "courier_tariff_period_conflict"
        second = await tariffs.approve(db, "owner", "owner", await request(db,
            request_id="later-binding", cash_account_id="cash-two", effective_from=NEXT))
        state = await read(db, "owner")
        assert state["courier_tariffs"][0] == original
        assert state["courier_tariffs"][1]["cash_account"]["id"] == "cash-two"
        assert second["version_id"] != first["version_id"]
        assert state["movements"] == [] and state["openings"] == {} and state["engine"] == {}
        assert len(state["audit"]) == 2
        projected, conflicts = tariffs.with_tariffs({}, "owner", state)
        assert conflicts == []
        assert [(r["context"], r["effective_from"], r["effective_to"], r["cash_account"]["id"])
                for r in projected["contracts"]] == [
                    ("order_completed", START, NEXT, "cash-one"),
                    ("order_completed", NEXT, None, "cash-two")]
        with pytest.raises(HTTPException) as reused:
            await tariffs.approve(db, "owner", "owner", {**payload, "cash_account_id": "cash-two"})
        assert reused.value.detail["code"] == "operational_request_conflict"
        assert await tariffs.approve(db, "owner", "owner", payload) == first
    asyncio.run(scenario())


def test_prepaid_version_identity_binds_the_selected_cashbox():
    async def scenario():
        first_db, second_db = await seed(), await seed()
        first_payload, second_payload = await request(first_db), await request(second_db, cash_account_id="cash-two")
        assert first_payload["source_hash"] == second_payload["source_hash"]
        first = await tariffs.approve(first_db, "owner", "owner", first_payload)
        second = await tariffs.approve(second_db, "owner", "owner", second_payload)
        assert first["version_id"] != second["version_id"]
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["price", "cash_status", "cash_name"])
def test_source_changes_invalidate_the_preview_without_saving_a_version(change):
    async def scenario():
        db = await seed()
        payload = await request(db)
        if change == "price":
            await db.settings.update_one({"user_id": "owner"}, {"$set": {"shipping_companies.0.cost_per_order": 40}})
        else:
            field, value = ("status", "inactive") if change == "cash_status" else ("name", "Renamed cashbox")
            await db.mz2_financial_accounts.update_one({"user_id": "owner", "id": "cash-one"}, {"$set": {field: value}})
        with pytest.raises(HTTPException) as stale:
            await tariffs.approve(db, "owner", "owner", payload)
        assert stale.value.detail["code"] == "courier_tariff_preview_stale"
        assert await db[STATES].count_documents({}) == 0
    asyncio.run(scenario())


def test_retry_revalidates_the_cashbox_catalog_inside_the_mutation(monkeypatch):
    async def scenario():
        db = await seed()
        payload = await request(db)
        async def retry(database, owner, operation):
            await operation(initial(owner))
            await database.mz2_financial_accounts.update_one({"id": "cash-one"}, {"$set": {"status": "inactive"}})
            return await operation(initial(owner))
        monkeypatch.setattr(tariffs, "mutate", retry)
        with pytest.raises(HTTPException) as stale:
            await tariffs.approve(db, "owner", "owner", payload)
        assert stale.value.detail["code"] == "courier_tariff_preview_stale"
        assert await db[STATES].count_documents({}) == 0
    asyncio.run(scenario())


def test_owner_http_contract_requires_cash_for_prepaid_and_rejects_unknown_fields():
    async def scenario():
        db = await seed()
        await db.users.insert_many([{"id": "owner", "role": "owner"},
                                    {"id": "employee", "role": "employee", "created_by": "owner"}])
        principal = {"id": "employee"}
        app = FastAPI()
        app.include_router(make_operational_balance_router(db, lambda: principal))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(API)).status_code == 403
            payload = await request(db)
            assert (await client.post(API, json=payload)).status_code == 403
            principal["id"] = "owner"
            without_cash = {k: v for k, v in payload.items() if k != "cash_account_id"}
            missing = await client.post(API, json=without_cash)
            assert missing.status_code == 409
            assert missing.json()["detail"]["code"] == "courier_tariff_cash_account_required"
            assert (await client.post(API, json={**payload, "cash_account_name": CASH["name"]})).status_code == 422
            assert (await client.post(API, json=payload)).status_code == 200
    asyncio.run(scenario())


def test_deferred_versions_keep_delivery_recognition_and_reject_a_cash_binding():
    async def scenario():
        db = await seed(company={**COMPANY, "is_deferred": True, "payment_mode": "deferred"})
        with pytest.raises(HTTPException) as invalid:
            await tariffs.approve(db, "owner", "owner", await request(db))
        assert invalid.value.detail["code"] == "courier_tariff_cash_account_invalid"
        await tariffs.approve(db, "owner", "owner", await request(db, cash_account_id=None))
        state = await read(db, "owner")
        assert "cash_account" not in state["courier_tariffs"][0]
        projected, conflicts = tariffs.with_tariffs({}, "owner", state)
        assert conflicts == [] and projected["contracts"][0]["context"] == "delivery"
        assert (await tariffs.catalog(db, "owner"))["items"][0]["same_terms"] is True
        assert state["movements"] == []
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [{"cash_account": None}, {"cash_account": {"id": "bank", "kind": "bank", "currency": "SAR", "name": "Bank"}},
                                    {"payment_mode": "unknown"}])
def test_malformed_saved_prepaid_contract_is_unavailable_instead_of_becoming_delivery(change):
    async def scenario():
        db = await seed()
        await tariffs.approve(db, "owner", "owner", await request(db))
        state = await read(db, "owner")
        state["courier_tariffs"][0].update(change)
        projected, conflicts = tariffs.with_tariffs({}, "owner", state)
        assert conflicts == ["aramex"]
        assert projected["contracts"] == []
    asyncio.run(scenario())
