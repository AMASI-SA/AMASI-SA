"""Post-opening delivered status earns service, not an invented delivery clock."""
from copy import deepcopy

import pytest

from operational_balance_engine import reconcile
from operational_balance_completeness import annotate_report_coverage
from operational_balance_service import report
from operational_balance_sources import collect_sources
from operational_delivery_evidence import STATUS_BASIS, STATUS_SCHEMA
from operational_financial_position import build_financial_position
from test_operational_balance_delivery_cutover import (
    DELIVERED, NOW, START, HardBoundaryDB, active, courier_data, driver_data, fee_total, row, run,
)
from test_operational_balance_verified_shipping_fees import fixture


CREATED = '2026-10-06T01:00:00+00:00'
LATER = '2026-10-08T00:00:00+00:00'


def undated(kind='courier', *, cash_proof=False):
    data = courier_data() if kind == 'courier' else fixture() if kind == 'native_driver' else driver_data()
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    raw.update(created_at=CREATED, status='delivered')
    raw.pop('status_changed_at', None)
    raw['shipping'].pop('delivered_at', None)
    for shipment in raw.get('shipments', []):
        shipment.pop('delivered_at', None)
    for assignment in data.get('store_delivery_assignments', []):
        assignment.pop('delivered_at', None)
    if not cash_proof:
        data['store_delivery_collections'] = []
    return data


def calculate(data, prior=None, *, at=NOW):
    prior = prior or active()
    before = deepcopy(data)
    sources = run(collect_sources(HardBoundaryDB(data), 'owner', START, at, operational_state=prior))
    assert data == before
    return reconcile(prior, sources, at)


def party(state, kind):
    return next(r for r in annotate_report_coverage(report(state))['parties'] if r['party_type'] == kind)


def table(state, identity):
    return next(t for t in build_financial_position(state)['currencies']['SAR']['tables'] if t['id'] == identity)


@pytest.mark.parametrize('kind', ['courier', 'native_driver', 'contract_driver'])
def test_new_delivered_order_earns_fee_without_delivery_date_or_cash_proof(kind):
    result = calculate(undated(kind))
    proof = result['engine']['facts']['delivery_event:100']
    frozen = result['engine']['facts']['shipping:100']
    assert proof['schema'] == STATUS_SCHEMA and proof['recognition_basis'] == STATUS_BASIS
    assert proof['delivered_at'] is frozen['delivered_at'] is None
    assert proof['recorded_at'] == frozen['recorded_at'] == NOW
    fee = result['engine']['obligations']['shipping:100:base_shipping']
    assert fee['confirmed'] == '10.00'
    assert fee['business_date'] == '2026-10-07'
    assert fee['business_date_basis'] == 'recognition_recorded_at'
    assert fee['delivered_at'] is None
    assert result['movements'] == [] and result['openings'] == active()['openings']
    assert 'cod_collection:100' not in result['engine']['facts']
    assert not any(r['kind'] == 'cod' and r['confirmed'] != '0.00' for r in result['engine']['obligations'].values())
    assert not any(i['code'] == 'operational_delivery_timestamp_missing' for i in result['engine']['issues'])
    details = party(result, 'courier' if kind == 'courier' else 'store_driver')
    assert details['delivered_orders'] == details['confirmed_orders'] == 1
    assert details['pending_orders'] == 1  # Actual collection still needs its own proof.
    assert {item['order_id'] for item in details['pending_order_details']} == {'100'}


@pytest.mark.parametrize('kind', ['courier', 'native_driver', 'contract_driver'])
@pytest.mark.parametrize('created', [START, '2026-10-04T23:59:59.999999+00:00'])
def test_creation_at_or_before_opening_still_needs_actual_after_start_delivery(kind, created):
    data = undated(kind)
    data['unified_orders'][0]['raw_by_source']['salla_direct']['created_at'] = created
    result = calculate(data)
    assert fee_total(result) == 0
    assert 'delivery_event:100' not in result['engine']['facts']
    assert any(i['code'] == 'operational_delivery_timestamp_missing' for i in result['engine']['issues'])


