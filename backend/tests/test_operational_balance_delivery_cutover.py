"""Old-order delivery events must not replay old sales or supplier estimates."""
from copy import deepcopy
from decimal import Decimal

import pytest

from test_operational_balance_sources import HardBoundaryDB, row, run, START, NOW
from test_operational_balance_source_fixes import shipping_data
from operational_balance_engine import reconcile
from operational_balance_service import report
from operational_balance_sources import collect_sources


OLD = '2026-10-01T09:00:00+00:00'
DELIVERED = '2026-10-06T12:00:00+00:00'


def driver_data(method='cash', *, delivered_at=DELIVERED):
    data = shipping_data(created_at=OLD, updated_at=NOW, payment={'method': 'cod'},
                         paid_amount='100', status='delivered')
    rate = data['mz2_shipping_setup_v2'][0]['contracts'][0]
    rate.update(party_type='store_driver', party_id='driver')
    data['store_delivery_assignments'] = [row(id='assignment', order_id='100', order_number='10',
        driver_id='driver', status='delivered', active=True, delivered_at=delivered_at)]
    data['store_delivery_collections'] = [row(id='collection', order_id='100', order_number='10',
        assignment_id='assignment', driver_id='driver', amount='200',
        amount_source='unified_orders.remaining_amount', payment_method=method,
        cod_custody_amount='200' if method == 'cash' else '0', collected_at=DELIVERED)]
    return data


def courier_data():
    data = shipping_data(created_at=OLD, updated_at=NOW, payment={'method': 'cod'},
                         paid_amount='100', status='delivered')
    source = data['unified_orders'][0]
    source['salla_shipping_current'] = {'source_kind': 'shipment', 'company_code': 'carrier',
        'company_name': 'Carrier', 'shipment_id': 'shipment', 'tracking_number': 'TEST-AWB',
        'status': 'delivered', 'superseded_shipment_ids': []}
    raw = source['raw_by_source']['salla_direct']
    raw['shipping'].update(shipment_id='shipment', tracking_number='TEST-AWB',
                           status='delivered', delivered_at=DELIVERED)
    raw['shipments'] = [{'id': 'shipment', 'tracking_number': 'TEST-AWB', 'company': {'code': 'carrier'},
                         'status': 'delivered', 'delivered_at': DELIVERED}]
    return data


def active():
    return {'owner_id': 'owner', 'status': 'active', 'started_at': START, 'movements': [],
            'openings': {'old-driver-balance': {'id': 'old-driver-balance', 'party_type': 'store_driver',
                'party_id': 'driver', 'currency': 'SAR', 'direction': 'for_us',
                'name': 'Synthetic driver', 'amount': '40.00'}}}


def collect(data):
    before = deepcopy(data)
    sources = run(collect_sources(HardBoundaryDB(data), 'owner', START, NOW))
    assert data == before
    assert sources['orders'] == []
    return sources


def fee_total(state):
    return sum((Decimal(r['confirmed']) for r in state['engine']['obligations'].values()
                if r['kind'] == 'shipping'), Decimal(0))


@pytest.mark.parametrize('method', ['cash', 'bank_transfer', 'card_terminal'])
def test_old_order_delivered_after_start_recognizes_fee_and_only_real_custody(method):
    sources = collect(driver_data(method))
    state = reconcile(active(), sources, NOW)
    assert fee_total(state) == Decimal('12.00')
    assert not any(r['kind'] in {'supplier', 'provider'} for r in state['engine']['obligations'].values())
    assert state['movements'] == []
    cod = state['engine']['obligations']['cod:100']
    assert cod['confirmed'] == ('200.00' if method == 'cash' else '0.00')
    position = report(state)
    driver = next(r for r in position['parties'] if r['party_id'] == 'driver')
    assert driver['outstanding_receivable'] == ('240.00' if method == 'cash' else '40.00')
    assert driver['outstanding_payable'] == '12.00'
    assert reconcile(state, sources, NOW) == state


def test_old_delivery_uses_event_time_for_fee_contract_not_later_order_update():
    data = driver_data()
    setup = data['mz2_shipping_setup_v2'][0]
    setup['contracts'][0]['effective_to'] = '2026-10-07T00:00:00+00:00'
    setup['contracts'].append({**setup['contracts'][0], 'id': 'later-rate', 'delivery_fee': '50',
        'effective_from': '2026-10-07T00:00:00+00:00', 'effective_to': None})
    state = reconcile(active(), collect(data), NOW)
    assert fee_total(state) == Decimal('12.00')
    assert state['engine']['facts']['shipping:100']['delivered_at'] == DELIVERED


@pytest.mark.parametrize('delivered_at', [OLD, '2026-10-04T23:59:59.999999+00:00'])
def test_prestart_deliveries_remain_opening_history(delivered_at):
    state = reconcile(active(), collect(driver_data(delivered_at=delivered_at)), NOW)
    assert fee_total(state) == 0
    assert 'cod:100' not in state['engine']['obligations']
    assert state['openings'] == active()['openings']


