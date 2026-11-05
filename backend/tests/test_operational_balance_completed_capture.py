"""A verified order transition survives a label created after its webhook."""
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import json

from mongomock_motor import AsyncMongoMockClient
import pytest

from operational_courier_transitions import COMPLETED_PROOF
import operational_completed_capture as reader
from operational_order_completed_evidence import completion_time, verified_completed_webhook
from salla_integration import courier_delivery_sync as sync
from test_operational_balance_prepaid_shipping import COMPLETED, NOW, START, calculate, fixture


CAPTURES = 'salla_webhook_event_captures'
MERCHANT = '1000'
LABEL_AT = '2026-10-06T09:00:30+00:00'
END = datetime.fromisoformat(NOW)
NATIVE_PATHS = ('payload.data.id', 'payload.data.order_id', 'payload.data.salla_order_id',
                'payload.data.order.id', 'payload.data.order.order_id', 'payload.data.order.salla_order_id',
                'payload.order_id', 'payload.salla_order_id')


def capture(*, at=COMPLETED):
    payload = {'event': 'order.status.updated', 'merchant': int(MERCHANT), 'created_at': at,
               'data': {'id': 100, 'reference_id': 10,
                        'status': {'slug': 'completed', 'name': 'تم التنفيذ'}}}
    return rehash({'merchant_id': MERCHANT, 'event': 'order.status.updated',
                   'verified_before_capture': True, 'payload': payload,
                   'first_received_at': datetime.fromisoformat(NOW)})


