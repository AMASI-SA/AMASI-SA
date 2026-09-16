"""Real Mongo acceptance tests; synthetic events, disposable local replica set.

MEZAN_SALLA_V3_TEST_MONGO_URL is deliberately separate from application settings.
The CI gate supplies it and rejects skipped cases. No production DB is accepted.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.uri_parser import parse_uri

import salla_orders_v3.ingestion as ingestion
import salla_orders_v3.probe as probe
from salla_orders_v3.worker import (
    claim_due_shadow_job,
    ensure_salla_orders_v3_indexes,
    heartbeat_shadow_job_lease,
    run_recovery_once,
)
from salla_orders_v3.gateway import PaginationPage


@pytest_asyncio.fixture
async def mongo_db():
    url = os.environ.get('MEZAN_SALLA_V3_TEST_MONGO_URL')
    if not url:
        pytest.skip('requires the isolated Mongo replica-set acceptance job')
    parsed = parse_uri(url)
    assert all(host in {'127.0.0.1', 'localhost', '::1'} for host, _ in parsed['nodelist'])
    assert not parsed['username'] and not parsed['password'] and not parsed['database']
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000, tz_aware=True)
    database_name = 'test_salla_v3_' + uuid.uuid4().hex
    db = client[database_name]
    try:
        hello = await client.admin.command('hello')
        assert hello.get('setName'), 'transactions require a real replica set'
        assert hello.get('isWritablePrimary') is True
        assert not callable(getattr(type(db), '_salla_orders_v3_transaction_runner', None))
        await ensure_salla_orders_v3_indexes(db)
        yield db
    finally:
        await client.drop_database(database_name)
        client.close()


def event(event_id='synthetic-event', revision=1):
    return {
        'id': event_id, 'event': 'order.updated',
        'created_at': '2026-09-16T10:00:00+00:00',
        'data': {'id': 901, 'reference_id': '3001', 'revision': revision},
    }


async def capture(db, *, owner='owner-1', body=None):
    return await ingestion.capture_verified_order_event(
        db, user_id=owner, store_id='50', event_body=body or event(),
    )


@pytest.mark.asyncio
async def test_mongo_concurrent_duplicate_events_commit_one_job(mongo_db):
    results = await asyncio.gather(*(capture(mongo_db) for _ in range(6)))
    final = await capture(mongo_db)
    assert sum(result['created'] for result in results) == 1
    assert final['queue_ensured'] is True
    assert await mongo_db.salla_orders_v3_events.count_documents({}) == 1
    assert await mongo_db.salla_orders_v3_jobs.count_documents({}) == 1
    row = await mongo_db.salla_orders_v3_events.find_one({})
    assert row['outbox_status'] == 'delivered'
    assert row['job_id'] == 'owner-1:50:901'
    assert 'outbox_expires_at' in row


@pytest.mark.asyncio
async def test_mongo_error_after_enqueue_rolls_back_and_repair_commits(mongo_db, monkeypatch):
    enqueue = ingestion.enqueue_shadow_job

    async def fail_after_real_write(*args, **kwargs):
        await enqueue(*args, **kwargs)
        raise RuntimeError('synthetic failure between transactional writes')

    monkeypatch.setattr(ingestion, 'enqueue_shadow_job', fail_after_real_write)
    with pytest.raises(RuntimeError, match='between transactional writes'):
        await capture(mongo_db)
    assert await mongo_db.salla_orders_v3_jobs.count_documents({}) == 0
    row = await mongo_db.salla_orders_v3_events.find_one({})
    assert row['outbox_status'] == 'retrying'
    assert 'outbox_expires_at' not in row
    monkeypatch.setattr(ingestion, 'enqueue_shadow_job', enqueue)
    due = row['next_attempt_at'] + timedelta(seconds=1)
    result = await ingestion.repair_event_job_outbox_once(mongo_db, now=due, clock=lambda: due)
    assert result['repaired'] == 1
    assert await mongo_db.salla_orders_v3_jobs.count_documents({}) == 1
    stored = await mongo_db.salla_orders_v3_events.find_one({})
    assert stored['outbox_status'] == 'delivered'
    assert stored['outbox_attempts'] == 2


@pytest.mark.asyncio
async def test_mongo_expired_outbox_fence_rolls_back_job(mongo_db):
    now = datetime.now(timezone.utc)
    row = {
        '_id': 'expired-intent', 'user_id': 'owner-1', 'store_id': '50',
        'payload': event()['data'], 'outbox_status': 'processing',
        'outbox_lease_token': 'synthetic-token', 'outbox_lease_epoch': 1,
        'outbox_lease_expires_at': now + timedelta(seconds=30),
    }
    await mongo_db.salla_orders_v3_events.insert_one(row)
    times = iter((now, now + timedelta(seconds=31)))
    outcome = await ingestion._deliver_event_outbox_row(mongo_db, row, clock=lambda: next(times))
    assert outcome is None
    assert await mongo_db.salla_orders_v3_jobs.count_documents({}) == 0
    stored = await mongo_db.salla_orders_v3_events.find_one({'_id': row['_id']})
    assert stored['outbox_status'] == 'processing'
    assert 'job_enqueued_at' not in stored


@pytest.mark.asyncio
async def test_mongo_job_claim_is_exclusive_and_new_signal_fences_owner(mongo_db):
    await capture(mongo_db)
    claims = await asyncio.gather(*(claim_due_shadow_job(mongo_db) for _ in range(6)))
    owned = [row for row in claims if row is not None]
    assert len(owned) == 1
    first = owned[0]
    await capture(mongo_db, body=event('new-synthetic-event', revision=2))
    assert not await heartbeat_shadow_job_lease(
        mongo_db, job_id=first['_id'], lease_token=first['lease_token'],
        lease_epoch=first['lease_epoch'], signal_revision=first['signal_revision'],
    )
    stored = await mongo_db.salla_orders_v3_jobs.find_one({'_id': first['_id']})
    assert stored['signal_revision'] == first['signal_revision'] + 1
    assert stored['provider_revision'] == 2


@pytest.mark.asyncio
async def test_mongo_same_provider_ids_are_isolated_by_owner(mongo_db):
    await asyncio.gather(capture(mongo_db, owner='owner-1'), capture(mongo_db, owner='owner-2'))
    assert await mongo_db.salla_orders_v3_events.count_documents({}) == 2
    rows = await mongo_db.salla_orders_v3_jobs.find({}).to_list(length=3)
    assert {row['_id'] for row in rows} == {'owner-1:50:901', 'owner-2:50:901'}
    assert all(row['signal_revision'] == 1 for row in rows)


@pytest.mark.asyncio
async def test_mongo_probe_single_winner_and_sealed_evidence_without_operational_writes(mongo_db, monkeypatch):
    from .test_salla_orders_v3_probe import setup_probe, collect

    db, calls = await setup_probe(monkeypatch, db=mongo_db, now=datetime.now(timezone.utc))
    before = await db.unified_orders.find_one({})
    results = await asyncio.gather(collect(db), collect(db), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, probe.ProbeError) and result.code == 'probe_cooldown' for result in results) == 1
    assert len(calls) == 3
    assert await db.salla_orders_v3_parity_evidence.count_documents({}) == 1
    assert before == await db.unified_orders.find_one({})
    assert await db.integration_inbox.count_documents({}) == 0
    assert await db.salla_orders_v3_jobs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_mongo_expired_cursor_repairs_pending_jobs_then_replays_same_date(mongo_db):
    now = datetime(2026, 9, 16, 9, tzinfo=timezone.utc)
    await mongo_db.salla_orders_v3_sync_state.insert_one({
        '_id': 'owner-1:50', 'next_from_date': '2026-09-15',
        'next_to_date': '2026-09-15', 'next_page': 2,
        'pagination_started_at': now - timedelta(minutes=16),
        'pending_discovery_jobs': [{'light_order': event()['data']}],
    })

    class SyntheticGateway:
        async def list_light_orders_page(self, user_id, *, page, from_date, to_date):
            assert page == 1
            assert from_date == to_date == '2026-09-15'
            assert await mongo_db.salla_orders_v3_jobs.count_documents({}) == 1
            return [event()['data']], PaginationPage(1, 2, 2, False)

    result = await run_recovery_once(
        mongo_db, user_id='owner-1', store_id='50', max_pages=1,
        gateway=SyntheticGateway(), now=now,
    )
    assert result['pagination_restarts'] == 1
    assert await mongo_db.salla_orders_v3_jobs.count_documents({}) == 1
    state = await mongo_db.salla_orders_v3_sync_state.find_one({'_id': 'owner-1:50'})
    assert state['next_from_date'] == '2026-09-15'
    assert state['next_page'] == 2
    assert state['pagination_started_at'] == now
    assert state['pending_discovery_jobs'] == []
