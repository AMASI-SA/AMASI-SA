"""Linked receipts on disposable local Mongo; never load the production app."""
import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException

from test_operational_balance_integration import run, started, NOW, movement
from test_operational_balance_standalone_inventory import setup as stock_setup
from operational_balance_inventory import save_purchase, purchase_view, inventory_view
from operational_balance_store import read, digest
from operational_balance_service import create_movement, report

OWNER = {'id': 'owner'}


async def invoice(db, *, tracked=True, quantity=5, config=None, location='shelf'):
    await stock_setup(db)
    await started(db, supplier='0')
    if config is not None:
        from test_inventory_option_configuration import product_source
        await db.mezan_products_v2.update_one({'mezan_product_id': 'product'},
            {'$set': {**product_source(), 'variants_count': 2}})
    body = {'request_id': 'purchase-for-receiving', 'expected_session_scope': digest(['owner', 'owner']),
        'supplier_id': 'supplier', 'invoice_number': 'RECEIVING-1', 'invoice_date': '2026-10-07',
        'lines': [{'kind': 'product', 'item_id': 'product', 'quantity': quantity,
            'unit_price': '12.50', 'tax': '0',
            **({'location_id': location} if location else {}),
            'purchase_configuration': config or {'state': 'ready', 'selections': [], 'inputs': []}}]}
    if tracked:
        body['receipt_tracking_version'] = 1
    return await save_purchase(db, 'owner', 'owner', body, clock=NOW)


async def command(db, saved, *, quantity=2, request_id='receipt-first', location='shelf'):
    from operational_inventory_purchase_receipts import context, preview
    current = await context(db, OWNER, saved['id'])
    body = {'request_id': request_id, 'expected_session_scope': digest(['owner', 'owner']),
        'receipt_date': '2026-10-07', 'note': '', 'lines': [
            {'line_id': current['invoice']['lines'][0]['line_id'], 'quantity': quantity, 'location_id': location}]}
    result = await preview(db, OWNER, saved['id'], body)
    return {**body, 'preview_hash': result['preview_hash']}


@pytest.mark.parametrize('legacy_metadata', [False, True])
def test_later_side_classification_preserves_partial_invoice_stock_identity_and_retry(legacy_metadata, monkeypatch):
    from test_inventory_option_configuration import config
    from operational_inventory_purchase_receipts import save, context
    from inventory_option_policy import get_policy, save_policy
    async def scenario(db):
        original_config = config()
        original_config['inputs'].append({'option_id': 'note', 'value': 'وصف محفوظ قبل التصنيف'})
        if legacy_metadata:
            import operational_purchase_metadata as metadata
            current_products = metadata._products
            def legacy_products(*args, **kwargs):
                result = current_products(*args, **kwargs)
                for product in result:
                    product.pop('inventory_option_policy_revision', None)
                    for option in product['options']:
                        option.pop('inventory_role', None)
                        option.pop('role_locked', None)
                return result
            # Produce the real pre-release schema before the invoice is saved;
            # never rewrite an approved invoice or its audit evidence.
            with monkeypatch.context() as historical_producer:
                historical_producer.setattr(metadata, '_products', legacy_products)
                saved = await invoice(db, config=original_config)
        else:
            saved = await invoice(db, config=original_config)
        first_command = await command(db, saved)
        first = await save(db, OWNER, saved['id'], first_command)
        before = await read(db, 'owner')
        definition = await get_policy(db, OWNER, 'product')
        await save_policy(db, OWNER, 'product', {
            'expected_revision': definition['revision'], 'catalog_fingerprint': definition['catalog_fingerprint'],
            'roles': {**definition['roles'], 'note': 'informational', 'optional': 'informational'},
        })
        assert await save(db, OWNER, saved['id'], first_command) == first
        second = await save(db, OWNER, saved['id'], await command(db, saved, quantity=3, request_id='receipt-after-classification'))
        assert second['lines'][0]['inventory_identity'] == first['lines'][0]['inventory_identity']
        assert second['lines'][0]['purchase_configuration'] == first['lines'][0]['purchase_configuration']
        assert second['lines'][0]['purchase_option_labels'] == first['lines'][0]['purchase_option_labels']
        after = await read(db, 'owner')
        assert before['inventory_purchases'] == after['inventory_purchases']
        assert before['movements'] == after['movements']
        view = (await context(db, OWNER, saved['id']))['invoice']
        assert view['receipt_status'] == 'received' and len(view['receipt_history']) == 2
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 2
        assert await db.mezan_inventory_reservations_v2.count_documents({}) == 0
    run(scenario)


