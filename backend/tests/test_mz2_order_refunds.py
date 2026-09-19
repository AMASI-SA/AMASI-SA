"""Real Mongo refund identities across order notifications and statements."""
import asyncio
import unittest
from decimal import Decimal
from fastapi import HTTPException
import test_mz2_receivable_workflow as fixtures
from accounting_order_refunds import process_order_refunds, link_statement_refund, refund_review_reasons


class OrderRefundTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WorkflowTests.asyncSetUp
    asyncTearDown = fixtures.WorkflowTests.asyncTearDown
    configure = fixtures.WorkflowTests.configure
    source = fixtures.WorkflowTests.source
    payload = fixtures.WorkflowTests.payload
    preview_and_post = fixtures.WorkflowTests.preview_and_post

    async def daily_post(self, provider, rid):
        original=await self.db.mz2_recognition_events.find_one({'proposal.event.provider':provider,'proposal.event.kind':'sale'})
        case=await self.db.mz2_customer_refunds.find_one({'original_key':original['_id']})
        root='/accounting-module/customer-refunds'
        if case.get('recognized') and Decimal(case['remaining']) < Decimal('23'):
            answer=await self.client.post(root,json=dict(original_key=original['_id'],case_reference=rid,amount='23',
                recognized_at='2020-01-03T12:00:00Z',reason='SYN additional confirmed return'))
            self.assertEqual(answer.status_code,200,answer.text)
            case=answer.json()
        if not case.get('recognized'):
            confirmed=await self.client.post(root+'/'+case['id']+'/recognize',json=dict(amount=case['amount'],
                recognized_at='2020-01-03T12:00:00Z',reason='SYN confirmed return',evidence_ref='CREDIT-'+rid))
            self.assertEqual(confirmed.status_code,200,confirmed.text)
        payment=await self.client.post(root+'/bank-payments',json=dict(original_key=original['_id'],case_reference=case['case_reference'],amount='23',paid_at='2020-01-03T12:00:00Z',execution_channel=provider,bank_reference=rid,provider_refund_id=rid))
        self.assertEqual(payment.status_code,200,payment.text)
        result=await self.client.post(root+'/bank-payments/'+payment.json()['id']+'/approve')
        self.assertEqual(result.status_code,200,result.text)
        return result.json()

    async def scenario(self, provider, statement_first):
        if provider != 'tamara':
            await self.source(provider)
        await self.preview_and_post(provider)
        number = 'SYN-MANUAL-TAX-' + provider
        draft = dict(id='draft-'+provider, user_id='owner', provider=provider,
            status='draft', source_file_id='file-'+provider, amounts={})
        await self.db.accounting_settlements_v2.insert_one(draft)
        for i in [1, 2]:
            rid = 'SYN-REFUND-'+provider+'-'+str(i)
            entry = dict(id='row-'+rid, user_id='owner', file_id=draft['source_file_id'],
                provider=provider, event_type='refund', order_number=number,
                actual_partial_refund_amount=23)
            if statement_first:
                await self.db.settlement_entries.insert_one(entry)
                self.assertTrue(await refund_review_reasons(self.db, 'owner', draft))
            await self.db.payment_refunds.insert_one(dict(id=rid, user_id='owner', provider=provider,
                provider_refund_id=rid, provider_payment_id='SYN-CAPTURE-'+provider,
                currency='SAR', amount='23.00', status='completed',
                refunded_at='2020-01-03T12:00:00Z', source='synthetic_provider_refund'))
            results = await asyncio.gather(*[process_order_refunds(self.db, owner='owner',
                order_number=number, source={'kind':source}) for source in ['salla_order_update','replay','sync']])
            self.assertTrue(all(r['financial_write'] is False for r in results), results)
            await self.daily_post(provider,rid)
            if not statement_first:
                await self.db.settlement_entries.insert_one(entry)
            before = await self.db.general_ledger.count_documents({})
            linked = await link_statement_refund(self.db, owner='owner', actor={'id':'owner'},
                draft_id=draft['id'], entry_id=entry['id'], refund_id=rid)
            self.assertFalse(linked['financial_write'])
            again = await link_statement_refund(self.db, owner='owner', actor={'id':'owner'},
                draft_id=draft['id'], entry_id=entry['id'], refund_id=rid)
            self.assertEqual(again['txn_group_id'], linked['txn_group_id'])
            self.assertEqual(await self.db.general_ledger.count_documents({}), before)
        self.assertFalse(await refund_review_reasons(self.db, 'owner', draft))
        rows = await self.db.general_ledger.find({'entity_type':'payment_gateway','entity_id':provider}).to_list(20)
        balance = sum(Decimal(str(x['amount']))*(1 if x['side']=='debit' else -1) for x in rows)
        self.assertEqual(balance, Decimal('69'))
        groups = await self.db.mz2_recognition_events.find({'proposal.event.provider':provider}).to_list(10)
        groups += await self.db.mz2_customer_refund_payments.find({'execution_channel':provider,'status':'posted'}).to_list(10)
        self.assertEqual(len(groups), 3)
        self.assertEqual(len({x['txn_group_id'] for x in groups}), 3)
        cases=await self.db.mz2_customer_refunds.find({'original_provider':provider,'recognized':True}).to_list(10)
        self.assertTrue(all(x['tax']['rate']=='15' for x in cases))
        self.assertTrue(all('tax' not in x for x in groups if x.get('execution_channel')))
        print('REFUND_PROOF', provider, statement_first, self.db.name, [x['txn_group_id'] for x in groups])

    async def test_order_first_all_actual_payment_providers(self):
        for provider in ['salla','tamara','tabby','emkan']:
            await self.scenario(provider, False)

    async def test_statement_first_all_actual_payment_providers(self):
        for provider in ['salla','tamara','tabby','emkan']:
            await self.scenario(provider, True)

    async def test_emkan_aggregate_row_links_two_distinct_partial_refunds(self):
        await self.source('emkan');await self.preview_and_post('emkan')
        for rid in ['SYN-AGG-1','SYN-AGG-2']:
            await self.db.payment_refunds.insert_one(dict(id=rid,user_id='owner',provider='emkan',
                provider_refund_id=rid,provider_payment_id='SYN-CAPTURE-emkan',currency='SAR',
                amount='23',status='completed',refunded_at='2020-01-03T12:00:00Z',source='synthetic'))
        await process_order_refunds(self.db,owner='owner',order_number='SYN-MANUAL-TAX-emkan',source={'kind':'order_update'})
        for rid in ['SYN-AGG-1','SYN-AGG-2']:
            await self.daily_post('emkan',rid)
        draft=dict(id='aggregate',user_id='owner',provider='emkan',status='draft',source_file_id='aggregate-file',amounts={})
        await self.db.accounting_settlements_v2.insert_one(draft)
        await self.db.settlement_entries.insert_one(dict(id='aggregate-row',user_id='owner',file_id='aggregate-file',
            event_type='sale_with_partial_refund',order_number='SYN-MANUAL-TAX-emkan',actual_partial_refund_amount=46))
        self.assertTrue(await refund_review_reasons(self.db,'owner',draft))
        with self.assertRaises(HTTPException):
            await link_statement_refund(self.db,owner='owner',actor={'id':'owner'},draft_id='aggregate',entry_id='aggregate-row',refund_id='SYN-AGG-1')
        self.assertEqual(await self.db.mz2_statement_refund_links.count_documents({}),0)
        await link_statement_refund(self.db,owner='owner',actor={'id':'owner'},draft_id='aggregate',entry_id='aggregate-row',refund_ids=['SYN-AGG-1','SYN-AGG-2'])
        self.assertFalse(await refund_review_reasons(self.db,'owner',draft))
        self.assertEqual(await self.db.general_ledger.count_documents({}),10)

    async def test_missing_identity_is_review_without_financial_write(self):
        await self.preview_and_post()
        await self.db.payment_refunds.insert_one(dict(id='local-only',user_id='owner',provider='tamara',
            provider_payment_id='SYN-CAPTURE-tamara',amount='23'))
        before = await self.db.general_ledger.count_documents({})
        result = await process_order_refunds(self.db,owner='owner',order_number='SYN-MANUAL-TAX-tamara',source={'kind':'SYN'})
        self.assertEqual(result['state'],'needs_review')
        self.assertIn(result['items'][0]['reason'],['refund_amount_or_identity_required','awaiting_daily_refund_recording'])
        self.assertEqual(await self.db.general_ledger.count_documents({}),before)

    async def test_conflicting_statement_and_cross_owner_do_not_write(self):
        await self.scenario('tamara',False)
        draft = dict(id='other-draft',user_id='owner',provider='tamara',status='draft',source_file_id='other-file')
        await self.db.accounting_settlements_v2.insert_one(draft)
        await self.db.settlement_entries.insert_one(dict(id='other-row',user_id='owner',file_id='other-file',
            provider='tamara',event_type='refund',order_number='SYN-MANUAL-TAX-tamara',actual_partial_refund_amount=24))
        before = await self.db.general_ledger.count_documents({})
        for owner in ['owner','other']:
            with self.assertRaises(HTTPException):
                await link_statement_refund(self.db,owner=owner,actor={'id':owner},draft_id='other-draft',
                    entry_id='other-row',refund_id='SYN-REFUND-tamara-1')
        self.assertEqual(await self.db.general_ledger.count_documents({}),before)


if __name__ == '__main__':
    unittest.main()
