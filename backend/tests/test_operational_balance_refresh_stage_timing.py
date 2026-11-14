"""Visible refresh diagnostics expose timings, never financial identities."""
import asyncio
from copy import deepcopy
import json
import logging
import time

from fastapi import APIRouter, FastAPI, HTTPException
import httpx
import pytest

from operational_demand_refresh import (
    LEASES, REQUEST_SECONDS, TIMING_LIMIT, DemandRefreshContext, demand_context,
    refresh_stage, run_visible_refresh,
)
from test_operational_balance_demand_refresh import DB, Request, allowed
from test_operational_balance_demand_refresh_routes import post
from operational_source_diagnostics import source_failure_detail


def timing_messages(caplog):
    return [record.getMessage() for record in caplog.records
            if record.name == 'operational_demand_refresh'
            and record.getMessage().startswith('operational_refresh_stage ')]


def test_actual_visible_route_records_completed_read_and_commit_stages(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='operational_demand_refresh')
    database = DB()
    response, _ = asyncio.run(post(database, monkeypatch))
    assert response.status_code == 200
    messages = timing_messages(caplog)
    for stage in ('native_evidence', 'snapshot_load', 'snapshot_compare',
                  'snapshot_pack', 'snapshot_commit', 'aggregate'):
        rows = [message for message in messages if f'stage={stage} ' in message]
        assert len(rows) == 1
        assert rows[0].endswith('outcome=complete')
        assert float(rows[0].split('duration_ms=')[1].split()[0]) >= 0
    assert database.data[LEASES] == {}


def test_source_failure_http_response_contains_the_unwound_request_stages(monkeypatch, caplog):
    from operational_balance_store import STATES
    from operational_demand_refresh_routes import attach_demand_refresh_routes
    from operational_read_runtime import SourceReadError
    from test_operational_balance_source_diagnostics import setup_failing_refresh
    import operational_balance_sources

    caplog.set_level(logging.INFO, logger='operational_demand_refresh')
    database = setup_failing_refresh(
        monkeypatch, SourceReadError('operational_source_read_timeout', 'unified_orders'))
    original_state = deepcopy(database.data[STATES])
    assert DemandRefreshContext(Request(), database, 'owner', 'staff', allowed).read_deadline is None

    async def fail_read(*args, **kwargs):
        deadline = demand_context().read_deadline
        assert type(deadline) is float and 0 < deadline - time.monotonic() <= REQUEST_SECONDS
        with refresh_stage('source_primary_reads'):
            raise SourceReadError('operational_source_read_timeout', 'unified_orders')

    monkeypatch.setattr(operational_balance_sources, 'collect_sources', fail_read)

    async def principal():
        return {'id': 'actor-private-fixture'}

    async def scope(db, user, permission):
        assert db is database and permission == 'reports'
        return user, 'owner', 'mezan2'

    async def scenario():
        router, app = APIRouter(prefix='/operational-balances'), FastAPI()
        attach_demand_refresh_routes(router, database, principal, scope)
        app.include_router(router, prefix='/api')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://isolated') as client:
            response = await client.post('/api/operational-balances/refresh', json={'visible': True})
        assert response.status_code == 409
        detail = response.json()['detail']
        expected = source_failure_detail('operational_source_read_timeout', 'unified_orders')
        assert {key: value for key, value in detail.items() if key != 'stage_timings'} == expected
        timings = detail['stage_timings']
        assert [(row['stage'], row['outcome']) for row in timings[-3:]] == [
            ('source_primary_reads', 'failed'), ('source_collection', 'failed'), ('aggregate', 'failed')]
        assert any(row['stage'] == 'native_evidence' and row['outcome'] == 'complete' for row in timings)
        assert any(row['stage'] == 'snapshot_load' and row['outcome'] == 'complete' for row in timings)
        assert all(set(row) == {'stage', 'duration_ms', 'outcome'}
                   and 0 <= row['duration_ms'] <= 300000 for row in timings)
        assert database.data[STATES] == original_state and database.data[LEASES] == {}
        assert all(row[0] == LEASES for row in database.writes)
        assert demand_context() is None
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for private in ('read_deadline', 'actor-private-fixture', '9182.37', '123.45', 'private-audit-fixture',
                        'private-movement-fixture'):
            assert private not in json.dumps(detail) and private not in caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize('payload,principal', [({'visible': False}, None),
                                               ({'visible': True}, {})])
