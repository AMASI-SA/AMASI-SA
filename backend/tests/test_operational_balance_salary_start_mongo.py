"""Real isolated Mongo CAS and HTTP payroll-policy acceptance; no production env."""
import asyncio
from copy import deepcopy

from fastapi import FastAPI, Header, HTTPException
import httpx
import pytest

from test_operational_balance_integration import run
from test_operational_balance_salary_start import seed_state, payload_for, START, NOW, employee, earned
from operational_balance_engine import reconcile
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES, read, mutate
from operational_salary_start import approve, preview, POLICY_KEY


def test_real_mongo_concurrent_identical_approval_and_refresh_preserve_money_once():
    async def scenario(db):
        await seed_state(db)
        payload = await payload_for(db)
        before = await read(db, 'owner')
        first, second = await asyncio.gather(*[approve(db, 'owner', 'owner', payload, clock=NOW) for _ in range(2)])
        assert first == second
        saved = await read(db, 'owner')
        assert len(saved['audit']) == 1 and saved['revision'] == before['revision'] + 1
        assert saved['openings'] == before['openings'] and saved['movements'] == before['movements']
        async def calculate(value):
            calculated = reconcile(value, {'employees': [employee()]}, NOW)
            value.clear(); value.update(calculated)
        await asyncio.gather(*[mutate(db, 'owner', calculate) for _ in range(2)])
        result = await read(db, 'owner')
        assert earned(result) == 100 and len(result['engine']['salary_days']) == 1
        assert await db.general_ledger.count_documents({}) == 0
        assert await db.mz2_ledger_entries.count_documents({}) == 0
        assert result['openings'] == before['openings'] and result['movements'] == before['movements']
    run(scenario)


def test_real_mongo_stale_source_rejects_without_partial_policy_or_audit():
    async def scenario(db):
        await seed_state(db)
        payload = await payload_for(db)
        before = await read(db, 'owner')
        await db.mezan_employee_salary_contracts_v2.update_one({'id': 'contract'}, {'$set': {'monthly_amount': '6000'}})
        with pytest.raises(HTTPException) as error:
            await approve(db, 'owner', 'owner', payload, clock=NOW)
        assert error.value.detail['code'] == 'operational_salary_preview_stale'
        assert await read(db, 'owner') == before
    run(scenario)


def test_real_mongo_role_revocation_during_source_read_is_checked_again(monkeypatch):
    import operational_salary_start as service
    original = service._sources
    async def scenario(db):
        await seed_state(db)
        payload = await payload_for(db)
        before = await read(db, 'owner')
        async def revoke(database, owner):
            result = await original(database, owner)
            await database.users.update_one({'id': owner}, {'$set': {'disabled': True}})
            return result
        monkeypatch.setattr(service, '_sources', revoke)
        with pytest.raises(HTTPException) as error:
            await approve(db, 'owner', 'owner', payload, clock=NOW)
        assert error.value.status_code == 403
        assert await read(db, 'owner') == before
    run(scenario)


def test_real_http_owner_only_preview_is_read_only_and_retries_do_not_accrue_twice(monkeypatch):
    import operational_balance_store as store
    monkeypatch.setattr(store, 'now', lambda: NOW)
    async def scenario(db):
        await seed_state(db)
        app = FastAPI()
        async def principal(x_test_user: str = Header(default='owner'), x_test_mobile: str = Header(default='no')):
            return {'id': x_test_user, '_session_client': 'amasi_mobile' if x_test_mobile == 'yes' else 'web'}
        app.include_router(make_operational_balance_router(db, principal), prefix='/api')
        base = '/api/operational-balances/salary-start'
        before = await read(db, 'owner')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated') as client:
            for headers in ({'x-test-user': 'staff'}, {'x-test-mobile': 'yes'}):
                assert (await client.post(base + '/preview', json={'first_day': '2026-11-01'}, headers=headers)).status_code == 403
            response = await client.post(base + '/preview', json={'first_day': '2026-11-01'})
            assert response.status_code == 200
            assert await read(db, 'owner') == before
            result = response.json()
            payload = {'request_id': 'http-payroll-confirm', 'first_day': '2026-11-01',
                       'expected_preview_hash': result['preview_hash'], 'expected_session_scope': result['session_scope'],
                       'opening_salaries_excluded': True}
            assert (await client.post(base + '/approve', json={**payload, 'opening_salaries_excluded': False})).status_code == 422
            assert (await client.post(base + '/approve', json={**payload, 'opening_salaries_excluded': 'true'})).status_code == 422
            assert (await client.post(base + '/approve', json=payload)).status_code == 200
            assert (await client.post(base + '/approve', json=payload)).status_code == 200
            saved = await read(db, 'owner')
            assert saved[POLICY_KEY]['first_day'] == '2026-11-01'
            assert len(saved['audit']) == 1
            assert saved['engine'] == before['engine'] and saved['movements'] == before['movements']
    run(scenario)
