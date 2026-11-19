"""Owner-authorized post-start Aramex fees, independent of execution chronology."""
from collections import defaultdict

from operational_delivery_evidence import _courier, _inactive, _instant, _shipment_rows, _text
from operational_order_completed_evidence import _source_version
from salla_shipping import extract_shipping, same_carrier
from shipping_companies import normalize_shipping_company

STATUS_BASIS = 'aramex_status_created_after_start'
STATUS_EVENT = 'aramex_status_eligible'
STATUS_FIELDS = ('recognition_basis', 'recorded_at', 'order_created_at', 'observed_order_status',
                 'source_updated_at', 'identity_scope')
_ALIASES = {
    'completed': {'completed', 'تم التنفيذ', 'تم_التنفيذ', 'منتهي', 'مكتمل'},
    'out_for_delivery': {'out_for_delivery', 'in_delivery', 'shipping', 'delivering',
                         'جاري التوصيل', 'جاري_التوصيل', 'جارٍ التوصيل', 'قيد التوصيل'},
    'delivered': {'delivered', 'تم التوصيل', 'تم_التوصيل'},
}


def _fail(reason):
    raise ValueError('operational_prepaid_shipping_' + reason)


def current_status(value):
    """The customized state overrides its parent; conflicting labels never win."""
    if isinstance(value, dict):
        value = value.get('customized') or value
    values = [value.get(key) for key in ('slug', 'code', 'name', 'name_ar', 'label', 'title')
              if value.get(key) is not None] if isinstance(value, dict) else [value]
    states = {next((state for state, aliases in _ALIASES.items()
                    if isinstance(label, str) and ' '.join(label.strip().casefold().split()) in aliases), None)
              for label in values}
    return states.pop() if len(states) == 1 else None


def _reference_rows(raw, *, include_projections=True):
    """Enumerate provider reference locations, never the order's own id."""
    shipping = raw.get('shipping') or {}
    shipments = raw.get('shipments') or []
    rows = list(shipments) if isinstance(shipments, list) else [shipments] if shipments else []
    rows.extend(row for row in (raw.get('shipment'),) if row is not None)
    if include_projections and raw.get('salla_shipping_current') is not None:
        rows.append(raw['salla_shipping_current'])
    if isinstance(shipping, dict):
        rows.extend(row for row in (shipping.get('shipment'), {**shipping, 'id': shipping.get('shipment_id')}) if row is not None)
    elif shipping:
        rows.append(shipping)
    if include_projections:
        rows.append({'id': raw.get('salla_shipment_id'), **{key: raw.get(key) for key in (
            'tracking_number', 'tracking_id', 'shipping_number', 'awb', 'tracking', 'shipping_company_code', 'shipping_company')}})
        rows.extend({'status': raw[key]} for key in ('shipping_status', 'shipment_status') if raw.get(key) is not None)
    return rows


def _references(row):
    tracking = row.get('tracking') or {}
    return ([('shipment', row.get(key)) for key in ('id', 'shipment_id')]
            + [('waybill', row.get(key)) for key in ('tracking_number', 'tracking_id', 'shipping_number', 'awb')]
            + [('waybill', tracking.get('number') if isinstance(tracking, dict) else None)])


def status_reference_conflicts(sources, state):
    """Index all present current/raw/frozen references once for the new policy."""
    seen = defaultdict(set)
    for source in sources:
        raw = (source.get('raw_by_source') or {}).get('salla_direct')
        if not isinstance(raw, dict) or not (oid := _text(raw.get('id'))):
            continue
        current = source.get('salla_shipping_current') or {}
        for row in [current, *_reference_rows(raw)]:
            if isinstance(row, dict):
                for kind, value in _references(row):
                    if _text(value):
                        seen[(kind, _text(value))].add(oid)
    for key, fact in (state or {}).get('engine', {}).get('facts', {}).items():
        if (key.startswith(('delivery_event:', 'prepaid_shipping:', 'completed_shipping:'))
                and isinstance(fact, dict) and fact.get('party_type') == 'courier' and _text(fact.get('order_id'))):
            for kind, value in (('shipment', fact.get('shipment_id')), ('waybill', fact.get('waybill'))):
                if _text(value):
                    seen[(kind, _text(value))].add(_text(fact['order_id']))
    return {key for key, orders in seen.items() if len(orders) > 1}


def _same_status_carrier(current, observed, setup, owner):
    names = [' '.join(str(row.get('company_name') or '').casefold().split()) for row in (current, observed)]
    # The approved exact carrier mapping grants authority. Name normalization
    # only detects contradictory provider labels and supports native variants.
    if any(normalize_shipping_company(name)[0] != 'aramex' for name in names if name):
        return False
    if current.get('company_code') and observed.get('company_code'):
        return str(current['company_code']) == str(observed['company_code'])
    if same_carrier(current, observed):
        return True
    # Code-only and name-only native shapes must independently resolve through
    # the owner's approved mapping, never through a display-name substring.
    try:
        return _courier(setup, owner, observed)[0] == 'aramex'
    except ValueError:
        return False