@pytest.mark.parametrize('kind', ['courier', 'native_driver'])
def test_later_refresh_or_new_actual_timestamp_does_not_redate_or_duplicate_fee(kind):
    data = undated(kind)
    first = calculate(data)
    first['movements'].append({'id': 'paid-fee', 'party_type': 'courier' if kind == 'courier' else 'store_driver',
        'party_id': 'carrier' if kind == 'courier' else 'driver', 'currency': 'SAR', 'kind': 'payment',
        'direction': 'outgoing', 'amount': '4.00',
        'allocations': [{'obligation_id': 'shipping:100:base_shipping', 'amount': '4.00'}]})
    replay = calculate(data, first, at=LATER)
    assert replay['engine']['facts'] == first['engine']['facts']
    assert replay['engine']['obligations'] == first['engine']['obligations']
    assert replay['movements'] == first['movements']
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    if kind == 'courier':
        raw['status_changed_at'] = DELIVERED
        raw['shipping']['delivered_at'] = DELIVERED
        raw['shipments'][0]['delivered_at'] = DELIVERED
    else:
        data['store_delivery_assignments'][0]['delivered_at'] = DELIVERED
    enriched = calculate(data, replay, at=LATER)
    assert enriched['engine']['facts'] == first['engine']['facts']
    assert enriched['engine']['obligations'] == first['engine']['obligations']
    assert enriched['movements'] == first['movements']
    assert party(enriched, 'courier' if kind == 'courier' else 'store_driver')['delivered_orders'] == 1


def test_recording_day_uses_riyadh_without_relabeling_it_as_delivery_day():
    result = calculate(undated(), at='2026-10-07T21:01:00+00:00')
    fee = result['engine']['obligations']['shipping:100:base_shipping']
    assert fee['business_date'] == '2026-10-08'
    assert fee['recorded_at'] == '2026-10-07T21:01:00+00:00'
    assert fee['delivered_at'] is None
    assert 'occurred_at' not in fee


@pytest.mark.parametrize('status', ['delivered', 'تم التوصيل'])
def test_creation_one_microsecond_after_opening_satisfies_strict_new_order_boundary(status):
    data = undated()
    data['unified_orders'][0]['raw_by_source']['salla_direct'].update(
        created_at='2026-10-05T03:00:00.000001+03:00', status=status)
    result = calculate(data)
    assert fee_total(result) == 10
    assert result['engine']['facts']['delivery_event:100']['delivered_at'] is None


def test_real_collection_clock_does_not_get_replaced_by_recording_time():
    data = undated('native_driver', cash_proof=True)
    data['store_delivery_collections'][0]['collected_at'] = LATER
    result = calculate(data)
    assert fee_total(result) == 10
    assert 'cod_collection:100' not in result['engine']['facts']
    detail = party(result, 'store_driver')
    assert detail['delivered_orders'] == detail['pending_orders'] == 1
    assert any(reason['code'] == 'driver_collection_custody_contract_invalid'
               for pending in detail['pending_order_details'] for reason in pending['reasons'])


def test_missing_source_version_clock_does_not_become_an_optional_delivery_time():
    data = undated()
    source = data['unified_orders'][0]
    source['g47_salla_snapshot'] = {'source_updated_at': NOW, 'requires_authoritative_refresh': False}
    source['raw_by_source']['salla_direct'].pop('updated_at')
    result = calculate(data)
    assert fee_total(result) == 0
    assert 'delivery_event:100' not in result['engine']['facts']


@pytest.mark.parametrize('kind', ['courier', 'native_driver', 'contract_driver'])
@pytest.mark.parametrize('at,reason', [(START, 'operational_delivery_timestamp_before_order'),
                                     (LATER, 'operational_delivery_event_in_future')])
def test_present_contradictory_delivery_date_is_not_treated_as_missing(kind, at, reason):
    data = undated(kind)
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    if kind == 'courier':
        raw['shipping']['delivered_at'] = raw['shipments'][0]['delivered_at'] = at
    else:
        raw['status_changed_at'] = at
    result = calculate(data)
    assert fee_total(result) == 0
    assert 'delivery_event:100' not in result['engine']['facts']
    assert any(issue['code'] == reason for issue in result['engine']['issues'])