def rehash(row):
    row['event_hash'] = hashlib.sha256(json.dumps(row['payload'], ensure_ascii=False,
        sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
    return row


def current_fixture():
    data, state = fixture(current='delivering', at=None)
    source = data['unified_orders'][0]
    source['raw_by_source']['salla_direct']['shipments'][0]['created_at'] = LABEL_AT
    return data, state


async def seed(source, captures=None, integrations=None):
    db = AsyncMongoMockClient()['synthetic_completed_capture']
    await db.unified_orders.insert_one(deepcopy(source))
    for row in captures if captures is not None else [capture()]:
        await db[CAPTURES].insert_one(deepcopy(row))
    for row in integrations if integrations is not None else [
            {'user_id': 'owner', 'store_id': int(MERCHANT), 'status': 'connected'}]:
        await db.salla_integrations.insert_one(deepcopy(row))
    return db


async def native_activity(_db, owner, method, path, *, params):
    assert owner == 'owner' and method == 'GET' and path == '/orders/100/histories'
    assert params == {'page': 1, 'per_page': 100}
    return {'success': True, 'data': [{'id': 'synthetic-activity', 'type': 'activity',
        'status': 'تم التنفيذ', 'customized': {'slug': 'completed', 'name': 'تم التنفيذ'},
        'note': '', 'created_at': COMPLETED, 'created_by': {'id': 'synthetic-app', 'name': 'App'}}],
        'pagination': {'total': 1, 'totalPages': 1, 'currentPage': 1}}


def issue():
    return {'source_id': '100', 'code': 'operational_prepaid_shipping_completion_timestamp_missing'}


def raw(source):
    return source['raw_by_source']['salla_direct']


def put(obj, path, value):
    keys = path.split('.')
    for key in keys[:-1]:
        obj = obj.setdefault(key, {})
    obj[keys[-1]] = value


def recover(source, captures=None, integrations=None, *, load_shipments=None):
    async def scenario():
        db = await seed(source, captures, integrations)
        result = await reader.recover_completed_capture(db, 'owner', source, END,
                                                        load_shipments=load_shipments)
        return result[0] if result else None
    return asyncio.run(scenario())


def attach(data, **kwargs):
    source = data['unified_orders'][0]
    source['salla_shipping_current'] = recover(source, **kwargs)
    return source['salla_shipping_current'][COMPLETED_PROOF]


def detached_fixture():
    data, state = current_fixture()
    source = data['unified_orders'][0]
    raw(source).pop('shipping')
    raw(source).pop('shipments')
    raw(source).update(shipping_status='shipping_ready', delivery_method=None)
    return data, state


def shipment():
    return {'id': 'shipment', 'order_id': 100, 'order_reference_id': 10,
            'type': 'shipment', 'status': 'delivering', 'courier_id': 'aramex',
            'courier_name': 'Aramex', 'tracking_number': 'TEST-AWB', 'created_at': LABEL_AT}


def loader(rows):
    async def load():
        return deepcopy(rows)
    return load


def test_verified_capture_before_label_recovers_one_completed_debit(monkeypatch):
    data, state = current_fixture()
    source = data['unified_orders'][0]
    monkeypatch.setattr(sync, 'call_salla', native_activity)

    async def scenario():
        db = await seed(source)
        await sync.acquire_missing(db, 'owner', [issue()], started_at=START)
        return await db.unified_orders.find_one({'user_id': 'owner'}, {'_id': 0})

    data['unified_orders'][0] = asyncio.run(scenario())
    result = calculate(data, state)
    assert [row['amount'] for row in result['movements']] == ['34.50']
    assert result['engine']['facts']['prepaid_shipping:100']['completed_at'] == COMPLETED
    assert result['engine']['obligations']['shipping-prepaid:100']['confirmed'] == '34.50'
    assert result['openings'] == state['openings']
    assert calculate(data, result) == result


def test_original_webhook_without_label_does_not_invent_later_completion():
    data, _ = detached_fixture()
    source = data['unified_orders'][0]
    original = {**raw(source), 'status': 'completed', 'updated_at': COMPLETED}
    assert verified_completed_webhook(capture()['payload'], original, source['salla_shipping_current'], 'owner') is None
    assert verified_completed_webhook(capture()['payload'], raw(source), source['salla_shipping_current'], 'owner') is None
    with pytest.raises(ValueError, match='shipment_source_missing'):
        recover(source)  # Receipt and current projection alone are not shipment proof.


@pytest.mark.parametrize('path', NATIVE_PATHS)
def test_every_native_identity_path_is_queried_and_conflicting_captures_are_not_hidden(path):
    data, state = current_fixture()
    source = data['unified_orders'][0]
    row = capture()
    row['payload']['data'].pop('id')
    put(row, path, 100)
    rehash(row)
    source['salla_shipping_current'] = recover(source, [row])
    assert len(calculate(data, state)['movements']) == 1
    second = deepcopy(row)
    second['payload']['created_at'] = '2026-10-06T09:01:00+00:00'
    rehash(second)
    with pytest.raises(ValueError, match='completion_ambiguous'):
        recover(current_fixture()[0]['unified_orders'][0], [capture(), second])


@pytest.mark.parametrize('path', NATIVE_PATHS)
def test_conflicting_native_alias_in_one_capture_cannot_be_hidden_by_another(path):
    row = capture()
    # Keep an independent matching indexed path even when data.id is modified.
    row['payload']['data']['order_id'] = 100
    put(row, path, 999)
    rehash(row)
    with pytest.raises(ValueError, match='identity_conflict'):
        recover(current_fixture()[0]['unified_orders'][0], [row])


@pytest.mark.parametrize(('path', 'value', 'reason'), [
    ('verified_before_capture', False, 'unverified'),
    ('verified_before_capture', 1, 'unverified'),
    ('payload.event', 'order.updated', 'unverified'),
    ('demo_store_cleanup.applied', True, 'unverified'),
    ('payload.merchant', 2000, 'identity_conflict'),
    ('payload.data.store_id', 2000, 'identity_conflict'),
    ('payload.data.order.merchant_id', 2000, 'identity_conflict'),
    ('payload.data.order.reference_id', 99, 'identity_conflict'),
    ('payload.order_number', '99', 'identity_conflict'),
    ('payload.data.order', 'malformed', 'identity_conflict'),
    ('payload.data.order_id', True, 'identity_conflict'),
    ('payload.data.to_status', 'processing', 'target_conflict'),
    ('payload.new_status', 'processing', 'target_conflict'),
    ('payload.data.order.status', 'processing', 'target_conflict'),
    ('payload.data.status', {'slug': 'completed', 'name': 'قيد المعالجة'}, 'target_conflict'),
    ('payload.data.status', {}, 'target_conflict'),
    ('payload.created_at', None, 'time_invalid'),
    ('payload.created_at', '2026-10-06T09:00:00', 'time_invalid'),
    ('payload.created_at', {'date': '2026-10-06 09:00:00'}, 'time_invalid'),
    ('payload.created_at', '2026-10-01T08:59:59+00:00', 'time_invalid'),
    ('payload.created_at', '2026-10-07T12:00:01+00:00', 'time_invalid'),
    ('payload.data.date', '2026-10-06T09:01:00+00:00', 'order_creation_conflict'),
])
def test_invalid_capture_never_supplies_completion(path, value, reason):
    row = capture()
    put(row, path, value)
    rehash(row)
    with pytest.raises(ValueError, match=reason):
        recover(current_fixture()[0]['unified_orders'][0], [row])


@pytest.mark.parametrize('change', ['payload', 'hash', 'secret'])
def test_hash_is_checked_against_the_original_sanitized_payload(change):
    row = capture()
    if change == 'payload':
        row['payload']['created_at'] = '2026-10-06T10:00:00+00:00'
    elif change == 'hash':
        row['event_hash'] = '0' * 64
    else:
        row['payload']['token'] = 'synthetic-unredacted-secret'
        rehash(row)
    with pytest.raises(ValueError, match='hash_mismatch'):
        recover(current_fixture()[0]['unified_orders'][0], [row])


@pytest.mark.parametrize('change', ['reference_only', 'wrong_native', 'wrong_metadata_event', 'wrong_metadata_store'])
def test_outside_native_selector_scope_cannot_supply_proof(change):
    row = capture()
    if change == 'reference_only':
        row['payload']['data'].pop('id')
    elif change == 'wrong_native':
        row['payload']['data']['id'] = 999
    elif change == 'wrong_metadata_event':
        row['event'] = 'order.updated'
    else:
        row['merchant_id'] = '2000'
    rehash(row)
    assert recover(current_fixture()[0]['unified_orders'][0], [row]) is None


@pytest.mark.parametrize('stamp', [
    '2026-10-06T12:00:00+03:00',
    {'date': '2026-10-06 12:00:00.000000', 'timezone_type': 3, 'timezone': 'Asia/Riyadh'},
    'Tue Oct 06 2026 12:00:00 GMT+0300',
])
def test_original_timezone_aware_timestamp_is_normalized_without_receipt_fallback(stamp):
    row = capture(at=stamp)
    row['first_received_at'] = datetime(2030, 1, 1)
    current = recover(current_fixture()[0]['unified_orders'][0], [row])
    assert current[COMPLETED_PROOF]['at'] == COMPLETED


def test_full_order_capture_must_match_the_canonical_creation_date():
    data, state = current_fixture()
    source = data['unified_orders'][0]
    row = capture()
    row['payload']['data'].update(date=raw(source)['created_at'], created_at=raw(source)['created_at'])
    rehash(row)
    source['salla_shipping_current'] = recover(source, [row])
    assert len(calculate(data, state)['movements']) == 1


def test_duplicate_receipts_and_distinct_payload_hashes_at_one_time_pay_once():
    data, state = current_fixture()
    second = capture()
    second['payload']['data']['customer'] = {'name': 'Synthetic private customer', 'phone': 'synthetic-only'}
    second['payload']['data']['note'] = 'Synthetic note never persisted in proof'
    rehash(second)
    proof = attach(data, captures=[capture(), capture(), second])
    assert len(proof['captures']) == 2
    serialized = json.dumps(proof)
    for forbidden in ('customer', 'phone', 'note', 'payload', 'received_at', 'history_complete', 'owner_at_capture'):
        assert forbidden not in serialized
    result = calculate(data, state)
    assert len(result['movements']) == 1
    assert calculate(data, result) == result


def test_cap_plus_one_prevents_a_partial_capture_set_from_being_accepted(monkeypatch):
    monkeypatch.setattr(reader, 'MAX_CAPTURES', 2)
    with pytest.raises(ValueError, match='scope_incomplete'):
        recover(current_fixture()[0]['unified_orders'][0], [capture(), capture(), capture()])


@pytest.mark.parametrize('target', ['processing', 'delivering', 'delivered'])
def test_noncompleted_event_after_canonical_version_cannot_be_ignored(target):
    later = capture(at='2026-10-07T12:00:01+00:00')
    later['payload']['data']['status'] = target
    rehash(later)
    with pytest.raises(ValueError, match='time_invalid'):
        recover(current_fixture()[0]['unified_orders'][0], [capture(), later])


def test_current_noncompleted_capture_does_not_erase_the_original_completed_time():
    later = capture(at='2026-10-07T11:00:00+00:00')
    later['payload']['data']['status'] = 'delivering'
    rehash(later)
    current = recover(current_fixture()[0]['unified_orders'][0], [capture(), later])
    assert current[COMPLETED_PROOF]['at'] == COMPLETED


@pytest.mark.parametrize('rows', [
    [{'user_id': 'other', 'store_id': 1000, 'status': 'connected'}],
    [{'user_id': 'owner', 'store_id': 1000, 'status': 'disconnected'}],
    [{'user_id': 'owner', 'status': 'connected'}],
    [{'user_id': 'owner', 'store_id': 1000, 'status': 'connected'},
     {'user_id': 'owner', 'store_id': 2000, 'status': 'connected'}],
    [{'user_id': 'owner', 'store_id': 1000, 'status': 'connected'},
     {'user_id': 'other', 'store_id': '1000', 'status': 'connected'}],
])
def test_no_unique_connected_bidirectional_owner_mapping_means_no_recovery(rows):
    source = current_fixture()[0]['unified_orders'][0]
    try:
        accepted = recover(source, integrations=rows)
    except ValueError as error:
        assert 'binding_' in str(error)
    else:
        assert accepted is None


def test_attribution_pilot_store_is_excluded(monkeypatch):
    monkeypatch.setattr(reader, 'is_attribution_pilot_store', lambda store: store == MERCHANT)
    with pytest.raises(ValueError, match='store_excluded'):
        recover(current_fixture()[0]['unified_orders'][0])


@pytest.mark.parametrize('path', ['merchant', 'merchant_id', 'store_id',
                                 'raw_by_source.salla_direct.merchant',
                                 'raw_by_source.salla_direct.merchant_id',
                                 'raw_by_source.salla_direct.store_id'])
def test_explicit_foreign_store_in_source_is_rejected_at_acquisition_and_consumption(path):
    data, state = current_fixture()
    source = data['unified_orders'][0]
    attach(data)
    put(source, path, 2000)
    assert calculate(data, state)['movements'] == []
    source['salla_shipping_current'].pop(COMPLETED_PROOF)
    with pytest.raises(ValueError, match='identity_conflict'):
        recover(source)


@pytest.mark.parametrize(('path', 'value'), [
    ('source', 'unverified_capture'), ('schema', 'unknown'), ('owner', 'other'),
    ('merchant_id', '2000'), ('order_id', '999'), ('order_number', '99'),
    ('shipment_id', 'different'), ('waybill', 'OTHER-AWB'),
    ('at', '2026-10-06T09:01:00+00:00'), ('target_status', 'delivered'),
    ('source_updated_at', '2026-10-08T00:00:00+00:00'), ('captures', []),
])
def test_sparse_capture_proof_is_validated_again_at_consumption(path, value):
    data, state = current_fixture()
    proof = attach(data)
    put(proof, path, value)
    assert calculate(data, state)['movements'] == []


@pytest.mark.parametrize(('key', 'value'), [('capture_hash', 'bad'), ('event', 'order.created'),
    ('merchant_id', '2000'), ('order_id', '999'), ('order_number', '99'), ('target_status', 'processing'),
    ('at', '2026-10-06T09:01:00+00:00'), ('unexpected', 'value')])
def test_each_stored_capture_record_is_revalidated(key, value):
    data, state = current_fixture()
    proof = attach(data)
    proof['captures'][0][key] = value
    assert calculate(data, state)['movements'] == []


def test_existing_completion_proof_cannot_be_replaced_by_a_conflicting_capture():
    data, _ = current_fixture()
    source = data['unified_orders'][0]
    proof = attach(data)
    assert recover(source)[COMPLETED_PROOF] == proof
    with pytest.raises(ValueError, match='completion_ambiguous'):
        recover(source, [capture(at='2026-10-06T09:01:00+00:00')])


def test_capture_proof_combines_with_explicit_canonical_and_typed_history_times():
    from test_operational_balance_courier_order_transition import history, COLLECTION
    data, state = current_fixture()
    attach(data)
    data[COLLECTION] = [history('2026-10-06T09:01:00+00:00', to_status='completed')]
    assert calculate(data, state)['movements'] == []
    data.pop(COLLECTION)
    source = data['unified_orders'][0]
    raw(source).update(status='completed', status_changed_at='2026-10-06T09:01:00+00:00')
    source['order_status_slug'] = 'completed'
    assert calculate(data, state)['movements'] == []


@pytest.mark.parametrize('failure', ['pending', 'watermark_mismatch', 'source_future', 'source_before_completion'])
def test_stale_source_cannot_consume_capture_proof(failure):
    data, state = current_fixture()
    attach(data)
    source = data['unified_orders'][0]
    if failure == 'pending':
        source['g47_salla_snapshot']['component_pending'] = True
    elif failure == 'watermark_mismatch':
        source['g47_salla_snapshot']['source_updated_at'] = COMPLETED
    else:
        value = '2026-10-08T00:00:00+00:00' if failure == 'source_future' else '2026-10-06T08:59:59+00:00'
        raw(source)['updated_at'] = value
        source['g47_salla_snapshot']['source_updated_at'] = value
    assert calculate(data, state)['movements'] == []


@pytest.mark.parametrize(('at', 'count'), [('2026-10-04T23:59:59+00:00', 0), (START, 1), (COMPLETED, 1)])
def test_original_capture_time_controls_operational_cutover(at, count):
    data, state = current_fixture()
    attach(data, captures=[capture(at=at)])
    assert len(calculate(data, state)['movements']) == count


@pytest.mark.parametrize('native_numeric_carrier', [False, True])
def test_separate_authenticated_shipment_recovers_the_original_shape_and_pays_once(monkeypatch, native_numeric_carrier):
    data, state = detached_fixture()
    source = data['unified_orders'][0]
    native = shipment()
    if native_numeric_carrier:
        source['salla_shipping_current'].update(company_code=None, company_name='aramex')
        native.update(courier_id=501, courier_name='أرامكس الدولي')
    before = deepcopy(raw(source))
    calls = []
    async def provider(_db, owner, method, path, *, params):
        calls.append(path)
        assert owner == 'owner' and method == 'GET' and path == '/shipments'
        assert params == {'order_id': '100', 'page': 1, 'per_page': 100}
        return {'success': True, 'data': [native],
                'pagination': {'total': 1, 'totalPages': 1, 'currentPage': 1}}
    monkeypatch.setattr(sync, 'call_salla', provider)
    async def scenario():
        db = await seed(source)
        await sync.acquire_missing(db, 'owner', [issue()], started_at=START)
        return await db.unified_orders.find_one({'user_id': 'owner'}, {'_id': 0})
    source = data['unified_orders'][0] = asyncio.run(scenario())
    assert calls == ['/shipments']
    assert raw(source) == before
    assert source['order_status_slug'] == 'delivering'
    current = source['salla_shipping_current']
    proof = current[COMPLETED_PROOF]
    assert current['status'] == proof['shipment']['status'] == 'delivering'
    assert raw(source)['shipping_status'] == 'shipping_ready'
    assert proof['at'] == COMPLETED and proof['shipment']['created_at'] == LABEL_AT
    assert set(proof['shipment']) == {'source', 'owner', 'order_id', 'order_number', 'shipment_id', 'waybill',
                                      'company_code', 'company_name', 'status', 'type', 'created_at', 'updated_at'}
    if native_numeric_carrier:
        assert current['company_code'] is None and current['company_name'] == 'aramex'
        assert proof['shipment']['company_code'] == '501'
        assert proof['shipment']['company_name'] == 'أرامكس الدولي'
    result = calculate(data, state)
    assert [row['amount'] for row in result['movements']] == ['34.50']
    assert result['movements'][0]['bank_id'] == 'salla-cash'
    assert calculate(data, result) == result
    assert result['openings'] == state['openings']


@pytest.mark.parametrize(('key', 'value'), [
    ('tracking_number', 'TEST-AWB'), ('shipping_number', 'TEST-AWB'), ('awb', 'TEST-AWB'),
    ('salla_shipment_id', 'shipment'), ('shipment_id', 'shipment'),
    ('tracking', {'number': 'TEST-AWB'}), ('shipping_label_url', 'synthetic-label'),
    ('courier_id', 'aramex'), ('shipping_company', 'Aramex'),
    ('shipment', {'id': 'shipment', 'tracking_number': 'TEST-AWB'}),
    ('shipping', {'company_code': 'aramex'}), ('shipments', 'malformed'),
    ('shipping', False), ('shipment', 0), ('shipments', False), ('tracking_number', 0),
])
def test_partial_or_malformed_raw_shipping_is_not_absence_and_never_authorizes_fetch(key, value):
    source = detached_fixture()[0]['unified_orders'][0]
    raw(source)[key] = value
    assert not reader.raw_waybill_absent(raw(source))
    async def forbidden():
        pytest.fail('A partial canonical identity cannot authorize an independent fallback')
    with pytest.raises(ValueError):
        recover(source, load_shipments=forbidden)


@pytest.mark.parametrize('status', ['cancelled', 'returned', 'تم الإلغاء', 'مسترجع'])
def test_cancelled_raw_shipping_cannot_trigger_fallback(status):
    source = detached_fixture()[0]['unified_orders'][0]
    raw(source)['shipping_status'] = status
    async def forbidden():
        pytest.fail('An invalidated source must be rejected before network acquisition')
    with pytest.raises(ValueError, match='shipment_invalid'):
        recover(source, load_shipments=forbidden)


@pytest.mark.parametrize(('path', 'value'), [
    ('order_id', 999), ('order.id', 999), ('order_reference_id', 99),
    ('order.reference_id', 99), ('merchant_id', 2000), ('order.store_id', 2000),
    ('id', 'replacement'), ('shipment_id', 'replacement'),
    ('tracking_number', 'OTHER-AWB'), ('tracking.number', 'OTHER-AWB'),
    ('type', 'return'), ('status', 'cancelled'), ('returned', True), ('superseded', True),
    ('courier_id', 'different-code'), ('company_code', 'different-code'),
    ('courier.code', 'different-code'), ('created_at', None),
    ('created_at', '2026-10-06T09:00:30'), ('created_at', '2026-10-07T12:00:01+00:00'),
    ('created_at', '2026-09-30T12:00:00+00:00'), ('updated_at', '2026-10-08T00:00:00+00:00'),
])
def test_separate_shipment_must_be_original_identified_active_and_current(path, value):
    source = detached_fixture()[0]['unified_orders'][0]
    native = shipment()
    put(native, path, value)
    with pytest.raises(ValueError):
        recover(source, load_shipments=loader([native]))


@pytest.mark.parametrize('extra', [None, {'id': 'older', 'status': 'cancelled', 'type': 'shipment'},
                                 {'id': 'returned', 'status': 'delivered', 'type': 'return'}])
def test_entire_shipment_result_must_contain_exactly_one_original_before_filtering(extra):
    rows = [] if extra is None else [shipment(), extra]
    with pytest.raises(ValueError, match='shipment_scope_ambiguous'):
        recover(detached_fixture()[0]['unified_orders'][0], load_shipments=loader(rows))


def test_superseded_current_waybill_cannot_be_restored_from_a_separate_list():
    source = detached_fixture()[0]['unified_orders'][0]
    source['salla_shipping_current']['superseded_shipment_ids'] = ['older-shipment']
    with pytest.raises(ValueError, match='shipment_scope_ambiguous'):
        recover(source, load_shipments=loader([shipment()]))


@pytest.mark.parametrize(('path', 'value'), [
    ('source', 'plain_snapshot'), ('owner', 'other'), ('order_id', '999'), ('order_number', '99'),
    ('shipment_id', 'different'), ('waybill', 'OTHER-AWB'), ('company_code', 'different-code'),
    ('type', 'return'), ('status', 'cancelled'), ('created_at', '2026-10-08T00:00:00+00:00'),
    ('updated_at', '2026-10-08T00:00:00+00:00'), ('unexpected', 'value'),
])
def test_separate_shipment_projection_is_checked_again_when_consumed(path, value):
    data, state = detached_fixture()
    proof = attach(data, load_shipments=loader([shipment()]))
    proof['shipment'][path] = value
    assert calculate(data, state)['movements'] == []


@pytest.mark.parametrize('replacement', ['orphan', 'other_awb', 'other_carrier', 'matching'])
def test_later_canonical_shipping_must_validate_independently_of_the_separate_proof(replacement):
    data, state = detached_fixture()
    attach(data, load_shipments=loader([shipment()]))
    source = data['unified_orders'][0]
    if replacement == 'orphan':
        raw(source)['tracking_number'] = 'TEST-AWB'
    else:
        raw(source)['shipments'] = [shipment()]
        if replacement == 'other_awb':
            raw(source)['shipments'][0]['tracking_number'] = 'OTHER-AWB'
        elif replacement == 'other_carrier':
            raw(source)['shipments'][0]['courier_id'] = 'different-code'
    assert len(calculate(data, state)['movements']) == (1 if replacement == 'matching' else 0)


@pytest.mark.parametrize('pagination', [None, {'total': 2, 'totalPages': 1, 'currentPage': 1},
    {'total': 101, 'totalPages': 2, 'currentPage': 1}, {'total': 1, 'totalPages': 1, 'currentPage': 2}])
def test_authenticated_but_truncated_shipment_page_cannot_supply_proof(monkeypatch, pagination):
    data, state = detached_fixture()
    source = data['unified_orders'][0]
    async def provider(_db, owner, method, path, *, params):
        assert path == '/shipments'
        return {'success': True, 'data': [shipment()], 'pagination': pagination}
    monkeypatch.setattr(sync, 'call_salla', provider)
    async def scenario():
        db = await seed(source)
        await sync.acquire_missing(db, 'owner', [issue()], started_at=START)
        return await db.unified_orders.find_one({'user_id': 'owner'}, {'_id': 0})
    data['unified_orders'][0] = asyncio.run(scenario())
    assert COMPLETED_PROOF not in data['unified_orders'][0]['salla_shipping_current']
    assert calculate(data, state)['movements'] == []


@pytest.mark.parametrize('detached', [False, True])
def test_later_delivery_and_replayed_capture_preserve_one_original_debit(detached):
    from salla_shipping import accept_shipping
    data, state = detached_fixture() if detached else current_fixture()
    source = data['unified_orders'][0]
    attach(data, load_shipments=loader([shipment()]) if detached else None)
    first = calculate(data, state)
    current = source['salla_shipping_current']
    update = {key: value for key, value in current.items() if key != COMPLETED_PROOF}
    update.update(status='delivered', provider_updated_at=NOW)
    source['salla_shipping_current'] = accept_shipping(source, update)
    raw(source).update(status='delivered', status_changed_at=NOW)
    source['order_status_slug'] = 'delivered'
    if not detached:
        for row in [raw(source)['shipping'], *raw(source)['shipments']]:
            row.update(status='delivered', delivered_at=NOW)
    assert source['salla_shipping_current'][COMPLETED_PROOF]['at'] == COMPLETED
    after = calculate(data, first)
    assert after['movements'] == first['movements']
    assert after['engine']['facts']['prepaid_shipping:100'] == first['engine']['facts']['prepaid_shipping:100']
    assert recover(source)[COMPLETED_PROOF] == current[COMPLETED_PROOF]
    assert calculate(data, after) == after


@pytest.mark.parametrize('change', ['source_revision', 'source_owner', 'source_store', 'current_waybill',
    'binding_store', 'binding_disconnected', 'binding_reverse_duplicate', 'binding_replaced'])
def test_source_and_bidirectional_binding_are_fenced_across_shipment_read(monkeypatch, change):
    source = detached_fixture()[0]['unified_orders'][0]
    async def scenario():
        db = await seed(source)
        async def provider(_db, owner, method, path, *, params):
            assert path == '/shipments'
            if change == 'source_revision':
                await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
                    'raw_by_source.salla_direct.updated_at': '2026-10-07T12:00:01+00:00',
                    'g47_salla_snapshot.source_updated_at': '2026-10-07T12:00:01+00:00'}})
            elif change == 'source_owner':
                await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {'user_id': 'other'}})
            elif change == 'source_store':
                await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {'store_id': '2000'}})
            elif change == 'current_waybill':
                await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
                    'salla_shipping_current.tracking_number': 'NEW-AWB'}})
            elif change == 'binding_store':
                await db.salla_integrations.update_one({'user_id': 'owner'}, {'$set': {'store_id': 2000}})
            elif change == 'binding_disconnected':
                await db.salla_integrations.update_one({'user_id': 'owner'}, {'$set': {'status': 'disconnected'}})
            elif change == 'binding_reverse_duplicate':
                await db.salla_integrations.insert_one({'user_id': 'other', 'store_id': MERCHANT, 'status': 'connected'})
            else:
                await db.salla_integrations.delete_one({'user_id': 'owner'})
                await db.salla_integrations.insert_one({'user_id': 'owner', 'store_id': 1000, 'status': 'connected'})
            return {'success': True, 'data': [shipment()],
                    'pagination': {'total': 1, 'totalPages': 1, 'currentPage': 1}}
        monkeypatch.setattr(sync, 'call_salla', provider)
        await sync.acquire_missing(db, 'owner', [issue()], started_at=START)
        return await db.unified_orders.find_one({'order_number': '10'})
    stored = asyncio.run(scenario())
    assert COMPLETED_PROOF not in stored['salla_shipping_current']


