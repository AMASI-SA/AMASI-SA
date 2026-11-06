"""Scheduling tests use real local Mongo, synthetic batches and no provider."""
import asyncio
from datetime import timedelta

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
async def test_budget_retains_backlog_and_no_catchup_storm_after_downtime(real_db):
    db, _ = real_db
    await arm(db)
    async def send(*args, **kwargs):
        return {'ok': True, 'status': 'succeeded', 'sent_count': 5, 'results': [{}] * 5}
    result = await cycles.run_due_cycle(db, batch_runner=send, max_batches=2, batch_pause=0)
    assert result['status'] == 'budget_exhausted'
    assert result['sent_count'] == 10
    assert result['batches'] == 2
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
