"""Completed prepaid orders do not need a printed shipping label to earn cost."""
from copy import deepcopy
import pytest
from operational_courier_transitions import COMPLETED_PROOF
from operational_delivery_evidence import _instant
from operational_order_completed_evidence import verified_completed_history, verified_completed_webhook
from test_operational_balance_courier_order_transition import COLLECTION, history
from test_operational_balance_prepaid_shipping import fixture, calculate, COMPLETED, NOW


def without_label(data, missing):
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    current = source['salla_shipping_current']
    if missing in {'both', 'waybill'}:
        current.pop('tracking_number', None)
        for key in ('tracking_number', 'shipping_number'):
            source.pop(key, None)
    if missing in {'both', 'shipment'}:
        current.pop('shipment_id', None)
        source.pop('salla_shipment_id', None)
    raw.pop('shipments', None)
    raw['shipping'].pop('shipment_id', None)
    raw['shipping'].pop('tracking_number', None)
    return source, raw, current


@pytest.mark.parametrize('old', [True, False])
@pytest.mark.parametrize('missing', ['both', 'waybill', 'shipment'])
def test_completed_event_without_label_charges_exactly_once(old, missing):
    data, state = fixture(old=old)
    source, raw, current = without_label(data, missing)
    result = calculate(data, state)
    assert len(result['movements']) == 1
    assert result['movements'][0]['amount'] == '34.50'
    assert result['movements'][0]['bank_id'] == 'salla-cash'
    assert result['movements'][0]['occurred_at'] == COMPLETED
    assert calculate(data, result)['movements'] == result['movements']
    raw['status'] = source['order_status_slug'] = 'delivered'
    raw['status_changed_at'] = NOW
    assert calculate(data, result)['movements'] == result['movements']


@pytest.mark.parametrize('proof_kind', ['history', 'webhook'])
def test_verified_completed_event_can_be_saved_without_label(proof_kind):
    data, state = fixture(at=None)
    source, raw, current = without_label(data, 'both')
    if proof_kind == 'history':
        source['salla_shipping_current'] = verified_completed_history(source, [{
            'id': 'completed-1', 'order_id': '100', 'to_status': 'completed', 'created_at': COMPLETED}],
            'owner', _instant(NOW))
    else:
        proof = verified_completed_webhook({'event': 'order.status.updated', 'created_at': COMPLETED,
            'data': {'order_id': '100', 'order_number': '10', 'status': 'completed'}}, raw, current, 'owner')
        assert proof is not None
        current[COMPLETED_PROOF] = proof
    assert len(calculate(data, state)['movements']) == 1


@pytest.mark.parametrize('status,at', [('completed', None), ('delivered', NOW)])
def test_missing_completed_time_or_delivered_only_never_invents_execution(status, at):
    data, state = fixture(current=status, at=at)
    without_label(data, 'both')
    assert calculate(data, state)['movements'] == []


def test_conflicting_carrier_still_rejected_without_label():
    data, state = fixture()
    source, raw, current = without_label(data, 'both')
    source['shipping_company_code'] = 'other-carrier'
    assert calculate(data, state)['movements'] == []


@pytest.mark.parametrize('changed_carrier', [False, True])
def test_order_completion_proof_survives_later_label_but_not_carrier_change(changed_carrier):
    data, state = fixture(at=None)
    original = deepcopy(data['unified_orders'][0])
    source, raw, current = without_label(data, 'both')
    proof = verified_completed_webhook({'event': 'order.status.updated', 'created_at': COMPLETED,
        'data': {'order_id': '100', 'order_number': '10', 'status': 'completed'}}, raw, current, 'owner')
    assert proof and proof['evidence_scope'] == 'order_completion'
    source.update(original)
    source['salla_shipping_current'][COMPLETED_PROOF] = proof
    if changed_carrier:
        source['salla_shipping_current']['company_name'] = 'Other Carrier'
    assert len(calculate(data, state)['movements']) == (0 if changed_carrier else 1)
