"""Real Mongo/ASGI cancellation of an independently captured, untaxed advance."""
import asyncio
import unittest
from unittest.mock import patch
from fastapi import APIRouter
from accounting_customer_advances import install_customer_advance_routes
from accounting_write_control import set_write_state
import test_mz2_receivable_workflow as workflow

BASE = '/accounting-module/customer-advances'


class AdvanceTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = workflow.WorkflowTests.asyncTearDown
    configure = workflow.WorkflowTests.configure
    source = workflow.WorkflowTests.source
    payload = workflow.WorkflowTests.payload
    preview_and_post = workflow.WorkflowTests.preview_and_post

    async def asyncSetUp(self):
        await workflow.WorkflowTests.asyncSetUp(self)
        await self.db.orders_db.update_many({}, {'$set': {'order_status': 'pending'}, '$unset': {'delivered_at': ''}})
        router = APIRouter()
        async def authenticated():
            return {'id': self.actor}
        install_customer_advance_routes(router, self.db, authenticated)
        self.app.include_router(router)

    def capture_body(self, **kwargs):
        return dict(provider='tamara', payment_id='SYN-CAPTURE-tamara',
            evidence_ref='SYN-CAPTURE-DOCUMENT', original_tax_amount='0',
            tax_review_ref='SYN-REVIEW-NO-ORIGINAL-TAX', **kwargs)

    async def capture(self):
        response = await self.client.post(BASE, json=self.capture_body())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def cancel(self, row):
        await self.db.orders_db.update_many({}, {'$set': {'order_status': 'cancelled'}})
        response = await self.client.post(BASE+'/'+row['id']+'/cancel', json=dict(
            accounting_at='2020-01-31T23:00:00+03:00', evidence_ref='SYN-CANCELLATION'))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def refund_evidence(self, identity, value, at):
        await self.db.payment_refunds.insert_one(dict(user_id='owner', provider='tamara',
            provider_payment_id='SYN-CAPTURE-tamara', provider_refund_id=identity,
            status='completed', currency='SAR', amount=value, refunded_at=at,
            source='synthetic_provider_document'))
        return dict(amount=value, paid_at=at, execution_channel='tamara',
            execution_reference='DOC-'+identity, provider_refund_id=identity)

    async def test_capture_cancel_partial_refunds_duplicate_and_no_tax_or_sales(self):
        first, same = await asyncio.gather(self.capture(), self.capture())
        self.assertEqual(first['capture_txn_group_id'], same['capture_txn_group_id'])
        row = await self.cancel(first)
        self.assertEqual((await self.cancel(first))['due_txn_group_id'], row['due_txn_group_id'])
        body = await self.refund_evidence('SYN-REFUND-40', '40', '2020-02-02T10:00:00+03:00')
        url = BASE+'/'+row['id']+'/payments'
        response = await self.client.post(url, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.client.post(url, json=body)).json()['txn_group_id'], response.json()['txn_group_id'])
        self.assertEqual((await self.db.mz2_customer_advances.find_one({}))['remaining'], '75.00')
        second = await self.refund_evidence('SYN-REFUND-75', '75', '2020-02-05T10:00:00+03:00')
        response = await self.client.post(url, json=second)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.db.mz2_customer_advances.find_one({}))['remaining'], '0.00')
        entries = await self.db.general_ledger.find({}).to_list(100)
        self.assertEqual(len(entries), 8)
        self.assertFalse(any(e['entity_type'] in {'tax', 'revenue'} for e in entries))
        from decimal import Decimal
        for group in {e['txn_group_id'] for e in entries}:
            self.assertEqual(sum(Decimal(str(e['amount']))*(1 if e['side']=='debit' else -1)
                for e in entries if e['txn_group_id']==group), 0)
        jan = [e for e in entries if e['metadata']['accounting_at'] < '2020-02-01']
        self.assertEqual(sum(e['amount']*(1 if e['side']=='credit' else -1) for e in jan
            if e.get('sub_account')=='customer_refund_payable'), 115)
        self.assertEqual(await self.db.mz2_customer_refunds.count_documents({}), 0)
        self.assertEqual(await self.db.mz2_recognition_events.count_documents({}), 0)

    async def test_tax_review_permissions_pause_and_wrong_source_are_blocked(self):
        body = self.capture_body()
        body['original_tax_amount'] = '15'
        self.assertEqual((await self.client.post(BASE, json=body)).status_code, 409)
        body['original_tax_amount'] = '0'; body['tax_review_ref'] = ' '
        self.assertEqual((await self.client.post(BASE, json=body)).status_code, 409)
        self.actor = 'viewer'
        self.assertEqual((await self.client.post(BASE, json=self.capture_body())).status_code, 403)
        self.actor = 'owner'
        await set_write_state(self.db, owner='owner', actor_id='owner', paused=True, revision=0, reason='test')
        self.assertEqual((await self.client.post(BASE, json=self.capture_body())).status_code, 423)
        self.assertEqual((await self.client.get(BASE)).status_code, 200)
        await set_write_state(self.db, owner='owner', actor_id='owner', paused=False, revision=1, reason='test')
        await self.db.payment_transactions.update_many({}, {'$set': {'status': 'authorized'}})
        self.assertEqual((await self.client.post(BASE, json=self.capture_body())).status_code, 409)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)
        self.assertEqual(await self.db.mz2_customer_advances.count_documents({}), 0)

    async def test_existing_sale_cannot_be_recaptured_and_advance_blocks_later_sale(self):
        await self.db.orders_db.update_many({}, {'$set': {'order_status': 'completed', 'delivered_at': workflow.WHEN}})
        await self.preview_and_post()
        await self.db.orders_db.update_many({}, {'$set': {'order_status': 'cancelled'}, '$unset': {'delivered_at': ''}})
        response = await self.client.post(BASE, json=self.capture_body())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('existing_capture_or_tax', response.text)
        self.assertEqual(await self.db.mz2_customer_advances.count_documents({}), 0)

    async def test_advance_prevents_later_sale_and_unconfirmed_or_early_refund(self):
        row = await self.capture()
        denied = await self.client.post(BASE+'/'+row['id']+'/cancel', json=dict(
            accounting_at='2020-01-31T23:00:00+03:00', evidence_ref='SYN-CANCEL'))
        self.assertEqual(denied.status_code, 409)
        await self.db.orders_db.update_many({}, {'$set': {'order_status': 'completed', 'delivered_at': workflow.WHEN}})
        denied = await self.client.post('/accounting-module/receivables/preview', json=self.payload())
        self.assertEqual(denied.status_code, 200, denied.text)
        self.assertEqual(denied.json()['state'], 'rejected')
        self.assertIn('existing_journal_requires_review', denied.text)
        row = await self.cancel(row)
        payload = dict(amount='40', paid_at='2020-02-02T10:00:00+03:00', execution_channel='tamara',
            execution_reference='NO-CONFIRMATION', provider_refund_id='MISSING')
        denied = await self.client.post(BASE+'/'+row['id']+'/payments', json=payload)
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 4)

    async def test_mid_post_failure_rolls_back_capture_then_retry_once(self):
        from ledger_core import post_txn_group
        async def failed(*args, **kwargs):
            await post_txn_group(*args, **kwargs)
            raise RuntimeError('synthetic failure after journal insertion')
        with patch('ledger_core.post_txn_group', failed):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                await self.client.post(BASE, json=self.capture_body())
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)
        self.assertEqual(await self.db.mz2_customer_advances.count_documents({}), 0)
        await self.capture()
        self.assertEqual(await self.db.general_ledger.count_documents({}), 2)

    async def test_closed_capture_cancellation_and_payment_have_no_partial_effect(self):
        from accounting_periods import set_period, PeriodChange
        async def close(month, closed, revision):
            await set_period(self.db, 'owner', 'owner', PeriodChange(month=month, closed=closed,
                revision=revision, reason='Synthetic explicit closure', evidence_ref='SYN-CLOSE'))
        await close('2020-01', True, 0)
        denied = await self.client.post(BASE, json=self.capture_body())
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertIn('accounting_period_closed', denied.text)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)
        self.assertEqual(await self.db.mz2_customer_advances.count_documents({}), 0)
        # Explicit owner test action; the rejected operation never reopens or redates.
        await close('2020-01', False, 1)
        row = await self.capture()
        await close('2020-01', True, 2)
        await self.db.orders_db.update_many({}, {'$set': {'order_status': 'cancelled'}})
        cancel = dict(accounting_at='2020-01-31T23:00:00+03:00', evidence_ref='SYN-CANCELLATION')
        denied = await self.client.post(BASE+'/'+row['id']+'/cancel', json=cancel)
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 2)
        self.assertNotIn('cancellation', await self.db.mz2_customer_advances.find_one({}))
        await close('2020-01', False, 3)
        row = await self.cancel(row)
        await close('2020-02', True, 0)
        body = await self.refund_evidence('SYN-CLOSED-PAY', '40', '2020-02-02T10:00:00+03:00')
        denied = await self.client.post(BASE+'/'+row['id']+'/payments', json=body)
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 4)
        self.assertEqual(await self.db.mz2_customer_advance_payments.count_documents({}), 0)
        self.assertEqual((await self.db.mz2_customer_advances.find_one({}))['remaining'], '115.00')
        self.assertTrue((await self.db.mz2_accounting_periods.find_one({'month': '2020-02'}))['closed'])

    async def test_bank_reference_case_cannot_duplicate_other_refund_path(self):
        await self.db.accounts.insert_one(dict(id='bank', user_id='owner', account_type='bank', name='Synthetic bank'))
        row = await self.cancel(await self.capture())
        await self.db.mz2_customer_refund_payments.insert_one(dict(user_id='owner', status='posted',
            execution_channel='bank', bank_account_id='bank', bank_reference='SYN-Already-Paid'))
        denied = await self.client.post(BASE+'/'+row['id']+'/payments', json=dict(amount='40',
            paid_at='2020-02-02T10:00:00+03:00', execution_channel='bank', bank_account_id='bank',
            execution_reference='syn-already-paid'))
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertIn('refund_execution_already_accounted', denied.text)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 4)
        self.assertEqual(await self.db.mz2_customer_advance_payments.count_documents({}), 0)
