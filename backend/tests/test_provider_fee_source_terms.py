"""Synthetic fee contracts exercise source selection without merchant pricing."""
from datetime import date
from decimal import Decimal

import pytest

from operational_fee_methods import fee_rail, order_fee_rail
from operational_provider_fees import provider_projection


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
