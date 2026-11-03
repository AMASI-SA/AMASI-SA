"""Supplier approval is the debt cutoff; the customer's order date is not.

Only synthetic native rows and the existing deny-write source fixture are used.
"""
from copy import deepcopy
from decimal import Decimal

import pytest

from backend.tests.test_operational_balance_sources import HardBoundaryDB, row, run
from operational_balance_engine import reconcile
from operational_balance_sources import collect_sources


START = '2026-11-01T00:30:01+03:00'
APPROVED = '2026-11-01T00:50:00+03:00'
NOW = '2026-11-01T01:00:00+03:00'
OLD = '2026-10-31T22:00:00+03:00'
NEW = '2026-11-01T00:40:00+03:00'


def native_order(number='10', *, created=OLD, currency='SAR'):
    return row(order_number=number, raw_by_source={'salla_direct': {
        'id': 'order-' + number, 'reference_id': number, 'created_at': created,
        'updated_at': created, 'status': 'in_progress', 'currency': currency,
        'payment': {'method': 'bank_transfer'},
        'total': {'amount': '300', 'currency': currency},
        'items': [{'id': 'line', 'product_id': 'product', 'quantity': 2}],
    }})


def native_invoice(*, identity='invoice', piece_ids=('p1', 'p2'), approved=APPROVED,
                   currency='SAR', unit=4000, tax=0):
    subtotal = unit * len(piece_ids)
    invoice = row(id=identity, invoice_number='SI-SYNTHETIC-' + identity,
        supplier_id='supplier', status='payable_posted', experiment_mode=False,
        approved_at=approved, currency=currency, subtotal_halalas=subtotal,
        total_halalas=subtotal + tax,
        lines=[dict(piece_ids=list(piece_ids), quantity=len(piece_ids),
                    product_price_authority='mezan_v2', product_unit_price_halalas=unit,
                    product_total_halalas=subtotal, total_halalas=subtotal)])
    if tax:
        invoice['purchase_tax'] = dict(treatment='INPUT_VAT', amount_halalas=tax,
            entity_id='tax', evidence_file_id='synthetic-evidence',
            evidence_sha256='a' * 64, confirmed=True)
    return invoice


def native_data(*, created=OLD, currency='SAR', with_cost=True):
    return {
        'unified_orders': [native_order(created=created, currency=currency)],
        'mezan_suppliers_v2': [row(id='supplier')],
        'mezan_product_cost_profiles_v2': [row(salla_product_id='product', base_cost='50')] if with_cost else [],
        'mezan_preparation_pieces_v1': [row(id=f'p{i}', piece_id=f'p{i}',
            order_number='10', order_item_id='salla:10:line', unit_index=i,
            supplier_id='supplier', supplier_dispatch_status='received') for i in (1, 2)],
        'mezan_supplier_invoices_v2': [native_invoice()],
    }