def test_missing_event_time_is_visible_and_never_replaced_with_updated_at():
    data = driver_data()
    data['store_delivery_assignments'][0].pop('delivered_at')
    sources = collect(data)
    assert sources['issues']
    assert not sources.get('delivery_events')
    assert fee_total(reconcile(active(), sources, NOW)) == 0


def test_old_order_receipts_and_payments_survive_replay_without_duplicate_fee():
    sources = collect(driver_data())
    state = reconcile(active(), sources, NOW)
    state['movements'] = [{'id': 'synthetic-settlement', 'direction': 'incoming', 'kind': 'collection',
        'amount': '150.00', 'currency': 'SAR', 'party_type': 'store_driver', 'party_id': 'driver',
        'bank_kind': 'bank', 'bank_id': 'bank', 'allocations': [{'obligation_id': 'cod:100', 'amount': '150.00'}]}]
    replayed = reconcile(state, sources, NOW)
    assert replayed['movements'] == state['movements']
    assert replayed['engine']['facts'] == state['engine']['facts']
    assert fee_total(replayed) == Decimal('12.00')
    driver = next(r for r in report(replayed)['parties'] if r['party_id'] == 'driver')
    assert driver['outstanding_receivable'] == '90.00'


def test_foreign_assignment_cannot_supply_delivery_for_an_old_order():
    data = driver_data()
    data['store_delivery_assignments'][0]['user_id'] = 'foreign'
    sources = collect(data)
    assert not sources.get('delivery_events')
    assert sources['issues']
    assert fee_total(reconcile(active(), sources, NOW)) == 0


def test_old_prepaid_delivery_does_not_require_product_cost_or_replay_provider_capture():
    data = driver_data()
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    raw['payment'] = {'method': 'tabby', 'status': 'captured', 'amount': '300',
                      'transaction_id': 'old-capture', 'paid_at': OLD}
    raw.pop('total')
    data.pop('mezan_product_cost_profiles_v2')
    state = reconcile(active(), collect(data), NOW)
    assert fee_total(state) == Decimal('10.00')
    assert not any(r['kind'] in {'supplier', 'provider'} for r in state['engine']['obligations'].values())
    assert state['movements'] == []


def test_later_refund_status_does_not_erase_earned_base_fee_or_invent_cash_custody():
    data = driver_data()
    data['unified_orders'][0]['raw_by_source']['salla_direct']['status'] = 'refunded'
    state = reconcile(active(), collect(data), NOW)
    assert fee_total(state) == Decimal('10.00')
    assert 'cod:100' not in state['engine']['obligations']
    assert any(i['code'] == 'operational_delivery_cod_history_reconciliation_required'
               for i in state['engine']['issues'])


def test_old_carrier_delivery_records_remaining_cod_and_shipping_once():
    state = reconcile(active(), collect(courier_data()), NOW)
    assert fee_total(state) == Decimal('12.00')
    carrier = next(r for r in report(state)['parties'] if r['party_type'] == 'courier')
    assert (carrier['outstanding_receivable'], carrier['outstanding_payable']) == ('200.00', '12.00')
    assert state['engine']['facts']['delivery_event:100']['shipment_id'] == 'shipment'
    assert state['movements'] == []


def test_paid_customer_status_is_not_proof_that_carrier_remitted_cod():
    data = courier_data()
    data['unified_orders'][0]['raw_by_source']['salla_direct']['paid_amount'] = '300'
    state = reconcile(active(), collect(data), NOW)
    assert 'cod:100' not in state['engine']['obligations']
    assert 'cod_collection:100' not in state['engine']['facts']
    assert fee_total(state) == Decimal('10.00')
    assert any(i['code'] == 'operational_delivery_cod_history_reconciliation_required'
               for i in state['engine']['issues'])


def test_foreign_currency_fee_contract_is_not_relabelled_as_sar():
    data = courier_data()
    data['mz2_shipping_setup_v2'][0]['contracts'][0]['currency'] = 'USD'
    state = reconcile(active(), collect(data), NOW)
    assert fee_total(state) == 0
    assert state['engine']['obligations']['cod:100']['confirmed'] == '200.00'
    assert any(i['code'] == 'operational_delivery_fee_currency_conflict' for i in state['engine']['issues'])


@pytest.mark.parametrize('patch', [
    {'refunded_amount': '100'}, {'refund_amount': '1'}, {'refunded_sar': '1'},
    {'cancelled_at': NOW}, {'refunded_at': NOW},
    {'payment': {'method': 'cod', 'status': 'refunded'}},
    {'amounts': {'total': {'amount': '900', 'currency': 'SAR'}}}, {'total_amount': '900'},
])
def test_contradictory_old_financial_snapshot_keeps_base_fee_without_invented_cod(patch):
    data = courier_data()
    data['unified_orders'][0]['raw_by_source']['salla_direct'].update(patch)
    state = reconcile(active(), collect(data), NOW)
    assert fee_total(state) == Decimal('10.00')
    assert 'cod_collection:100' not in state['engine']['facts']
    assert 'cod:100' not in state['engine']['obligations']
    assert state['engine']['issues']


