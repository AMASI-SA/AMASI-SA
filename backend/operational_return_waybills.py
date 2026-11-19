"""Project Salla's reverse freight into operational courier balances."""
from collections import defaultdict
from decimal import InvalidOperation
from zoneinfo import ZoneInfo

from operational_balance_sources import instant, issue, number, shipping_charge, usable
from operational_balance_store import digest
from operational_delivery_register import history_version, order_status
from shipping_companies import matches_courier_alias, normalize_shipping_company

PROOF = 'salla_operational_return_waybills'
MISSING = 'operational_return_waybill_native_evidence_required'
RETURNED = {'restored', 'returned', 'مسترجع'}
TERMINAL = {'returned', 'delivered', 'مسترجع', 'تم التوصيل'}
CANCELLED = {'cancelled', 'canceled', 'ملغي'}
PENDING = {'created', 'pending', 'in_progress', 'in_transit', 'shipped', 'delivering',
           'received_at_final_hub', 'return_in_progress', 'return_to_origin',
           'to_be_reattempted', 'reattempted', 'unable_to_deliver'}
RIYADH = ZoneInfo('Asia/Riyadh')
FIELDS = ('id', 'order_id', 'order_reference_id', 'type', 'status', 'courier_name', 'courier_code',
          'tracking_number', 'shipping_number', 'created_at', 'actual_delivered_at', 'returned_at')


def text(value):
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ''


def reverse_rows(shipments):
    return [row for row in shipments if isinstance(row, dict) and row.get('type') == 'return']


def native_proof(shipments, histories, oid, order_number):
    """Persist only native reverse legs. A bare order refund is never freight evidence."""
    legs = reverse_rows(shipments)
    saved = []
    for row in legs:
        if text(row.get('order_id')) != oid or text(row.get('order_reference_id')) not in {'', order_number}:
            raise ValueError('return_waybill_order_mismatch')
        leg = {key: row[key] for key in FIELDS if row.get(key) is not None}
        # Salla sometimes omits the completion clock even on a terminal leg.
        # Require the courier's own status transition on this single return.
        if len(legs) == 1 and leg.get('status') in TERMINAL and not (leg.get('returned_at') or leg.get('actual_delivered_at')):
            dates = set()
            for event in histories:
                if (event.get('type') in {'activity', 'status_changed', 'status_updated'}
                        and text(event.get('order_id')) in {'', oid}
                        and text(event.get('note')) in RETURNED
                        and order_status(event)[0] in RETURNED):
                    actor = event.get('created_by') or {}
                    carrier = text(leg.get('courier_name'))
                    author = text(actor.get('name')) if isinstance(actor, dict) else ''
                    key = normalize_shipping_company(carrier)[0]
                    if carrier and (author == carrier or key != 'unknown' and not key.startswith('other:')
                                    and key == normalize_shipping_company(author)[0]):
                        dates.add(instant(event.get('created_at')).isoformat())
            if len(dates) == 1:
                leg['returned_at'] = dates.pop()
        saved.append(leg)
    return saved


def collect(out, source, raw, setup, owner, start, now):
    oid = str(raw['id'])
    order_number = text(raw.get('reference_id') or raw.get('order_number'))
    if any(text(source.get(k)) not in {'', oid} for k in ('order_id', 'salla_order_id')) or text(source.get('order_number')) not in {'', order_number}:
        return
    proof = source.get(PROOF) or {}
    verified = proof.get('version') == history_version(raw) and proof.get('order_id') == oid
    shipments = proof.get('legs', []) if verified else raw.get('shipments')
    legs = reverse_rows(shipments) if isinstance(shipments, list) else []
    if not legs and not verified and order_status(raw)[0] in RETURNED:
        # Older orders can have a post-cutover return. Use the saved revision
        # to avoid acquiring orders whose last change predates the ledger.
        try:
            date = raw.get('date') if isinstance(raw.get('date'), dict) else {}
            changed = instant(raw.get('updated_at') or raw.get('date_updated') or date.get('updated'))
            if changed <= start:
                return
        except (TypeError, ValueError):
            pass
        issue(out, MISSING, oid, order_number=order_number)
    for leg in legs:
        try:
            ref = text(leg.get('tracking_number') or leg.get('shipping_number'))
            sid = text(leg.get('id'))
            if (not ref or not sid or text(leg.get('order_id')) not in {'', oid}
                    or text(leg.get('order_reference_id')) not in {'', order_number}):
                raise ValueError('operational_return_waybill_identity_incomplete')
            issued = instant(leg.get('created_at'))
            status = text(leg.get('status')).lower()
            if status not in TERMINAL | CANCELLED | PENDING:
                raise ValueError('operational_return_waybill_status_unknown')
            finished = leg.get('returned_at') or leg.get('actual_delivered_at')
            completed = instant(finished) if status in TERMINAL and finished else None
            if completed and (completed < issued or completed > now) or issued > now:
                raise ValueError('operational_return_waybill_date_invalid')
            if (completed or issued) <= start and not (status in TERMINAL and not completed):
                continue
            if status in PENDING or status in TERMINAL and not completed:
                # Source revisions can remain unchanged during carrier transit.
                # The native reader throttles retries independently per order.
                issue(out, MISSING, oid, order_number=order_number)
            if status in TERMINAL and not completed:
                if issued <= start:
                    continue
            keys = [text(leg.get(field)) for field in ('courier_code', 'courier_name') if text(leg.get(field))]
            parties = [p for p in setup.get('couriers', []) if usable(p) and p.get('confirmed_by') and p.get('confirmed_at')
                       and any(matches_courier_alias(p, k) for k in keys)]
            if len(parties) != 1:
                raise ValueError('operational_return_waybill_carrier_unconfirmed')
            party = parties[0]
            item = dict(order_id=oid, order_number=order_number, shipment_id=sid, reference=ref,
                        party_id=party['courier_key'], name=party.get('name') or keys[-1], issued_at=issued.isoformat(),
                        completed_at=completed.isoformat() if completed else None, cancelled=status in CANCELLED)
            if status not in CANCELLED:
                rates = [r for r in setup.get('contracts', []) if r.get('status') == 'approved'
                         and r.get('party_type') == 'courier' and r.get('party_id') == party['courier_key']
                         and r.get('context') == 'delivery' and instant(r['effective_from']) <= issued
                         and (not r.get('effective_to') or issued < instant(r['effective_to']))]
                if len(rates) == 1 and rates[0].get('currency', 'SAR') == 'SAR':
                    rate = rates[0]
                    quote = shipping_charge({**rate, 'currency': 'SAR'}, owner, party['courier_key'], issued, None, setup)
                    base = next((part for part in quote['fee_components'] if part['id'] == 'base_shipping' and part['complete']), None)
                    if base:
                        item.update(amount=format(number(base['amount']), '.2f'), contract_id=rate['id'])
            out.setdefault('return_waybills', []).append(item)
        except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
            code = str(exc) if str(exc).startswith('operational_return_waybill_') else 'operational_return_waybill_evidence_incomplete'
            issue(out, code, oid, order_number=order_number)


