"""Courier remittances require bank arrival evidence at the canonical writer."""
import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from operational_balance_routes import make_operational_balance_router
from operational_balance_service import create_movement, refresh, report
from operational_balance_store import digest, mutate, read, remember
from test_operational_balance_cod_flow import native_order
from test_operational_balance_integration import NOW, movement, run, started


async def seed(db, method=None):
    await started(db)
    await native_order(db, method)
    return await refresh(db, "owner", clock=NOW)


def receipt(**updates):
    body = movement()
    body.update(party_type="courier", party_id="carrier", direction="incoming", kind="collection",
                amount="150", reference="TEST-COURIER-BANK-ARRIVAL", business_date=NOW[:10],
                bank_arrival_confirmed=True, allocations=[{"obligation_id": "cod:100", "amount": "150"}])
    body.update(updates)
    return body


@pytest.mark.parametrize("kind", ["collection", "settlement", "payment", "refund"])
@pytest.mark.parametrize("fault", ["missing_confirmation", "false", "string", "number", "date", "reference", "blank_reference"])
def test_unconfirmed_courier_bank_receipt_never_changes_money(kind, fault):
    async def scenario(db):
        before = await seed(db)
        payload = receipt(kind=kind)
        if fault == "missing_confirmation":
            payload.pop("bank_arrival_confirmed")
        elif fault in {"false", "string", "number"}:
            payload["bank_arrival_confirmed"] = {"false": False, "string": "true", "number": 1}[fault]
        elif fault == "date":
            payload.pop("business_date")
        else:
            payload["reference"] = " " if fault == "blank_reference" else ""
        for _ in range(2):
            with pytest.raises(HTTPException) as error:
                await create_movement(db, "owner", "owner", payload, clock=NOW)
            assert error.value.status_code == 422
            assert error.value.detail["code"] == "operational_courier_bank_confirmation_required"
            assert error.value.detail["not_applied"] is True
        after = await read(db, "owner")
        assert after["movements"] == before["movements"] == []
        assert after["openings"] == before["openings"]
        assert after["engine"]["facts"] == before["engine"]["facts"]
        assert report(after)["summary"]["actual_liquidity"] == "1000.00"
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize("business_date,code", [
    ("2026-10-04", "operational_business_date_invalid"),
    ("2026-10-08", "operational_future_business_date"),
    ("2026-10-32", "operational_business_date_invalid"),
])
def test_courier_bank_arrival_date_is_a_valid_date_within_the_operational_period(business_date, code):
    async def scenario(db):
        await seed(db)
        with pytest.raises(HTTPException) as error:
            await create_movement(db, "owner", "owner", receipt(business_date=business_date), clock=NOW)
        assert error.value.detail["code"] == code
        assert (await read(db, "owner"))["movements"] == []
    run(scenario)


def test_verified_courier_remittance_concurrent_replay_keeps_one_bank_effect_and_shipping_fee():
    async def scenario(db):
        before = await seed(db)
        payload = receipt()
        results = await asyncio.gather(*[
            create_movement(db, "owner", "owner", payload, clock=NOW) for _ in range(10)])
        assert all(value == results[0] for value in results)
        after = await refresh(db, "owner", clock=NOW)
        assert len(after["movements"]) == 1
        assert after["openings"] == before["openings"]
        assert after["engine"]["facts"] == before["engine"]["facts"]
        computed = report(after)
        assert computed["summary"]["actual_liquidity"] == "1150.00"
        assert computed["details"]["cod_reports"]["cod:100"]["outstanding"] == "50.00"
        assert [row for row in computed["obligations"] if row["kind"] == "shipping"] == [
            row for row in report(before)["obligations"] if row["kind"] == "shipping"]
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize("method,account", [(None, "cash"), ("cash", "bank"), ("cash", "cash")])
def test_courier_cash_and_internal_driver_remittances_keep_their_existing_contract(method, account):
    async def scenario(db):
        await seed(db, method)
        payload = receipt(bank_id=account, source_account_type=account)
        payload.pop("bank_arrival_confirmed")
        payload.pop("business_date")
        payload["reference"] = ""
        if method:
            payload.update(party_type="store_driver", party_id="driver")
        await create_movement(db, "owner", "owner", payload, clock=NOW)
        assert len((await read(db, "owner"))["movements"]) == 1
        assert report(await read(db, "owner"))["summary"]["actual_liquidity"] == "1150.00"
    run(scenario)


