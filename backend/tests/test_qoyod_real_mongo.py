"""Real Mongo tests. No production fallback and no provider transport."""
import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from bson.codec_options import CodecOptions

from integrations.qoyod import scheduled_scan as scans
from integrations.qoyod.candidate_orders import build_candidate_audit
from integrations.qoyod.models import ensure_qoyod_indexes
from qoyod_auto_unified import incremental


async def wait_for(predicate, seconds=15):
    async with asyncio.timeout(seconds):
        while not await predicate():
            await asyncio.sleep(.04)


@pytest_asyncio.fixture
async def real_db():
    uri = os.environ.get('QOYOD_TEST_MONGO_URI', '')
    if not uri:
        pytest.skip('QOYOD_TEST_MONGO_URI required; no production fallback')
    assert uri.startswith('mongodb://127.0.0.1:')
    client = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    assert (await client.admin.command('hello')).get('setName')
    db = client['qoyod_test_' + uuid.uuid4().hex]
    await ensure_qoyod_indexes(db)
    await db.unified_orders.create_index([('user_id', 1), ('order_number', 1)], unique=True)
    await db.unified_orders.create_index([('user_id', 1), ('order_date', -1)])
    children = []
    yield db, children
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
    assert db.name.startswith('qoyod_test_')
    await client.drop_database(db.name)
    client.close()


