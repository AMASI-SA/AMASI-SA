"""The approved post-start Aramex status policy pays one exact wallet charge."""
from copy import deepcopy

import pytest

from operational_balance_service import report
from operational_aramex_status_shipping import status_reference_conflicts
from operational_courier_tariffs import with_tariffs
from operational_delivery_evidence import _instant
from operational_prepaid_shipping import source_event, apply_events
from test_operational_balance_prepaid_shipping import fixture, calculate
from test_operational_balance_delivery_cutover import START, NOW
from test_operational_completed_shipping_expansion import without_label


@pytest.mark.parametrize('status', ['completed', 'out_for_delivery', 'delivered'])
def test_post_start_current_aramex_status_pays_once_without_inventing_completion(status):
    data, state = fixture(old=False, current=status, at=None)
    result = calculate(data, state)
    movement, = result['movements']
    fact = result['engine']['facts']['prepaid_shipping:100']
    charge = result['engine']['obligations']['shipping-prepaid:100']
    assert fact['recognition_basis'] == 'aramex_status_created_after_start'
    assert fact['identity_scope'] == 'order_status'
    assert fact['observed_order_status'] == status
    assert fact['recorded_at'] == fact['source_updated_at'] == NOW
    assert fact['order_created_at'] == '2026-10-05T01:00:00+00:00'
    assert fact['completed_at'] is None
    assert charge['recognition_event'] == movement['recognition_event'] == 'aramex_status_eligible'
    assert charge['confirmed'] == movement['amount'] == '34.50'
    assert charge['completed_at'] is None and movement['completed_at'] is None
    assert movement['bank_id'] == 'salla-cash' and movement['bank_kind'] == 'cash'
    assert movement['occurred_at'] == NOW and charge['business_date'] == '2026-10-07'
    assert movement['allocations'] == [{'obligation_id': 'shipping-prepaid:100', 'amount': '34.50'}]
    assert 'cod:100' not in result['engine']['obligations']
    assert not any(row['kind'] == 'provider' for row in result['engine']['obligations'].values())
    wallet = next(row for row in report(result)['parties'] if row['party_type'] == 'cash')
    assert wallet['actual'] == '-34.50'
    assert calculate(data, result) == result


@pytest.mark.parametrize('missing', [('shipment_id',), ('tracking_number',), ('shipment_id', 'tracking_number')])
def test_explicit_status_policy_binds_the_order_and_carrier_without_requiring_a_label(missing):
    data, state = fixture(old=False, at=None)
    without_label(data, missing)
    prior = deepcopy(state)
    result = calculate(data, state)
    assert state == prior
    assert len(result['movements']) == 1
    assert result['movements'][0]['amount'] == '34.50'
    assert result['engine']['facts']['prepaid_shipping:100']['identity_scope'] == 'order_status'
    assert result['engine']['facts']['prepaid_shipping:100']['completed_at'] is None
    assert result['openings'] == prior['openings']


@pytest.mark.parametrize('status,canonical', [
    ('in_delivery', 'out_for_delivery'), ('shipping', 'out_for_delivery'), ('delivering', 'out_for_delivery'),
    ('جاري التوصيل', 'out_for_delivery'), ('جاري_التوصيل', 'out_for_delivery'), ('جارٍ التوصيل', 'out_for_delivery'),
    ('قيد التوصيل', 'out_for_delivery'), ('تم التنفيذ', 'completed'), ('تم التوصيل', 'delivered'),
])
def test_native_status_aliases_share_the_explicit_policy(status, canonical):
    data, state = fixture(old=False, current=status, at=None)
    result = calculate(data, state)
    assert result['movements'][0]['amount'] == '34.50'
    assert result['engine']['facts']['prepaid_shipping:100']['observed_order_status'] == canonical


@pytest.mark.parametrize('status', ['pending', 'cancelled', 'returned', 'shipped', 'تم الشحن', 'processing'])
def test_unrequested_current_states_do_not_receive_the_new_charge(status):
    data, state = fixture(old=False, current=status, at=None)
    assert not calculate(data, state)['movements']


