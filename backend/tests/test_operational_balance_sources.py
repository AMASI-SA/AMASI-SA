"""Boundary tests use a deny-list database: all nonapproved access raises."""
import asyncio
from copy import deepcopy
import pytest
import operational_balance_sources as src

START = '2026-10-05T00:00:00+00:00'
NOW = '2026-10-07T12:00:00+00:00'

class Cursor:
    def __init__(self, values): self.values = values
    async def to_list(self, count): return deepcopy(self.values[:count])

class Collection:
    def __init__(self, db, name): self.db, self.name = db, name
    def find(self, query, projection=None):
        if self.name == 'users':
            assert query == {'$or': [{'id': 'owner', 'role': 'owner'}, {'created_by': 'owner', 'role': 'admin'}]}
            self.db.reads.append(self.name)
            return Cursor([r for r in self.db.data.get(self.name, []) if
                           (r.get('id') == 'owner' and r.get('role') == 'owner') or
                           (r.get('created_by') == 'owner' and r.get('role') == 'admin')])
        assert query == {'user_id': 'owner'}
        self.db.reads.append(self.name)
        return Cursor([r for r in self.db.data.get(self.name, []) if r.get('user_id') == query['user_id']])
    def __getattr__(self, name): raise AssertionError(f'Write or unsupported operation: {name}')

class DB:
    def __init__(self, data=None): self.data, self.reads = data or {}, []
    def __getitem__(self, name):
        assert name in src.ALLOWED_COLLECTIONS, f'Forbidden source: {name}'
        return Collection(self, name)
    def __getattr__(self, name): raise AssertionError(f'Nonallowlisted access: {name}')

def run(awaitable): return asyncio.run(awaitable)
def row(**kw): return {'user_id': 'owner', **kw}
def order(**kw):
    return row(order_number='10', raw_by_source={'salla_direct': {
        'id': '100', 'reference_id': '10', 'created_at': '2026-10-06T01:00:00+00:00',
        'status': 'in_progress', 'currency': 'SAR', 'total': {'amount': '300', 'currency': 'SAR'},
        'items': [{'id': 'line', 'product_id': 'p1', 'quantity': 2}], **kw}})

def test_entity_identity_exact_owner_and_no_alias():
    db = DB({'mezan_employees_v2': [row(id='e1', name='A', legacy_id='old'), {'user_id': 'other', 'id': 'e2'}, row(id='e3', status='inactive')]})
    result = run(src.entities(db, 'owner', 'employee'))
    assert [r['id'] for r in result] == ['e1']
    assert 'legacy_id' not in result[0]
    assert db.reads == ['mezan_employees_v2']

def test_legacy_only_identity_is_not_visible():
    db = DB({'employees': [row(id='old', name='Old')]})
    assert run(src.entities(db, 'owner', 'employee')) == []
    with pytest.raises(ValueError, match='rejected'):
        run(src.rows(db, 'owner', 'employees'))

def test_duplicate_native_identity_fails_closed():
    db = DB({'mezan_suppliers_v2': [row(id='s'), row(id='s')]})
    with pytest.raises(ValueError, match='ambiguous'): run(src.entities(db, 'owner', 'supplier'))

def test_bank_cash_type_and_currency_preserved():
    db = DB({'mz2_financial_accounts': [row(id='b', name='Bank', account_type='bank', currency='USD', status='active'), row(id='c', name='Cash', account_type='cash', currency='SAR', status='active')]})
    assert run(src.entities(db, 'owner', 'bank'))[0]['currency'] == 'USD'
    assert [r['id'] for r in run(src.entities(db, 'owner', 'cash'))] == ['c']

