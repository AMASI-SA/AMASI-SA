"""Scheduled reconciliation contract: fixed windows, pagination, restart and locks."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient
from salla_integration import scheduled_reconciliation as scheduled, sync
from salla_integration.routes import _acquire_order_sync_lease, _release_order_sync_lease

NOW = datetime(2026, 11, 15, 7, tzinfo=timezone.utc)  # 10:00 Asia/Riyadh.


@pytest.fixture(autouse=True)
def schedule_clock(monkeypatch):
    monkeypatch.setattr(scheduled, 'now_utc', lambda: NOW)


def test_riyadh_daily_due_is_10_am_independent_of_host_timezone():
    from zoneinfo import ZoneInfo
    assert scheduled.scheduled_for(NOW) == datetime(2026, 11, 15, 7, tzinfo=timezone.utc)
    assert scheduled.scheduled_for(NOW.astimezone(ZoneInfo('America/New_York'))) == NOW
    assert scheduled.slot(NOW) == '2026-11-15'
    assert scheduled.slot(NOW + timedelta(days=1)) == '2026-11-16'


@pytest.mark.asyncio
async def test_restart_same_slot_and_next_slot(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    run = AsyncMock(return_value={'errors_count': 0, 'truncated': False})
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    assert await scheduled.run_due(db, 'owner', now=NOW)
    assert not await scheduled.run_due(db, 'owner', now=NOW + timedelta(minutes=1))
    assert not await scheduled.run_due(db, 'owner', now=NOW + timedelta(hours=12))
    assert await scheduled.run_due(db, 'owner', now=NOW + timedelta(days=1))
    assert run.await_count == 2
    assert run.await_args.kwargs['refresh_window'] == (NOW, NOW + timedelta(days=1))
    assert run.await_args.kwargs['existing_only'] is True


@pytest.mark.asyncio
async def test_failed_window_survives_restart_and_does_not_claim_success(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    monkeypatch.setattr(scheduled, 'now_utc', lambda: NOW)
    run = AsyncMock(side_effect=[{'errors_count': 1}, {'errors_count': 0, 'truncated': False}])
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    await scheduled.run_due(db, 'owner', now=NOW)
    state = await db[scheduled.STATE].find_one({'_id': 'owner'})
    assert state['status'] == 'failed' and 'completed_slot' not in state
    assert not await scheduled.run_due(db, 'owner', now=NOW + timedelta(minutes=14))
    await scheduled.run_due(db, 'owner', now=NOW + timedelta(minutes=15))
    assert run.await_args_list[0].kwargs['refresh_window'] == run.await_args_list[1].kwargs['refresh_window']
    assert not await scheduled.run_due(db, 'owner', now=NOW + timedelta(minutes=16))


@pytest.mark.asyncio
@pytest.mark.parametrize('at', [NOW - timedelta(hours=7), NOW - timedelta(seconds=1),
                              NOW + timedelta(minutes=5), NOW + timedelta(hours=2)])
async def test_no_new_cycle_before_or_after_launch_window(monkeypatch, at):
    db = AsyncMongoMockClient(tz_aware=True).db
    run = AsyncMock()
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    assert not await scheduled.run_due(db, 'owner', now=at)
    run.assert_not_awaited()
    state = await db[scheduled.STATE].find_one({'_id': 'owner'})
    assert state['mode'] == 'update_only'
    assert state['schedule_riyadh'] == ['10:00']
    assert state['next_due_at'] == (NOW if at < NOW else NOW + timedelta(days=1))


@pytest.mark.asyncio
async def test_old_12h_pending_window_is_not_replayed_on_deployment(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    await db[scheduled.STATE].insert_one({'_id': 'owner', 'status': 'failed',
        'completed_slot': 41500, 'retry_at': NOW - timedelta(days=1),
        'pending_window': {'start': NOW - timedelta(days=3), 'end': NOW - timedelta(days=2), 'slot': 41499}})
    run = AsyncMock(return_value={'errors_count': 0, 'truncated': False})
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    assert not await scheduled.run_due(db, 'owner', now=NOW - timedelta(hours=7))
    state = await db[scheduled.STATE].find_one({'_id': 'owner'})
    assert state['next_due_at'] == NOW
    assert 'pending_window' not in state and 'retry_at' not in state
    assert await scheduled.run_due(db, 'owner', now=NOW)
    assert run.await_args.kwargs['refresh_window'] == (NOW - timedelta(days=1), NOW)


@pytest.mark.asyncio
async def test_missed_cycle_is_not_caught_up_and_next_day_has_new_window(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    run = AsyncMock(side_effect=[{'errors_count': 1}, {'errors_count': 0}])
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    await scheduled.run_due(db, 'owner', now=NOW)
    assert not await scheduled.run_due(db, 'owner', now=NOW + timedelta(hours=2))
    assert not await scheduled.run_due(db, 'owner', now=NOW + timedelta(hours=23))
    assert await scheduled.run_due(db, 'owner', now=NOW + timedelta(days=1))
    assert run.await_args.kwargs['refresh_window'] == (NOW, NOW + timedelta(days=1))


@pytest.mark.asyncio
async def test_retries_are_bounded_to_same_daily_cycle(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    run = AsyncMock(return_value={'errors_count': 1})
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    for minutes in (0, 15, 30):
        at = NOW + timedelta(minutes=minutes)
        monkeypatch.setattr(scheduled, 'now_utc', lambda at=at: at)
        assert await scheduled.run_due(db, 'owner', now=at)
    assert not await scheduled.run_due(db, 'owner', now=NOW + timedelta(minutes=45))
    state = await db[scheduled.STATE].find_one({'_id': 'owner'})
    assert state['status'] == 'failed' and state['attempts'] == 3
    assert 'last_success_at' not in state
    assert run.await_count == 3
    assert {call.kwargs['refresh_window'] for call in run.await_args_list} == {(NOW-timedelta(days=1), NOW)}


@pytest.mark.asyncio
async def test_cancellation_preserves_retry_window_and_releases_both_leases(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    started = asyncio.Event()
    async def run(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    task = asyncio.create_task(scheduled.run_due(db, 'owner', now=NOW))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = await db[scheduled.STATE].find_one({'_id': 'owner'})
    assert state['status'] == 'interrupted' and state['pending_window']['end'] == NOW
    for owner in ('owner', 'owner:catalogue'):
        token, _ = await _acquire_order_sync_lease(db, owner)
        assert token
        await _release_order_sync_lease(db, owner, token)


@pytest.mark.asyncio
async def test_timeout_is_partial_and_does_not_claim_success(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    async def run(*args, **kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    monkeypatch.setattr(scheduled, 'RUN_TIMEOUT_SECONDS', 0.01)
    assert await scheduled.run_due(db, 'owner', now=NOW)
    state = await db[scheduled.STATE].find_one({'_id': 'owner'})
    assert state['status'] == 'failed' and state['last_error_type'] == 'TimeoutError'
    assert 'last_success_at' not in state


@pytest.mark.asyncio
async def test_manual_lease_prevents_concurrent_scheduled_import(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    token, _ = await _acquire_order_sync_lease(db, 'owner')
    run = AsyncMock()
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    assert not await scheduled.run_due(db, 'owner', now=NOW)
    run.assert_not_awaited()
    await _release_order_sync_lease(db, 'owner', token)


@pytest.mark.asyncio
async def test_two_replicas_start_one_run(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    started, release = asyncio.Event(), asyncio.Event()
    async def execute(*a, **kw):
        started.set()
        await release.wait()
        return {'errors_count': 0, 'truncated': False}
    monkeypatch.setattr(scheduled, 'run_orders_sync', execute)
    first = asyncio.create_task(scheduled.run_due(db, 'owner', now=NOW))
    await started.wait()
    assert not await scheduled.run_due(db, 'owner', now=NOW)
    release.set()
    assert await first


@pytest.mark.asyncio
async def test_short_order_pages_still_refresh_all_orders_and_items(monkeypatch):
    end = datetime(2026, 11, 10, 9, tzinfo=timezone.utc)
    def row(id, date):
        return {'id': id, 'reference_id': str(id), 'date': {'date': date, 'timezone': 'Asia/Riyadh'}, 'status': {'slug': 'in_progress'}}
    pages = [
        {'data': [row(1, '2026-11-09 12:00:00'), row(2, '2026-11-09 11:59:59')], 'pagination': {'currentPage': 1, 'totalPages': 2}},
        {'data': [row(3, '2026-11-10 12:00:00')], 'pagination': {'currentPage': 2, 'totalPages': 2}},
    ]
    calls = []
    async def provider(db, user, method, path, **kwargs):
        calls.append((method, path, kwargs['params']))
        assert method == 'GET'
        if path == '/orders':
            return pages[kwargs['params']['page'] - 1]
        assert path == '/orders/items'
        return {'data': [{'id': 'item-' + kwargs['params']['order_id'], 'quantity': 3}]}
    persist = AsyncMock(return_value={'created': True})
    finish = AsyncMock()
    monkeypatch.setattr(sync, 'call_salla', provider)
    monkeypatch.setattr(sync, '_salla_order_to_doc', lambda r: {'order_number': r['reference_id'], 'products': r['items'], 'order_status_slug': r['status']['slug']})
    monkeypatch.setattr(sync, '_enrich_order_receiving_bank', AsyncMock(side_effect=lambda db,u,r: r))
    monkeypatch.setattr(sync, '_persist_sync_component_snapshot', persist)
    monkeypatch.setattr(sync, '_refresh_plan_b_status_snapshot', AsyncMock())
    monkeypatch.setattr(sync, 'finish_sync_log', finish)
    monkeypatch.setattr(sync.asyncio, 'sleep', AsyncMock())
    from salla_integration import paid_sar_sync
    from resource_governor import governor
    monkeypatch.setattr(paid_sar_sync, 'observe_list_evidence', AsyncMock())
    monkeypatch.setattr(governor, 'peek', lambda: ('allowed', None))
    result = await sync.run_orders_sync(object(), 'owner', log_id='run', refresh_window=(end-timedelta(hours=24), end))
    assert result['created'] == 2 and result['skipped'] == 1 and not result['truncated']
    assert [c.args[2]['order_number'] for c in persist.await_args_list] == ['1', '3']
    assert all(c.args[2]['products'][0]['quantity'] == 3 for c in persist.await_args_list)
    assert all(c.args[2]['order_status_slug'] == 'in_progress' for c in persist.await_args_list)
    assert [c[2]['page'] for c in calls if c[1] == '/orders'] == [1, 2]
    assert all(c[2]['from_date'] == '2026-11-09' and c[2]['to_date'] == '2026-11-10' for c in calls if c[1] == '/orders')


@pytest.mark.asyncio
async def test_items_explicit_pages_and_invalid_payload(monkeypatch):
    monkeypatch.setattr(sync.asyncio, 'sleep', AsyncMock())
    monkeypatch.setattr(sync, 'call_salla', AsyncMock(side_effect=[
        {'data': [{'id': 1, 'quantity': 1}], 'pagination': {'currentPage': 1, 'totalPages': 2}},
        {'data': [{'id': 2, 'quantity': 1}], 'pagination': {'currentPage': 2, 'totalPages': 2}},
    ]))
    assert [r['id'] for r in await sync._fetch_scheduled_order_items(None, 'owner', 'id')] == [1, 2]
    monkeypatch.setattr(sync, 'call_salla', AsyncMock(return_value={'data': [None]}))
    with pytest.raises(ValueError):
        await sync._fetch_scheduled_order_items(None, 'owner', 'id')

@pytest.mark.asyncio
async def test_catalogue_refresh_preserves_local_costs_and_resumes(monkeypatch):
    from salla_integration import scheduled_catalogue as cat
    db = AsyncMongoMockClient(tz_aware=True).db
    await db[cat.PRODUCTS].insert_one({'user_id': 'owner', 'salla_product_id': '1',
        'name': 'old', 'local_cost': 55, 'component_links': ['local'], 'updated_at': NOW})
    await db.product_cost_profiles_v2.insert_one({'user_id': 'owner', 'base_cost': 55})
    calls = []
    async def provider(db, user, method, path, **kw):
        calls.append(path)
        assert method == 'GET'
        if path == '/products':
            page = kw['params']['page']
            return {'data': [{'id': page}], 'pagination': {'currentPage': page, 'totalPages': 2}}
        return {'data': {'id': int(path.rsplit('/', 1)[1]), 'name': 'new', 'price': 80,
            'quantity': 7, 'options': [{'id': 12, 'name': 'color', 'values': [{'id': 13, 'name': 'blue'}]}],
            'variants': [{'id': 14, 'quantity': 6, 'price': 81}]}}
    monkeypatch.setattr(cat, 'call_salla', provider)
    monkeypatch.setattr(cat.governor, 'peek', lambda: ('allowed', None))
    monkeypatch.setattr(cat.asyncio, 'sleep', AsyncMock())
    first = await cat.refresh_catalogue(db, 'owner', run_slot=1)
    assert not first['truncated'] and first['errors_count'] == 0
    product = await db[cat.PRODUCTS].find_one({'salla_product_id': '1'})
    assert product['local_cost'] == 55 and product['component_links'] == ['local']
    assert product['quantity'] == 7 and product['options'][0]['name'] == 'color'
    assert product['variants'][0]['quantity'] == 6
    again = await cat.refresh_catalogue(db, 'owner', run_slot=1)
    assert again['unchanged'] == 2 and calls.count('/products/1') == 1
    await cat.refresh_catalogue(db, 'owner', run_slot=2)
    assert calls.count('/products/1') == 2
    assert (await db.product_cost_profiles_v2.find_one({}))['base_cost'] == 55


@pytest.mark.asyncio
async def test_catalogue_partial_page_never_archives_local_products(monkeypatch):
    from salla_integration import scheduled_catalogue as cat
    db = AsyncMongoMockClient(tz_aware=True).db
    await db[cat.PRODUCTS].insert_one({'user_id': 'owner', 'salla_product_id': 'keep', 'archived': False})
    monkeypatch.setattr(cat, 'call_salla', AsyncMock(return_value={
        'data': [], 'pagination': {'currentPage': 1, 'totalPages': 2}}))
    monkeypatch.setattr(cat.governor, 'peek', lambda: ('allowed', None))
    result = await cat.refresh_catalogue(db, 'owner', run_slot=1)
    assert result['truncated']
    assert (await db[cat.PRODUCTS].find_one({}))['archived'] is False


@pytest.mark.asyncio
async def test_catalogue_schedule_is_independent_of_order_completion(monkeypatch):
    from salla_integration import scheduled_catalogue as cat
    db = AsyncMongoMockClient(tz_aware=True).db
    await db[scheduled.STATE].insert_one({'_id': 'owner', 'completed_slot': scheduled.slot(NOW)})
    run = AsyncMock(return_value={'errors_count': 0, 'truncated': False})
    monkeypatch.setattr(cat, 'refresh_catalogue', run)
    assert await scheduled.run_due(db, 'owner', now=NOW, catalogue=True)
    assert not await scheduled.run_due(db, 'owner', now=NOW, catalogue=True)
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_catalogue_continues_admitted_cycle_after_launch_grace(monkeypatch):
    from salla_integration import scheduled_catalogue as cat
    db = AsyncMongoMockClient(tz_aware=True).db
    monkeypatch.setattr(scheduled, 'run_orders_sync', AsyncMock(return_value={'errors_count': 0}))
    catalogue = AsyncMock(return_value={'errors_count': 0})
    monkeypatch.setattr(cat, 'refresh_catalogue', catalogue)
    assert await scheduled.run_due(db, 'owner', now=NOW)
    assert await scheduled.run_due(db, 'owner', now=NOW + timedelta(minutes=12), catalogue=True)
    assert catalogue.await_args.kwargs['refresh_window'] == (NOW-timedelta(days=1), NOW)
    assert catalogue.await_args.kwargs['existing_only'] is True
    assert not await scheduled.run_due(db, 'owner', now=NOW + timedelta(hours=1), catalogue=True)


@pytest.mark.asyncio
async def test_order_and_catalogue_steps_share_the_manual_lease(monkeypatch):
    from salla_integration import scheduled_catalogue as cat
    db = AsyncMongoMockClient(tz_aware=True).db
    started, release = asyncio.Event(), asyncio.Event()
    async def run(*args, **kwargs):
        started.set()
        await release.wait()
        return {'errors_count': 0}
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    catalogue = AsyncMock(return_value={'errors_count': 0})
    monkeypatch.setattr(cat, 'refresh_catalogue', catalogue)
    first = asyncio.create_task(scheduled.run_due(db, 'owner', now=NOW))
    await started.wait()
    assert not await scheduled.run_due(db, 'owner', now=NOW, catalogue=True)
    manual_token, _ = await _acquire_order_sync_lease(db, 'owner')
    assert manual_token is None
    release.set()
    await first
    catalogue.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_catalogue_lease_is_respected_during_migration(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    token, _ = await _acquire_order_sync_lease(db, 'owner:catalogue')
    run = AsyncMock()
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    assert not await scheduled.run_due(db, 'owner', now=NOW)
    run.assert_not_awaited()
    await _release_order_sync_lease(db, 'owner:catalogue', token)
    owner_token, _ = await _acquire_order_sync_lease(db, 'owner')
    assert owner_token
    await _release_order_sync_lease(db, 'owner', owner_token)


@pytest.mark.asyncio
async def test_missed_launch_updates_durable_next_due(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    run = AsyncMock()
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    assert not await scheduled.run_due(db, 'owner', now=NOW-timedelta(minutes=1))
    assert not await scheduled.run_due(db, 'owner', now=NOW+timedelta(minutes=6))
    assert (await db[scheduled.STATE].find_one({'_id': 'owner'}))['next_due_at'] == NOW+timedelta(days=1)
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_catalogue_time_completes_cycle_as_partial_without_retry(monkeypatch):
    from salla_integration import scheduled_catalogue as cat
    db = AsyncMongoMockClient(tz_aware=True).db
    run = AsyncMock(return_value={'errors_count': 0, 'truncated': False, 'incomplete_count': 3})
    monkeypatch.setattr(cat, 'refresh_catalogue', run)
    assert await scheduled.run_due(db, 'owner', now=NOW, catalogue=True)
    assert not await scheduled.run_due(db, 'owner', now=NOW+timedelta(minutes=15), catalogue=True)
    state = await db[scheduled.STATE].find_one({'_id': 'owner:catalogue'})
    assert state['status'] == 'partial' and state['last_result']['incomplete_count'] == 3
    assert 'last_success_at' not in state


@pytest.mark.asyncio
async def test_update_only_catalogue_limits_details_and_preserves_local_fields(monkeypatch):
    from salla_integration import scheduled_catalogue as cat
    db = AsyncMongoMockClient(tz_aware=True).db
    for pid in ('1', '2', '3', '5'):
        await db[cat.PRODUCTS].insert_one({'user_id': 'owner', 'salla_product_id': pid,
            'name': 'old', 'local_cost': 55, 'component_links': ['keep'],
            'options': [{'id': 'local-option'}], 'options_count': 1, 'updated_at': NOW})
    recent = (NOW-timedelta(hours=1)).isoformat()
    rows = [{'id': 1, 'updated_at': recent}, {'id': 2, 'updated_at': (NOW-timedelta(days=2)).isoformat()},
            {'id': 3}, {'id': 4, 'updated_at': recent}, {'id': 5, 'updated_at': '2026-11-15 09:00:00'}]
    calls = []
    async def provider(db, owner, method, path, **kwargs):
        assert method == 'GET'
        calls.append((path, kwargs))
        if path == '/products':
            assert kwargs['params'] == {'page': 1, 'per_page': 30, 'format': 'light'}
            return {'data': rows, 'pagination': {'currentPage': 1, 'totalPages': 1}}
        assert path == '/products/1'
        return {'data': {'id': 1, 'name': 'fresh', 'updated_at': recent, 'price': 80}}
    monkeypatch.setattr(cat, 'call_salla', provider)
    monkeypatch.setattr(cat.governor, 'peek', lambda: ('allowed', None))
    monkeypatch.setattr(cat.asyncio, 'sleep', AsyncMock())
    result = await cat.refresh_catalogue(db, 'owner', run_slot='2026-11-15',
        refresh_window=(NOW-timedelta(days=1), NOW), existing_only=True)
    assert result['updated'] == 1 and result['incomplete_count'] == 2 and result['missing_existing'] == 1
    assert [path for path, _ in calls] == ['/products', '/products/1']
    product = await db[cat.PRODUCTS].find_one({'user_id': 'owner', 'salla_product_id': '1'})
    assert product['name'] == 'fresh' and product['local_cost'] == 55
    assert product['component_links'] == ['keep'] and product['options'] == [{'id': 'local-option'}]
    assert await db[cat.PRODUCTS].count_documents({}) == 4
    assert (await db[cat.SYNC_RUNS].find_one({'id': result['run_id']}))['status'] == 'partial'


@pytest.mark.asyncio
async def test_update_only_catalogue_does_not_recreate_deleted_product(monkeypatch):
    from salla_integration import scheduled_catalogue as cat
    db = AsyncMongoMockClient(tz_aware=True).db
    await db[cat.PRODUCTS].insert_one({'user_id': 'owner', 'salla_product_id': '1', 'updated_at': NOW})
    recent = (NOW-timedelta(hours=1)).isoformat()
    async def provider(db, owner, method, path, **kwargs):
        if path == '/products':
            return {'data': [{'id': 1, 'updated_at': recent}], 'pagination': {'currentPage': 1, 'totalPages': 1}}
        await db[cat.PRODUCTS].delete_many({})
        return {'data': {'id': 1, 'name': 'fresh', 'updated_at': recent}}
    monkeypatch.setattr(cat, 'call_salla', provider)
    monkeypatch.setattr(cat.governor, 'peek', lambda: ('allowed', None))
    monkeypatch.setattr(cat.asyncio, 'sleep', AsyncMock())
    result = await cat.refresh_catalogue(db, 'owner', run_slot='2026-11-15',
        refresh_window=(NOW-timedelta(days=1), NOW), existing_only=True)
    assert result['errors_count'] == 1 and result['updated'] == 0
    assert await db[cat.PRODUCTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_update_only_orders_fetch_items_only_for_existing_owner_rows(monkeypatch):
    from salla_integration import paid_sar_sync
    from resource_governor import governor
    db = AsyncMongoMockClient(tz_aware=True).db
    await db.unified_orders.insert_many([
        {'user_id': 'owner', 'order_number': 'one'}, {'user_id': 'other', 'order_number': 'two'}])
    rows = [{'id': pid, 'reference_id': number,
             'date': {'date': '2026-11-15 09:00:00', 'timezone': 'Asia/Riyadh'},
             'updated_at': (NOW-timedelta(hours=1)).isoformat()}
            for pid, number in [('1', 'one'), ('2', 'two'), ('3', 'new')]]
    calls = []
    async def provider(db, user, method, path, **kwargs):
        calls.append((path, kwargs['params']))
        assert method == 'GET'
        if path == '/orders':
            return {'data': rows, 'pagination': {'currentPage': 1, 'totalPages': 1}}
        assert path == '/orders/items' and kwargs['params']['order_id'] == '1'
        return {'data': [{'id': 'line-one', 'quantity': 1}]}
    persist = AsyncMock(return_value={'created': False})
    snapshot = AsyncMock()
    monkeypatch.setattr(sync, 'call_salla', provider)
    monkeypatch.setattr(sync, '_salla_order_to_doc', lambda row: {'order_number': row['reference_id']})
    monkeypatch.setattr(sync, '_enrich_order_receiving_bank', AsyncMock(side_effect=lambda db,u,row: row))
    monkeypatch.setattr(sync, '_persist_sync_component_snapshot', persist)
    monkeypatch.setattr(sync, '_refresh_plan_b_status_snapshot', snapshot)
    monkeypatch.setattr(paid_sar_sync, 'observe_list_evidence', AsyncMock())
    monkeypatch.setattr(governor, 'peek', lambda: ('allowed', None))
    monkeypatch.setattr(sync.asyncio, 'sleep', AsyncMock())
    result = await sync.run_orders_sync(db, 'owner', refresh_window=(NOW-timedelta(days=1), NOW), existing_only=True)
    assert result['created'] == 0 and result['updated'] == 1 and result['missing_existing'] == 2
    assert persist.await_args.kwargs['existing_only'] is True
    assert snapshot.await_args.kwargs['allow_handoff_transition'] is False
    assert [path for path, _ in calls] == ['/orders', '/orders/items']
    assert await db.unified_orders.count_documents({}) == 2


@pytest.mark.asyncio
async def test_status_projection_is_preserved_without_carrier_handoff(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    handoff = AsyncMock()
    monkeypatch.setattr(sync, 'advance_carrier_handoff_from_salla_status', handoff)
    await sync._refresh_plan_b_status_snapshot(db, 'owner', 'one',
        {'order_status_slug': 'completed', 'order_status': 'تم التنفيذ'}, allow_handoff_transition=False)
    handoff.assert_not_awaited()
    row = await db.integration_inbox.find_one({'user_id': 'owner', 'salla_order_number': 'one'})
    assert row['no_qoyod_send'] is True and row['auto_send_allowed'] is False
    assert row['canonical_payload']['order_status'] == 'completed'


@pytest.mark.asyncio
async def test_existing_only_writer_never_inserts_missing_order_or_catalogue():
    import orders_db
    from tests.test_salla_products_projection import source, item
    db = AsyncMongoMockClient(tz_aware=True).db
    raw = source([item('line-one')])
    doc = sync._salla_order_to_doc(raw)
    result = await orders_db._upsert_order(db, 'owner', raw['reference_id'], doc,
        'salla_direct', raw, None, existing_only=True)
    assert result['skipped'] and not result['created']
    assert await db.unified_orders.count_documents({}) == 0
    await db.unified_orders.insert_one({'user_id': 'owner', 'order_number': raw['reference_id']})
    result = await orders_db._upsert_order(db, 'owner', raw['reference_id'], doc,
        'salla_direct', raw, None, existing_only=True)
    assert not result['created']
    assert await db.products.count_documents({}) == 0
    assert (await db.unified_orders.find_one({}))['qoyod_followup_dirty'] is True


@pytest.mark.asyncio
async def test_existing_only_writer_noop_keeps_audit_and_sparse_customer_choices():
    from copy import deepcopy
    import orders_db
    from tests.test_salla_products_projection import source, item
    db = AsyncMongoMockClient(tz_aware=True).db
    raw = source([item('line-one')])
    await db.unified_orders.insert_one({'user_id': 'owner', 'order_number': raw['reference_id']})
    async def save(payload):
        return await orders_db._upsert_order(db, 'owner', raw['reference_id'],
            sync._salla_order_to_doc(payload), 'salla_direct', payload, None, existing_only=True)
    await save(raw)
    await db.unified_orders.update_one({}, {'$set': {'g47_salla_snapshot': {'component_pending': False}}})
    before = await db.unified_orders.find_one({})
    assert (await save(raw))['unchanged'] is True
    assert await db.unified_orders.find_one({}) == before
    sparse = deepcopy(raw)
    sparse['updated_at'] = '2026-11-13T09:06:00Z'
    sparse['items'][0].pop('options')
    await save(sparse)
    after = await db.unified_orders.find_one({})
    assert after['products'][0]['options'] == before['products'][0]['options']
    assert after['raw_by_source']['salla_direct']['items'][0]['options'] == raw['items'][0]['options']
    assert await db.products.count_documents({}) == 0


@pytest.mark.asyncio
async def test_existing_only_snapshot_checks_presence_at_transaction_boundary(monkeypatch):
    import fulfillment_v2_routes as fulfillment
    db = AsyncMongoMockClient(tz_aware=True).db
    writer = AsyncMock()
    async def fenced(db, **kwargs):
        assert kwargs['allow_delivery_observation'] is False
        await kwargs['before_persist'](db)
        await writer()
        return await kwargs['persist'](db)
    monkeypatch.setattr(fulfillment, 'persist_component_source_snapshot', fenced)
    result = await sync._persist_sync_component_snapshot(db, 'owner', {'order_number': 'gone'},
        {'id': 'native', 'reference_id': 'gone'}, existing_only=True)
    assert result['skipped'] and result['reason'] == 'existing_order_missing'
    writer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('local_clock', ['webhook', 'source_fence'])
@pytest.mark.parametrize('provider_clock', ['recent', 'outside', 'missing', 'customer_only', 'naive'])
async def test_older_created_order_needs_verified_provider_update_in_window(monkeypatch, local_clock, provider_clock):
    from salla_integration import paid_sar_sync
    from resource_governor import governor
    db = AsyncMongoMockClient(tz_aware=True).db
    existing = {'user_id': 'owner', 'order_number': 'old-order', 'order_date': '2026-06-01'}
    if local_clock == 'webhook':
        existing['salla_webhook_received_at'] = NOW-timedelta(minutes=30)
    else:
        existing['g47_salla_snapshot'] = {'source_updated_at': (NOW-timedelta(minutes=30)).isoformat(timespec='microseconds')}
    await db.unified_orders.insert_one(existing)
    raw = {'id': 'old-native', 'reference_id': 'old-order',
           'date': {'date': '2026-06-01 10:00:00', 'timezone': 'Asia/Riyadh'}}
    if provider_clock == 'recent':
        raw['updated_at'] = (NOW-timedelta(minutes=20)).isoformat()
    elif provider_clock == 'outside':
        raw['updated_at'] = (NOW-timedelta(days=2)).isoformat()
    elif provider_clock == 'customer_only':
        raw['customer'] = {'updated_at': (NOW-timedelta(minutes=20)).isoformat()}
    elif provider_clock == 'naive':
        raw['updated_at'] = '2026-11-15 09:40:00'
    calls = []
    async def provider(db, owner, method, path, **kwargs):
        assert method == 'GET'
        params = kwargs.get('params', {})
        assert 'updated_at_gt' not in params
        calls.append((path, params))
        if path == '/orders' and 'reference_id' not in params:
            assert params['from_date'] == '2026-11-14' and params['to_date'] == '2026-11-15'
            return {'data': [], 'pagination': {'currentPage': 1, 'totalPages': 0}}
        if path == '/orders':
            assert params['reference_id'] == 'old-order'
            return {'data': [raw]}
        if path == '/orders/old-native':
            # The details fallback still cannot invent a missing order clock.
            assert provider_clock in {'missing', 'customer_only', 'naive'}
            return {'data': raw}
        assert path == '/orders/items' and provider_clock == 'recent'
        return {'data': [{'id': 'old-line', 'quantity': 1}]}
    persist = AsyncMock(return_value={'created': False})
    monkeypatch.setattr(sync, 'call_salla', provider)
    monkeypatch.setattr(sync, '_persist_sync_component_snapshot', persist)
    monkeypatch.setattr(sync, '_enrich_order_receiving_bank', AsyncMock(side_effect=lambda db,u,row: row))
    monkeypatch.setattr(sync, '_refresh_plan_b_status_snapshot', AsyncMock())
    monkeypatch.setattr(paid_sar_sync, 'observe_list_evidence', AsyncMock())
    monkeypatch.setattr(governor, 'peek', lambda: ('allowed', None))
    monkeypatch.setattr(sync.asyncio, 'sleep', AsyncMock())
    result = await sync.run_orders_sync(db, 'owner', refresh_window=(NOW-timedelta(days=1), NOW), existing_only=True)
    assert result['targeted_lookups'] == 1 and result['coverage_complete'] is False
    assert result['coverage_reason'] == 'provider_modified_since_filter_unavailable'
    assert result['created'] == 0
    if provider_clock == 'recent':
        assert result['updated'] == 1
        persist.assert_awaited_once()
        assert persist.await_args.kwargs['existing_only'] is True
        assert [path for path, _ in calls] == ['/orders', '/orders', '/orders/items']
    else:
        persist.assert_not_awaited()
        assert result['updated'] == 0
        assert result['incomplete_count'] == (0 if provider_clock == 'outside' else 1)
        assert [path for path, _ in calls] == (
            ['/orders', '/orders'] if provider_clock == 'outside'
            else ['/orders', '/orders', '/orders/old-native'])