def _bind_status_identity(raw, current, shipment, waybill, version, shipment_conflicts, *, setup, owner, as_of):
    """Require agreement of every present reference without inventing a label."""
    observed = extract_shipping(raw)
    if (current.get('superseded_shipment_ids') or not _same_status_carrier(current, current, setup, owner)
            or observed and not _same_status_carrier(current, observed, setup, owner)):
        _fail('status_identity_conflict')
    # Canonical current shipping is independently maintained by shipment reads;
    # sparse order envelopes need not repeat its carrier, AWB, or provider clock.
    if any(_instant(current[key]) > as_of for key in ('provider_updated_at', 'carrier_updated_at', 'shipment_updated_at')
           if current.get(key) is not None):
        _fail('status_identity_conflict')
    rows = _reference_rows(raw)
    if observed:
        rows.append({**observed, 'id': observed.get('shipment_id')})
    identities = {'shipment': {shipment} if shipment else set(), 'waybill': {waybill} if waybill else set()}
    for row in rows:
        if not isinstance(row, dict) or _inactive(row):
            _fail('status_identity_conflict')
        carrier = extract_shipping(row, event_name='shipment.updated')
        if (carrier and (carrier.get('company_code') or carrier.get('company_name'))
                and not _same_status_carrier(current, carrier, setup, owner)):
            _fail('status_identity_conflict')
        tracking = row.get('tracking') or {}
        if not isinstance(tracking, dict):
            _fail('status_identity_conflict')
        for kind, value in _references(row):
            if value is not None:
                if not _text(value):
                    _fail('status_identity_conflict')
                identities[kind].add(_text(value))
        if row.get('created_at') is not None and _instant(row['created_at']) > version:
            _fail('status_identity_conflict')
    if any(len(values) > 1 for values in identities.values()):
        _fail('status_identity_conflict')
    if any((kind, value) in shipment_conflicts for kind, values in identities.items() for value in values):
        _fail('shipment_shared_by_orders')
    native_label = any(isinstance(row, dict) and _text(row.get('id') or row.get('shipment_id'))
                       and any(kind == 'waybill' and _text(value) for kind, value in _references(row))
                       for row in _reference_rows(raw, include_projections=False))
    if shipment and waybill and native_label:
        candidates, selected, nested = _shipment_rows(raw, shipment)
        # The generic display extractor can omit a code-only shipment beneath
        # a name-only carrier. Every selected native row was independently
        # bound to the approved party above; require the original AWB itself.
        original_awb = any(kind == 'waybill' and _text(value) == waybill
                           for row in selected for kind, value in _references(row))
        if (len(selected) != len(candidates) or not original_awb
                or (observed or {}).get('shipment_id') not in (None, shipment)
                or (observed or {}).get('tracking_number') not in (None, waybill)
                or nested and _text(nested.get('id') or nested.get('shipment_id')) != shipment):
            _fail('status_identity_conflict')
    return {key: next(iter(identities[kind]), None) for key, kind in (('shipment_id', 'shipment'), ('waybill', 'waybill'))}


def status_recognition(raw, source, current, party, owner, start, end, shipment, waybill, shipment_conflicts, *, setup):
    """Return a distinct accounting observation only for the explicitly admitted cohort."""
    if party != 'aramex':
        return None
    status = current_status(raw.get('status'))
    if status is None:
        return None
    created = _instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
    if created <= start:
        return None
    oid, number = _text(raw.get('id')), _text(raw.get('reference_id') or raw.get('order_number'))
    canonical = (source.get('raw_by_source') or {}).get('salla_direct')
    if (not oid or not number or source.get('user_id') != owner or canonical != raw
            or any(source.get(key) is not None and _text(source[key]) != expected
                   for key, expected in (('order_id', oid), ('salla_order_id', oid), ('order_number', number)))):
        _fail('order_identity_conflict')
    if created > end:
        return None
    # Execution of an order assigned to Aramex earns one prepaid charge.
    # References are descriptive: replaced labels and pending component refreshes
    # cannot undo an observed eligible Salla order state.
    try:
        version = _source_version(raw)
    except (TypeError, ValueError, KeyError):
        version = None
    return {'recognition_basis': STATUS_BASIS, 'recorded_at': end.isoformat(),
            'order_created_at': created.isoformat(), 'observed_order_status': status,
            'source_updated_at': version.isoformat() if version and created <= version <= end else None,
            'identity_scope': 'order_status', 'shipment_id': shipment, 'waybill': waybill}


def accounting_time(event, start, now):
    """Validate the new basis independently before its charge/payment pair is applied."""
    if event.get('recognition_basis') is None:
        at = _instant(event['completed_at'])
        if not start <= at <= now:
            _fail('completion_time_invalid')
        return at
    if (event.get('recognition_basis') != STATUS_BASIS or event.get('completed_at') is not None
            or event.get('party_id') != 'aramex' or event.get('identity_scope') != 'order_status'
            or event.get('observed_order_status') not in _ALIASES or not _text(event.get('source_carrier_key'))
            or event.get('completion_admission_required')):
        _fail('status_evidence_invalid')
    created, at = (_instant(event[key]) for key in ('order_created_at', 'recorded_at'))
    version = event.get('source_updated_at')
    if not start < created <= at <= now or version and not created <= _instant(version) <= at:
        _fail('status_evidence_invalid')
    return at
