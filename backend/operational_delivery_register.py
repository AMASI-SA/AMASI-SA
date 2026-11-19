"""One Salla delivery per post-opening order, independent of money and later status."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from operational_delivery_evidence import DELIVERED, _instant, _status, _text
from shipping_companies import matches_courier_alias, normalize_shipping_company

MARKER = 'salla_first_delivered'
PREFIX = 'salla_delivered_order:'
POLICY = 'salla_delivery_register_v1'
STATUS_LABELS = {'canceled': 'ملغي', 'cancelled': 'ملغي', 'returned': 'مسترجع', 'refunded': 'مسترجع',
                 'restored': 'مسترجع', 'completed': 'تم التنفيذ', 'delivered': 'تم التوصيل',
                 'delivering': 'جاري التوصيل', 'out_for_delivery': 'جاري التوصيل',
                 'in_progress': 'قيد التنفيذ', 'under_processing': 'قيد التنفيذ'}


# The owner changed the operating rule for the November 2026 opening.
# Choose the rules of an archived calculation by its reporting instant.
# For current calculations, the actual configured opening is the only order cutoff.
RULE_IN_FORCE_FROM = datetime(2026, 11, 1, tzinfo=timezone(timedelta(hours=3)))


def uses_retained_deliveries(as_of):
    return as_of >= RULE_IN_FORCE_FROM


def order_status(raw):
    value = raw.get('status')
    if isinstance(value, dict):
        value = value.get('customized') or value
    if isinstance(value, dict):
        key = _status(value) or _status(value.get('name_ar') or value.get('label'))
        name = _text(value.get('name_ar') or value.get('name') or value.get('label'))
    else:
        key, name = _status(value), None
    if key.replace('_', ' ') in DELIVERED or name and name.replace('_', ' ').strip() in DELIVERED:
        key = 'delivered'
    return key, name or STATUS_LABELS.get(key) or key or 'غير محددة'


def carrier_fields(raw, current):
    shipping = raw.get('shipping') or {}
    values = {}
    for item in (current, raw.get('salla_shipping_current'), shipping):
        if isinstance(item, dict):
            company = item.get('company')
            values = {'company_code': _text(item.get('company_code') or (company.get('code') if isinstance(company, dict) else None)),
                      'company_name': _text(item.get('company_name') or (company.get('name') if isinstance(company, dict) else company))}
            if any(values.values()):
                break
    return {key: value for key, value in values.items() if value}


def first_observation(raw, shipping):
    if not isinstance(raw, dict) or order_status(raw)[0] not in DELIVERED or not _text(raw.get('id')):
        return None
    return {'order_id': _text(raw['id']), **carrier_fields(raw, shipping)}


async def bind_first_driver(db, owner, number, marker):
    # Capture a native assignment at intake so a reassignment before the next
    # balances refresh cannot transfer the delivery to the second driver.
    key = normalize_shipping_company(marker.get('company_name') or marker.get('company_code'))[0]
    if key not in {'mandoob', 'mandoob_riyadh', 'unknown'}:
        return marker
    fields = {k: 1 for k in ('user_id', 'order_number', 'order_id', 'id', 'driver_id', 'driver_name_snapshot',
                              'active', 'status', 'delivered_at')}
    rows = await db['store_delivery_assignments'].find({'user_id': owner, 'order_number': number}, fields).to_list(None)
    party = executor(marker, rows, marker['order_id'], {})
    if party.get('party_type') == 'store_driver' and party.get('party_id'):
        return {**marker, 'driver_id': party['party_id'], 'assignment_id': party.get('assignment_id'),
                'driver_name': party.get('party_name')}
    return marker


def history_delivery(history, oid, number):
    for row in history:
        if str(row.get('provider_order_id')) == oid and str(row.get('order_number')) == number:
            for proof in row.get('native_status_transition') or ():
                if isinstance(proof, dict) and proof.get('delivered') is True:
                    return True
    return False


def assignment_groups(assignments, owner):
    by_number = defaultdict(list)
    for assignment in assignments:
        if assignment.get('user_id') == owner:
            by_number[str(assignment.get('order_number'))].append(assignment)
    return by_number


def executor(carrier, assignments, oid, setup, *, historical=False):
    candidates = [row for row in assignments if row.get('driver_id')
                  and str(row.get('order_id') or oid) == oid]
    delivered = [row for row in candidates if _status(row.get('status')) in DELIVERED or row.get('delivered_at')]
    code, name = carrier.get('company_code'), carrier.get('company_name')
    key, display = normalize_shipping_company(name or code)
    matching = {row['courier_key'] for row in setup.get('couriers', [])
                if row.get('courier_key') and (matches_courier_alias(row, code) or matches_courier_alias(row, name))}
    native = delivered or [row for row in candidates if row.get('active') is True and not historical]
    drivers = {str(row['driver_id']) for row in native}
    if len(drivers) == 1 and (delivered or not matching):
        row = native[0]
        return {'party_type': 'store_driver', 'party_id': str(row['driver_id']),
                'party_name': row.get('driver_name_snapshot') or str(row['driver_id']),
                'assignment_id': _text(row.get('id'))}
    if drivers or key in {'mandoob', 'mandoob_riyadh', 'pickup'}:
        return {'party_type': 'store_driver'}
    if len(matching) == 1 or not matching and key != 'unknown':
        return {'party_type': 'courier', 'party_id': next(iter(matching)) if matching else key,
                'party_name': name or display, 'source_carrier_key': code or name}
    return {}


def observe(source, raw, start, end, history, assignments, setup, facts, previous=None):
    oid = _text(raw.get('id'))
    if not oid:
        return None
    created = _instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
    if not start < created <= end:
        return None
    number = _text(raw.get('reference_id') or raw.get('order_number')) or oid
    key, label = order_status(raw)
    saved = (previous if previous is not None else facts).get(PREFIX + oid) or {}
    marker = source.get(MARKER) or {}
    if marker.get('order_id') != oid:
        marker = {}
    old = facts.get('delivery_event:' + oid) or {}
    frozen = facts.get('shipping:' + oid) or {}
    prior_status = old.get('recognition_basis') == 'delivered_order_created_after_start'
    if not (saved or marker or prior_status or key in DELIVERED or history_delivery(history, oid, number)):
        return None
    record = dict(saved)
    if not record.get('party_id'):
        if old.get('party_id') or frozen.get('id'):
            proof = old if old.get('party_id') else frozen
            record.update(party_type=proof.get('party_type'), party_id=old.get('party_id') or frozen.get('id'))
            for field in ('assignment_id', 'source_carrier_key'):
                if proof.get(field):
                    record[field] = proof[field]
        else:
            current = source.get('salla_shipping_current') or {}
            carrier = carrier_fields(raw, current)
            if key not in DELIVERED and (current.get('superseded_shipment_ids')
                    or current.get('shipment_type') in {'return', 'reverse', 'return_shipment'}):
                carrier = {}
            carrier = marker or carrier
            if marker.get('driver_id'):
                record.update(party_type='store_driver', party_id=marker['driver_id'],
                              assignment_id=marker.get('assignment_id'), party_name=marker.get('driver_name'))
            else:
                original_carrier = normalize_shipping_company(marker.get('company_name') or marker.get('company_code'))[0]
                captured_external = bool(marker and original_carrier not in {'unknown', 'mandoob', 'mandoob_riyadh', 'pickup'})
                record.update(executor(carrier, () if captured_external else assignments, oid, setup,
                                       historical=key not in DELIVERED))
    record.update(order_id=oid, order_number=number, order_created_at=created.isoformat(),
                  recorded_at=saved.get('recorded_at') or end.isoformat())
    if not saved and not frozen and not old:
        native = next((a for a in assignments if record.get('assignment_id')
                       and a.get('id') == record['assignment_id']), {})
        stamp = native.get('delivered_at') or (raw.get('status_changed_at') if key in DELIVERED else None)
        if stamp:
            try:
                at = _instant(stamp)
                if created <= at <= end:
                    record['delivered_at'] = at.isoformat()
            except (TypeError, ValueError):
                pass
    if key or not saved:
        record.update(current_status=key, current_status_name=label)
    return record


def service_event(record, raw, facts):
    oid = record['order_id']
    old = facts.get('delivery_event:' + oid)
    if old:
        return old
    if not record.get('party_id'):
        return None
    frozen = facts.get('shipping:' + oid) or {}
    currency = raw.get('currency') or (raw.get('amounts') or {}).get('total', {})
    if isinstance(currency, dict):
        currency = currency.get('currency')
    if not currency:
        return None
    from operational_delivery_evidence import STATUS_BASIS, STATUS_SCHEMA
    event = {k: record[k] for k in ('order_id', 'order_number', 'order_created_at', 'party_type', 'party_id',
                                     'source_carrier_key', 'assignment_id') if record.get(k)}
    event.update(id='salla-delivery:' + oid, currency=currency, delivered_at=frozen.get('delivered_at'))
    if not frozen and record.get('delivered_at'):
        event['delivered_at'] = record['delivered_at']
    if event['delivered_at']:
        event['schema'] = 'operational_delivery_event_v1'
    else:
        event.update(schema=STATUS_SCHEMA, recognition_basis=STATUS_BASIS,
                     recorded_at=frozen.get('recorded_at') or record['recorded_at'])
    return event


def notice(facts, view):
    scope = {'drivers': 'store_driver', 'couriers': 'courier'}.get(view)
    rows = [record for key, record in facts.items() if key.startswith(PREFIX)
            and (not scope or record.get('party_type') == scope)]
    groups = {'canceled': [], 'restored': []}
    aliases = {'canceled': 'canceled', 'cancelled': 'canceled', 'ملغي': 'canceled', 'ملغى': 'canceled',
               'تم الإلغاء': 'canceled', 'تم الالغاء': 'canceled', 'مكنسل': 'canceled',
               'restored': 'restored', 'returned': 'restored', 'مسترجع': 'restored', 'مرتجع': 'restored',
               'تم الاسترجاع': 'restored'}
    for record in rows:
        category = aliases.get(record.get('current_status')) or aliases.get(record.get('current_status_name'))
        if category:
            groups[category].append({k: record.get(k) for k in
                ('order_id', 'order_number', 'current_status_name', 'party_name', 'party_id')})
    for items in groups.values():
        items.sort(key=lambda item: item['order_number'])
    return {'groups': groups, 'changed': groups['canceled'] + groups['restored'],
            'unassigned': sum(not row.get('party_id') for row in rows), 'count': len(rows)}
