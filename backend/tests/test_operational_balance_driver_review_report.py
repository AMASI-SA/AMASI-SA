"""Reporting validates exact monetary effects, never calculates new receipts."""
from copy import deepcopy
import pytest
from operational_balance_store import digest
from operational_driver_review_report import link_review_destinations, validated_review_facts


def recorded(method='card_terminal'):
    key = digest(['owner', 'operational-driver-review', 'review', 1])
    target = 'bank' if method == 'bank_transfer' else 'pos_receivable'
    fact = {'id': key, 'financial_source': 'operational', 'review_id': 'review', 'review_revision': 1,
        'order_id': 'order', 'driver_id': 'driver', 'payment_method': method, 'amount': '200.00',
        'currency': 'SAR', 'destination_id': 'destination', 'destination_type': target,
        'destination_name': 'Synthetic receiving account', 'receipt_hash': 'a' * 64,
        'approved_at': '2026-10-06T14:00:00+03:00', 'movement_id': 'movement' if target == 'bank' else None,
        'obligation_id': 'obligation' if target != 'bank' else None}
    fact['seal'] = digest(fact)
    state = {'owner_id': 'owner', 'driver_payment_review_facts': {key: fact}, 'movements': [], 'engine': {'obligations': {}}}
    if target == 'bank':
        state['movements'] = [{'id': 'movement', 'driver_review_event_id': key, 'direction': 'incoming', 'bank_id': 'destination',
            'party_id': 'destination', 'party_type': 'bank', 'bank_kind': 'bank', 'allocations': [], 'amount': '200.00',
            'currency': 'SAR', 'order_id': 'order', 'receipt_hash': 'a' * 64}]
    else:
        state['engine']['obligations'] = {'obligation': {'party_type': 'pos_receivable', 'driver_review_event_id': key,
            'direction': 'receivable', 'party_id': 'destination', 'confirmed': '200.00', 'currency': 'SAR', 'order_id': 'order'}}
    deliveries = {'complete': False, 'notes': [], 'rows': [{'order_id': 'order', 'party_id': 'driver', 'party_type': 'store_driver',
        'shipping_confirmed': '20.00', 'shipping_outstanding': '20.00', 'collection_status': 'pending_review'}]}
    return state, deliveries


@pytest.mark.parametrize('method', ['bank_transfer', 'card_terminal'])
def test_confirmed_destination_requires_sealed_effect_and_is_presentation_only(method):
    state, delivery = recorded(method); before = deepcopy(state)
    result, issues = link_review_destinations(state, delivery)
    assert not issues and state == before
    assert result['rows'][0]['collection_status'] == 'confirmed'
    assert result['rows'][0]['driver_custody'] == '0.00'
    assert result['rows'][0]['shipping_confirmed'] == '20.00'
    assert result['rows'][0]['destination_name'] == 'Synthetic receiving account'
    assert delivery['rows'][0]['collection_status'] == 'pending_review'


@pytest.mark.parametrize('method', ['bank_transfer', 'card_terminal'])
@pytest.mark.parametrize('case', ['missing_effect', 'changed_amount', 'changed_owner', 'changed_seal', 'bad_amount'])
def test_tampered_or_missing_effect_cannot_be_shown_as_confirmed(method, case):
    state, delivery = recorded(method)
    fact = next(iter(state['driver_payment_review_facts'].values()))
    if case == 'missing_effect': state['movements'] = []; state['engine']['obligations'] = {}
    elif case == 'changed_amount':
        if method == 'bank_transfer': state['movements'][0]['amount'] = '200.01'
        else: state['engine']['obligations']['obligation']['confirmed'] = '200.01'
    elif case == 'changed_owner': state['owner_id'] = 'other'
    elif case == 'changed_seal': fact['destination_name'] = 'Other'
    elif case == 'bad_amount': fact['amount'] = None
    result, issues = link_review_destinations(state, delivery)
    assert issues and not result['complete']
    assert result['rows'][0]['collection_status'] == 'pending_review'


def test_partial_pos_settlement_shows_remainder_without_a_second_driver_debt():
    state, delivery = recorded()
    state['movements'] = [{'allocations': [{'obligation_id': 'obligation', 'amount': '150.00'}]}]
    result, issues = link_review_destinations(state, delivery)
    assert not issues
    assert result['rows'][0]['destination_outstanding'] == '50.00'
    assert result['rows'][0]['collection_outstanding'] == '0.00'


def test_fee_snapshot_not_yet_available_is_explicit_not_invented():
    state, _ = recorded()
    result, issues = link_review_destinations(state, {'complete': True, 'notes': [], 'rows': []})
    assert not result['rows'] and not result['complete']
    assert issues[0]['code'] == 'operational_driver_review_awaiting_delivery_snapshot'
