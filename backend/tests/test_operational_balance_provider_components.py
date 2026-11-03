"""Synthetic provider contracts; no customer records or merchant rate defaults."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import pytest
from operational_balance_engine import provider_projection, reconcile

DAY = date(2026, 10, 6)
NOW = '2026-10-08T12:00:00+00:00'


def policy(**changes):
    common = dict(fixed_amount='0', cancellation_fee_treatment='recalculate',
                  vat_treatment='exclusive', vat_rate='15')
    result = dict(id='synthetic-policy', provider='test-provider', currency='SAR',
        status='active', effective_from='2026-01-01', fee_model_version=2,
        confirmed_by='test-owner', confirmed_at='2026-01-01T00:00:00+00:00',
        evidence='synthetic contract fixture', components=[
            dict(common, id='refundable', percentage='5', refund_fee_treatment='recalculate'),
            dict(common, id='retained', percentage='2', refund_fee_treatment='retain'),
            dict(common, id='fixed', percentage='0', fixed_amount='1', refund_fee_treatment='retain')])
    return dict(result, **changes)


def payment(**changes):
    return dict(provider='test-provider', currency='SAR', gross='1000', cancelled='0',
                captures=[{'id': 'capture1', 'amount':'1000', 'captured_at':NOW}], refunds=[], **changes)


def base_payment(**changes):
    p = payment()
    p.update(changes)
    return p


def refund(amount='200', identity='refund-1', status='executed'):
    return {'id':identity, 'amount':amount, 'status':status}


def project(p=None, terms=None):
    return provider_projection(p or payment(), terms or [policy()], DAY)


def test_component_projection_keeps_fee_and_tax_separate():
    result = project()
    assert result['estimated_fee_net'] == '71.00'
    assert result['estimated_fee_tax'] == '10.65'
    assert result['estimated_fees'] == '81.65'
    assert result['expected_receivable'] == '918.35'
    assert result['fee_model_version'] == 2
    assert result['fee_recognition'] == 'estimated'
    assert len(result['fee_components']) == 3


def test_partial_refund_only_returns_refundable_component_and_its_tax():
    result = project(base_payment(refunds=[refund()]))
    assert result['refunded'] == '200.00'
    assert result['refunded_fee_net'] == '10.00'
    assert result['refunded_fee_tax'] == '1.50'
    assert result['estimated_fees'] == '70.15'
    assert result['expected_receivable'] == '729.85'


def test_full_refund_retains_nonrefundable_and_fixed_fee_as_negative_net():
    result = project(base_payment(refunds=[refund('1000')]))
    assert result['net'] == '0.00'
    assert result['estimated_fees'] == '24.15'
    assert result['expected_receivable'] == '-24.15'
    assert result['refunded_fee_net'] == '50.00'
    assert result['refunded_fee_tax'] == '7.50'


def test_retention_contract_keeps_original_charge_after_full_refund():
    terms = policy()
    for c in terms['components']:
        c['refund_fee_treatment'] = 'retain'
    result = project(base_payment(refunds=[refund('1000')]), [terms])
    assert result['estimated_fees'] == '81.65'
    assert result['expected_receivable'] == '-81.65'


def test_duplicate_refund_and_multiple_partial_refunds_are_replay_safe():
    p = base_payment(refunds=[refund('300','r1'),refund('200','r2'),refund('300','r1','settled')])
    before = deepcopy(p)
    r = project(p)
    assert r['refunded'] == '500.00'
    assert r['estimated_fees'] == '52.90'
    assert r == project(base_payment(refunds=[refund('500')]))
    assert p == before


@pytest.mark.parametrize('bad', [None, '', True, '-1', 'NaN', 'Infinity', '0.001'])
def test_invalid_executed_refund_amount_is_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        project(base_payment(refunds=[refund(bad)]))


def test_conflicting_reference_is_not_silently_deduplicated():
    with pytest.raises(ValueError, match='refund_identity_conflict'):
        project(base_payment(refunds=[refund('100'),refund('200')]))


def test_unexecuted_refund_does_not_change_the_estimate():
    assert project(base_payment(refunds=[refund('200',status='requested')])) == project()


def test_v2_missing_refund_identity_is_rejected():
    with pytest.raises(ValueError, match='refund_identity'):
        project(base_payment(refunds=[refund('200','')]))


def test_v2_cancellation_requires_provider_void_evidence_not_only_salla_status():
    with pytest.raises(ValueError, match='cancellation_execution_evidence'):
        project(base_payment(cancelled='1000', captures=[]))


def test_proven_precapture_void_is_not_a_postcapture_refund():
    p = base_payment(cancelled='1000', captures=[], cancellations=[{
        'id':'void1','amount':'1000','status':'executed','stage':'before_capture'}])
    result = project(p)
    assert result['cancelled'] == '1000.00'
    assert result['estimated_fees'] == '0.00'
    assert result['expected_receivable'] == '0.00'


def test_void_and_capture_cannot_claim_the_same_amount():
    p = base_payment(cancelled='1000', cancellations=[{
        'id':'void1','amount':'1000','status':'executed','stage':'before_capture'}])
    with pytest.raises(ValueError, match='capture_cancellation_overlap'):
        project(p)


def test_plan_must_be_known_when_rates_differ_by_plan():
    policies = [policy(id='short',plan_id='short'), policy(id='long',plan_id='long')]
    with pytest.raises(ValueError, match='plan_required'):
        project(payment(), policies)
    assert project(base_payment(plan_id='short'), policies)['policy_id'] == 'short'


def test_wrong_plan_is_not_silently_replaced():
    with pytest.raises(ValueError, match='policy_incomplete'):
        project(base_payment(plan_id='unconfigured'), [policy(plan_id='short')])


def test_policy_components_need_explicit_approval_and_unique_identity():
    with pytest.raises(ValueError, match='policy_evidence'):
        project(terms=[policy(evidence='')])
    terms = policy(); terms['components'].append(terms['components'][0])
    with pytest.raises(ValueError, match='component_identity'):
        project(terms=[terms])


def test_inclusive_tax_is_not_added_twice():
    terms = policy()
    terms['components'] = [dict(id='inclusive', percentage='11.5', fixed_amount='0',
        refund_fee_treatment='recalculate',cancellation_fee_treatment='recalculate',
        vat_treatment='inclusive',vat_rate='15')]
    r = project(terms=[terms])
    assert (r['estimated_fee_net'],r['estimated_fee_tax'],r['estimated_fees']) == ('100.00','15.00','115.00')


def test_full_refund_returns_last_cent_exactly():
    p = base_payment(gross='0.10',captures=[],refunds=[refund('0.03','a'),refund('0.07','b')])
    terms = policy(); terms['components'] = [dict(terms['components'][0],percentage='5')]
    result = project(p,[terms])
    assert result['estimated_fees'] == '0.00'
    assert result['refunded_fee_net'] == '0.01'


def test_engine_keeps_confirmed_evidence_and_replay_does_not_create_cash():
    p = base_payment(refunds=[refund('1000')])
    sources = {'orders':[{'id':'order-test','currency':'SAR','status':'cancelled',
        'created_at':'2026-10-06T09:00:00+00:00','updated_at':NOW,'items':[], 'payment':p}],
        'fee_policies':[policy()]}
    state={'status':'active','started_at':'2026-10-05T09:00:00+00:00',
           'openings':{'untouched':{'amount':'500'}},'movements':[]}
    before=deepcopy(state)
    first=reconcile(state,sources,NOW)
    assert first['engine']['provider_reports']['provider:order-test']['estimated_fees']=='24.15'
    obligation=first['engine']['obligations']['provider:order-test']
    assert obligation['direction']=='payable' and obligation['expected']=='24.15'
    assert first['movements']==[] and first['openings']==before['openings']
    assert reconcile(first,sources,NOW)==first and state==before


def test_cancelled_salla_order_with_executed_refund_does_not_subtract_twice():
    from operational_customer_returns import attach_returns
    p = base_payment(cancelled='1000')
    snapshot = base_payment()
    state = {'customer_returns':[{'order_id':'o1','status':'refunded',
        'refund_source_type':'provider','payment_snapshot':snapshot,
        'refund_reference':'real-refund','amount':'1000'}]}
    sources = {'orders':[{'id':'o1','status':'cancelled','payment':p}]}
    attach_returns(state,sources)
    normalized = sources['orders'][0]['payment']
    assert normalized['cancelled']=='0'
    result = project(normalized)
    assert result['refunded']=='1000.00' and result['estimated_fees']=='24.15'
    assert p['cancelled']=='1000'  # Original payment object not silently edited.
    once = deepcopy(sources)
    assert attach_returns(state,sources)==once


def test_refunded_source_without_payment_restores_capture_proof_from_saved_snapshot():
    from operational_customer_returns import attach_returns
    state={'customer_returns':[{'order_id':'o1','status':'refunded','refund_source_type':'provider',
        'payment_snapshot':base_payment(),'refund_reference':'r','amount':'200'}]}
    sources={'orders':[{'id':'o1','status':'refunded'}]}
    attach_returns(state,sources)
    assert sources['orders'][0]['payment']['captures'][0]['id']=='capture1'
    assert project(sources['orders'][0]['payment'])['estimated_fees']=='70.15'


def test_native_refund_attachment_rejects_conflicting_snapshot_or_reference():
    from operational_provider_fees import with_recorded_refund
    with pytest.raises(ValueError, match='snapshot_conflict'):
        with_recorded_refund(payment(),base_payment(provider='different'),'r','200')
    with pytest.raises(ValueError, match='refund_identity_conflict'):
        with_recorded_refund(base_payment(refunds=[refund('100')]),payment(),'refund-1','200')


def test_refund_attachment_preserves_explicit_provider_void_events():
    from operational_provider_fees import with_recorded_refund
    p=base_payment(cancelled='200',captures=[{'id':'c','amount':'800','captured_at':NOW}],
        cancellations=[{'id':'v','amount':'200','status':'executed','stage':'before_capture'}])
    snap=deepcopy(p)
    r=with_recorded_refund(p,snap,'r','300')
    assert r['cancelled']=='200' and project(r)['refunded']=='300.00'
