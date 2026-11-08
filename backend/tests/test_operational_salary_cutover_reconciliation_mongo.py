"""Local-only CAS and HTTP acceptance for reviewed cutoff rounding."""
import asyncio
from copy import deepcopy
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException
import httpx
import pytest

from test_operational_balance_integration import run
from test_operational_balance_salary_start import seed_state
from operational_balance_routes import make_operational_balance_router
from operational_balance_engine import reconcile
from operational_balance_store import STATES, read, mutate
from operational_salary_start import _sources, preview, approve

STAMP = '2026-11-08T12:00:00+03:00'


async def setup(db):
    await seed_state(db)
    await db.mezan_employee_salary_contracts_v2.update_one({'id': 'contract'}, {'$set': {'monthly_amount': '1000'}})
    source = await _sources(db, 'owner')
    value = reconcile(await read(db, 'owner'), source, STAMP)
    value['engine']['as_of'] = STAMP
    value['movements'] = [{'id': 'already-paid', 'currency': 'SAR', 'amount': '33.33',
        'allocations': [{'obligation_id': 'salary:employee:2026-11-02', 'amount': '33.33'}]}]
    await db[STATES].replace_one({'_id': 'owner'}, value)
    check = await preview(db, 'owner', 'owner', '2026-11-01', clock=STAMP)
    payload = {'request_id': 'rounding-reviewed', 'first_day': '2026-11-01',
        'expected_preview_hash': check['preview_hash'], 'expected_session_scope': check['session_scope'],
        'opening_salaries_excluded': True, 'salary_rounding_reviewed': True}
    return source, payload


def test_real_mongo_review_and_worker_concurrency_are_idempotent_without_bank_or_paid_day_changes():
    async def scenario(db):
        source, payload = await setup(db)
        before = await read(db, 'owner')
        first, second = await asyncio.gather(*[approve(db, 'owner', 'owner', payload, clock=STAMP) for _ in range(2)])
        assert first == second and first['bank_money_changed'] is False
        approved = await read(db, 'owner')
        assert approved['engine'] == before['engine']
        assert len(approved['audit']) == 1
        async def calculate(state):
            updated = reconcile(state, source, STAMP)
            state.clear(); state.update(updated)
        await asyncio.gather(*[mutate(db, 'owner', calculate) for _ in range(2)])
        after = await read(db, 'owner')
        assert after['movements'] == before['movements']
        assert after['openings'] == before['openings']
        assert len(after['engine']['salary_days']) == 8
        assert len([v for v in after['engine']['obligations'].values() if v['kind'] == 'salary_cutover_adjustment']) == 5
        for key, day in before['engine']['salary_days'].items():
            assert after['engine']['salary_days'][key] == day
            assert after['engine']['obligations'][key] == before['engine']['obligations'][key]
        assert await db.general_ledger.count_documents({}) == 0
        assert await db.mz2_ledger_entries.count_documents({}) == 0
        from operational_profit_period import collect
        from operational_open_cost_exposure import READS
        empty_orders = {name: [] for name in READS}
        profit = await collect(db, 'owner', after, [], empty_orders,
                               datetime.fromisoformat(after['started_at']), datetime.fromisoformat(STAMP))
        assert profit['by_currency']['SAR']['salaries']['complete']
        assert profit['by_currency']['SAR']['salaries']['amount'] == '266.67'
        damaged = deepcopy(after)
        adjustment = next(k for k, v in damaged['engine']['obligations'].items() if v['kind'] == 'salary_cutover_adjustment')
        damaged['engine']['obligations'].pop(adjustment)
        incomplete = await collect(db, 'owner', damaged, [], empty_orders,
                                   datetime.fromisoformat(after['started_at']), datetime.fromisoformat(STAMP))
        assert not incomplete['by_currency']['SAR']['salaries']['complete']
        assert incomplete['by_currency']['SAR']['salaries']['amount'] is None
    run(scenario)


@pytest.mark.parametrize('change', ['payment', 'salary', 'opening', 'source', 'confirmation'])
def test_stale_or_unreviewed_rounding_never_partially_writes(change):
    async def scenario(db):
        _, payload = await setup(db)
        if change == 'payment':
            await db[STATES].update_one({'_id': 'owner'}, {'$set': {'movements.0.amount': '33.34'}})
        if change == 'salary':
            await db.mezan_employee_salary_contracts_v2.update_one({'id': 'contract'}, {'$set': {'monthly_amount': '1000.01'}})
        if change == 'opening':
            await db[STATES].update_one({'_id': 'owner'}, {'$set': {'openings.opening.amount': '601'}})
        if change == 'source':
            await db.mezan_employees_v2.insert_one({'id': 'missing-contract', 'user_id': 'owner', 'status': 'active'})
        if change == 'confirmation':
            payload.pop('salary_rounding_reviewed')
        before = await read(db, 'owner')
        with pytest.raises(HTTPException):
            await approve(db, 'owner', 'owner', payload, clock=STAMP)
        assert await read(db, 'owner') == before
    run(scenario)


def test_real_http_two_confirmations_owner_only_and_strict_boolean(monkeypatch):
    import operational_balance_store as store
    monkeypatch.setattr(store, 'now', lambda: STAMP)
    async def scenario(db):
        _, payload = await setup(db)
        app = FastAPI()
        async def principal(x_test_user: str = Header(default='owner'), x_test_mobile: str = Header(default='no')):
            return {'id': x_test_user, '_session_client': 'amasi_mobile' if x_test_mobile == 'yes' else 'web'}
        app.include_router(make_operational_balance_router(db, principal), prefix='/api')
        route = '/api/operational-balances/salary-start/approve'
        before = await read(db, 'owner')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated') as client:
            for headers in ({'x-test-user': 'staff'}, {'x-test-mobile': 'yes'}):
                assert (await client.post(route, json=payload, headers=headers)).status_code == 403
            for field in ('opening_salaries_excluded', 'salary_rounding_reviewed'):
                assert (await client.post(route, json={**payload, field: 'true'})).status_code == 422
                assert (await client.post(route, json={**payload, field: False})).status_code >= 400
            assert await read(db, 'owner') == before
            response = await client.post(route, json=payload)
            assert response.status_code == 200, response.text
            assert (await client.post(route, json=payload)).json() == response.json()
    run(scenario)
