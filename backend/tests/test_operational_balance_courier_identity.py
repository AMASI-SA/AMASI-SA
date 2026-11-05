"""Manual native carrier codes authorize only one immutable courier identity."""
import ast
import asyncio
from copy import deepcopy
from pathlib import Path
from typing import List, Optional

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from pydantic import BaseModel, Field, ValidationError, root_validator

from courier_cod_fee_rules import CourierCodFeeTier, validate_courier_cod_fee_tiers
from operational_balance_engine import reconcile
from operational_balance_sources import collect_sources, entities
from operational_balance_store import STATES, read
import operational_courier_tariffs as tariffs
from test_operational_balance_prepaid_shipping import COMPANY, CASH, START, NOW, fixture
from test_operational_balance_sources import HardBoundaryDB


async def prepared(name="TestCarrier QZ", code="carrier-qz", mode="prepaid"):
    company = {**COMPANY, "name": name, "salla_carrier_key": code, "cost_per_order": 17,
               "payment_mode": mode, "is_deferred": mode == "deferred"}
    db = AsyncMongoMockClient()["synthetic_manual_carrier_identity"]
    await db.settings.insert_one({"user_id": "owner", "shipping_companies": [company]})
    await db.mz2_financial_accounts.insert_many([deepcopy(CASH), {**CASH, "id": "other-cash"}])
    data, state = fixture(current="completed" if mode == "prepaid" else "delivered")
    data["settings"] = [{"user_id": "owner", "shipping_companies": [deepcopy(company)]}]
    carrier(data, name, code)
    return db, data, state


def carrier(data, name, code):
    source = data["unified_orders"][0]
    raw = source["raw_by_source"]["salla_direct"]
    source["salla_shipping_current"].update(company_name=name, company_code=code)
    raw["shipping"].update(company=name, company_code=code)
    raw["shipments"][0]["company"] = {"name": name, "code": code}


async def payload(db, **changes):
    item, = (await tariffs.catalog(db, "owner"))["items"]
    return {"party_id": item["id"], "source_hash": item.get("source_hash", "unavailable"),
            "effective_from": START, "confirmed": True, "reason": "Owner reviewed original Salla code",
            "request_id": "synthetic-identity-approval",
            **({"cash_account_id": CASH["id"]} if item["payment_mode"] == "prepaid" else {}), **changes}


async def approved(db, state):
    await tariffs.approve(db, "owner", "owner", await payload(db))
    return {**state, "courier_tariffs": (await read(db, "owner"))["courier_tariffs"]}


async def calculate(data, state):
    sources = await collect_sources(HardBoundaryDB(data), "owner", START, NOW, operational_state=state)
    return reconcile(state, sources, NOW)


@pytest.mark.parametrize("name,code", [("TestCarrier QZ", "carrier-qz"), ("DHL Express", "dhl")])
@pytest.mark.parametrize("mode", ["prepaid", "deferred"])
def test_manual_original_code_prices_an_arbitrary_company_without_company_specific_code(name, code, mode):
    async def scenario():
        db, data, state = await prepared(name, code, mode)
        assert (await calculate(data, {**state, "courier_tariffs": []}))["movements"] == []
        item, = (await tariffs.catalog(db, "owner"))["items"]
        assert "unavailable" not in item
        assert item["keys"] == [code] and item["salla_carrier_key"] == code
        assert item["identity_match"] == "exact"
        party, = await entities(db, "owner", "courier")
        assert party["id"] == item["id"]
        state = await approved(db, state)
        version, = state["courier_tariffs"]
        assert version["keys"] == [code] and version["identity_match"] == "exact"
        result = await calculate(data, state)
        charge = result["engine"]["obligations"]["shipping-prepaid:100" if mode == "prepaid" else "shipping:100:base_shipping"]
        assert charge["confirmed"] == "19.55" and charge["party_id"] == item["id"]
        if mode == "prepaid":
            movement, = result["movements"]
            assert movement["amount"] == "19.55" and movement["bank_id"] == CASH["id"]
            assert movement["allocations"] == [{"obligation_id": "shipping-prepaid:100", "amount": "19.55"}]
        else:
            assert result["movements"] == []
        assert await calculate(data, result) == result
    asyncio.run(scenario())


@pytest.mark.parametrize("source_name,source_code", [("carrier-qz", None), ("TestCarrier QZ", "CARRIER-QZ"),
                                                       ("TestCarrier QZ", "different-code")])