def test_courier_shipping_payment_does_not_require_incoming_bank_confirmation():
    async def scenario(db):
        before = await seed(db)
        shipping = next(row for row in before["engine"]["obligations"].values()
                        if row["kind"] == "shipping" and row.get("fee_component") == "base_shipping")
        payload = receipt(direction="outgoing", kind="payment", amount="10",
                          allocations=[{"obligation_id": shipping["id"], "amount": "10"}])
        payload.pop("bank_arrival_confirmed")
        await create_movement(db, "owner", "owner", payload, clock=NOW)
        assert report(await read(db, "owner"))["summary"]["actual_liquidity"] == "990.00"
    run(scenario)


def test_already_saved_legacy_bank_receipt_replays_its_original_result_without_a_new_requirement():
    async def scenario(db):
        await seed(db)
        payload = receipt()
        for key in ("bank_arrival_confirmed", "business_date"):
            payload.pop(key)
        payload["reference"] = ""
        # A persisted pre-gate receipt and its request hash are authoritative.
        saved = {**deepcopy(payload), "id": "legacy-courier-receipt", "actor_id": "owner",
                 "amount": "150.00", "bank_kind": "bank", "bank_name": "Bank",
                 "occurred_at": NOW, "source": "mezan2", "name": "Carrier"}

        async def historical(state):
            state["movements"].append(deepcopy(saved))
            remember(state, "movement:owner", payload, saved)
            return saved

        await mutate(db, "owner", historical)
        for _ in range(2):
            assert await create_movement(db, "owner", "owner", payload, clock=NOW) == saved
        state = await read(db, "owner")
        assert state["movements"] == [saved]
        assert report(state)["summary"]["actual_liquidity"] == "1150.00"
    run(scenario)


def test_workspace_http_requires_and_preserves_courier_bank_arrival_evidence():
    async def scenario(db):
        await seed(db)
        app = FastAPI()

        async def principal():
            return {"id": "owner", "_session_client": "web"}

        app.include_router(make_operational_balance_router(db, principal), prefix="/api")
        body = {"request_id": "courier-http-missing", "expected_session_scope": digest(["owner", "owner"]),
                "party_type": "courier", "party_id": "carrier", "side": "for_party", "reason": "collection",
                "amount": "150.00", "business_date": NOW[:10], "source_type": "bank", "source_id": "bank",
                "reference": "TEST-COURIER-HTTP-ARRIVAL"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
            response = await client.post("/api/operational-balances/workspace/entries", json=body)
            assert response.status_code == 422
            assert response.json()["detail"]["code"] == "operational_courier_bank_confirmation_required"
            body.update(request_id="courier-http-confirmed", bank_arrival_confirmed=True)
            first = await client.post("/api/operational-balances/workspace/entries", json=body)
            assert first.status_code == 200, first.text
            again = await client.post("/api/operational-balances/workspace/entries", json=body)
            assert again.json() == first.json()
        state = await read(db, "owner")
        assert len(state["movements"]) == 1
        assert state["movements"][0]["bank_arrival_confirmed"] is True
        assert state["movements"][0]["reference"] == "TEST-COURIER-HTTP-ARRIVAL"
        assert report(state)["summary"]["actual_liquidity"] == "1150.00"
    run(scenario)


@pytest.mark.parametrize("endpoint", ["/movements", "/workspace/entries"])
@pytest.mark.parametrize("confirmation", [1, "true", False])
def test_http_bank_arrival_requires_a_boolean_confirmation(endpoint, confirmation):
    async def scenario(db):
        await seed(db)
        app = FastAPI()

        async def principal():
            return {"id": "owner", "_session_client": "web"}

        app.include_router(make_operational_balance_router(db, principal), prefix="/api")
        body = (receipt() if endpoint == "/movements" else {
            "request_id": "courier-http-typed", "party_type": "courier", "party_id": "carrier",
            "side": "for_party", "reason": "collection", "amount": "150.00", "business_date": NOW[:10],
            "source_type": "bank", "source_id": "bank", "reference": "TEST-COURIER-TYPED-ARRIVAL"})
        body.update(expected_session_scope=digest(["owner", "owner"]), bank_arrival_confirmed=confirmation)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
            response = await client.post("/api/operational-balances" + endpoint, json=body)
            assert response.status_code == 422, response.text
        assert (await read(db, "owner"))["movements"] == []
    run(scenario)
