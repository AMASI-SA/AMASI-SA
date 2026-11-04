"""Synthetic fee contracts exercise source selection without merchant pricing."""
from datetime import date
from copy import deepcopy

import pytest

from operational_fee_methods import fee_rail, order_fee_rail
from operational_provider_fees import fee_policy_preview, provider_projection
from operational_configured_provider_fees import estimate_fee, refresh_provider_fee_inputs
from provider_fee_sources import bind_statement_plan, statement_plan_index


def policy(**changes):
    result = {
        'id': 'synthetic-policy', 'provider': 'tabby', 'currency': 'SAR',
        'status': 'active', 'effective_from': '2026-01-01',
        'fee_model_version': 2, 'plan_id': 'synthetic-long-product',
        'confirmed_by': 'synthetic-owner', 'confirmed_at': '2026-01-01T00:00:00Z',
        'evidence': 'synthetic contract',
        'components': [{
            'id': 'fixed', 'percentage': '0', 'fixed_amount': '2.50',
            'vat_treatment': 'exclusive', 'vat_rate': '17',
            'vat_rounding': 'half_even',
        }],
    }
    return {**result, **changes}


def payment(**changes):
    result = {
        'provider': 'tabby', 'currency': 'SAR', 'gross': '40.00',
        'cancelled': '0', 'plan_id': 'synthetic-long-product', 'refunds': [],
        'captures': [{'id': 'capture', 'amount': '40.00', 'captured_at': '2026-06-10T12:00:00Z'}],
    }
    return {**result, **changes}


def project(payment_value=None, policy_value=None):
    return provider_projection(payment_value or payment(), [policy_value or policy()], date(2026, 6, 10))


def test_capture_only_contract_does_not_invent_refund_or_cancellation_terms():
    result = project()
    assert result['estimated_fee_net'] == '2.50'
    assert result['estimated_fee_tax'] == '0.42'
    assert result['expected_receivable'] == '37.08'
    assert result['fee_components'][0]['refund_fee_treatment'] is None
    assert result['fee_components'][0]['cancellation_fee_treatment'] is None


def test_component_vat_rounding_default_remains_half_up():
    terms = policy()
    terms['components'][0].pop('vat_rounding')
    assert project(policy_value=terms)['estimated_fee_tax'] == '0.43'


def test_executed_refund_requires_explicit_component_treatment():
    refund = {'id': 'refund', 'amount': '5.00', 'status': 'executed'}
    with pytest.raises(ValueError, match='refund_fee_treatment_incomplete'):
        project(payment(refunds=[refund]))


def test_executed_cancellation_requires_explicit_component_treatment():
    cancel = {'id': 'cancel', 'amount': '40.00', 'status': 'executed', 'stage': 'before_capture'}
    with pytest.raises(ValueError, match='cancellation_fee_treatment_incomplete'):
        project(payment(captures=[], cancelled='40.00', cancellations=[cancel]))


@pytest.mark.parametrize('network', ['mada', 'visa', 'mastercard'])
def test_apple_pay_uses_the_evidenced_network(network):
    raw = {'payment_method': 'applepay', 'payment_methods': [{'payment_method': network}]}
    assert order_fee_rail(raw) == network
    assert fee_rail({'code': 'applepay', 'slug': network}) == network


def test_wallet_wrapper_does_not_resolve_two_conflicting_networks():
    raw = {'payment_method': 'applepay', 'payment_methods': [{'payment_method': 'mada'}, {'payment_method': 'visa'}]}
    assert order_fee_rail(raw) is None


def test_wallet_without_network_preserves_its_explicit_legacy_estimate_key():
    assert order_fee_rail({'payment_method': 'applepay'}) == 'apple_pay'


def test_unrounded_tax_basis_preserves_saved_salla_per_order_rounding():
    component = {'id': 'legacy-salla', 'percentage': '1.3', 'fixed_amount': '0.80',
                 'vat_treatment': 'exclusive', 'vat_rate': '17', 'vat_basis': 'unrounded_fee'}
    terms = policy(provider='salla', plan_id=None, payment_method='mada', components=[component])
    source = payment(provider='salla', plan_id=None, payment_method='mada', gross='237.53', captures=[])
    result = project(source, terms)
    assert (result['estimated_fee_net'], result['estimated_fee_tax'], result['estimated_fees']) == ('3.89', '0.66', '4.55')
    # Choose a boundary where rounding the taxable fee first changes the VAT.
    source['gross'] = '231.98'
    component['vat_rate'] = '19'
    raw = project(source, terms)
    component['vat_basis'] = 'rounded_fee'
    rounded = project(source, terms)
    assert raw['estimated_fee_tax'] == '0.72'
    assert rounded['estimated_fee_tax'] == '0.73'


