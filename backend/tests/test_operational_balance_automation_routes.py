"""The explicit owner capability cannot be enabled through GET or staff scope."""
import asyncio
from copy import deepcopy

from fastapi import APIRouter, FastAPI, HTTPException
import httpx
import pytest

from operational_balance_automation_routes import attach_automation_routes
from operational_balance_store import STATES, digest
from test_operational_balance_automation import SchedulerDB


PATH = '/api/operational-balances/automation'


async def request(db, *, method='GET', body=None, principal=None, revoke=False):
    calls = []
    async def current_user():
        if principal == {}:
            raise HTTPException(401)
        return principal if principal is not None else {'id': 'owner', 'role': 'owner'}
    async def scope(database, user, permission):
        calls.append(permission)
        return user, 'changed-owner' if revoke and len(calls) > 1 else 'owner', 'mezan2'
    app, router = FastAPI(), APIRouter(prefix='/operational-balances')
    attach_automation_routes(router, db, current_user, scope)
    app.include_router(router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated') as client:
        return await client.request(method, PATH, json=body), calls


def test_status_read_does_not_opt_in_reconcile_or_initialize_financial_data():
    db = SchedulerDB()
    before = deepcopy(db.data)
    response, calls = asyncio.run(request(db))
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['enabled'] is False
    assert response.json()['session_scope'] == digest(['owner', 'owner'])
    assert calls == ['reports', 'reports']
    assert db.data == before and db.writes == []


def test_explicit_enable_and_disable_only_changes_owner_metadata():
    from operational_balance_automation import SETTINGS
    db = SchedulerDB()
    before = deepcopy(db.data[STATES])
    for enabled in (True, False):
        response, _ = asyncio.run(request(db, method='POST', body={
            'enabled': enabled, 'expected_session_scope': digest(['owner', 'owner'])}))
        assert response.status_code == 200, response.text
        assert response.json()['enabled'] is enabled
        assert response.json()['financial_coverage_verified'] is False
    assert db.data[STATES] == before
    assert {name for name, _, _ in db.writes} == {SETTINGS}


@pytest.mark.parametrize('principal,expected', [({}, 401),
    ({'id': 'staff', 'role': 'employee'}, 403),
    ({'id': 'different-owner', 'role': 'owner'}, 403)])
@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_nonowner_cannot_read_or_enable(principal, expected, method):
    db = SchedulerDB()
    response, _ = asyncio.run(request(db, method=method, principal=principal, body={
        'enabled': True, 'expected_session_scope': digest(['owner', 'owner'])}))
    assert response.status_code == expected
    assert db.reads == [] and db.writes == []


@pytest.mark.parametrize('body', [
    {'enabled': True},
    {'enabled': 'true', 'expected_session_scope': digest(['owner', 'owner'])},
    {'enabled': 1, 'expected_session_scope': digest(['owner', 'owner'])},
    {'enabled': True, 'owner_id': 'other', 'expected_session_scope': digest(['owner', 'owner'])},
    {'enabled': True, 'interval_seconds': 1, 'expected_session_scope': digest(['owner', 'owner'])},
])
def test_capability_payload_is_strict_and_has_no_owner_or_interval_override(body):
    db = SchedulerDB()
    response, _ = asyncio.run(request(db, method='POST', body=body))
    assert response.status_code == 422
    assert db.writes == []


def test_wrong_session_scope_does_not_enable():
    db = SchedulerDB()
    response, _ = asyncio.run(request(db, method='POST', body={
        'enabled': True, 'expected_session_scope': digest(['other', 'other'])}))
    assert response.status_code == 403 and db.writes == []


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_account_change_during_metadata_operation_is_rejected(method):
    db = SchedulerDB()
    response, _ = asyncio.run(request(db, method=method, revoke=True, body={
        'enabled': True, 'expected_session_scope': digest(['owner', 'owner'])}))
    assert response.status_code == 403 and db.writes == []


def test_owner_trace_is_bounded_closed_vocabulary_and_drops_source_payloads():
    from operational_balance_automation import SETTINGS
    from test_operational_balance_read_diagnostics import sample_observation
    db = SchedulerDB()
    db.data[SETTINGS] = {'owner': {'_id': 'owner', 'owner_id': 'owner', 'last_trace': {
        'stage_timings': [{'stage': 'snapshot_unpack', 'outcome': 'complete', 'duration_ms': 100,
                          'private': 'PRIVATE-CUSTOMER'} for _ in range(100)],
        'primary_orders_reads': [{**sample_observation(), 'query': {'user_id': 'PRIVATE-OWNER'},
                                 'provider': 'PRIVATE-TOKEN'} for _ in range(20)],
        'private': 'PRIVATE-ORDER', 'execution': {'cpu_priority_lowered': True, 'private': 'PRIVATE-HOST'},
    }}}
    response, _ = asyncio.run(request(db))
    assert response.status_code == 200
    trace = response.json()['last_trace']
    assert len(trace['stage_timings']) == 64 and len(trace['primary_orders_reads']) == 12
    assert trace['execution'] == {'cpu_priority_lowered': True}
    assert 'PRIVATE' not in response.text and db.writes == []


def test_malformed_trace_values_never_escape_or_break_the_status_read():
    from operational_balance_automation import SETTINGS
    from test_operational_balance_read_diagnostics import sample_observation
    db = SchedulerDB()
    db.data[SETTINGS] = {'owner': {'_id': 'owner', 'owner_id': 'owner', 'last_trace': {
        'stage_timings': [{'stage': ['private'], 'outcome': 'complete', 'duration_ms': 10},
                         {'stage': 'snapshot_load', 'outcome': 'private', 'duration_ms': 1},
                         {'stage': 'snapshot_load', 'outcome': 'complete', 'duration_ms': 10 ** 1000}],
        'primary_orders_reads': [{**sample_observation(), 'queue_ms': 10 ** 1000},
                                {**sample_observation(), 'rows_retained': True}],
    }}}
    response, _ = asyncio.run(request(db))
    assert response.status_code == 200
    assert response.json()['last_trace'] == {'stage_timings': [], 'primary_orders_reads': []}
