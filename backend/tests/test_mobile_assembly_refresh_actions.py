import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
import mobile_order_refresh as source
import assembly_manual_label_refresh as labels
import preparation_piece_operations as ops
PIECE = {'piece_id': 'ab' * 16, 'order_number': '300000001',
    'status': ops.PIECE_STATUS_RECEIVED, 'execution_status': 'received_from_supplier',
    'supplier_dispatch_status': 'received', 'received_at': '2026-11-08T00:00:00Z',
    'services': [], 'responsible_employee_id': 'prep'}


@pytest.mark.asyncio
@pytest.mark.parametrize('page', ['app.page.orders', 'app.page.assembly_shipping'])
async def test_manual_refresh_scoped_and_no_automatic_side_effects(monkeypatch, page):
    db = AsyncMongoMockClient().test
    await db.users.insert_one({'id': 'employee', 'created_by': 'merchant'})
    monkeypatch.setattr(source, 'mobile_app_access_for_user', AsyncMock(return_value={'enabled': True, 'permissions': [page]}))
    calls = []
    async def refresh(db_arg, owner, number, **kw):
        assert owner == 'merchant' and number == '123'
        assert kw['force'] is True
        assert kw['allow_auto_fulfillment'] is kw['allow_delivery_observation'] is kw['allow_billing_eligibility'] is False
        await kw['before_persist'](db_arg)
        calls.append(number)
        return {'ok': True, 'found': True, 'updated': True, 'order_number': number}
    monkeypatch.setattr(source, 'refresh_order_from_salla', refresh)
    user = {'id': 'merchant', '_mobile_owner_id': 'merchant', '_mobile_actor_id': 'employee'}
    assert (await source.refresh_mobile_order(db, user, '123', page=page))['updated']
    assert calls == ['123']
    await db.users.update_one({'id': 'employee'}, {'$set': {'created_by': 'other'}})
    with pytest.raises(HTTPException) as exc:
        await source.refresh_mobile_order(db, user, '123', page=page)
    assert exc.value.status_code == 403 and calls == ['123']


@pytest.mark.asyncio
async def test_revocation_during_refresh_prevents_persist(monkeypatch):
    db = AsyncMongoMockClient().test
    await db.users.insert_one({'id': 'employee', 'created_by': 'merchant'})
    monkeypatch.setattr(source, 'mobile_app_access_for_user', AsyncMock(return_value={'enabled': True, 'permissions': ['app.page.orders']}))
    async def refresh(db_arg, *_args, **kw):
        await db.users.update_one({'id': 'employee'}, {'$set': {'disabled': True}})
        await kw['before_persist'](db_arg)
        pytest.fail('revoked user persisted')
    monkeypatch.setattr(source, 'refresh_order_from_salla', refresh)
    with pytest.raises(HTTPException):
        await source.refresh_mobile_order(db, {'id':'merchant','_mobile_actor_id':'employee'}, '123', page='app.page.orders')


@pytest.mark.asyncio
async def test_manual_label_single_flight_and_unlock(monkeypatch):
    db = AsyncMongoMockClient().test
    await db.order_review_workflows.insert_one({'user_id':'merchant','order_number':'123','assembly_status':'completed'})
    snapshot = {'source': {'order_status':'in_progress'}, 'raw': {'status':'in_progress'}, 'pieces': [{'assembly_status':'ready'}]}
    monkeypatch.setattr(labels, '_capture', AsyncMock(return_value=snapshot))
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def label(*args, **kwargs):
        calls.append(kwargs)
        started.set()
        await release.wait()
        return {'ok':True,'ready':False,'order_status_completed':True}
    monkeypatch.setattr(labels, 'carrier_label', label)
    auth = AsyncMock()
    first = asyncio.create_task(labels.complete_and_refresh(db, 'merchant', '123', auth))
    await asyncio.wait_for(asyncio.shield(first) if first.done() else started.wait(), 2)
    with pytest.raises(HTTPException) as exc:
        await labels.complete_and_refresh(db, 'merchant', '123', auth)
    assert exc.value.status_code == 409
    release.set()
    assert (await first)['print_state'] == 'pending'
    assert calls == [{'issue':False,'completed_only':True,'explicit_complete':True}]
    row = await db.order_review_workflows.find_one({'order_number':'123'})
    assert 'manual_label_refresh' not in row
    assert 'assembly_delivery' not in row  # Do not create an incomplete legacy outbox.
    await labels.complete_and_refresh(db, 'merchant', '123', auth)
    assert len(calls) == 2  # A new explicit action is allowed, not an automatic retry.


@pytest.mark.asyncio
async def test_label_incomplete_or_cancelled_never_dispatches(monkeypatch):
    monkeypatch.setattr(labels, 'carrier_label', AsyncMock())
    for snap in [None, {'source':{'order_status':'cancelled'}, 'raw': {'status':'cancelled'}}]:
        monkeypatch.setattr(labels, '_capture', AsyncMock(return_value=snap))
        with pytest.raises(HTTPException):
            await labels.complete_and_refresh(None,'merchant','123',AsyncMock())
    labels.carrier_label.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('optional', [False, True])
