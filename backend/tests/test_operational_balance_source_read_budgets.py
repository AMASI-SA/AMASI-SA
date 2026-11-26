"""Internal per-source budgets retain outer deadlines and owned cursor cleanup."""
import asyncio
from contextlib import nullcontext
import time
from types import MappingProxyType, SimpleNamespace

from motor.motor_asyncio import AsyncIOMotorClient
import pymongo
import pytest

import operational_demand_refresh as demand
import operational_read_runtime as runtime
from operational_balance_sources import refresh_source_rows
from operational_read_runtime import SourceReadError, load_collections, source_read_budget
from test_operational_read_runtime import MongoReadWire, MotorSourceDB, ThreadedMotorCursor


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', [None, {}, {'unified_orders': 20}])
async def test_internal_policy_changes_only_named_budget_and_retains_four_way_admission(policy):
    names = ['unified_orders', *(f'source_{i}' for i in range(11))]
    first_wave, release = asyncio.Event(), asyncio.Event()
    active, peak, budgets = set(), 0, {}

    async def reader(db, owner, name):
        nonlocal peak
        assert owner == 'owner' and runtime.source_read_is_owned()
        budgets[name] = pymongo._csot.remaining()
        active.add(name)
        peak = max(peak, len(active))
        if len(budgets) == 4:
            first_wave.set()
        try:
            await release.wait()
            return [name]
        finally:
            active.remove(name)

    task = asyncio.create_task(load_collections(object(), 'owner', names, reader=reader,
        concurrency=30, per_read_timeouts=policy))
    try:
        await asyncio.wait_for(first_wave.wait(), 2)
        assert len(budgets) == peak == 4
        release.set()
        assert await task == {name: [name] for name in names}
        assert not active and peak == 4
        for name, remaining in budgets.items():
            expected = (policy or {}).get(name, 15)
            assert expected - 1 < remaining <= expected
        assert pymongo._csot.remaining() is None and not runtime.source_read_is_owned()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [0, -1, -.1, True, False, float('nan'),
    float('inf'), -float('inf'), 10 ** 1000, '20', None, [], {}])
async def test_invalid_override_fails_before_admitting_any_reader(value):
    entered = []

    async def reader(*args):
        entered.append(args)
        raise AssertionError('invalid policy must never enter I/O')

    with pytest.raises(ValueError, match='^operational_source_read_budget_invalid$'):
        await load_collections(object(), 'owner', ['unified_orders'], reader=reader,
            per_read_timeouts={'unified_orders': value})
    assert entered == [] and pymongo._csot.remaining() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', [[], [('unified_orders', 20)], '20', 20,
    {'unknown': 20}, {'unified_orders': 20, 'unknown': 1}, {None: 20}])
async def test_non_mapping_or_out_of_cohort_override_fails_before_io(policy):
    entered = []

    async def reader(*args):
        entered.append(args)
        return []

    with pytest.raises(ValueError, match='^operational_source_read_budget_invalid$'):
        await load_collections(object(), 'owner', ['unified_orders'], reader=reader,
            per_read_timeouts=policy)
    assert entered == []


@pytest.mark.asyncio
@pytest.mark.parametrize(('policy', 'inherited', 'request_remaining', 'expected'), [
    (20, 60, 85, 20),
    (20, 3, 85, 3),
    (20, 60, 3, 3),  # Only three seconds remain in the unchanged visible budget.
    (20, 60, 18, 18),
    (2.5, 60, 85, 2.5),
])
async def test_named_budget_reaches_motor_worker_with_earliest_deadline(
        monkeypatch, policy, inherited, request_remaining, expected):
    assert demand.REQUEST_SECONDS == 85
    context = SimpleNamespace(read_deadline=time.monotonic() + request_remaining)
    monkeypatch.setattr(runtime, 'demand_context', lambda: context)
    cursor = ThreadedMotorCursor([[{'user_id': 'owner'}]], block=False)
    database = MotorSourceDB(cursor)
    with source_read_budget(inherited):
        result = await load_collections(database, 'owner', ['unified_orders'], reader=refresh_source_rows,
            per_read_timeouts=MappingProxyType({'unified_orders': policy}))
    assert result == {'unified_orders': [{'user_id': 'owner'}]}
    assert len(cursor.driver_budgets) == len(database.queries) == cursor.close_calls == 1
    assert expected - 1 < cursor.driver_budgets[0] <= expected
    assert not cursor.closed_during_read and all(operation.done() for operation in cursor.operations)
    assert pymongo._csot.remaining() is None and not runtime.source_read_is_owned()
    assert await asyncio.to_thread(pymongo._csot.remaining) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('deadline_kind', ['inherited', 'request'])