def spawn(db, children, action, label):
    path = Path(__file__).with_name('qoyod_mongo_process_worker.py')
    child = subprocess.Popen([sys.executable, str(path), db.name, action, label],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    children.append(child)
    return child


@pytest.mark.asyncio
async def test_scheduled_and_manual_workers_exclude_each_other_and_renew(real_db):
    db, children = real_db
    spawn(db, children, 'scheduled_scan', 'scheduled')
    spawn(db, children, 'manual_scan', 'manual')
    async def ready():
        return await db.test_workers.count_documents({'ready': True}) == 2
    await wait_for(ready)
    await db.test_control.insert_one({'_id': 'start'})
    async def attempted():
        return await db.test_workers.count_documents({'accepted': {'$exists': True}}) == 2
    await wait_for(attempted)
    assert await db.test_workers.count_documents({'accepted': True}) == 1
    assert len({row['pid'] async for row in db.test_workers.find({})}) == 2
    initial = await db.qoyod_full_scans.find_one({'_id': 'main'})
    async def renewed():
        current = await db.qoyod_full_scans.find_one({'_id': 'main'})
        return current['lease_until'] > initial['lease_until'] + timedelta(seconds=10)
    await wait_for(renewed, seconds=30)  # Actual production 20-second heartbeat.
    assert not (await scans.request_scan(db, tenant='main', owner='owner', manual=True))['accepted']
    assert (await db.test_control.find_one({'_id': 'peak'}))['count'] == 1
    await db.test_control.insert_one({'_id': 'release'})
    async def done():
        return await db.test_workers.count_documents({'done': True}) == 2
    await wait_for(done)
    assert (await scans.read_scan(db, tenant='main', owner='owner'))['report']['ok']


@pytest.mark.asyncio
async def test_worker_crash_recovers_in_same_slot_after_expiry(real_db):
    db, children = real_db
    child = spawn(db, children, 'crash_scan', 'crashing')
    await db.test_control.insert_one({'_id': 'start'})
    async def entered():
        return await db.test_workers.find_one({'_id': 'crashing', 'entered': True})
    await wait_for(entered)
    old = await db.qoyod_full_scans.find_one({'_id': 'main'})
    child.kill()  # Only this fixture's synthetic process, never an app worker.
    child.wait(timeout=10)
    async def expired():
        return datetime.now(timezone.utc) >= old['lease_until']
    await wait_for(expired, seconds=6)
    async def replacement(*args, **kwargs):
        return {'ok': True, 'rows': ['recovered']}, {'count': 0, 'complete': True}
    result = await scans.request_scan(db, tenant='main', owner='owner', executor=replacement)
    assert result['accepted'] is True
    await asyncio.gather(*list(scans._TASKS))
    assert (await scans.read_scan(db, tenant='main', owner='owner'))['report']['rows'] == ['recovered']
    # A resumed stale worker cannot publish over the replacement.
    await scans._run(db, 'main', 'owner', old, replacement)
    saved = await db.qoyod_full_scans.find_one({'_id': 'main'})
    assert saved['token'] != old['token']


@pytest.mark.asyncio
@pytest.mark.parametrize('failed', [False, True])
async def test_send_lock_allows_only_one_worker_to_create_invoice(real_db, failed):
    db, children = real_db
    if failed:
        await db.qoyod_manual_send_locks.insert_one({'user_id': 'main', 'order_number': 'same-order',
            'lock_id': 'failed-attempt', 'status': 'failed'})
    action = 'failed_lock' if failed else 'fresh_lock'
    spawn(db, children, action, 'sender-a')
    spawn(db, children, action, 'sender-b')
    await db.test_control.insert_one({'_id': 'start'})
    async def done():
        return await db.test_workers.count_documents({'done': True}) == 2
    await wait_for(done)
    assert await db.test_invoice_sink.count_documents({}) == 1
    assert await db.test_workers.count_documents({'lock_won': True}) == 1


def canonical(ref, **extra):
    return {'user_id': 'owner', 'order_number': ref, 'order_date': '2026-11-01',
            'order_status': 'completed', 'payment_method': 'mada', 'payment_status': 'paid',
            'total_amount': 100, **extra}


@pytest.mark.asyncio
async def test_new_event_during_read_survives_checkpoint(real_db):
    db, _ = real_db
    await db.unified_orders.insert_one(canonical('new', payment_status='unpaid'))
    await incremental.mark_dirty(db, 'owner', 'new')
    async def event_during_read(inner, **kwargs):
        old = await build_candidate_audit(inner, **kwargs)
        await inner.unified_orders.update_one({'order_number': 'new'}, {'$set': {'payment_status': 'paid'}})
        await incremental.mark_dirty(inner, 'owner', 'new')
        return old
    first = await incremental.discover(db, 'owner', event_during_read)
    assert not first['orders']
    assert (await db.qoyod_order_followup.find_one({'order_number': 'new'}))['priority'] == 0
    second = await incremental.discover(db, 'owner', build_candidate_audit)
    assert [r['order_number'] for r in second['orders']] == ['new']


@pytest.mark.asyncio
async def test_recovery_replays_page_after_partial_failure_without_losing_orders(real_db, monkeypatch):
    db, _ = real_db
    await db.unified_orders.insert_many([canonical(str(i).zfill(4)) for i in range(250)])
    original = incremental.write_batch
    async def fail_after_persist(collection, operations):
        await original(collection, operations)
        raise RuntimeError('synthetic crash before checkpoint')
    monkeypatch.setattr(incremental, 'write_batch', fail_after_persist)
    with pytest.raises(RuntimeError):
        await incremental.recover_page(db, 'owner')
    assert await db.qoyod_followup_cursors.count_documents({}) == 0
    monkeypatch.setattr(incremental, 'write_batch', original)
    for _ in range(4):
        await incremental.recover_page(db, 'owner')
        await db.qoyod_followup_cursors.update_many({}, {'$unset': {'next_sweep_at': ''}})
    assert await db.qoyod_order_followup.count_documents({}) == 250
    await db.unified_orders.insert_one(canonical('0000-missed'))
    await incremental.recover_page(db, 'owner')
    assert await db.qoyod_order_followup.find_one({'order_number': '0000-missed'})


@pytest.mark.asyncio
async def test_declared_indexes_exist_and_queue_reads_use_them(real_db):
    db, _ = real_db
    index = await db.qoyod_order_followup.index_information()
    assert index['qoyod_followup_owner_order']['unique'] is True
    assert index['qoyod_followup_due']['key'] == [('user_id', 1), ('priority', 1), ('next_check_at', 1), ('order_number', 1)]
    await incremental.mark_dirty(db, 'owner', 'one')
    plan = await db.command('explain', {'find': 'qoyod_order_followup',
        'filter': {'user_id': 'owner', 'priority': 0, 'next_check_at': {'$lte': datetime.now(timezone.utc)}},
        'sort': {'next_check_at': 1, 'order_number': 1}, 'limit': 50}, verbosity='executionStats',
        codec_options=CodecOptions(unicode_decode_error_handler='replace'))
    # This Windows Mongo build emits invalid UTF-8 in serverInfo.host only.
    # Do not weaken the application data codec or hide malformed plan values.
    def replacement_paths(value, path=''):
        if isinstance(value, dict):
            return [p for k, v in value.items() for p in replacement_paths(v, f'{path}.{k}')]
        if isinstance(value, list):
            return [p for i, v in enumerate(value) for p in replacement_paths(v, f'{path}[{i}]')]
        return [path] if isinstance(value, str) and '\ufffd' in value else []
    assert set(replacement_paths(plan)) <= {'.serverInfo.host'}
    assert 'qoyod_followup_due' in str(plan['queryPlanner']['winningPlan'])
    assert plan['executionStats']['totalDocsExamined'] <= 1
    assert (await db.qoyod_scan_views.index_information())['qoyod_scan_view_generation']['unique'] is True


@pytest.mark.asyncio
async def test_committed_intake_hint_is_acknowledged_only_after_queue_persistence(real_db, monkeypatch):
    db, _ = real_db
    await db.unified_orders.insert_one(canonical('committed', qoyod_followup_dirty=True, qoyod_followup_revision='r1'))
    original = incremental.write_batch
    async def crash_after_queue(collection, operations):
        if collection.name == 'unified_orders':
            raise RuntimeError('synthetic crash before acknowledgement')
        await original(collection, operations)
    monkeypatch.setattr(incremental, 'write_batch', crash_after_queue)
    with pytest.raises(RuntimeError):
        await incremental.drain_dirty_page(db, 'owner')
    assert (await db.unified_orders.find_one({'order_number': 'committed'}))['qoyod_followup_dirty'] is True
    assert await db.qoyod_order_followup.count_documents({}) == 1
    monkeypatch.setattr(incremental, 'write_batch', original)
    assert await incremental.drain_dirty_page(db, 'owner') == 1
    assert (await db.unified_orders.find_one({'order_number': 'committed'}))['qoyod_followup_dirty'] is False
    assert await db.qoyod_order_followup.count_documents({}) == 1
