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
            self.assertTrue(all(r['state']=='reconciled' for r in results), results)
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
        self.assertEqual(len(groups), 3)
        self.assertEqual(len({x['txn_group_id'] for x in groups}), 3)
        self.assertTrue(all(x['proposal']['tax']['rate']=='15' for x in groups))
        print('REFUND_PROOF', provider, statement_first, self.db.name, [x['txn_group_id'] for x in groups])

    async def test_order_first_all_actual_payment_providers(self):
        for provider in ['salla','tamara','tabby','emkan']:
            await self.scenario(provider, False)

    async def test_statement_first_all_actual_payment_providers(self):
        for provider in ['salla','tamara','tabby','emkan']:
            await self.scenario(provider, True)

    async def test_missing_identity_is_review_without_financial_write(self):
        await self.preview_and_post()
        await self.db.payment_refunds.insert_one(dict(id='local-only',user_id='owner',provider='tamara',
            provider_payment_id='SYN-CAPTURE-tamara',amount='23'))
        before = await self.db.general_ledger.count_documents({})
        result = await process_order_refunds(self.db,owner='owner',order_number='SYN-MANUAL-TAX-tamara',source={'kind':'SYN'})
        self.assertEqual(result['state'],'needs_review')
        self.assertEqual(result['items'][0]['reason'],'refund_identity_required')
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