@pytest.mark.parametrize('patch', [
    {'collected_at': OLD}, {'collected_at': '2026-10-06T12:01:00+00:00'},
    {'currency': 'QAR'}, {'amount_source': 'unverified'}, {'cod_custody_amount': '201'},
])
def test_invalid_driver_collection_does_not_suppress_earned_base_shipping(patch):
    data = driver_data()
    data['store_delivery_collections'][0].update(patch)
    state = reconcile(active(), collect(data), NOW)
    assert fee_total(state) == Decimal('10.00')
    assert 'cod_collection:100' not in state['engine']['facts']
    assert 'cod:100' not in state['engine']['obligations']
    assert any(i['code'] == 'driver_collection_custody_contract_invalid' for i in state['engine']['issues'])


@pytest.mark.parametrize('change', ['executor', 'timestamp', 'identity'])
def test_changed_delivery_cannot_reassign_frozen_money_or_its_settlements(change):
    data = driver_data()
    state = reconcile(active(), collect(data), NOW)
    original = deepcopy(state)
    assignment = data['store_delivery_assignments'][0]
    if change == 'executor':
        assignment['driver_id'] = 'replacement-driver'
    elif change == 'timestamp':
        assignment['delivered_at'] = '2026-10-07T08:00:00+00:00'
    else:
        assignment['id'] = 'replacement-assignment'
    result = reconcile(state, collect(data), NOW)
    assert result['engine']['facts'] == original['engine']['facts']
    assert result['engine']['obligations'] == original['engine']['obligations']
    assert result['movements'] == original['movements']
    assert any('evidence_changed' in issue['code'] for issue in result['engine']['issues'])


def test_old_driver_collection_arriving_later_completes_custody_without_second_fee():
    data = driver_data()
    receipt = data.pop('store_delivery_collections')
    pending = reconcile(active(), collect(data), NOW)
    assert fee_total(pending) == Decimal('12.00')
    assert pending['engine']['obligations']['cod:100']['confirmed'] == '0.00'
    data['store_delivery_collections'] = receipt
    confirmed = reconcile(pending, collect(data), NOW)
    assert fee_total(confirmed) == Decimal('12.00')
    assert confirmed['engine']['obligations']['cod:100']['confirmed'] == '200.00'
    assert confirmed['engine']['facts']['delivery_event:100'] == pending['engine']['facts']['delivery_event:100']


def test_delivery_stream_cannot_bypass_event_cutoff_or_replay_injected_sale_fields():
    sources = collect(driver_data())
    event = sources['delivery_events'][0]
    event['items'] = [{'id': 'fake-old-cost', 'cost': '900', 'supplier_id': 'supplier'}]
    event['payment'] = {'provider': 'tabby', 'gross': '300', 'captures': [{'id': 'fake-capture',
                        'amount': '300', 'captured_at': DELIVERED}]}
    state = reconcile(active(), sources, NOW)
    assert fee_total(state) == Decimal('12.00')
    assert not any(row['kind'] in {'supplier', 'provider'} for row in state['engine']['obligations'].values())
    event['delivery_event']['delivered_at'] = event['delivered_at'] = '2026-10-04T23:59:59.999999+00:00'
    rejected = reconcile(active(), sources, NOW)
    assert fee_total(rejected) == 0
    assert any(i['code'] == 'operational_delivery_event_contract_invalid' for i in rejected['engine']['issues'])


def test_real_refresh_preserves_old_delivery_custody_and_bank_settlement_once():
    from test_operational_balance_integration import run as mongo_run, started, movement
    from operational_balance_service import refresh, create_movement
    from operational_balance_store import read

    async def scenario(db):
        await started(db)
        data = driver_data()
        data['store_drivers'] = [row(id='driver', name='Synthetic driver', status='active')]
        for collection, documents in data.items():
            await db[collection].insert_many(documents)
        first = await refresh(db, 'owner', clock=NOW)
        assert fee_total(first) == Decimal('12.00')
        assert first['engine']['delivery_event_coverage']['version'] == 1
        assert report(first)['summary']['actual_liquidity'] == '1000.00'
        payload = movement()
        payload.update(party_type='store_driver', party_id='driver', direction='incoming', kind='collection',
                       amount='150', reference='old-order-cod-settlement',
                       allocations=[{'obligation_id': 'cod:100', 'amount': '150'}])
        posted = await create_movement(db, 'owner', 'owner', payload, clock=NOW)
        assert await create_movement(db, 'owner', 'owner', payload, clock=NOW) == posted
        refreshed = await refresh(db, 'owner', clock=NOW)
        result = report(refreshed)
        driver = next(r for r in result['parties'] if r['party_id'] == 'driver')
        assert (driver['outstanding_receivable'], driver['outstanding_payable']) == ('50.00', '12.00')
        assert result['summary']['actual_liquidity'] == '1150.00'
        assert len((await read(db, 'owner'))['movements']) == 1
        assert await db.general_ledger.count_documents({}) == 0
        assert await db.mz2_journal_entries.count_documents({}) == 0
    mongo_run(scenario)
