"""Do not hide valid product totals behind an unrelated carrier warning."""
import pytest
from operational_cost_completeness import cost_completeness, SCOPES
from operational_financial_position_core import _cost_table


@pytest.mark.parametrize('code', ['shipping_identity_or_rate_incomplete', 'current_shipping_identity_or_rate_incomplete',
    'shipping_setup_ambiguous', 'historical_shipping_quote_incomplete', 'historical_shipping_unassigned'])
def test_shipping_only_evidence_gap_does_not_mask_products(code):
    out=cost_completeness({code: 1})
    assert out['products_unreceived'] and out['products_received_uninvoiced']
    assert not out['shipping_expected']


@pytest.mark.parametrize('code',['bundle_snapshot_missing','current_cost_snapshot_line_changed',
    'mz2_product_cost_incomplete','historical_supplier_cost_incomplete','supplier_invoice_identity_ambiguous'])
def test_product_gap_stays_visible_without_blocking_valid_shipping(code):
    out=cost_completeness({code:1})
    assert not out['products_unreceived'] and not out['products_received_uninvoiced']
    assert out['shipping_expected']


def test_native_received_matching_does_not_erase_proven_remaining_quantity():
    out=cost_completeness({'current_bundle_received_purchase_financial_matching_required':1})
    assert out['products_unreceived'] and not out['products_received_uninvoiced']


@pytest.mark.parametrize('code',['canonical_salla_source_missing_for_open_cost_preview','order_created_at_incomplete',
    'new_unknown_evidence_gap','current_cost_snapshot_time_incomplete'])
def test_unknown_or_global_gap_does_not_become_complete_zero(code):
    assert not any(cost_completeness({code:1})[scope] for scope in SCOPES)


def test_scope_declaration_requires_explicit_shipping_component_from_canonical_adapter():
    out=cost_completeness({'current_native_contract_uncovered':1}, {'current_native_contract_uncovered':{'shipping_expected'}})
    assert out['products_unreceived'] and not out['shipping_expected']


def test_scoped_completeness_does_not_waive_sum_or_invalid_money_contract():
    exposure={'status':'preview','complete':False,'bucket_completeness':cost_completeness({'historical_shipping_unassigned':1}),
        'currencies':{'SAR':{'prestart_supplier_unreceived':'220.00'}},
        'groups':[{'period':'prestart','kind':'supplier','stage':'under_fulfillment','currency':'SAR','amount':'200.00'}]}
    out=_cost_table(exposure,'SAR','products_unreceived','products',('prestart_supplier_unreceived',),lambda r:True)
    assert not out['complete'] and out['totals']['amount'] is None
    exposure['groups'][0]['amount']='NaN'
    out=_cost_table(exposure,'SAR','products_unreceived','products',('prestart_supplier_unreceived',),lambda r:True)
    assert not out['complete'] and out['totals']['amount'] is None


def test_explicit_native_shipping_component_gap_reaches_the_scoped_report():
    from test_operational_open_cost_exposure import seeded_db, operating_state, preview
    state=operating_state()
    state['engine']['issues']=[{'code':'cod_amount_not_covered_by_contract_needs_review','component':'shipping_fee'}]
    result=preview(seeded_db(),state)
    scoped=result['bucket_completeness']
    assert scoped['products_unreceived'] and not scoped['shipping_expected']
    assert any(i['code']=='current_cod_amount_not_covered_by_contract_needs_review' for i in result['issues'])
    assert not result['complete'], 'The warning itself must remain visible'
