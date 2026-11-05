"""Original prepaid labels may follow completion, within the canonical snapshot."""
from copy import deepcopy

import pytest

from operational_courier_transitions import COMPLETED_PROOF
from operational_courier_tariffs import terms
from operational_delivery_evidence import _instant, _order_waybill
from operational_financial_position import build_financial_position
from operational_order_completed_evidence import verified_completed_history, verified_completed_webhook
from test_operational_balance_courier_order_transition import COLLECTION, history
from test_operational_balance_prepaid_shipping import COMPANY, COMPLETED, NOW, START, calculate, fixture


LABEL_CREATED = '2026-10-06T09:01:00+00:00'
PROOF_PATHS = ['canonical', 'activity', 'history', 'webhook']


def dated_fixture(proof_path, *, completed=COMPLETED, label_created=LABEL_CREATED):
    status = 'completed' if proof_path in {'canonical', 'webhook'} else 'delivering'
    data, state = fixture(current=status, at=None)
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    # The older lagging-label fixture omitted this field and did not exercise
    # a label actually created after the authoritative completion transition.
    raw['shipments'][0]['created_at'] = label_created
    if proof_path == 'canonical':
        raw['status_changed_at'] = completed
    return data, state


def attach_completion(data, proof_path, completed=COMPLETED):
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    if proof_path == 'activity':
        data[COLLECTION] = [history(completed, to_status='completed')]
    elif proof_path == 'history':
        source['salla_shipping_current'] = verified_completed_history(source, [{
            'id': 'completed-1', 'order_id': '100', 'to_status': 'completed',
            'created_at': completed,
        }], 'owner', _instant(NOW))
    elif proof_path == 'webhook':
        current = source['salla_shipping_current']
        proof = verified_completed_webhook({
            'event': 'order.status.updated', 'created_at': completed,
            'data': {'order_id': '100', 'order_number': '10', 'status': 'completed'},
        }, raw, current, 'owner')
        if proof is None:
            return False
        current[COMPLETED_PROOF] = proof
    return True


@pytest.mark.parametrize('proof_path', PROOF_PATHS)
@pytest.mark.parametrize('label_created', [LABEL_CREATED, NOW])
def test_original_label_after_completion_pays_at_completion_once(proof_path, label_created):
    data, prior = dated_fixture(proof_path, label_created=label_created)
    assert attach_completion(data, proof_path)
    result = calculate(data, prior)
    movement, = result['movements']
    assert (movement['amount'], movement['direction'], movement['bank_id']) == (
        '34.50', 'outgoing', 'salla-cash')
    detail, = build_financial_position(result)['currencies']['SAR']['deliveries']['rows']
    assert detail['completed_at'] == COMPLETED
    assert detail['shipping_confirmed'] == detail['shipping_settled'] == '34.50'
    assert detail['shipping_outstanding'] == '0.00'
    assert detail['delivered_at'] is None
    assert result['openings'] == prior['openings']
    assert calculate(data, result) == result


@pytest.mark.parametrize('proof_path', PROOF_PATHS)
@pytest.mark.parametrize('fault', [
    'label_after_snapshot', 'label_in_future', 'snapshot_in_future',
    'snapshot_before_completion', 'snapshot_missing', 'snapshot_mismatch',
])
def test_later_label_still_requires_a_valid_canonical_snapshot(proof_path, fault):
    data, state = dated_fixture(proof_path)
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    if fault == 'label_after_snapshot':
        raw['updated_at'] = COMPLETED
    elif fault == 'label_in_future':
        raw['shipments'][0]['created_at'] = '2026-10-08T00:00:00+00:00'
    elif fault == 'snapshot_in_future':
        raw['updated_at'] = '2026-10-08T00:00:00+00:00'
    elif fault == 'snapshot_before_completion':
        raw['updated_at'] = '2026-10-06T08:59:00+00:00'
    elif fault == 'snapshot_missing':
        raw.pop('updated_at')
    if fault != 'snapshot_mismatch':
        source['g47_salla_snapshot']['source_updated_at'] = raw.get('updated_at')
    else:
        source['g47_salla_snapshot']['source_updated_at'] = LABEL_CREATED
    if proof_path == 'history':
        with pytest.raises(ValueError, match='operational_(prepaid_shipping|delivery)_'):
            attach_completion(data, proof_path)
    elif proof_path == 'webhook' and fault not in {'snapshot_in_future', 'snapshot_mismatch'}:
        assert attach_completion(data, proof_path) is False
    else:
        # The webhook has no reconciliation clock or owner snapshot watermark;
        # these two checks remain authoritative when its proof is consumed.
        assert attach_completion(data, proof_path)
    result = calculate(data, state)
    assert result['movements'] == []
    assert 'shipping-prepaid:100' not in result['engine']['obligations']


