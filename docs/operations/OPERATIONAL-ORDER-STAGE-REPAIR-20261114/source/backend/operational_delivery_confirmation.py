"""Sealed owner attestation of delivery after opening, without an invented date.

The record is evidence only. Normal collection rechecks the current executor,
source contradictions and every existing fee/COD/opening/payout gate.
"""
from operational_balance_store import digest

PREFIX = 'owner_delivery_after_start:'
SCHEMA = 'operational_owner_delivery_confirmation_v1'
EVENT_SCHEMA = 'operational_owner_delivery_event_v1'
BASIS = 'owner_confirmed_after_start'
DATE_GAPS = frozenset({'operational_delivery_timestamp_missing'})
BINDING_FIELDS = ('order_id', 'order_number', 'order_created_at', 'party_type', 'party_id',
                  'currency', 'assignment_id', 'shipment_id', 'waybill', 'source_carrier_key')


def binding(event):
    return {key: event[key] for key in BINDING_FIELDS if key in event}


def validate_record(record, *, owner, started_at, order_id=None):
    """A caller-supplied normalized flag can never replace the saved owner proof."""
    from operational_delivery_evidence import _instant, _text
    if (not isinstance(record, dict) or record.get('schema') != SCHEMA
            or record.get('seal') != digest({key: value for key, value in record.items() if key != 'seal'})
            or record.get('owner_id') != owner or record.get('confirmed_by') != owner
            or record.get('confirmed_after_start') is not True
            or _instant(record.get('started_at')) != _instant(started_at)
            or not _instant(started_at) <= _instant(record.get('confirmed_at'))
            or record.get('party_type') not in {'courier', 'store_driver'}
            or any(not _text(record.get(key)) for key in ('id', 'order_id', 'order_number', 'party_id'))
            or order_id is not None and str(record['order_id']) != str(order_id)):
        raise ValueError('operational_delivery_confirmation_invalid')
    return record


def saved_record(state, order_id):
    record = (state.get('engine') or {}).get('facts', {}).get(PREFIX + str(order_id))
    if record is None:
        return None
    return validate_record(record, owner=state['owner_id'], started_at=state['started_at'], order_id=order_id)


def record_confirmation(state, event, *, actor, actor_name, confirmed_at, source_hash, preview_hash):
    from operational_delivery_evidence import _instant
    if actor != state['owner_id']:
        raise ValueError('operational_delivery_confirmation_owner_required')
    stamp = _instant(confirmed_at).isoformat()
    record = {'schema': SCHEMA, 'id': digest([state['owner_id'], state['started_at'], event['order_id'], BASIS]),
              'owner_id': state['owner_id'], 'started_at': state['started_at'],
              'confirmed_by': actor, 'confirmed_by_name': str(actor_name or actor),
              'confirmed_at': stamp, 'confirmed_after_start': True,
              'source_hash': source_hash, 'preview_hash': preview_hash, **binding(event)}
    record['seal'] = digest(record)
    return validate_record(record, owner=state['owner_id'], started_at=state['started_at'])


def public_record(state, record):
    event = (state.get('engine') or {}).get('facts', {}).get('delivery_event:' + record['order_id']) or {}
    applied = (event.get('schema') == EVENT_SCHEMA and event.get('confirmation_id') == record['id']
               and event.get('confirmation_seal') == record['seal'] and event.get('recognition_basis') == BASIS
               and event.get('delivered_at') is None and event.get('recorded_at') == record['confirmed_at']
               and binding(event) == {key: record[key] for key in BINDING_FIELDS if key in record})
    return {'id': record['id'], 'confirmed_at': record['confirmed_at'],
            'state': 'applied' if applied else 'pending_reconciliation'}


def applied_metadata(facts, order_id, party_type, party_id):
    """Positive saved reconciliation evidence, including fully resolved orders."""
    record = facts.get(PREFIX + str(order_id))
    if not isinstance(record, dict) or (record.get('party_type'), record.get('party_id')) != (party_type, party_id):
        return None
    try:
        validate_record(record, owner=record.get('owner_id'), started_at=record.get('started_at'), order_id=order_id)
    except (ValueError, TypeError, KeyError):
        return None
    public = public_record({'engine': {'facts': facts}}, record)
    if public['state'] != 'applied':
        return None
    return {'can_confirm': False, 'state': 'applied', 'party_type': party_type, 'party_id': party_id,
            'confirmation_id': public['id'], 'confirmed_at': public['confirmed_at']}