async def test_expired_outer_deadline_cannot_be_revived_by_twenty_second_policy(monkeypatch, deadline_kind):
    entered = []
    context = SimpleNamespace(read_deadline=time.monotonic() - 1 if deadline_kind == 'request' else None)
    monkeypatch.setattr(runtime, 'demand_context', lambda: context)

    async def reader(*args):
        entered.append(args)
        return []

    with source_read_budget(0 if deadline_kind == 'inherited' else 60):
        with pytest.raises(SourceReadError) as failure:
            await load_collections(object(), 'owner', ['unified_orders'], reader=reader,
                per_read_timeouts={'unified_orders': 20})
    assert (failure.value.code, failure.value.collection) == ('operational_source_read_timeout', 'unified_orders')
    assert entered == [] and pymongo._csot.remaining() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('outer_deadline', [False, True])
async def test_queue_uses_admission_time_policy_without_renewing_inherited_deadline(monkeypatch, outer_deadline):
    entered, release = asyncio.Event(), asyncio.Event()
    policy, budgets, admitted = {'unified_orders': 20}, {}, []
    context = SimpleNamespace(read_deadline=time.monotonic() + .5 if outer_deadline else None)
    monkeypatch.setattr(runtime, 'demand_context', lambda: context)

    async def reader(db, owner, name):
        admitted.append(name)
        budgets[name] = pymongo._csot.remaining()
        if name == 'first':
            entered.set()
            await release.wait()
        return []

    with source_read_budget(.5) if outer_deadline else nullcontext():
        task = asyncio.create_task(load_collections(object(), 'owner', ['first', 'unified_orders'],
            reader=reader, concurrency=1, per_read_timeouts=policy))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert admitted == ['first']
            # Caller mutation after validation cannot alter a queued source.
            policy['unified_orders'] = 1000
            await asyncio.sleep(.04)
            release.set()
            assert await task == {'first': [], 'unified_orders': []}
            if outer_deadline:
                assert 0 < budgets['unified_orders'] < budgets['first'] - .03
                assert budgets['first'] <= .5
            else:
                assert 14 < budgets['first'] <= 15
                assert 19 < budgets['unified_orders'] <= 20
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_policy_preserves_one_cumulative_driver_budget_across_batches_and_one_query():
    documents = [{'user_id': 'owner', 'order_number': str(index)} for index in range(3)]
    cursor = ThreadedMotorCursor([[document] for document in documents])
    database = MotorSourceDB(cursor)
    task = asyncio.create_task(load_collections(database, 'owner', ['unified_orders'],
        reader=refresh_source_rows, per_read_timeouts={'unified_orders': 20}))
    try:
        await asyncio.wait_for(cursor.entered.wait(), 1)
        await asyncio.sleep(.04)
        cursor.release.set()
        assert await task == {'unified_orders': documents}
        assert len(database.queries) == cursor.close_calls == 1
        assert len(cursor.driver_budgets) == 3
        assert 19 < cursor.driver_budgets[0] <= 20
        assert 0 < cursor.driver_budgets[-1] < cursor.driver_budgets[0] - .03
        assert not cursor.closed_during_read and all(operation.done() for operation in cursor.operations)
    finally:
        cursor.release.set()
        task.cancel()
        await asyncio.gather(task, *cursor.operations, return_exceptions=True)