@pytest.mark.parametrize(('change', 'accepted'), [('stable', True), ('identical_retry', True),
    ('conflicting_completion', False), ('invalid_verification', False), ('newer_noncompleted', False)])
def test_capture_set_is_rechecked_after_the_network_read(monkeypatch, change, accepted):
    source = detached_fixture()[0]['unified_orders'][0]
    async def scenario():
        db = await seed(source)
        async def provider(_db, owner, method, path, *, params):
            assert path == '/shipments'
            if change != 'stable':
                row = capture()
                if change == 'conflicting_completion':
                    row['payload']['created_at'] = '2026-10-06T09:01:00+00:00'
                elif change == 'invalid_verification':
                    row['verified_before_capture'] = False
                elif change == 'newer_noncompleted':
                    row['payload']['created_at'] = '2026-10-07T12:00:01+00:00'
                    row['payload']['data']['status'] = 'delivering'
                await db[CAPTURES].insert_one(rehash(row))
            return {'success': True, 'data': [shipment()],
                    'pagination': {'total': 1, 'totalPages': 1, 'currentPage': 1}}
        monkeypatch.setattr(sync, 'call_salla', provider)
        await sync.acquire_missing(db, 'owner', [issue()], started_at=START)
        return await db.unified_orders.find_one({'user_id': 'owner'})
    stored = asyncio.run(scenario())
    assert (COMPLETED_PROOF in stored['salla_shipping_current']) is accepted