def test_capture_selection_ignores_payout_rows():
    capture = policy()
    payout = policy(id='synthetic-payout', event_type='payout')
    assert provider_projection(payment(), [capture, payout], date(2026, 6, 10))['policy_id'] == capture['id']


def test_unknown_or_missing_product_never_falls_back_to_generic_rate():
    specific, generic = policy(), policy(id='generic', plan_id=None)
    for plan in (None, 'another-product'):
        with pytest.raises(ValueError):
            provider_projection(payment(plan_id=plan), [specific, generic], date(2026, 6, 10))


def test_plan_requirement_stays_with_its_card_network_scope():
    mada = policy(id='mada-policy', provider='salla', payment_method='mada', plan_id=None)
    visa = policy(id='visa-policy', provider='salla', payment_method='visa', plan_id='visa-product')
    source = payment(provider='salla', payment_method='mada', plan_id='other-product')
    result = provider_projection(source, [mada, visa], date(2026, 6, 10))
    assert result['policy_id'] == 'mada-policy'
    with pytest.raises(ValueError, match='provider_fee_policy_incomplete_or_ambiguous'):
        provider_projection({**source, 'payment_method': 'visa'}, [mada, visa], date(2026, 6, 10))


def statement_fixture():
    entry = {'id': 'entry-a', 'file_id': 'file-a', 'user_id': 'owner', 'provider': 'tabby',
             'order_number': 'order-a', 'raw_product_type': 'synthetic-long-product'}
    document = {'id': 'file-a', 'user_id': 'owner', 'provider': 'tabby',
                'filename': 'example-statement.xlsx', 'file_hash': 'synthetic-content-hash'}
    return entry, document


def test_statement_product_requires_same_owner_provider_and_file():
    entry, document = statement_fixture()
    index = statement_plan_index([entry], [document], 'owner')
    source = payment(plan_id=None)
    before = deepcopy(source)
    bound = bind_statement_plan(source, index, 'order-a')
    assert bound['plan_id'] == 'synthetic-long-product'
    assert bound['plan_evidence']['entry_ids'] == ['entry-a']
    assert source == before
    for changed in ({'user_id': 'other'}, {'provider': 'tamara'}, {'file_hash': ''},
                    {'filename': 'SYN-P01-test.xlsx'}):
        assert statement_plan_index([entry], [{**document, **changed}], 'owner') == {}


def test_conflicting_statement_product_keeps_paid_amount_and_blocks_pricing():
    entry, document = statement_fixture()
    index = statement_plan_index([entry, {**entry, 'id': 'entry-b', 'raw_product_type': 'other-product'}],
                                 [document], 'owner')
    bound = bind_statement_plan(payment(), index, 'order-a')
    assert bound['gross'] == '40.00'
    assert bound['fee_plan_issue'] == 'provider_payment_plan_conflict'
    with pytest.raises(ValueError, match='provider_payment_plan_conflict'):
        project(bound)


def test_operational_estimate_uses_saved_product_policy_before_legacy_flat_rate():
    settings = {'payment_methods': [{'name': 'تابي', 'commission_percent': '3', 'fixed_fee': '8', 'vat_percent': '5'}]}
    kwargs = {'policies': [policy()], 'payment': payment(), 'created_at': '2026-06-10T12:00:00Z'}
    assert str(estimate_fee(settings, 'tabby', '40.00', **kwargs)) == '2.92'
    kwargs['payment'] = payment(plan_id=None)
    assert estimate_fee(settings, 'tabby', '40.00', **kwargs) is None


def test_operational_inputs_retain_active_policies_for_the_balance_report():
    state = {'engine': {}}
    refresh_provider_fee_inputs(state, {'orders': [], 'provider_fee_settings': {}, 'fee_policies': [policy()]})
    assert state['engine']['provider_fee_inputs']['policies'] == [policy()]


def test_central_mode_does_not_fall_back_when_the_provider_has_no_policy():
    settings = {'payment_methods': [{'name': 'تابي', 'commission_percent': '3', 'fixed_fee': '8', 'vat_percent': '5'}]}
    assert estimate_fee(settings, 'tabby', '40.00', source_mode='mz2_policies') is None


def test_refund_vat_rounds_per_unique_event_with_an_original_fee_cap():
    component = {'id': 'refundable', 'percentage': '5', 'fixed_amount': '0',
                 'vat_treatment': 'exclusive', 'vat_rate': '17', 'vat_rounding': 'half_even',
                 'refund_fee_treatment': 'recalculate'}
    terms = policy(components=[component])
    refunds = [{'id': 'refund-a', 'amount': '10.00', 'status': 'executed'},
               {'id': 'refund-b', 'amount': '10.00', 'status': 'executed'}]
    result = project(payment(refunds=refunds), terms)
    assert result['refunded_fee_net'] == '1.00'
    assert result['refunded_fee_tax'] == '0.16'
    assert result == project(payment(refunds=[*refunds, refunds[0]]), terms)
    full = project(payment(refunds=[*refunds, {'id': 'refund-c', 'amount': '20.00', 'status': 'settled'}]), terms)
    assert (full['estimated_fee_net'], full['estimated_fee_tax'], full['estimated_fees']) == ('0.00', '0.00', '0.00')