def collect(data):
    before = deepcopy(data)
    result = run(collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
    assert data == before
    return result


def active():
    return {'status': 'active', 'started_at': START, 'openings': {
        'opening-supplier': {'id': 'opening-supplier', 'party_type': 'supplier',
            'party_id': 'supplier', 'currency': 'SAR', 'direction': 'against_us',
            'name': 'Synthetic supplier', 'amount': '100.00'}}, 'movements': []}


def confirmed_rows(state):
    return {key: value for key, value in state['engine']['obligations'].items()
            if key.startswith('supplier:') and ':receipt:' in key}


def test_approved_new_invoice_on_old_order_is_confirmed_without_old_order_effects():
    sources = collect(native_data())
    assert sources['orders'] == []
    assert sum(Decimal(row['amount']) for row in sources['supplier_receipts']) == Decimal('80')
    initial = active()
    state = reconcile(initial, sources, NOW)
    rows = confirmed_rows(state)
    assert sum(Decimal(row['confirmed']) for row in rows.values()) == Decimal('80')
    assert set(state['engine']['obligations']) == set(rows)
    assert state['openings'] == initial['openings']
    assert state['movements'] == initial['movements']
    assert state['engine']['provider_reports'] == state['engine']['cod_reports'] == {}
    assert reconcile(state, sources, NOW) == state


def test_approved_invoice_does_not_require_current_cost_profile():
    sources = collect(native_data(created=NEW, with_cost=False))
    state = reconcile(active(), sources, NOW)
    assert sum(Decimal(row['confirmed']) for row in confirmed_rows(state).values()) == Decimal('80')


@pytest.mark.parametrize('patch', [
    {'approved_at': OLD}, {'approved_at': '2026-11-01T02:00:00+03:00'},
    {'approved_at': None}, {'approved_at': 'bad-date'}, {'status': 'draft'},
    {'status': 'experiment_completed'}, {'experiment_mode': True},
    {'experiment_run_id': 'synthetic-experiment'},
])
def test_only_real_approved_invoices_in_start_window_create_new_debt(patch):
    data = native_data()
    data['mezan_supplier_invoices_v2'][0].update(patch)
    sources = collect(data)
    assert sources['supplier_receipts'] == []
    assert confirmed_rows(reconcile(active(), sources, NOW)) == {}


def test_approval_at_start_is_inclusive_and_uses_riyadh_business_date():
    data = native_data()
    data['mezan_supplier_invoices_v2'][0]['approved_at'] = START
    state = reconcile(active(), collect(data), NOW)
    assert sum(Decimal(row['confirmed']) for row in confirmed_rows(state).values()) == Decimal('80')
    assert {row['business_date'] for row in confirmed_rows(state).values()} == {'2026-11-01'}


def test_one_invoice_spanning_old_and_new_orders_is_counted_once_without_old_estimates():
    data = native_data()
    data['unified_orders'].append(native_order('20', created=NEW))
    for order in data['unified_orders']:
        order['raw_by_source']['salla_direct']['items'][0]['quantity'] = 1
    data['mezan_preparation_pieces_v1'][1].update(order_number='20', order_item_id='salla:20:line', unit_index=1)
    sources = collect(data)
    assert [order['id'] for order in sources['orders']] == ['order-20']
    state = reconcile(active(), sources, NOW)
    rows = confirmed_rows(state)
    assert set(rows) == {
        'supplier:order-10:p1:receipt:invoice:p1:product',
        'supplier:order-20:p2:receipt:invoice:p2:product',
    }
    assert sum(Decimal(row['confirmed']) for row in rows.values()) == Decimal('80')
    estimates = {key: row for key, row in state['engine']['obligations'].items() if key not in rows}
    assert set(estimates) == {'supplier:order-20:p2'}
    assert estimates['supplier:order-20:p2']['expected'] == '0.00'  # Final invoice replaces the estimate.
    assert reconcile(state, sources, NOW) == state


def test_native_invoice_sar_and_tax_are_independent_from_gulf_order_currency():
    data = native_data(currency='QAR', with_cost=False)
    data['mezan_supplier_invoices_v2'] = [native_invoice(tax=1200)]
    sources = collect(data)
    state = reconcile(active(), sources, NOW)
    rows = list(confirmed_rows(state).values())
    assert {row['currency'] for row in rows} == {'SAR'}
    assert sum(Decimal(row['net_amount']) for row in rows) == Decimal('80')
    assert sum(Decimal(row['tax_amount']) for row in rows) == Decimal('12')
    assert sum(Decimal(row['confirmed']) for row in rows) == Decimal('92')
    assert {row['source_invoice_id'] for row in rows} == {'invoice'}
    assert {row['source_invoice_number'] for row in rows} == {'SI-SYNTHETIC-invoice'}
    assert all(row['source_invoice_approved_at'] == '2026-10-31T21:50:00+00:00' for row in rows)
    data['mezan_supplier_invoices_v2'][0]['purchase_tax']['confirmed'] = False
    assert collect(data)['supplier_receipts'] == []


def test_omitted_legacy_native_currency_uses_supplier_sar_contract_not_customer_currency():
    data = native_data(currency='AED')
    data['mezan_supplier_invoices_v2'][0].pop('currency')
    assert {row['currency'] for row in collect(data)['supplier_receipts']} == {'SAR'}


@pytest.mark.parametrize('currency', ['QAR', 'USD', '', None])
def test_explicit_conflicting_or_unknown_invoice_currency_is_not_converted(currency):
    data = native_data()
    data['mezan_supplier_invoices_v2'][0]['currency'] = currency
    sources = collect(data)
    assert sources['supplier_receipts'] == []
    assert any(row['code'] == 'supplier_invoice_currency_conflict' for row in sources['issues'])


@pytest.mark.parametrize('missing', ['canonical_source', 'order_date', 'piece', 'supplier'])
def test_missing_canonical_invoice_links_fail_closed_with_visible_issue(missing):
    data = native_data()
    if missing == 'canonical_source':
        data['unified_orders'][0].pop('raw_by_source')
    elif missing == 'order_date':
        data['unified_orders'][0]['raw_by_source']['salla_direct'].pop('created_at')
    elif missing == 'piece':
        data['mezan_preparation_pieces_v1'] = []
    else:
        data['mezan_suppliers_v2'] = []
    sources = collect(data)
    assert sources['supplier_receipts'] == []
    assert sources['issues']


@pytest.mark.parametrize('duplicate', ['order', 'piece', 'invoice'])
def test_ambiguous_canonical_identity_is_rejected(duplicate):
    data = native_data()
    collection = {'order': 'unified_orders', 'piece': 'mezan_preparation_pieces_v1',
                  'invoice': 'mezan_supplier_invoices_v2'}[duplicate]
    data[collection] += deepcopy(data[collection])
    sources = collect(data)
    assert sources['supplier_receipts'] == []
    assert sources['issues']


def test_prestart_invoice_cannot_be_charged_again_by_new_invoice():
    data = native_data()
    data['mezan_supplier_invoices_v2'].append(native_invoice(identity='opening-covered', approved=OLD))
    sources = collect(data)
    assert sources['supplier_receipts'] == []
    assert any(row['code'] == 'supplier_cross_invoice_component_coverage_ambiguous' for row in sources['issues'])
    state = reconcile(active(), sources, NOW)
    assert state['openings'] == active()['openings']
    assert confirmed_rows(state) == {}


def test_new_service_on_previously_invoiced_product_is_not_a_duplicate_product_charge():
    data = native_data()
    previous = native_invoice(identity='opening-covered', piece_ids=('p1',), approved=OLD)
    current = native_invoice(piece_ids=('p1',), unit=0)
    current.update(subtotal_halalas=1000, total_halalas=1000)
    current['lines'][0].update(product_charge_eligible=False, total_halalas=1000,
        services=[dict(service_id='embroidery', unit_price_halalas=1000, quantity_per_piece=1, total_halalas=1000)])
    data['mezan_supplier_invoices_v2'] = [previous, current]
    sources = collect(data)
    assert [row['id'] for row in sources['supplier_receipts']] == ['invoice:p1:service:embroidery']
    rows = confirmed_rows(reconcile(active(), sources, NOW))
    assert len(rows) == 1
    assert next(iter(rows.values()))['confirmed'] == '10.00'


def test_existing_frozen_fact_ids_payment_allocations_and_returns_survive_replay():
    from operational_balance_service import report

    sources = collect(native_data())
    state = reconcile(active(), sources, NOW)
    key = 'supplier:order-10:p1:receipt:invoice:p1:product'
    # Simulate already persisted pre-fix facts/obligations: provenance fields
    # were absent, but their financial fact payload and allocation IDs existed.
    for obligation in confirmed_rows(state).values():
        for field in ('source_invoice_id', 'source_invoice_number', 'source_invoice_approved_at'):
            obligation.pop(field)
    original_facts = deepcopy(state['engine']['facts'])
    state['openings']['opening-bank'] = dict(id='opening-bank', party_type='bank', party_id='bank',
        currency='SAR', direction='for_us', name='Synthetic bank', amount='1000.00')
    state['movements'] = [dict(id='payment', party_type='supplier', party_id='supplier', currency='SAR',
        direction='outgoing', kind='payment', amount='25.00', bank_kind='bank', bank_id='bank',
        actual_fee_amount='0.00', allocations=[dict(obligation_id=key, amount='25.00')])]
    sources['supplier_returns'] = [dict(id='accepted-return', receipt_id='invoice:p1:product',
        amount='30.00', accepted=True, accepted_at=NOW)]
    result = reconcile(state, sources, NOW)
    assert {key: value for key, value in result['engine']['facts'].items() if key.startswith('receipt:')} == original_facts
    assert result['openings'] == state['openings']
    assert result['movements'] == state['movements']
    assert result['engine']['obligations'][key]['confirmed'] == '10.00'
    assert result['engine']['obligations'][key]['source_invoice_id'] == 'invoice'
    assert result['engine']['obligations']['credit:' + key]['confirmed'] == '15.00'
    summary = report(result)
    bank = next(row for row in summary['parties'] if row['party_type'] == 'bank')
    assert bank['actual'] == '975.00'
    assert reconcile(result, sources, NOW) == result
    # A subsequent missing/stale source cannot erase the accepted obligation,
    # payment, or return. No new source read is required to preserve known facts.
    reread = reconcile(result, {}, NOW)
    assert reread['engine']['obligations'] == result['engine']['obligations']
    assert reread['movements'] == result['movements']


def test_changed_confirmed_invoice_is_flagged_without_rewriting_its_money():
    data = native_data()
    state = reconcile(active(), collect(data), NOW)
    frozen = deepcopy(state['engine']['facts'])
    data['mezan_supplier_invoices_v2'] = [native_invoice(unit=5000)]
    changed = reconcile(state, collect(data), NOW)
    assert changed['engine']['facts'] == frozen
    assert sum(Decimal(row['confirmed']) for row in confirmed_rows(changed).values()) == Decimal('80')
    assert any(row['code'] == 'confirmed_evidence_changed' for row in changed['engine']['issues'])


def test_foreign_owner_invoice_is_never_read_even_with_same_identity():
    data = native_data()
    foreign = native_invoice(unit=500000)
    foreign['user_id'] = 'another-owner'
    data['mezan_supplier_invoices_v2'].append(foreign)
    state = reconcile(active(), collect(data), NOW)
    assert sum(Decimal(row['confirmed']) for row in confirmed_rows(state).values()) == Decimal('80')
