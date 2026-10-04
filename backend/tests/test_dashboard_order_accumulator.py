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


@pytest.mark.parametrize('batch_size', [1, 7, 128])
def test_fee_batches_match_canonical_across_aliases_refunds_and_halalah(batch_size):
    import random
    rng = random.Random(912)
    settings = PAYMENTS + [dict(name='mada', commission_percent=3.19, fixed_fee=0.123, vat_percent=15)]
    names = ['مدى', 'mada', 'Visa', 'visa', 'bank transfer', 'unknown rail', '', 'Cash on Delivery']
    rows = [dict(order_number=str(i), payment_method=rng.choice(names),
                 total_amount=rng.choice([-10.015, -0.01, 0, 0.005, 0.05, 1.005, 12.335, 109.995]),
                 shipping_company='iMile', shipping_cost=2.675) for i in range(513)]
    rows.append(dict(rows[0], order_number='unknown-fx', currency='USD', total_amount=23))
    original = deepcopy(rows)
    state = DashboardOrderAccumulator(settings, SHIPPINGS, CFG)
    for row in rows:
        state.observe(row)
    state.begin_fee_pass()
    for offset in range(0, len(rows), batch_size):
        state.observe_fee_batch(iter(rows[offset:offset + batch_size]))
    result = state.finish()
    parsed = orders_to_parsed(rows)
    assert result['parsed'] == {k:v for k,v in parsed.items() if k != 'orders_individual'}
    assert result['matched'] == match_settings(parsed, settings, SHIPPINGS)
    assert result['shipping'] == aggregate_breakdown(rows, CFG)
    assert rows == original


def test_fee_batch_overflow_rejected_before_replay_state_changes():
    rows = [dict(order_number=str(i), total_amount=1.005, payment_method='مدى') for i in range(129)]
    state = DashboardOrderAccumulator(PAYMENTS, SHIPPINGS, CFG)
    for row in rows:
        state.observe(row)
    state.begin_fee_pass()
    with pytest.raises(ValueError, match='128'):
        state.observe_fee_batch(iter(rows))
    assert state.fee_count == 0
    state.observe_fee_batch(rows[:128])
    state.observe_fees(rows[-1])
    assert state.finish()['matched'] == match_settings(orders_to_parsed(rows), PAYMENTS, SHIPPINGS)


def test_batch_uses_one_canonical_calculation_per_final_group(monkeypatch):
    import dashboard_order_accumulator as module
    canonical = module.match_settings
    calls = []
    def capture(parsed, *args):
        calls.append(parsed)
        return canonical(parsed, *args)
    monkeypatch.setattr(module, 'match_settings', capture)
    rows = [dict(order_number=str(i), total_amount=10.005, payment_method='مدى') for i in range(128)]
    state = DashboardOrderAccumulator(PAYMENTS, SHIPPINGS, CFG)
    for row in rows:
        state.observe(row)
    state.begin_fee_pass()
    state.observe_fee_batch(rows)
    assert len(calls) == 1
    assert len(calls[0]['orders_individual']) == 128
    assert calls[0]['payment_methods'][0]['orders_count'] == 128
    assert state.finish()['matched'] == canonical(orders_to_parsed(rows), PAYMENTS, SHIPPINGS)


@pytest.mark.parametrize('name', ['مدى', 'mada', 'Visa'])
def test_fee_batch_carries_exact_per_order_rounding_across_many_batches(name):
    amounts = [-10.015, -0.01, 0, 0.005, 0.05, 1.005, 12.335, 109.995]
    settings = PAYMENTS + [dict(name='mada', commission_percent=3.19, fixed_fee=0.123, vat_percent=15)]
    rows = [dict(order_number=str(i), payment_method=name,
                 total_amount=amounts[i % len(amounts)]) for i in range(1025)]
    state = DashboardOrderAccumulator(settings, SHIPPINGS, CFG)
    for row in rows:
        state.observe(row)
    state.begin_fee_pass()
    for offset in range(0, len(rows), 128):
        state.observe_fee_batch(rows[offset:offset + 128])
    actual = state.finish()['matched']
    assert actual == match_settings(orders_to_parsed(rows), settings, SHIPPINGS)
    assert actual['payment_breakdown'][0]['fee_calculation_basis'] == 'per_order_salla_rounding'


def test_duplicate_salla_aliases_do_not_schedule_unused_fee_replay():
    from payment_methods import normalize_payment_method, SALLA_SUB_KEYS
    state = DashboardOrderAccumulator(PAYMENTS, SHIPPINGS, CFG)
    for i in range(1027):
        name = 'mada reference ' + str(i)
        assert normalize_payment_method(name)[0] in SALLA_SUB_KEYS
        state.observe(dict(order_number=str(i),payment_method=name,total_amount=1.005))
    state.begin_fee_pass()
    assert len(state.fees) == 0
    assert len(state.fees) <= len(SALLA_SUB_KEYS)


@pytest.mark.parametrize('names',[
    ['مدى','mada','MADA','mada reference 1','mada reference 2','Visa'],
    ['مدى','Visa','bank transfer','unknown rail'],
    ['mada reference '+str(i) for i in range(17)]+['Visa','unknown rail'],
])
def test_pruned_fee_scheduling_matches_previous_and_canonical_values(names):
    from decimal import Decimal
    from payment_methods import normalize_payment_method, SALLA_SUB_KEYS
    class BeforeScheduling(DashboardOrderAccumulator):
        def begin_fee_pass(self):
            self.phase='fees'
            for name in self.payments:
                if normalize_payment_method(name)[0] in SALLA_SUB_KEYS:
                    self.fees[name]=dict(count=0,base=Decimal('0'),vat=Decimal('0'))
    settings = PAYMENTS + [dict(name='mada',commission_percent=3.19,fixed_fee=.123,vat_percent=15),
                          dict(name='mada reference 1',commission_percent=1.11,fixed_fee=.005,vat_percent=5)]
    amounts=[-.015,0,.005,.05,1.005,12.335,109.995]
    rows=[dict(order_number=str(i),payment_method=name,total_amount=amounts[j],shipping_company='iMile',shipping_cost=2.675)
          for i,(name,j) in enumerate((name,j) for name in names for j in range(len(amounts)))]
    results=[]
    for cls in (BeforeScheduling,DashboardOrderAccumulator):
        state=cls(settings,SHIPPINGS,CFG)
        for row in rows:state.observe(row)
        state.begin_fee_pass()
        if cls is DashboardOrderAccumulator:
            assert len(state.fees)<=len(SALLA_SUB_KEYS)
        for offset in range(0,len(rows),13):state.observe_fee_batch(rows[offset:offset+13])
        results.append(state.finish())
    assert results[0]==results[1]
    assert results[1]['matched']==match_settings(orders_to_parsed(rows),settings,SHIPPINGS)