@pytest.mark.parametrize('created,eligible', [
    ('2026-10-04T23:59:59.999999+00:00', False), (START, False),
    ('2026-10-05T00:00:00.000001+00:00', True), (NOW, True),
    ('2026-10-07T12:00:00.000001+00:00', False),
])
def test_the_new_policy_has_a_strict_order_creation_boundary(created, eligible):
    data, state = fixture(old=False, at=None)
    data['unified_orders'][0]['raw_by_source']['salla_direct']['created_at'] = created
    assert bool(calculate(data, state)['movements']) is eligible


@pytest.mark.parametrize('failure', ['customized_pending', 'status_conflict', 'mixed_labels', 'stale_watermark',
    'pending_watermark', 'missing_watermark', 'source_before_creation', 'future_source', 'other_carrier',
    'foreign_cash', 'unapproved_tariff', 'duplicate_cash', 'native_driver'])
def test_authority_status_carrier_and_payment_conflicts_never_post(failure):
    data, state = fixture(old=False, at=None)
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    if failure == 'customized_pending':
        raw['status'] = {'slug': 'completed', 'customized': {'slug': 'pending'}}
    elif failure == 'status_conflict':
        source['order_status_slug'] = 'delivered'
    elif failure == 'mixed_labels':
        raw['status'] = {'slug': 'completed', 'name': 'تم التوصيل'}
    elif failure == 'stale_watermark':
        source['g47_salla_snapshot']['source_updated_at'] = START
    elif failure == 'pending_watermark':
        source['g47_salla_snapshot']['component_pending'] = True
    elif failure == 'missing_watermark':
        source.pop('g47_salla_snapshot')
    elif failure in {'source_before_creation', 'future_source'}:
        raw['updated_at'] = START if failure == 'source_before_creation' else '2026-10-08T00:00:00+00:00'
        source['g47_salla_snapshot']['source_updated_at'] = raw['updated_at']
    elif failure == 'other_carrier':
        source['salla_shipping_current'].update(company_code='imile', company_name='iMile')
        raw['shipping'].update(company_code='imile', company='iMile')
        raw['shipments'][0]['company'] = {'code': 'imile'}
        state['courier_tariffs'][0].update(id='imile', name='iMile', keys=['imile', 'iMile'])
    elif failure == 'foreign_cash':
        data['mz2_financial_accounts'][0]['user_id'] = 'another-owner'
    elif failure == 'unapproved_tariff':
        state['courier_tariffs'][0]['confirmed_by'] = 'another-owner'
    elif failure == 'duplicate_cash':
        data['mz2_financial_accounts'].append(deepcopy(data['mz2_financial_accounts'][0]))
    else:
        data['store_delivery_assignments'] = [{'user_id': 'owner', 'order_number': '10', 'order_id': '100',
            'id': 'assignment', 'driver_id': 'driver', 'active': True, 'status': 'assigned'}]
    result = calculate(data, state)
    assert not result['movements']
    assert 'shipping-prepaid:100' not in result['engine']['obligations']


def test_customized_current_delivery_state_overrides_its_completed_parent():
    data, state = fixture(old=False, current='delivering', at=None)
    data['unified_orders'][0]['raw_by_source']['salla_direct']['status'] = {
        'slug': 'completed', 'customized': {'slug': 'in_delivery', 'name': 'جاري التوصيل'}}
    result = calculate(data, state)
    assert result['engine']['facts']['prepaid_shipping:100']['observed_order_status'] == 'out_for_delivery'
    assert result['movements'][0]['amount'] == '34.50'


@pytest.mark.parametrize('conflict', ['root_shipment', 'root_waybill', 'nested_waybill', 'carrier_code',
    'carrier_name', 'superseded', 'inactive', 'multiple_labels'])
