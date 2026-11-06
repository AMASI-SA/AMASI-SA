import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient

from integrations.qoyod import scheduled_scan as scans
from integrations.qoyod.candidate_orders import build_candidate_audit
from integrations.qoyod_manual import auto_send
from qoyod_auto_unified import incremental


def order(ref, **extra):
    return {'user_id': 'owner', 'order_number': ref, 'order_date': '2026-11-01',
            'order_status': 'completed', 'payment_method': 'mada',
            'payment_status': 'paid', 'total_amount': 100, **extra}


def test_five_riyadh_slots_evenly_spaced_and_restart_stable():
    midnight = datetime(2026, 11, 4, 21, tzinfo=timezone.utc)
    keys = [scans.slot_key(midnight + timedelta(seconds=i)) for i in range(86400)]
    assert len(set(keys)) == 5
    assert [keys.count(k) for k in sorted(set(keys))] == [17280] * 5
    assert scans.slot_key(midnight + timedelta(days=1)) == keys[0] + 5


@pytest.mark.asyncio
async def test_manual_and_scheduled_claims_share_one_distributed_lease():
    db = AsyncMongoMockClient().db
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def execute(*args, **kwargs):
        calls.append(1)
        entered.set()
        await release.wait()
        return {'ok': True, 'counts': {'مطابق': 1}, 'rows': []}, {'count': 0, 'complete': True}

    results = await asyncio.gather(*[
        scans.request_scan(db, tenant='main', owner='owner', manual=bool(i % 2), executor=execute)
        for i in range(12)
    ])
    await entered.wait()
    assert sum(r['accepted'] for r in results) == 1
    assert len(calls) == 1
    release.set()
    await asyncio.gather(*list(scans._TASKS))
    assert (await scans.read_scan(db, tenant='main', owner='owner'))['report']['counts'] == {'مطابق': 1}
    assert not (await scans.request_scan(db, tenant='main', owner='owner', executor=execute))['accepted']


@pytest.mark.asyncio
async def test_failed_refresh_retains_last_success_and_wrong_owner_cannot_read_it():
    db = AsyncMongoMockClient().db
    saved = {'orders_user_id': 'owner', 'slot': scans.slot_key(scans.utcnow()),
             'updated_at': '2026-11-05T12:00:00+00:00', 'report': {'rows': [{'match': 'مطابق'}]},
             'invoice_presence': {'count': 8, 'complete': True}}
    await db.qoyod_full_scans.insert_one({'_id': 'main', 'last_success': saved})

    async def fail(*args, **kwargs):
        raise RuntimeError('private provider error must not leak')

    await scans.request_scan(db, tenant='main', owner='owner', manual=True, executor=fail)
    await asyncio.gather(*list(scans._TASKS))
    result = await scans.read_scan(db, tenant='main', owner='owner')
    assert result['report'] == saved['report']
    assert result['scan']['stale'] is True
    assert result['scan']['last_error'] == 'scan_failed'
    assert (await scans.read_scan(db, tenant='main', owner='other'))['report'] is None


@pytest.mark.asyncio
async def test_expired_or_replaced_scan_cannot_publish_success():
    db = AsyncMongoMockClient().db
    claim = await scans._claim(db, 'main', 'owner', True)

    async def replaced(*args, **kwargs):
        await db.qoyod_full_scans.update_one({'_id': 'main'}, {'$set': {'token': 'replacement'}})
        return {'rows': ['must not publish']}, {'count': 99}

    await scans._run(db, 'main', 'owner', claim, replaced)
    state = await db.qoyod_full_scans.find_one({'_id': 'main'})
    assert 'last_success' not in state
    assert state['token'] == 'replacement'


@pytest.mark.asyncio
async def test_discovery_is_exact_bounded_and_does_not_read_inbox(monkeypatch):
    db = AsyncMongoMockClient().db
    await db.unified_orders.insert_many([order(f'{i:04}') for i in range(250)])
    await db.unified_orders.insert_one(order('new'))
    await incremental.mark_dirty(db, 'owner', 'new')
    spy = AsyncMock(wraps=build_candidate_audit)
    audit = await incremental.discover(db, 'owner', spy)
    assert audit['discovery']['checked'] == 100
    assert 'new' in spy.call_args.kwargs['order_numbers']
    assert audit['scanned_rows']['unified_orders'] == 100
    assert audit['scanned_rows']['integration_inbox'] == 0
    assert audit['counts']['worker_candidates'] == 100


