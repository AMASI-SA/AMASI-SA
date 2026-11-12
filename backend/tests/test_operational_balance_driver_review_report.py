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


def covered_delivery(method, issues=()):
    from operational_financial_position_core import _deliveries
    state, _ = recorded(method)
    details = {'facts': {
        'shipping:order': {'id': 'driver', 'party_type': 'store_driver', 'delivered_at': '2026-10-06T09:00:00+00:00'},
        'cod_collection:order': {'party_id': 'driver', 'evidence': {
            'evidence_complete': True, 'collection_confirmed': False}},
        }, 'cod_reports': {'cod:order': {
            'order_id': 'order', 'party_type': 'store_driver', 'party_id': 'driver', 'currency': 'SAR',
            'payment_method': method, 'collection_amount': '200.00', 'confirmed_custody': '0.00',
            'settled': '0.00', 'outstanding': '0.00', 'customer_outstanding': '0.00'}}}
    fees = [{'id': 'shipping:order', 'kind': 'shipping', 'party_type': 'store_driver', 'party_id': 'driver',
             'confirmed': '20.00', 'settled': '0.00', 'outstanding': '20.00'}]
    return state, _deliveries(fees, details, 'SAR', {('store_driver', 'driver'): 'driver'}, issues)


@pytest.mark.parametrize('method', ['bank_transfer', 'card_terminal'])
def test_sealed_noncash_effect_resolves_only_its_destination_and_confirmation_gaps(method):
    state, delivery = covered_delivery(method)
    before = deepcopy((state, delivery))
    assert set(delivery['rows'][0]['incomplete_reasons']) == {'collection_destination_unresolved', 'collection_unconfirmed'}
    result, issues = link_review_destinations(state, delivery)
    assert not issues and result['complete'] is True
    assert result['rows'][0]['complete'] is True and result['rows'][0]['incomplete_reasons'] == []
    assert result['rows'][0]['shipping_confirmed'] == result['rows'][0]['shipping_outstanding'] == '20.00'
    assert result['rows'][0]['destination_outstanding'] == ('0.00' if method == 'bank_transfer' else '200.00')
    assert (state, delivery) == before


@pytest.mark.parametrize('gap', ['fee', 'global', 'other_order'])
def test_noncash_approval_does_not_erase_an_independent_coverage_gap(gap):
    source_issues = ([{'code': 'shipping_fee_incomplete', 'source_id': 'shipping:order'}] if gap == 'fee' else
                     [{'code': 'operational_capacity_requires_archive'}] if gap == 'global' else [])
    state, delivery = covered_delivery('bank_transfer', source_issues)
    if gap == 'other_order':
        delivery['rows'].append({**deepcopy(delivery['rows'][0]), 'order_id': 'other'})
    result, issues = link_review_destinations(state, delivery)
    assert not issues and result['complete'] is False
    assert result['rows'][0]['collection_status'] == 'confirmed'
    if gap == 'fee':
        assert result['rows'][0]['incomplete_reasons'] == ['shipping_fee_incomplete']
        assert result['rows'][0]['complete'] is False
    elif gap == 'global':
        assert result['coverage_issues'] == ['operational_capacity_requires_archive']
        assert result['rows'][0]['complete'] is True
    else:
        assert result['rows'][1]['complete'] is False
        assert result['rows'][1]['collection_status'] == 'pending_review'


def test_conflicting_review_effect_invalidates_forecast_position_but_keeps_future_components():
    from operational_financial_position import build_financial_position
    from operational_financial_position_core import build_financial_position as project
    from test_operational_balance_financial_position import state_for, opening, NOW, START

    state = state_for()
    opening(state, 'bank', 'bank', '100.00')
    state['driver_payment_review_facts'] = {'damaged': {'id': 'damaged'}}
    exposure = {'status': 'preview', 'as_of': NOW, 'engine_as_of': NOW, 'started_at': START,
                'complete': True, 'issues': [], 'currencies': {'SAR': {}}, 'groups': [],
                'prestart_open_cod': {'by_currency': {}, 'withheld_candidates': 0},
                'open_customer_cod': {'by_currency': {}, 'withheld_candidates': 0}}
    unlinked = project(state, cost_exposure=exposure)['currencies']['SAR']
    assert unlinked['metrics']['forecast_monetary_position'] == '100.00'
    result = build_financial_position(state, cost_exposure=exposure)['currencies']['SAR']
    assert result['completeness']['actual'] is result['completeness']['forecast'] is False
    assert result['metrics']['forecast_monetary_position'] is None
    forecast = next(row for row in result['forecast_rows'] if row['id'] == 'forecast_monetary_position')
    assert forecast['amount'] is None and forecast['complete'] is False
    assert result['metrics']['forecast_change'] == '0.00'
    assert result['metrics']['liquidity'] == '100.00'