def test_receiving_missing_frozen_product_definition_cannot_use_new_catalog_silently():
    from operational_opening_inventory import compile_preview
    async def scenario(db):
        await invoice(db)
        body = {'request_id': 'missing-frozen-options', 'lines': [{'kind': 'product', 'item_id': 'product',
                'quantity': 1, 'unit_cost': '12.50', 'location_id': 'shelf',
                'purchase_configuration': {'state': 'ready', 'selections': [], 'inputs': []}}]}
        with pytest.raises(HTTPException) as exc:
            await compile_preview(db, 'owner', body, standalone=True, allow_duplicate_lines=True,
                                  configuration_snapshots=[{'id': 'product', 'kind': 'product'}])
        assert exc.value.detail['code'] == 'purchase_receipt_configuration_required'
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
    run(scenario)


def test_opt_in_invoice_starts_waiting_without_stock_or_cash():
    async def scenario(db):
        saved = await invoice(db)
        assert saved.get('receipt_tracking_version') == 1
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
        state = await read(db, 'owner')
        assert not state['movements']
        assert report(state)['summary']['actual_liquidity'] == '1000.00'
        assert report(state)['summary']['payable'] == '62.50'
    run(scenario)


def test_partial_then_remaining_receipts_are_once_only_and_financially_independent():
    from operational_inventory_purchase_receipts import save, context, RECEIPTS
    from operational_inventory_projection import projection
    async def scenario(db):
        saved = await invoice(db)
        before = await read(db, 'owner')
        bank_before = await db.mz2_financial_accounts.find({}).to_list(100)
        first = await command(db, saved)
        result = await save(db, OWNER, saved['id'], first)
        assert await save(db, OWNER, saved['id'], first) == result
        assert result['invoice']['receipt_status'] == 'partial'
        line = result['invoice']['lines'][0]
        assert (line['received_quantity'], line['pending_receipt_quantity']) == (2, 3)
        assert result['invoice']['outstanding'] == '62.50'
        after = await read(db, 'owner')
        assert {k: v for k, v in after.items() if k != 'revision'} == {k: v for k, v in before.items() if k != 'revision'}
        assert after['revision'] == before['revision'] + 1
        assert await db.mz2_financial_accounts.find({}).to_list(100) == bank_before
        stock = await projection(db, 'owner')
        assert sum(row['physical'] for row in stock['items']) == 2
        assert sum(row['available'] for row in stock['items']) == 0
        assert {row['operational_unit_cost'] for row in stock['items']} == {'12.50'}
        second = await command(db, saved, quantity=3, request_id='receipt-rest')
        await save(db, OWNER, saved['id'], second)
        view = (await context(db, OWNER, saved['id']))['invoice']
        assert view['receipt_status'] == 'received'
        assert view['lines'][0]['received_quantity'] == 5
        assert view['lines'][0]['pending_receipt_quantity'] == 0
        assert len(view['receipt_history']) == 2
        assert {h['receipt_date'] for h in view['receipt_history']} == {'2026-10-07'}
        assert await db[RECEIPTS].count_documents({}) == 2
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 2
        assert (await db.warehouse_locations.find_one({'id': 'shelf'}))['occupancy']['total_quantity'] == 5
        for name in ('accounting_journal_groups_v2', 'accounting_general_ledger_v2', 'mz2_inventory_cost_states',
                     'mezan_inventory_reservations_v2', 'mezan_fulfillment_events_v2'):
            assert await db[name].count_documents({}) == 0
    run(scenario)


def test_payment_before_receipt_does_not_count_as_stock_or_repeat_money():
    from operational_inventory_purchase_receipts import save, context
    async def scenario(db):
        saved = await invoice(db)
        pay = {**movement(), 'amount': '25', 'allocations': [{'obligation_id': saved['obligation_id'], 'amount': '25'}]}
        await create_movement(db, 'owner', 'owner', pay, clock=NOW)
        waiting = (await context(db, OWNER, saved['id']))['invoice']
        assert waiting['receipt_status'] == 'not_received'
        assert waiting['settled'] == '25.00'
        before = report(await read(db, 'owner'))
        await save(db, OWNER, saved['id'], await command(db, saved))
        after = report(await read(db, 'owner'))
        assert after == before
        assert len((await read(db, 'owner'))['movements']) == 1
    run(scenario)


