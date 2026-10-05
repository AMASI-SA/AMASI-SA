"""Native source regressions. In-memory deny-all-other-collections database only."""
from copy import deepcopy
import hashlib
import json

from backend.tests.test_operational_balance_sources import HardBoundaryDB, row, order, run, START, NOW
import operational_balance_sources as src


def shipping_data(rate=None, **raw):
    base = dict(id='rate', party_type='courier', party_id='carrier', context='delivery', currency='SAR',
                effective_from=START, effective_to=None, status='approved', confirmed_by='owner', confirmed_at=START)
    if rate is None:
        rate = dict(base, delivery_fee='10', cod_fixed_fee='0', cod_percent='1', vat_included=True, vat_percent='15')
    data = {'unified_orders': [order(shipping={'company_code': 'carrier'}, **raw)],
            'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='20')],
            'mz2_shipping_setup_v2': [row(couriers=[dict(courier_key='carrier', name='Carrier', status='active',
                confirmed_by='owner', confirmed_at=START, salla_carrier_keys=['carrier'])], contracts=[rate])]}
    if rate.get('kind') == 'rich':
        data['mz2_shipping_setup_v2'][0]['contract_evidence'] = [{k:v for k,v in item.items() if k not in ('size','snapshot_sha256')} for item in rate['evidence_snapshot']['items']]
    return data


