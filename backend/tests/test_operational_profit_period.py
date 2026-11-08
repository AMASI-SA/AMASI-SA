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


def test_revised_salary_cannot_certify_conflicting_recorded_day():
    def fixture(db, state):
        db.data['mezan_employees_v2'] = [{'user_id':'owner','id':'employee1','status':'active','hire_date':'2026-11-01'}]
        db.data['mezan_employee_salary_contracts_v2'] = [{'user_id':'owner','employee_id':'employee1',
            'effective_from':'2026-11-01', 'monthly_amount':'3000', 'currency':'SAR'}]
        state['engine']['salary_days'] = {'salary:employee1:2026-11-02': {'amount':'90', 'currency':'SAR'}}
        state['engine']['salary_conflicts'] = {'salary:employee1:2026-11-02': {'reason':'revision_changed'}}
    value = collect_one(order(status='delivered'), fixture)['by_currency']['SAR']['salaries']
    assert not value['complete'] and value['amount'] is None
    assert any(row['code'] == 'profit_salary_recorded_reconciliation_required' for row in value['issues'])


def test_salary_period_is_bounded_before_candidate_expansion(monkeypatch):
    import operational_profit_period as module
    def fixture(db, state):
        state['started_at'] = '1900-01-01T00:00:00Z'
        db.data['mezan_employees_v2'] = [{'user_id':'owner','id':'employee1','status':'active','hire_date':'2026-11-01'}]
        db.data['mezan_employee_salary_contracts_v2'] = [{'user_id':'owner','employee_id':'employee1',
            'effective_from':'2026-11-01', 'monthly_amount':'3000', 'currency':'SAR'}]
    def forbidden(*args):
        raise AssertionError('unbounded candidates invoked')
    monkeypatch.setattr(module, 'candidates', forbidden)
    value = collect_one(order(status='delivered'), fixture)
    assert any(r['code'] == 'profit_salary_period_or_workload_unbounded' for r in value['issues']['salaries'])


def test_proven_cash_collection_has_no_platform_fee_without_guessing_missing_method():
    from operational_profit_order_costs import collect as costs
    from operational_open_cost_exposure import READS
    doc = order(status='delivered', paid='100')
    raw = doc['raw_by_source']['salla_direct']
    db = seeded_db(order_row=doc)
    data = {name:db.data.get(name,[]) for name in READS}
    state = operating_state()
    oid = str(raw['id'])
    state['engine'].setdefault('facts', {}).update({
        'shipping:'+oid: {'id':'driver', 'party_type':'store_driver'},
        'cod_collection:'+oid: {'party_id':'driver', 'amount':'100', 'evidence': {
            'evidence_complete':True, 'collection_confirmed':True, 'payment_method':'cash',
            'gross':'100', 'collected':'100', 'outstanding':'0', 'custody_amount':'100', 'evidence_ids':['receipt']}}})
    row = costs(state,data,[raw],[])['SAR']['payment_fees']
    assert row['complete'] and row['amount'] == '0.00'
    raw.pop('payment_method')
    assert not costs(state,data,[raw],[])['SAR']['payment_fees']['complete']


def test_fee_net_requires_bound_explicit_tax_proof_or_proven_zero_tax():
    from decimal import Decimal
    from operational_profit_order_costs import fee_expense_net
    projection = {'policy_id':'tariff', 'estimated_fee_net':'10', 'estimated_fee_tax':'1.5', 'estimated_fees':'11.5'}
    assert fee_expense_net(projection, Decimal('11.5')) is None
    proof = {'treatment':'INPUT_VAT', 'confirmed':True, 'currency':'SAR',
        'policy_id':'tariff', 'evidence_id':'vatdoc', 'tax_amount':'1.5'}
    assert fee_expense_net(projection, Decimal('11.5'), proof) == Decimal('10')
    for key, value in [('policy_id','other'), ('tax_amount','2'), ('currency','USD'), ('confirmed',False)]:
        assert fee_expense_net(projection, Decimal('11.5'), {**proof,key:value}) is None
    assert fee_expense_net({**projection,'estimated_fee_tax':'0','estimated_fees':'10'}, Decimal('10')) == Decimal('10')
    assert fee_expense_net(None, Decimal('0')) is None


def test_cash_exemption_rejects_partial_card_split_and_conflicting_capture():
    from operational_profit_order_costs import proven_cash_without_provider_fee
    raw = order(status='delivered',paid='100')['raw_by_source']['salla_direct']
    oid = str(raw['id'])
    fact = {'party_id':'driver','amount':'100','evidence':{'evidence_complete':True,
        'collection_confirmed':True,'payment_method':'cash','evidence_ids':['receipt'],
        'gross':'100','collected':'100','outstanding':'0','custody_amount':'100'}}
    facts = {'shipping:'+oid:{'id':'driver','party_type':'store_driver'}, 'cod_collection:'+oid:fact}
    assert proven_cash_without_provider_fee(raw,facts)
    for key, value in [('payment_method','card_terminal'),('collected','90'),('evidence_complete',False)]:
        invalid = deepcopy(facts)
        invalid['cod_collection:'+oid]['evidence'][key] = value
        assert not proven_cash_without_provider_fee(raw,invalid)
    assert not proven_cash_without_provider_fee({**raw,'payment_methods':[{'method':'cod'}]},facts)
    assert not proven_cash_without_provider_fee(raw,{**facts,'capture:unexpected':{'order_id':oid,'amount':'1','provider':'tabby'}})


def test_excluded_status_cannot_hide_stale_canonical_snapshot():
    doc = order(status='cancelled')
    doc['g47_salla_snapshot']['source_updated_at'] = '2026-11-01T13:00:00+00:00'
    result = collect_one(doc)
    assert any(row['code'] == 'profit_order_snapshot_unverified' for row in result['issues']['net_sales'])