def test_historical_invoice_does_not_become_unreceived_or_allow_duplicate_stock():
    from operational_inventory_purchase_receipts import context, enrich_purchases
    async def scenario(db):
        saved = await invoice(db, tracked=False)
        result = await enrich_purchases(db, 'owner', inventory_view(await read(db, 'owner')))
        view = result['items'][0]
        assert view.get('receipt_status', 'unproven') == 'unproven'
        assert view['physical_stock_status'] == 'unproven'
        assert view['lines'][0].get('received_quantity') is None
        assert view['lines'][0].get('pending_receipt_quantity') is None
        assert view == saved
        with pytest.raises(HTTPException) as exc:
            await context(db, OWNER, saved['id'])
        assert exc.value.detail['code'] == 'purchase_receipt_historical_reconciliation'
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize('change', ['quantity', 'line', 'owner', 'scope', 'before_invoice', 'future', 'foreign_location'])
def test_invalid_receipt_cannot_write_stock(change):
    from operational_inventory_purchase_receipts import preview
    async def scenario(db):
        saved = await invoice(db)
        body = await command(db, saved)
        body.pop('preview_hash')
        user = OWNER
        if change == 'quantity': body['lines'][0]['quantity'] = 6
        elif change == 'line': body['lines'][0]['line_id'] = 'foreign-line'
        elif change == 'owner': user = {'id': 'staff'}
        elif change == 'scope': body['expected_session_scope'] = digest(['other', 'owner'])
        elif change == 'before_invoice': body['receipt_date'] = '2026-10-06'
        elif change == 'future': body['receipt_date'] = '2099-01-01'
        else: body['lines'][0]['location_id'] = 'foreign-location'
        with pytest.raises(HTTPException):
            await preview(db, user, saved['id'], body)
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
        assert (await db.warehouse_locations.find_one({'id': 'shelf'}))['occupancy'] is None
    run(scenario)


def test_concurrent_receipts_cannot_overreceive_and_stale_preview_is_rejected():
    from operational_inventory_purchase_receipts import save, context, RECEIPTS
    async def scenario(db):
        saved = await invoice(db)
        a = await command(db, saved, quantity=4, request_id='concurrent-receipt-a')
        b = await command(db, saved, quantity=4, request_id='concurrent-receipt-b')
        results = await asyncio.gather(save(db, OWNER, saved['id'], a), save(db, OWNER, saved['id'], b), return_exceptions=True)
        assert sum(isinstance(r, dict) for r in results) == 1
        assert all(isinstance(r, dict) or isinstance(r, HTTPException) and r.status_code in (409, 422, 429) for r in results)
        assert await db[RECEIPTS].count_documents({}) == 1
        assert (await context(db, OWNER, saved['id']))['invoice']['lines'][0]['received_quantity'] == 4
        failed = a if isinstance(results[0], Exception) else b
        with pytest.raises(HTTPException): await save(db, OWNER, saved['id'], failed)
        assert (await db.warehouse_locations.find_one({'id': 'shelf'}))['occupancy']['total_quantity'] == 4
    run(scenario)


def test_event_failure_rolls_back_marker_stock_and_revision(monkeypatch):
    import operational_inventory_purchase_receipts as service
    original = service.validate_insert
    monkeypatch.setattr(service, 'validate_insert', lambda name, doc: name != 'warehouse_location_events' and original(name, doc))
    async def scenario(db):
        saved = await invoice(db)
        body = await command(db, saved)
        before = await read(db, 'owner')
        with pytest.raises(HTTPException): await service.save(db, OWNER, saved['id'], body)
        assert await read(db, 'owner') == before
        assert await db[service.RECEIPTS].count_documents({}) == 0
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
        assert (await db.warehouse_locations.find_one({'id': 'shelf'}))['occupancy'] is None
    run(scenario)


