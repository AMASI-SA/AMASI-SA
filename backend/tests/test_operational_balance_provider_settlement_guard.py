"""Protect settled evidence until actual fee-credit reconciliation is available."""
from copy import deepcopy
from operational_balance_engine import reconcile
from test_operational_balance_provider_components import payment, policy, refund, NOW


def test_component_refund_after_settlement_is_blocked_without_actual_fee_credit_allocation():
    sources = {'orders':[{'id':'settled','currency':'SAR','status':'delivered',
        'created_at':'2026-10-06T09:00:00+00:00','updated_at':NOW,'items':[], 'payment':payment()}],
        'fee_policies':[policy()]}
    state={'status':'active','started_at':'2026-10-05T09:00:00+00:00','openings':{},'movements':[]}
    settled=reconcile(state,sources,NOW)
    settled['movements']=[{'id':'payout1','amount':'918.35','actual_fee_amount':'81.65',
        'direction':'incoming','allocations':[{'obligation_id':'provider:settled','amount':'1000.00'}]}]
    before=deepcopy(settled)
    sources['orders'][0]['payment']['refunds']=[refund('1000')]
    after=reconcile(settled,sources,NOW)
    obligation=after['engine']['obligations']['provider:settled']
    assert obligation['direction']=='receivable' and obligation['confirmed']=='1000.00'
    assert obligation['incomplete'] is True
    assert any(i['code']=='provider_postsettlement_refund_review_required' for i in after['engine']['issues'])
    assert 'refund:test-provider:refund-1' not in after['engine']['facts']
    assert 'credit:provider:settled' not in after['engine']['obligations']
    assert after['movements']==before['movements'] and after['openings']==before['openings']
    assert settled==before and reconcile(after,sources,NOW)==after


def test_native_refund_after_settlement_rolls_back_in_isolated_mongo():
    import pytest
    from fastapi import HTTPException
    from operational_customer_returns import save_case
    from operational_balance_service import refresh, report, create_movement
    from operational_balance_store import read
    from test_operational_balance_customer_returns import command
    from test_operational_balance_provider_refund_components_integration import setup
    from test_operational_balance_integration import run, movement

    async def scenario(db):
        await setup(db)
        await refresh(db, 'owner', clock=NOW)
        payout = movement()
        payout.update(request_id='settlement-before-refund', party_type='provider', party_id='tamara',
            amount='274.70', actual_fee_amount='25.30', direction='incoming', kind='settlement',
            reference='synthetic-statement', allocations=[{'obligation_id':'provider:order1','amount':'300.00'}])
        await create_movement(db, 'owner', 'owner', payout, clock=NOW)
        before = await read(db, 'owner')
        request = command(status='refunded', amount='100', refund_source_type='provider', refund_source_id='tamara')
        with pytest.raises(HTTPException) as rejected:
            await save_case(db, 'owner', 'staff', request, clock=NOW)
        assert rejected.value.detail['code'] == 'customer_return_provider_incomplete'
        after = await read(db, 'owner')
        assert after.get('customer_returns', []) == []
        assert after['movements'] == before['movements']
        assert after['openings'] == before['openings']
        assert after['engine']['facts'] == before['engine']['facts']
        assert report(after)['summary']['actual_liquidity'] == '1274.70'
    run(scenario)
