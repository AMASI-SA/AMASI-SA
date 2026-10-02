"""Real isolated Mongo: earned refund at month end, cash in next month."""
import asyncio
import unittest
from unittest.mock import patch
from decimal import Decimal
from fastapi import HTTPException
from accounting_write_control import set_write_state
from accounting_order_refunds import process_order_refunds
from mz2_native_fixture import native_balance as compute_balance
import test_mz2_daily_refunds as daily
BASE = daily.BASE


class EntitlementTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = daily.DailyRefundTests.asyncSetUp
    asyncTearDown = daily.DailyRefundTests.asyncTearDown
    configure = daily.DailyRefundTests.configure
    source = daily.DailyRefundTests.source
    payload = daily.DailyRefundTests.payload
    preview_and_post = daily.DailyRefundTests.preview_and_post
    post = daily.DailyRefundTests.post
    setup_sale = daily.DailyRefundTests.setup_sale
    bank = daily.DailyRefundTests.bank
    movement = daily.DailyRefundTests.movement
    case = daily.DailyRefundTests.case
    confirm = daily.DailyRefundTests.confirm
    notify = daily.DailyRefundTests.notify

    async def test_month_end_entitlement_next_month_payment_dates_and_no_repeat_tax(self):
        await self.bank()
        key = await self.setup_sale(gross='115')
        draft = await self.case(key,'MONTH-END','115')
        before = await self.db.accounting_general_ledger_v2.count_documents({})
        # Draft payment may precede confirmation in data arrival order, but
        # cannot be approved until the entitlement is independently confirmed.
        payment = await self.post('/bank-payments',dict(original_key=key,case_reference='MONTH-END',
            amount='115',paid_at='2020-02-02T10:00:00+03:00',bank_account_id='bank',
            bank_reference='SYN-FEB-BANK',execution_channel='bank'))
        denied = await self.client.post(BASE+'/bank-payments/'+payment['id']+'/approve')
        self.assertEqual(denied.status_code,409)
        self.assertEqual(await self.db.accounting_general_ledger_v2.count_documents({}),before)
        confirmed = await self.confirm(draft,'2020-01-31T18:00:00+03:00')
        self.assertEqual(confirmed['state'],'due')
        self.assertEqual(confirmed['remaining'],'115.00')
        self.assertEqual((await compute_balance(self.db,user_id='owner',entity_type='bank',entity_id='bank',sub_account='main'))['net_balance'],1000)
        # Changing the current policy must not change the original sale split.
        await self.configure('20',1)
        result = await self.post('/bank-payments/'+payment['id']+'/approve')
        self.assertNotEqual(result['txn_group_id'],confirmed['due_txn_group_id'])
        self.assertEqual((await self.post('/bank-payments/'+payment['id']+'/approve'))['txn_group_id'],result['txn_group_id'])
        async def period(start,end):
            r=await self.client.get(BASE+'/journal',params={'from_at':start,'to_at':end})
            self.assertEqual(r.status_code,200,r.text)
            return r.json()['items']
        jan=await period('2020-01-01T00:00:00+03:00','2020-02-01T00:00:00+03:00')
        feb=await period('2020-02-01T00:00:00+03:00','2020-03-01T00:00:00+03:00')
        self.assertEqual({(r['entity_type'],r['side']):Decimal(r['amount']) for r in jan},
            {('revenue','debit'):100,('tax','debit'):15,('liability','credit'):115})
        self.assertEqual({(r['entity_type'],r['side']):Decimal(r['amount']) for r in feb},
            {('liability','debit'):115,('bank','credit'):115})
        self.assertEqual({r['metadata']['accounting_at'] for r in jan},{'2020-01-31T15:00:00.000000+00:00'})
        self.assertEqual({r['metadata']['accounting_at'] for r in feb},{'2020-02-02T07:00:00.000000+00:00'})
        self.assertTrue(all(r['created_at']!=r['metadata']['accounting_at'] for r in jan+feb))
        self.assertEqual((await compute_balance(self.db,user_id='owner',entity_type='liability',entity_id=draft['id'],sub_account='customer_refund_payable'))['net_balance'],0)
        self.assertEqual((await compute_balance(self.db,user_id='owner',entity_type='payment_gateway',entity_id='tamara',sub_account='receivable'))['net_balance'],115)

    async def test_notification_and_cancellation_without_recognized_sale_do_not_accrue(self):
        result=await self.notify('tamara',cancel=True)
        self.assertEqual(result['state'],'needs_review')
        self.assertEqual(await self.db.accounting_general_ledger_v2.count_documents({}),0)
        self.assertEqual(await self.db.mz2_customer_refunds.count_documents({}),0)
        denied=await self.client.post(BASE,json=dict(original_key='0'*64,case_reference='CANCEL',amount='115',
            recognized_at='2020-01-03T12:00:00Z',reason='No recognized revenue'))
        self.assertEqual(denied.status_code,409)
        await self.setup_sale(gross='115')
        before=await self.db.accounting_general_ledger_v2.count_documents({})
        await self.notify('tamara',cancel=True)
        row=await self.db.mz2_customer_refunds.find_one({})
        self.assertFalse(row['recognized'])
        self.assertEqual(await self.db.accounting_general_ledger_v2.count_documents({}),before)

    async def test_authority_pause_tenant_and_payment_before_entitlement(self):
        # Configure the foreign owner so this case exercises tenant isolation.
        await self.db.mz2_atomic_owners.insert_one({
            "_id": "other", "revision": 0, "writes_paused": False, "control_revision": 0,
        })
        await self.bank(); key=await self.setup_sale(); row=await self.case(key,'AUTH','50')
        body=dict(amount='50',recognized_at='2020-01-05T12:00:00Z',reason='confirmed',evidence_ref='AUTH')
        for actor,status in [('viewer',403),('other',404)]:
            self.actor=actor
            self.assertEqual((await self.client.post(BASE+'/'+row['id']+'/recognize',json=body)).status_code,status)
        self.actor='owner'
        await set_write_state(self.db,owner='owner',actor_id='owner',paused=True,revision=0,reason='test')
        self.assertEqual((await self.client.post(BASE+'/'+row['id']+'/recognize',json=body)).status_code,423)
        await set_write_state(self.db,owner='owner',actor_id='owner',paused=False,revision=1,reason='test')
        await self.post('/'+row['id']+'/recognize',body)
        payment=await self.movement(key,row['case_reference'],'50')
        denied=await self.client.post(BASE+'/bank-payments/'+payment['id']+'/approve')
        self.assertEqual(denied.status_code,409)
        self.assertIn('payment_before_refund_entitlement',denied.text)

    async def test_concurrent_confirmation_duplicate_evidence_and_total_cap(self):
        key=await self.setup_sale(gross='115'); row=await self.case(key,'FIRST','69')
        results=await asyncio.gather(self.confirm(row,evidence='SAME'),self.confirm(row,evidence='SAME'))
        self.assertEqual(results[0]['due_txn_group_id'],results[1]['due_txn_group_id'])
        row2=await self.case(key,'SECOND','23')
        duplicate=await self.client.post(BASE+'/'+row2['id']+'/recognize',json=dict(amount='23',
            recognized_at='2020-01-03T12:00:00Z',reason='SYN confirmed customer right',evidence_ref='SAME'))
        self.assertEqual(duplicate.status_code,409)
        await self.confirm(row2,evidence='SECOND')
        row3=await self.case(key,'FINAL','23'); await self.confirm(row3,evidence='FINAL')
        self.assertEqual(await self.db.mz2_refund_entitlements.count_documents({}),3)
        denied=await self.client.post(BASE,json=dict(original_key=key,case_reference='EXCESS',amount='0.01',
            recognized_at='2020-01-03T12:00:00Z',reason='excess'))
        self.assertEqual(denied.status_code,409)
        cases=await self.db.mz2_customer_refunds.find({'recognized':True}).to_list(10)
        self.assertEqual(sum(Decimal(c['tax']['net']) for c in cases),Decimal('100'))
        self.assertEqual(sum(Decimal(c['tax']['tax']) for c in cases),Decimal('15'))

    async def test_failed_accrual_rolls_back_and_later_webhook_cannot_change_confirmed_amount(self):
        import accounting_recognition_native as native
        await self.setup_sale(); await self.notify('tamara',50)
        row=await self.db.mz2_customer_refunds.find_one({})
        before=await self.db.accounting_general_ledger_v2.count_documents({})
        original=native.post_journal_v2
        async def fail(*args,**kwargs):
            await original(*args,**kwargs)
            raise RuntimeError('synthetic crash after first entitlement leg')
        with patch.object(native,'post_journal_v2',side_effect=fail):
            with self.assertRaises(RuntimeError): await self.confirm(row)
        self.assertEqual(await self.db.accounting_general_ledger_v2.count_documents({}),before)
        self.assertEqual(await self.db.mz2_refund_entitlements.count_documents({}),0)
        self.assertFalse((await self.db.mz2_customer_refunds.find_one({'id':row['id']}))['recognized'])
        confirmed=await self.confirm(row)
        result=await self.notify('tamara',70)
        current=await self.db.mz2_customer_refunds.find_one({'id':row['id']})
        self.assertEqual(current['amount'],'50.00')
        self.assertEqual(current['tax'],confirmed['tax'])
        self.assertEqual(current['state'],'due')
        self.assertEqual(result['state'],'needs_review')
        self.assertEqual(await self.db.accounting_general_ledger_v2.count_documents({}),before+3)

    async def test_native_refund_reversal_keeps_period_history_and_excludes_other_reversals(self):
        from accounting_atomic import atomic_owner
        from accounting_ledger_v2 import reverse_journal_v2
        from accounting_refund_entitlements import period_journal
        from mz2_native_fixture import provision_native_opening

        await self.bank()
        sale_key = await self.setup_sale(gross="115")
        draft = await self.case(sale_key, "SYN-PERIOD-REVERSAL", "115")
        due = await self.confirm(draft, "2020-01-31T18:00:00+03:00")
        async def period(start, end, owner="owner"):
            result = await period_journal(self.db, owner=owner, from_at=start, to_at=end)
            self.assertEqual(result["scope"], "confirmed_refund_entitlements_and_payments_v2")
            return result["items"]
        jan_start, feb_start, march_start = (month+"T00:00:00+03:00" for month in
                                            ("2020-01-01", "2020-02-01", "2020-03-01"))
        original_january = await period(jan_start, feb_start)
        self.assertEqual(len(original_january), 3)

        async def reverse(group, at):
            async def write(scoped):
                return await reverse_journal_v2(scoped._db, user_id="owner", actor_id="owner", actor_name="Synthetic",
                    original_txn_group_id=group, effective_at=at, reason="Synthetic approved correction",
                    mongo_session=scoped._session)
            return await atomic_owner(self.db, "owner", write)
        reversal_at = "2020-02-02T07:00:00.000000Z"
        reversed_due = await reverse(due["due_txn_group_id"], reversal_at)
        # A genuine unrelated sale reversal in the same month must not appear.
        sale = await self.db.mz2_recognition_events.find_one({"_id": sale_key})
        unrelated = await reverse(sale["txn_group_id"], "2020-02-03T07:00:00Z")
        january = await period(jan_start, feb_start)
        february = await period(feb_start, march_start)
        whole = await period(jan_start, march_start)
        self.assertEqual(january, original_january)
        self.assertEqual(len(february), 3)
        self.assertEqual(len(whole), 6)
        self.assertEqual({row["txn_group_id"] for row in february}, {reversed_due["group"]["txn_group_id"]})
        self.assertNotIn(unrelated["group"]["txn_group_id"], {row["txn_group_id"] for row in whole})
        self.assertTrue(all(row["entry_type"] == "reversal" for row in february))
        self.assertEqual({row["effective_at"] for row in february}, {reversed_due["group"]["effective_at"]})
        self.assertEqual({row["metadata"]["reverses_entry_id"] for row in february}, {row["id"] for row in january})
        def liability(rows):
            return sum((Decimal(row["amount"]) if row["side"] == "debit" else -Decimal(row["amount"]))
                       for row in rows if row["entity_type"] == "liability")
        self.assertEqual(liability(january), Decimal("-115"))
        self.assertEqual(liability(february), Decimal("115"))
        self.assertEqual(liability(whole), Decimal("0"))
        self.assertEqual((await compute_balance(self.db, user_id="owner", entity_type="liability",
            entity_id=draft["id"], sub_account="customer_refund_payable"))["net_balance"], 0)
        # An independently opened foreign owner's report cannot select these legs.
        await provision_native_opening(self.db, owner="other", bank_balances={"other-bank": "25.00"})
        self.assertEqual(await period(jan_start, march_start, owner="other"), [])


if __name__=='__main__': unittest.main()
