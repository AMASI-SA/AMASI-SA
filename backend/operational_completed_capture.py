"""Recover an original completed event without replaying its order snapshot.

The capture fingerprint checks stored-payload integrity; it is not a signature.
Only the verified webhook writer supplies captures. Current owner/merchant
binding is checked in both directions, independently of capture receipt time.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import re

from operational_delivery_evidence import INACTIVE, _current_shipping, _inactive, _instant, _order_waybill, _status, _text
from salla_integration.store_scope import is_attribution_pilot_store

COLLECTION = 'salla_webhook_event_captures'
EVENT = 'order.status.updated'
SOURCE = 'verified_order_completed_capture'
SCHEMA = 'operational_completed_capture_v1'
MAX_CAPTURES = 300
MAX_TIME_MS = 3000
ORDER_PATHS = ('payload.data.id', 'payload.data.order_id', 'payload.data.salla_order_id',
               'payload.data.order.id', 'payload.data.order.order_id', 'payload.data.order.salla_order_id',
               'payload.order_id', 'payload.salla_order_id')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_NATIVE_IDS = ('id', 'order_id', 'salla_order_id')
_REFERENCES = ('reference_id', 'order_number', 'order_reference_id')
_RECORD_KEYS = {'capture_hash', 'event', 'merchant_id', 'order_id', 'order_number', 'target_status', 'at'}
_SHIPMENT_KEYS = {'source', 'owner', 'order_id', 'order_number', 'shipment_id', 'waybill',
                  'company_code', 'company_name', 'status', 'type', 'created_at', 'updated_at'}
_RAW_SHIPPING_KEYS = {
    'shipping', 'shipment', 'shipments', 'salla_shipping_current', 'salla_shipment_id', 'shipment_id',
    'shipping_company', 'shipping_company_code', 'shipping_company_name', 'shipping_company_logo',
    'company', 'company_name', 'company_code', 'company_id', 'company_logo',
    'courier', 'courier_name', 'courier_code', 'courier_id', 'courier_logo',
    'tracking', 'tracking_number', 'tracking_id', 'shipping_number', 'awb',
    'tracking_url', 'tracking_link', 'label', 'label_url', 'shipping_label_url', 'awb_url', 'waybill_url',
}


def _fail(reason):
    raise ValueError('operational_prepaid_shipping_capture_' + reason)


def _variants(value):
    return [value, int(value)] if value.isascii() and value.isdigit() else [value]


def _agree(values, expected, *, required=False):
    values = [value for value in values if value is not None]
    if required and not values or any(_text(value) != expected for value in values):
        _fail('identity_conflict')


def _event_time(value):
    # Never substitute an order update or database receipt timestamp.
    native = value.get('date') if isinstance(value, dict) else value
    if isinstance(native, datetime) and native.utcoffset() is None:
        _fail('time_invalid')
    try:
        if isinstance(value, str) and ' GMT' in value:
            value = datetime.strptime(value, '%a %b %d %Y %H:%M:%S GMT%z')
        return _instant(value)
    except (TypeError, ValueError):
        _fail('time_invalid')


async def owner_binding(db, owner):
    """A unique connected binding, not a latest-wins owner resolver."""
    if not _text(owner):
        _fail('binding_unavailable')
    projection = {'_id': 1, 'user_id': 1, 'store_id': 1, 'status': 1}
    rows = await db.salla_integrations.find({'user_id': owner}, projection).max_time_ms(
        MAX_TIME_MS).limit(2).to_list(length=2)
    if not rows:
        return None  # No authorized capture scope; the native-history path remains separate.
    if len(rows) != 1:
        _fail('binding_ambiguous')
    row = rows[0]
    merchant = _text(row.get('store_id'))
    if row.get('user_id') != owner or not merchant or row.get('status') != 'connected':
        _fail('binding_unavailable')
    if is_attribution_pilot_store(merchant):
        _fail('store_excluded')
    reverse = await db.salla_integrations.find({'store_id': {'$in': _variants(merchant)}}, projection).max_time_ms(
        MAX_TIME_MS).limit(2).to_list(length=2)
    if len(reverse) != 1 or reverse[0] != row:
        _fail('binding_ambiguous')
    return {'owner': owner, 'merchant_id': merchant, 'record_id': row.get('_id')}


def _source_identity(source, owner):
    raw = (source.get('raw_by_source') or {}).get('salla_direct') or {}
    oid, number = _text(raw.get('id')), _text(source.get('order_number'))
    if source.get('user_id') != owner or not oid or not number:
        _fail('identity_conflict')
    _agree([raw.get(k) for k in _NATIVE_IDS] + [source.get(k) for k in ('order_id', 'salla_order_id')], oid)
    _agree([raw.get(k) for k in _REFERENCES], number, required=True)
    created = _instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
    return raw, oid, number, created


def _merchant_scope(merchant, *objects):
    # These native scalar IDs are optional in order snapshots. An unfamiliar
    # object shape cannot silently become an unspecified merchant.
    _agree([obj.get(key) for obj in objects for key in ('merchant', 'merchant_id', 'store_id')], merchant)


def _record(capture, merchant, oid, number, created, version, now):
    from operational_courier_transitions import completed_state
    from salla_integration.webhook_event_capture import _fingerprint, _sanitize

    payload = capture.get('payload')
    cleanup = capture.get('demo_store_cleanup') or {}
    fingerprint = capture.get('event_hash')
    if (capture.get('verified_before_capture') is not True or capture.get('event') != EVENT
            or not isinstance(payload, dict) or payload.get('event') != EVENT
            or not isinstance(cleanup, dict) or cleanup.get('applied') is True):
        _fail('unverified')
    if (not isinstance(fingerprint, str) or not _HASH.fullmatch(fingerprint)
            or _sanitize(payload) != payload or _fingerprint(payload) != fingerprint):
        _fail('hash_mismatch')
    _agree([capture.get('merchant_id'), payload.get('merchant')], merchant, required=True)
    if _text(capture.get('merchant_id')) != merchant or _text(payload.get('merchant')) != merchant:
        _fail('identity_conflict')
    data = payload.get('data')
    if not isinstance(data, dict) or 'order' in data and not isinstance(data['order'], dict):
        _fail('identity_conflict')
    objects = [data, *([data['order']] if 'order' in data else [])]
    _merchant_scope(merchant, payload, *objects)
    # Check every alias before considering nested data. Merging the objects
    # first could conceal a conflicting outer order ID, reference or target.
    _agree([obj.get(k) for obj in objects for k in _NATIVE_IDS]
           + [payload.get(k) for k in ('order_id', 'salla_order_id')], oid, required=True)
    _agree([obj.get(k) for obj in objects for k in _REFERENCES]
           + [payload.get(k) for k in _REFERENCES], number)
    targets = [obj[k] for obj in [payload, *objects] for k in ('to_status', 'new_status', 'status')
               if obj.get(k) is not None]
    states = [completed_state(target) for target in targets]
    if not states or None in states or len(set(states)) != 1:
        _fail('target_conflict')
    at = _event_time(payload.get('created_at'))
    for obj in objects:
        for key in ('date', 'created_at', 'order_date'):
            if obj.get(key) is not None and _instant(obj[key]) != created:
                _fail('order_creation_conflict')
    # A later non-completed event also makes this source revision stale. Do
    # not discard it before validating the native event clock.
    if not created <= at <= version <= now:
        _fail('time_invalid')
    if not states[0]:
        return None
    return {'capture_hash': fingerprint, 'event': EVENT, 'merchant_id': merchant,
            'order_id': oid, 'order_number': number, 'target_status': 'completed', 'at': at.isoformat()}


def raw_waybill_absent(raw):
    """An orphan identity/AWB is incomplete evidence, not an absent shipment."""
    from salla_shipping import extract_shipping

    return (all(value is None or type(value) is str and value == ''
                or type(value) in (dict, list) and not value
                for value in (raw.get(key) for key in _RAW_SHIPPING_KEYS))
            and extract_shipping(raw) is None)


def _shipping_lifecycle(raw):
    for key in ('shipping_status', 'shipment_status'):
        # Order shipping_ready and a shipment's delivering are different
        # lifecycle fields. Only explicit invalidations conflict here.
        if _status(raw.get(key)) in INACTIVE:
            _fail('shipment_invalid')


def _raw_shipping_identity(raw, current):
    """A complete embedded shipment cannot hide conflicting native root facts."""
    from salla_shipping import extract_shipping

    _shipping_lifecycle(raw)
    _agree([raw.get(key) for key in ('salla_shipment_id', 'shipment_id')], current.get('shipment_id'))
    references = [raw.get(key) for key in ('tracking_number', 'tracking_id', 'shipping_number', 'awb')]
    tracking = raw.get('tracking')
    if tracking not in (None, '', {}, []):
        if not isinstance(tracking, dict):
            _fail('shipment_invalid')
        references.append(tracking.get('number'))
    _agree(references, current.get('tracking_number'))
    codes = [raw[key] for key in ('shipping_company_code', 'company_code', 'courier_code', 'courier_id', 'company_id')
             if raw.get(key) is not None]
    # Match the native code aliases recognized by _carrier before its choice
    # of one container can conceal another. Display names are not code aliases.
    for key in ('courier', 'company', 'shipping_company'):
        value = raw.get(key)
        if isinstance(value, dict):
            codes.extend(value[field] for field in ('code', 'id') if value.get(field) is not None)
        elif value not in (None, '', []) and not isinstance(value, str):
            _fail('shipment_invalid')
    if codes:
        observed = extract_shipping(raw) or {}
        code = _text(current.get('company_code')) or _text(observed.get('company_code'))
        if not code:
            _fail('shipment_carrier_conflict')
        _agree(codes, code)


def _shipment_projection(rows, owner, merchant, oid, number, raw, current, version):
    from salla_shipping import extract_shipping, same_carrier

    # Count the entire authenticated result before considering return, old or
    # inactive rows. Filtering first could mistake a replacement for an original.
    if not isinstance(rows, list) or len(rows) != 1 or current.get('superseded_shipment_ids'):
        _fail('shipment_scope_ambiguous')
    row = rows[0]
    if (not isinstance(row, dict) or _inactive(row) or row.get('type') != 'shipment'
            or not _text(row.get('status'))):
        _fail('shipment_invalid')
    order = row.get('order') or {}
    if not isinstance(order, dict):
        _fail('shipment_invalid')
    _merchant_scope(merchant, row, order)
    _agree([row.get('order_id'), order.get('id'), order.get('order_id')], oid, required=True)
    _agree([row.get('order_reference_id'), row.get('reference_id')]
           + [order.get(key) for key in _REFERENCES], number)
    _agree([row.get('id'), row.get('shipment_id')], current.get('shipment_id'), required=True)
    tracking = row.get('tracking') or {}
    if not isinstance(tracking, dict):
        _fail('shipment_invalid')
    _agree([row.get(key) for key in ('tracking_number', 'tracking_id', 'shipping_number', 'awb')]
           + [tracking.get('number')], current.get('tracking_number'), required=True)
    observed = extract_shipping(row, event_name='shipment.snapshot')
    if not observed or not same_carrier(current, observed):
        _fail('shipment_carrier_conflict')
    # Explicit aliases must agree too; the normalizer's precedence cannot hide
    # a second conflicting code behind a display-name match.
    codes = [row.get(key) for key in ('company_code', 'shipping_company_code', 'courier_code', 'courier_id', 'company_id')]
    for key in ('courier', 'company', 'shipping_company'):
        value = row.get(key)
        if isinstance(value, dict):
            codes.extend(value.get(field) for field in ('code', 'id'))
    _agree(codes, observed.get('company_code'))
    created = _event_time(row.get('created_at'))
    updated = _event_time(row['updated_at']) if row.get('updated_at') is not None else None
    order_created = _instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
    if (not order_created <= created <= version
            or updated is not None and not created <= updated <= version):
        _fail('shipment_time_invalid')
    return {'source': 'authenticated_salla_shipments', 'owner': owner, 'order_id': oid, 'order_number': number,
            'shipment_id': current['shipment_id'], 'waybill': current['tracking_number'],
            'company_code': observed.get('company_code'), 'company_name': observed.get('company_name'),
            'status': row['status'], 'type': 'shipment', 'created_at': created.isoformat(),
            'updated_at': updated.isoformat() if updated is not None else None}


def capture_waybill_view(raw, current, version):
    """Use separate proof in a transient current view; never rewrite raw data."""
    from operational_courier_transitions import COMPLETED_PROOF
    from salla_shipping import same_carrier

    proof = current.get(COMPLETED_PROOF) or {}
    row = proof.get('shipment')
    if proof.get('source') != SOURCE or not isinstance(row, dict) or set(row) != _SHIPMENT_KEYS:
        _fail('shipment_proof_invalid')
    oid, number = _text(raw.get('id')), _text(raw.get('reference_id') or raw.get('order_number'))
    if (row.get('source') != 'authenticated_salla_shipments' or row.get('owner') != proof.get('owner')
            or row.get('order_id') != oid or row.get('order_number') != number
            or row.get('shipment_id') != current.get('shipment_id') or row.get('waybill') != current.get('tracking_number')
            or row.get('type') != 'shipment' or not _text(row.get('status')) or _inactive(row)
            or current.get('superseded_shipment_ids') or not same_carrier(current, row)):
        _fail('shipment_proof_invalid')
    _shipping_lifecycle(raw)
    created, order_created = _event_time(row.get('created_at')), _instant(
        raw.get('date') or raw.get('created_at') or raw.get('order_date'))
    bound = _instant(proof.get('source_updated_at'))
    if (not order_created <= created <= bound <= version
            or row.get('updated_at') is not None and not created <= _event_time(row['updated_at']) <= bound):
        _fail('shipment_time_invalid')
    if not raw_waybill_absent(raw):
        # Later canonical data must independently agree. Partial or conflicting
        # raw evidence cannot be hidden by the separately acquired shipment.
        _order_waybill(raw, current, current.get('shipment_id'), current.get('tracking_number'), version)
    snapshot = {'id': row['shipment_id'], 'order_id': oid, 'order_reference_id': number,
                'courier_id': row.get('company_code'), 'courier_name': row.get('company_name'),
                'tracking_number': row['waybill'], 'type': 'shipment', 'status': row['status'],
                'created_at': row['created_at'], 'updated_at': row.get('updated_at')}
    return {**raw, 'shipping': {}, 'shipment': None, 'shipments': [snapshot]}


def capture_time(proof, owner, oid, number, version, raw, current, source):
    """Validate the sparse trusted projection again when it is consumed."""
    merchant, records = _text(proof.get('merchant_id')), proof.get('captures')
    if (proof.get('schema') != SCHEMA or proof.get('source') != SOURCE or proof.get('owner') != owner
            or not merchant or is_attribution_pilot_store(merchant)
            or not isinstance(records, list) or not 1 <= len(records) <= MAX_CAPTURES
            or _instant(proof.get('source_updated_at')) > version):
        _fail('proof_invalid')
    canonical, native_id, reference, _ = _source_identity(source, owner)
    if canonical != raw or (native_id, reference) != (oid, number):
        _fail('identity_conflict')
    _merchant_scope(merchant, source, raw)
    _raw_shipping_identity(raw, current)
    hashes, times = set(), set()
    for row in records:
        if (not isinstance(row, dict) or set(row) != _RECORD_KEYS
                or row.get('event') != EVENT or row.get('merchant_id') != merchant
                or row.get('order_id') != oid or row.get('order_number') != number
                or row.get('target_status') != 'completed' or not isinstance(row.get('capture_hash'), str)
                or not _HASH.fullmatch(row['capture_hash']) or row['capture_hash'] in hashes):
            _fail('proof_invalid')
        hashes.add(row['capture_hash'])
        times.add(_event_time(row.get('at')))
    if len(times) != 1 or times != {_event_time(proof.get('at'))}:
        _fail('completion_ambiguous')
    at = times.pop()
    if not at <= _instant(proof.get('source_updated_at')) <= version:
        _fail('time_invalid')
    if 'shipment' in proof:
        capture_waybill_view(raw, current, version)
    return at


async def _capture_set(db, merchant, oid, number, created, version, now):
    selector = {'merchant_id': merchant, 'event': EVENT,
                '$or': [{path: {'$in': _variants(oid)}} for path in ORDER_PATHS]}
    # The full sanitized payload is transiently required to verify its existing
    # fingerprint. No customer data, payload or receipt time enters the proof.
    projection = {'_id': 0, 'merchant_id': 1, 'event': 1, 'event_hash': 1,
                  'verified_before_capture': 1, 'demo_store_cleanup.applied': 1, 'payload': 1}
    captures = await db[COLLECTION].find(selector, projection).max_time_ms(MAX_TIME_MS).limit(
        MAX_CAPTURES + 1).to_list(length=MAX_CAPTURES + 1)
    if len(captures) > MAX_CAPTURES:
        _fail('scope_incomplete')
    records, fingerprints = {}, set()
    for row in captures:
        record = _record(row, merchant, oid, number, created, version, now)
        fingerprints.add(row['event_hash'])
        if record is not None:
            key = record['capture_hash']
            if key in records and records[key] != record:
                _fail('completion_ambiguous')
            records[key] = record
    # Identical receipts are the same observation. Include non-completed events
    # too, so a later event cannot disappear during the bounded network read.
    digest = sha256('\n'.join(sorted(fingerprints)).encode()).hexdigest()
    return records, digest


async def recheck_capture_set(db, owner, source, now, binding, expected):
    """Close the network-read window without claiming a cross-collection CAS."""
    from operational_order_completed_evidence import _source_version

    raw, oid, number, created = _source_identity(source, owner)
    _merchant_scope(binding['merchant_id'], source, raw)
    _, digest = await _capture_set(db, binding['merchant_id'], oid, number, created, _source_version(raw), now)
    if digest != expected:
        _fail('scope_changed')


async def recover_completed_capture(db, owner, source, now, *, load_shipments=None):
    """Read all stored candidates for one native order, with a hard scope cap."""
    from operational_courier_transitions import COMPLETED_PROOF
    from operational_order_completed_evidence import bind_completed_waybill, completion_time, _source_version

    raw, oid, number, created = _source_identity(source, owner)
    binding = await owner_binding(db, owner)
    if binding is None:
        return None
    _merchant_scope(binding['merchant_id'], source, raw)
    version = _source_version(raw)
    records, scope = await _capture_set(db, binding['merchant_id'], oid, number, created, version, now)
    if not records:
        return None
    times = {row['at'] for row in records.values()}
    if len(times) != 1:
        _fail('completion_ambiguous')
    at = _event_time(times.pop())
    current, shipment, waybill = _current_shipping(raw, source, require_delivered=False)
    _raw_shipping_identity(raw, current)
    existing = current.get(COMPLETED_PROOF)
    if existing is not None:
        # Never replace or hide another observed completion. An equal existing
        # proof remains immutable; a contradictory or invalid proof fails closed.
        if completion_time(raw, source, [], now) != at:
            _fail('completion_ambiguous')
        bind_completed_waybill(raw, current, shipment, waybill, at)
        return deepcopy(current), binding, scope
    accepted = deepcopy(current)
    accepted[COMPLETED_PROOF] = {'source': SOURCE, 'schema': SCHEMA, 'owner': owner,
        'merchant_id': binding['merchant_id'], 'order_id': oid, 'order_number': number,
        'shipment_id': shipment, 'waybill': waybill, 'at': at.isoformat(), 'target_status': 'completed',
        'source_updated_at': version.isoformat(), 'captures': [records[k] for k in sorted(records)]}
    if raw_waybill_absent(raw) and load_shipments is not None:
        _shipping_lifecycle(raw)
        accepted[COMPLETED_PROOF]['shipment'] = _shipment_projection(
            await load_shipments(), owner, binding['merchant_id'], oid, number, raw, current, version)
    completion_time(raw, {**source, 'salla_shipping_current': accepted}, [], now)
    bind_completed_waybill(raw, accepted, shipment, waybill, at)
    return accepted, binding, scope


async def ensure_completed_capture_indexes(db):
    """Startup schema work only; the acquisition reader never creates indexes."""
    for index, path in enumerate(ORDER_PATHS):
        await db[COLLECTION].create_index([('merchant_id', 1), ('event', 1), (path, 1)],
            name='operational_completed_capture_' + str(index), unique=False,
            partialFilterExpression={'event': EVENT})
    await db.salla_integrations.create_index('store_id', unique=False)