def test_request_conflict_and_default_closed_control_preserved():
    from operational_inventory_purchase_receipts import save
    from standalone_inventory_entry import set_control
    async def scenario(db):
        saved = await invoice(db)
        body = await command(db, saved)
        await set_control(db, OWNER, {'revision': 1, 'paused': True})
        with pytest.raises(HTTPException) as exc: await save(db, OWNER, saved['id'], body)
        assert exc.value.status_code == 423
        await set_control(db, OWNER, {'revision': 2, 'paused': False})
        first = await save(db, OWNER, saved['id'], body)
        changed = {**body, 'note': 'different'}
        with pytest.raises(HTTPException) as exc: await save(db, OWNER, saved['id'], changed)
        assert exc.value.detail['code'] == 'purchase_receipt_request_conflict'
        assert await save(db, OWNER, saved['id'], body) == first
        control = await db.mz2_atomic_owners.find_one({'_id': 'owner'})
        assert control.get('physical_inventory_writes_paused') is not False
        assert control.get('writes_paused') is not False
    run(scenario)


def test_receiving_profile_cannot_modify_money_even_when_error_is_caught():
    from inventory_write_gate import admitted
    from operational_atomic import operational_owner
    async def scenario(db):
        await invoice(db)
        before = await read(db, 'owner')
        for name in ('general_ledger', 'mz2_inventory_cost_states', 'settings', 'mz2_atomic_owners'):
            async def forbidden(scoped):
                try: await scoped[name].insert_one({'user_id': 'owner', 'amount': 1})
                except HTTPException: pass
            with pytest.raises(HTTPException):
                await admitted(db, 'owner', 'receipt-test', lambda: operational_owner(db, 'owner', forbidden, profile='purchase_receiving'), standalone=True)
        async def corrupt(scoped):
            try:
                await scoped.operational_balance_states_v1.update_one(
                    {'_id': 'owner', 'owner_id': 'owner', 'revision': before['revision']}, {'$set': {'movements': []}})
            except HTTPException: pass
        with pytest.raises(HTTPException):
            await admitted(db, 'owner', 'receipt-test', lambda: operational_owner(db, 'owner', corrupt, profile='purchase_receiving'), standalone=True)
        assert await read(db, 'owner') == before
    run(scenario)


def test_native_http_queue_receive_history_and_owner_permissions():
    from test_operational_balance_opening_inventory import client
    async def scenario(db):
        saved = await invoice(db, location=None)
        key_before = saved['lines'][0]['purchase_line_key']
        assert 'location_id' not in saved['lines'][0]
        async with client(db, 'owner') as c:
            base = '/api/operational-balances/inventory-purchases'
            response = await c.get(base)
            assert response.status_code == 200, response.text
            row = response.json()['items'][0]
            assert row['receipt_status'] == 'not_received'
            response = await c.get(f'{base}/{saved["id"]}/receiving-context')
            assert response.status_code == 200, response.text
            assert response.json()['locations'][0]['id'] == 'shelf'
            body = {'request_id': 'http-receipt-one', 'expected_session_scope': digest(['owner', 'owner']),
                'receipt_date': '2026-10-07', 'lines': [{'line_id': row['lines'][0]['line_id'], 'quantity': 2, 'location_id': 'shelf'}]}
            response = await c.post(f'{base}/{saved["id"]}/receipts/preview', json=body)
            assert response.status_code == 200, response.text
            payload = {**body, 'preview_hash': response.json()['preview_hash']}
            response = await c.post(f'{base}/{saved["id"]}/receipts/save', json=payload)
            assert response.status_code == 200, response.text
            result = response.json()
            again = await c.post(f'{base}/{saved["id"]}/receipts/save', json=payload)
            assert again.status_code == 200 and again.json() == result
            response = await c.get(base + '/entry/supplier/RECEIVING-1')
            assert response.status_code == 200, response.text
            assert response.json()['receipt_status'] == 'partial'
            assert response.json()['lines'][0]['purchase_line_key'] == key_before
            assert response.json()['receipt_history'][0]['lines'][0]['location_id'] == 'shelf'
        async with client(db, 'staff') as c:
            assert (await c.get(f'{base}/{saved["id"]}/receiving-context')).status_code == 403
            assert (await c.post(f'{base}/{saved["id"]}/receipts/save', json=payload)).status_code == 403
        async with client(db, 'other') as c:
            assert (await c.get(f'{base}/{saved["id"]}/receiving-context')).status_code == 404
        assert (await read(db, 'owner'))['inventory_purchases'][0]['lines'][0]['purchase_line_key'] == key_before
    run(scenario)


