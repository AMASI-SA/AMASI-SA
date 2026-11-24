"""Evidence-backed prepaid courier charges, fully paid in one operational state."""
from copy import deepcopy
from decimal import Decimal
from zoneinfo import ZoneInfo

from courier_cod_fee_rules import split_tax_amount
from operational_courier_transitions import COMPLETED_PROOF
from operational_delivery_evidence import _courier, _current_shipping, _instant, _status
from operational_completed_shipping_admission import build_review_index, require_admission
from operational_order_completed_evidence import bind_completed_waybill, completion_time
from operational_aramex_status_shipping import (OWNER_STATUS_BASIS, STATUS_EVENT, STATUS_FIELDS, accounting_time, owner_rule_active,
    owner_status_observation, status_recognition)

SCHEMA = 'operational_prepaid_shipping_v1'


def source_event(data, setup, source, raw, state, owner, start, end, history, shipment_conflicts,
                 *, review_only=False, review_index=None, status_shipment_conflicts=None, assignment_index=None):
    """Return (prepaid identity, new charge). Source reads never move money."""
    if source.get('user_id') != owner or not state or state.get('owner_id') != owner:
        raise ValueError('operational_prepaid_shipping_owner_mismatch')
    oid = str(raw['id'])
    saved = (state or {}).get('engine', {}).get('facts', {}).get('prepaid_shipping:' + oid)
    if saved:
        return True, None  # Its immutable original cash leg survives later order changes.
    current = source.get('salla_shipping_current') or raw.get('salla_shipping_current') or {}
    prepaid = [rate for rate in setup.get('contracts', []) if rate.get('context') == 'order_completed'
               and rate.get('payment_mode') == 'prepaid']
    if owner_rule_active(state, owner):
        candidate = approved_aramex_charge(data, setup, source, raw, owner, start, end, current, prepaid)
        if candidate is not None:
            # The approved status policy must not conceal a genuine completion
            # timestamp acquired later. Bind the same carrier/waybill and price
            # that event at its original time before considering observation.
            try:
                from operational_delivery_assignment_index import has_active_assignment_for_number
                if has_active_assignment_for_number(data['store_delivery_assignments'],
                        str(raw.get('reference_id') or raw.get('order_number')), owner=owner,
                        assignment_index=assignment_index):
                    return False, None
                native_current, shipment, waybill = _current_shipping(raw, source, require_delivered=False, require_label=False)
                if (shipment and ('shipment', shipment) in shipment_conflicts
                        or waybill and ('waybill', waybill) in shipment_conflicts):
                    raise ValueError('operational_prepaid_shipping_shipment_shared_by_orders')
                at = completion_time(raw, source, history, end)
                bind_completed_waybill(raw, native_current, shipment, waybill, at)
            except ValueError:
                return True, candidate  # The writer keeps this undated pair pending.
            if at < start:
                return True, None
            original = priced_event(data, setup, raw, owner, start, at, candidate['party_id'],
                candidate['source_carrier_key'], shipment, waybill, {}, prepaid)
            if original is not None:
                if not review_only:
                    require_admission(state, original, review_index=review_index)
                return True, original
            return True, candidate
    keys = {str(key).casefold() for rate in prepaid for key in rate.get('keys', [])}
    if not any(str(current.get(key) or '').casefold() in keys for key in ('company_code', 'company_name')):
        return False, None
    # Native store delivery remains governed by its assignment/earnings writer.
    from operational_delivery_assignment_index import DeliveryAssignmentIndex, _plain_identity, has_active_assignment_for_number
    assignments = data['store_delivery_assignments']
    if (type(assignment_index) is DeliveryAssignmentIndex and assignment_index.matches(assignments, owner)
            and type(raw) is dict
            and _plain_identity(raw.get('reference_id')) and _plain_identity(raw.get('order_number'))):
        native_assignment = has_active_assignment_for_number(assignments,
            str(raw.get('reference_id') or raw.get('order_number')), owner=owner, assignment_index=assignment_index)
    else:
        # Keep ordinary callers and unusual identities at their original lazy conversion boundary.
        native_assignment = any(str(row.get('order_number')) == str(raw.get('reference_id') or raw.get('order_number'))
            and row.get('active') is not False and row.get('status') in {'assigned', 'out_for_delivery', 'delivered'}
            for row in assignments)
    if native_assignment:
        return False, None
    status = raw.get('status')
    has_completion = current.get(COMPLETED_PROOF) or any(
        isinstance(proof, dict) and proof.get('target_status') == 'completed'
        for row in history for proof in (row.get('native_status_transition') or [])
    )
    effective = status.get('customized') or status if isinstance(status, dict) else status
    if _status(effective) in {'pending', 'new', 'created', 'processing', 'in_progress', 'under_processing',
            'under_review', 'waiting_review', 'awaiting_payment', 'بانتظار المراجعة', 'جاري التجهيز', 'قيد التجهيز'} and not has_completion:
        return True, None  # An unexecuted order has no missing earned-charge proof.
    from operational_delivery_register import uses_retained_deliveries
    if uses_retained_deliveries(end):
        party, carrier_key = _courier(setup, owner, current)
        recognition = {}
        try:
            current, shipment, waybill = _current_shipping(raw, source, require_delivered=False, require_label=False)
            if ('shipment', shipment) in shipment_conflicts or ('waybill', waybill) in shipment_conflicts:
                raise ValueError('operational_prepaid_shipping_shipment_shared_by_orders')
            at = completion_time(raw, source, history, end)
            bind_completed_waybill(raw, current, shipment, waybill, at)
        except ValueError:
            if review_only or status_shipment_conflicts is None:
                raise
            from operational_delivery_evidence import _text
            shipment, waybill = _text(current.get('shipment_id')), _text(current.get('tracking_number'))
            recognition = status_recognition(raw, source, current, party, owner, start, end,
                                             shipment, waybill, status_shipment_conflicts, setup=setup)
            if recognition is None:
                raise
            at = end
    else:
        current, shipment, waybill = _current_shipping(raw, source, require_delivered=False, require_label=False)
        party, carrier_key = _courier(setup, owner, current)
        if ('shipment', shipment) in shipment_conflicts or ('waybill', waybill) in shipment_conflicts:
            raise ValueError('operational_prepaid_shipping_shipment_shared_by_orders')
        recognition = {}
        try:
            at = completion_time(raw, source, history, end)
            bind_completed_waybill(raw, current, shipment, waybill, at)
        except ValueError as error:
            if (str(error) != 'operational_prepaid_shipping_completion_timestamp_missing'
                    or review_only or status_shipment_conflicts is None):
                raise
            recognition = status_recognition(raw, source, current, party, owner, start, end,
                                             shipment, waybill, status_shipment_conflicts | set(shipment_conflicts), setup=setup)
            if recognition is None:
                raise
            at = end
    if at < start:
        return True, None
    event = priced_event(data, setup, raw, owner, start, at, party, carrier_key, shipment, waybill, recognition, prepaid)
    if not event:
        return False, None
    if not review_only:
        require_admission(state, event, review_index=review_index)
    return True, event