class ObservedCursor:
    def __init__(self, cursor, observation):
        self.cursor, self.observation = cursor, observation

    def max_time_ms(self, value):
        self.observation['max_time_ms'] = value
        self.cursor = self.cursor.max_time_ms(value)
        return self

    def limit(self, value):
        self.observation['limit'] = value
        self.cursor = self.cursor.limit(value)
        return self

    async def to_list(self, length):
        self.observation['length'] = length
        return await self.cursor.to_list(length=length)


class ObservedCollection:
    def __init__(self, collection, name, log):
        self.collection, self.name, self.log = collection, name, log

    def find(self, selector, projection):
        observation = {'collection': self.name, 'method': 'find',
                       'selector': deepcopy(selector), 'projection': deepcopy(projection)}
        self.log.append(observation)
        return ObservedCursor(self.collection.find(selector, projection), observation)

    async def create_index(self, keys, **kwargs):
        self.log.append({'collection': self.name, 'method': 'create_index',
                         'keys': deepcopy(keys), **kwargs})
        return await self.collection.create_index(keys, **kwargs)

    def __getattr__(self, name):
        # Reader tests reject all writes and unexpected collection operations.
        raise AssertionError('Unexpected collection operation: ' + self.name + '.' + name)


class ObservedDB:
    def __init__(self, db):
        self.db, self.log = db, []

    def __getitem__(self, name):
        return ObservedCollection(self.db[name], name, self.log)

    def __getattr__(self, name):
        return self[name]


