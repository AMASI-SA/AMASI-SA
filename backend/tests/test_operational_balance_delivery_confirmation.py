"""Owner date attestations preserve source dates, cutoffs and money authority."""
from copy import deepcopy

import pytest

from operational_balance_engine import reconcile
from operational_balance_sources import collect_sources
from operational_balance_store import digest
from operational_delivery_confirmation import (BASIS, EVENT_SCHEMA, PREFIX, candidate_event,
    confirmed_event, record_confirmation, public_record, validate_record)
from test_operational_balance_delivery_cutover import OLD, DELIVERED, NOW, START, HardBoundaryDB, active, fee_total, run
from test_operational_balance_delivery_start_rule import undated, calculate, party, table, LATER
from test_operational_balance_delivery_start_rule import CREATED
from test_operational_balance_sources import row


def data_for(kind='courier'):
    data = undated(kind)
    data['unified_orders'][0]['raw_by_source']['salla_direct']['created_at'] = OLD
    return data


def candidate(data, *, at=NOW, history=(), **extra):
    source = data['unified_orders'][0]
    return candidate_event(source['raw_by_source']['salla_direct'], source=source, owner='owner',
        assignments=data.get('store_delivery_assignments', []), setup=data['mz2_shipping_setup_v2'][0],
        started_at=START, as_of=at, history=history, **extra)


def confirmed(data, state=None):
    state = deepcopy(state or active())
    event = candidate(data)
    record = record_confirmation(state, event, actor='owner', actor_name='Synthetic owner', confirmed_at=NOW,
                                 source_hash=digest(data), preview_hash=digest('preview'))
    state.setdefault('engine', {}).setdefault('facts', {})[PREFIX + '100'] = record
    return state, record


@pytest.mark.parametrize('kind', ['courier', 'native_driver', 'contract_driver'])
def test_manual_old_delivery_is_delivery_only_and_never_fabricates_date_or_cash(kind):
    data = data_for(kind)
    before = deepcopy(data)
    state, record = confirmed(data)
    sources = run(collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=state))
    assert data == before and sources['orders'] == []
    assert len(sources['delivery_events']) == 1
    event = sources['delivery_events'][0]['delivery_event']
    assert event['schema'] == EVENT_SCHEMA and event['recognition_basis'] == BASIS
    assert event['delivered_at'] is None and event['recorded_at'] == NOW and event['order_created_at'] == OLD
    result = reconcile(state, sources, NOW)
    # The contract-only fixture also proves its pre-existing fixed COD service
    # commission; that fee is independent of actual customer cash custody.
    assert fee_total(result) == (12 if kind == 'contract_driver' else 10)
    assert result['engine']['facts'][PREFIX + '100'] == record
    assert public_record(result, record)['state'] == 'applied'
    assert result['openings'] == active()['openings'] and result['movements'] == []
    assert not any(row['kind'] in {'provider', 'supplier'} for row in result['engine']['obligations'].values())
    assert 'cod_collection:100' not in result['engine']['facts']
    assert all(row['confirmed'] == '0.00' for row in result['engine']['obligations'].values() if row['kind'] == 'cod')
    fee = result['engine']['obligations']['shipping:100:base_shipping']
    assert fee['delivered_at'] is None and fee['recorded_at'] == NOW and 'occurred_at' not in fee
    details = party(result, 'courier' if kind == 'courier' else 'store_driver')
    assert details['delivered_orders'] == details['confirmed_orders'] == 1
    assert calculate(data, result, at=LATER)['engine']['obligations'] == result['engine']['obligations']


@pytest.mark.parametrize('kind', ['courier', 'native_driver', 'contract_driver'])
def test_saved_attestation_survives_old_financial_snapshot_without_counting_a_fee(kind):
    data = data_for(kind)
    previous = calculate(data)
    previous['engine']['as_of'] = NOW
    state, record = confirmed(data, previous)
    assert state['engine']['as_of'] == previous['engine']['as_of']
    assert state['engine']['obligations'] == previous['engine']['obligations']
    info = party(state, 'courier' if kind == 'courier' else 'store_driver')
    assert info['delivered_orders'] == 0
    detail = next(row for row in info['pending_order_details'] if row['order_id'] == '100')
    assert detail['delivery_after_start_confirmation'] == {
        'can_confirm': False, 'state': 'pending_reconciliation', 'party_type': record['party_type'],
        'party_id': record['party_id'], 'confirmation_id': record['id'], 'confirmed_at': NOW}
    assert fee_total(state) == 0


