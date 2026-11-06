from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, patch
import sys
import types

import pytest
from mongomock_motor import AsyncMongoMockClient
from salla_integration import cost_option_recovery as recovery
from order_cost_authority import calculate_catalog_line_cost


def fixture():
    line = {'order_item_id': 'line', 'product_id': 'product', 'quantity': 1, 'options': [], 'unit_cost': 40}
    raw_line = {'id': 'line', 'product_id': 'product', 'quantity': 1, 'options': [], 'price': 170}
    order = {'user_id': 'owner', 'order_number': 'number', 'order_status_slug': 'pending_review',
        'products': [line], 'cost_snapshot': {'immutable': True},
        'raw_by_source': {'salla_direct': {'id': 'native', 'reference_id': 'number',
            'updated_at': '2026-11-06T00:00:00Z', 'items': [raw_line]}}}
    provider = [{**raw_line, 'price': 999, 'options': [{'product_option_id': 'size-type',
        'name': 'Renamed option', 'value': {'id': 'adults', 'name': 'Renamed value'}}]}]
    return order, provider


def cost(item):
    return calculate_catalog_line_cost(item, product={'options': [{'id': 'size-type',
        'required': True, 'values': [{'id': 'adults'}, {'id': 'children'}]}]},
        profile={'base_cost': 40}, product_bindings=[{'resource_id': 'bag'}],
        option_bindings=[{'id': 'adult-cost', 'option_id': 'size-type', 'value_id': 'adults',
                          'mode': 'direct', 'direct_amount': 10}], resources={'bag': {'unit_cost': 1.2}})


def test_recovers_registered_cost_by_ids_without_price_or_fact_rewrite():
    order, provider = fixture()
    before = deepcopy(order)
    assert not cost(order['products'][0])['mezan_cost_complete']
    result = recovery.option_patch(order, provider)
    assert result['raw_by_source.salla_direct']['items'][0]['price'] == 170
    assert result['products'][0]['unit_cost'] == 40
    assert cost(result['products'][0])['line_total'] == 51.2
    assert cost(result['products'][0])['mezan_cost_complete']
    assert order == before
    assert set(result) == {'products', 'raw_by_source.salla_direct'}


@pytest.mark.parametrize('field,value', [('id', 'other'), ('product_id', 'other'), ('quantity', 2)])
def test_mismatch_never_guesses(field, value):
    order, provider = fixture()
    provider[0][field] = value
    assert recovery.option_patch(order, provider) is None


def test_empty_duplicate_existing_and_wrong_raw_identity_stay_unresolved():
    order, provider = fixture()
    assert recovery.option_patch(order, []) is None
    assert recovery.option_patch(order, provider * 2) is None
    order['products'][0]['options'] = [{'name': 'existing', 'value': 'keep'}]
    assert recovery.option_patch(order, provider) is None
    order, provider = fixture()
    order['raw_by_source']['salla_direct']['items'][0]['product_id'] = 'wrong'
    assert recovery.option_patch(order, provider) is None


def test_only_missing_choices_with_catalog_evidence_are_candidates():
    order, provider = fixture()
    assert recovery.candidate(order, {'product'})
    assert not recovery.candidate(order, set())
    order['raw_by_source']['salla_direct']['items'] = provider
    assert not recovery.candidate(order, {'product'})


@pytest.mark.asyncio
async def test_atomic_source_change_prevents_write():
    order, provider = fixture()
    db = AsyncMongoMockClient().db
    await db.unified_orders.insert_one({**order, 'order_status_slug': 'cancelled'})
    async def fenced(db, **kwargs):
        assert kwargs['allow_delivery_observation'] is False
        await kwargs['before_persist'](db)
        return await kwargs['persist'](db)
    module = types.ModuleType('fulfillment_v2_routes')
    module.persist_component_source_snapshot = fenced
    with patch.dict(sys.modules, {'fulfillment_v2_routes': module}):
        with pytest.raises(recovery.SourceChanged):
            await recovery.apply_options(db, order, provider)
    stored = await db.unified_orders.find_one({'user_id': 'owner'})
    assert stored['products'][0]['options'] == []


@pytest.mark.asyncio
async def test_retry_is_durable_and_deduplicates_concurrent_workers():
    import asyncio
    order, provider = fixture()
    db = AsyncMongoMockClient().db
    fetch = AsyncMock(return_value=provider)
    apply = AsyncMock(return_value=False)
    with patch('salla_integration.sync._fetch_salla_order_items', fetch), patch.object(recovery, 'apply_options', apply):
        results = await asyncio.gather(*(recovery.recover_one(db, order) for _ in range(3)))
        assert sum(results) == 1
        assert fetch.await_count == 1
        assert not await recovery.recover_one(db, order)
        row = await db[recovery.COLLECTION].find_one({'_id': {'$ne': 'scan'}})
        assert row['outcome'] == 'unresolved'
        await db[recovery.COLLECTION].update_one({'_id': row['_id']}, {'$set': {
            'next_attempt_at': recovery.now() - timedelta(seconds=1)}})
        assert await recovery.recover_one(db, order)
        assert fetch.await_count == 2
        fetch.assert_awaited_with(db, 'owner', 'native')


