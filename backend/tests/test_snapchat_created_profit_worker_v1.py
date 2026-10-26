from __future__ import annotations

import asyncio
import threading
import time

import pytest

from resource_governor import ResourceGovernor
from integrations_control_center import snapchat_campaign_created_order_semantics as module
from integrations_control_center import snapchat_campaign_profitability as profitability


@pytest.fixture
def admitted(monkeypatch):
    governor = ResourceGovernor()
    monkeypatch.setattr(governor, 'decision', lambda: ('normal', None))
    monkeypatch.setattr(module, 'governor', governor)
    async def context(db, user_id):
        return {}
    monkeypatch.setattr(profitability, '_load_cost_context', context)
    return governor


def call(cohort, *, owned=True):
    return module.calculate_financial_profitability(
        None, 'test', account_id='a', date_from='2026-01-01', date_to='2026-01-01',
        financial_matched=cohort, campaign_spend={('a', 'c'): 0},
        _ownership_token=module._OWNED_INPUT_TOKEN if owned else None,
    )


@pytest.mark.asyncio
async def test_cancelled_workers_keep_real_governor_admission_until_drained(admitted, monkeypatch):
    started = asyncio.Event()
    release = threading.Event()
    lock = threading.Lock()
    loop = asyncio.get_running_loop()
    active = 0
    peak = 0
    def worker(*args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        with lock:
            active -= 1
        return {}, {}
    monkeypatch.setattr(module, '_calculate_financial_profitability_sync', worker)
    cohort = {('a', 'c'): [{}]}
    tasks = [asyncio.create_task(call(cohort)) for _ in range(3)]
    try:
        await asyncio.wait_for(started.wait(), 2)
        tasks[0].cancel()
        await asyncio.sleep(.01)
        tasks[0].cancel()
        await asyncio.sleep(.02)
        assert admitted._active.get('ads') == 2
        assert not tasks[0].done()
        assert not tasks[2].done()
        assert peak == 2
    finally:
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert admitted._active.get('ads') == 0
    assert peak == 2


@pytest.mark.asyncio
async def test_large_owned_cohort_keeps_event_loop_responsive(admitted, monkeypatch):
    def cost(order, context):
        return {'order_sales_sar': 1., 'product_cost_sar': 1., 'allocated_product_sales_sar': 1.,
                'unallocated_sales_sar': 0., 'missing_everywhere': False, 'uses_salla_fallback': False,
                'mezan_cost_complete': True, 'no_products': False, 'lines': []}
    monkeypatch.setattr(profitability, '_order_cost_and_products', cost)
    cohort = {('a', 'c'): [{'id': i} for i in range(20000)]}
    task = asyncio.create_task(call(cohort))
    ticks = []
    while not task.done():
        ticks.append(time.monotonic())
        await asyncio.sleep(.005)
    _, total = await task
    assert total['orders'] == 20000
    assert len(ticks) >= 2
    assert max(b - a for a, b in zip(ticks, ticks[1:])) < .3


@pytest.mark.asyncio
async def test_resource_pressure_fails_closed_without_worker(monkeypatch):
    governor = ResourceGovernor()
    monkeypatch.setattr(governor, 'decision', lambda: ('blocked', None))
    monkeypatch.setattr(module, 'governor', governor)
    with pytest.raises(module.manager.SnapchatNativeSyncError) as exc:
        await call({('a', 'c'): [{}]})
    assert exc.value.status_code == 503
    assert exc.value.retryable is True
    assert not governor._active.get('ads')

@pytest.mark.asyncio
async def test_report_owns_distinct_cohorts_and_reinstallation_is_idempotent(monkeypatch):
    manager = module.manager
    async def old_outcomes(*args, **kwargs):
        raise AssertionError('must be replaced')
    async def original_report(db, user_id, **kwargs):
        await manager._salla_account_outcomes(db, user_id, date_from='2026-01-01',
            date_to='2026-01-01', timezone_name='UTC', identities=[])
        await asyncio.sleep(0)
        return {'result_source': 'salla', 'selected_account_id': user_id,
                'date_from': '2026-01-01', 'date_to': '2026-01-01',
                'campaigns': [{'account_id': user_id, 'campaign_id': 'c', 'spend_sar': 0}],
                'totals': {}, 'source': {}}
    async def build(db, user_id, **kwargs):
        return {}, {}, {}, {(user_id, 'c'): [{'id': user_id}]}
    observed = {}
    async def calculate(db, user_id, **kwargs):
        assert kwargs['_ownership_token'] is module._OWNED_INPUT_TOKEN
        observed[user_id] = kwargs['financial_matched']
        await asyncio.sleep(0)
        return {}, {'orders': 1}
    monkeypatch.setattr(manager, '_salla_account_outcomes', old_outcomes)
    monkeypatch.setattr(manager, 'build_account_timezone_campaign_report', original_report)
    monkeypatch.setattr(module, 'build_created_and_financial_outcomes', build)
    monkeypatch.setattr(module, 'calculate_financial_profitability', calculate)
    module.install_fixed_created_order_semantics()
    installed = manager.build_account_timezone_campaign_report
    async def outer(*args, **kwargs):
        return await installed(*args, **kwargs)
    monkeypatch.setattr(manager, 'build_account_timezone_campaign_report', outer)
    module.install_fixed_created_order_semantics()
    assert manager.build_account_timezone_campaign_report is outer
    before = module._FINANCIAL_MATCHED.get()
    await asyncio.gather(outer(object(), 'one'), outer(object(), 'two'))
    assert observed == {'one': {('one', 'c'): [{'id': 'one'}]},
                        'two': {('two', 'c'): [{'id': 'two'}]}}
    assert module._FINANCIAL_MATCHED.get() is before
    assert module._OWNED_FINANCIAL_MATCHED.get() is None

@pytest.mark.asyncio
async def test_cancel_during_global_release_drains_before_return(admitted, monkeypatch):
    entered = threading.Event()
    finish = threading.Event()
    def worker(*args):
        entered.set()
        assert finish.wait(5)
        return {}, {}
    monkeypatch.setattr(module, '_calculate_financial_profitability_sync', worker)
    task = asyncio.create_task(call({('a', 'c'): [{}]}))
    while not entered.is_set():
        await asyncio.sleep(.001)
    await admitted._global._condition.acquire()
    try:
        finish.set()
        while admitted._active.get('ads', 0):
            await asyncio.sleep(.001)
        task.cancel()
        await asyncio.sleep(.01)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done()
        assert admitted._global.in_use == 1
    finally:
        admitted._global._condition.release()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert admitted._global.in_use == 0