def candidate_event(raw, *, source, owner, assignments, setup, started_at, as_of, history=(),
                    shipment_conflicts=(), recorded_at=None, assignment_index=None):
    """Check the date gap and exact executor; never quote a fee or collect cash.

    Once a proof exists, later supplied real dates may agree with its bound.
    A known before-start, future, contradictory or post-confirmation date fails.
    """
    from operational_courier_transitions import order_delivery_time
    from operational_delivery_evidence import (DELIVERED, _courier, _currency, _current_shipping,
        _driver_assignment, _instant, _order_waybill, _shipment_rows, _status, _text)
    if not isinstance(source, dict) or source.get('user_id') != owner:
        raise ValueError('operational_delivery_owner_mismatch')
    canonical = (source.get('raw_by_source') or {}).get('salla_direct')
    if not isinstance(raw, dict) or not isinstance(canonical, dict):
        raise ValueError('operational_delivery_canonical_source_missing')
    oid, number = _text(raw.get('id')), _text(raw.get('reference_id') or raw.get('order_number'))
    if (not oid or not number or _text(canonical.get('id')) != oid
            or _text(canonical.get('reference_id') or canonical.get('order_number')) != number):
        raise ValueError('operational_delivery_order_identity_conflict')
    watermark = source.get('g47_salla_snapshot') or {}
    if not isinstance(watermark, dict) or watermark.get('requires_authoritative_refresh'):
        raise ValueError('operational_delivery_canonical_source_stale')
    status = raw.get('status')
    if isinstance(status, dict):
        status = status.get('customized') or status
    if (_status(status) not in DELIVERED or any(_status(value) not in DELIVERED for value in
            (raw.get('status_slug'), source.get('order_status'), source.get('order_status_slug')) if value is not None)):
        raise ValueError('operational_delivery_order_shipment_status_conflict')
    start, end = _instant(started_at), _instant(as_of)
    created = _instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
    boundary = _instant(recorded_at) if recorded_at else end
    if not created <= boundary <= end or not start <= boundary:
        raise ValueError('operational_delivery_confirmation_time_invalid')
    # A new post-start delivered order already has an automatic eligibility rule.
    if recorded_at is None and created > start:
        raise ValueError('operational_delivery_confirmation_not_required')
    order_at = order_delivery_time(raw, source, history, end, allow_undated=True, undated_before=recorded_at)
    times = [order_at] if order_at is not None else []
    if assignment_index is None:
        assignment = _driver_assignment(assignments, owner, oid, number)
    else:
        from operational_delivery_assignment_index import indexed_driver_assignment
        assignment = indexed_driver_assignment(assignments, owner, oid, number, assignment_index=assignment_index)
    event = {'schema': EVENT_SCHEMA, 'recognition_basis': BASIS, 'order_id': oid,
             'order_number': number, 'order_created_at': created.isoformat(), 'delivered_at': None,
             'recorded_at': boundary.isoformat()}
    if assignment is not None:
        if assignment.get('delivered_at') not in (None, ''):
            times.append(_instant(assignment['delivered_at']))
        event.update(id='store-delivery:' + assignment['id'], party_type='store_driver',
                     party_id=_text(assignment['driver_id']), assignment_id=assignment['id'],
                     currency=_currency(raw, assignment))
    else:
        current, shipment, waybill = _current_shipping(raw, source, require_delivered=False)
        if ('shipment', shipment) in shipment_conflicts or ('waybill', waybill) in shipment_conflicts:
            raise ValueError('operational_delivery_shipment_shared_by_orders')
        _order_waybill(raw, current, shipment, waybill, boundary)
        _, selected, _ = _shipment_rows(raw, shipment)
        for row in selected:
            for key in ('delivered_at', 'actual_delivered_at'):
                if row.get(key) not in (None, ''):
                    at = _instant(row[key])
                    if row.get('created_at') is not None and _instant(row['created_at']) > at:
                        raise ValueError('operational_delivery_timestamp_before_shipment')
                    times.append(at)
        party, carrier = _courier(setup, owner, current)
        event.update(id='courier-delivery:' + digest([oid, shipment]), party_type='courier', party_id=party,
                     currency=_currency(raw), shipment_id=shipment, waybill=waybill, source_carrier_key=carrier)
    if any(at < created for at in times):
        raise ValueError('operational_delivery_timestamp_before_order')
    if any(at < start for at in times):
        raise ValueError('operational_delivery_timestamp_before_start')
    if any(at > boundary for at in times):
        raise ValueError('operational_delivery_confirmed_evidence_changed' if recorded_at else 'operational_delivery_event_in_future')
    if len(set(times)) > 1:
        raise ValueError('operational_delivery_timestamp_ambiguous')
    if times and recorded_at is None:
        raise ValueError('operational_delivery_confirmation_not_required')
    return event


