"""Deferred base courier fees at proved completion, independent of COD.

This source never manufactures a delivery event. The existing delivered-order
reader continues to own COD evidence and any collection commission. Only the
base shipping component is recognized here, once per order.
"""
from copy import deepcopy
from decimal import Decimal
from zoneinfo import ZoneInfo

from operational_balance_store import digest
from operational_completed_shipping_admission import build_review_index, recorded_base_fee, require_admission, review
from operational_courier_transitions import COMPLETED_PROOF, completed_state
from operational_delivery_evidence import _courier, _current_shipping, _instant
from operational_order_completed_evidence import bind_completed_waybill, completion_time

SCHEMA = 'operational_completed_shipping_v1'


def _has_completion(raw, current, history):
    status = raw.get('status')
    effective = status.get('customized') or status if isinstance(status, dict) else status
    return (completed_state(effective) is True or bool(current.get(COMPLETED_PROOF))
            or any(isinstance(proof, dict) and proof.get('target_status') == 'completed'
                   for row in history for proof in (row.get('native_status_transition') or [])))


def _base_quote(rate, owner, party, at, evidence):
    from operational_balance_sources import shipping_charge
    quote = shipping_charge(rate, owner, party, at, None, {'contract_evidence': evidence})
    parts = [part for part in quote.get('fee_components', []) if part.get('id') == 'base_shipping']
    if (quote.get('currency') != 'SAR' or len(parts) != 1 or parts[0].get('complete') is not True):
        raise ValueError('operational_shipping_completion_tariff_incomplete')
    amount = Decimal(str(parts[0]['amount']))
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal('.01')):
        raise ValueError('operational_shipping_completion_amount_invalid')
    return format(amount, '.2f')


def deferred_source_event(data, setup, source, raw, state, owner, start, end, history, shipment_conflicts,
                          *, review_only=False, review_index=None):
    """Return a fee-only event or None; historical expansion raises a candidate."""
    if state is None:
        return None
    if source.get('user_id') != owner or state.get('owner_id') != owner:
        raise ValueError('operational_shipping_completion_owner_mismatch')
    oid, number = str(raw.get('id') or ''), str(raw.get('reference_id') or raw.get('order_number') or '')
    facts = state.get('engine', {}).get('facts', {})
    if facts.get('completed_shipping:' + oid) or facts.get('prepaid_shipping:' + oid):
        return None
    if recorded_base_fee(state, oid, review_index=review_index):
        return None  # An already earned fee is neither replaced nor charged twice.
    current = source.get('salla_shipping_current') or raw.get('salla_shipping_current') or {}
    if not isinstance(current, dict) or not _has_completion(raw, current, history):
        return None
    if any(str(row.get('order_number')) == number and row.get('active') is not False
           and row.get('status') in {'assigned', 'out_for_delivery', 'delivered'}
           for row in data.get('store_delivery_assignments', [])):
        return None  # Store-driver earnings retain their native assignment rule.
    current, shipment, waybill = _current_shipping(raw, source, require_delivered=False, require_label=False)
    party, carrier_key = _courier(setup, owner, current)
    if (shipment and ('shipment', shipment) in shipment_conflicts
            or waybill and ('waybill', waybill) in shipment_conflicts):
        raise ValueError('operational_shipping_completion_shipment_shared_by_orders')
    at = completion_time(raw, source, history, end)
    bind_completed_waybill(raw, current, shipment, waybill, at)
    if at < start:
        return None
    rates = [rate for rate in setup.get('contracts', []) if rate.get('party_type') == 'courier'
             and rate.get('party_id') == party and rate.get('status') == 'approved'
             and rate.get('context') in {'delivery', 'order_completed'} and _instant(rate['effective_from']) <= at
             and (not rate.get('effective_to') or at < _instant(rate['effective_to']))]
    if len(rates) != 1:
        raise ValueError('operational_shipping_completion_tariff_ambiguous')
    rate = rates[0]
    if rate.get('payment_mode') == 'prepaid' or rate.get('context') == 'order_completed':
        return None
    if (not rate.get('confirmed_by') or not rate.get('confirmed_at') or rate.get('currency') != 'SAR'
            or rate.get('kind') == 'operational_courier_tariff' and rate.get('confirmed_by') != owner):
        raise ValueError('operational_shipping_completion_tariff_unapproved')
    if rate.get('kind') == 'operational_courier_tariff' and sum(
            row.get('id') == party and _instant(row['effective_from']) == _instant(rate['effective_from'])
            for row in state.get('courier_tariffs', [])) != 1:
        raise ValueError('operational_shipping_completion_tariff_ambiguous')
    evidence = deepcopy(setup.get('contract_evidence', [])) if rate.get('kind') == 'rich' else []
    amount = _base_quote(rate, owner, party, at, evidence)
    event = {'schema': SCHEMA, 'owner_id': owner, 'order_id': oid, 'order_number': number,
             'party_type': 'courier', 'party_id': party, 'party_name': rate.get('name') or current.get('company_name') or party,
             'currency': 'SAR', 'amount': amount, 'completed_at': at.isoformat(), 'payment_mode': 'deferred',
             'recognition_event': 'order_completed', 'component': 'base_shipping',
             'shipment_id': shipment, 'waybill': waybill, 'source_carrier_key': carrier_key,
             'contract_id': rate['id'], 'tariff_version_id': rate.get('version_id'),
             'contract_snapshot': deepcopy(rate), 'contract_evidence': evidence,
             'contract_hash': digest([rate, evidence]), 'completion_admission_required': True}
    if not review_only:
        require_admission(state, event, review_index=review_index)
    return event


