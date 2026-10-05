"""Native invoice -> read adapter -> engine -> movement integration, isolated Mongo only."""
import asyncio
from copy import deepcopy
from decimal import Decimal
from uuid import uuid4
import os

from motor.motor_asyncio import AsyncIOMotorClient
from operational_balance_sources import collect_sources
from operational_balance_engine import reconcile
from operational_balance_service import save_opening, create_movement, refresh, report, supplier_return
from operational_balance_store import read, RECEIPTS

START = '2026-10-05T09:00:00+00:00'
NOW = '2026-10-08T12:00:00+00:00'
URI = os.environ.get('OPERATIONAL_TEST_MONGO_URI', 'mongodb://127.0.0.1:27305')
assert URI.startswith('mongodb://127.0.0.1:'), 'Only dedicated local Mongo is allowed'


def run(test):
    async def execute():
        client = AsyncIOMotorClient(URI, serverSelectionTimeoutMS=3000)
        db = client['operational_balance_test_invoice_' + uuid4().hex[:20]]
        try:
            await client.admin.command('ping')
            await db.users.insert_one({'id': 'owner', 'role': 'owner', 'is_active': True})
            await db.mz2_financial_accounts.insert_one({'user_id': 'owner', 'id': 'bank', 'name': 'Bank', 'account_type': 'bank', 'currency': 'SAR', 'status': 'active'})
            await db.mezan_suppliers_v2.insert_many([{'user_id': 'owner', 'id': key, 'company_name': key, 'status': 'active'} for key in ('supplier', 'other-supplier')])
            await save_opening(db, 'owner', 'owner', {'request_id': 'bankbaseline', 'party_type': 'bank', 'party_id': 'bank',
                'amount': '10000', 'currency': 'SAR', 'direction': 'for_us'}, clock=START)
            await save_opening(db, 'owner', 'owner', {'request_id': 'finishopenings'}, finish=True, clock=START)
            await db.unified_orders.insert_one({'user_id': 'owner', 'order_number': '10', 'raw_by_source': {'salla_direct': {
                'id': 'order', 'reference_id': '10', 'created_at': '2026-10-06T09:00:00+00:00', 'updated_at': '2026-10-06T10:00:00+00:00',
                'status': 'in_progress', 'currency': 'SAR', 'total': {'amount': '10000', 'currency': 'SAR'},
                'items': [{'id': 'line', 'product_id': 'product', 'quantity': 1}]}}})
            await db.mezan_product_cost_profiles_v2.insert_one({'user_id': 'owner', 'salla_product_id': 'product', 'base_cost': '8000'})
            await db.mezan_preparation_pieces_v1.insert_one({'user_id': 'owner', 'id': 'piece', 'piece_id': 'piece', 'unit_index': 1,
                'order_number': '10', 'order_item_id': 'salla:10:line', 'supplier_id': 'supplier', 'status': 'in_progress'})
            await test(db)
            # Reading invoice evidence and operational payments never creates a writer collection.
            allowed = {'users', 'mz2_financial_accounts', 'mezan_suppliers_v2', 'unified_orders', 'mezan_product_cost_profiles_v2',
                       'mezan_preparation_pieces_v1', 'mezan_supplier_invoices_v2', 'operational_balance_states_v1', 'operational_balance_receipts_v1'}
            assert set(await db.list_collection_names()) <= allowed
        finally:
            await client.drop_database(db.name)
            client.close()
    asyncio.run(execute())


async def invoice(db, amount='5000', *, identity='invoice', approved=True):
    row = {'user_id': 'owner', 'id': identity, 'supplier_id': 'supplier',
           'lines': [{'line_number': 1, 'piece_ids': ['piece'], 'quantity': 1,
                      'product_price_authority': 'mezan_v2', 'product_unit_price_halalas': int(Decimal(amount)*100)}]}
    if approved:
        row['approved_at'] = '2026-10-07T10:00:00+00:00'
    await db.mezan_supplier_invoices_v2.insert_one(row)


async def balances(db):
    await refresh(db, 'owner', clock=NOW)
    state = await read(db, 'owner')
    return state, report(state)


def supplier_totals(result, party='supplier'):
    rows = [r for r in result['parties'] if r['party_type'] == 'supplier' and r['party_id'] == party]
    if not rows:
        return ('0.00', '0.00', '0.00')
    return tuple(rows[0][k] for k in ('expected_payable', 'confirmed_payable', 'outstanding_payable'))


def test_invoice_partial_8000_to_3000_expected_5000_confirmed_and_replay():
    async def scenario(db):
        state, result = await balances(db)
        assert supplier_totals(result) == ('8000.00', '0.00', '0.00')
        await invoice(db)
        state, result = await balances(db)
        assert supplier_totals(result) == ('3000.00', '5000.00', '5000.00')
        assert result['summary']['actual_liquidity'] == '10000.00'
        sources = await collect_sources(db, 'owner', START, NOW)
        first = reconcile(state, sources, NOW)
        assert reconcile(first, sources, NOW) == first
        assert len([k for k in first['engine']['facts'] if k.startswith('receipt:')]) == 1
    run(scenario)


def test_full_approved_invoice_eliminates_estimate():
    async def scenario(db):
        await invoice(db, '8000')
        _, result = await balances(db)
        assert supplier_totals(result) == ('0.00', '8000.00', '8000.00')
    run(scenario)