def confirmed_event(record, raw, **kwargs):
    validate_record(record, owner=kwargs['owner'], started_at=kwargs['started_at'], order_id=raw.get('id'))
    event = candidate_event(raw, **kwargs, recorded_at=record['confirmed_at'])
    if binding(event) != {key: record[key] for key in BINDING_FIELDS if key in record}:
        raise ValueError('operational_delivery_confirmation_source_changed')
    return {**event, 'confirmation_id': record['id'], 'confirmation_seal': record['seal']}


def validate_event(state, event):
    record = saved_record(state, event.get('order_id'))
    if (not record or event.get('schema') != EVENT_SCHEMA or event.get('recognition_basis') != BASIS
            or event.get('confirmation_id') != record['id'] or event.get('confirmation_seal') != record['seal']
            or event.get('delivered_at') is not None or event.get('recorded_at') != record['confirmed_at']
            or binding(event) != {key: record[key] for key in BINDING_FIELDS if key in record}):
        raise ValueError('operational_delivery_confirmation_invalid')
    return record


def annotate_pending(details, *, party_type, party_id, facts):
    """Saved evidence survives a failed refresh; metadata never changes money."""
    for row in details:
        record = facts.get(PREFIX + row['order_id'])
        if isinstance(record, dict) and record.get('party_type') == party_type and record.get('party_id') == party_id:
            try:
                validate_record(record, owner=record.get('owner_id'), started_at=record.get('started_at'), order_id=row['order_id'])
            except (ValueError, TypeError, KeyError):
                continue
            public = public_record({'engine': {'facts': facts}}, record)
            row['delivery_after_start_confirmation'] = {'can_confirm': False, 'state': public['state'],
                'party_type': party_type, 'party_id': party_id, 'confirmation_id': public['id'],
                'confirmed_at': public['confirmed_at']}
        elif (any(reason.get('code') in DATE_GAPS for reason in row['reasons'])
              and not any(str(reason.get('code', '')).startswith('operational_delivery_')
                          and reason.get('code') not in DATE_GAPS for reason in row['reasons'])):
            # A fresh preview still checks all current contradictions and binding.
            row['delivery_after_start_confirmation'] = {'can_confirm': True, 'state': 'available',
                                                       'party_type': party_type, 'party_id': party_id}


def date_gap_identity(out, code, raw, assignments, owner, *, record=None, started_at=None, assignment_index=None):
    """Keep a driver's date-only issue attached to its exact native assignment."""
    if record is not None:
        try:
            validate_record(record, owner=owner, started_at=started_at, order_id=raw.get('id'))
        except (ValueError, TypeError, KeyError):
            record = None
    if code not in DATE_GAPS and record is None:
        return
    from operational_delivery_evidence import _driver_assignment, _text
    oid, number = _text(raw.get('id')), _text(raw.get('reference_id') or raw.get('order_number'))
    if not oid or not number:
        return
    if record:
        kind, identity, number = record['party_type'], record['party_id'], record['order_number']
    else:
        try:
            if assignment_index is None:
                assignment = _driver_assignment(assignments, owner, oid, number)
            else:
                from operational_delivery_assignment_index import indexed_driver_assignment
                assignment = indexed_driver_assignment(assignments, owner, oid, number, assignment_index=assignment_index)
        except (ValueError, TypeError, KeyError):
            return
        if not assignment:
            return
        kind, identity = 'store_driver', assignment['driver_id']
    from operational_issue_index import issues_for_identity
    for issue in issues_for_identity(out, code, oid):
        if issue.get('code') == code and str(issue.get('source_id')) == oid:
            issue.pop('courier_fee_review', None)
            issue.pop('driver_fee_review', None)
            issue.update(order_id=oid, order_number=number, party_type=kind, party_id=identity, component='shipping_fee')
            if kind == 'store_driver':
                issue['currency'] = 'SAR'