def test_optional_labels_do_not_permit_conflicting_present_identity(conflict):
    data, state = fixture(old=False, at=None)
    without_label(data, ('tracking_number',))
    source = data['unified_orders'][0]
    current = source['salla_shipping_current']
    raw = source['raw_by_source']['salla_direct']
    if conflict == 'root_shipment':
        raw['salla_shipment_id'] = 'another-shipment'
    elif conflict == 'root_waybill':
        current['tracking_number'] = 'known-waybill'
        raw['tracking_number'] = 'another-waybill'
    elif conflict == 'nested_waybill':
        current['tracking_number'] = 'known-waybill'
        raw['shipping']['shipment'] = {'id': 'shipment', 'tracking_number': 'another-waybill'}
    elif conflict == 'carrier_code':
        raw['shipping']['company_code'] = 'imile'
    elif conflict == 'carrier_name':
        raw['shipping']['company'] = 'iMile'
    elif conflict == 'superseded':
        current['superseded_shipment_ids'] = ['old-shipment']
    elif conflict == 'inactive':
        raw['shipping']['status'] = 'cancelled'
    else:
        raw['shipments'] = [{'id': 'shipment', 'company': {'code': 'aramex'}},
                            {'id': 'another-shipment', 'company': {'code': 'aramex'}}]
    result = calculate(data, state)
    assert not result['movements']


@pytest.mark.parametrize('name', ['Aramex International', 'أرامكس', 'ارامكس'])
@pytest.mark.parametrize('label', ['full', 'partial', 'absent'])
def test_approved_aramex_code_accepts_native_company_name_variants(name, label):
    data, state = fixture(old=False, at=None)
    if label != 'full':
        without_label(data, ('tracking_number',) if label == 'partial' else ('shipment_id', 'tracking_number'))
    data['unified_orders'][0]['raw_by_source']['salla_direct']['shipping']['company'] = name
    assert calculate(data, state)['movements'][0]['amount'] == '34.50'


@pytest.mark.parametrize('shape', ['named_current', 'coded_current'])
def test_code_only_and_name_only_shapes_must_resolve_to_the_same_approved_courier(shape):
    data, state = fixture(old=False, at=None)
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    if shape == 'named_current':
        source['salla_shipping_current'].pop('company_code')
        raw['shipping'].pop('company_code')
    else:
        source['salla_shipping_current'].pop('company_name')
        raw['shipping'].pop('company_code')
        raw['shipments'][0]['company'] = {'name': 'Aramex'}
    result = calculate(data, state)
    assert result['movements'][0]['amount'] == '34.50'


def test_customer_currency_does_not_change_the_approved_sar_shipping_contract():
    data, state = fixture(old=False, at=None)
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    raw['currency'] = 'USD'
    result = calculate(data, state)
    charge = result['engine']['obligations']['shipping-prepaid:100']
    movement, = result['movements']
    assert charge['currency'] == movement['currency'] == 'SAR'
    assert charge['confirmed'] == movement['amount'] == '34.50'
    assert movement['bank_id'] == 'salla-cash'


@pytest.mark.parametrize('problem', ['future', 'before_creation', 'ambiguous'])
def test_known_invalid_completion_is_never_replaced_by_a_status_fallback(problem):
    data, state = fixture(old=False, at=None)
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    if problem == 'future':
        raw['status_changed_at'] = '2026-10-08T00:00:00+00:00'
    elif problem == 'before_creation':
        raw['status_changed_at'] = START
    else:
        raw['status_changed_at'] = '2026-10-06T09:00:00+00:00'
        raw['status'] = {'slug': 'completed', 'changed_at': '2026-10-06T10:00:00+00:00'}
    assert not calculate(data, state)['movements']


@pytest.mark.parametrize('second_current', [False, True])
def test_shared_raw_and_current_references_across_orders_block_both_new_charges(second_current):
    data, state = fixture(old=False, at=None)
    without_label(data)
    first = data['unified_orders'][0]
    first['raw_by_source']['salla_direct']['tracking_number'] = 'shared-waybill'
    second = deepcopy(first)
    second.update(salla_order_id='200', order_number='20')
    second['raw_by_source']['salla_direct'].update(id='200', reference_id='20')
    if second_current:
        second['salla_shipping_current']['tracking_number'] = 'shared-waybill'
        second['raw_by_source']['salla_direct'].pop('tracking_number')
        second['raw_by_source']['salla_direct']['shipping']['tracking_number'] = 'shared-waybill'
    data['unified_orders'].append(second)
    result = calculate(data, state)
    assert not result['movements']
    held = [row for row in result['engine']['issues'] if row['code'] == 'operational_prepaid_shipping_shipment_shared_by_orders']
    assert {row['source_id'] for row in held} == {'100', '200'}