def rich_rate():
    terms = dict(id='rate', user_id='owner', courier_id='carrier', payment_mode='postpaid', shipping_cost='10',
                 shipping_cost_vat_inclusive=True, shipping_vat_percent='15', commission_vat_inclusive=True,
                 commission_vat_percent='15', cod_fee_tiers=[dict(min_amount='0', max_amount=None, min_inclusive=True,
                 max_inclusive=False, commission_percent='0.01', fixed_fee='0', vat_percent='15', vat_included=True)],
                 effective_from=START, effective_to=None, evidence_ref='approved-contract', source_kind='contract',
                 verification_status='approved', approved_by='owner', approved_at=START, revision=1)
    rate = dict(id='rate', kind='rich', party_type='courier', party_id='carrier', context='delivery', currency='SAR',
                status='approved', confirmed_by='owner', confirmed_at=START, effective_from=START, effective_to=None,
                contract_version=terms)
    def fingerprint(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    items=[]
    for purpose in ('contract','shipping_tax','commission_tax'):
        item=dict(evidence_id=purpose,user_id='owner',courier_id='carrier',file_id=purpose,record_type='accountant_reviewed_shipping_source',state='approved',deleted=False,revision=1,approved_by='owner',approved_at=START,source_sha256='a'*64,purpose=purpose,size=5)
        item['snapshot_sha256']=fingerprint(item);items.append(item)
    rate['evidence_snapshot']=dict(schema='mz2.shipping.evidence.snapshot.v1',items=items,sha256=fingerprint(items))
    return rate


def test_rich_shipping_native_calculator_and_component_failure_isolation():
    data = shipping_data(rich_rate(), payment={'method': 'cod'}, paid_amount='100')
    result = run(src.collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
    assert result['orders'][0]['carrier']['cost'] == '12.00'
    revoked = deepcopy(data)
    revoked['mz2_shipping_setup_v2'][0]['contract_evidence'][0]['state'] = 'revoked'
    rejected = run(src.collect_sources(HardBoundaryDB(revoked), 'owner', START, NOW))
    assert len(rejected['orders']) == 1 and rejected['orders'][0]['carrier']['cost'] is None
    bad = deepcopy(data)
    bad['mz2_shipping_setup_v2'][0]['contracts'][0]['contract_version']['source_kind'] = 'legacy_copy'
    result = run(src.collect_sources(HardBoundaryDB(bad), 'owner', START, NOW))
    assert len(result['orders']) == 1 and len(result['orders'][0]['items']) == 1
    assert result['orders'][0]['carrier']['cost'] is None
    assert any(i.get('component') == 'shipping_fee' for i in result['issues'])


def test_cod_reads_root_and_payment_actions_without_double_collection():
    for facts in [dict(paid_amount='100'), dict(payment_actions={'remaining_action': {'remaining_amount': '200', 'paid_amount': '100'}})]:
        result = run(src.collect_sources(HardBoundaryDB(shipping_data(payment={'method': 'cod'}, **facts)), 'owner', START, NOW))
        cod = result['orders'][0]['cod']
        assert (cod['gross'], cod['collected'], cod['outstanding']) == ('300', '100', '200')
        assert result['orders'][0]['cod_amount'] == '200'
    data = shipping_data(payment={'method': 'cod', 'paid_amount': '100'}, paid_amount='200')
    result = run(src.collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
    assert len(result['orders']) == 1 and 'cod_amount' not in result['orders'][0]
    assert any(i['code'] == 'cod_collection_contract_incomplete' for i in result['issues'])


def test_driver_custody_is_only_native_cash_not_bank_or_card():
    for method, custody in [('cash', '200'), ('bank_transfer', '0'), ('card_terminal', '0')]:
        data = shipping_data(payment={'method': 'cod'}, paid_amount='100')
        data['store_delivery_assignments'] = [row(id='assignment', order_number='10', driver_id='driver', status='delivered')]
        data['store_delivery_collections'] = [row(id='collection', assignment_id='assignment', order_number='10',
            driver_id='driver', amount='200', amount_source='unified_orders.remaining_amount', payment_method=method, cod_custody_amount=custody, collected_at=NOW)]
        result = run(src.collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
        assert result['orders'][0]['cod_amount'] == custody
        assert result['orders'][0]['cod']['custody_amount'] == custody
        assert result['orders'][0]['cod']['evidence_complete'] is True
        assert result['orders'][0]['cod']['collected'] == ('300' if method == 'cash' else '100')
        assert result['orders'][0]['cod']['outstanding'] == ('0' if method == 'cash' else '200')
        if method == 'cash':
            data['unified_orders'][0]['raw_by_source']['salla_direct']['paid_amount'] = '300'
            reread = run(src.collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
            assert reread['orders'][0]['cod']['collected'] == '300'


def test_driver_late_collection_and_unproven_partial_amount_are_not_inferred():
    data = shipping_data(payment={'method': 'cod'}, paid_amount='0')
    data['store_delivery_assignments'] = [row(id='assignment', order_number='10', driver_id='driver', status='delivered')]
    result = run(src.collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
    assert result['orders'][0]['cod']['evidence_complete'] is False
    data['store_delivery_collections'] = [row(id='collection', assignment_id='assignment', driver_id='driver',
        amount='100', payment_method='cash', cod_custody_amount='100', collected_at=NOW)]
    result = run(src.collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
    assert result['orders'][0]['cod']['collected'] == '0'
    assert result['orders'][0]['cod']['custody_amount'] == '100'
    assert any(i['code'] == 'driver_collection_remaining_snapshot_incomplete' for i in result['issues'])


def test_rich_valid_base_survives_uncovered_commission_and_cod_stays_confirmed():
    from operational_balance_engine import reconcile
    from decimal import Decimal
    rate = rich_rate()
    rate['contract_version']['cod_fee_tiers'][0]['max_amount'] = '100'
    data = shipping_data(rate, payment={'method': 'cod'}, paid_amount='100', status='delivered')
    result = run(src.collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
    carrier = result['orders'][0]['carrier']
    assert Decimal(carrier['cost']) == Decimal('10') and carrier['fee_complete'] is False
    assert carrier['fee_components'][0] == {'id': 'base_shipping', 'amount': '10.00', 'complete': True}
    assert carrier['fee_components'][1] == {'id': 'cod_commission', 'amount': None, 'complete': False}
    assert any(i['code'] == 'cod_amount_not_covered_by_contract_needs_review' and i['component'] == 'cod_commission'
               and i['recognition_state'] == 'confirmed' for i in result['issues'])
    state = reconcile({'status': 'active', 'started_at': START, 'engine': {}}, result, NOW)
    assert state['engine']['obligations']['cod:100']['confirmed'] == '200.00'
    assert state['engine']['obligations']['shipping:100:base_shipping']['confirmed'] == '10.00'
    data['mz2_shipping_setup_v2'][0]['contracts'][0]['contract_version']['cod_fee_tiers'][0]['max_amount'] = None
    qualified = run(src.collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
    settled = reconcile(state, qualified, NOW)
    settled = reconcile(settled, qualified, NOW)
    assert settled['engine']['obligations']['shipping:100:cod_commission']['confirmed'] == '2.00'
    assert settled['engine']['obligations']['shipping:100:base_shipping']['confirmed'] == '10.00'


def test_supplier_tax_confirmed_gross_and_explicit_net_are_exact():
    data = shipping_data()
    data['mezan_product_cost_profiles_v2'][0]['base_cost'] = '50'
    data['mezan_suppliers_v2'] = [row(id='supplier')]
    data['mezan_preparation_pieces_v1'] = [row(id=f'p{i}', order_number='10', order_item_id='salla:10:line',
        unit_index=i, supplier_id='supplier') for i in (1, 2)]
    data['mezan_supplier_invoices_v2'] = [row(id='invoice', supplier_id='supplier', approved_at=NOW,
        subtotal_halalas=8000, total_halalas=9200, purchase_tax=dict(treatment='INPUT_VAT', amount_halalas=1200,
        entity_id='tax', evidence_file_id='file', evidence_sha256='a'*64, confirmed=True),
        lines=[dict(piece_ids=['p1','p2'], quantity=2, product_price_authority='mezan_v2', product_unit_price_halalas=4000,
                    product_total_halalas=8000, total_halalas=8000)])]
    result = run(src.collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
    assert [(r['net_amount'], r['tax_amount'], r['gross_amount'], r['amount']) for r in result['supplier_receipts']] == [('40','6','46','46')]*2
    from operational_balance_engine import reconcile
    from decimal import Decimal
    state = reconcile({'status': 'active', 'started_at': START, 'engine': {}}, result, NOW)
    suppliers = [r for r in state['engine']['obligations'].values() if r.get('party_type') == 'supplier']
    assert sum(Decimal(r['expected']) for r in suppliers) == Decimal('20')
    assert sum(Decimal(r['confirmed']) for r in suppliers) == Decimal('92')
    data['mezan_supplier_invoices_v2'][0]['purchase_tax']['confirmed'] = False
    result = run(src.collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
    assert result['supplier_receipts'] == []
    assert any(i['code'] == 'supplier_invoice_tax_contract_incomplete' for i in result['issues'])


def test_supplier_invoice_tax_cent_allocation_includes_service_and_is_reorder_stable():
    invoice = dict(subtotal_halalas=3, total_halalas=4,
        purchase_tax=dict(treatment='INPUT_VAT', amount_halalas=1, entity_id='tax', evidence_file_id='file',
                          evidence_sha256='a'*64, confirmed=True),
        lines=[dict(piece_ids=['b','a'], product_unit_price_halalas=1, services=[dict(service_id='svc', unit_price_halalas=1, quantity_per_piece='.5')])])
    values = src.invoice_component_amounts(invoice)
    from decimal import Decimal
    assert sum(Decimal(v['net_amount']) for v in values.values()) == Decimal('.03')
    assert sum(Decimal(v['tax_amount']) for v in values.values()) == Decimal('.01')
    assert sum(Decimal(v['gross_amount']) for v in values.values()) == Decimal('.04')
    invoice['lines'][0]['piece_ids'].reverse()
    assert src.invoice_component_amounts(invoice) == values


def test_recurring_full_period_remains_expected_even_with_invoice():
    data = {'operating_recurring_obligations_v2': [row(id='rent', status='active', start_date='2026-10-01',
        cycle='monthly', period_amount='5000', expense_type='rent', entity_type='branch', auto_renew=True)],
        'operating_recurring_invoices_v2': [row(id='bill', obligation_id='rent', period_start='2026-10-01',
            period_end='2026-10-31', amount='5000', payment_status='unpaid')]}
    result = run(src.collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
    assert len(result['recurring']) == 1
    due = result['recurring'][0]
    assert due['amount'] == '5000' and due['confirmed'] is False
    assert due['id'] == 'rent:2026-10-01:2026-10-31' and due['due_at'] == '2026-10-05'



def test_authorized_cod_300_less_prior_80_has_remaining_220():
    result = run(src.collect_sources(HardBoundaryDB(shipping_data(payment={'method': 'cod'}, paid_amount='80')), 'owner', START, NOW))
    cod = result['orders'][0]['cod']
    assert (cod['gross'], cod['collected'], cod['outstanding']) == ('300', '80', '220')
    assert result['orders'][0]['cod_amount'] == '220'