def test_supplier_return_reduces_pending_ceiling_without_repeating_finance():
    from operational_inventory_purchase_receipts import save, context
    from operational_supplier_adjustments import save_adjustment
    async def scenario(db):
        saved = await invoice(db)
        await save(db, OWNER, saved['id'], await command(db, saved))
        await save_adjustment(db, 'owner', 'owner', {'request_id': 'accepted-return-1',
            'expected_session_scope': digest(['owner', 'owner']), 'invoice_id': saved['id'], 'kind': 'return',
            'reference': 'RETURN-1', 'business_date': '2026-10-07', 'accepted': True,
            'lines': [{'kind': 'product', 'item_id': 'product', 'purchase_line_key': saved['lines'][0]['purchase_line_key'], 'quantity': 2}]}, clock=NOW)
        remaining = (await context(db, OWNER, saved['id']))['invoice']['lines'][0]
        assert (remaining['received_quantity'], remaining['returned_quantity'], remaining['pending_receipt_quantity']) == (2, 2, 1)
        with pytest.raises(HTTPException): await command(db, saved, quantity=2, request_id='too-much-after-return')
        before = report(await read(db, 'owner'))
        await save(db, OWNER, saved['id'], await command(db, saved, quantity=1, request_id='last-after-return'))
        assert report(await read(db, 'owner')) == before
        assert (await context(db, OWNER, saved['id']))['invoice']['receipt_status'] == 'closed'
        assert (await db.warehouse_locations.find_one({'id': 'shelf'}))['occupancy']['total_quantity'] == 3
    run(scenario)


@pytest.mark.parametrize('returned', [4, 10])
def test_returned_quantity_closes_receiving_without_claiming_it_was_received(returned):
    from operational_inventory_purchase_receipts import context, save
    from operational_supplier_adjustments import save_adjustment
    async def scenario(db):
        saved = await invoice(db, quantity=10)
        await save_adjustment(db, 'owner', 'owner', {'request_id': 'accepted-return-all-or-part',
            'expected_session_scope': digest(['owner', 'owner']), 'invoice_id': saved['id'], 'kind': 'return',
            'reference': 'RETURN-BEFORE-RECEIPT', 'business_date': '2026-10-07', 'accepted': True,
            'lines': [{'kind': 'product', 'item_id': 'product', 'purchase_line_key': saved['lines'][0]['purchase_line_key'], 'quantity': returned}]}, clock=NOW)
        if returned < 10:
            await save(db, OWNER, saved['id'], await command(db, saved, quantity=10-returned))
        view = (await context(db, OWNER, saved['id']))['invoice']
        assert view['receipt_status'] == 'closed'
        assert view['can_receive'] is False and view['receipt_remaining_lines'] == 0
        assert view['lines'][0]['received_quantity'] == 10-returned
        assert view['lines'][0]['returned_quantity'] == returned
        assert view['lines'][0]['pending_receipt_quantity'] == 0
        with pytest.raises(HTTPException): await command(db, saved, quantity=1, request_id='after-closed')
    run(scenario)


def test_financial_snapshot_closure_race_aborts_receipt_atomically(monkeypatch):
    import operational_inventory_purchase_receipts as service
    from operational_balance_store import mutate
    async def scenario(db):
        saved = await invoice(db)
        body = await command(db, saved)
        original = service.compile_receipt
        compiled = asyncio.Event()
        resume = asyncio.Event()
        first = True
        async def pause_after_snapshot(*args, **kwargs):
            nonlocal first
            result = await original(*args, **kwargs)
            if first:
                first = False
                compiled.set()
                await asyncio.wait_for(resume.wait(), 5)
            return result
        monkeypatch.setattr(service, 'compile_receipt', pause_after_snapshot)
        task = asyncio.create_task(service.save(db, OWNER, saved['id'], body))
        await asyncio.wait_for(compiled.wait(), 5)
        async def close(state): state['status'] = 'readonly'
        try:
            await mutate(db, 'owner', close)
        finally:
            resume.set()
        with pytest.raises(HTTPException): await task
        assert (await read(db, 'owner'))['status'] == 'readonly'
        assert await db[service.RECEIPTS].count_documents({}) == 0
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
        assert (await db.warehouse_locations.find_one({'id': 'shelf'}))['occupancy'] is None
    run(scenario)