def test_query_is_native_indexed_bounded_and_projects_no_owner_credentials():
    source = current_fixture()[0]['unified_orders'][0]
    async def scenario():
        db = await seed(source)
        await db.salla_integrations.update_one({'user_id': 'owner'}, {'$set': {
            'access_token': 'synthetic-secret', 'refresh_token': 'synthetic-secret'}})
        observed = ObservedDB(db)
        result = await reader.recover_completed_capture(observed, 'owner', source, END)
        assert result is not None
        return observed.log
    log = asyncio.run(scenario())
    assert set(reader.ORDER_PATHS) == set(NATIVE_PATHS)
    assert [(entry['collection'], entry['method']) for entry in log] == [
        ('salla_integrations', 'find'), ('salla_integrations', 'find'), (CAPTURES, 'find')]
    for entry in log[:2]:
        assert entry['projection'] == {'_id': 1, 'user_id': 1, 'store_id': 1, 'status': 1}
        assert entry['limit'] == entry['length'] == 2
        assert entry['max_time_ms'] == 3000
    entry = log[2]
    assert entry['selector'] == {'merchant_id': '1000', 'event': 'order.status.updated',
                                '$or': [{path: {'$in': ['100', 100]}} for path in NATIVE_PATHS]}
    assert entry['projection'] == {'_id': 0, 'merchant_id': 1, 'event': 1, 'event_hash': 1,
        'verified_before_capture': 1, 'demo_store_cleanup.applied': 1, 'payload': 1}
    assert entry['limit'] == entry['length'] == 301
    assert entry['max_time_ms'] == 3000