def test_conflicting_present_native_shipment_times_stay_pending():
    data = undated()
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    raw['shipping']['delivered_at'] = DELIVERED
    raw['shipments'][0]['actual_delivered_at'] = NOW
    result = calculate(data)
    assert fee_total(result) == 0
    assert any(i['code'] == 'operational_delivery_timestamp_ambiguous' for i in result['engine']['issues'])


@pytest.mark.parametrize('kind', ['courier', 'native_driver', 'contract_driver'])
@pytest.mark.parametrize('at,reason', [(DELIVERED, None), (NOW, 'operational_delivery_timestamp_ambiguous'),
                                     (LATER, 'operational_delivery_event_in_future')])
def test_negative_local_history_is_older_than_the_current_positive_source(kind, at, reason):
    data = undated(kind)
    data['order_activity_events_v2'] = [row(order_number='10', provider_order_id='100', source='salla_history',
        source_event_id='negative', native_status_transition=[{'delivered': False, 'at': at}])]
    result = calculate(data)
    if reason:
        assert fee_total(result) == 0
        assert 'delivery_event:100' not in result['engine']['facts']
        assert any(i['code'] == reason for i in result['engine']['issues'])
    else:
        assert result['engine']['obligations']['shipping:100:base_shipping']['confirmed'] == '10.00'
        assert result['engine']['facts']['delivery_event:100']['delivered_at'] is None


def test_undated_driver_history_reads_are_bounded_and_dated_driver_selection_is_unchanged():
    from operational_courier_transitions import load_delivery_history
    sources = [{'user_id': 'owner', 'raw_by_source': {'salla_direct': {
        'id': str(index), 'reference_id': str(index + 1000), 'status': 'delivered', 'created_at': CREATED}}}
        for index in range(1, 252)]
    db = HardBoundaryDB({'order_activity_events_v2': []})
    history = run(load_delivery_history(db, 'owner', sources, max_rows=20000,
        status_order_numbers={str(index + 1000) for index in range(1, 252)}))
    assert history == {}
    assert db.reads == ['order_activity_events_v2', 'order_activity_events_v2']
    for has_date in (True, False):
        data = fixture() if has_date else undated('native_driver')
        assert 'salla_shipping_current' not in data['unified_orders'][0]
        scoped = HardBoundaryDB(data)
        run(collect_sources(scoped, 'owner', START, NOW, operational_state=active()))
        assert scoped.reads.count('order_activity_events_v2') == (0 if has_date else 1)


@pytest.mark.parametrize('kind', ['courier', 'native_driver'])
def test_later_actual_time_cannot_claim_delivery_after_its_already_recorded_status(kind):
    data = undated(kind)
    first = calculate(data)
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    raw['updated_at'] = LATER
    if kind == 'courier':
        raw['status_changed_at'] = LATER
    else:
        data['store_delivery_assignments'][0]['delivered_at'] = LATER
    result = calculate(data, first, at=LATER)
    assert result['engine']['facts'] == first['engine']['facts']
    assert result['engine']['obligations'] == first['engine']['obligations']
    assert fee_total(result) == 10
    assert any(i['code'] == 'operational_delivery_confirmed_evidence_changed' for i in result['engine']['issues'])


def test_unknown_delivery_time_requires_an_unambiguous_tariff_for_the_whole_possible_period():
    data = undated()
    contracts = data['mz2_shipping_setup_v2'][0]['contracts']
    boundary = '2026-10-06T08:00:00+00:00'
    contracts[0]['effective_to'] = boundary
    contracts.append({**contracts[0], 'id': 'new-price', 'delivery_fee': '25',
                      'effective_from': boundary, 'effective_to': None})
    result = calculate(data)
    assert fee_total(result) == 0
    assert 'shipping:100:base_shipping' not in result['engine']['obligations']
    courier = party(result, 'courier')
    assert courier['delivered_orders'] == 1 and courier['confirmed_orders'] == 0
    assert courier['outstanding_payable'] is None
    reasons = {reason['code'] for item in courier['pending_order_details'] for reason in item['reasons']}
    assert 'shipping_delivery_tariff_period_unproven' in reasons
    assert 'operational_delivery_timestamp_missing' not in reasons
    view = table(result, 'couriers')
    assert view['totals']['delivered_orders'] == 1
    assert any(c['key'] == 'delivered_orders' and c['label'] == 'تواصيل مثبتة' for c in view['columns'])
    delivery = build_financial_position(result)['currencies']['SAR']['deliveries']['rows'][0]
    assert delivery['shipping_confirmed'] is None
    assert delivery['recognition_basis'] == STATUS_BASIS and delivery['delivered_at'] is None


