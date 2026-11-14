"""Small completion-fee review reader and safe public diagnostic identities.

The owner review reads one canonical order and its related evidence. It never
refreshes Salla, scans product costs, reconciles finance, or writes an activation.
"""
from copy import deepcopy

from operational_completed_shipping_admission import recorded_base_fee
from operational_financial_activity_runtime import prepare_in_thread
from operational_issue_index import append_unique_issue


def public_candidate(event):
    if not event:
        return None
    fields = ('order_id', 'order_number', 'party_type', 'party_id', 'party_name', 'currency',
              'amount', 'completed_at', 'shipment_id', 'waybill')
    result = {key: event.get(key) for key in fields}
    prepaid = event.get('schema') == 'operational_prepaid_shipping_v1'
    result['payment_mode'] = 'prepaid' if prepaid else 'deferred'
    result['cash_account_name'] = (event.get('cash_account') or {}).get('name') if prepaid else None
    return result


def completion_issue(out, code, event, *, required_action=None):
    """Contract evidence remains internal; issues carry stable owner identities."""
    summary = public_candidate(event)
    summary.pop('cash_account_name', None)
    summary['candidate_amount'] = summary.pop('amount')
    entry = {'code': code, 'source_id': event['order_id'], 'status': 'incomplete',
             'component': 'shipping_fee', 'currency': event.get('currency'),
             'completed_shipping_review': summary,
             'required_action': required_action or 'راجع افتتاح الشركة والسدادات السابقة ثم اعتمد أجرة هذا الطلب من مراجعة أجور تم التنفيذ.'}
    append_unique_issue(out, entry)


def _values(value):
    result = [str(value)]
    if str(value).isascii() and str(value).isdigit():
        result.append(int(value))
    return result


async def _read(db, owner, name, query, projection, limit):
    rows = await db[name].find({'user_id': owner, **query}, projection).to_list(limit + 1)
    if len(rows) > limit:
        raise ValueError('operational_shipping_completion_review_scope_too_large')
    if any(row.get('user_id') != owner for row in rows):
        raise ValueError('operational_source_owner_mismatch')
    return rows


