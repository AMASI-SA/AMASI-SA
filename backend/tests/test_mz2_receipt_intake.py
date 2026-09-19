"""Actual Mongo + actual installed lifecycle, isolated synthetic owners."""
import asyncio
import os
import unittest
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from fastapi import FastAPI, APIRouter
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from accounting_receipt_service import install_accounting_receipt_routes, bank_reference
from accounting_settlement_routes import install_accounting_settlement_routes
from accounting_settlement_lifecycle_routes import install_accounting_settlement_lifecycle_routes

BASE = '/accounting-module'


class IntakeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(os.environ['MZ2_TEST_MONGO_URI'])
        self.db = self.mongo['mz2_receipt_test_' + uuid4().hex]
        self.actor = 'owner'
        await self.db.users.insert_many([
            dict(id='owner', role='owner'),
            dict(id='entry', role='staff', created_by='owner', accounting_permissions=[
                'accounting.receipts.create', 'accounting.drafts.create', 'accounting.movements.view']),
            dict(id='viewer', role='viewer', created_by='owner', accounting_permissions=['accounting.settlements.view']),
            dict(id='other', role='owner'),
        ])
        await self.db.accounts.insert_one(dict(id='bank', user_id='owner', name='SYN bank', account_type='bank'))
        await self.db.accounting_provider_bank_bindings_v2.insert_one(dict(
            user_id='owner', provider='tabby', bank_account_id='bank', verification_status='verified'))
        app = FastAPI()
        router = APIRouter()
        async def actor():
            return {'id': self.actor}
        install_accounting_receipt_routes(router, self.db, actor)
        # The first handler in the real app must be the one tested.
        install_accounting_settlement_lifecycle_routes(router, self.db, actor)
        install_accounting_settlement_routes(router, self.db, actor)
        app.include_router(router)
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url='http://test')
        self.body = dict(provider='tabby', amount='104.65', bank_message='SYN bank ref: SYN-RECEIPT-01',
                         received_on='2026-09-19', request_id=str(uuid4()))

    async def asyncTearDown(self):
        await self.client.aclose()
        self.mongo.close()

    async def receipt(self, **changes):
        response = await self.client.post(BASE + '/bank-receipts', json={**self.body, **changes})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def statement(self, name='draft'):
        doc = dict(id=name, user_id='owner', provider='tabby', currency='SAR', status='draft',
            version=1, receipt_workflow_version=2, bank_account_id='bank', source_file_id='SYN-file',
            source_file_hash='SYN-hash', statement_reference='SYN-' + name, source_review_count=0,
            idempotency_key='SYN-' + name, source_snapshot={'matched': 1, 'unmatched': 0},
            amounts=dict(gross_sales=115, reported_net=104.65, commission=3, commission_vat=.45,
                         settlement_fee=6, settlement_fee_vat=.9))
        await self.db.accounting_settlements_v2.insert_one(doc)
        return doc

    async def link(self, receipt, draft='draft'):
        return await self.client.put(BASE + '/settlements/drafts/' + draft + '/receipt',
                                     json={'receipt_id': receipt['id']})

    async def transition(self, action, draft='draft'):
        return await self.client.post(BASE + '/settlements/drafts/' + draft + '/' + action, json={})

    async def assert_no_finance(self):
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)
        self.assertEqual(await self.db.account_transactions.count_documents({}), 0)

    async def test_both_input_orders_and_no_financial_write(self):
        receipt = await self.receipt()
        await self.assert_no_finance()
        await self.statement()
        self.assertEqual((await self.link(receipt)).status_code, 200)
        self.assertEqual((await self.transition('submit')).status_code, 200)
        await self.assert_no_finance()
        await self.statement('second')
        second = await self.receipt(bank_message='SYN ref: SYN-SECOND', request_id=str(uuid4()))
        self.assertEqual((await self.link(second, 'second')).status_code, 200)
        await self.assert_no_finance()

    async def test_missing_bank_and_difference_block_review_and_post(self):
        await self.statement()
        self.assertEqual((await self.transition('submit')).status_code, 409)
        receipt = await self.receipt(amount='104.66')
        self.assertEqual((await self.link(receipt)).status_code, 200)
        self.assertEqual((await self.transition('submit')).status_code, 409)
        self.assertEqual((await self.transition('post')).status_code, 409)
        await self.assert_no_finance()

    async def test_ambiguous_candidates_never_auto_link(self):
        await self.statement()
        await self.receipt()
        await self.receipt(bank_message='SYN ref: SYN-SECOND', request_id=str(uuid4()))
        result = await self.client.get(BASE + '/bank-receipts')
        self.assertFalse(result.json()['auto_link'])
        self.assertEqual(len(result.json()['items']), 2)
        self.assertNotIn('bank_receipt_id', await self.db.accounting_settlements_v2.find_one({'id': 'draft'}))
        self.assertEqual((await self.transition('submit')).status_code, 409)

    async def test_concurrent_retry_and_lost_response_same_receipt(self):
        results = await asyncio.gather(self.receipt(), self.receipt())
        self.assertEqual(results[0]['id'], results[1]['id'])
        replay = await self.receipt(request_id=str(uuid4()))
        self.assertEqual(replay['id'], results[0]['id'])
        self.assertEqual(await self.db.mz2_bank_receipts.count_documents({}), 1)
        conflict = await self.client.post(BASE + '/bank-receipts', json={**self.body, 'amount': '200'})
        self.assertEqual(conflict.status_code, 409)
        await self.assert_no_finance()

    async def test_permissions_and_cross_owner_reject_without_writes(self):
        await self.statement()
        self.actor = 'entry'
        receipt = await self.receipt()
        self.assertEqual((await self.link(receipt)).status_code, 200)
        self.assertEqual((await self.transition('submit')).status_code, 200)
        self.assertEqual((await self.transition('review')).status_code, 403)
        self.assertEqual((await self.transition('post')).status_code, 403)
        self.actor = 'viewer'
        self.assertEqual((await self.client.get(BASE + '/bank-receipts')).status_code, 200)
        self.assertEqual((await self.client.post(BASE + '/bank-receipts', json=self.body)).status_code, 403)
        self.assertEqual((await self.link(receipt)).status_code, 403)
        self.actor = 'other'
        self.assertEqual((await self.link(receipt)).status_code, 404)
        self.assertEqual((await self.client.get(BASE + '/bank-receipts')).json()['items'], [])
        await self.assert_no_finance()

    async def test_one_receipt_cannot_link_two_statements(self):
        await self.statement(); await self.statement('second')
        receipt = await self.receipt()
        answers = await asyncio.gather(self.link(receipt), self.link(receipt, 'second'))
        self.assertEqual(sorted(x.status_code for x in answers), [200, 409])
        self.assertEqual(await self.db.accounting_settlements_v2.count_documents({'bank_receipt_id':receipt['id']}), 1)

    async def test_active_post_route_atomic_abort_and_retry(self):
        import accounting_settlement_lifecycle_routes as lifecycle
        await self.statement()
        receipt = await self.receipt()
        await self.link(receipt)
        await self.transition('submit'); await self.transition('review')
        # Balanced preexisting synthetic sale, independent of the settlement.
        await self.db.general_ledger.insert_many([
            dict(id='sale-d',user_id='owner',txn_group_id='sale',entity_type='payment_gateway',
                 entity_id='tabby',sub_account='receivable',side='debit',amount=115,status='posted'),
            dict(id='sale-c',user_id='owner',txn_group_id='sale',entity_type='revenue',
                 entity_id='sales',side='credit',amount=115,status='posted')])
        original = lifecycle._audit_state
        async def fail(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError('SYN interruption after ledger, receipt and draft')
        with patch.object(lifecycle, '_audit_state', fail):
            with self.assertRaises(RuntimeError):
                await self.transition('post')
        self.assertEqual(await self.db.general_ledger.count_documents({}), 2)
        self.assertEqual((await self.db.accounting_settlements_v2.find_one({'id':'draft'}))['status'], 'reviewed')
        self.assertEqual((await self.db.mz2_bank_receipts.find_one({'id':receipt['id']}))['status'], 'linked')
        result = await self.transition('post')
        self.assertEqual(result.status_code, 200, result.text)
        group = result.json()['ledger_txn_group_id']
        rows = await self.db.general_ledger.find({'txn_group_id':group}).to_list(20)
        self.assertEqual(len(rows), 6)
        self.assertEqual(sum(Decimal(str(r['amount'])) for r in rows if r['side']=='debit'), 115)
        self.assertEqual(sum(Decimal(str(r['amount'])) for r in rows if r['side']=='credit'), 115)
        self.assertEqual((await self.transition('post')).status_code, 409)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 8)
        saved = await self.db.mz2_bank_receipts.find_one({'id':receipt['id']})
        self.assertEqual(saved['ledger_txn_group_id'], group)
        self.assertEqual(await self.db.account_transactions.count_documents({}), 0)
        print('SYNTHETIC_EVIDENCE', self.db.name, group, receipt['id'])

    def test_explicit_bank_reference_only(self):
        self.assertIsNone(bank_reference('وصل 104.65 يوم 2026-09-19 إلى حساب 123456'))
        self.assertEqual(bank_reference('مرجع: SYN-BANK-001'), 'SYN-BANK-001')

    async def test_missing_reference_duplicate_is_not_guessed(self):
        await self.receipt(bank_message='SYN amount received')
        retry = await self.client.post(BASE + '/bank-receipts', json={
            **self.body, 'bank_message': 'SYN another message', 'request_id': str(uuid4())})
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(await self.db.mz2_bank_receipts.count_documents({}), 1)
        await self.assert_no_finance()

    async def test_invalid_amount_and_unverified_binding_write_nothing(self):
        for amount in ['NaN', '-1', '0', '1.001']:
            result = await self.client.post(BASE + '/bank-receipts', json={**self.body,'amount':amount})
            self.assertEqual(result.status_code, 422)
        await self.db.accounting_provider_bank_bindings_v2.update_one({}, {'$set':{'verification_status':'unverified'}})
        result = await self.client.post(BASE + '/bank-receipts', json=self.body)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(await self.db.mz2_bank_receipts.count_documents({}), 0)
        await self.assert_no_finance()


if __name__ == '__main__':
    unittest.main()