def test_draft_invoice_does_not_confirm_and_requires_no_accounting_journal():
    async def scenario(db):
        await invoice(db, approved=False)
        _, result = await balances(db)
        assert supplier_totals(result) == ('8000.00', '0.00', '0.00')
        await db.mezan_supplier_invoices_v2.update_one({'id': 'invoice'}, {'$set': {'approved_at': '2026-10-07T10:00:00+00:00'}})
        _, result = await balances(db)
        assert supplier_totals(result) == ('3000.00', '5000.00', '5000.00')
    run(scenario)


def test_cancellation_before_invoice_clears_only_estimate():
    async def scenario(db):
        await balances(db)
        await db.unified_orders.update_one({'order_number': '10'}, {'$set': {'raw_by_source.salla_direct.status': 'cancelled', 'raw_by_source.salla_direct.updated_at': NOW}})
        _, result = await balances(db)
        assert supplier_totals(result) == ('0.00', '0.00', '0.00')
        # An actually issued later invoice is still evidence; order cancellation cannot erase it.
        await invoice(db)
        _, result = await balances(db)
        assert supplier_totals(result) == ('0.00', '5000.00', '5000.00')
    run(scenario)


def test_cancellation_after_invoice_preserves_confirmed():
    async def scenario(db):
        await invoice(db)
        await balances(db)
        await db.unified_orders.update_one({'order_number': '10'}, {'$set': {'raw_by_source.salla_direct.status': 'cancelled', 'raw_by_source.salla_direct.updated_at': NOW}})
        _, result = await balances(db)
        assert supplier_totals(result) == ('0.00', '5000.00', '5000.00')
    run(scenario)


def test_piece_reassignment_moves_only_remaining_estimate():
    async def scenario(db):
        await invoice(db)
        await balances(db)
        await db.mezan_preparation_pieces_v1.update_one({'id': 'piece'}, {'$set': {'supplier_id': 'other-supplier'}})
        _, result = await balances(db)
        assert supplier_totals(result) == ('0.00', '5000.00', '5000.00')
        assert supplier_totals(result, 'other-supplier') == ('3000.00', '0.00', '0.00')
    run(scenario)


def test_supplier_payment_reduces_bank_and_confirmed_outstanding_once():
    async def scenario(db):
        await invoice(db)
        await balances(db)
        payload = {'request_id': 'payinvoice', 'party_type': 'supplier', 'party_id': 'supplier', 'bank_id': 'bank',
            'amount': '2000', 'currency': 'SAR', 'direction': 'outgoing', 'kind': 'payment', 'note': 'Invoice payment',
            'receipt_id': None, 'order_number': None, 'reference': 'payment-one', 'allocations': []}
        item = await create_movement(db, 'owner', 'owner', payload, clock=NOW)
        assert await create_movement(db, 'owner', 'owner', payload, clock=NOW) == item
        state, result = await balances(db)
        assert supplier_totals(result) == ('3000.00', '5000.00', '3000.00')
        assert result['summary']['actual_liquidity'] == '8000.00'
        assert len(state['movements']) == 1
        assert result['summary']['settled'] == '2000.00'
    run(scenario)


def test_accepted_supplier_return_reduces_confirmed_never_restores_estimate():
    async def scenario(db):
        await invoice(db)
        await balances(db)
        payload = {'request_id': 'supplierreturn', 'receipt_id': 'invoice:piece:product', 'amount': '1000',
                   'note': 'Supplier accepted return', 'evidence_receipt_id': 'accepted-return-document', 'supplier_id': 'supplier'}
        await db[RECEIPTS].insert_one({'_id': 'accepted-return-document', 'owner_id': 'owner', 'sha256': 'returnhash'})
        await supplier_return(db, 'owner', 'owner', payload, clock=NOW)
        _, result = await balances(db)
        assert supplier_totals(result) == ('3000.00', '4000.00', '4000.00')
        assert result['summary']['actual_liquidity'] == '10000.00'
    run(scenario)



def test_partial_invoice_covers_five_of_eight_native_piece_quantities():
    async def scenario(db):
        await db.unified_orders.update_one({'order_number': '10'}, {'$set': {'raw_by_source.salla_direct.items.0.quantity': 8}})
        await db.mezan_product_cost_profiles_v2.update_one({'salla_product_id': 'product'}, {'$set': {'base_cost': '1000'}})
        await db.mezan_preparation_pieces_v1.delete_many({'user_id': 'owner'})
        await db.mezan_preparation_pieces_v1.insert_many([{'user_id': 'owner', 'id': f'piece{i}', 'piece_id': f'piece{i}',
            'unit_index': i, 'order_number': '10', 'order_item_id': 'salla:10:line', 'supplier_id': 'supplier', 'status': 'in_progress'} for i in range(1, 9)])
        await db.mezan_supplier_invoices_v2.insert_one({'user_id': 'owner', 'id': 'quantity-invoice', 'supplier_id': 'supplier',
            'approved_at': '2026-10-07T10:00:00+00:00', 'lines': [{'line_number': 1, 'piece_ids': [f'piece{i}' for i in range(1, 6)],
            'quantity': 5, 'product_price_authority': 'mezan_v2', 'product_unit_price_halalas': 100000}]})
        before = {name: await db[name].find({}).to_list(100) for name in ('unified_orders', 'mezan_preparation_pieces_v1', 'mezan_supplier_invoices_v2')}
        state, result = await balances(db)
        assert supplier_totals(result) == ('3000.00', '5000.00', '5000.00')
        assert len([key for key in state['engine']['facts'] if key.startswith('receipt:')]) == 5
        _, repeated = await balances(db)
        assert supplier_totals(repeated) == supplier_totals(result)
        assert {name: await db[name].find({}).to_list(100) for name in before} == before
    run(scenario)