async def test_optional_ready_writes_no_fictitious_employee_receipt(monkeypatch, optional):
    db = AsyncMongoMockClient().test
    piece = {**deepcopy(PIECE), 'user_id':'merchant', 'order_item_id':'line', 'unit_index':1}
    await db[ops.PIECES].insert_one(piece)
    await db[ops.WORKFLOWS].insert_one({'user_id':'merchant','order_number':piece['order_number'],'stage':'in_progress'})
    monkeypatch.setattr(ops, '_current_assembly_order', AsyncMock(return_value=object()))
    monkeypatch.setattr(ops, '_ensure_assembly_order_eligible', AsyncMock())
    monkeypatch.setattr(ops, 'is_optional', AsyncMock(return_value=optional))
    monkeypatch.setattr(ops, 'enforce_stage_instructions', AsyncMock())
    monkeypatch.setattr(ops, '_consume_piece_components', AsyncMock())
    monkeypatch.setattr(ops, '_assembly_progress', AsyncMock(return_value={'ready_count':1,'order_completed':False}))
    args = dict(user_id='merchant',piece_id=piece['piece_id'],client_request_id='once',actor_id='employee',actor_name='Employee')
    if not optional:
        with pytest.raises(HTTPException):
            await ops._mark_assembly_piece_ready_in_transaction(db, **args)
    else:
        await ops._mark_assembly_piece_ready_in_transaction(db, **args)
        saved = await db[ops.PIECES].find_one({'piece_id':piece['piece_id']})
        assert saved['assembly_status'] == 'ready'
        assert saved['assembly_preparation_receipt_optional'] is True
        assert 'preparation_received_by' not in saved
        assert 'preparation_receipt_status' not in saved
        assert saved['status'] == piece['status']


def test_manual_label_fence_allows_expected_status_webhook_only():
    before = {'source': {'order_status':'in_progress'}, 'raw': {'status':'in_progress', 'items':[{'id':'one'}]}, 'pieces':[{'assembly_status':'ready'}]}
    current = deepcopy(before)
    current['source']['order_status'] = current['raw']['status'] = 'completed'
    assert labels._same_material_snapshot(before, current)
    current['raw']['items'].append({'id':'new'})
    assert not labels._same_material_snapshot(before, current)
    current = deepcopy(before)
    current['source']['order_status'] = current['raw']['status'] = 'cancelled'
    assert not labels._same_material_snapshot(before, current)


@pytest.mark.asyncio
async def test_last_optional_ready_completes_real_progress_and_print_capture(monkeypatch):
    from types import SimpleNamespace
    from order_engine.mapper import map_salla_order
    from review_local_policy import LOCAL_COMPLETION_MODE
    from assembly_print_completion import _capture
    db = AsyncMongoMockClient().test
    raw = {'id': 123, 'reference_id':'300000001', 'created_at':'2026-11-08T00:00:00Z', 'status':{'slug':'in_progress'},
           'items':[{'id':77,'name':'Product','quantity':1,'product':{'id':9}}]}
    order = map_salla_order(raw)
    line_id = order.items[0].order_item_id
    piece = {**deepcopy(PIECE), 'user_id':'merchant', 'order_item_id':line_id, 'unit_index':1}
    await db[ops.PIECES].insert_one(piece)
    decision = {'lines':[{'order_item_id':line_id,'quantity':1,'configured':True,'requires_preparation':True}], 'ready_to_ship':False}
    await db[ops.WORKFLOWS].insert_one({'user_id':'merchant','order_number':raw['reference_id'],
        'stage':'in_progress','completion_mode':LOCAL_COMPLETION_MODE,'fulfillment_decision':decision})
    await db.unified_orders.insert_one({'user_id':'merchant','order_number':raw['reference_id'],
        'order_status':'in_progress','raw_by_source':{'salla_direct':raw}})
    monkeypatch.setattr(ops, '_current_assembly_order', AsyncMock(return_value=order))
    monkeypatch.setattr(ops, '_ensure_assembly_order_eligible', AsyncMock())
    monkeypatch.setattr(ops, 'is_optional', AsyncMock(return_value=True))
    monkeypatch.setattr(ops, 'enforce_stage_instructions', AsyncMock())
    monkeypatch.setattr(ops, '_consume_piece_components', AsyncMock())
    monkeypatch.setattr(ops, 'shipping_address_is_complete', lambda *_:True)
    monkeypatch.setattr(ops, 'build_order_fulfillment_decision', AsyncMock(return_value=decision))
    result = await ops._mark_assembly_piece_ready_in_transaction(db, user_id='merchant',piece_id=piece['piece_id'],
        client_request_id='once',actor_id='employee',actor_name='Employee')
    assert result['progress']['order_completed'] is True
    assert result['progress']['stage'] == 'completed'
    assert (await db[ops.WORKFLOWS].find_one({'order_number':raw['reference_id']}))['assembly_status'] == 'completed'
    assert await _capture(db,'merchant',raw['reference_id']) is not None