@pytest.mark.asyncio
async def test_provider_failure_stays_retryable_without_claiming_completion():
    order, _ = fixture()
    db = AsyncMongoMockClient().db
    with patch('salla_integration.sync._fetch_salla_order_items', AsyncMock(side_effect=RuntimeError('private'))):
        assert await recovery.recover_one(db, order)
    row = await db[recovery.COLLECTION].find_one({})
    assert row['outcome'] == 'retry_required'
    assert 'private' not in str(row)


@pytest.mark.asyncio
async def test_worker_caps_fetches_and_continues_cursor_without_starvation():
    from product_option_cost_routes import BINDINGS
    db = AsyncMongoMockClient().db
    await db.salla_integrations.insert_one({'user_id': 'owner', 'status': 'connected'})
    await db[BINDINGS].insert_one({'user_id': 'owner', 'salla_product_id': 'product'})
    for index in range(7):
        order, _ = fixture()
        order['order_number'] = str(index)
        order['raw_by_source']['salla_direct']['reference_id'] = str(index)
        await db.unified_orders.insert_one(order)
    fetch = AsyncMock(return_value=[])
    with patch('salla_integration.sync._fetch_salla_order_items', fetch):
        await recovery.run_once(db)
        assert fetch.await_count == 4
        await recovery.run_once(db)
        assert fetch.await_count == 4
        await db[recovery.COLLECTION].update_one({'_id': 'scan'}, {'$set': {
            'due_at': recovery.now() - timedelta(seconds=1)}})
        await recovery.run_once(db)
        assert fetch.await_count == 7


@pytest.mark.asyncio
async def test_successful_write_uses_owner_fence_and_only_patches_options():
    order, provider = fixture()
    db = AsyncMongoMockClient().db
    await db.unified_orders.insert_one(deepcopy(order))
    async def fenced(db, **kwargs):
        assert kwargs['user_id'] == 'owner'
        assert kwargs['order_number'] == 'number'
        assert kwargs['allow_delivery_observation'] is False
        assert 'authoritative_refresh' not in kwargs
        await kwargs['before_persist'](db)
        return await kwargs['persist'](db)
    module = types.ModuleType('fulfillment_v2_routes')
    module.persist_component_source_snapshot = fenced
    with patch.dict(sys.modules, {'fulfillment_v2_routes': module}):
        assert await recovery.apply_options(db, order, provider)
    stored = await db.unified_orders.find_one({'user_id': 'owner'})
    assert cost(stored['products'][0])['line_total'] == 51.2
    assert stored['cost_snapshot'] == order['cost_snapshot']
    assert stored['order_status_slug'] == order['order_status_slug']
    assert stored['raw_by_source']['salla_direct']['items'][0]['price'] == 170
    assert recovery.option_patch(stored, provider) is None


@pytest.mark.asyncio
async def test_disconnected_owner_never_fetches():
    from product_option_cost_routes import BINDINGS
    db = AsyncMongoMockClient().db
    order, _ = fixture()
    await db.unified_orders.insert_one(order)
    await db.salla_integrations.insert_one({'user_id': 'owner', 'status': 'paused'})
    await db[BINDINGS].insert_one({'user_id': 'owner', 'salla_product_id': 'product'})
    with patch('salla_integration.sync._fetch_salla_order_items', AsyncMock()) as fetch:
        await recovery.run_once(db)
        fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_arrival_is_not_blocked_by_historical_cursor_or_other_tenant_binding():
    from product_option_cost_routes import BINDINGS
    db = AsyncMongoMockClient().db
    await db.salla_integrations.insert_one({'user_id': 'owner', 'status': 'connected'})
    await db[BINDINGS].insert_one({'user_id': 'owner', 'salla_product_id': 'product'})
    old, _ = fixture()
    old['products'][0]['options'] = [{'name': 'already resolved'}]
    old_id = (await db.unified_orders.insert_one(old)).inserted_id
    await db[recovery.COLLECTION].insert_one({'_id': 'scan', 'cursor': old_id,
        'due_at': recovery.now() - timedelta(seconds=1)})
    new, _ = fixture()
    new['order_number'] = 'new'
    new['raw_by_source']['salla_direct']['reference_id'] = 'new'
    await db.unified_orders.insert_one(new)
    foreign = deepcopy(new)
    foreign['user_id'] = 'other-owner'
    foreign.pop('_id', None)
    await db.unified_orders.insert_one(foreign)
    await db.salla_integrations.insert_one({'user_id': 'other-owner', 'status': 'connected'})
    with patch('salla_integration.sync._fetch_salla_order_items', AsyncMock(return_value=[])) as fetch:
        await recovery.run_once(db)
        fetch.assert_awaited_once_with(db, 'owner', 'native')