def test_startup_indexes_cover_each_native_query_branch_and_reverse_store_lookup():
    async def scenario():
        observed = ObservedDB(AsyncMongoMockClient()['synthetic_capture_indexes'])
        await reader.ensure_completed_capture_indexes(observed)
        return observed.log
    indexes = asyncio.run(scenario())
    assert len(indexes) == 9
    assert {tuple(tuple(key) for key in entry['keys']) for entry in indexes[:-1]} == {
        (('merchant_id', 1), ('event', 1), (path, 1)) for path in NATIVE_PATHS}
    assert all(entry['collection'] == CAPTURES and entry['method'] == 'create_index'
               and entry['unique'] is False and entry['partialFilterExpression'] == {'event': 'order.status.updated'}
               for entry in indexes[:-1])
    assert indexes[-1] == {'collection': 'salla_integrations', 'method': 'create_index',
                            'keys': 'store_id', 'unique': False}


def test_acquisition_persists_only_evidence_and_retry_without_financial_or_order_status_writes(monkeypatch):
    source = current_fixture()[0]['unified_orders'][0]
    async def forbidden(*args, **kwargs):
        pytest.fail('Canonical shipment and captured completion need no provider request')
    monkeypatch.setattr(sync, 'call_salla', forbidden)
    async def scenario():
        db = await seed(source)
        protected = ['mz2_operational_balance_states', 'mz2_financial_accounts', 'store_delivery_assignments',
                     'store_delivery_collections', 'order_status_histories', 'orders']
        before = {}
        for name in protected:
            await db[name].insert_one({'user_id': 'owner', 'id': 'synthetic', 'balance': '100.00', 'status': 'original'})
            before[name] = await db[name].find({}).to_list(length=10)
        await sync.acquire_missing(db, 'owner', [issue()], started_at=START)
        for name in protected:
            assert await db[name].find({}).to_list(length=10) == before[name]
        return await db.unified_orders.find_one({'user_id': 'owner'}, {'_id': 0})
    stored = asyncio.run(scenario())
    assert raw(stored) == raw(source)
    assert stored['order_status_slug'] == source['order_status_slug']
    assert stored['g47_salla_snapshot'] == source['g47_salla_snapshot']
    assert stored['salla_shipping_current']['status'] == source['salla_shipping_current']['status']
    assert stored['salla_shipping_current'][COMPLETED_PROOF]['at'] == COMPLETED
    assert stored['salla_courier_delivery_retry_after']


