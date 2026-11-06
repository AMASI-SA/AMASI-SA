"""Cycle acceptance on disposable real Mongo; the provider alone is synthetic."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from test_qoyod_real_mongo import real_db, canonical
from test_qoyod_two_hour_cycle import arm
from integrations.qoyod_manual import auto_cycle as cycles, auto_send, cycle_manifest


async def synthetic_batch(db, *, batch_limit):
    settings = await auto_send._current_settings(db)
    rows, counts = await auto_send._load_candidate_rows(db, settings=settings,
        orders_user_id='owner', batch_limit=batch_limit)
    results = [{'order_number': row['order_number'],
        'outcome': {'0001': 'retry_later', '0002': 'manual_review'}.get(row['order_number'], 'sent')}
        for row in rows]
    if results:
        # Intentionally NO unique index on the sink: duplicate attempts must fail the test.
        await db.test_invoice_sink.insert_many([dict(row) for row in results])
    return {'ok': True, 'status': 'succeeded' if rows else 'idle',
        'sent_count': sum(r['outcome'] == 'sent' for r in results), 'results': results,
        'discovery': counts['discovery']}


@pytest.mark.asyncio
async def test_all_606_eligible_once_across_slices_and_restart_without_followup_hints(real_db):
    db, _ = real_db
    await arm(db)
    await db.unified_orders.insert_many([canonical(f'{i:04d}') for i in range(606)] + [
        canonical('paid-before'), canonical('unpaid', payment_status='unpaid'),
        canonical('cancelled', order_status='canceled'),
    ])
    await db.qoyod_invoices.insert_one({'user_id': 'main', 'reference': 'paid-before',
        'source': 'plan_b_send', 'qoyod_invoice_id': 'synthetic-existing'})
    ids, slices = set(), 0
    while True:
        result = await cycles.run_due_cycle(db, batch_runner=synthetic_batch,
            use_manifest=True, batch_pause=0, max_batches=20)
        ids.add(result['cycle_id'])
        slices += 1
        if result['status'] != 'continuing':
            break
        saved = await db.qoyod_auto_cycles.find_one({})
        assert saved['status'] == 'running' and 'finished_at' not in saved
        assert await db.qoyod_auto_cycle_items.count_documents({'expires_at': {'$exists': True}}) == 0
        # A newly acquired process/lease resumes the persisted cursor and same cohort.
        assert slices < 20
    assert len(ids) == 1 and slices > 5
    assert result['status'] == 'idle'
    assert result['eligible_count'] == result['processed_count'] == 606
    assert result['sent_count'] == 604 and result['review_count'] == 2
    assert await db.test_invoice_sink.count_documents({}) == 606
    assert len(await db.test_invoice_sink.distinct('order_number')) == 606
    assert await db.test_invoice_sink.count_documents({'order_number': {'$in': ['unpaid', 'cancelled', 'paid-before']}}) == 0
    saved = await db.qoyod_auto_cycles.find_one({})
    assert saved['next_due_at'] - saved['finished_at'] == timedelta(hours=2)
    assert await db.qoyod_auto_cycle_items.count_documents({'expires_at': {'$exists': True}}) == 606
    assert (await cycles.run_due_cycle(db, batch_runner=synthetic_batch, use_manifest=True))['status'] == 'not_due'


@pytest.mark.asyncio
async def test_manifest_page_crash_replays_without_losing_or_duplicating_entries(real_db, monkeypatch):
    db, _ = real_db
    await arm(db)
    await db.unified_orders.insert_many([canonical(f'{i:04d}') for i in range(105)])
    claim = await cycles._claim(db, 'owner')
    collection = db.qoyod_auto_cycles
    original = type(collection).update_one
    async def fail_after_items(self, query, update, *args, **kwargs):
        if self.name == cycles.COLLECTION and 'manifest_after' in update.get('$set', {}) and 'manifest_high_ref' not in update['$set']:
            raise RuntimeError('synthetic page checkpoint crash')
        return await original(self, query, update, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(type(collection), 'update_one', fail_after_items)
        with pytest.raises(RuntimeError, match='checkpoint crash'):
            await cycle_manifest.prepare_page(db, claim)
    assert await db.qoyod_auto_cycle_items.count_documents({}) == 100
    assert not await cycle_manifest.prepare_page(db, claim)
    assert await cycle_manifest.prepare_page(db, claim)
    assert await db.qoyod_auto_cycle_items.count_documents({}) == 105


@pytest.mark.asyncio
async def test_live_change_or_disarm_does_not_leave_an_endless_cycle(real_db):
    db, _ = real_db
    await arm(db)
    await db.unified_orders.insert_many([canonical('a'), canonical('b')])
    first = await cycles.run_due_cycle(db, batch_runner=synthetic_batch, use_manifest=True,
        max_batches=1, batch_pause=0)
    assert first['status'] == 'continuing'
    await db.unified_orders.update_one({'order_number': 'a'}, {'$set': {'payment_status': 'unpaid'}})
    result = await cycles.run_due_cycle(db, batch_runner=synthetic_batch, use_manifest=True, batch_pause=0)
    assert result['status'] == 'idle' and result['processed_count'] == 2
    assert await db.test_invoice_sink.count_documents({}) == 1
    await cycles.request_cycle(db, actor='owner')
    await db.qoyod_settings.update_one({}, {'$set': {'auto_send': False}})
    assert (await cycles.run_due_cycle(db, batch_runner=synthetic_batch, use_manifest=True))['status'] == 'not_armed'
    assert (await db.qoyod_settings.find_one({}))['auto_send'] is False


@pytest.mark.asyncio
async def test_manual_during_cooldown_and_repeated_requests_join_one_running_cycle(real_db):
    db, _ = real_db
    await arm(db)
    idle = AsyncMock(return_value={'ok': True, 'status': 'idle'})
    first = await cycles.run_due_cycle(db, batch_runner=idle)
    requests = await asyncio.gather(*[cycles.request_cycle(db, actor='owner') for _ in range(12)])
    assert sum(not r['joined'] for r in requests) == 1
    assert len({r['request_id'] for r in requests}) == 1
    entered, release = asyncio.Event(), asyncio.Event()
    async def wait(*args, **kwargs):
        entered.set()
        await release.wait()
        return {'ok': True, 'status': 'idle'}
    task = asyncio.create_task(cycles.run_due_cycle(db, batch_runner=wait))
    await asyncio.wait_for(entered.wait(), 5)
    joined = await cycles.request_cycle(db, actor='owner')
    assert joined['joined'] and joined['status'] == 'running'
    release.set()
    manual = await task
    assert manual['cycle_id'] != first['cycle_id']
    saved = await db.qoyod_auto_cycles.find_one({})
    assert saved['trigger'] == 'manual' and not saved['manual_requested']
    assert saved['next_due_at'] - saved['finished_at'] == timedelta(hours=2)


@pytest.mark.asyncio
async def test_manual_http_requires_actual_linked_owner_and_only_queues_work(real_db, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    import background_worker_process
    from integrations.qoyod.routes import make_qoyod_router
    db, _ = real_db
    await arm(db)
    await db.users.insert_many([{'id': 'owner', 'role': 'owner'}, {'id': 'employee', 'role': 'employee'},
        {'id': 'other-owner', 'role': 'owner'}])
    await db.qoyod_settings.update_one({}, {'$set': {'security_owner_id': 'owner'}})
    principal = {'id': 'owner', 'role': 'owner'}
    async def session():
        return principal
    app = FastAPI()
    router = make_qoyod_router(db, session)
    path = next(r.path for r in router.routes if r.path.endswith('/auto-send/cycle'))
    app.include_router(router)
    wake = AsyncMock(return_value=True)
    monkeypatch.setattr(background_worker_process, 'wake_background_process', wake)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://isolated') as client:
        response = await client.post(path)
        assert response.status_code == 202 and response.json()['status'] == 'queued'
        wake.assert_awaited_once()
        assert (await client.post(path)).json()['joined'] is True
        status = (await client.get(path)).json()['cycle']
        assert status['manual_requested'] is True
        assert 'requested_by' not in status and 'manual_request_id' not in status
        for actor in ('employee', 'other-owner', 'missing'):
            principal['_mobile_actor_id'] = actor
            assert (await client.post(path)).status_code == 403
            assert (await client.get(path)).status_code == 403
        principal.pop('_mobile_actor_id')
        await db.qoyod_settings.update_one({}, {'$set': {'auto_send': False}})
        assert (await client.post(path)).status_code == 409
    assert await db.qoyod_manual_auto_runs.count_documents({}) == 0
    assert await db.qoyod_invoices.count_documents({}) == 0
    assert await db.qoyod_auto_cycle_items.count_documents({}) == 0
    assert (await db.qoyod_settings.find_one({}))['auto_send'] is False
