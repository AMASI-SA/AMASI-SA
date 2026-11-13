"""Scheduled reconciliation contract: fixed windows, pagination, restart and locks."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient
from salla_integration import scheduled_reconciliation as scheduled, sync
from salla_integration.routes import _acquire_order_sync_lease, _release_order_sync_lease

NOW = datetime(2026, 11, 10, 21, tzinfo=timezone.utc)


def test_riyadh_slots_are_midnight_and_noon():
    assert scheduled.slot(NOW) != scheduled.slot(NOW - timedelta(seconds=1))
    assert scheduled.slot(NOW + timedelta(hours=12)) != scheduled.slot(NOW + timedelta(hours=12, seconds=-1))


@pytest.mark.asyncio
async def test_restart_same_slot_and_next_slot(monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    run = AsyncMock(return_value={'errors_count': 0, 'truncated': False})
    monkeypatch.setattr(scheduled, 'run_orders_sync', run)
    assert await scheduled.run_due(db, 'owner', now=NOW)
    assert not await scheduled.run_due(db, 'owner', now=NOW + timedelta(minutes=1))
    assert await scheduled.run_due(db, 'owner', now=NOW + timedelta(hours=12))
    assert run.await_count == 2
    assert run.await_args.kwargs['refresh_window'] == (NOW - timedelta(hours=12), NOW + timedelta(hours=12))


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
    await scheduled.run_due(db, 'owner', now=NOW + timedelta(hours=25))
    assert run.await_args_list[0].kwargs['refresh_window'] == run.await_args_list[1].kwargs['refresh_window']
    assert await scheduled.run_due(db, 'owner', now=NOW + timedelta(hours=25))


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
