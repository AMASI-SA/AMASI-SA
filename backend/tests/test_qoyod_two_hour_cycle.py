"""Scheduling tests use real local Mongo, synthetic batches and no provider."""
import asyncio
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_qoyod_real_mongo import real_db
from integrations.qoyod_manual import auto_cycle as cycles


async def arm(db):
    await db.qoyod_settings.insert_one({
        'user_id': 'main', 'enabled': True, 'auto_send': True,
        'dry_run_mode': False, 'plan_b_auto_send_armed_at': '2026-11-06',
        'plan_b_auto_send_orders_user_id': 'owner', 'legacy_pipeline_frozen': True,
        'trigger_once_only': True, 'invoice_trigger_statuses': ['completed'],
        'plan_b_unified_auto_send_enabled': True,
    })


@pytest.mark.asyncio
async def test_two_hours_are_global_and_a_cycle_drains_more_than_one_batch(real_db):
    db, _ = real_db
    await arm(db)
    batches = []
    async def send(_db, *, batch_limit):
        batches.append(batch_limit)
        await asyncio.sleep(.01)
        n = (5, 5, 2, 0)[len(batches) - 1]
        return {'ok': True, 'status': 'succeeded' if n else 'idle',
                'sent_count': n, 'results': [{}] * n,
                'discovery': {'checked': n}}
    results = await asyncio.gather(*[
        cycles.run_due_cycle(db, batch_runner=send, batch_pause=0) for _ in range(8)
    ])
    finished = [r for r in results if r.get('claimed')]
    assert len(finished) == 1
    assert finished[0]['sent_count'] == 12
    assert batches == [5, 5, 5, 5]
    saved = await db.qoyod_auto_cycles.find_one({'_id': 'main'})
    assert saved['next_due_at'] - saved['finished_at'] == timedelta(hours=2)
    assert (await cycles.run_due_cycle(db, batch_runner=send))['status'] == 'not_due'
    assert len(batches) == 4


@pytest.mark.asyncio
async def test_round_failure_keeps_auto_enabled_and_does_not_retry_after_15_seconds(real_db):
    db, _ = real_db
    await arm(db)
    calls = []
    async def fail(*args, **kwargs):
        calls.append(1)
        return {'ok': False, 'status': 'round_failed',
                'exception_type': 'CandidateAuditScanLimitExceeded'}
    result = await cycles.run_due_cycle(db, batch_runner=fail)
    assert result['status'] == 'round_failed'
    row = await db.qoyod_auto_cycles.find_one({'_id': 'main'})
    assert row['next_due_at'] - row['finished_at'] == timedelta(hours=2)
    assert row['last_error'] == 'CandidateAuditScanLimitExceeded'
    assert (await db.qoyod_settings.find_one({}))['auto_send'] is True
    assert (await cycles.run_due_cycle(db, batch_runner=fail))['status'] == 'not_due'
    assert calls == [1]