def test_raw_order_only_no_root_financial_fallback():
    db = DB({'unified_orders': [row(order_number='10', total=999, status='delivered')]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert output['orders'] == []
    assert output['issues'][0]['code'] == 'canonical_salla_source_missing'
    assert set(db.reads) <= src.ALLOWED_COLLECTIONS

def test_new_order_cost_only_from_mz2_profile():
    db = DB({'unified_orders': [order()], 'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='20')]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert output['orders'][0]['items'] == [{'id': 'salla:10:line', 'supplier_id': None, 'cost': '20', 'quantity': '2'}]
    db.data['mezan_product_cost_profiles_v2'] = []
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert output['orders'][0]['items'] == []
    assert any(i['code'] == 'mz2_product_cost_incomplete' for i in output['issues'])

def test_partial_supplier_invoice_confirms_only_received_piece():
    db = DB({'unified_orders': [order()], 'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='20')],
        'mezan_suppliers_v2': [row(id='supplier')],
        'mezan_preparation_pieces_v1': [row(id=f'piece{i}', order_number='10', order_item_id='salla:10:line', unit_index=i,
            supplier_id='supplier', status='received' if i == 1 else 'assigned', received_at='2026-10-06T05:00:00+00:00' if i == 1 else None) for i in (1, 2)],
        'mezan_supplier_invoices_v2': [row(id='invoice', supplier_id='supplier', approved_at='2026-10-06T05:00:00+00:00',
            lines=[{'piece_ids': ['piece1'], 'product_price_authority': 'mezan_v2', 'product_unit_price_halalas': 2200}])]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert len(output['orders'][0]['items']) == 2
    assert len(output['supplier_receipts']) == 1
    assert output['supplier_receipts'][0]['item_id'] == 'piece1'
    assert output['supplier_receipts'][0]['amount'] == '22'

def test_bank_order_status_and_start_gate():
    db = DB({'unified_orders': [order()]})
    assert run(src.order_bank_eligible(db, 'owner', '10', START)) == '100'
    with pytest.raises(ValueError, match='precedes'):
        run(src.order_bank_eligible(db, 'owner', '10', NOW))
    db.data['unified_orders'][0]['raw_by_source']['salla_direct']['status'] = 'cancelled'
    with pytest.raises(ValueError, match='ineligible'):
        run(src.order_bank_eligible(db, 'owner', '10', START))

def test_fee_contract_flattened_without_inventing_refund_or_tax_terms():
    policy = dict(id='fee', provider='tabby', percentage='4', vat_treatment='exclusive', effective_from='2026-01-01')
    db = DB({'mz2_provider_fee_policies_v2': [row(provider='tabby', policies=[policy])]})
    assert run(src.collect_sources(db, 'owner', START, NOW))['fee_policies'] == [policy]

def test_salary_versions_are_mz2_only_and_exact_decimal():
    db = DB({'mezan_employees_v2': [row(id='e', status='active')], 'mezan_employee_salary_contracts_v2': [row(employee_id='e', salary_revisions=[
        {'effective_from': '2026-01-01', 'monthly_amount': '3000.01'}, {'effective_from': '2026-10-07', 'monthly_amount': '3100.99'}])]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert [r['salary'] for r in output['employees'][0]['salary_revisions']] == ['3000.01', '3100.99']

def test_recurring_invoice_replaces_daily_estimate_no_cash_source():
    db = DB({'operating_recurring_obligations_v2': [row(id='rent', status='active', start_date='2026-10-01', cycle='monthly', period_amount='3100', expense_type='rent', entity_type='branch', entity_id='branch')],
             'operating_recurring_invoices_v2': [row(id='invoice', obligation_id='rent', period_start='2026-10-01', period_end='2026-10-31', amount='6200')]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert len(output['recurring']) == 3
    assert all(r['amount'] == '200.00' and r['confirmed'] for r in output['recurring'])
    assert 'movements' not in output


def test_capture_requires_native_reference_amount_and_timestamp():
    payment = {'method': 'tabby', 'status': 'paid', 'paid_amount': '300', 'paid_at': '2026-10-06T02:00:00+00:00', 'reference': 'capture1'}
    db = DB({'unified_orders': [order(payment=payment)]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert output['orders'][0]['payment']['captures'][0]['amount'] == '300'
    del db.data['unified_orders'][0]['raw_by_source']['salla_direct']['payment']['reference']
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert 'captures' not in output['orders'][0]['payment']
    assert any(i['code'] == 'provider_capture_evidence_incomplete' for i in output['issues'])


def test_ad_snapshot_keeps_cumulative_value_and_checks_close_proof():
    import hashlib
    import json
    proof = {'version': 1, 'complete': True, 'complete_response': True, 'explicit_spend_present': True,
        'account_id': 'a', 'business_date': '2026-10-06', 'timezone': 'Asia/Riyadh', 'currency': 'SAR', 'spend_native': '450', 'observed_at': NOW}
    proof['fingerprint'] = hashlib.sha256(json.dumps(proof, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    db = DB({'mz2_ad_account_bindings_v2': [row(_id='binding', platform='meta', funding_mode='postpaid', integration_account_id='i', platform_account_id='a', confirmed_by='owner', confirmed_at=START)],
        'mezan_integration_accounts_v2': [row(mezan_integration_account_id='i', provider='meta_ads', external_account_id='a', timezone='Asia/Riyadh', currency='SAR', connection_status='connected')],
        'mezan_meta_performance_daily_v2': [row(_id='snapshot', ad_account_id='a', provider='meta_ads', date='2026-10-06', spend_native='450', currency_native='SAR', account_timezone='Asia/Riyadh', observed_at=NOW, source_close_proof=proof)]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert len(output['ad_snapshots']) == 1
    assert output['ad_snapshots'][0]['amount'] == '450'
    assert output['ad_snapshots'][0]['closed'] is True
    db.data['mezan_meta_performance_daily_v2'][0]['source_close_proof']['spend_native'] = '999'
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert output['ad_snapshots'][0]['closed'] is False


def test_salary_inactive_without_effective_history_is_incomplete():
    db = DB({'mezan_employees_v2': [row(id='e', status='inactive')], 'mezan_employee_salary_contracts_v2': [row(employee_id='e', effective_from='2026-01-01', monthly_amount='3000')]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert output['employees'] == []
    assert any(i['code'] == 'employee_salary_contract_incomplete' for i in output['issues'])


def test_duplicate_canonical_order_rejected_and_prestart_order_excluded():
    db = DB({'unified_orders': [order(), order()]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert output['orders'] == []
    assert any(i['code'] == 'canonical_order_identity_ambiguous' for i in output['issues'])
    db.data['unified_orders'] = [order(created_at='2026-10-04T00:00:00+00:00')]
    assert run(src.collect_sources(db, 'owner', START, NOW))['orders'] == []

def test_native_options_materials_and_service_costs_separated():
    raw = order(items=[{'id': 'line', 'product_id': 'p1', 'quantity': 2, 'options': [{'id': 'color', 'name': 'Color', 'value': {'id': 'red', 'name': 'Red'}}]}])
    db = DB({'unified_orders': [raw], 'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='20')],
        'mezan_product_resource_bindings_v2': [row(id='material-link', salla_product_id='p1', resource_id='cloth', quantity='2')],
        'mezan_product_option_cost_bindings_v2': [row(id='direct', salla_product_id='p1', option_id='color', value_id='red', mode='direct', direct_amount='3', quantity='2'),
            row(id='service', salla_product_id='p1', option_id='color', value_id='red', mode='resource', resource_id='print', quantity='1')],
        'mezan_cost_resources_v2': [row(id='cloth', kind='component', unit_cost='5'), row(id='print', kind='service', unit_cost='7')]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    items = output['orders'][0]['items']
    assert [(r['id'], r['cost'], r['quantity']) for r in items] == [('salla:10:line', '36', '2'), ('salla:10:line:service:print', '7', '2')]
    # An unmatched option never incurs the conditional cost.
    db.data['unified_orders'][0]['raw_by_source']['salla_direct']['items'][0]['options'][0]['value'] = {'id': 'blue', 'name': 'Blue'}
    items = run(src.collect_sources(db, 'owner', START, NOW))['orders'][0]['items']
    assert len(items) == 1 and items[0]['cost'] == '30'


def test_approved_invoice_confirms_product_and_service_without_current_received_status():
    db = DB({'unified_orders': [order()], 'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='20')],
        'mezan_suppliers_v2': [row(id='supplier')],
        'mezan_preparation_pieces_v1': [row(id=f'piece{i}', order_number='10', order_item_id='salla:10:line', unit_index=i,
            supplier_id='supplier', status='in_progress', services=[{'service_id': 'print', 'required_quantity': '2', 'reference_unit_cost': '7'}]) for i in (1, 2)],
        'mezan_supplier_invoices_v2': [row(id='invoice', supplier_id='supplier', approved_at='2026-10-06T05:00:00+00:00',
            lines=[{'piece_ids': ['piece1'], 'product_price_authority': 'mezan_v2', 'product_unit_price_halalas': 2200,
                    'services': [{'service_id': 'print', 'unit_price_halalas': 800, 'quantity_per_piece': '2'}]}])]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert len(output['orders'][0]['items']) == 4
    assert [(r['item_id'], r['amount']) for r in output['supplier_receipts']] == [('piece1', '22'), ('piece1:service:print', '16')]
    assert all(r['invoice_id'] == 'invoice' for r in output['supplier_receipts'])
    assert output == run(src.collect_sources(db, 'owner', START, NOW))


def test_missing_resource_cost_is_incomplete_never_zero():
    db = DB({'unified_orders': [order()], 'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='20')],
        'mezan_product_resource_bindings_v2': [row(id='link', salla_product_id='p1', resource_id='absent', quantity=1)]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert output['orders'][0]['items'] == []
    assert any(r['code'] == 'mz2_product_cost_incomplete' for r in output['issues'])


def test_ad_baseline_stale_snapshot_is_preserved_but_not_verified():
    db = DB({'mz2_ad_account_bindings_v2': [row(_id='binding', platform='meta', funding_mode='postpaid', integration_account_id='i', platform_account_id='a', confirmed_by='owner', confirmed_at=START)],
        'mezan_integration_accounts_v2': [row(mezan_integration_account_id='i', provider='meta_ads', external_account_id='a', timezone='Asia/Riyadh', currency='SAR', connection_status='connected')],
        'mezan_meta_performance_daily_v2': [row(_id='snapshot', ad_account_id='a', provider='meta_ads', date='2026-10-05', spend_native='200', currency_native='SAR', account_timezone='Asia/Riyadh', observed_at='2026-10-04T23:00:00+00:00')]})
    baseline = run(src.start_baselines(db, 'owner', START))
    assert baseline['ad_days'][0]['amount'] == '200'
    assert baseline['ad_days'][0]['verified'] is False
    assert baseline['issues'][0]['code'] == 'ad_start_snapshot_requires_reconciliation'
    db.data['mezan_meta_performance_daily_v2'][0]['observed_at'] = START
    baseline = run(src.start_baselines(db, 'owner', START))
    assert baseline['ad_days'][0]['verified'] is True
    db.data['mezan_meta_performance_daily_v2'][0].update(spend_native='450', observed_at='2026-10-05T05:00:00+00:00')
    output = run(src.collect_sources(db, 'owner', START, NOW, baselines=baseline))
    assert output['ad_snapshots'][0]['amount'] == '250'
    assert output['ad_snapshots'][0]['source_cumulative_amount'] == '450'

# This independent list deliberately does not use the production allowlist.
# A future accidental source fallback must fail the runtime read, even if the
# production developer also adds it to ALLOWED_COLLECTIONS.
HARD_SOURCE_ALLOWLIST = frozenset({
    'users', 'expense_categories', 'mz2_ad_automation_policies_v2',
    'mezan_employees_v2', 'mezan_suppliers_v2', 'store_drivers', 'mz2_external_persons_v2', 'mz2_financial_accounts',
    'mz2_shipping_setup_v2', 'mz2_provider_fee_policies_v2', 'mz2_ad_account_bindings_v2', 'mezan_integration_accounts_v2',
    'mz2_ad_fx_snapshots_v2',
    'unified_orders', 'mezan_product_cost_profiles_v2', 'mezan_product_resource_bindings_v2',
    'mezan_product_option_cost_bindings_v2', 'mezan_cost_resources_v2', 'mezan_preparation_pieces_v1',
    'mezan_supplier_invoices_v2', 'store_delivery_assignments', 'store_delivery_collections',
    'mezan_employee_salary_contracts_v2', 'operating_recurring_obligations_v2', 'operating_recurring_invoices_v2',
    'mezan_snapchat_daily_projections_v2', 'mezan_meta_performance_daily_v2',
    'mezan_tiktok_performance_daily_v2', 'mezan_google_ads_performance_daily_v2',
})

class HardBoundaryDB(DB):
    def __getitem__(self, name):
        assert name in HARD_SOURCE_ALLOWLIST, f'Forbidden runtime source access: {name}'
        return Collection(self, name)


def test_runtime_collection_gate_exercises_every_reader_and_traps_legacy():
    forbidden = ('employees', 'suppliers', 'financial_accounts', 'operating_salaries', 'accounting_settings',
                 'accounting_journal_entries', 'payment_fee_policies', 'return_cases')
    db = HardBoundaryDB({name: [row(id='tempting-fallback', amount='99999')] for name in forbidden})
    for kind in ('employee', 'supplier', 'store_driver', 'external_person', 'bank', 'cash', 'provider', 'courier', 'ad_account',
                 'employee_custody', 'owner_withdrawal', 'operating_expense'):
        run(src.entities(db, 'owner', kind))
    run(src.collect_sources(db, 'owner', START, NOW))
    run(src.start_baselines(db, 'owner', START))
    with pytest.raises(ValueError, match='canonical_order_missing'):
        run(src.order_bank_eligible(db, 'owner', 'missing', START))
    assert set(db.reads) == HARD_SOURCE_ALLOWLIST
    for name in forbidden:
        with pytest.raises(AssertionError, match='Forbidden runtime'):
            db[name].find({'user_id': 'owner'})
    assert not (set(db.reads) & set(forbidden))


def test_native_fee_policy_effective_dates_and_financial_parity_without_fallback():
    from datetime import date
    from operational_balance_engine import provider_projection
    policy = {'id': 'current', 'provider': 'tabby', 'status': 'active', 'percentage': '2.5', 'fixed_amount': '1',
              'minimum': None, 'maximum': None, 'vat_treatment': 'inclusive', 'currency': 'SAR', 'effective_from': '2026-10-01'}
    old = {**policy, 'id': 'old', 'percentage': '5', 'effective_from': '2026-01-01', 'effective_to': '2026-09-30'}
    db = HardBoundaryDB({'mz2_provider_fee_policies_v2': [row(provider='tabby', currency='SAR', policies=[old, policy])],
                        'payment_fee_policies': [row(provider='tabby', percentage='99')]})
    policies = run(src.collect_sources(db, 'owner', START, NOW))['fee_policies']
    payment = {'provider': 'tabby', 'currency': 'SAR', 'gross': '1000', 'cancelled': '0', 'refunds': []}
    current = provider_projection(payment, policies, date(2026, 10, 6))
    previous = provider_projection(payment, policies, date(2026, 9, 30))
    assert (current['estimated_fees'], current['expected_receivable'], current['policy_id']) == ('26.00', '974.00', 'current')
    assert (previous['estimated_fees'], previous['expected_receivable']) == ('51.00', '949.00')
    assert 'payment_fee_policies' not in db.reads
    with pytest.raises(ValueError, match='refund_fee_treatment_incomplete'):
        provider_projection({**payment, 'refunds': [{'id': 'r', 'amount': '100', 'status': 'executed'}]}, policies, date(2026, 10, 6))
    with pytest.raises(ValueError, match='cancellation_fee_treatment_incomplete'):
        provider_projection({**payment, 'cancelled': '100'}, policies, date(2026, 10, 6))
    with pytest.raises(ValueError):
        provider_projection(payment, [{**policy, 'vat_treatment': 'exclusive'}], date(2026, 10, 6))


def test_invoice_quantity_conflict_and_fractional_service_rounding_parity():
    db = DB({'unified_orders': [order(items=[{'id': 'line', 'product_id': 'p1', 'quantity': 3}])],
        'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='0')], 'mezan_suppliers_v2': [row(id='supplier')],
        'mezan_preparation_pieces_v1': [row(id=f'piece{i}', order_number='10', order_item_id='salla:10:line', unit_index=i,
            supplier_id='supplier', status='in_progress') for i in (1, 2, 3)],
        'mezan_supplier_invoices_v2': [row(id='invoice', supplier_id='supplier', approved_at='2026-10-06T05:00:00+00:00',
            lines=[{'piece_ids': ['piece3', 'piece1', 'piece2'], 'quantity': 3, 'product_price_authority': 'mezan_v2',
                'product_unit_price_halalas': 0, 'product_total_halalas': 0, 'total_halalas': 100,
                'services': [{'service_id': 'tiny', 'unit_price_halalas': 100, 'quantity_per_piece': '0.3333', 'total_halalas': 100}]}])]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    amounts = {r['item_id']: r['amount'] for r in output['supplier_receipts'] if ':service:' in r['item_id']}
    assert amounts == {'piece1:service:tiny': '0.33', 'piece2:service:tiny': '0.34', 'piece3:service:tiny': '0.33'}
    db.data['mezan_supplier_invoices_v2'][0]['lines'][0]['piece_ids'].reverse()
    assert output == run(src.collect_sources(db, 'owner', START, NOW))
    db.data['mezan_supplier_invoices_v2'][0]['lines'][0]['quantity'] = 4
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert output['supplier_receipts'] == []
    assert any(i['code'] == 'supplier_invoice_amount_or_quantity_incomplete' for i in output['issues'])

def test_duplicate_invoice_identity_and_piece_coverage_fail_closed():
    base = row(id='inv', supplier_id='supplier', approved_at='2026-10-06T05:00:00+00:00',
        lines=[{'piece_ids': ['piece'], 'product_price_authority': 'mezan_v2', 'product_unit_price_halalas': 100}])
    db = DB({'mezan_suppliers_v2': [row(id='supplier')], 'mezan_supplier_invoices_v2': [base, deepcopy(base)]})
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert output['supplier_receipts'] == []
    assert any(i['code'] == 'supplier_invoice_identity_ambiguous' for i in output['issues'])
    db.data['mezan_supplier_invoices_v2'] = [base]
    base['lines'].append(deepcopy(base['lines'][0]))
    output = run(src.collect_sources(db, 'owner', START, NOW))
    assert any(i['code'] == 'supplier_invoice_piece_coverage_ambiguous' for i in output['issues'])


@pytest.mark.parametrize('currency,rate,expected', [('SAR', None, '10'), ('USD', '3.75', '37.50'), ('USD', None, None)])
def test_native_ad_fx_snapshot_only_preserves_original_currency(currency, rate, expected):
    db = HardBoundaryDB({'mz2_ad_account_bindings_v2': [row(_id='binding', platform='meta', funding_mode='postpaid', integration_account_id='i', platform_account_id='a', confirmed_by='owner', confirmed_at=START)],
        'mezan_integration_accounts_v2': [row(mezan_integration_account_id='i', provider='meta_ads', external_account_id='a', timezone='Asia/Riyadh', currency=currency, connection_status='connected')],
        'mezan_meta_performance_daily_v2': [row(_id='snapshot', ad_account_id='a', provider='meta_ads', date='2026-10-06', spend_native='10', currency_native=currency, account_timezone='Asia/Riyadh', observed_at=NOW)],
        'exchange_rates': [row(currency='USD', rate='99')],
        'mz2_ad_fx_snapshots_v2': [row(id='fx', status='active', currency='USD', business_date='2026-10-06',
            fx_rate_to_sar=rate, fx_at='2026-10-06T00:00:00+00:00', fx_source='approved-bank-rate', evidence='rate-document',
            confirmed_by='owner', confirmed_at='2026-10-06T01:00:00+00:00')] if rate else []})
    result = run(src.collect_sources(db, 'owner', START, NOW))
    fact = result['ad_snapshots'][0]
    assert fact['amount'] == '10' and fact['currency'] == currency
    assert fact.get('sar_amount') == expected
    assert 'exchange_rates' not in db.reads
    if rate:
        assert (fact['fx_snapshot_id'], fact['fx_rate'], fact['fx_date']) == ('fx', '3.75', '2026-10-06')
        assert fact['fx_at'] == '2026-10-06T00:00:00+00:00'
    elif currency != 'SAR':
        assert any(i['code'] == 'ad_fx_snapshot_incomplete' for i in result['issues'])
        assert 'fx_rate' not in fact

def test_cross_invoice_same_physical_component_rejected_even_after_supplier_change():
    from operational_balance_engine import reconcile
    original = row(id='first', supplier_id='supplier', approved_at='2026-10-06T05:00:00+00:00',
        lines=[{'piece_ids': ['piece1'], 'quantity': 1, 'product_price_authority': 'mezan_v2', 'product_unit_price_halalas': 1000}])
    db = DB({'unified_orders': [order(items=[{'id': 'line', 'product_id': 'p1', 'quantity': 1}])],
        'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='10')],
        'mezan_suppliers_v2': [row(id='supplier'), row(id='second-supplier')],
        'mezan_preparation_pieces_v1': [row(id='piece1', order_number='10', order_item_id='salla:10:line', unit_index=1, supplier_id='supplier')],
        'mezan_supplier_invoices_v2': [original]})
    first_sources = run(src.collect_sources(db, 'owner', START, NOW))
    state = reconcile({'status': 'active', 'started_at': START}, first_sources, NOW)
    assert sum(src.number(r['confirmed']) for r in state['engine']['obligations'].values()) == 10
    # A later nonfinancial closure marker does not change operational identity.
    original['accounting_status'] = 'closed'
    assert first_sources == run(src.collect_sources(db, 'owner', START, NOW))
    second = deepcopy(original)
    second.update(id='second', supplier_id='second-supplier')
    db.data['mezan_supplier_invoices_v2'].append(second)
    conflicting = run(src.collect_sources(db, 'owner', START, NOW))
    assert conflicting['supplier_receipts'] == []
    assert any(i['code'] == 'supplier_cross_invoice_component_coverage_ambiguous' for i in conflicting['issues'])
    preserved = reconcile(state, conflicting, NOW)
    assert sum(src.number(r['confirmed']) for r in preserved['engine']['obligations'].values()) == 10
    fresh = reconcile({'status': 'active', 'started_at': START}, conflicting, NOW)
    assert sum(src.number(r['confirmed']) for r in fresh['engine']['obligations'].values()) == 0

def test_custody_reuses_native_employee_identity_and_expenses_native_registry():
    db = HardBoundaryDB({'mezan_employees_v2': [row(id='employee', name='Ahmad', status='active')],
        'expense_categories': [row(code='custom', name='Native category', status='active'), row(code='salary', name='Reserved', status='active')],
        'expense_category_tree': [row(id='tree-only', name='Forbidden')]})
    custody = run(src.entities(db, 'owner', 'employee_custody'))
    assert custody == [{'id': 'employee', 'name': 'Ahmad', 'currency': 'SAR', 'kind': 'employee_custody'}]
    categories = {r['id']: r for r in run(src.entities(db, 'owner', 'operating_expense'))}
    assert {'fuel', 'rent', 'telecom', 'utilities', 'hospitality_food', 'hospitality_drinks', 'subscriptions', 'maintenance', 'office', 'transportation', 'other', 'custom'} <= set(categories)
    assert 'salary' not in categories and 'tree-only' not in categories
    assert categories['custom']['source'] == 'mz2_category_registry'
    assert 'expense_category_tree' not in db.reads


def test_owner_withdrawal_only_exact_owner_and_scoped_active_native_admin():
    db = HardBoundaryDB({'users': [{'id': 'owner', 'role': 'owner', 'name': 'Owner'},
        {'id': 'admin', 'role': 'admin', 'created_by': 'owner', 'name': 'Manager'},
        {'id': 'disabled', 'role': 'admin', 'created_by': 'owner', 'disabled': True},
        {'id': 'foreign', 'role': 'admin', 'created_by': 'other'},
        {'id': 'accountant', 'role': 'accountant', 'created_by': 'owner'}]})
    assert [r['id'] for r in run(src.entities(db, 'owner', 'owner_withdrawal'))] == ['owner', 'admin']


def test_hybrid_ad_uses_confirmed_versioned_native_split_only():
    binding = row(_id='binding', platform='meta', funding_mode='hybrid', hybrid_policy='explicit_split', version=2,
                  integration_account_id='i', platform_account_id='a', confirmed_by='owner', confirmed_at=START)
    policy = row(id='policy', platform='meta', integration_account_id='i', binding_version=2, version=1, status='active',
                 confirmed_by='owner', confirmed_at=START, start_date='2026-10-05', wallet_fraction='0.4', evidence='owner-approved split')
    db = HardBoundaryDB({'mz2_ad_account_bindings_v2': [binding], 'mz2_ad_automation_policies_v2': [policy],
        'mezan_integration_accounts_v2': [row(mezan_integration_account_id='i', provider='meta_ads', external_account_id='a', timezone='Asia/Riyadh', currency='SAR', connection_status='connected')],
        'mezan_meta_performance_daily_v2': [row(_id='snapshot', ad_account_id='a', provider='meta_ads', date='2026-10-06', spend_native='100', currency_native='SAR', account_timezone='Asia/Riyadh', observed_at=NOW)]})
    entity = run(src.entities(db, 'owner', 'ad_account'))[0]
    assert entity['wallet_fraction'] == '0.4' and entity['settings_complete'] is True
    snapshot = run(src.collect_sources(db, 'owner', START, NOW))['ad_snapshots'][0]
    assert snapshot['funding_type'] == 'hybrid' and snapshot['wallet_fraction'] == '0.4'
    assert snapshot['hybrid_policy_id'] == 'policy'
    policy['binding_version'] = 1
    assert run(src.entities(db, 'owner', 'ad_account'))[0]['settings_complete'] is False
    assert run(src.collect_sources(db, 'owner', START, NOW))['ad_snapshots'] == []

def test_missing_display_names_never_expose_technical_identity():
    db = HardBoundaryDB({'mezan_suppliers_v2': [row(id='technical-secret-id', status='active')],
        'users': [{'id': 'owner', 'role': 'owner'}, {'id': 'admin-id', 'role': 'admin', 'created_by': 'owner'}]})
    supplier = run(src.entities(db, 'owner', 'supplier'))[0]
    assert supplier['name'] == 'جهة — إعداد الاسم غير مكتمل' and supplier['ready'] is False
    names = [r['name'] for r in run(src.entities(db, 'owner', 'owner_withdrawal'))]
    assert names == ['المالك', 'المدير']