@pytest.mark.asyncio
async def test_recovery_checkpoint_survives_restart_and_wraps_for_direct_updates():
    db = AsyncMongoMockClient().db
    await db.unified_orders.insert_many([order(f'{i:04}') for i in range(250)])
    observed = []
    for _ in range(4):
        observed.append(await incremental.recover_page(db, 'owner'))
        await db.qoyod_followup_cursors.update_many({}, {'$unset': {'next_sweep_at': ''}})
    assert observed == [100, 100, 50, 0]
    assert await db.qoyod_order_followup.count_documents({}) == 250
    assert await incremental.recover_page(db, 'owner') == 100


@pytest.mark.asyncio
async def test_event_arriving_during_discovery_is_not_lost():
    db = AsyncMongoMockClient().db
    await db.unified_orders.insert_one(order('unpaid', payment_status='unpaid'))
    await incremental.mark_dirty(db, 'owner', 'unpaid')
    revision = (await db.qoyod_order_followup.find_one({}))['revision']

    async def concurrent_event(inner, **kwargs):
        result = await build_candidate_audit(inner, **kwargs)
        await incremental.mark_dirty(inner, 'owner', 'unpaid')
        return result

    await incremental.discover(db, 'owner', concurrent_event)
    saved = await db.qoyod_order_followup.find_one({'order_number': 'unpaid'})
    assert saved['revision'] != revision
    assert saved['priority'] == 0


@pytest.mark.asyncio
async def test_incremental_selection_keeps_payment_reference_and_batch_guards():
    db = AsyncMongoMockClient().db
    await db.unified_orders.insert_many([order(f'{i:04}') for i in range(20)] + [
        order('blocked-payment', payment_status='unpaid'),
        order('blocked-state', order_status='canceled'), order('exists'),
    ])
    await db.qoyod_invoices.insert_one({'user_id': 'main', 'source': 'plan_b_send',
        'reference': 'exists', 'qoyod_invoice_id': '1'})
    candidates, counts = await auto_send._load_candidate_rows(
        db, settings={'invoice_trigger_statuses': ['completed']}, orders_user_id='owner', batch_limit=500,
    )
    assert len(candidates) == 5
    assert not {'blocked-payment', 'blocked-state', 'exists'} & {r['order_number'] for r in candidates}
    assert counts['counts_scope'] == 'incremental_window'
    assert counts['authoritative_backlog_count'] is None


@pytest.mark.asyncio
async def test_followup_continues_while_full_scan_is_blocked():
    db = AsyncMongoMockClient().db
    gate = asyncio.Event()
    async def slow(*args, **kwargs):
        await gate.wait()
        return {'rows': []}, {'count': 0}
    await scans.request_scan(db, tenant='main', owner='owner', executor=slow)
    await db.unified_orders.insert_one(order('live'))
    await incremental.mark_dirty(db, 'owner', 'live')
    candidates, _ = await auto_send._load_candidate_rows(
        db, settings={'invoice_trigger_statuses': ['completed']}, orders_user_id='owner', batch_limit=5)
    assert [r['order_number'] for r in candidates] == ['live']
    gate.set()
    await asyncio.gather(*list(scans._TASKS))


@pytest.mark.asyncio
async def test_full_scan_exceeds_interactive_cap_in_cooperative_batches():
    from integrations.qoyod.candidate_orders import CandidateAuditScanLimitExceeded
    db = AsyncMongoMockClient().db
    await db.unified_orders.insert_many([order(str(i)) for i in range(4)])
    with pytest.raises(CandidateAuditScanLimitExceeded):
        await build_candidate_audit(db, orders_user_id='owner', markers_user_id='main', scan_limit=2)
    report = await build_candidate_audit(db, orders_user_id='owner', markers_user_id='main',
        scan_limit=2, full_scan=True, lightweight=True)
    assert report['scanned_rows']['unified_orders'] == 4
    assert report['scan_truncated'] is False


