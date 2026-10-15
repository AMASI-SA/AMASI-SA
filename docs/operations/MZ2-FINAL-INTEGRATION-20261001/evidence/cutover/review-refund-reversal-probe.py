import asyncio, sys
from pathlib import Path
sys.path[:0] = ['backend','backend/tests']
from test_mz2_daily_refunds import DailyRefundTests
from accounting_atomic import atomic_owner
from accounting_ledger_v2 import reverse_journal_v2, compute_balance_v2
from accounting_refund_entitlements import period_journal
from decimal import Decimal

async def main():
    fixture=DailyRefundTests('test_historical_refund_does_not_become_another_pending_refund')
    await fixture.asyncSetUp()
    try:
        await fixture.bank()
        key=await fixture.setup_sale(gross='115')
        case=await fixture.case(key,'SYN-REVIEW-REVERSED-DUE','115')
        due=await fixture.confirm(case)
        async def reverse(db):
            return await reverse_journal_v2(db._db,user_id='owner',actor_id='owner',actor_name='Synthetic',
                original_txn_group_id=due['due_txn_group_id'],effective_at='2020-01-04T12:00:00Z',
                reason='Synthetic approved correction',mongo_session=db._session)
        reversal=await atomic_owner(fixture.db,'owner',reverse)
        report=await period_journal(fixture.db,owner='owner',from_at='2020-01-01T00:00:00Z',to_at='2020-01-05T00:00:00Z')
        bal=await compute_balance_v2(fixture.db,user_id='owner',entity_type='liability',entity_id=case['id'],sub_account='customer_refund_payable')
        liability=sum((Decimal(r['amount']) if r['side']=='debit' else -Decimal(r['amount'])) for r in report['items'] if r['entity_type']=='liability')
        print({'period_items':len(report['items']),'period_liability_net':str(liability),'native_liability_net':bal['net_balance'],
            'reversal_present':any(r['txn_group_id']==reversal['group']['txn_group_id'] for r in report['items'])})
    finally:
        await fixture.asyncTearDown()
asyncio.run(main())
