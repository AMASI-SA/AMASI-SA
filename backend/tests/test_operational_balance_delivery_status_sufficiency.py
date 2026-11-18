"""Current delivered status is sufficient for orders created after opening."""
from copy import deepcopy

import pytest

from operational_courier_transitions import WEBHOOK_PROOF
from operational_delivery_evidence import STATUS_BASIS, STATUS_SCHEMA, resolve_delivery_event
from test_operational_balance_delivery_cutover import DELIVERED, NOW, START, courier_data, driver_data, fee_total, row, run
from test_operational_balance_delivery_start_rule import CREATED, LATER, calculate, party, undated


@pytest.mark.parametrize('gap', [
    'stale-embedded-waybill', 'missing-embedded-shipment', 'incomplete-carrier-history',
    'unrelated-history', 'conflicting-history', 'invalid-order-clock', 'future-order-clock',
    'precreation-order-clock', 'conflicting-shipment-clocks',
])
def test_poststart_delivery_is_independent_of_optional_history_and_shipment_dates(gap):
    data = undated()
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    if gap == 'stale-embedded-waybill':
        raw['status_changed_at'] = DELIVERED
        raw['shipping']['shipment'] = {'id': 'obsolete-embedded-label'}
    elif gap == 'missing-embedded-shipment':
        raw.pop('shipments')
        raw['shipping'].pop('shipment_id')
    elif gap == 'incomplete-carrier-history':
        source['salla_shipping_current'][WEBHOOK_PROOF] = {'source': 'verified_salla_carrier_history'}
    elif gap in {'unrelated-history', 'conflicting-history'}:
        data['order_activity_events_v2'] = [row(order_number='10',
            provider_order_id='unrelated' if gap == 'unrelated-history' else '100',
            source='salla_history', source_event_id='historical-observation',
            native_status_transition=[{'delivered': False, 'at': NOW}])]
    elif gap == 'invalid-order-clock':
        raw['status_changed_at'] = 'not-a-delivery-clock'
    elif gap == 'future-order-clock':
        raw['status_changed_at'] = LATER
    elif gap == 'precreation-order-clock':
        raw['status_changed_at'] = START
    else:
        raw['shipping']['delivered_at'] = DELIVERED
        raw['shipments'][0]['actual_delivered_at'] = NOW
    result = calculate(data)
    event = result['engine']['facts']['delivery_event:100']
    assert event['schema'] == STATUS_SCHEMA and event['recognition_basis'] == STATUS_BASIS
    assert event['delivered_at'] is None and event['recorded_at'] == NOW
    assert event['party_id'] == 'carrier' and event['waybill'] == 'TEST-AWB'
    assert not any(key in event for key in ('native_delivery_at', 'source_history_id', 'timestamp_source'))
    assert fee_total(result) == 10
    assert not any(issue['code'].startswith('operational_delivery_') for issue in result['engine']['issues'])
    assert 'cod_collection:100' not in result['engine']['facts']
    assert not any(value['kind'] == 'cod' and value['confirmed'] != '0.00'
                   for value in result['engine']['obligations'].values())
    assert party(result, 'courier')['delivered_orders'] == 1
    assert result['movements'] == []


def test_valid_actual_date_and_existing_paid_facts_keep_their_dates_and_keys():
    data = courier_data()
    data['unified_orders'][0]['raw_by_source']['salla_direct']['created_at'] = CREATED
    first = calculate(data)
    assert first['engine']['facts']['delivery_event:100']['delivered_at'] == DELIVERED
    assert first['engine']['facts']['delivery_event:100']['schema'] == 'operational_delivery_event_v1'
    first['movements'].append({'id': 'paid-fee', 'party_type': 'courier', 'party_id': 'carrier',
        'currency': 'SAR', 'kind': 'payment', 'direction': 'outgoing', 'amount': '4.00',
        'allocations': [{'obligation_id': 'shipping:100:base_shipping', 'amount': '4.00'}]})
    repeated = calculate(data, first, at=LATER)
    assert repeated['engine']['facts'] == first['engine']['facts']
    assert repeated['engine']['obligations'] == first['engine']['obligations']
    assert repeated['movements'] == first['movements']
    assert repeated['openings'] == first['openings']
    assert fee_total(repeated) == fee_total(first) == 12


@pytest.mark.parametrize('kind,fault', [('courier', 'retired-carrier'), ('courier', 'missing-currency'),
                                       ('driver', 'missing-currency')])
def test_delivery_before_opening_stays_out_of_scope_before_financial_validation(kind, fault):
    data = courier_data() if kind == 'courier' else driver_data(delivered_at='2026-10-04T12:00:00+00:00')
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    raw['status_changed_at'] = '2026-10-04T12:00:00+00:00'
    for shipment in [raw['shipping'], *raw.get('shipments', [])]:
        shipment['delivered_at'] = raw['status_changed_at']
    if fault == 'retired-carrier':
        data['mz2_shipping_setup_v2'][0]['couriers'] = []
    else:
        raw.pop('currency', None)
        for candidate in [raw.get('amounts', {}), raw.get('payment', {}),
                          raw.get('total', {}), *data.get('store_delivery_assignments', [])]:
            if isinstance(candidate, dict):
                candidate.pop('currency', None)
    assert resolve_delivery_event(raw, source=source, owner='owner',
        assignments=data.get('store_delivery_assignments', []), setup=data['mz2_shipping_setup_v2'][0],
        started_at=START, as_of=NOW) is None


