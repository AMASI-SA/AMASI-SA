"""Bounded one-order local reader for an owner's delivery-date attestation."""
from operational_balance_store import check_authorization, digest
from operational_completed_shipping_sources import _values
from operational_delivery_confirmation import candidate_event
from operational_delivery_evidence import _driver_assignment, _text, conflicting_shipments
from operational_financial_activity_runtime import prepare_in_thread

ORDER_FIELDS = {key: 1 for key in ('user_id', 'order_id', 'order_number', 'order_status', 'order_status_slug',
    'salla_order_id', 'salla_shipping_current', 'raw_by_source.salla_direct', 'g47_salla_snapshot',
    'salla_shipment_id', 'tracking_number', 'shipping_number', 'shipping_company_code',
    'shipping_company', 'shipping_status', 'shipment_status')}


async def _read(db, owner, name, query, projection, limit):
    await check_authorization()
    values = await db[name].find({'user_id': owner, **query}, projection).to_list(limit + 1)
    await check_authorization()
    if len(values) > limit:
        raise ValueError('operational_delivery_confirmation_scope_too_large')
    if any(row.get('user_id') != owner for row in values):
        raise ValueError('operational_delivery_owner_mismatch')
    return values


async def read_candidate(db, owner, state, payload, as_of, *, record=None):
    from operational_courier_transitions import load_delivery_history
    from operational_courier_tariffs import with_tariffs
    oid, number = payload['order_id'], payload['order_number']
    matches = await _read(db, owner, 'unified_orders', {'$or': [
        {'raw_by_source.salla_direct.id': {'$in': _values(oid)}},
        *[{key: {'$in': _values(number)}} for key in ('order_number',
          'raw_by_source.salla_direct.reference_id', 'raw_by_source.salla_direct.order_number')]]}, ORDER_FIELDS, 2)
    if len(matches) != 1:
        raise ValueError('operational_delivery_order_identity_conflict' if matches else 'operational_delivery_order_missing')
    source = matches[0]
    raw = (source.get('raw_by_source') or {}).get('salla_direct')
    if not isinstance(raw, dict):
        raise ValueError('operational_delivery_canonical_source_missing')
    if str(raw.get('id') or '') != oid or str(raw.get('reference_id') or raw.get('order_number') or '') != number:
        raise ValueError('operational_delivery_order_identity_conflict')
    setups = await _read(db, owner, 'mz2_shipping_setup_v2', {}, {'_id': 0}, 2)
    if len(setups) > 1:
        raise ValueError('shipping_setup_ambiguous')
    setup, _ = with_tariffs(setups[0] if setups else {}, owner, state)
    assignments = await _read(db, owner, 'store_delivery_assignments', {'$or': [
        {'order_number': {'$in': _values(number)}}, {'order_id': {'$in': _values(oid)}}]}, {'_id': 0}, 100)
    assignment = _driver_assignment(assignments, owner, oid, number)
    if assignment is not None:
        identity = _text(assignment['id'])
        # The full collector sees duplicate IDs on other orders too. Match that
        # contract with one bounded identity lookup in the same owner snapshot;
        # count documents, never collapse equal IDs or equal document contents.
        same_identity = await _read(db, owner, 'store_delivery_assignments',
            {'id': {'$in': _values(identity)}}, {'_id': 0}, 100)
        if sum(_text(row.get('id')) == identity for row in same_identity) != 1:
            raise ValueError('operational_delivery_assignment_ambiguous')
        if assignment not in same_identity:
            raise ValueError('operational_delivery_confirmation_source_changed')
    current = source.get('salla_shipping_current') or raw.get('salla_shipping_current') or {}
    refs = []
    for field, paths in (
        ('shipment_id', ('salla_shipping_current.shipment_id', 'raw_by_source.salla_direct.salla_shipping_current.shipment_id',
            'salla_shipment_id', 'raw_by_source.salla_direct.shipping.shipment_id', 'raw_by_source.salla_direct.shipments.id')),
        ('tracking_number', ('salla_shipping_current.tracking_number', 'raw_by_source.salla_direct.salla_shipping_current.tracking_number',
            'tracking_number', 'shipping_number', 'raw_by_source.salla_direct.shipping.tracking_number',
            'raw_by_source.salla_direct.shipments.tracking_number')),
    ):
        if isinstance(current, dict) and current.get(field):
            refs.extend({path: {'$in': _values(current[field])}} for path in paths)
    siblings = await _read(db, owner, 'unified_orders', {'$or': refs}, ORDER_FIELDS, 100) if refs else []
    sources = {str(row.get('_id') or (row.get('raw_by_source') or {}).get('salla_direct', {}).get('id')): row
               for row in [source, *siblings]}
    conflicts = await prepare_in_thread(conflicting_shipments, list(sources.values()), state)
    await check_authorization()
    history = await load_delivery_history(db, owner, [source], max_rows=1000, status_order_numbers={number})
    await check_authorization()
    source_hash = digest([source, assignments, setup, history.get(number, ()), sorted(conflicts)])
    args = dict(source=source, owner=owner, assignments=assignments, setup=setup,
                started_at=state['started_at'], as_of=as_of, history=history.get(number, ()), shipment_conflicts=conflicts)
    if record:
        from operational_delivery_confirmation import confirmed_event
        event = await prepare_in_thread(confirmed_event, record, raw, **args)
    else:
        event = await prepare_in_thread(candidate_event, raw, **args)
    if (event['party_type'], event['party_id']) != (payload['party_type'], payload['party_id']):
        raise ValueError('operational_delivery_confirmation_source_changed')
    return event, source_hash
