"""Real isolated Mongo proof for stable intent ownership and definitive rejection."""
import asyncio
import httpx
from fastapi import FastAPI, Header
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import read, digest, OPERATION_CLAIMS
from operational_balance_service import save_opening, report
from test_operational_balance_integration import run, started, movement, opening, START


def client_for(db):
    app = FastAPI()
    async def principal(x_test_user: str = Header(default="staff")):
        return {"id": x_test_user}
    app.include_router(make_operational_balance_router(db, principal), prefix="/api")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated")


def test_owner_change_cannot_rebind_same_intent_even_with_new_scope_and_concurrency():
    async def scenario(db):
        await started(db)
        await db.mezan_suppliers_v2.insert_one({"id": "supplier", "user_id": "other", "name": "المورد", "status": "active"})
        await db.mz2_financial_accounts.insert_one({"id": "bank", "user_id": "other", "name": "بنك معزول", "account_type": "bank", "currency": "SAR", "status": "active"})
        await save_opening(db,"other","other",opening(),clock=START)
        await save_opening(db,"other","other",{"request_id":"start-other-01"},finish=True,clock=START)
        payload = {**movement(), "expected_session_scope": digest(["owner", "staff"])}
        async with client_for(db) as client:
            url = "/api/operational-balances/movements"
            first = await client.post(url,json=payload)
            assert first.status_code == 200, first.text
            await db.users.update_one({"id":"staff"},{"$set":{"created_by":"other"}})
            old = await client.post(url,json=payload)
            assert old.status_code == 409 and not old.json()["detail"].get("not_applied")
            rebound = {**payload,"expected_session_scope":digest(["other","staff"])}
            retried = await asyncio.gather(*[client.post(url,json=rebound) for _ in range(8)])
            assert all(r.status_code == 409 for r in retried)
            assert all(r.json()["detail"]["code"] == "operational_request_scope_conflict" for r in retried)
            await db.users.update_one({"id":"staff"},{"$set":{"created_by":"owner"}})
            same = await asyncio.gather(*[client.post(url,json=payload) for _ in range(8)])
            assert all(r.status_code == 200 and r.json() == first.json() for r in same)
        assert len((await read(db,"owner"))["movements"]) == 1
        assert len((await read(db,"other"))["movements"]) == 0
        assert await db[OPERATION_CLAIMS].count_documents({}) == 1
        assert report(await read(db,"owner"))["summary"]["actual_liquidity"] == "700.00"
        assert report(await read(db,"other"))["summary"]["actual_liquidity"] == "1000.00"
    run(scenario)


def test_api_scope_required_and_definitive_business_rejection_has_no_cash_effect():
    async def scenario(db):
        await started(db)
        async with client_for(db) as client:
            url = "/api/operational-balances/movements"
            assert (await client.post(url,json=movement())).status_code == 422
            payload = {**movement(),"expected_session_scope":digest(["owner","staff"]),"bank_id":"missing"}
            denied = await client.post(url,json=payload)
            assert denied.status_code == 409
            assert denied.json()["detail"]["not_applied"] is True
            assert (await read(db,"owner"))["movements"] == []
            corrected = {**payload,"request_id":"corrected-intent-01","bank_id":"bank"}
            assert (await client.post(url,json=corrected)).status_code == 200
            conflict = await client.post(url,json={**corrected,"amount":"100"})
            assert conflict.status_code == 409 and not conflict.json()["detail"].get("not_applied")
        assert len((await read(db,"owner"))["movements"]) == 1
    run(scenario)


def test_saved_pre_scope_wip_movement_cannot_be_replayed_as_new():
    from operational_balance_service import create_movement
    from operational_balance_store import STATES, request_key
    async def scenario(db):
        await started(db)
        payload = movement()
        first = await create_movement(db,"owner","staff",payload)
        state = await read(db,"owner")
        entry = state["requests"].pop(request_key("movement:staff", payload["request_id"]))
        state["requests"][request_key("movement",payload["request_id"])] = entry
        await db[STATES].replace_one({"_id":"owner"},state)
        await db[OPERATION_CLAIMS].delete_many({})
        assert await create_movement(db,"owner","staff",payload) == first
        from fastapi import HTTPException
        import pytest
        with pytest.raises(HTTPException) as caught:
            await create_movement(db,"other","staff",payload)
        assert caught.value.detail["code"] == "operational_request_scope_conflict"
        assert len((await read(db,"owner"))["movements"]) == 1
        assert (await read(db,"other"))["movements"] == []
    run(scenario)


def test_concurrent_stale_rejection_replays_committed_success(monkeypatch):
    import operational_balance_service as service
    async def scenario(db):
        await started(db)
        await db.mezan_employees_v2.insert_one({"id":"e","user_id":"owner","name":"موظف","status":"active"})
        funding={**movement(),"request_id":"fund-500","party_type":"employee_custody","party_id":"e","amount":"500"}
        await service.create_movement(db,"owner","owner",funding)
        expense={**movement(),"request_id":"spend-600","party_type":"operating_expense","party_id":"fuel","bank_id":"e","source_account_type":"employee_custody","amount":"600"}
        entered,release=asyncio.Event(),asyncio.Event()
        original=service.entity
        paused=False
        async def entity(*args,**kwargs):
            nonlocal paused
            if asyncio.current_task().get_name()=="stale-request" and not paused:
                paused=True;entered.set();await release.wait()
            return await original(*args,**kwargs)
        monkeypatch.setattr(service,"entity",entity)
        old=asyncio.create_task(service.create_movement(db,"owner","owner",expense),name="stale-request")
        await asyncio.wait_for(entered.wait(),3)
        await service.create_movement(db,"owner","owner",{**funding,"request_id":"fund-200","amount":"200"})
        winner=await service.create_movement(db,"owner","owner",expense)
        release.set()
        assert await old == winner
        assert len([m for m in (await read(db,"owner"))["movements"] if m["request_id"]=="spend-600"])==1
    run(scenario)


def test_terminal_rejection_remains_rejected_after_later_funding_and_concurrent_retry():
    from operational_balance_service import create_movement
    from fastapi import HTTPException
    import pytest
    async def scenario(db):
        await started(db)
        await db.mezan_employees_v2.insert_one({"id":"e","user_id":"owner","name":"موظف","status":"active"})
        funding={**movement(),"request_id":"fund-500","party_type":"employee_custody","party_id":"e","amount":"500"}
        await create_movement(db,"owner","owner",funding)
        expense={**movement(),"request_id":"spend-600","party_type":"operating_expense","party_id":"fuel","bank_id":"e","source_account_type":"employee_custody","amount":"600"}
        with pytest.raises(HTTPException) as caught:
            await create_movement(db,"owner","owner",expense)
        assert caught.value.detail["not_applied"] is True
        await create_movement(db,"owner","owner",{**funding,"request_id":"fund-200","amount":"200"})
        retries=await asyncio.gather(*[create_movement(db,"owner","owner",expense) for _ in range(8)],return_exceptions=True)
        assert all(isinstance(r,HTTPException) and r.detail.get("not_applied") for r in retries)
        assert not any(m["request_id"]=="spend-600" for m in (await read(db,"owner"))["movements"])
        corrected=await create_movement(db,"owner","owner",{**expense,"request_id":"new-confirmed-intent"})
        assert corrected["amount"]=="600.00"
    run(scenario)
