"""Dependency banners must agree with computed scope, including late conflicts."""
from copy import deepcopy
import pytest

import operational_financial_position as wrapper
from operational_financial_position_core import build_financial_position
from test_operational_balance_financial_position import state_for, opening


def dependency(result, code):
    return next(row for row in result['dependencies'] if row['code'] == code)


def evidence(state, currencies=('SAR',)):
    base = build_financial_position(state)
    started, stamp = base['started_at'], base['as_of']
    return {'status':'preview', 'started_at':started, 'as_of':stamp, 'engine_as_of':stamp,
        'complete':True, 'currencies':{c:{} for c in currencies}, 'groups':[], 'issues':[],
        'cod_forecast_coverage': {'version':1,'scope':'all_open_canonical_orders', 'started_at':started,'as_of':stamp,
            'by_currency':{c:{'complete':True,'amount':'0.00','positive_orders':0,'candidate_orders':0,'issues':[]} for c in currencies}},
        'profit_period': {'version':1,'scope':'operating_period_accrual','started_at':started,'as_of':stamp,
            'by_currency': {c:{key:{'complete':True,'amount':'100.00' if key=='net_sales' else '10.00',
                'source_count':1, 'basis':'synthetic_matched_period_evidence'} for key in
                ('net_sales','cost_of_goods','shipping','payment_fees','advertising','salaries','operating_expenses','financing','tax')}
                for c in currencies}}}


def state():
    value=state_for()
    opening(value,'bank','bank','1000.00')
    return value


def test_complete_banners_follow_complete_calculations_not_constant_placeholders():
    value=state(); proof=evidence(value); before=deepcopy((value,proof))
    result=build_financial_position(value,cost_exposure=proof)
    assert dependency(result,'complete_collection_forecast')['status']=='ready'
    assert dependency(result,'profit_period_coverage')['status']=='ready'
    assert result['currencies']['SAR']['metrics']['net_profit']=='20.00'
    assert result['currencies']['SAR']['metrics']['forecast_monetary_position']=='1000.00'
    assert before==(value,proof)


def test_missing_sources_are_unavailable_not_complete_or_fabricated_zero():
    result=build_financial_position(state())
    assert dependency(result,'complete_collection_forecast')['status']=='unavailable'
    assert dependency(result,'profit_period_coverage')['status']=='unavailable'
    assert result['currencies']['SAR']['metrics']['net_profit'] is None


def test_partial_proven_components_report_incomplete_with_no_net_profit():
    value=state(); proof=evidence(value)
    proof['profit_period']['by_currency']['SAR']['tax']={'complete':False,'amount':None,'issues':[{'code':'tax_evidence_missing'}]}
    proof['cod_forecast_coverage']['by_currency']['SAR'].update(complete=False,amount=None)
    result=build_financial_position(value,cost_exposure=proof)
    assert dependency(result,'complete_collection_forecast')['status']=='incomplete'
    assert dependency(result,'profit_period_coverage')['status']=='incomplete'
    assert result['currencies']['SAR']['metrics']['operating_profit']=='40.00'
    assert result['currencies']['SAR']['metrics']['net_profit'] is None


def test_one_currency_cannot_certify_an_uncovered_currency():
    value=state(); opening(value,'qar-bank','bank','200.00',currency='QAR')
    result=build_financial_position(value,cost_exposure=evidence(value))
    for code in ('complete_collection_forecast','profit_period_coverage'):
        row=dependency(result,code)
        assert row['status']=='incomplete'
        assert row['currency_status']=={'SAR':'ready','QAR':'unavailable'}


def test_scope_currency_present_only_in_period_proof_is_not_silently_dropped():
    value=state(); proof=evidence(value,('SAR','QAR'))
    proof['profit_period']['by_currency']['QAR']['tax']={'complete':False,'amount':None}
    result=build_financial_position(value,cost_exposure=proof)
    assert 'QAR' in result['currencies']
    assert dependency(result,'profit_period_coverage')['status']=='incomplete'


def test_late_review_conflict_invalidates_forecast_position_but_not_independent_change(monkeypatch):
    value=state(); proof=evidence(value)
    before=build_financial_position(value,cost_exposure=proof)
    assert before['currencies']['SAR']['completeness']['forecast']
    monkeypatch.setattr(wrapper,'project_balances',lambda *args,**kwargs:deepcopy(before))
    monkeypatch.setattr(wrapper,'link_review_destinations',lambda state,delivery:(delivery,[
        {'code':'operational_driver_review_effect_conflict','source_id':'review-one'}]))
    result=wrapper.build_financial_position(value,cost_exposure=proof)
    data=result['currencies']['SAR']
    assert not data['completeness']['actual'] and not data['completeness']['forecast']
    assert data['metrics']['monetary_position'] is None
    assert data['metrics']['forecast_monetary_position'] is None
    assert next(row for row in data['forecast_rows'] if row['id']=='forecast_monetary_position')['amount'] is None
    assert data['metrics']['forecast_change']=='0.00'
    assert dependency(result,'profit_period_coverage')['status']=='ready'


def test_delivery_snapshot_pending_alone_does_not_discard_verified_position(monkeypatch):
    value=state(); proof=evidence(value)
    monkeypatch.setattr(wrapper,'link_review_destinations',lambda state,delivery:(delivery,[
        {'code':'operational_driver_review_awaiting_delivery_snapshot','source_id':'review-one'}]))
    result=wrapper.build_financial_position(value,cost_exposure=proof)
    assert result['currencies']['SAR']['metrics']['forecast_monetary_position']=='1000.00'
    assert result['currencies']['SAR']['completeness']['forecast']


def test_complete_cod_does_not_certify_unresolved_provider_collection():
    from test_operational_balance_financial_position import obligation
    value=state()
    obligation(value,'provider:pending','provider','provider','0.00',party='tabby',expected='100.00',direction='receivable')
    result=build_financial_position(value,cost_exposure=evidence(value))
    cod=next(row for row in result['currencies']['SAR']['tables'] if row['id']=='cod_expected')
    assert cod['complete']
    assert dependency(result,'complete_collection_forecast')['status']=='incomplete'


def test_stale_period_and_cod_proof_cannot_make_dependencies_ready():
    value=state(); proof=evidence(value)
    proof['profit_period']['as_of']='2026-10-01T00:00:00Z'
    proof['cod_forecast_coverage']['as_of']='2026-10-01T00:00:00Z'
    result=build_financial_position(value,cost_exposure=proof)
    assert dependency(result,'complete_collection_forecast')['status']=='unavailable'
    assert dependency(result,'profit_period_coverage')['status']=='unavailable'