def test_hidden_or_unauthorized_request_emits_no_refresh_stage(monkeypatch, caplog,
                                                            payload, principal):
    caplog.set_level(logging.INFO, logger='operational_demand_refresh')
    database = DB()
    response, _ = asyncio.run(post(database, monkeypatch, payload=payload, principal=principal))
    assert response.status_code in {401, 422}
    assert timing_messages(caplog) == []
    assert database.writes == []


def test_deadline_identifies_cancelled_stage_and_joins_before_return(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='operational_demand_refresh')
    monkeypatch.setattr('operational_demand_refresh.REQUEST_SECONDS', 0.02)

    async def scenario():
        database, request = DB(), Request()
        stopped = False

        async def operation():
            nonlocal stopped
            with refresh_stage('source_collection'):
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped = True

        with pytest.raises(HTTPException) as failure:
            await run_visible_refresh(request, database, 'owner', 'actor-private-fixture',
                                      allowed, operation)
        assert failure.value.status_code == 408
        timings = failure.value.detail['stage_timings']
        assert len(timings) == 1
        assert timings[0]['stage'] == 'source_collection'
        assert timings[0]['outcome'] == 'cancelled'
        assert 0 <= timings[0]['duration_ms'] <= 300000
        assert set(timings[0]) == {'stage', 'duration_ms', 'outcome'}
        assert stopped and database.data[LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(scenario())
    messages = timing_messages(caplog)
    assert len(messages) == 1
    assert 'stage=source_collection ' in messages[0]
    assert messages[0].endswith('outcome=cancelled')
    assert 'actor-private-fixture' not in messages[0] and 'owner' not in messages[0]


def test_failure_log_omits_exception_details_and_stays_request_local(caplog):
    caplog.set_level(logging.INFO, logger='operational_demand_refresh')
    private_details = 'synthetic order=secret-fixture amount=9182.37 token=never-log'

    async def scenario():
        database = DB()

        async def operation():
            with refresh_stage('profit_snapshot'):
                raise ValueError(private_details)

        with pytest.raises(ValueError, match='synthetic order'):
            await run_visible_refresh(Request(), database, 'owner', 'staff', allowed, operation)
        assert database.data[LEASES] == {}
        with refresh_stage('outside_request'):
            pass

    asyncio.run(scenario())
    messages = timing_messages(caplog)
    assert len(messages) == 1
    assert 'stage=profit_snapshot ' in messages[0]
    assert messages[0].endswith('outcome=failed')
    assert private_details not in caplog.text
    assert 'outside_request' not in caplog.text


@pytest.mark.parametrize('stage,duration,outcome', [
    ('owner-private-fixture', 1, 'complete'),
    ([], 1, 'complete'),
    ('source_orders', 1, 'private exception'),
    ('source_orders', 1, []),
    *[('source_orders', value, 'complete') for value in
      (None, True, '123', -1, float('nan'), float('inf'), 300001, 10 ** 1000)],
])
def test_timeout_diagnostics_reject_unrecognized_or_malformed_values(stage, duration, outcome):
    context = DemandRefreshContext(Request(), DB(), 'owner', 'staff', allowed)
    context.record_stage(stage, duration, outcome)
    assert list(context.stage_timings) == []


@pytest.mark.parametrize('failure_kind', ['deadline', 'source'])
def test_timeout_diagnostics_keep_bounded_tail_and_terminal_cancelled_stage(monkeypatch, failure_kind):
    monkeypatch.setattr('operational_demand_refresh.REQUEST_SECONDS', 0.02)

    async def scenario():
        database = DB()

        async def operation():
            with refresh_stage('aggregate'):
                for _ in range(TIMING_LIMIT + 10):
                    with refresh_stage('source_bank_catalog'):
                        pass
                # An unrecognized record is never put in the HTTP response.
                demand_context().record_stage('private-owner-fixture', 1, 'complete')
                with refresh_stage('source_orders'):
                    if failure_kind == 'source':
                        raise HTTPException(409, detail=source_failure_detail(
                            'operational_source_read_timeout', 'unified_orders'))
                    await asyncio.Event().wait()

        with pytest.raises(HTTPException) as failure:
            await run_visible_refresh(Request(), database, 'owner', 'staff', allowed, operation)
        timings = failure.value.detail['stage_timings']
        assert failure.value.status_code == (409 if failure_kind == 'source' else 408)
        assert len(timings) == TIMING_LIMIT
        outcome = 'failed' if failure_kind == 'source' else 'cancelled'
        assert [(r['stage'], r['outcome']) for r in timings[-2:]] == [
            ('source_orders', outcome), ('aggregate', outcome)]
        assert 'private' not in str(failure.value.detail)
        assert database.data[LEASES] == {} and demand_context() is None
        return database.writes

    writes = asyncio.run(scenario())
    assert [row[1] for row in writes] == ['acquire', 'release']


@pytest.mark.parametrize('failure_kind', ['deadline', 'source'])
def test_concurrent_timeout_diagnostics_are_request_local(monkeypatch, failure_kind):
    monkeypatch.setattr('operational_demand_refresh.REQUEST_SECONDS', 0.05)

    async def scenario():
        both_started, started = asyncio.Event(), []

        async def request(stage):
            database = DB()

            async def operation():
                with refresh_stage(stage):
                    started.append(stage)
                    if len(started) == 2:
                        both_started.set()
                    await both_started.wait()
                    if failure_kind == 'source':
                        raise HTTPException(409, detail=source_failure_detail(
                            'operational_source_read_timeout', 'unified_orders'))
                    await asyncio.Event().wait()

            with pytest.raises(HTTPException) as failure:
                await run_visible_refresh(Request(), database, 'owner', 'staff', allowed, operation)
            assert database.data[LEASES] == {} and demand_context() is None
            return failure.value.detail['stage_timings']

        first, second = await asyncio.gather(request('source_orders'), request('profit_snapshot'))
        assert [row['stage'] for row in first] == ['source_orders']
        assert [row['stage'] for row in second] == ['profit_snapshot']
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(scenario())


@pytest.mark.parametrize('status,changes', [
    (403, {}), (500, {}),
    (409, {'code': 'operational_refresh_in_progress'}),
    (409, {'message': 'private arbitrary exception'}),
    (409, {'owner_id': 'private-owner-fixture'}),
    (409, {'cause': ['private-exception']}),
])
def test_source_timings_do_not_extend_unrelated_or_malformed_http_errors(status, changes):
    async def scenario():
        database = DB()
        detail = {**source_failure_detail('operational_source_read_timeout', 'unified_orders'), **changes}
        original = deepcopy(detail)
        error = HTTPException(status, detail=detail, headers={'Retry-After': '3'})

        async def operation():
            with refresh_stage('source_primary_reads'):
                raise error

        with pytest.raises(HTTPException) as failure:
            await run_visible_refresh(Request(), database, 'owner', 'staff', allowed, operation)
        assert failure.value is error
        assert failure.value.status_code == status and failure.value.detail == original
        assert failure.value.headers == {'Retry-After': '3'}
        assert database.data[LEASES] == {} and demand_context() is None

    asyncio.run(scenario())


def test_stage_diagnostics_do_not_change_success_or_non_timeout_error():
    async def scenario():
        database = DB()

        async def success():
            with refresh_stage('profit_snapshot'):
                return {'saved': True}

        assert await run_visible_refresh(Request(), database, 'owner', 'staff', allowed, success) == {'saved': True}

        async def denied():
            with refresh_stage('source_orders'):
                raise HTTPException(status_code=403, detail={'code': 'revoked'})

        with pytest.raises(HTTPException) as failure:
            await run_visible_refresh(Request(), database, 'owner', 'staff', allowed, denied)
        assert failure.value.status_code == 403
        assert failure.value.detail == {'code': 'revoked'}
        assert database.data[LEASES] == {} and demand_context() is None

    asyncio.run(scenario())