@pytest.mark.parametrize('kind', ['courier', 'native_driver'])
def test_exact_date_gap_has_party_bound_action_without_waiving_other_reasons(kind):
    state = calculate(data_for(kind))
    info = party(state, 'courier' if kind == 'courier' else 'store_driver')
    item = info['pending_order_details'][0]
    assert item['delivery_after_start_confirmation'] == {'can_confirm': True, 'state': 'available',
        'party_type': info['party_type'], 'party_id': info['party_id']}
    state['engine']['issues'].append({'code': 'operational_delivery_timestamp_ambiguous',
        'source_id': '100', 'order_id': '100', 'party_type': info['party_type'], 'party_id': info['party_id']})
    refreshed = party(state, info['party_type'])
    assert 'delivery_after_start_confirmation' not in refreshed['pending_order_details'][0]


@pytest.mark.parametrize('kind', ['courier', 'native_driver'])
@pytest.mark.parametrize('at', ['2026-09-30T00:00:00+00:00', '2026-10-04T00:00:00+00:00', LATER])
def test_manual_candidate_does_not_override_a_known_contradictory_date(kind, at):
    data = data_for(kind)
    if kind == 'courier':
        data['unified_orders'][0]['raw_by_source']['salla_direct']['status_changed_at'] = at
    else:
        data['store_delivery_assignments'][0]['delivered_at'] = at
    with pytest.raises(ValueError):
        candidate(data)


@pytest.mark.parametrize('kind', ['courier', 'native_driver'])
def test_later_arriving_actual_date_can_agree_but_never_redates_saved_record(kind):
    data = data_for(kind)
    state, record = confirmed(data)
    first = calculate(data, state)
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    if kind == 'courier':
        raw['status_changed_at'] = DELIVERED
        raw['shipping']['delivered_at'] = DELIVERED
        raw['shipments'][0]['delivered_at'] = DELIVERED
    else:
        data['store_delivery_assignments'][0]['delivered_at'] = DELIVERED
    second = calculate(data, first, at=LATER)
    assert second['engine']['facts'] == first['engine']['facts']
    assert second['engine']['obligations'] == first['engine']['obligations']
    assert second['engine']['facts'][PREFIX + '100']['confirmed_at'] == record['confirmed_at']
    raw['status_changed_at'] = LATER
    contradicted = calculate(data, first, at=LATER)
    assert contradicted['engine']['facts'] == first['engine']['facts']
    assert contradicted['engine']['obligations'] == first['engine']['obligations']
    assert any(row['code'].startswith('operational_delivery_') for row in contradicted['engine']['issues'])


@pytest.mark.parametrize('change', ['party', 'assignment', 'start', 'seal', 'owner'])
def test_saved_proof_cannot_be_rebound_or_forged(change):
    data = data_for('native_driver')
    state, record = confirmed(data)
    if change == 'party':
        data['store_delivery_assignments'][0]['driver_id'] = 'other'
    elif change == 'assignment':
        data['store_delivery_assignments'][0]['id'] = 'replacement'
    elif change == 'start':
        state['started_at'] = '2026-10-05T01:00:00+00:00'
        with pytest.raises(ValueError):
            validate_record(record, owner='owner', started_at=state['started_at'])
        return
    else:
        record['seal' if change == 'seal' else 'owner_id'] = 'forged'
    result = calculate(data, state)
    assert fee_total(result) == 0 and 'delivery_event:100' not in result['engine']['facts']


def test_a_normalized_manual_event_requires_the_saved_sealed_owner_record():
    data = data_for()
    state, _ = confirmed(data)
    sources = run(collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=state))
    forged = reconcile(active(), sources, NOW)
    assert fee_total(forged) == 0 and 'delivery_event:100' not in forged['engine']['facts']
    assert any(row['code'] == 'operational_delivery_confirmation_invalid' for row in forged['engine']['issues'])


