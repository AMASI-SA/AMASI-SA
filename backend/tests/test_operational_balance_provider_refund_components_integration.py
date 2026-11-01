"""Real isolated Mongo boundary; included by the existing operational CI glob."""
from copy import deepcopy
from fastapi import HTTPException
import pytest
from operational_customer_returns import save_case
from operational_balance_service import refresh, report
from operational_balance_store import read
from test_operational_balance_customer_returns import fixture, command
from test_operational_balance_integration import run, NOW
from test_operational_balance_provider_components import policy


async def setup(db):
    await fixture(db)
    terms=policy(provider='tamara')  # synthetic contract, not a real provider default
    await db.mz2_provider_fee_policies_v2.update_one({'user_id':'owner'},{'$set':{'policies':[terms]}})


def test_component_policy_refunds_use_native_atomic_case_and_preserve_bank_opening():
    async def scenario(db):
        await setup(db)
        p=command(status='refunded',amount='100',refund_source_type='provider',refund_source_id='tamara')
        first=await save_case(db,'owner','staff',p,clock=NOW)
        assert (await save_case(db,'owner','staff',p,clock=NOW))['id']==first['id']
        state=await read(db,'owner')
        r=state['engine']['provider_reports']['provider:order1']
        assert r['estimated_fees']=='19.55' and r['refunded']=='100.00'
        assert r['refunded_fee_net']=='5.00' and r['refunded_fee_tax']=='0.75'
        assert len(state['customer_returns'])==1 and state['movements']==[]
        assert report(state)['summary']['actual_liquidity']=='1000.00'
        before=deepcopy(state['openings'])
        await db.unified_orders.update_one({'user_id':'owner'},{'$set':{'raw_by_source.salla_direct.status':'cancelled'}})
        second=dict(p,request_id='refund-rest',refund_reference='refund-rest',amount='200',
                    items=[{'id':'a','quantity':1},{'id':'b','quantity':1}])
        await save_case(db,'owner','staff',second,clock=NOW)
        await refresh(db,'owner',clock=NOW);await refresh(db,'owner',clock=NOW)
        state=await read(db,'owner');r=state['engine']['provider_reports']['provider:order1']
        assert r['cancelled']=='0.00' and r['refunded']=='300.00'
        assert r['estimated_fees']=='8.05' and r['expected_receivable']=='-8.05'
        assert state['openings']==before and state['movements']==[]
        assert len(state['customer_returns'])==2
    run(scenario)


def test_missing_component_evidence_does_not_partially_save_refund():
    async def scenario(db):
        await setup(db)
        await db.mz2_provider_fee_policies_v2.update_one({'user_id':'owner'},{'$unset':{'policies.0.evidence':''}})
        p=command(status='refunded',amount='100',refund_source_type='provider',refund_source_id='tamara')
        with pytest.raises(HTTPException) as rejected:
            await save_case(db,'owner','staff',p,clock=NOW)
        assert rejected.value.detail['code']=='customer_return_fee_policy_incomplete'
        state=await read(db,'owner')
        assert state.get('customer_returns',[])==[] and state['movements']==[]
        assert report(state)['summary']['actual_liquidity']=='1000.00'
    run(scenario)
