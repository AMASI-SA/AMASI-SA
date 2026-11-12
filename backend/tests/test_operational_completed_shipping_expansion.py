"""Completed courier work is earned without creating delivery or COD evidence."""
from copy import deepcopy

import pytest

from test_operational_balance_prepaid_shipping import fixture, calculate, COMPLETED
from test_operational_balance_delivery_cutover import START, NOW


def without_label(data, missing=('shipment_id', 'tracking_number')):
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    for key in missing:
        source['salla_shipping_current'].pop(key, None)
        raw['shipping'].pop(key, None)
    for key in ('salla_shipment_id', 'tracking_number', 'shipping_number'):
        source.pop(key, None)
    raw['shipments'] = []


def admitted(state, at=START):
    state.setdefault('engine', {})['completed_shipping_admission'] = {
        'version': 1, 'owner_id': state['owner_id'], 'started_at': state['started_at'], 'activated_at': at,
    }
    return state


@pytest.mark.parametrize('missing', [('shipment_id',), ('tracking_number',), ('shipment_id', 'tracking_number')])
@pytest.mark.parametrize('old', [True, False])
def test_new_completed_prepaid_work_does_not_require_a_label(old, missing):
    data, state = fixture(old=old)
    without_label(data, missing)
    admitted(state)
    result = calculate(data, state)
    assert result['engine']['obligations']['shipping-prepaid:100']['confirmed'] == '34.50'
    assert len(result['movements']) == 1
    assert result['movements'][0]['amount'] == '34.50'
    assert not any(key.startswith(('cod:', 'delivery_event:')) for key in result['engine']['facts'])
    assert not any(key.startswith('cod:') for key in result['engine']['obligations'])
    assert calculate(data, result)['movements'] == result['movements']


@pytest.mark.parametrize('marker', [None, NOW])
def test_historical_newly_eligible_prepaid_work_requires_review(marker):
    data, state = fixture()
    without_label(data)
    if marker:
        admitted(state, marker)
    before = deepcopy(state)
    result = calculate(data, state)
    assert state == before
    assert not result['movements']
    assert 'shipping-prepaid:100' not in result['engine']['obligations']
    assert any(row['code'] == 'operational_shipping_completion_history_review_required'
               for row in result['engine']['issues'])


def test_label_backed_prepaid_contract_retains_existing_recognition():
    data, state = fixture()
    result = calculate(data, state)
    assert result['engine']['obligations']['shipping-prepaid:100']['confirmed'] == '34.50'
    assert result['movements'][0]['occurred_at'] == COMPLETED


def test_later_status_keeps_label_free_typed_history_available_to_sources():
    from test_operational_balance_courier_order_transition import history, COLLECTION
    data, state = fixture(current='shipping', at=None)
    without_label(data)
    admitted(state)
    data[COLLECTION] = [history(COMPLETED, to_status='completed')]
    result = calculate(data, state)
    assert result['engine']['obligations']['shipping-prepaid:100']['confirmed'] == '34.50'
    assert result['movements'][0]['occurred_at'] == COMPLETED