async def review_candidate(db, owner, state, number, as_of):
    """Return one exact candidate plus blockers using only relevant source rows."""
    from operational_balance_sources import instant
    from operational_completed_shipping import deferred_source_event
    from operational_courier_tariffs import with_tariffs
    from operational_courier_transitions import load_delivery_history
    from operational_delivery_evidence import conflicting_shipments
    from operational_historical_shipping_fee import native_conflicts
    from operational_prepaid_shipping import source_event

    if state.get('owner_id') != owner:
        raise ValueError('operational_shipping_completion_owner_mismatch')
    order_fields = {key: 1 for key in ('user_id', 'order_id', 'order_number', 'order_status', 'order_status_slug',
        'salla_order_id', 'salla_shipping_current',
        'raw_by_source.salla_direct', 'g47_salla_snapshot', 'salla_shipment_id', 'tracking_number', 'shipping_number',
        'shipping_company_code', 'shipping_company', 'shipping_status', 'shipment_status')}
    matches = await _read(db, owner, 'unified_orders', {'$or': [
        {path: {'$in': _values(number)}} for path in ('order_number',
            'raw_by_source.salla_direct.reference_id', 'raw_by_source.salla_direct.order_number')]}, order_fields, 2)
    if len(matches) != 1:
        return None, ['canonical_order_identity_ambiguous' if matches else 'operational_shipping_completion_order_missing'], None
    source = matches[0]
    raw = (source.get('raw_by_source') or {}).get('salla_direct')
    if not isinstance(raw, dict):
        return None, ['canonical_salla_source_missing'], None
    oid = str(raw.get('id') or '')
    if not oid or str(raw.get('reference_id') or raw.get('order_number') or '') != number:
        return None, ['operational_shipping_completion_order_identity_conflict'], None
    same_id = await _read(db, owner, 'unified_orders',
        {'raw_by_source.salla_direct.id': {'$in': _values(oid)}}, {'user_id': 1}, 2)
    if len(same_id) != 1:
        return None, ['canonical_order_identity_ambiguous'], None
    if (source.get('g47_salla_snapshot') or {}).get('requires_authoritative_refresh'):
        return None, ['canonical_salla_refresh_required'], None
    start, end = instant(state['started_at']), instant(as_of)
    if end < start or instant(raw.get('date') or raw.get('created_at') or raw.get('order_date')) > end:
        return None, ['operational_shipping_completion_time_invalid'], None
    facts = state.get('engine', {}).get('facts', {})
    saved = facts.get('completed_shipping:' + oid) or facts.get('prepaid_shipping:' + oid)
    if saved:
        return deepcopy(saved), [], 'applied'
    if await prepare_in_thread(recorded_base_fee, state, oid):
        return None, ['operational_shipping_completion_existing_fee'], None

    setup_rows = await _read(db, owner, 'mz2_shipping_setup_v2', {}, {'_id': 0}, 2)
    if len(setup_rows) > 1:
        return None, ['shipping_setup_ambiguous'], None
    setup, tariff_conflicts = with_tariffs(setup_rows[0] if setup_rows else {}, owner, state)
    if tariff_conflicts:
        # The source builder removes conflicting versions; retain its identity
        # resolution. An unrelated company's conflict does not block this order.
        current = source.get('salla_shipping_current') or raw.get('salla_shipping_current') or {}
        from operational_delivery_evidence import _courier
        party, _ = _courier(setup, owner, current)
        if party in tariff_conflicts:
            return None, ['operational_courier_tariff_authority_conflict'], None
    assignments = await _read(db, owner, 'store_delivery_assignments', {'$or': [
        {'order_number': {'$in': _values(number)}}, {'order_id': {'$in': _values(oid)}}]}, {'_id': 0}, 100)
    account_ids = sorted({str((rate.get('cash_account') or {}).get('id')) for rate in setup.get('contracts', [])
                          if (rate.get('cash_account') or {}).get('id')})
    accounts = await _read(db, owner, 'mz2_financial_accounts', {'id': {'$in': account_ids}}, {'_id': 0}, 100) if account_ids else []
    current = source.get('salla_shipping_current') or raw.get('salla_shipping_current') or {}
    refs = []
    for field, paths in (
        ('shipment_id', ('salla_shipping_current.shipment_id', 'raw_by_source.salla_direct.salla_shipping_current.shipment_id',
                         'salla_shipment_id', 'raw_by_source.salla_direct.shipping.shipment_id',
                         'raw_by_source.salla_direct.shipments.id')),
        ('tracking_number', ('salla_shipping_current.tracking_number', 'raw_by_source.salla_direct.salla_shipping_current.tracking_number',
                             'tracking_number', 'shipping_number',
                             'raw_by_source.salla_direct.shipping.tracking_number', 'raw_by_source.salla_direct.shipments.tracking_number')),
    ):
        if isinstance(current, dict) and current.get(field):
            refs.extend({path: {'$in': _values(current[field])}} for path in paths)
    siblings = await _read(db, owner, 'unified_orders', {'$or': refs}, order_fields, 100) if refs else []
    sources = {str(row.get('_id') or (row.get('raw_by_source') or {}).get('salla_direct', {}).get('id')): row
               for row in [source, *siblings]}
    conflicts = conflicting_shipments(list(sources.values()), state)
    completion_keys = {str(key).casefold() for rate in setup.get('contracts', [])
                       if rate.get('party_type') == 'courier' and rate.get('context') in {'delivery', 'order_completed'}
                       for key in rate.get('keys', [])}
    history = await load_delivery_history(db, owner, [source], max_rows=1000, completion_keys=completion_keys)
    data = {'store_delivery_assignments': assignments, 'mz2_financial_accounts': accounts}
    args = (data, setup, source, raw, state, owner, start, end, history.get(number, ()), conflicts)
    prepaid, event = await prepare_in_thread(source_event, *args, review_only=True)
    if not prepaid:
        event = await prepare_in_thread(deferred_source_event, *args, review_only=True)
    if not event:
        return None, ['operational_shipping_completion_candidate_unavailable'], None
    native = await native_conflicts(db, owner, [event], max_rows=20000)
    return event, ([native[number]] if number in native else []), None