@pytest.mark.parametrize('valid_date', [True, False])
def test_only_a_valid_actual_date_selects_a_tariff_within_the_observation_interval(valid_date):
    data = undated()
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    raw['status_changed_at'] = DELIVERED if valid_date else 'invalid-optional-clock'
    contracts = data['mz2_shipping_setup_v2'][0]['contracts']
    boundary = '2026-10-06T08:00:00+00:00'
    contracts[0]['effective_to'] = boundary
    contracts.append({**contracts[0], 'id': 'later-price', 'delivery_fee': '25',
                      'effective_from': boundary, 'effective_to': None})
    result = calculate(data)
    event = result['engine']['facts']['delivery_event:100']
    assert event['delivered_at'] == (DELIVERED if valid_date else None)
    assert fee_total(result) == (25 if valid_date else 0)
    assert party(result, 'courier')['delivered_orders'] == 1
    assert ('shipping:100:base_shipping' in result['engine']['obligations']) is valid_date


def test_rejected_partial_carrier_provenance_does_not_leak_into_status_proof():
    from test_operational_balance_courier_acquisition import accept, fixture
    data, source, shipment, histories = fixture()
    accept(source, shipment, histories)
    raw = source['raw_by_source']['salla_direct']
    raw.update(created_at=CREATED, status_changed_at=NOW)
    result = calculate(data)
    event = result['engine']['facts']['delivery_event:100']
    assert event['schema'] == STATUS_SCHEMA and event['delivered_at'] is None
    assert not any(key.startswith('source_') and key != 'source_carrier_key' for key in event)
    assert 'native_delivery_at' not in event and 'timestamp_source' not in event
    assert fee_total(result) == 10


@pytest.mark.parametrize('fault,reason', [
    ('current-alias', 'current_shipment_conflict'), ('inactive-current', 'shipment_inactive'),
    ('missing-label', 'shipment_identity_missing'), ('unapproved-carrier', 'courier_identity_ambiguous'),
    ('shared-waybill', 'shipment_shared_by_orders'),
])
def test_current_executor_conflicts_cannot_fall_back_to_a_status_fee(fault, reason):
    data = undated()
    source = data['unified_orders'][0]
    if fault == 'current-alias':
        source['tracking_number'] = 'another-current-label'
    elif fault == 'inactive-current':
        source['salla_shipping_current']['status'] = 'returned'
    elif fault == 'missing-label':
        source['salla_shipping_current']['tracking_number'] = None
    elif fault == 'unapproved-carrier':
        data['mz2_shipping_setup_v2'][0]['couriers'] = []
    else:
        other = deepcopy(source)
        other['order_number'] = '20'
        other['raw_by_source']['salla_direct'].update(id='200', reference_id='20')
        data['unified_orders'].append(other)
    result = calculate(data)
    assert 'delivery_event:100' not in result['engine']['facts']
    assert fee_total(result) == 0
    assert any(issue['code'] == 'operational_delivery_' + reason for issue in result['engine']['issues'])


@pytest.mark.parametrize('kind', ['courier', 'native_driver'])
def test_later_optional_timestamp_cannot_disqualify_or_redate_a_frozen_status(kind):
    data = undated(kind)
    first = calculate(data)
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    raw['updated_at'] = raw['status_changed_at'] = LATER
    if kind == 'native_driver':
        data['store_delivery_assignments'][0]['delivered_at'] = LATER
    result = calculate(data, first, at=LATER)
    assert result['engine']['facts'] == first['engine']['facts']
    assert result['engine']['obligations'] == first['engine']['obligations']
    assert result['movements'] == first['movements']
    assert not any(issue['code'].startswith('operational_delivery_') for issue in result['engine']['issues'])


@pytest.mark.parametrize('delivered', [True, False])
def test_effective_customized_status_controls_poststart_eligibility(delivered):
    data = undated()
    source = data['unified_orders'][0]
    source['salla_shipping_current']['status'] = 'delivering'
    raw = source['raw_by_source']['salla_direct']
    raw['status'] = {'slug': 'shipped' if delivered else 'delivered', 'customized': {
        'slug': 'delivered' if delivered else 'returned',
        'name': 'تم التوصيل' if delivered else 'مسترجع'}}
    result = calculate(data)
    assert ('delivery_event:100' in result['engine']['facts']) is delivered
    assert fee_total(result) == (10 if delivered else 0)


@pytest.mark.parametrize('gap', [
    'operational_delivery_timestamp_missing', 'operational_delivery_order_waybill_unproven',
    'operational_delivery_shipment_source_missing', 'operational_delivery_shipment_not_delivered',
])
def test_saved_delivery_gap_does_not_download_history_for_a_qualifying_order(monkeypatch, gap):
    from salla_integration import courier_delivery_sync as sync
    from test_operational_balance_courier_waybill_acquisition import binding_gap
    _, source, _, _ = binding_gap()
    source['raw_by_source']['salla_direct']['created_at'] = CREATED

    class Orders:
        sent = False

        async def find_one_and_update(self, *args, **kwargs):
            if self.sent:
                return None
            self.sent = True
            return deepcopy(source)

    class DB:
        unified_orders = Orders()

    calls = []

    async def unavailable_history(*args, **kwargs):
        calls.append(args[3])
        raise AssertionError('Post-start status must not need a provider history request')

    monkeypatch.setattr(sync, 'call_salla', unavailable_history)
    result = run(sync.acquire_missing(DB(), 'owner', [{'code': gap, 'source_id': '100'}], started_at=START))
    assert calls == []
    assert result['outcomes'] == [{'order_id': '100', 'status': 'already_eligible'}]
    assert result['saved'] == result['verified'] == 0