@pytest.mark.parametrize('mode', ['fixed', 'deferred_parent', 'deferred_name', 'literal_no'])
def test_receipt_preserves_distinct_option_ids_and_readable_labels(mode):
    from operational_inventory_purchase_receipts import save
    from operational_inventory_projection import projection
    from test_inventory_option_configuration import config
    async def scenario(db):
        body = config()
        if mode == 'deferred_parent':
            body = config(answer='', name='', shape='', deferred=('embroider',))
        elif mode == 'deferred_name':
            body = config(name='', deferred=('name',))
        elif mode == 'literal_no':
            body = config(answer='no', name='', shape='')
        saved = await invoice(db, config=body, location=None)
        result = await save(db, OWNER, saved['id'], await command(db, saved))
        physical = (await projection(db, 'owner'))['items'][0]
        expected_deferred = sorted(body['deferred_option_ids'])
        assert physical['purchase_configuration']['deferred_option_ids'] == expected_deferred
        assert physical['preparation_state'] == ('requires_preparation' if expected_deferred else 'ready_complete')
        assert {v['option_id'] for v in physical['deferred_option_labels']} == set(expected_deferred)
        expected = {v['option_id'] for v in body['selections'] + body['inputs']}
        assert {v['option_id'] for v in physical['purchase_option_labels']} == expected
        assert len(physical['specifications']) == len(expected)
        assert all(key.startswith('option:') for key in physical['specifications'])
        if mode == 'fixed':
            assert {'embroider', 'name'} <= expected
            assert any(v['value'] == 'فاطمة' for v in physical['purchase_option_labels'])
        assert physical['available'] == 0
        assert result['lines'][0]['purchase_configuration'] == physical['purchase_configuration']
    run(scenario)


@pytest.mark.parametrize('capacity', [20, 3])
def test_distinct_invoice_lines_can_arrive_together_with_combined_capacity(capacity):
    from operational_inventory_purchase_receipts import context, preview, save
    async def scenario(db):
        await invoice(db)
        await db.warehouse_locations.insert_one({'user_id': 'owner', 'id': 'second-shelf', 'warehouse_id': 'warehouse',
            'name': 'Second shelf', 'code': 'S2', 'state': 'empty', 'max_items': 20, 'occupancy': None})
        await db.warehouse_locations.update_one({'id': 'shelf'}, {'$set': {'max_items': capacity}})
        purchase = {'request_id': 'two-planned-locations', 'expected_session_scope': digest(['owner', 'owner']),
            'supplier_id': 'supplier', 'invoice_number': 'RECEIVING-2', 'invoice_date': '2026-10-07',
            'receipt_tracking_version': 1, 'lines': [
                {'kind': 'product', 'item_id': 'product', 'quantity': 2, 'unit_price': '12.50', 'tax': '0',
                 'location_id': location, 'purchase_configuration': {'state': 'ready', 'selections': [], 'inputs': []}}
                for location in ('shelf', 'second-shelf')]}
        saved = await save_purchase(db, 'owner', 'owner', purchase, clock=NOW)
        current = await context(db, OWNER, saved['id'])
        body = {'request_id': 'receive-two-locations-together', 'expected_session_scope': digest(['owner', 'owner']),
            'receipt_date': '2026-10-07', 'lines': [
                {'line_id': row['line_id'], 'quantity': 2, 'location_id': 'shelf'} for row in current['invoice']['lines']]}
        if capacity == 3:
            with pytest.raises(HTTPException) as exc: await preview(db, OWNER, saved['id'], body)
            assert exc.value.detail['code'] == 'opening_inventory_capacity_exceeded'
            assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
        else:
            result = await preview(db, OWNER, saved['id'], body)
            await save(db, OWNER, saved['id'], {**body, 'preview_hash': result['preview_hash']})
            location = await db.warehouse_locations.find_one({'id': 'shelf'})
            assert location['occupancy']['total_quantity'] == 4
            assert len(location['occupancy']['items']) == 2
            assert (await context(db, OWNER, saved['id']))['invoice']['receipt_status'] == 'received'
    run(scenario)
