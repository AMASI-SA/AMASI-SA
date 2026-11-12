"""A typed order completion proves prepaid shipping time, never customer cash."""
from copy import deepcopy
from datetime import datetime

from operational_courier_transitions import COMPLETED_PROOF, completed_state, native_status_transition, transition_observation
from operational_delivery_evidence import _instant, _order_waybill, _text
from operational_completed_capture import SOURCE as CAPTURE_SOURCE, capture_time, capture_waybill_view

HISTORY_SOURCE = 'verified_order_completed_history'
WEBHOOK_SOURCE = 'verified_order_completed_update'


def _source_version(raw):
    date = raw.get('date') if isinstance(raw.get('date'), dict) else {}
    return _instant(raw.get('updated_at') or date.get('updated') or raw.get('date_updated'))


def _label_free_scope(current):
    if current.get('shipment_id') and current.get('tracking_number'):
        return {}
    return {'evidence_scope': 'order_completion',
            'carrier_code': current.get('company_code'), 'carrier_name': current.get('company_name')}


def _proof_identity_matches(observation, current):
    if observation.get('evidence_scope') == 'order_completion':
        return (observation.get('source') in {HISTORY_SOURCE, WEBHOOK_SOURCE}
                and (observation.get('carrier_code') or observation.get('carrier_name'))
                and observation.get('carrier_code') == current.get('company_code')
                and observation.get('carrier_name') == current.get('company_name'))
    return (observation.get('shipment_id') == current.get('shipment_id')
            and observation.get('waybill') == current.get('tracking_number'))


def bind_completed_waybill(raw, current, shipment, waybill, at):
    """The original prepaid label may follow completion within this snapshot."""
    version = _source_version(raw)
    if at > version:
        raise ValueError('operational_prepaid_shipping_completion_time_invalid')
    # Completion evidence belongs to the order even before a label exists.
    # If a complete label is present, contradictory original-label evidence
    # remains a real source conflict; deferred delivery keeps its own rules.
    if not shipment or not waybill:
        return
    # Bound label creation to the canonical source version. The authoritative
    # completion time still controls cutover, tariff selection and recognition.
    observation = current.get(COMPLETED_PROOF) or {}
    current_view = capture_waybill_view(raw, current, version) if (
        observation.get('source') == CAPTURE_SOURCE and 'shipment' in observation) else raw
    _order_waybill(current_view, current, shipment, waybill, version)


def completion_time(raw, source, history, end):
    oid, number = _text(raw.get('id')), _text(raw.get('reference_id') or raw.get('order_number'))
    if (not oid or not number or not source.get('user_id')
            or any(source.get(k) is not None and _text(source[k]) != expected
                   for k, expected in (('order_id', oid), ('salla_order_id', oid), ('order_number', number)))):
        raise ValueError('operational_prepaid_shipping_order_identity_conflict')
    version = _source_version(raw)
    watermark = source.get('g47_salla_snapshot') or {}
    if (any(watermark.get(k) for k in ('cancelled', 'component_pending', 'requires_authoritative_refresh'))
            or watermark.get('source_updated_at') is not None and _instant(watermark['source_updated_at']) != version
            or version > end):
        raise ValueError('operational_prepaid_shipping_source_stale')
    status = raw.get('status')
    effective = status.get('customized') or status if isinstance(status, dict) else status
    times = set()
    if completed_state(effective) is True:
        if any(completed_state(value) is not True for value in (raw.get('status_slug'), source.get('order_status'),
               source.get('order_status_slug')) if value is not None):
            raise ValueError('operational_prepaid_shipping_order_status_conflict')
        changed = effective.get('changed_at') if isinstance(effective, dict) else None
        times.update(_instant(value) for value in (changed, raw.get('status_changed_at')) if value is not None)
    for row in history:
        if row.get('provider_order_id') != oid or row.get('order_number') != number:
            raise ValueError('operational_prepaid_shipping_order_identity_conflict')
        proofs = row.get('native_status_transition')
        if not _text(row.get('source_event_id')):
            raise ValueError('operational_prepaid_shipping_completion_ambiguous')
        proof = transition_observation(proofs)
        # Legacy delivered=False could mean cancelled, shipped or anything else.
        # Only the typed target retained by the verified producer proves completed.
        if isinstance(proof, dict) and proof.get('target_status') == 'completed':
            if proof.get('delivered') is not False or proof.get('source'):
                raise ValueError('operational_prepaid_shipping_completion_ambiguous')
            times.add(_instant(proof.get('at')))
    current = source.get('salla_shipping_current') or {}
    observation = current.get(COMPLETED_PROOF)
    if observation is not None:
        if (not isinstance(observation, dict) or observation.get('ambiguous')
                or observation.get('source') not in {HISTORY_SOURCE, WEBHOOK_SOURCE, CAPTURE_SOURCE}
                or observation.get('owner') != source['user_id'] or observation.get('order_id') != oid
                or observation.get('order_number') != number or observation.get('target_status') != 'completed'
                or not _proof_identity_matches(observation, current)):
            raise ValueError('operational_prepaid_shipping_completion_ambiguous')
        if observation['source'] == HISTORY_SOURCE:
            if (observation.get('history_complete') is not True or not isinstance(observation.get('events'), list)
                    or not observation['events'] or _instant(observation.get('source_updated_at')) > version):
                raise ValueError('operational_prepaid_shipping_completion_ambiguous')
            detached = {**source, 'salla_shipping_current': {k: v for k, v in current.items() if k != COMPLETED_PROOF}}
            if completion_time(raw, detached, observation['events'], end) != _instant(observation.get('at')):
                raise ValueError('operational_prepaid_shipping_completion_ambiguous')
        elif observation['source'] == CAPTURE_SOURCE:
            times.add(capture_time(observation, source['user_id'], oid, number, version, raw, current, source))
        times.add(_instant(observation.get('at')))
    if not times:
        raise ValueError('operational_prepaid_shipping_completion_timestamp_missing')
    if len(times) != 1:
        raise ValueError('operational_prepaid_shipping_completion_ambiguous')
    at = times.pop()
    created = _instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
    if not created <= at <= version <= end:
        raise ValueError('operational_prepaid_shipping_completion_time_invalid')
    return at


