"""Shipping payables must not mask independent customer receipts."""
from copy import deepcopy

import pytest

from operational_provider_history import late_payment_blocks
from test_provider_late_payment_boundary import fixture, NOW


def with_shipping(provider='paypal', currency='SAR'):
    state, order, payment = fixture(provider, currency)
    charge = dict(id='shipping-prepaid:one', kind='shipping', party_type='courier',
                  party_id='aramex', currency='SAR', direction='payable', confirmed='34.50')
    state['engine']['obligations'] = {charge['id']: charge}
    state['movements'] = [dict(party_type='courier', party_id='aramex', currency='SAR',
        kind='payment', direction='outgoing', amount='34.50', actual_fee_amount='0',
        order_id='one', order_number=order['order_number'], occurred_at=NOW,
        allocations=[dict(obligation_id=charge['id'], amount='34.50')])]
    return state, order, payment


@pytest.mark.parametrize('provider', ['paypal', 'emkan', 'salla', 'tabby', 'tamara'])
@pytest.mark.parametrize('currency', ['SAR', 'KWD', 'QAR'])
def test_allocated_shipping_expense_does_not_hold_customer_payment(provider, currency):
    state, order, payment = with_shipping(provider, currency)
    before = deepcopy(state)
    assert not late_payment_blocks(state, order, payment, through=NOW)
    assert state == before


@pytest.mark.parametrize('invalid', ['incoming', 'refund', 'no_allocation', 'unknown_charge',
    'wrong_party', 'wrong_currency', 'receivable', 'provider_charge', 'partial', 'overpaid',
    'duplicate', 'bank_request', 'provider_allocation'])
def test_shipping_exception_does_not_hide_possible_customer_money(invalid):
    state, order, payment = with_shipping()
    movement = state['movements'][0]
    charge = state['engine']['obligations']['shipping-prepaid:one']
    if invalid == 'incoming': movement['direction'] = 'incoming'
    elif invalid == 'refund': movement['kind'] = 'refund'
    elif invalid == 'no_allocation': movement['allocations'] = []
    elif invalid == 'unknown_charge': state['engine']['obligations'] = {}
    elif invalid == 'wrong_party': charge['party_id'] = 'other'
    elif invalid == 'wrong_currency': charge['currency'] = 'KWD'
    elif invalid == 'receivable': charge['direction'] = 'receivable'
    elif invalid == 'provider_charge': charge['kind'] = 'provider'
    elif invalid == 'partial': movement['allocations'][0]['amount'] = '10.00'
    elif invalid == 'overpaid': charge['confirmed'] = '10.00'
    elif invalid == 'duplicate': movement['allocations'] *= 2
    elif invalid == 'bank_request': movement['request_id'] = 'order-bank:one'
    elif invalid == 'provider_allocation': movement['allocations'][0]['obligation_id'] = 'provider:one'
    assert late_payment_blocks(state, order, payment, through=NOW)


def test_shipping_expense_and_provider_receivable_survive_repeated_reconciliation():
    from operational_balance_engine import reconcile
    state, order, payment = with_shipping()
    payment['captures'] = [{'id': 'native-provider-receipt', 'amount': '100.00',
                            'captured_at': '2026-11-16T12:00:00+00:00'}]
    state.update(status='active', revision=1)
    before = deepcopy(state)
    result = reconcile(state, {'orders': [order]}, NOW)
    assert result['engine']['obligations']['provider:one']['confirmed'] == '100.00'
    assert result['movements'] == before['movements']
    assert result['openings'] == before['openings']
    assert reconcile(result, {'orders': [order]}, NOW) == result
    assert state == before