@pytest.mark.parametrize('has_binding', [True, False])
def test_no_capture_retains_native_history_fallback_and_activity_alone_stays_missing(monkeypatch, has_binding):
    data, state = current_fixture()
    source = data['unified_orders'][0]
    calls = []
    async def provider(*args, **kwargs):
        calls.append(args[3])
        return await native_activity(*args, **kwargs)
    monkeypatch.setattr(sync, 'call_salla', provider)
    async def scenario():
        db = await seed(source, captures=[], integrations=None if has_binding else [])
        await sync.acquire_missing(db, 'owner', [issue()], started_at=START)
        return await db.unified_orders.find_one({'user_id': 'owner'}, {'_id': 0})
    data['unified_orders'][0] = asyncio.run(scenario())
    assert calls == ['/orders/100/histories']
    assert calculate(data, state)['movements'] == []
    assert COMPLETED_PROOF not in data['unified_orders'][0]['salla_shipping_current']


def test_invalid_capture_does_not_fall_through_to_a_fresh_history_guess(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail('An invalid stored event must not be hidden by another source')
    monkeypatch.setattr(sync, 'call_salla', forbidden)
    async def scenario():
        source = current_fixture()[0]['unified_orders'][0]
        row = capture()
        row['verified_before_capture'] = False
        db = await seed(source, [row])
        await sync.acquire_missing(db, 'owner', [issue()], started_at=START)
        return await db.unified_orders.find_one({'user_id': 'owner'})
    assert COMPLETED_PROOF not in asyncio.run(scenario())['salla_shipping_current']


@pytest.mark.parametrize(('key', 'value'), [('tracking_number', 'CONFLICTING-AWB'),
    ('salla_shipment_id', 'conflicting-shipment'), ('shipping_company_code', 'different-code'),
    ('tracking.number', 'CONFLICTING-AWB'), ('courier.code', 'different-code'),
    ('courier.id', 'different-code'), ('company.code', 'different-code'), ('company.id', 'different-code'),
    ('shipping_company.code', 'different-code'), ('shipping_company.id', 'different-code')])
@pytest.mark.parametrize('already_captured', [False, True])
def test_complete_raw_shipment_cannot_hide_conflicting_root_identity(key, value, already_captured):
    data, state = current_fixture()
    source = data['unified_orders'][0]
    if already_captured:
        attach(data)
        put(raw(source), key, value)
        assert calculate(data, state)['movements'] == []
    else:
        put(raw(source), key, value)
        with pytest.raises(ValueError, match='identity_conflict'):
            recover(source)


@pytest.mark.parametrize('status', ['returned', 'cancelled'])
def test_complete_raw_shipment_cannot_hide_root_invalidation(status):
    data, state = current_fixture()
    source = data['unified_orders'][0]
    attach(data)
    raw(source)['shipping_status'] = status
    assert calculate(data, state)['movements'] == []
    source['salla_shipping_current'].pop(COMPLETED_PROOF)
    with pytest.raises(ValueError, match='shipment_invalid'):
        recover(source)
