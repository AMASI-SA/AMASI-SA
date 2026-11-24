"""Request-local read evidence must not alter source admission or ownership."""
import asyncio
from copy import deepcopy
import json

from fastapi import HTTPException
import pytest

import operational_demand_refresh as demand
from operational_balance_store import STATES, mutate
from operational_source_diagnostics import source_failure_detail
from test_operational_balance_demand_refresh import DB, Request, allowed
from test_operational_read_runtime import MotorSourceDB, ThreadedMotorCursor


class SourceDB(DB):
    def __init__(self, cursor):
        super().__init__()
        self.source = MotorSourceDB(cursor)

    def __getitem__(self, name):
        return self.source[name] if name == 'unified_orders' else super().__getitem__(name)


async def source_refresh(database, captured, *, observed=True, timeout=15):
    from operational_balance_sources import refresh_source_rows
    from operational_read_runtime import load_collections, SourceReadError

    async def operation():
        captured.append(demand.demand_context())
        try:
            return await load_collections(database, 'owner', ['unified_orders'],
                reader=refresh_source_rows, per_read_timeout=timeout,
                observe_primary_orders=observed)
        except SourceReadError as error:
            raise HTTPException(409, source_failure_detail(error.code, error.collection)) from None

    return await demand.run_visible_refresh(Request(), database, 'owner', 'staff', allowed, operation)


def sample_observation():
    return {
        'collection': 'unified_orders', 'outcome': 'failed', 'cursor_kind': 'motor',
        'queue_ms': 1.25, 'budget_ms': 15000, 'first_row_ms': None,
        'rows_retained': 0, 'last_checkpoint_rows': 0, 'last_checkpoint_ms': None,
        'read_elapsed_ms': 17000, 'cancel_join_ms': 1990, 'close_ms': 1,
    }


def test_snapshot_failure_reports_existing_read_unpack_and_copy_boundaries():
    async def scenario():
        database = DB()
        saved = deepcopy(database.data)

        async def reject_after_load(state):
            raise HTTPException(409, source_failure_detail(
                'operational_source_read_timeout', 'unified_orders'))

        with pytest.raises(HTTPException) as raised:
            await demand.run_visible_refresh(Request(), database, 'owner', 'staff', allowed,
                lambda: mutate(database, 'owner', reject_after_load))

        assert raised.value.status_code == 409
        assert raised.value.detail['cause'] == 'operational_source_read_timeout'
        timings = raised.value.detail['stage_timings']
        names = [row['stage'] for row in timings]
        assert names[:4] == [
            'snapshot_raw_read', 'snapshot_unpack', 'snapshot_copy_prepare', 'snapshot_load']
        assert all(row['outcome'] == 'complete' and row['duration_ms'] >= 0 for row in timings)
        assert database.data[STATES] == saved[STATES]
        assert not database.data[demand.LEASES]
        assert not any(operation in {'insert', 'replace'} for _, operation, _ in database.writes)
        assert database.writes[-1][1] == 'release'

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_observed_primary_read_preserves_query_full_evidence_and_owned_operations():
    rows = [{'user_id': 'owner', 'order_id': 'SYNTHETIC-ORDER',
             'raw_by_source': {'salla': {'private_payload': 'SYNTHETIC-PRIVATE'}},
             'salla_paid_sar_evidence': {'contradictory_shape': ['retained', {'all': 'fields'}]}}]
    executions = []
    for observed in (False, True):
        cursor = ThreadedMotorCursor(deepcopy([rows]), block=False)
        database, captured = SourceDB(cursor), []
        result = await source_refresh(database, captured, observed=observed)
        assert result == {'unified_orders': rows}
        assert cursor.close_calls == 1 and not cursor.closed_during_read
        assert len(cursor.operations) == 1 and cursor.consumed == 1
        assert all(operation.done() for operation in cursor.operations)
        assert not database.data[demand.LEASES]
        executions.append((database.source.queries,
                           [(name, operation) for name, operation, _ in database.writes],
                           list(captured[0].primary_orders_reads)))
    assert executions[0][:2] == executions[1][:2]
    query, projection = executions[1][0][0]
    assert query == {'user_id': 'owner'}
    assert projection['raw_by_source'] == 1 and projection['salla_paid_sar_evidence'] == 1
    assert not any('.' in field for field in projection)
    assert executions[0][2] == []
    observed = executions[1][2]
    assert len(observed) == 1
    assert observed[0]['cursor_kind'] == 'motor' and observed[0]['outcome'] == 'complete'
    assert observed[0]['rows_retained'] == 1 and observed[0]['first_row_ms'] >= 0
    assert observed[0]['cancel_join_ms'] == 0
    assert 'SYNTHETIC' not in json.dumps(observed)