def approved_aramex_charge(data, setup, source, raw, owner, start, end, current, prepaid):
    # The current carrier must match the owner's approved Aramex contract.
    from shipping_companies import normalize_shipping_company
    from salla_shipping import extract_shipping
    current = current or extract_shipping(raw) or {}
    if not isinstance(current, dict):
        return None
    rates = [r for r in prepaid if r.get('party_id') == 'aramex']
    aliases = [str(k) for r in rates for k in r.get('keys', [])]
    name = str(current.get('company_name') or '')
    code = str(current.get('company_code') or '')
    carrier = normalize_shipping_company(name)[0] if name else None
    if carrier and carrier != 'aramex' and not carrier.startswith('other:'):
        return None
    key = next((k for k in aliases if k.casefold() in {code.casefold(), name.casefold()}), None)
    if not key and carrier == 'aramex':
        key = next((k for k in aliases if normalize_shipping_company(k)[0] == 'aramex'), None)
    if not key:
        return None
    party = 'aramex'
    observation = owner_status_observation(raw, source, party, owner, start, end, current)
    if not observation:
        return None
    return priced_event(data, setup, raw, owner, start, end, party, key, observation['shipment_id'],
                        observation['waybill'], observation, prepaid)


def priced_event(data, setup, raw, owner, start, at, party, carrier_key, shipment, waybill, recognition, prepaid):
    rates = [rate for rate in prepaid if rate.get('party_id') == party and rate.get('status') == 'approved'
             and _instant(rate['effective_from']) <= at
             and (not rate.get('effective_to') or at < _instant(rate['effective_to']))]
    overlap = [rate for rate in setup.get('contracts', []) if rate.get('context') == 'delivery'
               and rate.get('party_id') == party and rate.get('status') == 'approved'
               and _instant(rate['effective_from']) <= at
               and (not rate.get('effective_to') or at < _instant(rate['effective_to']))]
    if not rates and overlap:
        return None  # A later approved deferred period has its own delivery rule.
    if len(rates) != 1 or overlap:
        raise ValueError('operational_prepaid_shipping_tariff_ambiguous')
    if at < start:
        return None
    rate = rates[0]
    if (recognition.get('recognition_basis') == OWNER_STATUS_BASIS
            and _instant(rate['effective_from']) > _instant(recognition['order_created_at'])):
        raise ValueError('operational_prepaid_shipping_tariff_period_requires_review')
    account = rate.get('cash_account') or {}
    if (rate.get('confirmed_by') != owner or not rate.get('confirmed_at') or rate.get('currency') != 'SAR'
            or account.get('kind') != 'cash' or account.get('currency') != 'SAR' or not account.get('id')):
        raise ValueError('operational_prepaid_shipping_cash_binding_required')
    from operational_balance_sources import usable
    accounts = [row for row in data['mz2_financial_accounts'] if row.get('id') == account['id']]
    if (len(accounts) != 1 or accounts[0].get('user_id') != owner or accounts[0].get('account_type') != 'cash'
            or accounts[0].get('status') != 'active' or not usable(accounts[0]) or accounts[0].get('currency') != 'SAR'
            or accounts[0].get('ready') is False or not (accounts[0].get('display_name') or accounts[0].get('name'))):
        raise ValueError('operational_prepaid_shipping_cash_account_unavailable')
    base = split_tax_amount(rate['terms']['cost_per_order'], rate['terms']['vat_percent'], inclusive=False)
    event = {'schema': SCHEMA, 'owner_id': owner, 'order_id': str(raw['id']),
        'order_number': str(raw.get('reference_id') or raw.get('order_number')), 'party_type': 'courier', 'party_id': party,
        'party_name': rate['name'], 'currency': 'SAR', 'amount': str(base['gross']),
        'net_amount': str(base['net']), 'tax_amount': str(base['vat']),
        'completed_at': None if recognition else at.isoformat(), 'tariff_version_id': rate['version_id'],
        'cash_account': deepcopy(account), 'shipment_id': shipment, 'waybill': waybill,
        'source_carrier_key': carrier_key, **recognition}
    if not recognition and (not shipment or not waybill):
        event['completion_admission_required'] = True
    return event


