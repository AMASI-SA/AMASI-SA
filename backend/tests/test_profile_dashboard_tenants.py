"""Attribution counts disjoint work and preserves the native await protocol."""
import asyncio
import time
import pytest
from profile_dashboard_tenants import Attribution, TENANT


def test_task_driver_result_wait_and_failure():
    async def scenario():
        probe = Attribution()
        loop = asyncio.get_running_loop()
        loop.set_task_factory(probe.factory)
        try:
            async def child():
                await asyncio.sleep(0)
                await asyncio.sleep(.01)
                return 42
            token = TENANT.set('A')
            task = asyncio.create_task(child())
            TENANT.reset(token)
            assert await task == 42
            async def failure():
                await asyncio.sleep(0)
                raise ValueError('expected')
            with pytest.raises(ValueError, match='expected'):
                await asyncio.create_task(failure())
            assert probe.active['A']['calls'] >= 3
            assert probe.waits['A']['suspended_task_seconds'] >= .009
            assert probe.active['A']['wall_seconds'] < probe.waits['A']['suspended_task_seconds']
        finally:
            loop.set_task_factory(None)
    asyncio.run(scenario())


def test_task_driver_cancellation_and_context_isolation():
    async def scenario():
        probe = Attribution()
        loop = asyncio.get_running_loop()
        loop.set_task_factory(probe.factory)
        cleaned = []
        async def child():
            try:
                await asyncio.sleep(10)
            finally:
                cleaned.append(TENANT.get())
        tasks = []
        try:
            for owner in ('A', 'B'):
                token = TENANT.set(owner)
                tasks.append(asyncio.create_task(child()))
                TENANT.reset(token)
            await asyncio.sleep(0)
            for task in tasks:
                task.cancel()
            result = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(isinstance(value, asyncio.CancelledError) for value in result)
            assert cleaned == ['A', 'B']
            assert set(probe.active) == {'A', 'B'}
        finally:
            loop.set_task_factory(None)
    asyncio.run(scenario())


def test_nested_sync_exclusive_categories_do_not_double_count():
    probe = Attribution()
    inner = probe.sync_wrapper(lambda: time.sleep(.01), 'inner')
    outer = probe.sync_wrapper(inner, 'outer')
    token = TENANT.set('A')
    try:
        start = time.perf_counter()
        outer()
        wall = time.perf_counter() - start
    finally:
        TENANT.reset(token)
    metrics = probe.sync['A']
    assert metrics['inner']['wall_seconds'] >= .009
    assert metrics['outer']['wall_seconds'] < metrics['inner']['wall_seconds']
    assert sum(row['wall_seconds'] for row in metrics.values()) <= wall


def test_async_stage_timings_are_separate_from_disjoint_dispatch():
    async def scenario():
        probe = Attribution()
        loop = asyncio.get_running_loop()
        loop.set_task_factory(probe.factory)
        async def child():
            await asyncio.sleep(.01)
            return 'same'
        try:
            wrapped = probe.async_wrapper(child, 'batch')
            assert await asyncio.create_task(wrapped()) == 'same'
            stages = probe.stages['unattributed']
            assert stages['batch_wall_inclusive']['wall_seconds'] >= .009
            assert stages['batch_active_inclusive']['wall_seconds'] < .009
        finally:
            loop.set_task_factory(None)
    asyncio.run(scenario())
