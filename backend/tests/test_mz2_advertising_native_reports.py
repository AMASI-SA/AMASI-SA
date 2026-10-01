"""Native advertising producers feed classified reports without fallback identities."""
from decimal import Decimal
import pytest

from test_mz2_bank_evidence_adapters import db, imported, payload
from test_mz2_advertising_v2 import OWNER, seed, approved
from accounting_advertising_contract import Expense, SpendPost, EXPENSES
from accounting_advertising_setup import setup
from accounting_advertising_bridge import bank_movement, post_spend
from accounting_mz2_reports import mz2_trial_balance, mz2_financial_position


async def posted_activity(db):
    await seed(db,amount=30)
    fact = await approved(db,mode='hybrid')
    await setup(db,OWNER,Expense(purpose='bank_fee',entity_id='owner-confirmed-bank-fee',evidence='Owner approved native fee identity'))
    principal = await imported(db,OWNER,'bank-proof',value='100.00')
    fee = await imported(db,OWNER,'bank-proof',value='5.00',reference='REPORT-FEE')
    funded = await bank_movement(db,OWNER,payload(principal,bank_fee_sar='5.00',bank_fee_evidence=fee['id'],
        effective_at='2026-01-02T00:00:00+03:00'))
    spent = await post_spend(db,OWNER,SpendPost(snapshot_id=fact['id'],wallet_sar_amount='30.00'))
    return funded,spent


@pytest.mark.asyncio
async def test_real_funding_fee_and_spend_are_classified_in_native_reports(db):
    funded,spent = await posted_activity(db)
    trial = await mz2_trial_balance(db,owner=OWNER)
    assert trial['status'] == 'available', trial.get('readiness_blockers', trial)
    assert trial['ledger_backend'] == 'v2' and trial['legacy_financial_data_included'] is False
    totals = {(row['entity_type'],row['entity_id'],row['sub_account']):Decimal(str(row['net'])) for row in trial['items']}
    assert totals[('bank','bank-proof','main')] == 895
    assert totals[('ad_account','meta-wallet','balance')] == 70
    assert totals[('expense','approved-ad-expense','')] == 30
    assert totals[('expense','owner-confirmed-bank-fee','')] == 5
    assert sum(totals.values()) == 0
    assert {key[1] for key in totals if key[0]=='expense'} == {'approved-ad-expense','owner-confirmed-bank-fee'}
    position = await mz2_financial_position(db,owner=OWNER)
    assert position['status'] == 'available', position
    assert Decimal(str(position['totals']['total_assets'])) == 965
    assert Decimal(str(position['totals']['total_liabilities'])) == 0
    assert funded['txn_group_id'] != spent['txn_group_id']


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',['missing','foreign_owner','unconfirmed','wrong_purpose'])
async def test_reports_do_not_invent_missing_or_foreign_expense_identity(db,mutation):
    await posted_activity(db)
    query = {'user_id':OWNER,'entity_id':'owner-confirmed-bank-fee'}
    if mutation == 'missing': await db[EXPENSES].delete_one(query)
    else:
        changes = {'foreign_owner':{'user_id':'another-owner'},'unconfirmed':{'confirmed_by':None},
            'wrong_purpose':{'purpose':'unrelated'}}[mutation]
        await db[EXPENSES].update_one(query,{'$set':changes})
    trial = await mz2_trial_balance(db,owner=OWNER)
    assert trial['status'] != 'available' and trial['items'] == []
    assert any(row.get('entity_id') == 'owner-confirmed-bank-fee' for row in trial.get('readiness_blockers',[])),trial
    position = await mz2_financial_position(db,owner=OWNER)
    assert position['status'] != 'available' and position['totals'] is None