def test_explicit_identity_never_falls_back_to_display_name_or_casefold_for_a_charge(source_name, source_code):
    async def scenario():
        db, data, state = await prepared()
        state = await approved(db, state)
        carrier(data, source_name, source_code)
        result = await calculate(data, state)
        assert result["movements"] == []
        assert "shipping-prepaid:100" not in result["engine"]["obligations"]
    asyncio.run(scenario())


@pytest.mark.parametrize("second_code", ["carrier-qz", "CARRIER-QZ"])
def test_duplicate_or_case_conflicting_settings_cannot_approve_a_charge(second_code):
    async def scenario():
        db, _, _ = await prepared()
        company = {**COMPANY, "name": "Another Courier", "salla_carrier_key": second_code}
        await db.settings.update_one({"user_id": "owner"}, {"$push": {"shipping_companies": company}})
        items = (await tariffs.catalog(db, "owner"))["items"]
        assert len(items) == 2 and all(item.get("unavailable") for item in items)
        for item in items:
            with pytest.raises(HTTPException):
                await tariffs.approve(db, "owner", "owner", {"party_id": item["id"],
                    "source_hash": item.get("source_hash"), "effective_from": START, "confirmed": True,
                    "reason": "Conflicting code", "request_id": "synthetic-conflict", "cash_account_id": CASH["id"]})
        assert await db[STATES].count_documents({}) == 0
    asyncio.run(scenario())


def test_deleted_company_does_not_release_its_frozen_code_for_another_party():
    async def scenario():
        db, _, state = await prepared()
        await approved(db, state)
        before = await read(db, "owner")
        await db.settings.update_one({"user_id": "owner"}, {"$set": {"shipping_companies": [
            {**COMPANY, "name": "Different Company", "salla_carrier_key": "carrier-qz"}]}})
        item, = (await tariffs.catalog(db, "owner"))["items"]
        assert item.get("unavailable")
        with pytest.raises(HTTPException):
            await tariffs.approve(db, "owner", "owner", await payload(db, request_id="synthetic-reassignment"))
        assert await read(db, "owner") == before
    asyncio.run(scenario())


def test_explicit_dhl_code_keeps_existing_catalog_party_opening_and_movement_unchanged():
    async def scenario():
        db, _, _ = await prepared("DHL Express", None)
        before_party, = await entities(db, "owner", "courier")
        assert before_party["id"] == "dhl"
        state = await read(db, "owner")
        state["revision"] = 1
        state["openings"] = {"opening-dhl": {"id": "opening-dhl", "party_type": "courier", "party_id": "dhl",
            "name": "DHL", "currency": "SAR", "direction": "for_them", "amount": "80.00"}}
        state["movements"] = [{"id": "existing-payment", "party_type": "courier", "party_id": "dhl",
            "direction": "outgoing", "amount": "20.00", "currency": "SAR", "bank_id": CASH["id"],
            "bank_kind": "cash", "allocations": [{"obligation_id": "opening-dhl", "amount": "20.00"}]}]
        await db[STATES].insert_one(deepcopy(state))
        await db.settings.update_one({"user_id": "owner"}, {"$set": {"shipping_companies.0.salla_carrier_key": "dhl"}})
        after_party, = await entities(db, "owner", "courier")
        assert after_party["id"] == before_party["id"]
        result = await tariffs.approve(db, "owner", "owner", await payload(db))
        after = await read(db, "owner")
        assert result["id"] == "dhl" and after["courier_tariffs"][0]["keys"] == ["dhl"]
        assert after["openings"] == state["openings"] and after["movements"] == state["movements"]
        assert after["engine"] == state["engine"]
    asyncio.run(scenario())