@pytest.mark.asyncio
async def test_crashed_cycle_resumes_only_after_lease_expiry(real_db):
    db, _ = real_db
    await arm(db)
    entered = asyncio.Event()
    async def stuck(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    first = asyncio.create_task(cycles.run_due_cycle(db, batch_runner=stuck))
    await asyncio.wait_for(entered.wait(), 5)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    row = await db.qoyod_auto_cycles.find_one({'_id': 'main'})
    cycle_id = row['cycle_id']
    async def idle(*args, **kwargs):
        return {'ok': True, 'status': 'idle', 'discovery': {'checked': 0}}
    assert (await cycles.run_due_cycle(db, batch_runner=idle))['status'] == 'not_due'
    await db.qoyod_auto_cycles.update_one({'_id': 'main'}, [
        {'$set': {'lease_until': {'$subtract': ['$$NOW', 1]}}},
    ])
    recovered = await cycles.run_due_cycle(db, batch_runner=idle)
    assert recovered['claimed'] is True
    assert recovered['cycle_id'] == cycle_id
    assert (await db.qoyod_auto_cycles.find_one({}))['status'] == 'idle'


@pytest.mark.asyncio
async def test_replaced_owner_cannot_send_a_second_batch_or_publish(real_db):
    db, _ = real_db
    await arm(db)
    calls = []
    async def replace(*args, **kwargs):
        calls.append(1)
        await db.qoyod_auto_cycles.update_one({'_id': 'main'}, {'$set': {'owner': 'replacement'}})
        return {'ok': True, 'status': 'succeeded', 'sent_count': 5, 'results': [{}] * 5}
    result = await cycles.run_due_cycle(db, batch_runner=replace, batch_pause=0)
    assert result['status'] == 'lease_lost'
    assert calls == [1]
    row = await db.qoyod_auto_cycles.find_one({})
    assert row['owner'] == 'replacement'
    assert 'finished_at' not in row


@pytest.mark.asyncio
async def test_slice_resumes_same_cycle_until_all_eligible_are_attempted(real_db):
    db, _ = real_db
    await arm(db)
    await db.test_backlog.insert_many([{'reference': str(i), 'sent': False} for i in range(15)])
    async def send(*args, **kwargs):
        rows = await db.test_backlog.find({'sent': False}).limit(5).to_list(length=5)
        await db.test_backlog.update_many({'_id': {'$in': [r['_id'] for r in rows]}}, {'$set': {'sent': True}})
        return {'ok': True, 'status': 'succeeded' if rows else 'idle',
                'sent_count': len(rows), 'results': rows}
    result = await cycles.run_due_cycle(db, batch_runner=send, max_batches=2, batch_pause=0)
    assert result['status'] == 'continuing'
    assert result['sent_count'] == 10
    assert result['batches'] == 2
    assert await db.test_backlog.count_documents({'sent': False}) == 5
    resumed = await cycles.run_due_cycle(db, batch_runner=send, batch_pause=0)
    assert resumed['cycle_id'] == result['cycle_id']
    assert resumed['status'] == 'idle'
    assert resumed['sent_count'] == 15
    assert await db.test_backlog.count_documents({'sent': False}) == 0
    assert (await cycles.run_due_cycle(db, batch_runner=send))['status'] == 'not_due'


@pytest.mark.asyncio
async def test_disabled_automatic_send_never_calls_batch_or_changes_switches(real_db):
    db, _ = real_db
    await db.qoyod_settings.insert_one({'user_id': 'main', 'enabled': False, 'auto_send': False})
    async def forbidden(*args, **kwargs):
        pytest.fail('disabled automatic sender reached a batch')
    assert (await cycles.run_due_cycle(db, batch_runner=forbidden))['status'] == 'not_armed'
    assert await db.qoyod_auto_cycles.count_documents({}) == 0
    assert (await db.qoyod_settings.find_one({}))['auto_send'] is False


@pytest.mark.asyncio
async def test_two_os_processes_create_one_cycle_and_no_duplicate_synthetic_invoices(real_db):
    db, children = real_db
    await arm(db)
    path = Path(__file__).with_name('qoyod_cycle_process_worker.py')
    for label in ('one', 'two'):
        children.append(subprocess.Popen([sys.executable, str(path), db.name, label],
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
    async with asyncio.timeout(15):
        while await db.test_workers.count_documents({'ready': True}) != 2:
            await asyncio.sleep(.03)
    await db.test_control.insert_one({'_id': 'cycle-start'})
    async with asyncio.timeout(15):
        while await db.test_workers.count_documents({'done': True}) != 2:
            await asyncio.sleep(.03)
    assert await db.test_workers.count_documents({'claimed': True}) == 1
    assert await db.test_invoice_sink.count_documents({}) == 12
    assert len(await db.test_invoice_sink.distinct('reference')) == 12
    assert len({row['pid'] async for row in db.test_workers.find({})}) == 2


@pytest.mark.asyncio
async def test_lost_heartbeat_cancels_inflight_batch_before_another_send(real_db, monkeypatch):
    db, _ = real_db
    await arm(db)
    monkeypatch.setattr(cycles, 'HEARTBEAT_SECONDS', .02)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def blocked(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    task = asyncio.create_task(cycles.run_due_cycle(db, batch_runner=blocked))
    await asyncio.wait_for(entered.wait(), 5)
    await db.qoyod_auto_cycles.update_one({'_id': 'main'}, {'$set': {'owner': 'replacement'}})
    result = await asyncio.wait_for(task, 5)
    assert result['status'] == 'lease_lost'
    assert cancelled.is_set()
    assert 'finished_at' not in (await db.qoyod_auto_cycles.find_one({}))


@pytest.mark.asyncio
async def test_background_discovery_keeps_new_orders_without_sending_between_cycles(real_db):
    from test_qoyod_real_mongo import canonical
    db, _ = real_db
    await arm(db)
    await db.unified_orders.insert_one(canonical('waiting', qoyod_followup_dirty=True,
                                                 qoyod_followup_revision='fresh'))
    task = asyncio.create_task(cycles.followup_loop(db))
    try:
        async with asyncio.timeout(5):
            while not await db.qoyod_order_followup.find_one({'order_number': 'waiting'}):
                await asyncio.sleep(.02)
        assert await db.qoyod_invoices.count_documents({}) == 0
        assert await db.qoyod_auto_cycles.count_documents({}) == 0
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
