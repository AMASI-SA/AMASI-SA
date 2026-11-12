"""Completion evidence survives label creation without becoming COD evidence."""
import asyncio
from copy import deepcopy

import pytest
from mongomock_motor import AsyncMongoMockClient

from operational_courier_transitions import COMPLETED_PROOF
from operational_order_completed_evidence import completion_time, verified_completed_webhook, verified_completed_history
from operational_delivery_evidence import _instant
from salla_shipping import accept_shipping
from test_operational_balance_prepaid_shipping import fixture, COMPLETED
from test_operational_balance_delivery_cutover import START, NOW
from test_operational_completed_shipping_expansion import without_label, admitted


def completion_fixture():
    data, state = fixture(at=None)
    without_label(data)
    admitted(state)
    source = data['unified_orders'][0]
    return source, source['raw_by_source']['salla_direct']


@pytest.mark.parametrize('kind', ['webhook', 'history'])
def test_typed_completion_before_label_preserves_order_carrier_and_actual_time(kind):
    source, raw = completion_fixture()
    current = source['salla_shipping_current']
    if kind == 'webhook':
        proof = verified_completed_webhook({'event': 'order.status.updated', 'created_at': COMPLETED,
            'data': {'order_id': '100', 'order_number': '10', 'status': 'تم التنفيذ'}}, raw, current, 'owner')
        assert proof is not None
        current[COMPLETED_PROOF] = proof
    else:
        source['salla_shipping_current'] = verified_completed_history(source,
            [{'id': 'completion', 'order_id': '100', 'to_status': 'completed', 'created_at': COMPLETED}],
            'owner', _instant(NOW))
        proof = source['salla_shipping_current'][COMPLETED_PROOF]
    assert proof['evidence_scope'] == 'order_completion' and proof['carrier_code'] == 'aramex'
    assert completion_time(raw, source, (), _instant(NOW)) == _instant(COMPLETED)
    observed = {**source['salla_shipping_current'], 'shipment_id': 'later-label', 'tracking_number': 'LATER-AWB',
                'provider_updated_at': NOW}
    observed.pop(COMPLETED_PROOF)
    accepted = accept_shipping(source, observed)
    assert accepted[COMPLETED_PROOF] == proof
    source['salla_shipping_current'] = accepted
    assert completion_time(raw, source, (), _instant(NOW)) == _instant(COMPLETED)
    changed = deepcopy(source)
    changed['salla_shipping_current']['company_code'] = 'another'
    with pytest.raises(ValueError, match='completion_ambiguous'):
        completion_time(raw, changed, (), _instant(NOW))


def test_missing_completion_sync_reads_native_history_without_requesting_a_shipment(monkeypatch):
    from salla_integration import courier_delivery_sync as sync
    source, _ = completion_fixture()
    calls = []
    async def provider(_db, owner, method, path, *, params):
        calls.append(path)
        assert owner == 'owner' and method == 'GET' and path == '/orders/100/histories'
        return {'success': True, 'data': [{'id': 'completion', 'order_id': '100',
            'to_status': 'completed', 'created_at': COMPLETED}],
            'pagination': {'total': 1, 'totalPages': 1, 'currentPage': 1}}
    monkeypatch.setattr(sync, 'call_salla', provider)
    async def scenario():
        db = AsyncMongoMockClient()['synthetic_completion_no_label']
        await db.unified_orders.insert_one(deepcopy(source))
        await sync.acquire_missing(db, 'owner', [{'source_id': '100',
            'code': 'operational_prepaid_shipping_completion_timestamp_missing'}], started_at=START)
        return await db.unified_orders.find_one({'user_id': 'owner', 'order_number': '10'}, {'_id': 0})
    saved = asyncio.run(scenario())
    assert calls == ['/orders/100/histories']
    assert saved['salla_shipping_current'][COMPLETED_PROOF]['at'] == COMPLETED
    assert saved['salla_shipping_current'][COMPLETED_PROOF]['evidence_scope'] == 'order_completion'
    assert saved['raw_by_source'] == source['raw_by_source']


@pytest.mark.parametrize('change', ['time', 'carrier'])
def test_label_free_webhook_never_substitutes_received_time_or_unknown_carrier(change):
    source, raw = completion_fixture()
    current = source['salla_shipping_current']
    stamp = COMPLETED
    if change == 'time':
        stamp = None
    else:
        current.pop('company_code')
        current.pop('company_name')
    assert verified_completed_webhook({'event': 'order.status.updated', 'created_at': stamp,
        'data': {'order_id': '100', 'order_number': '10', 'status': 'completed'}}, raw, current, 'owner') is None


@pytest.mark.parametrize('field,identity', [('shipment_id', 'shared-shipment'), ('tracking_number', 'SHARED-AWB')])
@pytest.mark.parametrize('saved_kind', ['current', 'completed_shipping:', 'prepaid_shipping:', 'approval'])
def test_a_partial_label_cannot_be_reused_from_another_order(field, identity, saved_kind):
    from operational_delivery_evidence import conflicting_shipments
    source, _ = completion_fixture()
    source['salla_shipping_current'][field] = identity
    other = deepcopy(source)
    other['raw_by_source']['salla_direct']['id'] = '200'
    other['raw_by_source']['salla_direct']['reference_id'] = '20'
    saved = {'order_id': '100', 'party_type': 'courier',
             'shipment_id' if field == 'shipment_id' else 'waybill': identity}
    state, sources = {}, [other]
    if saved_kind == 'current':
        sources.append(source)
    elif saved_kind == 'approval':
        state['completed_shipping_fee_approvals'] = {'approval': {'candidate': saved}}
    else:
        state['engine'] = {'facts': {saved_kind + '100': saved}}
    expected = ('shipment' if field == 'shipment_id' else 'waybill', identity)
    assert expected in conflicting_shipments(sources, state)
    assert not conflicting_shipments([source], {})


def test_two_completed_orders_with_no_label_do_not_invent_a_shared_label():
    from operational_delivery_evidence import conflicting_shipments
    source, _ = completion_fixture()
    other = deepcopy(source)
    other['raw_by_source']['salla_direct']['id'] = '200'
    assert not conflicting_shipments([source, other], {})