@pytest.mark.parametrize('amount', ['0.00', '10.00'])
def test_delivery_count_deduplicates_event_fee_and_native_earning_even_for_zero_fee(amount):
    data = undated('native_driver', cash_proof=True)
    data['store_delivery_assignments'][0]['delivery_fee_snapshot'] = amount
    data['store_delivery_driver_earnings'][0]['amount'] = amount
    result = calculate(data)
    assert result['engine']['obligations']['shipping:100:base_shipping']['confirmed'] == amount
    assert party(result, 'store_driver')['delivered_orders'] == 1
    assert table(result, 'drivers')['totals']['delivered_orders'] == 1
    assert result['engine']['facts']['cod_collection:100']['amount'] == '200.00'
    assert result['engine']['facts']['native_driver_earning:100']['earned_at'] == DELIVERED
    assert result['engine']['facts']['native_driver_earning:100']['delivered_at'] is None


@pytest.mark.parametrize('posted,fee', [(START, '10.00'), (DELIVERED, None), (NOW, None)])
def test_native_payout_overlap_uses_original_earned_at_not_later_recorded_at(posted, fee):
    data = undated('native_driver')
    data['store_delivery_driver_settlements'] = [row(id='payout', driver_id='driver', status='posted',
        posting_scope='operational_balance', settlement_type='earning_payment', accounting_status='operational_only',
        financial_source='store_delivery_operational', financial_handoff_status='pending_mz2_driver_balance_link',
        driver_decision_by='driver', driver_decision='approved', amount='15', delivery_fee_settled_amount='15',
        posted_at=posted, driver_decision_at=posted, created_at=START)]
    result = calculate(data)
    obligation = result['engine']['obligations'].get('shipping:100:base_shipping')
    assert (obligation['confirmed'] if obligation else None) == fee
    assert party(result, 'store_driver')['delivered_orders'] == 1
    if fee is None:
        assert any(i['code'] == 'driver_fee_payout_review_required' for i in result['engine']['issues'])
    assert result['movements'] == []


@pytest.mark.parametrize('fault', ['missing-earning-time', 'duplicate-earning', 'wrong-driver', 'amount-conflict'])
def test_native_identity_amount_and_earning_proof_remain_independent_requirements(fault):
    data = undated('native_driver')
    earning = data['store_delivery_driver_earnings'][0]
    if fault == 'missing-earning-time':
        earning.pop('earned_at')
    elif fault == 'duplicate-earning':
        data['store_delivery_driver_earnings'].append(deepcopy(earning))
    elif fault == 'wrong-driver':
        earning['driver_id'] = 'another-driver'
    else:
        earning['amount'] = '11.00'
    result = calculate(data)
    assert fee_total(result) == 0
    assert party(result, 'store_driver')['delivered_orders'] == 1
    assert any(i['code'] in {'driver_fee_evidence_conflict', 'driver_fee_amount_conflict'} for i in result['engine']['issues'])
    assert not any(i['code'] == 'operational_delivery_timestamp_missing' for i in result['engine']['issues'])


def test_status_proof_cannot_be_replayed_as_a_prestart_or_fabricated_dated_event():
    data = undated()
    sources = run(collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=active()))
    for changed in ({'order_created_at': START}, {'delivered_at': DELIVERED}, {'recorded_at': CREATED.replace('06T', '05T')}):
        bad = deepcopy(sources)
        bad['orders'][0]['delivery_event'].update(changed)
        result = reconcile(active(), bad, NOW)
        assert fee_total(result) == 0
        assert 'delivery_event:100' not in result['engine']['facts']
        assert any(i['code'] == 'operational_delivery_event_contract_invalid' for i in result['engine']['issues'])