@pytest.mark.asyncio
@pytest.mark.parametrize('timeout_kind', ['source', 'request'])
async def test_first_row_timeout_reports_only_after_owned_driver_and_close_finish(monkeypatch, timeout_kind):
    if timeout_kind == 'request':
        monkeypatch.setattr(demand, 'REQUEST_SECONDS', .01)
    cursor = ThreadedMotorCursor([[{'user_id': 'owner'}], [{'user_id': 'owner'}]], block_close=True)
    database, captured = SourceDB(cursor), []
    saved = deepcopy(database.data[STATES])
    task = asyncio.create_task(source_refresh(database, captured, timeout=.01 if timeout_kind == 'source' else 15))
    try:
        await asyncio.wait_for(cursor.entered.wait(), 1)
        await asyncio.sleep(.03)
        assert not task.done() and cursor.close_calls == 0
        assert len(cursor.operations) == 1
        cursor.release.set()
        await asyncio.wait_for(cursor.close_entered.wait(), 1)
        assert not task.done() and cursor.close_calls == 1
        assert not cursor.closed_during_read
        cursor.close_release.set()
        with pytest.raises(HTTPException) as raised:
            await asyncio.wait_for(task, 1)
        if timeout_kind == 'source':
            assert raised.value.status_code == 409
            assert raised.value.detail['cause'] == 'operational_source_read_timeout'
            assert raised.value.detail['source_collection'] == 'unified_orders'
        else:
            assert raised.value.status_code == 408
            assert raised.value.detail['code'] == 'operational_refresh_timeout'
        record, = raised.value.detail['primary_orders_reads']
        assert record['outcome'] == ('failed' if timeout_kind == 'source' else 'cancelled')
        assert record['cursor_kind'] == 'motor'
        assert record['rows_retained'] == 0 and record['first_row_ms'] is None
        assert 0 < record['budget_ms'] <= 10
        assert record['read_elapsed_ms'] >= record['cancel_join_ms'] > 0
        assert record['close_ms'] >= 0
        assert database.data[STATES] == saved and not database.data[demand.LEASES]
        assert all(operation.done() for operation in cursor.operations)
        assert len(cursor.operations) == 1
    finally:
        cursor.release.set()
        cursor.close_release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_partial_iteration_retains_progress_without_starting_another_batch():
    class SecondBatchCursor(ThreadedMotorCursor):
        def __init__(self):
            super().__init__([[{'user_id': 'owner'} for _ in range(256)],
                              [{'user_id': 'owner'}], [{'user_id': 'owner'}]],
                             block=False, block_close=True)
            self.second_started = asyncio.Event()

        def _refresh(self):
            if self.index == 1:
                self.release.clear()
                self.second_started.set()
            return super()._refresh()

    cursor = SecondBatchCursor()
    database, captured = SourceDB(cursor), []
    task = asyncio.create_task(source_refresh(database, captured))
    try:
        await asyncio.wait_for(cursor.second_started.wait(), 1)
        task.cancel()
        for _ in range(12):
            await asyncio.sleep(0)
        assert not task.done() and cursor.close_calls == 0
        cursor.release.set()
        await asyncio.wait_for(cursor.close_entered.wait(), 1)
        assert not task.done() and not cursor.closed_during_read
        cursor.close_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        record, = captured[0].primary_orders_reads
        assert record['outcome'] == 'cancelled'
        assert record['rows_retained'] == record['last_checkpoint_rows'] == 256
        assert record['first_row_ms'] <= record['last_checkpoint_ms'] <= record['read_elapsed_ms']
        assert record['cancel_join_ms'] > 0
        assert cursor.consumed == 257  # The already-owned late row was discarded.
        assert len(cursor.operations) == 2 and cursor.close_calls == 1
        assert all(operation.done() for operation in cursor.operations)
        assert not database.data[demand.LEASES]
    finally:
        cursor.release.set()
        cursor.close_release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('violation', ['overflow', 'owner'])
