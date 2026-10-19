"""Isolated cached-option acceptance; every synthetic database is retained.

No server import, dotenv loading, cleanup deletion, or production connection.
Run with --noconftest and PYTHON_DOTENV_DISABLED=1.
"""
import asyncio
from copy import deepcopy
import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from operational_opening_inventory import BATCHES, RECEIPTS, approve, context, preview
from operational_inventory_projection import projection


OWNER = {'id': 'owner'}
URI = os.environ.get('OPERATIONAL_TEST_MONGO_URI',
                     'mongodb://127.0.0.1:27476/?replicaSet=printReadback1810')
assert urlsplit(URI).scheme == 'mongodb' and urlsplit(URI).hostname == '127.0.0.1'
assert ',' not in urlsplit(URI).netloc and urlsplit(URI).username is None
NO_FINANCIAL = ('accounting_journal_groups_v2', 'accounting_general_ledger_v2',
                'general_ledger', 'mz2_inventory_cost_states', 'mz2_opening_facts_v2',
                'purchase_invoices', 'settings')


def run(scenario):
    async def execute():
        client = AsyncIOMotorClient(URI, serverSelectionTimeoutMS=3000)
        db = client['mz2_opening_cached_20261019_' + uuid4().hex]
        try:
            hello = await client.admin.command('hello')
            assert hello.get('setName'), 'A real isolated replica set is required'
            print('RETAINED_SYNTHETIC_DATABASE=' + db.name)
            await scenario(db)
        finally:
            client.close()
    asyncio.run(execute())


async def seed(db, enabled=True):
    await db.users.insert_many([
        {'id': 'owner', 'role': 'owner', 'is_active': True},
        {'id': 'staff', 'role': 'employee', 'is_active': True},
    ])
    await db.mezan_products_v2.insert_one({
        'user_id': 'owner', 'mezan_product_id': 'product', 'name': 'Synthetic chain',
        'sku': 'SYNTHETIC1', 'options_count': 3, 'variants': [], 'variants_count': 0,
        'raw_salla': {'options': [
            {'id': 12, 'name': 'اللون', 'type': 'select', 'is_required': True,
             'values': [{'id': 9, 'name': 'فضي'}]},
            {'id': 13, 'name': 'الاسم', 'type': 'text', 'required': True},
            {'id': 14, 'name': 'الخدمة', 'type': 'select', 'required': True,
             'values': [{'id': 10, 'name': 'نقش'}]},
        ]}})
    await db.warehouse_locations_warehouses.insert_one({
        'user_id': 'owner', 'id': 'warehouse', 'status': 'active'})
    await db.warehouse_locations.insert_one({
        'user_id': 'owner', 'id': 'shelf', 'warehouse_id': 'warehouse',
        'name': 'Synthetic shelf', 'state': 'empty', 'max_items': 100, 'occupancy': None})
    await db.mz2_atomic_owners.insert_one({
        '_id': 'owner', 'physical_inventory_writes_paused': not enabled,
        'inventory_control_revision': 1, 'writes_paused': True})


def request():
    return {'request_id': 'cached-opening-synthetic', 'lines': [
        {'kind': 'product', 'item_id': 'product', 'location_id': 'shelf',
         'quantity': quantity, 'unit_cost': '7.00', 'purchase_configuration': {
             'state': 'ready', 'selections': [
                 {'option_id': '12', 'value_id': '9'},
                 {'option_id': '14', 'value_id': '10'}],
             'inputs': [{'option_id': '13', 'value': name}]}}
        for name, quantity in [('عبير', 10), ('فاطمة', 15)]]}


async def assert_no_financial(db):
    for name in NO_FINANCIAL:
        assert await db[name].count_documents({}) == 0, name
    assert (await db.mz2_atomic_owners.find_one({'_id': 'owner'}))['writes_paused'] is True


def test_cached_named_rows_preview_approval_replay_preserves_stock_and_no_journals():
    async def scenario(db):
        await seed(db)
        original = deepcopy(await db.mezan_products_v2.find_one({}))
        catalog = await context(db, OWNER)
        assert catalog['products'][0]['configuration_issues'] == []
        assert [o['id'] for o in catalog['products'][0]['options']] == ['12', '13', '14']
        body = request()
        p = await preview(db, OWNER, body)
        assert [r['quantity'] for r in p['lines']] == [10, 15]
        assert [r['total_cost'] for r in p['lines']] == ['70.00', '105.00']
        assert p['total_cost'] == '175.00' and p['financial_effect'] == 'none'
        assert len({r['inventory_identity']['configuration_key'] for r in p['lines']}) == 2
        assert await db[RECEIPTS].count_documents({}) == 0
        approval = {**body, 'preview_hash': p['preview_hash']}
        accepted = await approve(db, OWNER, approval)
        assert await approve(db, OWNER, approval) == accepted
        proofs = await db[RECEIPTS].find({}).sort('source_line_id', 1).to_list(10)
        assert [r['quantity'] for r in proofs] == [10, 15]
        assert [r['specifications']['الاسم'] for r in proofs] == ['عبير', 'فاطمة']
        assert all(r['specifications']['اللون'] == 'فضي' and r['specifications']['الخدمة'] == 'نقش' for r in proofs)
        assert all(r['valuation_status'] == 'PENDING_FINANCIAL_VALUATION' and r['financial_effect'] == 'none' for r in proofs)
        stock = await projection(db, 'owner')
        assert len(stock['items']) == 2
        assert sorted(r['physical'] for r in stock['items']) == [10, 15]
        assert all(r['available'] == 0 and r['unit_cost'] is None for r in stock['items'])
        assert await db.mezan_products_v2.find_one({}) == original
        await assert_no_financial(db)
    run(scenario)


def test_changed_cached_definition_invalidates_preview_without_stock_effect():
    async def scenario(db):
        await seed(db)
        body = request()
        p = await preview(db, OWNER, body)
        await db.mezan_products_v2.update_one({'mezan_product_id': 'product'},
            {'$set': {'raw_salla.options.0.values.0.name': 'فضي معدل'}})
        with pytest.raises(HTTPException) as error:
            await approve(db, OWNER, {**body, 'preview_hash': p['preview_hash']})
        assert error.value.detail['code'] == 'opening_inventory_preview_changed'
        assert await db[BATCHES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
        assert (await db.warehouse_locations.find_one({}))['occupancy'] is None
        await assert_no_financial(db)
    run(scenario)


def test_cached_options_do_not_bypass_writer_or_owner_guards():
    async def scenario(db):
        await seed(db, enabled=False)
        body = request()
        p = await preview(db, OWNER, body)
        assert p['writer_disabled'] is True
        with pytest.raises(HTTPException) as error:
            await approve(db, OWNER, {**body, 'preview_hash': p['preview_hash']})
        assert error.value.status_code == 423
        with pytest.raises(HTTPException) as error:
            await preview(db, {'id': 'staff'}, body)
        assert error.value.status_code == 403
        assert await db[BATCHES].count_documents({}) == 0
        assert await db[RECEIPTS].count_documents({}) == 0
        await assert_no_financial(db)
    run(scenario)
