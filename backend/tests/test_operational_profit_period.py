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


def test_canonical_sales_cohort_uses_created_date_and_all_approved_statuses():
    doc = order(old=True, status='delivered')
    raw = doc['raw_by_source']['salla_direct']
    raw['amounts']['tax'] = {'amount':'13.04'}
    assert 'net_sales' not in collect_one(doc)['by_currency']['SAR']
    current = order(status='in_progress')
    current['raw_by_source']['salla_direct']['amounts']['tax'] = {'amount':'13.04'}
    assert collect_one(current)['by_currency']['SAR']['net_sales']['amount'] == '86.96'


def test_salary_contract_period_calendar_rounding_no_posting():
    doc = order(status='delivered')
    def salaries(db, state):
        db.data['mezan_employees_v2'] = [{'user_id':'owner','id':'employee1','status':'active','hire_date':'2026-11-01'}]
        db.data['mezan_employee_salary_contracts_v2'] = [{'user_id':'owner','employee_id':'employee1',
            'effective_from':'2026-11-01', 'monthly_amount':'3000', 'currency':'SAR'}]
    row = collect_one(doc, salaries)['by_currency']['SAR']['salaries']
    # Approved legacy cutover starts Nov2, not silently rewritten to Nov1.
    assert row['complete'] and row['amount'] == '100.00' and row['source_count'] == 1


def test_cost_actual_requires_all_received_invoiced_components_not_catalog():
    from operational_profit_order_costs import collect as costs
    from operational_open_cost_exposure import READS
    from test_operational_open_cost_exposure import piece
    doc = order(status='delivered')
    raw = doc['raw_by_source']['salla_direct']
    db = seeded_db(order_row=doc, physical=piece('NEW', received=True), invoices=[{
        'id':'invoice1','supplier_id':'supplier1','approved_at':'2026-11-01T11:00:00+00:00',
        'lines':[{'piece_ids':['physical1'],'product_price_authority':'mezan_v2','product_unit_price_halalas':4000}]}])
    data = {name:db.data.get(name,[]) for name in READS}
    state = operating_state()
    row = costs(state,data,[raw],[])['SAR']['cost_of_goods']
    assert not row['complete'] and row['amount'] is None and row['estimated_subtotal'] == '40.00'
    state['engine']['obligations'] = {'receipt-cost':{'kind':'supplier','currency':'SAR',
        'evidence_ids':['receipt:invoice1:physical1:product'], 'source_component_fully_invoiced':True,
        'source_invoice_id':'invoice1', 'net_amount':'40', 'returned_amount':'0'}}
    row = costs(state,data,[raw],[])['SAR']['cost_of_goods']
    assert row['complete'] and row['amount'] == '40.00'
    data['mezan_preparation_pieces_v1'].append(dict(data['mezan_preparation_pieces_v1'][0], id='duplicate'))
    assert not costs(state,data,[raw],[])['SAR']['cost_of_goods']['complete']