def project(obligations, events, stamp, report):
    by_reference = defaultdict(list)
    by_shipment = {}
    for key, row in obligations.items():
        if (row.get('return_case_id') or row.get('salla_return_shipment_id')) and row.get('shipment_reference'):
            by_reference[row['party_id'], row['shipment_reference'].strip().casefold()].append(key)
            if row.get('salla_return_shipment_id'):
                by_shipment[row['salla_return_shipment_id']] = key
    by_ref, by_id = defaultdict(set), defaultdict(set)
    for event in events:
        ref = event['party_id'], event['reference'].casefold()
        by_ref[ref].add((event['order_id'], event['shipment_id'], event['cancelled'], event['completed_at'], event['issued_at']))
        by_id[event['shipment_id']].add((event['order_id'], ref))
    for event in events:
        oid, sid = event['order_id'], event['shipment_id']
        ref = event['party_id'], event['reference'].casefold()
        keys = by_reference[ref]
        if len(by_ref[ref]) != 1 or len(by_id[sid]) != 1 or len(keys) > 1 or sid in by_shipment and by_shipment[sid] not in keys:
            report('operational_return_waybill_identity_conflict', oid)
            continue
        key = keys[0] if keys else 'salla-return-shipping:' + digest(list(ref))
        old = obligations.get(key, {})
        if (old.get('order_id', oid) != oid or old.get('salla_return_shipment_id', sid) != sid):
            report('operational_return_waybill_identity_conflict', oid)
            continue
        if old and number(old['confirmed']) > 0:
            if event['cancelled']:
                report('operational_return_waybill_confirmed_cancellation', oid)
            # Preserve the original posted amount, settlement key and audit.
            if not old.get('salla_return_shipment_id'):
                old['salla_return_shipment_id'] = sid
            continue
        amount = old.get('return_fee_amount') or (old.get('expected') if old.get('return_case_id') else None) or event.get('amount')
        if not amount and not event['cancelled']:
            report('operational_return_waybill_tariff_required', oid)
            continue
        if old.get('return_cancelled') and not event['cancelled']:
            if event['completed_at']:
                report('operational_return_waybill_terminal_conflict', oid)
            continue
        completed = event['completed_at']
        business_at = completed or event['issued_at']
        obligations[key] = {**old, 'id': key, 'kind': 'shipping', 'party_type': 'courier', 'party_id': ref[0],
            'name': event['name'], 'currency': 'SAR', 'direction': 'payable', 'order_id': oid,
            'order_number': event['order_number'], 'reference': event['reference'], 'shipment_reference': event['reference'],
            'salla_return_shipment_id': sid, 'return_fee_amount': amount, 'return_cancelled': event['cancelled'],
            'contract_id': old.get('contract_id') or event.get('contract_id'),
            'expected': '0.00' if completed or event['cancelled'] else amount,
            'confirmed': amount if completed and not event['cancelled'] else '0.00',
            'return_business_at': business_at, 'business_date': instant(business_at).astimezone(RIYADH).date().isoformat(),
            'recorded_at': stamp if completed else old.get('recorded_at', stamp)}
        if not keys:
            keys.append(key)
        by_shipment[sid] = key
