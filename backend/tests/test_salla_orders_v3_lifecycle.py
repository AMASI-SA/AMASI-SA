"""Process restart and shutdown contract for the optional observer."""
import ast
import asyncio
from pathlib import Path

import pytest

import salla_orders_v3.worker as worker


def test_server_starts_observer_per_process_and_stops_before_mongo():
    tree = ast.parse((Path(__file__).parents[1] / 'server.py').read_text())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)}
    global_calls = ast.unparse(functions['_global_startup'])
    local_calls = ast.unparse(functions['_local_startup'])
    shutdown = ast.unparse(functions['on_shutdown'])
    assert '_start_salla_orders_v3_shadow_worker' not in global_calls
    assert 'start_salla_orders_v3_shadow_runtime' in local_calls
    assert shutdown.index('stop_salla_orders_v3_shadow_worker') < shutdown.index('client.close()')


@pytest.mark.asyncio
async def test_disabled_runtime_never_reads_or_writes_mongo(monkeypatch):
    monkeypatch.delenv('SALLA_ORDERS_V3_SHADOW_ENABLED', raising=False)
    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError(f'unexpected database use: {name}')
    assert await worker.start_salla_orders_v3_shadow_runtime(NoDatabase()) is None


@pytest.mark.asyncio
async def test_runtime_restart_starts_new_task_after_awaited_shutdown(monkeypatch):
    monkeypatch.setenv('SALLA_ORDERS_V3_SHADOW_ENABLED', 'true')
    monkeypatch.setattr(worker, '_task', None)
    started, stopped, indexed = [], [], []
    async def indexes(db):
        indexed.append(db)
    async def loop(db):
        started.append(db)
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            stopped.append(db)
    monkeypatch.setattr(worker, 'ensure_salla_orders_v3_indexes', indexes)
    monkeypatch.setattr(worker, '_worker_loop', loop)
    db = object()
    first = await worker.start_salla_orders_v3_shadow_runtime(db)
    await asyncio.sleep(0)
    assert await worker.start_salla_orders_v3_shadow_runtime(db) is first
    await worker.stop_salla_orders_v3_shadow_worker(first)
    assert stopped == [db]
    assert first.done()
    second = await worker.start_salla_orders_v3_shadow_runtime(db)
    try:
        await asyncio.sleep(0)
        assert second is not first
        assert started == [db, db]
        assert indexed
    finally:
        await worker.stop_salla_orders_v3_shadow_worker(second)
    assert stopped == [db, db]


@pytest.mark.asyncio
async def test_index_failure_does_not_start_worker(monkeypatch):
    monkeypatch.setenv('SALLA_ORDERS_V3_SHADOW_ENABLED', 'true')
    monkeypatch.setattr(worker, '_task', None)
    async def indexes(_db):
        raise RuntimeError('index-unavailable')
    monkeypatch.setattr(worker, 'ensure_salla_orders_v3_indexes', indexes)
    with pytest.raises(RuntimeError, match='index-unavailable'):
        await worker.start_salla_orders_v3_shadow_runtime(object())
    assert worker._task is None


@pytest.mark.asyncio
async def test_stopping_old_task_does_not_clear_successor(monkeypatch):
    old = asyncio.create_task(asyncio.sleep(0))
    await old
    successor = asyncio.create_task(asyncio.sleep(60))
    monkeypatch.setattr(worker, '_task', successor)
    try:
        await worker.stop_salla_orders_v3_shadow_worker(old)
        assert worker._task is successor
        assert not successor.done()
    finally:
        successor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await successor


@pytest.mark.asyncio
async def test_cancelling_provider_call_drains_job_heartbeat(monkeypatch):
    from unittest.mock import AsyncMock

    entered = asyncio.Event()
    stopped = asyncio.Event()
    heartbeat_tasks = []

    async def heartbeat(_db, *, stop, **kwargs):
        heartbeat_tasks.append(asyncio.current_task())
        await stop.wait()
        stopped.set()

    class BlockingProvider:
        async def prepare_order_snapshot(self, **kwargs):
            entered.set()
            await asyncio.Future()

    monkeypatch.setattr(worker, 'heartbeat_shadow_job_lease', AsyncMock(return_value=True))
    monkeypatch.setattr(worker, '_job_heartbeat_loop', heartbeat)
    monkeypatch.setattr(worker, 'shadow_collection', lambda *args: object())
    task = asyncio.create_task(worker.process_shadow_job(
        object(), {'_id': 'job', 'lease_token': 'token', 'lease_epoch': 1,
                   'signal_revision': 1, 'attempts': 1}, engine=BlockingProvider(),
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set(), 'provider cancellation left a live Mongo heartbeat'
        assert all(heartbeat.done() for heartbeat in heartbeat_tasks)
    finally:
        for heartbeat_task in heartbeat_tasks:
            if not heartbeat_task.done():
                heartbeat_task.cancel()
        await asyncio.gather(*heartbeat_tasks, return_exceptions=True)