def test_changed_code_invalidates_preview_and_cannot_reassign_an_approved_company():
    async def scenario():
        db, data, state = await prepared()
        old_preview = await payload(db)
        await db.settings.update_one({"user_id": "owner"}, {"$set": {"shipping_companies.0.salla_carrier_key": "new-code"}})
        with pytest.raises(HTTPException):
            await tariffs.approve(db, "owner", "owner", old_preview)
        await db.settings.update_one({"user_id": "owner"}, {"$set": {"shipping_companies.0.salla_carrier_key": "carrier-qz"}})
        state = await approved(db, state)
        paid = await calculate(data, state)
        before = await read(db, "owner")
        await db.settings.update_one({"user_id": "owner"}, {"$set": {"shipping_companies.0.salla_carrier_key": "new-code"}})
        item, = (await tariffs.catalog(db, "owner"))["items"]
        assert item.get("unavailable")
        with pytest.raises(HTTPException):
            await tariffs.approve(db, "owner", "owner", await payload(db, request_id="synthetic-code-change"))
        assert await read(db, "owner") == before
        carrier(data, "TestCarrier QZ", "new-code")
        replayed = await calculate(data, paid)
        assert replayed["movements"] == paid["movements"]
        assert replayed["engine"]["facts"]["prepaid_shipping:100"] == paid["engine"]["facts"]["prepaid_shipping:100"]
        assert replayed["engine"]["obligations"]["shipping-prepaid:100"] == paid["engine"]["obligations"]["shipping-prepaid:100"]
    asyncio.run(scenario())


def test_overlapping_approved_identities_fail_closed_before_a_new_cash_debit():
    async def scenario():
        db, data, state = await prepared()
        state = await approved(db, state)
        first, = state["courier_tariffs"]
        state["courier_tariffs"].append({**deepcopy(first), "id": "another-party", "name": "Different Company",
            "settings_id": "another-party", "version_id": "conflicting-version"})
        setup, conflicts = tariffs.with_tariffs({}, "owner", state)
        assert set(conflicts) == {first["id"], "another-party"}
        assert setup["contracts"] == [] and setup["couriers"] == []
        result = await calculate(data, state)
        assert result["movements"] == [] and "shipping-prepaid:100" not in result["engine"]["obligations"]
    asyncio.run(scenario())


@pytest.mark.parametrize("ambiguous", [False, True])
def test_explicit_code_reuses_only_one_exact_native_definition(ambiguous):
    async def scenario():
        db, _, _ = await prepared()
        native = {"courier_key": "native-courier", "name": "TestCarrier QZ", "salla_carrier_keys": ["carrier-qz"],
                  "status": "active", "confirmed_by": "owner", "confirmed_at": START}
        await db.mz2_shipping_setup_v2.insert_one({"_id": "owner", "user_id": "owner", "couriers": [native,
            *([{**native, "courier_key": "other-native", "salla_carrier_keys": ["CARRIER-QZ"]}] if ambiguous else [])]})
        item, = (await tariffs.catalog(db, "owner"))["items"]
        if ambiguous:
            assert item.get("unavailable")
            with pytest.raises(HTTPException):
                await tariffs.approve(db, "owner", "owner", await payload(db))
        else:
            assert item["id"] == "native-courier" and item["native_mapping"] is True
            assert "unavailable" not in item
    asyncio.run(scenario())


def test_non_owner_cannot_confirm_an_explicit_binding():
    async def scenario():
        db, _, _ = await prepared()
        assert (await tariffs.catalog(db, "another-owner"))["items"] == []
        with pytest.raises(HTTPException) as error:
            await tariffs.approve(db, "owner", "employee", await payload(db))
        assert error.value.status_code == 403
        assert await db[STATES].count_documents({}) == 0
    asyncio.run(scenario())


def test_shipping_settings_model_round_trips_exact_code_and_rejects_malformed_identity():
    # Compile the actual model in isolation: importing the application server
    # would initialize unrelated integrations and database clients.
    tree = ast.parse((Path(__file__).parents[1] / "server.py").read_text())
    model = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ShippingCompany")
    namespace = {"BaseModel": BaseModel, "Field": Field, "Optional": Optional, "List": List,
                 "root_validator": root_validator, "CourierCodFeeTier": CourierCodFeeTier,
                 "validate_courier_cod_fee_tiers": validate_courier_cod_fee_tiers, "__name__": __name__}
    exec(compile(ast.Module(body=[model], type_ignores=[]), "server.py", "exec"), namespace)
    schema = namespace["ShippingCompany"]
    assert schema(**COMPANY, salla_carrier_key="Carrier-QZ").model_dump()["salla_carrier_key"] == "Carrier-QZ"
    assert schema(**COMPANY).salla_carrier_key is None
    for bad in ("", " carrier-qz", "carrier-qz ", "carrier\nqz", "x" * 201):
        with pytest.raises(ValidationError):
            schema(**COMPANY, salla_carrier_key=bad)
