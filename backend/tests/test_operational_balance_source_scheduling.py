"""The largest primary source starts promptly without omitting other evidence."""
import asyncio
from collections import Counter

import pytest

import operational_balance_sources as sources
import operational_read_runtime as runtime


class PrimaryComplete(Exception):
    pass


@pytest.mark.asyncio
async def test_orders_enter_first_bounded_wave_and_every_primary_source_is_joined(monkeypatch):
    started, finished, running = [], [], set()
    first_wave, release = asyncio.Event(), asyncio.Event()
    peak, options = 0, []
    original = runtime.load_collections

    async def observed_load(*args, **kwargs):
        options.append({key: kwargs.get(key, default) for key, default in
                       (('concurrency', 4), ('per_read_timeout', 15), ('observe_primary_orders', False))})
        return await original(*args, **kwargs)

    async def reader(db, owner, name):
        nonlocal peak
        started.append(name)
        running.add(name)
        peak = max(peak, len(running))
        if len(started) == 4:
            first_wave.set()
        try:
            await release.wait()
            await asyncio.sleep(0)
            return []
        finally:
            running.remove(name)
            finished.append(name)

    def complete():
        assert not running
        raise PrimaryComplete

    monkeypatch.setattr(runtime, 'load_collections', observed_load)
    monkeypatch.setattr(sources, 'refresh_source_rows', reader)
    task = asyncio.create_task(sources.collect_sources(object(), 'synthetic-owner',
        '2026-11-01T00:00:00+03:00', '2026-11-24T12:00:00+00:00', on_primary_reads_complete=complete))
    try:
        await asyncio.wait_for(first_wave.wait(), 2)
        assert 'unified_orders' in started[:4]
        assert peak == 4
        release.set()
        with pytest.raises(PrimaryComplete):
            await asyncio.wait_for(task, 2)
        assert len(started) == len(set(started)) == 32
        assert Counter(started) == Counter(finished) and not running
        assert started[1:] == sorted(started[1:])
        assert options == [{'concurrency': 4, 'per_read_timeout': 15, 'observe_primary_orders': True}]
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_prioritized_source_failure_still_cancels_and_joins_all_started_readers(monkeypatch):
    active, joined, started = set(), set(), set()
    first_wave = asyncio.Event()
    completed = False

    async def reader(db, owner, name):
        active.add(name)
        started.add(name)
        if len(started) == 4:
            first_wave.set()
        try:
            if name == 'unified_orders':
                await first_wave.wait()
                raise runtime.SourceReadError('operational_source_read_timeout', name)
            await asyncio.Event().wait()
        finally:
            active.remove(name)
            joined.add(name)

    def complete():
        nonlocal completed
        completed = True

    monkeypatch.setattr(sources, 'refresh_source_rows', reader)
    with pytest.raises(runtime.SourceReadError) as failure:
        await asyncio.wait_for(sources.collect_sources(object(), 'synthetic-owner',
            '2026-11-01T00:00:00+03:00', '2026-11-24T12:00:00+00:00',
            on_primary_reads_complete=complete), 2)
    assert failure.value.collection == 'unified_orders'
    assert not completed and not active and started == joined