def test_manual_tariff_window_starts_at_opening_not_at_old_order_creation():
    data = data_for()
    setup = data['mz2_shipping_setup_v2'][0]
    current = setup['contracts'][0]
    old = {**current, 'id': 'before-opening', 'delivery_fee': '99', 'effective_to': START}
    current['effective_from'] = START
    setup['contracts'].append(old)
    state, _ = confirmed(data)
    assert fee_total(calculate(data, state)) == 10
    current['effective_to'] = DELIVERED
    setup['contracts'].append({**current, 'id': 'changed-price', 'delivery_fee': '20',
                               'effective_from': DELIVERED, 'effective_to': None})
    result = calculate(data, state)
    assert fee_total(result) == 0
    row = party(result, 'courier')
    assert row['delivered_orders'] == 1 and row['confirmed_orders'] == 0
    assert any(reason['code'] == 'shipping_delivery_tariff_period_unproven'
               for detail in row['pending_order_details'] for reason in detail['reasons'])


def test_unallocated_old_courier_payout_holds_money_but_retains_delivery_count():
    data = data_for()
    state, _ = confirmed(data)
    state['movements'] = [{'id': 'old-payment', 'kind': 'payment', 'direction': 'outgoing',
        'party_type': 'courier', 'party_id': 'carrier', 'currency': 'SAR', 'amount': '9.00', 'allocations': []}]
    result = calculate(data, state)
    assert fee_total(result) == 0 and result['movements'] == state['movements']
    assert party(result, 'courier')['delivered_orders'] == 1
    assert any(row['code'] == 'courier_fee_historical_review_required' for row in result['engine']['issues'])


def test_post_start_order_does_not_need_a_second_manual_confirmation_rule():
    with pytest.raises(ValueError, match='confirmation_not_required'):
        candidate(undated())


@pytest.mark.parametrize('kind', ['courier', 'native_driver'])
@pytest.mark.parametrize('at,blocked', [(DELIVERED, False), (NOW, True), (LATER, True)])
def test_manual_saved_proof_rechecks_local_negative_history_for_both_executors(kind, at, blocked):
    data = data_for(kind)
    state, _ = confirmed(data)
    data['order_activity_events_v2'] = [row(order_number='10', provider_order_id='100', source='salla_history',
        source_event_id='negative', native_status_transition=[{'delivered': False, 'at': at}])]
    result = calculate(data, state)
    assert ('delivery_event:100' not in result['engine']['facts']) is blocked
    assert fee_total(result) == (0 if blocked else 10)
    if blocked:
        item = party(result, 'courier' if kind == 'courier' else 'store_driver')['pending_order_details'][0]
        assert item['delivery_after_start_confirmation']['state'] == 'pending_reconciliation'


def test_missing_canonical_source_keeps_saved_proof_visible_without_inventing_service():
    data = data_for()
    state, record = confirmed(data)
    data['unified_orders'] = []
    result = calculate(data, state)
    assert fee_total(result) == 0
    info = party(result, 'courier')
    assert info['delivered_orders'] == 0
    assert info['pending_order_details'][0]['delivery_after_start_confirmation']['confirmation_id'] == record['id']
    assert any(reason['code'] == 'operational_delivery_confirmation_source_unavailable'
               for reason in info['pending_order_details'][0]['reasons'])


def test_actual_projection_exposes_positive_applied_evidence_with_unknown_delivery_time():
    from operational_financial_position import build_financial_position
    data = data_for()
    state, record = confirmed(data)
    result = calculate(data, state)
    projection = build_financial_position(result)
    detail = next(row for row in projection['currencies']['SAR']['deliveries']['rows'] if row['order_id'] == '100')
    assert detail['order_number'] == '10' and detail['party_type'] == 'courier' and detail['party_id'] == 'carrier'
    assert detail['delivered_at'] is None and detail['recognition_basis'] == BASIS and detail['recorded_at'] == NOW
    assert detail['delivery_after_start_confirmation'] == {'can_confirm': False, 'state': 'applied',
        'party_type': 'courier', 'party_id': 'carrier', 'confirmation_id': record['id'], 'confirmed_at': NOW}
    pending_projection = build_financial_position(state)
    assert all('delivery_after_start_confirmation' not in row
               for row in pending_projection['currencies'].get('SAR', {}).get('deliveries', {}).get('rows', []))


