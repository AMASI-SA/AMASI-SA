from copy import deepcopy
import pytest
from orders_db import orders_to_parsed
from excel_parser import match_settings
from shipping_cost_ssot import aggregate_breakdown
from dashboard_order_accumulator import DashboardOrderAccumulator

PAYMENTS = [
    {'name':'مدى', 'commission_percent':2.15,'fixed_fee':1.0,'vat_percent':15},
    {'name':'Visa', 'commission_percent':2.7,'fixed_fee':1.0,'vat_percent':15},
    {'name':'bank transfer','commission_percent':1.25,'fixed_fee':0.3,'vat_percent':15},
]
SHIPPINGS = [{'name':'iMile','cost_per_order':7.135,'vat_percent':15}]
CFG = {'iMile':SHIPPINGS[0]}

def run(rows):
    state = DashboardOrderAccumulator(PAYMENTS, SHIPPINGS, CFG)
    for row in rows:
        state.observe(row)
    state.begin_fee_pass()
    for row in rows:
        state.observe_fees(row)
    return state.finish()

@pytest.mark.parametrize('names', [['مدى'], ['مدى','mada'], ['Visa','visa','bank transfer'], []])
def test_matches_legacy_financial_and_currency_contract(names):
    rows = [dict(order_number=str(i), total_amount=amount, payment_method=name,
                 shipping_company='iMile', shipping_cost=9.245, order_date='2026-10-01',
                 order_status='completed', data_source='salla')
            for i,(name,amount) in enumerate((name,amount) for name in names
                for amount in [0,-10,0.05,10.005,100.11,200.99])]
    if rows:
        rows += [dict(rows[0], order_number='unknown-fx',currency='USD', total_amount=23)]
    before=deepcopy(rows)
    result=run(rows)
    expected=orders_to_parsed(rows)
    assert result['parsed'] == {k:v for k,v in expected.items() if k!='orders_individual'}
    assert result['matched'] == match_settings(expected,PAYMENTS,SHIPPINGS)
    assert result['shipping'] == aggregate_breakdown(rows,CFG)
    assert rows==before

def test_does_not_retain_order_documents():
    state=DashboardOrderAccumulator(PAYMENTS,SHIPPINGS,CFG)
    for i in range(10000):
        state.observe(dict(order_number=str(i),total_amount=50,payment_method='مدى',
                           shipping_company='iMile',unneeded_payload='x'*10000))
    assert state.retained_sample_count==10
    assert state.payment_group_count==1
    with pytest.raises(RuntimeError):
        state.finish()

def test_replay_change_is_rejected_not_partial_financial_response():
    state=DashboardOrderAccumulator(PAYMENTS,SHIPPINGS,CFG)
    row=dict(order_number='1',total_amount=50,payment_method='مدى')
    state.observe(row)
    state.begin_fee_pass()
    state.observe_fees(dict(row,total_amount=60))
    with pytest.raises(RuntimeError,match='identical order replay'):
        state.finish()


def test_distinct_alias_configs_and_boundary_rounding_match_whole_period():
    import random
    rng=random.Random(42)
    settings=PAYMENTS+[dict(name='mada',commission_percent=3.19,fixed_fee=0.123,vat_percent=15)]
    names=['مدى','mada','Visa','visa','bank transfer','unknown rail']
    rows=[dict(order_number=str(i),payment_method=rng.choice(names),
               total_amount=rng.choice([-0.01,0,0.005,1.005,12.335,109.995]),
               shipping_company=rng.choice(['iMile','Unknown']),shipping_cost=2.675)
          for i in range(500)]
    state=DashboardOrderAccumulator(settings,SHIPPINGS,CFG)
    for row in rows: state.observe(row)
    state.begin_fee_pass()
    for row in rows: state.observe_fees(row)
    result=state.finish()
    parsed=orders_to_parsed(rows)
    assert result['parsed']=={k:v for k,v in parsed.items() if k!='orders_individual'}
    assert result['matched']==match_settings(parsed,settings,SHIPPINGS)
    assert result['shipping']==aggregate_breakdown(rows,CFG)
