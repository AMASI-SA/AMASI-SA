"""Read-only owner payroll context for the UI; synthetic data only."""
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException

from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES, digest
from operational_salary_start import read_context, approve, POLICY_KEY
from test_operational_balance_salary_start import run_mock, payload_for, NOW, START, policy, state


def test_context_is_read_only_and_does_not_load_employee_money(monkeypatch):
    import operational_salary_start as service
    async def forbidden(*args, **kwargs):
        raise AssertionError('Context must not project or approve salaries')
    monkeypatch.setattr(service, '_sources', forbidden)
    async def scenario(db):
        before = deepcopy(await db[STATES].find_one({'_id': 'owner'}))
        data = await read_context(db, 'owner', 'owner', clock=NOW)
        assert data == {'source': 'operational_salary_cutover', 'read_only': True,
                        'started_at': START, 'first_day': '2026-11-01',
                        'effective_first_day': '2026-11-02', 'timezone': 'Asia/Riyadh',
                        'approved': False, 'approved_at': None, 'session_scope': digest(['owner', 'owner']),
                        'as_of': NOW}
        assert await db[STATES].find_one({'_id': 'owner'}) == before
    run_mock(scenario)


@pytest.mark.parametrize('started,first', [
    ('2026-11-15T23:59:59+03:00', '2026-11-15'),
    ('2026-11-14T21:00:00Z', '2026-11-15'),
    ('2026-11-30T21:00:00Z', '2026-12-01')])
def test_server_context_uses_exact_riyadh_cutover_not_month_first(started, first):
    async def scenario(db):
        await db[STATES].update_one({'_id': 'owner'}, {'$set': {'started_at': started}})
        data = await read_context(db, 'owner', 'owner', clock=started)
        assert data['first_day'] == first and data['approved'] is False
    run_mock(scenario)


def test_context_after_successful_approval_resolves_an_unknown_http_outcome_without_reapproval():
    async def scenario(db):
        await approve(db, 'owner', 'owner', await payload_for(db), clock=NOW)
        before = deepcopy(await db[STATES].find_one({'_id': 'owner'}))
        for _ in range(2):
            data = await read_context(db, 'owner', 'owner', clock=NOW)
            assert data['approved'] is True and data['effective_first_day'] == data['first_day']
        assert await db[STATES].find_one({'_id': 'owner'}) == before
        assert len(before['audit']) == 1
    run_mock(scenario)


@pytest.mark.parametrize('change', ['staff', 'revoked', 'frozen', 'native', 'invalid_policy', 'future', 'invalid_time'])
def test_unavailable_or_conflicting_context_is_refused_without_mutations(change):
    async def scenario(db):
        actor = 'staff' if change == 'staff' else 'owner'
        if change == 'revoked': await db.users.update_one({'id': 'owner'}, {'$set': {'disabled': True}})
        if change == 'frozen': await db[STATES].update_one({'_id': 'owner'}, {'$set': {'status': 'frozen'}})
        if change == 'native': await db.settings.insert_one({'user_id': 'owner', 'mezan2_financial_cutover': {'status': 'active'}})
        if change == 'invalid_policy':
            bad = policy(state())[POLICY_KEY]; bad['first_day'] = '2026-10-01'
            await db[STATES].update_one({'_id': 'owner'}, {'$set': {POLICY_KEY: bad}})
        if change in {'future', 'invalid_time'}:
            await db[STATES].update_one({'_id': 'owner'}, {'$set': {'started_at': '2026-12-01T00:00:00Z' if change == 'future' else 'bad'}})
        before = deepcopy(await db[STATES].find_one({'_id': 'owner'}))
        with pytest.raises(HTTPException): await read_context(db, 'owner', actor, clock=NOW)
        assert await db[STATES].find_one({'_id': 'owner'}) == before
    run_mock(scenario)


def test_http_context_owner_only_and_outer_context_advertises_no_staff_action(monkeypatch):
    import operational_balance_store as store
    monkeypatch.setattr(store, 'now', lambda: NOW)
    async def scenario(db):
        app = FastAPI()
        async def principal(x_test_user: str = Header(default='owner'), x_test_mobile: str = Header(default='no')):
            return {'id': x_test_user, '_session_client': 'amasi_mobile' if x_test_mobile == 'yes' else 'web'}
        app.include_router(make_operational_balance_router(db, principal), prefix='/api')
        before = deepcopy(await db[STATES].find_one({'_id': 'owner'}))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated') as client:
            for headers in ({'x-test-user': 'staff'}, {'x-test-mobile': 'yes'}):
                response = await client.get('/api/operational-balances/salary-start/context', headers=headers)
                assert response.status_code == 403
            response = await client.get('/api/operational-balances/salary-start/context')
            assert response.status_code == 200 and response.json()['first_day'] == '2026-11-01'
            for actor in ('owner', 'staff'):
                response = await client.get('/api/operational-balances/context', headers={'x-test-user': actor})
                assert response.status_code == 200
                assert response.json()['can_manage_salary_start'] is (actor == 'owner')
        assert await db[STATES].find_one({'_id': 'owner'}) == before
    run_mock(scenario)