def test_new_order_status_does_not_supply_a_missing_or_shared_executor_identity():
    data = undated()
    data['unified_orders'][0]['salla_shipping_current']['tracking_number'] = None
    result = calculate(data)
    assert fee_total(result) == 0 and 'delivery_event:100' not in result['engine']['facts']
    assert any(i['code'] == 'operational_delivery_shipment_identity_missing' for i in result['engine']['issues'])


def test_legacy_confirmed_shipping_fee_still_supplies_compatible_delivery_count():
    state = active()
    state['engine'] = {'as_of': NOW, 'facts': {}, 'obligations': {'shipping:legacy': {
        'id': 'shipping:legacy', 'kind': 'shipping', 'party_type': 'store_driver', 'party_id': 'driver',
        'currency': 'SAR', 'direction': 'payable', 'confirmed': '12.00', 'expected': '0.00',
        'business_date': '2026-10-06'}}}
    assert party(state, 'store_driver')['delivered_orders'] == 1
    assert party(state, 'store_driver')['confirmed_orders'] == 1


@pytest.mark.parametrize('created,other_gap,expected_reads', [(CREATED, False, 0), (START, False, 1), (CREATED, True, 1)])
def test_acquisition_does_not_download_history_just_to_date_a_qualifying_new_delivery(monkeypatch, created, other_gap, expected_reads):
    from salla_integration import courier_delivery_sync as sync
    from test_operational_balance_courier_acquisition import fixture as native_source
    _, source, _, _ = native_source()
    raw = source['raw_by_source']['salla_direct']
    raw['created_at'] = created
    # Preserve the exact approved carrier binding in every source view.
    raw['shipping'].update(company_code=None, company='iMile للتوصيل')
    raw['shipments'][0]['company'] = {'name': 'iMile للتوصيل'}
    calls = []

    class Orders:
        def __init__(self):
            self.sent = False

        async def find_one_and_update(self, *args, **kwargs):
            if self.sent:
                return None
            self.sent = True
            return deepcopy(source)

    class DB:
        unified_orders = Orders()

    async def history(*args, **kwargs):
        calls.append(args[3])
        raise ValueError('synthetic-unavailable-history')

    monkeypatch.setattr(sync, 'call_salla', history)
    issues = [{'code': 'operational_delivery_timestamp_missing', 'source_id': '100'}]
    if other_gap:
        issues.append({'code': 'operational_delivery_shipment_source_missing', 'source_id': '100'})
    run(sync.acquire_missing(DB(), 'owner', issues, started_at=START))
    assert len(calls) == expected_reads


@pytest.mark.parametrize('kind', ['courier', 'native_driver'])
def test_real_refresh_persists_unknown_delivery_date_once_without_rewriting_sources_or_cash(kind):
    from test_operational_balance_integration import run as mongo_run, started
    from operational_balance_service import refresh
    from operational_balance_store import read

    async def scenario(db):
        await started(db)
        initial = await read(db, 'owner')
        data = undated(kind)
        for collection, documents in data.items():
            if documents:
                await db[collection].insert_many(deepcopy(documents))
        before = {collection: await db[collection].find({}, {'_id': 0}).to_list(100)
                  for collection in data}
        first = await refresh(db, 'owner', clock=NOW)
        assert fee_total(first) == 10
        assert first['engine']['facts']['delivery_event:100']['delivered_at'] is None
        assert first['engine']['facts']['delivery_event:100']['recorded_at'] == NOW
        assert 'cod_collection:100' not in first['engine']['facts']
        second = await refresh(db, 'owner', clock=LATER)
        assert second['engine']['facts'] == first['engine']['facts']
        assert second['engine']['obligations'] == first['engine']['obligations']
        assert second['openings'] == initial['openings']
        assert second['movements'] == initial['movements'] == []
        assert party(second, 'courier' if kind == 'courier' else 'store_driver')['delivered_orders'] == 1
        for collection, documents in before.items():
            assert await db[collection].find({}, {'_id': 0}).to_list(100) == documents
        assert await db.general_ledger.count_documents({}) == 0
        assert await db.mz2_journal_entries.count_documents({}) == 0

    mongo_run(scenario)
