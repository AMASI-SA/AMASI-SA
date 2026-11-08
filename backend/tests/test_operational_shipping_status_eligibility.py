"""Shipping earns at the dated order status; a logistics label is not required."""
from copy import deepcopy
import pytest
from test_operational_balance_prepaid_shipping import fixture, calculate
from test_operational_balance_courier_order_transition import fixture as deferred_fixture
from test_operational_balance_verified_shipping_fees import calculated
from test_operational_balance_delivery_cutover import DELIVERED, fee_total


def strip_labels(data):
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    current = source['salla_shipping_current']
    for field in ('shipment_id', 'tracking_number'):
        current.pop(field, None)
        raw.get('shipping', {}).pop(field, None)
    raw.pop('shipments', None)
    return source, raw, current


@pytest.mark.parametrize('old', [True, False])
def test_prepaid_completed_status_and_actual_date_charge_once_without_label(old):
    data, state = fixture(old=old)
    source, raw, current = strip_labels(data)
    before = deepcopy(data)
    first = calculate(data, state)
    assert data == before
    assert len(first['movements']) == 1
    assert first['movements'][0]['amount'] == '34.50'
    assert first['engine']['obligations']['shipping-prepaid:100']['confirmed'] == '34.50'
    assert calculate(data, first) == first
    raw['status'] = 'delivered'
    source['order_status_slug'] = 'delivered'
    current.update(shipment_id='later-label', tracking_number='later-tracking')
    later = calculate(data, first)
    assert later['movements'] == first['movements']
    assert later['engine']['obligations']['shipping-prepaid:100'] == first['engine']['obligations']['shipping-prepaid:100']
    assert fee_total(later) == fee_total(first)


@pytest.mark.parametrize('old', [True, False])
def test_deferred_delivered_status_and_actual_date_earn_once_without_label(old):
    data, _, raw, _ = deferred_fixture(old)
    raw['status_changed_at'] = DELIVERED
    source, raw, current = strip_labels(data)
    first = calculated(data)
    assert fee_total(first) == 10
    assert not first['movements']
    assert 'cod:100' not in first['engine']['obligations']
    assert calculated(data, first) == first
    current.update(shipment_id='later-label', tracking_number='later-tracking')
    assert calculated(data, first) == first


@pytest.mark.parametrize('kind', ['prepaid', 'deferred'])
@pytest.mark.parametrize('bad_date', [None, '2026-10-04T23:59:59+00:00', '2026-12-01T00:00:00+00:00'])
def test_status_without_eligible_actual_date_cannot_earn(kind, bad_date):
    if kind == 'prepaid':
        data, state = fixture()
    else:
        data, _, _, _ = deferred_fixture()
    _, raw, _ = strip_labels(data)
    raw.pop('status_changed_at', None)
    if bad_date:
        raw['status_changed_at'] = bad_date
    result = calculate(data, state) if kind == 'prepaid' else calculated(data)
    assert fee_total(result) == 0
    assert not result['movements']


@pytest.mark.parametrize('kind', ['prepaid', 'deferred'])
def test_canonical_order_carrier_without_normalized_label_is_sufficient(kind):
    if kind == 'prepaid':
        data, state = fixture()
    else:
        data, _, raw, _ = deferred_fixture()
        raw['status_changed_at'] = DELIVERED
    source, raw, current = strip_labels(data)
    raw['shipping']['company_code'] = current['company_code']
    raw['shipping']['company_name'] = current['company_name']
    source.pop('salla_shipping_current')
    result = calculate(data, state) if kind == 'prepaid' else calculated(data)
    assert fee_total(result) == (34.5 if kind == 'prepaid' else 10)


def test_label_metadata_conflicts_still_hold_an_unpaid_prepaid_charge():
    data, state = fixture()
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    raw['shipments'][0]['company']['code'] = 'different-carrier'
    result = calculate(data, state)
    assert fee_total(result) == 0
    assert result['movements'] == []


def test_deferred_no_label_fact_rejects_changed_carrier_without_rewriting_amount():
    data, _, raw, _ = deferred_fixture()
    raw['status_changed_at'] = DELIVERED
    source, raw, current = strip_labels(data)
    first = calculated(data)
    old_fact = deepcopy(first['engine']['facts']['delivery_event:100'])
    setup = data['mz2_shipping_setup_v2'][0]
    setup['couriers'].append({**setup['couriers'][0], 'courier_key': 'other', 'salla_carrier_keys': ['other']})
    setup['contracts'].append({**setup['contracts'][0], 'id': 'other-rate', 'party_id': 'other'})
    current.update(company_code='other', company_name='Other')
    result = calculated(data, first)
    assert fee_total(result) == fee_total(first)
    assert result['engine']['facts']['delivery_event:100'] == old_fact
    assert any('confirmed_evidence_changed' in i['code'] for i in result['engine']['issues'])


def test_existing_label_fact_is_preserved_when_later_payload_loses_label():
    data, _, raw, _ = deferred_fixture()
    raw['status_changed_at'] = DELIVERED
    first = calculated(data)
    prior = deepcopy(first['engine']['facts'])
    strip_labels(data)
    result = calculated(data, first)
    assert result['engine']['facts'] == prior
    assert fee_total(result) == fee_total(first)
    assert result['movements'] == first['movements']
    assert calculated(data, result) == result