def apply_events(state, events, start, now, issue):
    """Validate each entire expense/payment pair before changing either side.

    Called outside per-order calculation rollback. The enclosing owner CAS
    persists its fact, fully allocated movement and charge as one aggregate.
    """
    from operational_balance_store import audit

    engine = state['engine']
    facts, obligations = engine['facts'], engine['obligations']
    review_index = build_review_index(state) if events else None
    for event in events:
        oid = str(event.get('order_id') or '')
        fact_id, charge_id, movement_id = 'prepaid_shipping:' + oid, 'shipping-prepaid:' + oid, 'shipping-prepaid-payment:' + oid
        try:
            if not oid or event.get('schema') != SCHEMA or event.get('owner_id') != state['owner_id']:
                raise ValueError('operational_prepaid_shipping_event_invalid')
            if fact_id in facts:
                if facts[fact_id] != event:
                    raise ValueError('operational_prepaid_shipping_recorded_evidence_changed')
                continue
            # An observation proves a present status, not when the expense and
            # cash payment happened. Keep the entire new pair pending until a
            # native completion time is available; existing immutable pairs
            # were handled above and are never removed or redated.
            if event.get('recognition_basis') or not event.get('completed_at'):
                issue('operational_prepaid_shipping_original_date_unproven', oid,
                      party_type=event.get('party_type'), party_id=event.get('party_id'),
                      missing_fields=['completion_timestamp'])
                continue
            require_admission(state, event, review_index=review_index)
            if event.get('recognition_basis') == OWNER_STATUS_BASIS and not owner_rule_active(state, state['owner_id']):
                raise ValueError('operational_prepaid_shipping_owner_rule_required')
            at = accounting_time(event, start, now)
            versions = [row for row in state.get('courier_tariffs', []) if row.get('id') == event.get('party_id')]
            valid = [row for row in versions if row.get('version_id') == event.get('tariff_version_id')]
            if len(valid) != 1:
                raise ValueError('operational_prepaid_shipping_tariff_ambiguous')
            rate = valid[0]
            if (rate.get('payment_mode') != 'prepaid' or rate.get('currency') != 'SAR' or event.get('currency') != 'SAR'
                    or rate.get('confirmed_by') != state['owner_id'] or not rate.get('confirmed_at')
                    or _instant(rate['effective_from']) > at
                    or any(_instant(rate['effective_from']) < _instant(row['effective_from']) <= at for row in versions)
                    or rate.get('cash_account') != event.get('cash_account') or event.get('party_type') != 'courier'):
                raise ValueError('operational_prepaid_shipping_event_invalid')
            if event.get('recognition_basis'):
                key, aliases = event['source_carrier_key'], rate.get('keys') or []
                if (key not in aliases if rate.get('identity_match') == 'exact'
                        else key.casefold() not in {str(alias).casefold() for alias in aliases}):
                    raise ValueError('operational_prepaid_shipping_status_evidence_invalid')
            account = event['cash_account']
            if account.get('kind') != 'cash' or account.get('currency') != 'SAR' or not account.get('id'):
                raise ValueError('operational_prepaid_shipping_cash_binding_required')
            price = split_tax_amount(rate['terms']['cost_per_order'], rate['terms']['vat_percent'], inclusive=False)
            if (Decimal(event['amount']) != price['gross'] or Decimal(event['net_amount']) != price['net']
                    or Decimal(event['tax_amount']) != price['vat'] or price['gross'] <= 0):
                raise ValueError('operational_prepaid_shipping_amount_conflict')
            if (review_index.recorded_any_fee(oid) or review_index.recorded(oid) or charge_id in obligations
                    or movement_id in review_index.movement_ids or review_index.linked_payouts(event)):
                raise ValueError('operational_prepaid_shipping_existing_charge_review_required')
            mutable = review_index.shipping_keys[oid]
            if mutable & review_index.allocated:
                raise ValueError('operational_prepaid_shipping_existing_charge_review_required')
            gross = format(price['gross'], '.2f')
            by_status = bool(event.get('recognition_basis'))
            metadata = {'payment_mode': 'prepaid', 'recognition_event': STATUS_EVENT if by_status else 'order_completed', 'order_id': oid,
                        'order_number': event['order_number'], 'completed_at': event['completed_at'],
                        'tariff_version_id': rate['version_id'], 'cash_account_id': account['id'],
                        **({key: event[key] for key in ('shipment_id', 'waybill') if event.get(key)}),
                        **({key: event[key] for key in STATUS_FIELDS} if by_status else {})}
            charge = {'id': charge_id, 'kind': 'shipping', 'party_type': 'courier', 'party_id': event['party_id'],
                      'name': event['party_name'], 'currency': 'SAR', 'direction': 'payable', 'expected': '0.00',
                      'confirmed': gross, 'business_date': at.astimezone(ZoneInfo('Asia/Riyadh')).date().isoformat(),
                      'net_amount': format(price['net'], '.2f'), 'tax_amount': format(price['vat'], '.2f'), **metadata}
            movement = {'id': movement_id, 'request_id': movement_id, 'party_type': 'courier', 'party_id': event['party_id'],
                        'name': event['party_name'], 'currency': 'SAR', 'amount': gross, 'direction': 'outgoing',
                        'kind': 'payment', 'bank_kind': 'cash', 'bank_id': account['id'], 'bank_name': account['name'],
                        'source_account_type': 'cash', 'allocations': [{'obligation_id': charge_id, 'amount': gross}],
                        'actual_fee_amount': '0.00', 'actor_id': 'operational_order_worker', 'source': 'mezan2',
                        'occurred_at': at.isoformat(), 'business_date': charge['business_date'],
                        'receipt_id': None, 'reference': ('أجرة أرامكس المعتمدة بحالة الطلب ' if by_status
                                                        else 'أجرة شحن مدفوعة عند تنفيذ الطلب ') + event['order_number'],
                        'automatic_prepaid_shipping': True, **metadata}
        except (ValueError, KeyError, TypeError, ArithmeticError) as error:
            code = str(error) if str(error).startswith(('operational_prepaid_shipping_', 'operational_shipping_completion_')) else 'operational_prepaid_shipping_event_invalid'
            issue(code, oid)
            continue
        # No fallible order/payment processing follows this commit within the
        # helper. A duplicate owner CAS retries from the newly recorded fact.
        for key in mutable:
            del obligations[key]
        facts[fact_id] = deepcopy(event)
        obligations[charge_id] = charge
        state.setdefault('movements', []).append(movement)
        review_index.record(event, charge, movement)
        state.setdefault('audit', [])
        audit(state, 'operational_order_worker', 'prepaid_shipping_paid', None, deepcopy(movement),
              ('أجرة أرامكس المسبقة بحالة الطلب المنشأ بعد بدء التشغيل وفق العقد والصندوق المعتمدين' if by_status
               else 'أجرة الشحن المسبقة عند انتقال الطلب إلى تم التنفيذ وفق العقد والصندوق المعتمدين'), 'mz2', at.isoformat())