def test_present_raw_reference_is_frozen_and_still_protects_a_later_order():
    data, state = fixture(old=False, at=None)
    without_label(data)
    first = data['unified_orders'][0]
    first['raw_by_source']['salla_direct']['tracking_number'] = 'original-waybill'
    saved = calculate(data, state)
    fact = saved['engine']['facts']['prepaid_shipping:100']
    assert fact['waybill'] == 'original-waybill' and fact['shipment_id'] is None
    second = deepcopy(first)
    second.update(salla_order_id='200', order_number='20')
    second['raw_by_source']['salla_direct'].update(id='200', reference_id='20')
    first['raw_by_source']['salla_direct'].pop('tracking_number')
    data['unified_orders'].append(second)
    result = calculate(data, saved)
    assert result['movements'] == saved['movements']
    assert result['engine']['facts']['prepaid_shipping:100'] == fact
    assert 'prepaid_shipping:200' not in result['engine']['facts']


def test_explicit_accounting_time_selects_and_freezes_its_approved_tariff_and_wallet_once():
    data, state = fixture(old=False, at=None)
    later = deepcopy(state['courier_tariffs'][0])
    later.update(version_id='later-approved', effective_from='2026-10-06T00:00:00+00:00')
    later['terms']['cost_per_order'] = '40.00'
    state['courier_tariffs'].append(later)
    saved = calculate(data, state)
    assert saved['movements'][0]['amount'] == '46.00'
    assert saved['movements'][0]['occurred_at'] == NOW
    assert saved['engine']['facts']['prepaid_shipping:100']['tariff_version_id'] == 'later-approved'
    for status in ('out_for_delivery', 'delivered', 'cancelled'):
        data['unified_orders'][0]['raw_by_source']['salla_direct']['status'] = status
        data['unified_orders'][0]['order_status_slug'] = status
        before = deepcopy(saved['engine']['facts']['prepaid_shipping:100'])
        replay = calculate(data, saved)
        assert replay['movements'] == saved['movements']
        assert replay['engine']['facts']['prepaid_shipping:100'] == before


@pytest.mark.parametrize('change', ['basis', 'completed_at', 'status', 'start', 'source_future', 'recorded_past',
    'carrier', 'party', 'scope', 'amount'])
def test_apply_boundary_rejects_corrupted_status_evidence_atomically(change):
    data, state = fixture(old=False, at=None)
    data.setdefault('store_delivery_assignments', [])
    setup, conflicts = with_tariffs({}, 'owner', state)
    assert not conflicts
    source = data['unified_orders'][0]
    _, event = source_event(data, setup, source, source['raw_by_source']['salla_direct'], state,
        'owner', _instant(START), _instant(NOW), (), (),
        status_shipment_conflicts=status_reference_conflicts(data['unified_orders'], state))
    key, value = {
        'basis': ('recognition_basis', 'unapproved_basis'), 'completed_at': ('completed_at', NOW),
        'status': ('observed_order_status', 'cancelled'), 'start': ('order_created_at', START),
        'source_future': ('source_updated_at', '2026-10-08T00:00:00+00:00'),
        'recorded_past': ('recorded_at', START), 'carrier': ('source_carrier_key', 'imile'),
        'party': ('party_id', 'imile'), 'scope': ('identity_scope', 'unknown'), 'amount': ('amount', '1.00'),
    }[change]
    event[key] = value
    state['engine'].update(facts={}, obligations={})
    before = deepcopy(state)
    issues = []
    apply_events(state, [event], _instant(START), _instant(NOW), lambda code, oid: issues.append((code, oid)))
    assert state == before
    assert len(issues) == 1