def verified_completed_webhook(body, raw, current, owner):
    if body.get('event') != 'order.status.updated' or not _text(owner) or not isinstance(current, dict):
        return None
    data = body.get('data') or {}
    if not isinstance(data, dict):
        return None
    status = raw.get('status')
    effective = status.get('customized') or status if isinstance(status, dict) else status
    target = data.get('to_status') or data.get('new_status') or data.get('status')
    target = target.get('customized') or target if isinstance(target, dict) else target
    oid, number = _text(raw.get('id')), _text(raw.get('reference_id') or raw.get('order_number'))
    if (not oid or not number or completed_state(effective) is not True or completed_state(target) is not True
            or any(data.get(k) is not None and _text(data[k]) != value
                   for k, value in (('order_id', oid), ('order_number', number), ('reference_id', number)))):
        return None
    try:
        stamp = body.get('created_at')
        if isinstance(stamp, str) and ' GMT' in stamp:
            stamp = datetime.strptime(stamp, '%a %b %d %Y %H:%M:%S GMT%z')
        at = _instant(stamp)
        bind_completed_waybill(raw, current, current.get('shipment_id'), current.get('tracking_number'), at)
    except (TypeError, ValueError):
        return None
    return {'source': WEBHOOK_SOURCE, 'owner': owner, 'order_id': oid, 'order_number': number,
            'shipment_id': current.get('shipment_id'), 'waybill': current.get('tracking_number'),
            'at': at.isoformat(), 'target_status': 'completed', **_label_free_scope(current)}


def verified_completed_history(source, histories, owner, now):
    raw = (source.get('raw_by_source') or {}).get('salla_direct') or {}
    current = source.get('salla_shipping_current') or {}
    oid, number = _text(raw.get('id')), _text(source.get('order_number'))
    if source.get('user_id') != owner or not oid or not number:
        raise ValueError('operational_prepaid_shipping_order_identity_conflict')
    events = []
    for row in histories:
        if row.get('order_id') is not None and _text(row['order_id']) != oid:
            raise ValueError('operational_prepaid_shipping_order_identity_conflict')
        proof = native_status_transition(row, oid)
        if proof is not None:
            events.append({'provider_order_id': oid, 'order_number': number,
                           'source_event_id': _text(row.get('id') or row.get('history_id')),
                           'native_status_transition': [proof]})
    if not any(row['native_status_transition'][0].get('target_status') == 'completed' for row in events):
        raise ValueError('operational_prepaid_shipping_completion_timestamp_missing')
    at = completion_time(raw, source, events, now)
    bind_completed_waybill(raw, current, current.get('shipment_id'), current.get('tracking_number'), at)
    result = deepcopy(current)
    result[COMPLETED_PROOF] = {'source': HISTORY_SOURCE, 'owner': owner, 'order_id': oid, 'order_number': number,
        'shipment_id': current.get('shipment_id'), 'waybill': current.get('tracking_number'), 'at': at.isoformat(),
        'target_status': 'completed', 'source_updated_at': source['g47_salla_snapshot']['source_updated_at'],
        'history_complete': True, 'events': sorted(events, key=lambda row: row['source_event_id']),
        **_label_free_scope(current)}
    return result