@pytest.mark.parametrize('saved_kind', ['manual', 'manual_empty', 'manual_null', 'automatic'])
@pytest.mark.parametrize('carrier_still_delivered', [False, True])
def test_stale_saved_delivery_cannot_fall_back_after_status_and_identity_downgrade(saved_kind, carrier_still_delivered):
    data = data_for('contract_driver')
    state, _ = confirmed(data)
    if saved_kind == 'automatic':
        data = undated('contract_driver')
        event = calculate(data)['engine']['facts']['delivery_event:100']
        state = {**active(), 'engine': {'facts': {'delivery_event:100': event}}}
    elif saved_kind in {'manual_empty', 'manual_null'}:
        state['engine']['facts'][PREFIX + '100'] = {} if saved_kind == 'manual_empty' else None
    before = deepcopy(state)
    source = data['unified_orders'][0]
    source['raw_by_source']['salla_direct'].update(created_at=CREATED, status='cancelled')
    data['store_delivery_assignments'][0].pop('order_id')
    if carrier_still_delivered:
        # The outer current-shipment check passes, while the old inner status
        # check skipped the resolver. Both guards must honor saved proof presence.
        source['salla_shipping_current'] = {'status': 'delivered'}
    sources = run(collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=state))
    order = next(value for value in sources['orders'] if value['id'] == '100')
    assert order.get('shipping_evidence_held') is True
    assert not order.get('carrier') and not order.get('delivery_event')
    result = reconcile(state, sources, NOW)
    assert fee_total(result) == 0
    assert result['engine']['facts'] == before['engine']['facts']
    assert result['openings'] == before['openings'] and result['movements'] == before['movements']
    assert any(row['code'] == 'operational_delivery_order_shipment_status_conflict' for row in result['engine']['issues'])


def test_stale_saved_automatic_delivery_keeps_the_original_booked_fee_without_new_effect():
    data = undated('contract_driver')
    state = calculate(data)
    source = data['unified_orders'][0]
    source['raw_by_source']['salla_direct']['status'] = 'cancelled'
    data['store_delivery_assignments'][0].pop('order_id')
    result = calculate(data, state, at=LATER)
    assert result['engine']['facts'] == state['engine']['facts']
    assert result['engine']['obligations'] == state['engine']['obligations']
    assert fee_total(result) == fee_total(state) == 12
    assert result['engine']['obligations']['shipping:100:base_shipping']['confirmed'] == '10.00'
    assert result['engine']['obligations']['shipping:100:cod_commission']['confirmed'] == '2.00'
    assert result['openings'] == state['openings'] and result['movements'] == state['movements']
    assert any(row['code'] == 'operational_delivery_order_shipment_status_conflict' for row in result['engine']['issues'])


@pytest.mark.parametrize('duplicate_owner', ['owner', 'other'])
def test_one_order_reader_checks_assignment_identity_outside_current_order(duplicate_owner):
    from mongomock_motor import AsyncMongoMockClient
    from operational_delivery_confirmation_sources import read_candidate
    async def scenario():
        data = data_for('contract_driver')
        duplicate = {**deepcopy(data['store_delivery_assignments'][0]), 'user_id': duplicate_owner,
                     'order_id': 'other-order', 'order_number': 'other-number'}
        data['store_delivery_assignments'].append(duplicate)
        db = AsyncMongoMockClient()['one_order_assignment_uniqueness']
        for name, documents in data.items():
            if documents:
                await db[name].insert_many(deepcopy(documents))
        payload = {'order_id': '100', 'order_number': '10', 'party_type': 'store_driver', 'party_id': 'driver'}
        if duplicate_owner == 'owner':
            with pytest.raises(ValueError, match='^operational_delivery_assignment_ambiguous$'):
                candidate(data)
            with pytest.raises(ValueError, match='^operational_delivery_assignment_ambiguous$'):
                await read_candidate(db, 'owner', active(), payload, NOW)
        else:
            event, source_hash = await read_candidate(db, 'owner', active(), payload, NOW)
            assert event['assignment_id'] == 'assignment' and event['party_id'] == 'driver'
            assert len(source_hash) == 64
    run(scenario())