@pytest.mark.parametrize('completed,expected_count', [
    ('2026-10-04T23:59:59+00:00', 0), (START, 1),
])
def test_completion_controls_cutover_even_when_label_is_after_start(completed, expected_count):
    data, state = dated_fixture('activity', completed=completed, label_created='2026-10-05T00:01:00+00:00')
    assert attach_completion(data, 'activity', completed)
    result = calculate(data, state)
    assert len(result['movements']) == expected_count
    if expected_count:
        detail, = build_financial_position(result)['currencies']['SAR']['deliveries']['rows']
        assert detail['completed_at'] == START


@pytest.mark.parametrize('fault', [
    'wrong_owner', 'current_identity', 'carrier', 'waybill', 'superseded',
    'multiple_shipments', 'inactive_current', 'inactive_shipment', 'shared_shipment', 'shared_waybill',
])
def test_later_label_does_not_relax_original_waybill_or_owner_checks(fault):
    data, state = dated_fixture('canonical')
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    current = source['salla_shipping_current']
    shipment = raw['shipments'][0]
    if fault == 'wrong_owner':
        source['user_id'] = 'other-owner'
    elif fault == 'current_identity':
        source['salla_shipment_id'] = 'other-shipment'
    elif fault == 'carrier':
        shipment['company']['code'] = 'other-carrier'
    elif fault == 'waybill':
        shipment['tracking_number'] = 'OTHER-AWB'
    elif fault == 'superseded':
        current['superseded_shipment_ids'] = ['old-shipment']
    elif fault == 'multiple_shipments':
        raw['shipments'].append({**shipment, 'id': 'second-shipment'})
    elif fault == 'inactive_current':
        current['status'] = 'cancelled'
    elif fault == 'inactive_shipment':
        shipment['status'] = 'cancelled'
    else:
        other = deepcopy(source)
        other.update(order_number='20', salla_order_id='200')
        other['raw_by_source']['salla_direct'].update(id='200', reference_id='20')
        if fault == 'shared_waybill':
            other['salla_shipping_current']['shipment_id'] = 'second-shipment'
            other['raw_by_source']['salla_direct']['shipments'][0]['id'] = 'second-shipment'
            other['raw_by_source']['salla_direct']['shipping']['shipment_id'] = 'second-shipment'
        data['unified_orders'].append(other)
    result = calculate(data, state)
    assert result['movements'] == []
    assert 'shipping-prepaid:100' not in result['engine']['obligations']


def test_later_label_does_not_collapse_conflicting_completion_times():
    data, state = dated_fixture('activity')
    data[COLLECTION] = [history(COMPLETED, to_status='completed'),
                        history(LABEL_CREATED, id='transition-2', to_status='completed')]
    result = calculate(data, state)
    assert result['movements'] == []
    assert any(row['code'] == 'operational_prepaid_shipping_completion_ambiguous'
               for row in result['engine']['issues'])


@pytest.mark.parametrize('proof_path', ['history', 'webhook'])
def test_later_label_uses_completion_tariff_instead_of_label_tariff(proof_path):
    data, state = dated_fixture(proof_path)
    state['courier_tariffs'].append({**deepcopy(state['courier_tariffs'][0]),
        'version_id': 'later-tariff', 'effective_from': LABEL_CREATED,
        'terms': terms({**COMPANY, 'cost_per_order': 50})})
    assert attach_completion(data, proof_path)
    result = calculate(data, state)
    assert [row['amount'] for row in result['movements']] == ['34.50']


@pytest.mark.parametrize('proof_path', ['history', 'webhook'])
def test_later_delivery_of_dated_label_keeps_the_single_completed_debit(proof_path):
    from salla_shipping import accept_shipping

    data, state = dated_fixture(proof_path)
    assert attach_completion(data, proof_path)
    first = calculate(data, state)
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    raw.update(status='delivered', status_changed_at=NOW)
    source['order_status_slug'] = 'delivered'
    delivered = {**source['salla_shipping_current'], 'status': 'delivered', 'provider_updated_at': NOW}
    delivered.pop(COMPLETED_PROOF)
    source['salla_shipping_current'] = accept_shipping(source, delivered)
    for shipping in (raw['shipping'], *raw['shipments']):
        shipping['status'] = 'delivered'
    result = calculate(data, first)
    assert len(result['movements']) == 1
    assert result['movements'] == first['movements']
    assert result['engine']['facts']['prepaid_shipping:100'] == first['engine']['facts']['prepaid_shipping:100']
    assert result['engine']['facts']['shipping:100']['delivered_at'] == NOW
    assert [row['id'] for row in result['engine']['obligations'].values()
            if row['kind'] == 'shipping'] == ['shipping-prepaid:100']


def test_deferred_waybill_still_cannot_be_created_after_its_delivery():
    data, _ = dated_fixture('canonical')
    source = data['unified_orders'][0]
    current = source['salla_shipping_current']
    with pytest.raises(ValueError, match='operational_delivery_timestamp_before_shipment'):
        _order_waybill(source['raw_by_source']['salla_direct'], current,
                       current['shipment_id'], current['tracking_number'], _instant(COMPLETED))
