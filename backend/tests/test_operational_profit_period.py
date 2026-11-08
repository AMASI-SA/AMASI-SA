import asyncio
from copy import deepcopy
from datetime import datetime, timezone

from operational_profit_period import collect
from operational_cod_forecast_coverage import project
from test_operational_balance_open_customer_cod import order
from test_operational_open_cost_exposure import seeded_db, operating_state, START, NOW
from operational_open_cost_exposure import OPEN, CLOSED


def collect_one(doc, mutate=lambda *_: None):
    db, state = seeded_db(order_row=doc), operating_state()
    mutate(db, state)
    before = deepcopy((db.data, state))
    data = db.data
    raw = [(r, r['raw_by_source']['salla_direct']) for r in data['unified_orders']]
    value = asyncio.run(collect(db, 'owner', state, raw, data,
        datetime.fromisoformat(START), datetime.fromisoformat(NOW)))
    assert before == (db.data, state)
    return value


def test_sales_explicit_tax_and_snapshot_no_balance_inference():
    doc = order(status='delivered')
    doc['raw_by_source']['salla_direct']['amounts']['tax'] = {'amount': '13.04', 'currency': 'SAR'}
    value = collect_one(doc)['by_currency']['SAR']['net_sales']
    assert value['complete'] and value['amount'] == '86.96'


def test_sales_missing_tax_is_unknown_and_return_not_double_subtracted():
    doc = order(status='delivered')
    assert collect_one(doc)['by_currency']['SAR']['net_sales']['amount'] is None
    doc['raw_by_source']['salla_direct']['amounts']['tax'] = {'amount': '13.04'}
    result = collect_one(doc, lambda db, state: state.update(customer_returns=[
        {'order_id': str(doc['raw_by_source']['salla_direct']['id']), 'amount': '20'}]))
    row = result['by_currency']['SAR']['net_sales']
    assert row['amount'] is None
    assert row['issues'][0]['code'] == 'profit_return_sales_tax_basis_required'


def test_cod_complete_needs_all_candidates_not_just_accepted():
    doc = order()
    raw = doc['raw_by_source']['salla_direct']
    args = ([ (doc, raw) ], {'unified_orders': [doc]}, {},
        datetime.fromisoformat(START), datetime.fromisoformat(NOW))
    proof = project(*args, [], open_statuses=OPEN, closed_statuses=CLOSED)['by_currency']['SAR']
    assert not proof['complete'] and proof['amount'] is None and proof['affected_order_count'] == 1
    evidence = [{'order_id':str(raw['id']), 'currency':'SAR', 'amount':'77.00'}]
    proof = project(*args, evidence, open_statuses=OPEN, closed_statuses=CLOSED)['by_currency']['SAR']
    assert proof['complete'] and proof['amount'] == '77.00'
    raw['payment_method'] = 'unknown_gateway'
    proof = project(*args, evidence, open_statuses=OPEN, closed_statuses=CLOSED)['by_currency']['SAR']
    assert not proof['complete'] and proof['amount'] is None


def test_cod_unknown_source_blocks_other_currency_without_fake_order_count():
    doc = order()
    proof = project([(doc, doc['raw_by_source']['salla_direct'])],
        {'unified_orders':[doc, {}]}, {}, datetime.fromisoformat(START), datetime.fromisoformat(NOW),
        [{'order_id':str(doc['raw_by_source']['salla_direct']['id']), 'currency':'SAR', 'amount':'77'}],
        open_statuses=OPEN, closed_statuses=CLOSED)['by_currency']['SAR']
    assert not proof['complete'] and proof['unidentified_issue_count'] == 1
    assert proof['affected_order_count'] == 0