def test_policy_estimate_requires_refund_identities_for_refundable_components():
    terms = policy()
    terms['components'][0]['refund_fee_treatment'] = 'recalculate'
    assert estimate_fee({}, 'tabby', '40.00', refunded='10.00', policies=[terms], payment=payment()) is None


@pytest.mark.parametrize('event', ['capture', 'refund', 'payout', 'cancellation'])
def test_saved_policy_preview_is_calculation_only_for_each_event_type(event):
    terms = policy(event_type=event)
    before = deepcopy(terms)
    result = fee_policy_preview(terms, '40.00', '2026-06-10')
    assert result['preview'] is True
    assert result['event_type'] == event
    assert (result['fee_net'], result['fee_vat'], result['fee_total']) == ('2.50', '0.42', '2.92')
    assert terms == before


def test_refund_processing_fee_is_per_executed_identity_and_separate_from_capture_credit():
    capture = policy()
    capture['components'][0]['refund_fee_treatment'] = 'retain'
    processing = policy(id='processing', event_type='refund')
    processing['components'][0]['fixed_amount'] = '0.50'
    events = [{'id': 'refund-a', 'amount': '10.00', 'status': 'executed', 'refunded_at': '2026-06-11T12:00:00Z'},
              {'id': 'refund-b', 'amount': '10.00', 'status': 'settled', 'refunded_at': '2026-06-12T12:00:00Z'}]
    source = payment(refunds=[*events, events[0]])
    result = provider_projection(source, [capture, processing], date(2026, 6, 10), as_of_date=date(2026, 6, 13))
    assert (result['estimated_fee_net'], result['estimated_fee_tax']) == ('3.50', '0.58')
    assert result['expected_receivable'] == '15.92'
    assert len(result['refund_processing_fees']) == 2
    assert result['refunded_fee_net'] == '0.00'
    pending = payment(refunds=[{**events[0], 'status': 'requested'}])
    assert 'refund_processing_fees' not in provider_projection(pending, [capture, processing], date(2026, 6, 10))


def test_refund_processing_policy_uses_riyadh_event_day_and_unknown_date_blocks_a_rate_change():
    capture = policy()
    capture['components'][0]['refund_fee_treatment'] = 'retain'
    old = policy(id='old-refund', event_type='refund', effective_to='2026-06-11')
    new = policy(id='new-refund', event_type='refund', effective_from='2026-06-12')
    new['components'][0]['fixed_amount'] = '0.80'
    event = {'id': 'refund', 'amount': '5.00', 'status': 'executed', 'refunded_at': '2026-06-11T22:30:00Z'}
    result = provider_projection(payment(refunds=[event]), [capture, old, new], date(2026, 6, 10), as_of_date=date(2026, 6, 13))
    assert result['refund_processing_fees'][0]['policy_id'] == 'new-refund'
    event.pop('refunded_at')
    with pytest.raises(ValueError, match='provider_fee_transaction_date_required'):
        provider_projection(payment(refunds=[event]), [capture, old, new], date(2026, 6, 10), as_of_date=date(2026, 6, 13))


def test_settled_actual_fee_covers_refund_processing_estimates_once():
    from test_operational_balance_configured_provider_fees import paid_state, platform
    state, order = paid_state('tabby', '40.00', '20.00')
    capture = policy(plan_id=None)
    capture['components'][0]['refund_fee_treatment'] = 'retain'
    processing = policy(id='processing', event_type='refund', plan_id=None)
    order['payment']['refunds'] = [{'id': 'refund', 'amount': '20.00', 'status': 'executed', 'refunded_at': order['updated_at']}]
    refresh_provider_fee_inputs(state, {'orders': [order], 'provider_fee_settings': {},
        'fee_policies': [capture, processing], 'provider_fee_source_mode': 'mz2_policies'})
    state['movements'].append({'id': 'payout', 'kind': 'settlement', 'party_type': 'provider', 'party_id': 'tabby',
        'direction': 'incoming', 'currency': 'SAR', 'amount': '39.00', 'actual_fee_amount': '1.00',
        'allocations': [{'obligation_id': 'provider:one', 'amount': '40.00'}]})
    assert platform(state, 'tabby')['fees'] == '1.00'
