"""Pure, read-only supplier invoice reconciliation. Never applies the proposal.

Inputs must be owner-scoped saved/proposed snapshots and original native invoice
headers. A clean arithmetic comparison is NOT opening-coverage approval, a source
refresh, a bank transfer or a financial correction. No database or network I/O.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

CENT = Decimal('0.01')
LIMIT = Decimal('999999999999.99')
FIELDS = ('gross', 'confirmed', 'settled', 'outstanding', 'overpayment')


def _money(value, *, halalas=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError('supplier_review_amount_invalid')
    try:
        result = Decimal(value)
        unit = Decimal(1) if halalas else CENT
        if not result.is_finite() or result < 0 or result != result.quantize(unit):
            raise ValueError()
        result = result / 100 if halalas else result
        if result > LIMIT:
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise ValueError('supplier_review_amount_invalid') from None


def _time(value):
    try:
        # Motor returns BSON UTC dates without tzinfo by default; unzoned strings remain ambiguous.
        if isinstance(value, datetime):
            result = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        else:
            result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if result.utcoffset() is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('supplier_review_timestamp_invalid') from None


def _id(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('supplier_review_identity_invalid')
    return value


def _amounts(values):
    return {key: format(value, '.2f') for key, value in values.items()}


def _components(invoice):
    """Validate exact invoice component identities, not just an aggregate sum."""
    result, pieces = set(), set()
    lines = invoice.get('lines')
    if not isinstance(lines, list) or not lines:
        raise ValueError('supplier_review_invoice_lines_missing')
    for line in lines:
        if not isinstance(line, dict) or line.get('product_price_authority') != 'mezan_v2':
            raise ValueError('supplier_review_invoice_line_invalid')
        ids = line.get('piece_ids')
        if not isinstance(ids, list) or not ids:
            raise ValueError('supplier_review_invoice_line_invalid')
        if 'quantity' in line and _money(line['quantity']) != len(ids):
            raise ValueError('supplier_review_invoice_quantity_conflict')
        for piece in ids:
            _id(piece)
            if piece in pieces:
                raise ValueError('supplier_review_duplicate_component')
            pieces.add(piece)
            prefix = 'receipt:' + invoice['id'] + ':' + piece + ':'
            if line.get('product_charge_eligible') is not False:
                result.add(prefix + 'product')
            services = line.get('services', [])
            if not isinstance(services, list):
                raise ValueError('supplier_review_invoice_line_invalid')
            seen = set()
            for service in services:
                sid = _id(service.get('service_id')) if isinstance(service, dict) else _id(None)
                if sid in seen:
                    raise ValueError('supplier_review_duplicate_component')
                seen.add(sid)
                result.add(prefix + 'service:' + sid)
    return result


def _allocated(state):
    allocated, ids = {}, set()
    for movement in state.get('movements', []):
        identity = _id(movement.get('id'))
        if identity in ids:
            raise ValueError('supplier_review_duplicate_movement')
        ids.add(identity)
        seen = set()
        for row in movement.get('allocations', []):
            key = _id(row.get('obligation_id'))
            if key in seen:
                raise ValueError('supplier_review_duplicate_allocation')
            seen.add(key)
            allocated[key] = allocated.get(key, Decimal(0)) + _money(row.get('amount'))
    return allocated


def _invoice_rows(state, lookup, allocated):
    grouped, diagnostics = {}, []
    facts = state.get('engine', {}).get('facts', {})
    for key, row in state.get('engine', {}).get('obligations', {}).items():
        if (row.get('kind') != 'supplier' or row.get('party_type') != 'supplier'
                or row.get('derived_credit') or row.get('direction') != 'payable'
                or not row.get('evidence_ids')):
            continue
        try:
            evidence = row['evidence_ids']
            if not isinstance(evidence, list) or len(evidence) != 1:
                raise ValueError('supplier_review_receipt_link_invalid')
            rid = _id(evidence[0])
            fact = facts.get(rid)
            if not rid.startswith('receipt:') or not isinstance(fact, dict):
                raise ValueError('supplier_review_receipt_fact_missing')
            proposed = lookup.get(key, {})
            invoice_id = row.get('source_invoice_id')
            if not invoice_id:
                # Legacy obligations may lack display metadata. Match the exact
                # existing obligation and receipt, never a guessed string prefix.
                if (proposed.get('evidence_ids') != evidence
                        or proposed.get('party_id') != row.get('party_id')
                        or proposed.get('currency') != row.get('currency')):
                    raise ValueError('supplier_review_invoice_link_missing')
                invoice_id = proposed.get('source_invoice_id')
            invoice_id = _id(invoice_id)
            if fact.get('supplier_id') != row.get('party_id'):
                raise ValueError('supplier_review_receipt_party_conflict')
            gross = _money(fact.get('gross_amount', fact.get('amount')))
            if 'gross_amount' in row and _money(row['gross_amount']) != gross:
                raise ValueError('supplier_review_receipt_amount_conflict')
            if _money(fact.get('amount')) != gross:
                raise ValueError('supplier_review_receipt_amount_conflict')
            if ('net_amount' in fact or 'tax_amount' in fact) and (
                    _money(fact.get('net_amount')) + _money(fact.get('tax_amount')) != gross):
                raise ValueError('supplier_review_receipt_amount_conflict')
            returned = sum((_money(value.get('amount')) for name, value in facts.items()
                if name.startswith('return:') and value.get('receipt_id') == rid), Decimal(0))
            confirmed = _money(row.get('confirmed'))
            if confirmed != gross - returned or confirmed > gross:
                raise ValueError('supplier_review_receipt_amount_conflict')
            group = grouped.setdefault(invoice_id, {
                'party_id': row['party_id'], 'currency': row.get('currency'),
                'components': set(), 'approved_times': set(),
                'totals': {name: Decimal(0) for name in FIELDS}})
            if group['party_id'] != row['party_id'] or group['currency'] != row.get('currency'):
                raise ValueError('supplier_review_invoice_scope_conflict')
            if rid in group['components']:
                raise ValueError('supplier_review_duplicate_component')
            group['components'].add(rid)
            group['approved_times'].add(_time(fact.get('accepted_at')))
            settled = allocated.get(key, Decimal(0))
            for name, value in {'gross': gross, 'confirmed': confirmed, 'settled': settled,
                                'outstanding': max(confirmed-settled, Decimal(0)),
                                'overpayment': max(settled-confirmed, Decimal(0))}.items():
                group['totals'][name] += value
        except (ValueError, KeyError, TypeError) as error:
            diagnostics.append({'code': str(error), 'obligation_id': str(key)})
    return grouped, diagnostics


def compare_supplier_invoices(current, proposed, invoices, *, owner_id, as_of):
    """Compare immutable snapshots; any financial approval is a separate action.

    No total is represented as complete when invoice/component coverage is
    incomplete. Shipping/provider issues do not by themselves hide known invoice
    amounts. The caller must separately establish the live source snapshot and
    whether each proposed amount is already covered by opening balances.
    """
    owner_id = _id(owner_id)
    if current.get('owner_id') != owner_id or proposed.get('owner_id') != owner_id:
        raise ValueError('supplier_review_owner_mismatch')
    if current.get('status') != 'active' or proposed.get('status') != 'active':
        raise ValueError('supplier_review_not_active')
    start, now = _time(current.get('started_at')), _time(as_of)
    if _time(proposed.get('started_at')) != start or now < start:
        raise ValueError('supplier_review_cutoff_changed')
    for state in (current, proposed):
        stamp = state.get('engine', {}).get('as_of')
        if stamp is not None and _time(stamp) > now:
            raise ValueError('supplier_review_stale_snapshot')
    for name in ('openings', 'movements'):
        if current.get(name, {} if name == 'openings' else []) != proposed.get(name, {} if name == 'openings' else []):
            raise ValueError('supplier_review_' + name + '_changed')
    previous_facts = current.get('engine', {}).get('facts', {})
    new_facts = proposed.get('engine', {}).get('facts', {})
    if any(new_facts.get(key) != value for key, value in previous_facts.items()):
        raise ValueError('supplier_review_confirmed_facts_changed')
    if not isinstance(invoices, list) or len(invoices) > 20000:
        raise ValueError('supplier_review_invoice_list_invalid')
    allocated = _allocated(current)
    lookup = proposed.get('engine', {}).get('obligations', {})
    before, before_issues = _invoice_rows(current, lookup, allocated)
    after, after_issues = _invoice_rows(proposed, lookup, allocated)
    issues = before_issues + after_issues
    old_obligations = current.get('engine', {}).get('obligations', {})
    money_fields = ('kind', 'party_id', 'party_type', 'currency', 'direction', 'expected', 'confirmed')
    for key in set(old_obligations) | set(lookup):
        old_row, new_row = old_obligations.get(key, {}), lookup.get(key, {})
        if old_row.get('kind') == new_row.get('kind') == 'supplier':
            continue
        if not old_row and new_row.get('kind') == 'supplier':
            continue
        if {k: old_row.get(k) for k in money_fields} != {k: new_row.get(k) for k in money_fields}:
            issues.append({'code': 'supplier_review_unrelated_financial_change', 'obligation_id': key})
    rows, excluded, seen, eligible = [], [], set(), set()
    zero = {'components': set(), 'totals': {name: Decimal(0) for name in FIELDS}}
    for invoice in invoices:
        identity = None
        try:
            if not isinstance(invoice, dict) or invoice.get('user_id') != owner_id:
                raise ValueError('supplier_review_invoice_owner_mismatch')
            identity = _id(invoice.get('id'))
            if identity in seen:
                raise ValueError('supplier_review_duplicate_invoice')
            seen.add(identity)
            if invoice.get('experiment_mode') or invoice.get('experiment_run_id'):
                excluded.append({'invoice_id': identity, 'reason': 'experiment'}); continue
            if not invoice.get('approved_at') or invoice.get('status') not in {None, 'payable_posted'}:
                excluded.append({'invoice_id': identity, 'reason': 'not_approved'}); continue
            approved = _time(invoice['approved_at'])
            if not start <= approved <= now:
                excluded.append({'invoice_id': identity, 'reason': 'outside_cutoff_window'}); continue
            eligible.add(identity)
            currency = invoice.get('currency', 'SAR')
            if currency != 'SAR':
                raise ValueError('supplier_review_invoice_currency_unverified')
            supplier = _id(invoice.get('supplier_id'))
            expected = _components(invoice)
            total = _money(invoice.get('total_halalas'), halalas=True)
            old, new = before.get(identity, zero), after.get(identity, zero)
            for group in (old, new):
                if group is not zero and (group['party_id'] != supplier or group['currency'] != currency):
                    raise ValueError('supplier_review_invoice_scope_conflict')
            problems = []
            if new is not zero and new['approved_times'] != {approved}:
                problems.append('supplier_review_approval_date_conflict')
            if new['components'] != expected or new['totals']['gross'] != total:
                problems.append('supplier_review_proposed_invoice_incomplete')
            if not old['components'].issubset(expected):
                problems.append('supplier_review_existing_component_conflict')
            delta = new['totals']['confirmed'] - old['totals']['confirmed']
            reason = ('already_matched' if delta == 0 else
                      'missing_invoice' if not old['components'] else 'partially_counted_invoice')
            if delta < 0:
                reason = 'decrease_requires_explicit_review'
            rows.append({'invoice_id': identity, 'invoice_number': invoice.get('invoice_number'),
                'supplier_id': supplier, 'currency': currency, 'approved_at': approved.isoformat(),
                'invoice_gross': format(total, '.2f'), 'current': _amounts(old['totals']),
                'proposed': _amounts(new['totals']) if not problems else None,
                'difference': format(delta, '.2f') if not problems else None,
                'current_components': len(old['components']), 'required_components': len(expected),
                'reason': reason, 'issues': problems,
                'opening_coverage': 'requires_owner_review' if delta else 'not_changed'})
            issues.extend({'code': code, 'invoice_id': identity} for code in problems)
        except (ValueError, KeyError, TypeError) as error:
            issues.append({'code': str(error), 'invoice_id': identity})
    for identity in sorted(set(after) - eligible):
        if after.get(identity) != before.get(identity):
            issues.append({'code': 'supplier_review_change_without_eligible_invoice', 'invoice_id': identity})
    for identity in sorted(set(before) - set(after)):
        issues.append({'code': 'supplier_review_existing_invoice_disappeared', 'invoice_id': identity})
    complete = not issues
    totals = None
    if complete:
        totals = {name: format(sum((Decimal(row[name]) for row in rows), Decimal(0)), '.2f')
                  for name in ('invoice_gross', 'difference')}
    return {'schema': 'supplier_invoice_readonly_comparison_v1', 'owner_id': owner_id,
            'started_at': start.isoformat(), 'as_of': now.isoformat(), 'posting_allowed': False,
            'financial_correction_approved': False, 'opening_coverage_verified': False,
            'arithmetic_complete': complete, 'rows': sorted(rows, key=lambda row: row['invoice_id']),
            'excluded': excluded, 'issues': issues, 'totals': totals,
            'openings_unchanged': True, 'movements_unchanged': True}
