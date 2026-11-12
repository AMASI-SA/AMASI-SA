"""Bounded read concurrency, snapshot isolation and cancellation guarantees."""
import asyncio
from copy import deepcopy

import pytest

from operational_read_runtime import SourceReadError, SnapshotReadCache, load_collections, source_read_budget


def state(owner='owner', revision=1, as_of='2026-11-12T10:00:00+00:00'):
    return {'owner_id': owner, 'revision': revision, 'status': 'active',
            'started_at': '2026-11-01T00:00:00+00:00', 'engine': {'as_of': as_of}}


@pytest.mark.asyncio
async def test_independent_reads_run_at_most_four_at_once_without_losing_any_collection():
    active, peak = 0, 0
    async def reader(db, owner, name):
        nonlocal active, peak
        assert owner == 'owner'
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(.001)
            return [name]
        finally:
            active -= 1
    names = tuple('collection_' + str(i) for i in range(12))
    result = await load_collections(object(), 'owner', names, reader=reader, concurrency=20)
    assert result == {name: [name] for name in names}
    assert peak == 4 and active == 0


@pytest.mark.asyncio
async def test_snapshot_transaction_reads_stay_serial():
    class Snapshot:
        session = None
        def __init__(self):
            self.session = object()
    active, peak = 0, 0
    async def reader(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return []
    await load_collections(Snapshot(), 'owner', ['one', 'two', 'three'], reader=reader)
    assert peak == 1


@pytest.mark.asyncio
async def test_scope_failure_keeps_collection_context_and_cancels_sibling_reads():
    stopped = set()
    async def reader(db, owner, name):
        if name == 'oversized':
            await asyncio.sleep(0)
            raise ValueError('operational_source_scope_too_large')
        try:
            await asyncio.Event().wait()
        finally:
            stopped.add(name)
    with pytest.raises(SourceReadError) as error:
        await load_collections(object(), 'owner', ['oversized', 'other', 'another'], reader=reader)
    assert error.value.code == 'operational_source_scope_too_large'
    assert error.value.collection == 'oversized'
    assert stopped == {'other', 'another'}


@pytest.mark.asyncio
async def test_read_budget_times_out_only_reads_and_identifies_the_collection():
    cancelled = asyncio.Event()
    async def reader(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    with source_read_budget(.01):
        with pytest.raises(SourceReadError) as error:
            await load_collections(object(), 'owner', ['slow'], reader=reader)
    assert cancelled.is_set()
    assert error.value.code == 'operational_source_read_timeout' and error.value.collection == 'slow'
    assert 'owner' not in str(error.value)


@pytest.mark.asyncio
async def test_estimate_cache_coalesces_identical_snapshot_reads_and_returns_independent_values():
    cache, database, calls = SnapshotReadCache(), object(), 0
    async def loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(.01)
        return {'groups': [{'amount': '20.00'}]}
    first, second = await asyncio.gather(*(cache.get(database, 'owner', state(), None, loader) for _ in range(2)))
    assert calls == 1 and first == second
    first['groups'][0]['amount'] = '999.00'
    third = await cache.get(database, 'owner', state(), None, loader)
    assert third == second and third['groups'][0]['amount'] == '20.00' and calls == 1


@pytest.mark.asyncio
async def test_estimate_cache_never_reuses_other_owner_database_revision_or_snapshot():
    cache, database, calls = SnapshotReadCache(), object(), 0
    async def loader():
        nonlocal calls
        calls += 1
        return calls
    assert await cache.get(database, 'owner', state(), None, loader) == 1
    assert await cache.get(database, 'other', state('other'), None, loader) == 2
    assert await cache.get(object(), 'owner', state(), None, loader) == 3
    assert await cache.get(database, 'owner', state(revision=2), None, loader) == 4
    assert await cache.get(database, 'owner', state(as_of='2026-11-12T10:05:00+00:00'), None, loader) == 5
    assert await cache.get(database, 'owner', state(), '2026-11-12T10:03:00+00:00', loader) == 6
    altered_start = deepcopy(state())
    altered_start['started_at'] = '2026-11-02T00:00:00+00:00'
    assert await cache.get(database, 'owner', altered_start, None, loader) == 7


@pytest.mark.asyncio
async def test_cache_expires_and_failed_reads_are_never_retained():
    clock, calls = [0], 0
    cache, database = SnapshotReadCache(ttl=20, clock=lambda: clock[0]), object()
    async def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError('source incomplete')
        return calls
    with pytest.raises(ValueError):
        await cache.get(database, 'owner', state(), None, loader)
    assert await cache.get(database, 'owner', state(), None, loader) == 2
    clock[0] = 19
    assert await cache.get(database, 'owner', state(), None, loader) == 2
    clock[0] = 20
    assert await cache.get(database, 'owner', state(), None, loader) == 3


@pytest.mark.asyncio
async def test_one_cancelled_reader_does_not_cancel_another_reader_of_the_same_snapshot():
    cache, database, release, entered = SnapshotReadCache(), object(), asyncio.Event(), asyncio.Event()
    async def loader():
        entered.set()
        await release.wait()
        return {'amount': '20.00'}
    first = asyncio.create_task(cache.get(database, 'owner', state(), None, loader))
    second = asyncio.create_task(cache.get(database, 'owner', state(), None, loader))
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == {'amount': '20.00'}
    assert not cache.inflight


@pytest.mark.asyncio
async def test_cancelling_the_last_reader_drains_its_read_only_work():
    cache, database, entered, stopped = SnapshotReadCache(), object(), asyncio.Event(), asyncio.Event()
    async def loader():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    task = asyncio.create_task(cache.get(database, 'owner', state(), None, loader))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set() and not cache.inflight and not cache.entries


@pytest.mark.asyncio
async def test_optional_cost_projection_bounds_its_late_reads_without_writing_state(monkeypatch):
    import operational_open_cost_exposure as exposure
    stopped = asyncio.Event()
    async def slow(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(exposure, '_project', slow)
    monkeypatch.setattr(exposure, 'READ_TIMEOUT', .01)
    saved = state()
    before = deepcopy(saved)
    with pytest.raises(SourceReadError) as error:
        await exposure.project(object(), 'owner', saved)
    assert stopped.is_set() and saved == before
    assert error.value.code == 'operational_source_read_timeout'
    assert error.value.collection == 'open_cost_exposure'
