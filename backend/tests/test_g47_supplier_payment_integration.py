"""Synthetic supplier payments through real V2 writer and local Mongo transactions."""
import asyncio
import unittest
import uuid
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx
from fastapi import APIRouter
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

import test_g47_purchase_approval_integration as purchase_fixture
from accounting_atomic import atomic_owner
from accounting_ledger_v2 import compute_balance_v2, post_journal_v2, verify_journal_v2
from liabilities_routes import attach_liabilities_routes
from supplier_ledger_detail_routes import make_supplier_ledger_detail_router
from supplier_payment_service import OPERATIONS
import supplier_payment_service as payments


class LosePaymentResponse(httpx.AsyncBaseTransport):
    def __init__(self, app):
        self.inner = httpx.ASGITransport(app=app)
        self.lost = False

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if request.url.path.endswith('/payments') and not self.lost:
            self.lost = True
            await response.aread()
            await response.aclose()
            raise httpx.ReadError('Synthetic committed response loss', request=request)
        return response

    async def aclose(self):
        await self.inner.aclose()


class SupplierPaymentMongoIntegration(unittest.IsolatedAsyncioTestCase):
    seed = purchase_fixture.PurchaseApprovalMongoIntegration.seed
    drop_disposable_database = purchase_fixture.PurchaseApprovalMongoIntegration.drop_disposable_database
    payload = purchase_fixture.PurchaseApprovalMongoIntegration.payload
    draft = purchase_fixture.PurchaseApprovalMongoIntegration.draft
    request_for = purchase_fixture.PurchaseApprovalMongoIntegration.request_for
    approve = purchase_fixture.PurchaseApprovalMongoIntegration.approve

    async def asyncSetUp(self):
        await purchase_fixture.PurchaseApprovalMongoIntegration.asyncSetUp(self)
        router = APIRouter(prefix='/api')
        attach_liabilities_routes(router, self.db)

        async def synthetic_identity():
            return dict(self.actor)

        router.include_router(make_supplier_ledger_detail_router(self.db, synthetic_identity))
        self.app.include_router(router)
        pending = list(self.app.routes)
        while pending:
            route = pending.pop()
            included = getattr(route, 'original_router', None)
            if included is not None:
                pending.extend(included.routes)
            for dependency in getattr(getattr(route, 'dependant', None), 'dependencies', []):
                if getattr(dependency.call, '__name__', None) == 'current_user':
                    self.app.dependency_overrides[dependency.call] = synthetic_identity
        await self.db.mz2_financial_accounts.insert_one({'id': 'bank', 'user_id': 'owner', 'name': 'Synthetic bank', 'account_type': 'bank', 'currency': 'SAR', 'status': 'active'})
        await self.db.settings.update_one({'user_id': 'owner'}, {'$push': {'mezan2_financial_cutover.opening_balance_zero_accounts': {
            'entity_type': 'bank', 'entity_id': 'bank', 'sub_account': 'main', 'accounting_at': '2026-09-01T00:00:00.000000Z',
            'opening_balance_txn_group_id': 'zero:synthetic-opening', 'evidence_ref': 'SYNTHETIC-ZERO-BANK'}}})
        await self.seed_journal('bank-funding', [
            {'leg_key': 'bank', 'entity_type': 'bank', 'entity_id': 'bank', 'sub_account': 'main', 'side': 'debit', 'amount': '100.00'},
            {'leg_key': 'equity', 'entity_type': 'equity', 'entity_id': 'fixture', 'sub_account': 'opening', 'side': 'credit', 'amount': '100.00'}])

    async def seed_journal(self, key, entries, effective_at='2026-09-02T00:00:00+00:00'):
        async def post(scoped):
            return await post_journal_v2(scoped._db, user_id='owner', actor_id='owner', actor_name='Synthetic owner',
                idempotency_key='synthetic:'+key, txn_type='synthetic_fixture', source='synthetic_fixture',
                effective_at=effective_at, entries=[{**r, 'entry_type': 'synthetic_fixture'} for r in entries],
                metadata={'evidence': 'synthetic-local-only'}, mongo_session=scoped._session)
        return await atomic_owner(self.db, 'owner', post)

    async def approved(self):
        invoice = await self.draft()
        response = await self.approve(invoice)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def context(self, invoice=None):
        base = f"/api/purchase-invoices/{invoice['id']}" if invoice else '/api/purchase-invoices/supplier/supplier'
        response = await self.client.get(base+'/payment-context')
        self.assertEqual(response.status_code, 200, response.text)
        return base, response.json()

    def payment(self, context, amount='10.00', **changes):
        value = {'operation_id': context['operation_id'], 'expected_payment_revision': context['expected_payment_revision'],
            'amount': amount, 'paid_from_account_id': 'bank', 'payment_date': '2026-09-26', 'notes': 'Synthetic payment'}
        value.update(changes)
        return value

    async def assert_legacy_empty(self):
        for name in ['general_ledger', 'account_transactions', 'financial_movements', 'mz2_outgoing_financial_events']:
            self.assertEqual(await self.db[name].count_documents({}), 0, name)

    async def assert_balances(self, payable, bank):
        supplier = await compute_balance_v2(self.db, user_id='owner', entity_type='supplier', entity_id='supplier', sub_account='payable')
        funds = await compute_balance_v2(self.db, user_id='owner', entity_type='bank', entity_id='bank', sub_account='main')
        self.assertEqual(supplier['net_balance_minor'], -payable)
        self.assertEqual(funds['net_balance_minor'], bank)
        async for group in self.db.accounting_journal_groups_v2.find({}):
            self.assertTrue((await verify_journal_v2(self.db, user_id='owner', txn_group_id=group['txn_group_id']))['verified'])
        await self.assert_legacy_empty()

    async def test_purchase_partial_second_payment_zero_payable_and_real_supplier_ledger(self):
        invoice = await self.approved()
        base, context = await self.context(invoice)
        self.assertEqual(context['supplier_entity_id'], 'supplier')
        self.assertEqual(context['remaining_amount'], '30.00')
        first = await self.client.post(base+'/payments', json=self.payment(context))
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()['liability']['status'], 'partial')
        await self.assert_balances(2000, 9000)
        _, context = await self.context(invoice)
        second = await self.client.post(base+'/payments', json=self.payment(context, '20.00'))
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()['liability']['status'], 'paid')
        await self.assert_balances(0, 7000)
        statement = await self.client.get('/api/purchase-invoices/supplier/supplier/statement')
        self.assertEqual(statement.status_code, 200, statement.text)
        self.assertEqual(statement.json()['totals']['balance_owed'], '0.00')
        ledger = await self.client.get('/api/accounting/suppliers/supplier/ledger-detail')
        self.assertEqual(ledger.status_code, 200, ledger.text)
        self.assertEqual(ledger.json()['ledger_backend'], 'v2')
        self.assertEqual(len(ledger.json()['timeline']), 3)
        self.assertEqual(ledger.json()['period']['total_invoiced'], 30)
        self.assertEqual(ledger.json()['period']['total_paid'], 30)
        self.assertEqual(ledger.json()['reconciliation']['gl_balance_total'], 0)
        self.assertEqual(len(ledger.json()['invoices']), 1)
        self.assertEqual(ledger.json()['invoices'][0]['status'], 'paid')
        self.assertEqual(len(ledger.json()['invoices'][0]['payments_applied']), 2)
        self.assertEqual(len(ledger.json()['invoices'][0]['gl_legs']), 2)
        self.assertEqual({leg['side'] for leg in ledger.json()['invoices'][0]['gl_legs']}, {'debit', 'credit'})

    async def test_overpayment_and_low_v2_funds_rejected_without_effects(self):
        invoice = await self.approved()
        base, context = await self.context(invoice)
        response = await self.client.post(base+'/payments', json=self.payment(context, '30.01'))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'supplier_payment_exceeds_payable')
        await self.seed_journal('withdraw', [
            {'leg_key': 'bank', 'entity_type': 'bank', 'entity_id': 'bank', 'sub_account': 'main', 'side': 'credit', 'amount': '95.00'},
            {'leg_key': 'equity', 'entity_type': 'equity', 'entity_id': 'fixture', 'sub_account': 'opening', 'side': 'debit', 'amount': '95.00'}])
        _, context = await self.context(invoice)
        response = await self.client.post(base+'/payments', json=self.payment(context))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'insufficient_v2_bank_balance')
        self.assertEqual((await self.db.liabilities.find_one({}))['paid_amount'], 0)
        await self.assert_balances(3000, 500)

    async def test_concurrent_same_request_and_lost_response_exactly_once(self):
        invoice = await self.approved()
        base, context = await self.context(invoice)
        payload = self.payment(context)
        replies = await asyncio.gather(*[self.client.post(base+'/payments', json=payload) for _ in range(2)])
        self.assertEqual([r.status_code for r in replies], [200, 200])
        self.assertEqual(replies[0].json(), replies[1].json())
        await self.assert_balances(2000, 9000)
        _, context = await self.context(invoice)
        payload = self.payment(context, '20.00')
        lost = httpx.AsyncClient(transport=LosePaymentResponse(self.app), base_url='http://synthetic.local')
        self.addAsyncCleanup(lost.aclose)
        with self.assertRaises(httpx.ReadError):
            await lost.post(base+'/payments', json=payload)
        replay = await self.client.post(base+'/payments', json=payload)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(await self.db[OPERATIONS].count_documents({'status': 'succeeded'}), 2)
        await self.assert_balances(0, 7000)
        altered = await self.client.post(base+'/payments', json={**payload, 'amount': '19.00'})
        self.assertEqual(altered.status_code, 409, altered.text)

    async def test_concurrent_different_attempts_cannot_overpay(self):
        invoice = await self.approved()
        base, context = await self.context(invoice)
        a = self.payment(context, '20.00')
        b = {**a, 'operation_id': str(uuid.uuid4())}
        replies = await asyncio.gather(self.client.post(base+'/payments', json=a), self.client.post(base+'/payments', json=b))
        self.assertEqual(sorted(r.status_code for r in replies), [200, 409])
        await self.assert_balances(1000, 8000)
        self.assertEqual((await self.db.liabilities.find_one({}))['paid_amount'], 20)

    async def test_closed_period_pause_and_permission_revocation_fail_closed(self):
        invoice = await self.approved()
        base, context = await self.context(invoice)
        await self.db.mz2_accounting_periods.insert_one({'user_id': 'owner', 'month': '2026-09', 'closed': True})
        response = await self.client.post(base+'/payments', json=self.payment(context))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'accounting_period_closed')
        await self.db.mz2_accounting_periods.delete_many({})
        await self.db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'writes_paused': True}})
        response = await self.client.post(base+'/payments', json=self.payment(context))
        self.assertEqual(response.status_code, 423, response.text)
        await self.db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'writes_paused': False}})
        await self.db.users.update_one({'id': 'owner'}, {'$set': {'role': 'employee', 'created_by': 'owner', 'accounting_permissions': ['accounting.inventory.view']}})
        response = await self.client.post(base+'/payments', json=self.payment(context))
        self.assertEqual(response.status_code, 403, response.text)
        await self.assert_balances(3000, 10000)

    async def test_unlinked_counterparty_rejected_before_purchase_and_rechecked_at_approval(self):
        await self.db.counterparties.insert_one({'id': 'unlinked', 'user_id': 'owner', 'kind': 'supplier', 'name': 'Synthetic supplier'})
        response = await self.client.post('/api/purchase-invoices', json=self.payload(supplier_counterparty_id='unlinked', supplier_account_id='unlinked'))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'supplier_verified_identity_required')
        invoice = await self.draft()
        await self.db.suppliers.delete_one({'id': 'supplier'})
        response = await self.approve(invoice)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.db.liabilities.count_documents({}), 0)
        self.assertEqual(await self.db.mezan_inventory_receipts_v2.count_documents({}), 0)
        await self.assert_balances(0, 10000)

    async def test_identity_change_bank_tenant_and_projection_tamper_fail_closed(self):
        invoice = await self.approved()
        base, context = await self.context(invoice)
        await self.db.suppliers.update_one({'id': 'supplier'}, {'$set': {'status': 'inactive'}})
        response = await self.client.post(base+'/payments', json=self.payment(context))
        self.assertEqual(response.status_code, 409, response.text)
        await self.db.suppliers.update_one({'id': 'supplier'}, {'$set': {'status': 'active'}})
        await self.db.mz2_financial_accounts.insert_one({'id': 'foreign', 'user_id': 'other', 'account_type': 'bank'})
        response = await self.client.post(base+'/payments', json=self.payment(context, paid_from_account_id='foreign'))
        self.assertEqual(response.status_code, 409, response.text)
        _, context = await self.context(invoice)
        await self.db.liabilities.update_one({'id': invoice['liability_id']}, {'$set': {'paid_amount': 5}})
        response = await self.client.post(base+'/payments', json=self.payment(context))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'supplier_payment_projection_requires_reconciliation')
        await self.assert_balances(3000, 10000)

    async def test_old_pay_dispatch_has_no_legacy_fallback_and_projection_is_immutable(self):
        invoice = await self.approved()
        base, context = await self.context(invoice)
        old = '/api/liabilities/'+invoice['liability_id']
        payload = self.payment(context)
        response = await self.client.post(old+'/pay', json={k:v for k,v in payload.items() if k not in {'operation_id', 'expected_payment_revision'}})
        self.assertEqual(response.status_code, 409, response.text)
        response = await self.client.post(old+'/pay', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.client.put(old, json={'expected_amount': 999})).status_code, 409)
        self.assertEqual((await self.client.delete(old)).status_code, 409)
        await self.assert_balances(2000, 9000)

    async def test_unallocated_supplier_payable_excludes_invoice_allocations(self):
        invoice = await self.approved()
        await self.seed_journal('supplier-opening', [
            {'leg_key': 'supplier', 'entity_type': 'supplier', 'entity_id': 'supplier', 'sub_account': 'payable', 'side': 'credit', 'amount': '15.00'},
            {'leg_key': 'equity', 'entity_type': 'equity', 'entity_id': 'fixture', 'sub_account': 'opening', 'side': 'debit', 'amount': '15.00'}])
        base, context = await self.context()
        self.assertEqual(context['remaining_amount'], '15.00')
        response = await self.client.post(base+'/payments', json=self.payment(context, '15.01'))
        self.assertEqual(response.status_code, 409, response.text)
        _, context = await self.context()
        response = await self.client.post(base+'/payments', json=self.payment(context, '15.00'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()['liability'])
        self.assertEqual((await self.db.liabilities.find_one({'id': invoice['liability_id']}))['paid_amount'], 0)
        await self.assert_balances(3000, 8500)

    async def test_real_journal_aborts_with_projection_failure_then_stable_retry(self):
        invoice = await self.approved()
        base, context = await self.context(invoice)
        payload = self.payment(context)
        real_post = payments.post_journal_v2

        async def fail_after_real_journal(*args, **kwargs):
            await real_post(*args, **kwargs)
            raise HTTPException(409, detail={'code': 'synthetic_projection_failure'})

        with patch.object(payments, 'post_journal_v2', fail_after_real_journal):
            response = await self.client.post(base+'/payments', json=payload)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual((await self.db[OPERATIONS].find_one({}))['status'], 'failed')
        self.assertEqual((await self.db.liabilities.find_one({}))['paid_amount'], 0)
        await self.assert_balances(3000, 10000)
        response = await self.client.post(base+'/payments', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        await self.assert_balances(2000, 9000)

    async def test_v2_state_and_safe_active_are_mandatory(self):
        invoice = await self.approved()
        base, context = await self.context(invoice)
        await self.db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'ledger_backend_state': 'legacy_active'}})
        response = await self.client.post(base+'/payments', json=self.payment(context))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'supplier_payment_requires_v2_active')
        await self.db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'ledger_backend_state': 'v2_active'}})
        await self.db.settings.update_one({'user_id': 'owner'}, {'$set': {'mezan2_financial_cutover.status': 'draft'}})
        response = await self.client.post(base+'/payments', json=self.payment(context))
        self.assertEqual(response.status_code, 409, response.text)
        await self.assert_balances(3000, 10000)

    async def test_standalone_has_zero_payment_effects(self):
        mongo = AsyncIOMotorClient(purchase_fixture.local_uri('MZ2_TEST_STANDALONE_URI'), serverSelectionTimeoutMS=3000)
        name = 'g47_supplier_standalone_'+uuid.uuid4().hex
        db = mongo[name]
        try:
            self.assertFalse((await mongo.admin.command('hello')).get('setName'))
            await db.users.insert_one(dict(self.actor))
            with self.assertRaises(HTTPException) as failure:
                await payments.pay_supplier(db, user=self.actor, supplier_id='supplier', payload={
                    'operation_id': str(uuid.uuid4()), 'expected_payment_revision': 0, 'amount': '1.00',
                    'paid_from_account_id': 'bank', 'payment_date': '2026-09-26'})
            self.assertEqual(failure.exception.status_code, 503)
            for name_ in [OPERATIONS, 'accounting_journal_groups_v2', 'accounting_general_ledger_v2', 'liabilities', 'general_ledger', 'account_transactions']:
                self.assertEqual(await db[name_].count_documents({}), 0)
        finally:
            self.assertTrue(name.startswith('g47_supplier_standalone_'))
            await mongo.drop_database(name)
            mongo.close()

    async def test_future_only_bank_funds_cannot_authorize_current_payment(self):
        invoice = await self.approved()
        await self.seed_journal('empty-bank', [
            {'leg_key': 'bank', 'entity_type': 'bank', 'entity_id': 'bank', 'sub_account': 'main', 'side': 'credit', 'amount': '100.00'},
            {'leg_key': 'equity', 'entity_type': 'equity', 'entity_id': 'fixture', 'sub_account': 'opening', 'side': 'debit', 'amount': '100.00'}])
        await self.seed_journal('future-funds', [
            {'leg_key': 'bank', 'entity_type': 'bank', 'entity_id': 'bank', 'sub_account': 'main', 'side': 'debit', 'amount': '100.00'},
            {'leg_key': 'equity', 'entity_type': 'equity', 'entity_id': 'fixture', 'sub_account': 'opening', 'side': 'credit', 'amount': '100.00'}], effective_at='2099-01-01T00:00:00+00:00')
        base, context = await self.context(invoice)
        self.assertEqual(context['banks'][0]['balance'], '0.00')
        response = await self.client.post(base+'/payments', json=self.payment(context))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'insufficient_v2_bank_balance')
        self.assertEqual((await self.db.liabilities.find_one({}))['paid_amount'], 0)
        await self.assert_legacy_empty()

    async def test_missing_bank_opening_and_invalid_group_cannot_authorize(self):
        invoice = await self.approved()
        base, context = await self.context(invoice)
        settings = await self.db.settings.find_one({'user_id': 'owner'})
        zeros = settings['mezan2_financial_cutover']['opening_balance_zero_accounts']
        await self.db.settings.update_one({'user_id': 'owner'}, {'$pull': {'mezan2_financial_cutover.opening_balance_zero_accounts': {'entity_id': 'bank'}}})
        response = await self.client.post(base+'/payments', json=self.payment(context))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['reason'], 'accounts_require_approved_opening')
        await self.db.settings.update_one({'user_id': 'owner'}, {'$set': {'mezan2_financial_cutover.opening_balance_zero_accounts': zeros}})
        group = await self.db.accounting_journal_groups_v2.find_one({'source': 'synthetic_fixture'})
        await self.db.accounting_journal_groups_v2.delete_one({'_id': group['_id']})
        response = await self.client.post(base+'/payments', json=self.payment(context))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'supplier_payment_ledger_not_ready')
        self.assertEqual((await self.db.liabilities.find_one({}))['paid_amount'], 0)
        self.assertEqual(await self.db.accounting_general_ledger_v2.count_documents({'entry_type': 'supplier_payment'}), 0)
        await self.assert_legacy_empty()

    async def test_future_payment_date_rejected_with_zero_effects(self):
        invoice = await self.approved()
        base, context = await self.context(invoice)
        response = await self.client.post(base+'/payments', json=self.payment(context, payment_date='2099-01-01'))
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()['detail']['code'], 'supplier_payment_future_date_not_allowed')
        await self.assert_balances(3000, 10000)

    async def test_zero_opening_cannot_hide_unrelated_uncosted_positive_stock(self):
        await self.db.warehouse_locations.update_one({'id': 'location-component'}, {'$set': {'occupancy': {'total_quantity': 5, 'items': [
            {'item_type': 'stock_component', 'resource_id': 'component-A', 'quantity': 5}]}}})
        payload = self.payload()
        payload['lines'] = [payload['lines'][1]]
        invoice = await self.draft(payload)
        response = await self.approve(invoice)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'inventory_cost_reconciliation_required')
        identity = {'item_type': 'stock_component', 'resource_id': 'component-A'}
        key = purchase_fixture.receiving.identity_key('owner', identity)
        for stored_key, policy in [(key, 'unapproved-policy'), ('wrong-key', purchase_fixture.receiving.COST_POLICY)]:
            await self.db.mz2_inventory_cost_states.delete_many({})
            await self.db.mz2_inventory_cost_states.insert_one({'_id': stored_key, 'user_id': 'owner', 'inventory_identity': identity,
                'authoritative': True, 'average_cost': '1.00', 'cost_policy_version': policy})
            response = await self.approve(invoice)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['detail']['code'], 'inventory_cost_reconciliation_required')
        self.assertEqual(await self.db.liabilities.count_documents({}), 0)
        self.assertEqual(await self.db.mezan_inventory_receipts_v2.count_documents({}), 0)
        await self.assert_balances(0, 10000)