def apply_deferred_events(state, events, start, now, issue):
    """Atomically add the known base obligation; no cash, bank or COD mutation."""
    from operational_balance_store import audit
    facts = state['engine'].setdefault('facts', {})
    obligations = state['engine'].setdefault('obligations', {})
    review_index = build_review_index(state) if events else None
    for event in events:
        oid = str(event.get('order_id') or '')
        fact_id, charge_id = 'completed_shipping:' + oid, 'shipping-completed:' + oid
        try:
            if (not oid or event.get('schema') != SCHEMA or event.get('owner_id') != state.get('owner_id')
                    or event.get('party_type') != 'courier' or event.get('currency') != 'SAR'
                    or event.get('payment_mode') != 'deferred' or event.get('component') != 'base_shipping'
                    or event.get('recognition_event') != 'order_completed'
                    or event.get('completion_admission_required') is not True):
                raise ValueError('operational_shipping_completion_event_invalid')
            if fact_id in facts:
                if facts[fact_id] != event:
                    raise ValueError('operational_shipping_completion_recorded_evidence_changed')
                continue
            require_admission(state, event, review_index=review_index)
            at = _instant(event['completed_at'])
            if not start <= at <= now:
                raise ValueError('operational_shipping_completion_time_invalid')
            rate, evidence = event['contract_snapshot'], event['contract_evidence']
            if (digest([rate, evidence]) != event.get('contract_hash') or rate.get('id') != event.get('contract_id')
                    or rate.get('party_id') != event['party_id'] or rate.get('party_type') != 'courier'
                    or rate.get('status') != 'approved' or rate.get('context') != 'delivery'
                    or rate.get('payment_mode') == 'prepaid' or not rate.get('confirmed_by')
                    or not rate.get('confirmed_at') or _instant(rate['effective_from']) > at
                    or rate.get('effective_to') and at >= _instant(rate['effective_to'])):
                raise ValueError('operational_shipping_completion_tariff_changed')
            if rate.get('kind') == 'operational_courier_tariff':
                versions = [row for row in state.get('courier_tariffs', []) if row.get('id') == event['party_id']]
                matching = [row for row in versions if row.get('version_id') == event.get('tariff_version_id')]
                if (len(matching) != 1 or rate.get('confirmed_by') != state['owner_id']
                        or sum(_instant(row['effective_from']) == _instant(rate['effective_from']) for row in versions) != 1
                        or any(matching[0].get(key) != rate.get(key) for key in matching[0] if key != 'id')
                        or any(_instant(rate['effective_from']) < _instant(row['effective_from']) <= at for row in versions)):
                    raise ValueError('operational_shipping_completion_tariff_changed')
            amount = _base_quote(rate, state['owner_id'], event['party_id'], at, evidence)
            if amount != event.get('amount'):
                raise ValueError('operational_shipping_completion_amount_invalid')
            check = review(state, event, review_index=review_index)
            if check['issues'] or fact_id in facts or charge_id in obligations or 'prepaid_shipping:' + oid in facts:
                raise ValueError('operational_shipping_completion_existing_charge_review_required')
            root = 'shipping:' + oid
            # Remove only mutable base estimates. Delivered COD commission
            # components retain their independently validated source and timing.
            mutable = [key for key in (root, root + ':base_shipping')
                       if obligations.get(key, {}).get('kind') == 'shipping']
            if any(Decimal(str(obligations[key].get('confirmed', 0))) != 0 for key in mutable):
                raise ValueError('operational_shipping_completion_existing_charge_review_required')
            charge = {'id': charge_id, 'kind': 'shipping', 'party_type': 'courier', 'party_id': event['party_id'],
                      'name': event['party_name'], 'currency': 'SAR', 'direction': 'payable',
                      'expected': '0.00', 'confirmed': amount, 'order_id': oid, 'order_number': event['order_number'],
                      'completed_at': event['completed_at'], 'payment_mode': 'deferred',
                      'recognition_event': 'order_completed', 'component': 'base_shipping',
                      'contract_id': event['contract_id'], 'tariff_version_id': event.get('tariff_version_id'),
                      'business_date': at.astimezone(ZoneInfo('Asia/Riyadh')).date().isoformat()}
        except (ValueError, KeyError, TypeError, ArithmeticError) as error:
            code = str(error) if str(error).startswith('operational_shipping_completion_') else 'operational_shipping_completion_event_invalid'
            issue(code, oid)
            continue
        for key in mutable:
            del obligations[key]
        facts[fact_id] = deepcopy(event)
        obligations[charge_id] = charge
        review_index.record(event, charge)
        state.setdefault('audit', [])
        audit(state, 'operational_order_worker', 'completed_shipping_earned', None, deepcopy(charge),
              'استحقاق أجرة الشحن عند تم التنفيذ وفق الدليل والتعرفة المعتمدين', 'mz2', event['completed_at'])
