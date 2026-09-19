"""Real Mongo acceptance: notifications draft, daily approval alone posts."""
import asyncio
from decimal import Decimal
import unittest
from unittest.mock import patch
import test_mz2_receivable_workflow as fixtures
from accounting_order_refunds import process_order_refunds, link_statement_refund, refund_review_reasons
from accounting_atomic import atomic_owner
from accounting_receivable_service import prepare
from accounting_recognition_evidence import EvidenceError
from ledger_core import post_txn_group, compute_balance

BASE='/accounting-module/customer-refunds'
ACTOR={'id':'owner'}


class DailyRefundTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.WorkflowTests.asyncSetUp
    asyncTearDown=fixtures.WorkflowTests.asyncTearDown
    configure=fixtures.WorkflowTests.configure
    source=fixtures.WorkflowTests.source
    payload=fixtures.WorkflowTests.payload
    preview_and_post=fixtures.WorkflowTests.preview_and_post

    async def post(self,path,body=None):
        answer=await self.client.post(BASE+path,json=body)
        self.assertEqual(answer.status_code,200,answer.text)
        return answer.json()

    async def setup_sale(self,provider='tamara',gross='200'):
        if provider!='tamara':
            await self.source(provider)
        await self.db.payment_transactions.update_one({'provider':provider},{'$set':{'amount':gross,'captured_amount':gross}})
        await self.db.orders_db.update_one({'payment_method':provider},{'$set':{'total_amount':gross}})
        await self.preview_and_post(provider)
        sale=await self.db.mz2_recognition_events.find_one({'proposal.event.provider':provider})
        return sale['_id']

    async def bank(self):
        await self.db.accounts.insert_one({'id':'bank','user_id':'owner','account_type':'bank','name':'SYN bank'})
        async def write(scoped):
            return await post_txn_group(scoped,user_id='owner',actor_id='owner',actor_name='SYN',
                entries=[dict(entity_type='bank',entity_id='bank',sub_account='main',side='debit',amount=1000,entry_type='bank_transfer'),
                         dict(entity_type='equity',entity_id='SYN',side='credit',amount=1000,entry_type='bank_transfer')],txn_type='bank_transfer')
        await atomic_owner(self.db,'owner',write)

    async def movement(self,key,reference,amount,channel='bank',rid=None):
        return await self.post('/bank-payments',dict(original_key=key,case_reference=reference,
            bank_account_id='bank' if channel=='bank' else '',amount=amount,paid_at='2020-01-04T12:00:00Z',
            bank_reference='SYN-'+reference+'-'+amount,execution_channel=channel,provider_refund_id=rid))

    async def case(self,key,reference,amount):
        return await self.post('',dict(original_key=key,case_reference=reference,amount=amount,
            recognized_at='2020-01-03T12:00:00Z',reason='SYN required refund'))

    async def notify(self,provider,amount=None,cancel=False):
        payload={'status':{'slug':'canceled' if cancel else 'partially_refunded'}}
        if amount:
            payload['payment_actions']={'refund_action':{'refund_amount':{'amount':amount,'currency':'SAR'}}}
        return await process_order_refunds(self.db,owner='owner',order_number='SYN-MANUAL-TAX-'+provider,
            source={'kind':'SYN_verified_webhook'},payload=payload)

    async def test_bank_two_payments_30_20_webhook_first_and_daily_first(self):
        await self.bank()
        for i,provider in enumerate(['salla','tamara','tabby','emkan']):
            key=await self.setup_sale(provider)
            before=await self.db.general_ledger.count_documents({})
            if i%2==0:
                await asyncio.gather(self.notify(provider,50),self.notify(provider,50))
                row=await self.db.mz2_customer_refunds.find_one({'original_key':key})
            else:
                row=await self.case(key,'SYN-case-'+provider,'50')
            self.assertEqual(await self.db.general_ledger.count_documents({}),before)
            for value,remaining in [('30','20.00'),('20','0.00')]:
                payment=await self.movement(key,row['case_reference'],value)
                before=await self.db.general_ledger.count_documents({})
                results=await asyncio.gather(self.post('/bank-payments/'+payment['id']+'/approve'),self.post('/bank-payments/'+payment['id']+'/approve'))
                self.assertEqual(results[0]['txn_group_id'],results[1]['txn_group_id'])
                self.assertEqual(await self.db.general_ledger.count_documents({}),before+3)
                current=await self.db.mz2_customer_refunds.find_one({'id':row['id']})
                self.assertEqual(current['remaining'],remaining)
            before=await self.db.general_ledger.count_documents({})
            await asyncio.gather(self.notify(provider,50),self.notify(provider,50))
            self.assertEqual(await self.db.general_ledger.count_documents({}),before)
            self.assertEqual(await self.db.mz2_customer_refunds.count_documents({'original_key':key}),1)
            bal=await compute_balance(self.db,user_id='owner',entity_type='payment_gateway',entity_id=provider,sub_account='receivable')
            self.assertEqual(bal['net_balance'],200)
        self.assertEqual((await compute_balance(self.db,user_id='owner',entity_type='bank',entity_id='bank',sub_account='main'))['net_balance'],800)

    async def test_full_and_partial_provider_daily_approval_and_statement_order(self):
        for provider in ['salla','tamara','tabby','emkan']:
            key=await self.setup_sale(provider,'115')
            await self.notify(provider,cancel=True)
            case=await self.db.mz2_customer_refunds.find_one({'original_key':key})
            draft=dict(id='draft-'+provider,user_id='owner',provider=provider,status='draft',source_file_id='file-'+provider,amounts={})
            await self.db.accounting_settlements_v2.insert_one(draft)
            for i,value in enumerate(['23','23','69']):
                entry=dict(id='entry-'+provider+str(i),user_id='owner',file_id=draft['source_file_id'],order_number='SYN-MANUAL-TAX-'+provider,actual_partial_refund_amount=float(value))
                await self.db.settlement_entries.insert_one(entry)
                self.assertTrue(await refund_review_reasons(self.db,'owner',draft))
                # Distinct equal partial amounts use independent execution refs.
                body=dict(original_key=key,case_reference=case['case_reference'],amount=value,
                    paid_at='2020-01-04T12:00:00Z',bank_reference='SYN-exec-'+provider+str(i),execution_channel=provider)
                pay=await self.post('/bank-payments',body)
                result=await self.post('/bank-payments/'+pay['id']+'/approve')
                before=await self.db.general_ledger.count_documents({})
                await link_statement_refund(self.db,owner='owner',actor=ACTOR,draft_id=draft['id'],entry_id=entry['id'],refund_id=pay['id'])
                await self.notify(provider,cancel=True)
                self.assertEqual(await self.db.general_ledger.count_documents({}),before)
            self.assertFalse(await refund_review_reasons(self.db,'owner',draft))
            case=await self.db.mz2_customer_refunds.find_one({'id':case['id']})
            self.assertEqual(case['remaining'],'0.00')
            payments=await self.db.mz2_customer_refund_payments.find({'original_key':key}).to_list(10)
            self.assertEqual(sum(Decimal(p['tax']['net']) for p in payments),Decimal('100'))
            self.assertEqual(sum(Decimal(p['tax']['tax']) for p in payments),Decimal('15'))

    async def test_late_provider_conflict_and_automatic_path_denied(self):
        await self.bank();key=await self.setup_sale()
        row=await self.case(key,'SYN-double','50');payment=await self.movement(key,row['case_reference'],'50')
        await self.post('/bank-payments/'+payment['id']+'/approve')
        await self.db.payment_refunds.insert_one(dict(user_id='owner',provider='tamara',provider_payment_id='SYN-CAPTURE-tamara',provider_refund_id='SYN-late',amount='50',currency='SAR',status='completed',refunded_at='2020-01-04T12:00:00Z'))
        before=await self.db.general_ledger.count_documents({})
        result=await self.notify('tamara',50)
        self.assertEqual(result['state'],'needs_review')
        self.assertEqual((await self.db.mz2_customer_refunds.find_one({'id':row['id']}))['state'],'conflict')
        with self.assertRaises(EvidenceError):
            await prepare(self.db,owner='owner',provider='tamara',payment_id='SYN-CAPTURE-tamara',refund_id='SYN-late')
        self.assertEqual(await self.db.general_ledger.count_documents({}),before)

    async def test_viewer_owner_isolation_failure_and_recovery(self):
        await self.bank();key=await self.setup_sale();row=await self.case(key,'SYN-recovery','50')
        payment=await self.movement(key,row['case_reference'],'50')
        self.actor='viewer';before=await self.db.general_ledger.count_documents({})
        denial=await self.client.post(BASE+'/bank-payments/'+payment['id']+'/approve')
        self.assertEqual(denial.status_code,403)
        self.actor='other';denial=await self.client.post(BASE+'/bank-payments/'+payment['id']+'/approve');self.assertEqual(denial.status_code,404)
        self.actor='owner'
        import ledger_core
        real=ledger_core.post_ledger_entry
        count=0
        async def fail(*args,**kwargs):
            nonlocal count
            result=await real(*args,**kwargs);count+=1
            if count==2:raise RuntimeError('SYN interrupted journal')
            return result
        with patch('ledger_core.post_ledger_entry',fail):
            with self.assertRaises(RuntimeError):await self.post('/bank-payments/'+payment['id']+'/approve')
        self.assertEqual(await self.db.general_ledger.count_documents({}),before)
        self.assertNotEqual((await self.db.mz2_customer_refund_payments.find_one({'id':payment['id']}))['status'],'posted')
        await self.post('/bank-payments/'+payment['id']+'/approve')
        self.assertEqual(await self.db.general_ledger.count_documents({}),before+3)