@pytest.mark.asyncio
async def test_live_and_recovery_both_receive_send_slots_under_backlog():
    db = AsyncMongoMockClient().db
    await db.unified_orders.insert_many([order(f'old-{i}', order_date='2026-10-01') for i in range(10)]
        + [order(f'new-{i}') for i in range(10)])
    for i in range(10):
        await incremental.mark_dirty(db, 'owner', f'new-{i}')
    selected, _ = await auto_send._load_candidate_rows(db,
        settings={'invoice_trigger_statuses': ['completed']}, orders_user_id='owner', batch_limit=5)
    assert sum(r['order_number'].startswith('new') for r in selected) == 3
    assert sum(r['order_number'].startswith('old') for r in selected) == 2


@pytest.mark.asyncio
async def test_executor_publishes_provider_verified_views_without_second_candidate_audit(monkeypatch):
    from types import SimpleNamespace
    from integrations.qoyod import credentials, routes, reconciliation_v2, candidate_orders
    db = AsyncMongoMockClient().db
    await db.qoyod_settings.insert_one({'user_id': 'main'})
    await db.unified_orders.insert_one(order('needs-invoice'))
    client = SimpleNamespace(list_invoices=AsyncMock(return_value={'invoices': [{
        'id': 42, 'reference': 'provider-only', 'issue_date': '2026-11-01',
        'total': 100, 'paid_amount': 100, 'status': 'Paid'}]}))
    monkeypatch.setattr(credentials, 'get_api_key', AsyncMock(return_value='synthetic-test-key'))
    monkeypatch.setattr(routes, '_build_qoyod_client_for', AsyncMock(return_value=client))
    spy = AsyncMock(wraps=candidate_orders.build_candidate_audit)
    monkeypatch.setattr(reconciliation_v2, 'build_candidate_audit', spy)
    await scans.request_scan(db, tenant='main', owner='owner', executor=scans.execute_scan)
    await asyncio.gather(*list(scans._TASKS))
    state = await scans.read_scan(db, tenant='main', owner='owner')
    assert state['scan']['last_error'] is None
    assert state['invoice_presence']['count'] == 1
    assert spy.await_count == 1
    display = await scans.read_unsent_view(db, tenant='main', owner='owner', from_date='2026-07-01')
    assert [r['order_number'] for r in display['orders']] == ['needs-invoice']
    assert display['snapshot_available'] is True
    assert spy.await_count == 1  # Display requests never rebuild the audit.
    assert client.list_invoices.await_count == 1


@pytest.mark.asyncio
async def test_http_display_never_starts_scan_and_native_employee_cannot_refresh(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from integrations.qoyod.routes import make_qoyod_router
    db = AsyncMongoMockClient().db
    await db.users.insert_many([{'id': 'owner', 'role': 'owner'}, {'id': 'employee', 'role': 'employee'}])
    await db.qoyod_settings.insert_one({'user_id': 'main', 'security_owner_id': 'owner'})
    user = {'id': 'owner', 'role': 'owner'}
    async def session():
        return user
    app = FastAPI()
    router = make_qoyod_router(db, session)
    display = next(r.path for r in router.routes if r.path.endswith('/reconciliation-report'))
    refresh = next(r.path for r in router.routes if r.path.endswith('/reconciliation-refresh'))
    app.include_router(router)
    request = AsyncMock(return_value={'report': None, 'scan': {'running': True}, 'accepted': True})
    monkeypatch.setattr(scans, 'request_scan', request)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get(display + '?sync_first=true')).status_code == 200
        request.assert_not_awaited()
        assert (await client.post(refresh)).status_code == 200
        request.assert_awaited_once()
        user['_mobile_actor_id'] = 'employee'
        assert (await client.post(refresh)).status_code == 403
        assert request.await_count == 1


@pytest.mark.asyncio
async def test_expired_worker_lease_does_not_authorize_new_send():
    db = AsyncMongoMockClient().db
    owner = await auto_send._acquire_lease(db)
    assert await auto_send._lease_is_current(db, owner)
    await db.qoyod_manual_auto_worker_leases.update_one({}, {'$set': {
        'lease_until': datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert not await auto_send._lease_is_current(db, owner)
