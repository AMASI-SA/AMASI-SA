"""A validated per-piece invoice replaces, rather than tops up, its estimate."""
from copy import deepcopy
from decimal import Decimal
import pytest
from test_operational_supplier_invoice_cutover import native_data, collect, active, NEW, NOW
from operational_balance_engine import reconcile


@pytest.mark.parametrize('currency', ['SAR', 'QAR', 'KWD', 'AED'])
def test_fully_invoiced_piece_has_no_leftover_estimate_in_any_customer_currency(currency):
    data = native_data(created=NEW, currency=currency)
    result = reconcile(active(), collect(data), NOW)
    assert sum(Decimal(row['confirmed']) for row in result['engine']['obligations'].values()
               if row.get('kind') == 'supplier') == Decimal('80')
    assert sum(Decimal(row['expected']) for row in result['engine']['obligations'].values()
               if row.get('kind') == 'supplier') == 0
    assert {row['currency'] for row in result['engine']['obligations'].values()
            if row.get('kind') == 'supplier' and Decimal(row['confirmed']) > 0} == {'SAR'}


def test_invoice_supersedes_estimate_but_does_not_rewrite_original_fact_payload():
    data = native_data(created=NEW)
    sources = collect(data)
    first = reconcile(active(), sources, NOW)
    before_facts = deepcopy(first['engine']['facts'])
    second = reconcile(first, sources, NOW)
    assert first == second
    assert second['engine']['facts'] == before_facts
    assert all('component_fully_invoiced' not in v for k,v in before_facts.items() if k.startswith('receipt:'))
    assert all(row['expected'] == '0.00' for row in second['engine']['obligations'].values()
               if row['kind'] == 'supplier')


def test_supplier_return_does_not_recreate_an_unreceived_estimate():
    data = native_data(created=NEW); sources = collect(data)
    state = reconcile(active(), sources, NOW)
    sources['supplier_returns'] = [{'id':'return-one', 'receipt_id':'invoice:p1:product',
                                  'amount':'10.00', 'accepted':True, 'accepted_at':NOW}]
    after = reconcile(state, sources, NOW)
    assert sum(Decimal(row['confirmed']) for row in after['engine']['obligations'].values()
               if row['kind'] == 'supplier') == Decimal('70')
    assert all(row['expected'] == '0.00' for row in after['engine']['obligations'].values()
               if row['kind'] == 'supplier')


def test_actual_invoice_above_estimate_is_not_missing_financial_evidence():
    data = native_data(created=NEW)
    data['mezan_product_cost_profiles_v2'][0]['base_cost'] = '30'
    state = reconcile(active(), collect(data), NOW)
    assert sum(Decimal(row['confirmed']) for row in state['engine']['obligations'].values()
               if row['kind'] == 'supplier') == Decimal('80')
    assert not any(row['code'] == 'supplier_receipt_exceeds_estimate' for row in state['engine']['issues'])


def test_uninvoiced_piece_remains_expected_without_invoiced_piece_residue():
    from test_operational_supplier_invoice_cutover import native_invoice
    data = native_data(created=NEW)
    data['mezan_supplier_invoices_v2'] = [native_invoice(piece_ids=('p1',))]
    state = reconcile(active(), collect(data), NOW)
    rows = state['engine']['obligations']
    assert rows['supplier:order-10:p1']['expected'] == '0.00'
    assert rows['supplier:order-10:p2']['expected'] == '50.00'
    assert sum(Decimal(r['confirmed']) for r in rows.values() if r['kind']=='supplier') == Decimal('40')


def test_replayed_final_invoice_marker_survives_missing_source_without_new_fact_fields():
    sources = collect(native_data(created=NEW))
    before = reconcile(active(), sources, NOW)
    sources['supplier_receipts'] = []
    after = reconcile(before, sources, NOW)
    assert after['engine']['facts'] == before['engine']['facts']
    assert all(row['expected']=='0.00' for row in after['engine']['obligations'].values()
               if row['kind']=='supplier')


def test_legacy_partial_receipt_without_full_piece_proof_keeps_remaining_estimate():
    sources = collect(native_data(created=NEW))
    for r in sources['supplier_receipts']: r.pop('component_fully_invoiced')
    state = reconcile(active(), sources, NOW)
    assert sum(Decimal(r['expected']) for r in state['engine']['obligations'].values()
               if r['kind']=='supplier') == Decimal('20')