def sparse_order_fixture(status='completed'):
    data, state = fixture(old=False, current=status, at=None)
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    raw.pop('shipping')
    raw.pop('shipments')
    raw.update(delivery_method='shipping', shipping_status='delivering')
    source['salla_shipping_current'].update(source_kind='shipment', company_code='synthetic-aramex-code',
        company_name='أرامكس - شحن دولي', provider_updated_at=NOW, shipment_updated_at=NOW)
    state['courier_tariffs'][0].update(keys=['synthetic-aramex-code'],
        identity_match='exact', salla_carrier_key='synthetic-aramex-code')
    return data, state


@pytest.mark.parametrize('status', ['completed', 'out_for_delivery', 'delivered'])
def test_trusted_separate_current_shipment_does_not_require_a_duplicate_order_envelope(status):
    data, state = sparse_order_fixture(status)
    saved = calculate(data, state)
    movement, = saved['movements']
    fact = saved['engine']['facts']['prepaid_shipping:100']
    assert movement['amount'] == '34.50' and movement['bank_id'] == 'salla-cash'
    assert fact['completed_at'] is None and fact['recorded_at'] == NOW
    assert (fact['shipment_id'], fact['waybill']) == ('shipment', 'TEST-AWB')
    assert fact['source_carrier_key'] == 'synthetic-aramex-code'
    assert calculate(data, saved)['movements'] == saved['movements']


@pytest.mark.parametrize('shape', ['carrier', 'shipment_id', 'waybill', 'shipment_without_awb', 'root_references'])
def test_sparse_present_raw_evidence_can_agree_without_repeating_the_complete_current_label(shape):
    data, state = sparse_order_fixture()
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    if shape == 'carrier':
        raw['shipping'] = {'company_code': 'synthetic-aramex-code', 'company': 'Aramex International'}
    elif shape == 'shipment_id':
        raw['shipping'] = {'shipment_id': 'shipment'}
    elif shape == 'waybill':
        raw['shipping'] = {'tracking_number': 'TEST-AWB'}
    elif shape == 'shipment_without_awb':
        raw['shipments'] = [{'id': 'shipment', 'company': {'code': 'synthetic-aramex-code'}}]
    else:
        raw.update(salla_shipment_id='shipment', tracking_number='TEST-AWB')
    assert calculate(data, state)['movements'][0]['amount'] == '34.50'


@pytest.mark.parametrize('conflict', ['raw_carrier', 'raw_waybill', 'raw_shipment', 'raw_inactive',
    'native_future', 'current_missing', 'current_unapproved', 'current_source_kind', 'current_inactive',
    'current_future', 'stale_canonical'])
def test_sparse_order_does_not_erase_present_contradictions_or_current_authority(conflict):
    data, state = sparse_order_fixture()
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    if conflict == 'raw_carrier':
        raw['shipping_company'] = 'iMile'
    elif conflict == 'raw_waybill':
        raw['tracking_number'] = 'another-waybill'
    elif conflict == 'raw_shipment':
        raw['salla_shipment_id'] = 'another-shipment'
    elif conflict == 'raw_inactive':
        raw['shipping_status'] = 'cancelled'
    elif conflict == 'native_future':
        raw['shipments'] = [{'id': 'shipment', 'created_at': '2026-10-08T00:00:00+00:00'}]
    elif conflict == 'current_missing':
        source.pop('salla_shipping_current')
    elif conflict == 'current_unapproved':
        source['salla_shipping_current']['company_code'] = 'another-code'
    elif conflict == 'current_source_kind':
        source['salla_shipping_current']['source_kind'] = 'display_guess'
    elif conflict == 'current_inactive':
        source['salla_shipping_current']['status'] = 'cancelled'
    elif conflict == 'current_future':
        source['salla_shipping_current']['shipment_updated_at'] = '2026-10-08T00:00:00+00:00'
    else:
        source['g47_salla_snapshot']['requires_authoritative_refresh'] = True
    assert not calculate(data, state)['movements']


def test_independent_current_shipment_clock_can_follow_the_order_clock():
    data, state = sparse_order_fixture()
    source = data['unified_orders'][0]
    source['raw_by_source']['salla_direct']['updated_at'] = '2026-10-07T09:00:00+00:00'
    source['g47_salla_snapshot']['source_updated_at'] = '2026-10-07T09:00:00+00:00'
    assert calculate(data, state)['movements'][0]['amount'] == '34.50'