async def test_observation_cannot_prune_overflow_or_foreign_evidence(violation):
    rows = ([{'user_id': 'owner'} for _ in range(20002)] if violation == 'overflow'
            else [{'user_id': 'SYNTHETIC-FOREIGN'}])
    cursor = ThreadedMotorCursor([rows], block=False)
    database, captured = SourceDB(cursor), []
    with pytest.raises(HTTPException) as raised:
        await source_refresh(database, captured)
    expected = 'operational_source_scope_too_large' if violation == 'overflow' else 'operational_source_owner_mismatch'
    assert raised.value.detail['cause'] == expected
    record, = raised.value.detail['primary_orders_reads']
    assert record['rows_retained'] == (20001 if violation == 'overflow' else 1)
    assert record['outcome'] == 'failed'
    stages = {row['stage']: row['outcome'] for row in raised.value.detail['stage_timings']}
    assert stages['orders_cursor_read'] == 'complete' and stages['orders_rows_validate'] == 'failed'
    assert cursor.close_calls == 1 and len(cursor.operations) == 1
    assert cursor.consumed == record['rows_retained']
    assert 'SYNTHETIC-FOREIGN' not in json.dumps(raised.value.detail)


def test_primary_read_records_are_fixed_schema_bounded_and_request_local():
    first = demand.DemandRefreshContext(Request(), DB(), 'PRIVATE-OWNER', 'PRIVATE-ACTOR', allowed)
    other = demand.DemandRefreshContext(Request(), DB(), 'OTHER-OWNER', 'OTHER-ACTOR', allowed)
    for index in range(20):
        first.record_primary_orders_read({**sample_observation(), 'queue_ms': index,
                                         'query': {'user_id': 'PRIVATE-OWNER'}, 'token': 'PRIVATE-TOKEN'})
    records = first.primary_read_details()['primary_orders_reads']
    assert len(records) == 12 and records[0]['queue_ms'] == 8 and records[-1]['queue_ms'] == 19
    assert all(set(record) == set(sample_observation()) for record in records)
    assert 'PRIVATE' not in json.dumps(records) and other.primary_read_details() == {}
    for key, invalid in [('budget_ms', float('nan')), ('read_elapsed_ms', float('inf')),
                         ('queue_ms', True), ('close_ms', -1), ('rows_retained', 20002),
                         ('rows_retained', '1'), ('collection', 'PRIVATE-COLLECTION'),
                         ('cursor_kind', 'PRIVATE'), ('outcome', 'PRIVATE')]:
        first.record_primary_orders_read({**sample_observation(), key: invalid})
    assert first.primary_read_details()['primary_orders_reads'] == records


@pytest.mark.asyncio
async def test_diagnostic_failure_does_not_replace_canonical_source_exception(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError('SYNTHETIC-DIAGNOSTIC-FAILURE')

    monkeypatch.setattr(demand.DemandRefreshContext, 'record_primary_orders_read', broken)
    monkeypatch.setattr(demand._LOG, 'info', broken)
    cursor = ThreadedMotorCursor([[{'user_id': 'SYNTHETIC-FOREIGN'}]], block=False)
    with pytest.raises(HTTPException) as raised:
        await source_refresh(SourceDB(cursor), [])
    assert raised.value.detail['cause'] == 'operational_source_owner_mismatch'
    assert 'SYNTHETIC' not in json.dumps(raised.value.detail)
    assert cursor.close_calls == 1 and not cursor.closed_during_read


@pytest.mark.asyncio
async def test_unrelated_http_failure_retains_its_exact_contract():
    async def fail():
        context = demand.demand_context()
        context.record_primary_orders_read(sample_observation())
        raise HTTPException(409, {'code': 'unrelated', 'message': 'unchanged'})

    with pytest.raises(HTTPException) as raised:
        await demand.run_visible_refresh(Request(), DB(), 'owner', 'staff', allowed, fail)
    assert raised.value.detail == {'code': 'unrelated', 'message': 'unchanged'}


@pytest.mark.asyncio
@pytest.mark.parametrize(('visible', 'collection', 'enabled'), [
    (True, 'unified_orders', False),
    (True, 'mezan_employees_v2', True),
    (False, 'unified_orders', True),
])
async def test_primary_observation_requires_visible_context_opt_in_and_orders(visible, collection, enabled):
    import operational_read_runtime as runtime
    context = demand.DemandRefreshContext(Request(), DB(), 'owner', 'staff', allowed)
    token = demand._CURRENT.set(context if visible else None)
    calls = []

    async def reader(database, owner, name):
        calls.append(name)
        return []

    try:
        assert await runtime.load_collections(object(), 'owner', [collection], reader=reader,
            observe_primary_orders=enabled) == {collection: []}
        assert calls == [collection] and list(context.primary_orders_reads) == []
        assert runtime._PRIMARY_ORDERS_READ.get() is None
    finally:
        demand._CURRENT.reset(token)