@pytest.mark.asyncio
async def test_policy_retains_real_wire_csot_cutoff_and_joins_cursor_without_replay():
    wire = await MongoReadWire('getMore').start()
    client = AsyncIOMotorClient(f'mongodb://127.0.0.1:{wire.port}/?directConnection=true',
        connect=False, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)
    try:
        with source_read_budget(.3):
            with pytest.raises(SourceReadError) as failure:
                await load_collections(client['owned_read_wire'], 'owner', ['unified_orders'],
                    reader=refresh_source_rows, per_read_timeouts={'unified_orders': 20})
        assert (failure.value.code, failure.value.collection) == ('operational_source_read_timeout', 'unified_orders')
        await asyncio.wait_for(wire.read_disconnected.wait(), 1)
        finds = [command for command in wire.commands if 'find' in command]
        assert len(finds) == 1 and finds[0]['filter'] == {'user_id': 'owner'}
        assert 0 < finds[0]['maxTimeMS'] <= 300
        assert len([command for command in wire.commands if 'getMore' in command]) == 2
        assert pymongo._csot.remaining() is None and not runtime.source_read_is_owned()
    finally:
        await asyncio.wait_for(asyncio.to_thread(client.close), 3)
        await wire.close()
    assert not wire.errors and not wire.handlers and not wire.writers


@pytest.mark.asyncio
async def test_named_policy_keeps_snapshot_session_serial():
    database = SimpleNamespace(session=object())
    active, peak = 0, 0

    async def reader(db, owner, name):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            return []
        finally:
            active -= 1

    assert await load_collections(database, 'owner', ['unified_orders', 'other'], reader=reader,
        per_read_timeouts={'unified_orders': 20}) == {'unified_orders': [], 'other': []}
    assert active == 0 and peak == 1


@pytest.mark.asyncio
async def test_source_failure_returns_no_partial_result_and_joins_owned_mapped_cursor():
    cursor = ThreadedMotorCursor([[{'user_id': 'owner'}], [{'user_id': 'owner'}]])
    database, complete = MotorSourceDB(cursor), asyncio.Event()

    async def reader(db, owner, name):
        if name == 'unified_orders':
            return await refresh_source_rows(db, owner, name)
        if name == 'complete':
            complete.set()
            return ['finished but not a committed partial batch']
        await cursor.entered.wait()
        await complete.wait()
        raise ValueError('operational_source_owner_mismatch')

    task = asyncio.create_task(load_collections(database, 'owner', ['unified_orders', 'complete', 'invalid'],
        reader=reader, per_read_timeouts={'unified_orders': 20}))
    try:
        await asyncio.wait_for(cursor.entered.wait(), 1)
        await asyncio.wait_for(complete.wait(), 1)
        await asyncio.sleep(.01)
        assert not task.done() and cursor.close_calls == 0
        cursor.release.set()
        with pytest.raises(SourceReadError) as failure:
            await task
        assert (failure.value.code, failure.value.collection) == ('operational_source_owner_mismatch', 'invalid')
        assert len(database.queries) == len(cursor.operations) == cursor.close_calls == 1
        assert not cursor.closed_during_read and all(operation.done() for operation in cursor.operations)
    finally:
        cursor.release.set()
        task.cancel()
        await asyncio.gather(task, *cursor.operations, return_exceptions=True)


@pytest.mark.asyncio
async def test_caller_cancellation_with_named_policy_still_joins_worker_before_close():
    cursor = ThreadedMotorCursor([[{'user_id': 'owner'}], [{'user_id': 'owner'}]])
    task = asyncio.create_task(load_collections(MotorSourceDB(cursor), 'owner', ['unified_orders'],
        reader=refresh_source_rows, per_read_timeouts={'unified_orders': 20}))
    try:
        await asyncio.wait_for(cursor.entered.wait(), 1)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done() and cursor.close_calls == 0
        task.cancel()
        cursor.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(cursor.operations) == cursor.close_calls == 1 and not cursor.closed_during_read
        assert all(operation.done() for operation in cursor.operations)
    finally:
        cursor.release.set()
        task.cancel()
        await asyncio.gather(task, *cursor.operations, return_exceptions=True)
