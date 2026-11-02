"""Explicit, source-verified native invoice -> operational identity matching.

Pure preview/proposal only. The approval service owns permission, snapshot and
CAS. Neither this module nor its caller posts to the native ledger, inventory,
bank or provider. Existing movements/openings are never rewritten.
"""
from copy import deepcopy
from decimal import Decimal

from operational_balance_store import digest
from operational_balance_sources import instant
from operational_native_purchase_review import _money, _id, _require, NATIVE_INVOICE_EVIDENCE_FIELDS

LINKS = 'native_purchase_links'
VERSION = 1
INVOICE_FIELDS = NATIVE_INVOICE_EVIDENCE_FIELDS


def invoice_identity(invoice):
    return digest({k: invoice.get(k) for k in INVOICE_FIELDS})


def _fmt(value):
    return format(value, '.2f')


def _claim(row):
    return {k: deepcopy(v) for k, v in row.items() if k != 'fingerprint'}


def _seal(row):
    row['fingerprint'] = digest(_claim(row))
    return row


def _payment_key(row):
    return _id(row.get('payment_operation_id'))


def _movement_matches(movement, payment, *, supplier, targets):
    """Only a fully allocated exact bank leg can be reused without rewriting it."""
    _require(movement.get('party_type') == 'supplier' and movement.get('party_id') == supplier
        and movement.get('direction') == 'outgoing' and movement.get('currency') == 'SAR'
        and movement.get('kind') in {'payment', 'settlement'} and movement.get('bank_kind') == 'bank'
        and movement.get('bank_id') == payment['bank_account_id']
        and _money(movement.get('amount')) == _money(payment['amount_sar'])
        and movement.get('bank_currency') in (None, 'SAR')
        and _money(movement.get('bank_amount') or movement['amount']) == _money(payment['amount_sar'])
        and not _money(movement.get('bank_fee_amount') or '0')
        and not _money(movement.get('actual_fee_amount') or '0'),
        'native_link_existing_payment_mismatch')
    allocations = movement.get('allocations')
    _require(isinstance(allocations, list) and allocations
        and all(isinstance(a, dict) and a.get('obligation_id') in targets for a in allocations),
        'native_link_existing_payment_allocation_required')
    keys = [a['obligation_id'] for a in allocations]
    _require(len(keys) == len(set(keys)) and sum((_money(a.get('amount')) for a in allocations), Decimal(0))
        == _money(payment['amount_sar']), 'native_link_existing_payment_allocation_required')
    return movement


def _balances(state, supplier, banks):
    from operational_balance_service import report
    rows = report(state)['parties']
    def find(kind, key, field):
        matches = [r for r in rows if r['party_type'] == kind and r['party_id'] == key and r['currency'] == 'SAR']
        _require(len(matches) <= 1, 'native_link_balance_identity_ambiguous')
        return _money(matches[0][field]) if matches and field != 'actual' else (
            Decimal(matches[0][field]) if matches else Decimal(0))
    return {'supplier_outstanding_sar': _fmt(find('supplier', supplier, 'outstanding_payable')),
            'banks': {key: _fmt(find('bank', key, 'actual')) for key in sorted(banks)}}


def validate_saved_link(state, invoice, source):
    """Validate exact persisted identities against fresh sealed source evidence."""
    record = (state.get(LINKS) or {}).get(invoice.get('id'))
    result = dict(matched=False, posting_allowed=False, issues=[])
    if record is None:
        result['issues'] = ['native_link_not_recorded']
        return result
    try:
        _require(record.get('version') == VERSION and record.get('fingerprint') == digest(_claim(record))
            and record.get('owner_id') == state.get('owner_id') == invoice.get('user_id')
            and record.get('invoice_identity') == invoice_identity(invoice), 'native_link_saved_evidence_changed')
        _require(source.get('source_verified') is True, 'native_link_source_unverified')
        targets = record['obligation_ids']
        _require(isinstance(targets, list) and targets and len(set(targets)) == len(targets),
                 'native_link_target_missing')
        obligations = state.get('engine', {}).get('obligations') or {}
        gross = Decimal(0)
        for key in targets:
            row = obligations.get(key) or {}
            _require(row.get('id') == key and row.get('kind') == 'supplier'
                and row.get('party_type') == 'supplier' and row.get('party_id') == record['supplier_id']
                and row.get('currency') == 'SAR' and row.get('direction') == 'payable'
                and not row.get('superseded') and not _money(row.get('expected', '0')),
                'native_link_target_changed')
            gross += _money(row.get('confirmed'))
        _require(gross == _money(source.get('native_invoice_gross_sar')) == _money(record['gross_sar']),
                 'native_link_target_amount_changed')
        saved = record['payments']
        observed = {_payment_key(p): p for p in source['payment_journals']}
        _require(len(observed) == len(source['payment_journals']) and set(saved) == set(observed),
                 'native_link_new_or_changed_payments')
        moves = state.get('movements', [])
        used = set()
        for op, payment in observed.items():
            link = saved[op]
            _require(all(link.get(k) == payment.get(k) for k in (
                'txn_group_id', 'bank_account_id', 'amount_sar', 'effective_at', 'payment_operation_id',
                    'supplier_entry_id', 'bank_entry_id')),
                'native_link_payment_source_changed')
            matches = [m for m in moves if m.get('id') == link.get('movement_id')]
            _require(len(matches) == 1 and link['movement_id'] not in used,
                     'native_link_payment_identity_ambiguous')
            _movement_matches(matches[0], payment, supplier=record['supplier_id'], targets=targets)
            used.add(link['movement_id'])
        allocated = {m['id'] for m in moves if any(a.get('obligation_id') in targets for a in m.get('allocations', []))}
        _require(allocated == used, 'native_link_unmatched_operational_payment')
        _require(record.get('source_evidence_sha256') == source.get('source_evidence_sha256'),
                 'native_link_saved_source_changed')
        result.update(matched=True, obligation_ids=deepcopy(targets), payment_count=len(used),
            source_verified=True, opening_coverage_basis=record.get('opening_coverage_basis'),
            difference_sar='0.00', source_evidence_sha256=source['source_evidence_sha256'])
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        result['issues'] = [str(exc) if str(exc).startswith('native_') else 'native_link_saved_evidence_invalid']
    return result


def build_link_plan(state, invoice, source, selection, *, supplier, banks, as_of, collisions=()):
    """Calculate one invoice's exact delta, never infer identity by equal amounts.

    Existing targets/payment IDs are an explicit owner's mapping proposal,
    displayed before separate approval. Missing native payments are proposed
    as operational mirrors of the SAME already-executed bank leg, not bank API
    payments. Reusing unallocated/partly allocated old cash needs a separate
    correction; this operation will not edit old movement allocations.
    """
    try:
        _require(state.get('status') == 'active' and state.get('started_at')
                 and state.get('owner_id') == invoice.get('user_id'), 'native_link_owner_or_state_invalid')
        owner, iid = _id(state['owner_id']), _id(invoice.get('id'))
        _require(source.get('source_verified') is True and len(source.get('source_evidence_sha256', '')) == 64,
                 'native_link_source_unverified')
        sid = _id(invoice.get('supplier_entity_id'))
        _require(supplier.get('id') == sid and supplier.get('kind') == 'supplier'
            and supplier.get('currency') == 'SAR' and supplier.get('ready') is not False,
            'native_link_supplier_unavailable')
        gross, paid = _money(source.get('native_invoice_gross_sar')), _money(source.get('native_settled_sar'))
        _require(gross > 0 and 0 <= paid <= gross and gross - paid == _money(source.get('native_outstanding_sar')),
                 'native_link_source_amount_invalid')
        _require(instant(state['started_at']) <= instant(invoice.get('approved_at')) <= instant(as_of),
                 'native_link_prestart_invoice_requires_separate_opening_review')
        _require(selection.get('invoice_id') == iid, 'native_link_invoice_selection_mismatch')
        _id(selection.get('evidence_ref'))
        previous = (state.get(LINKS) or {}).get(iid)
        if previous:
            _require(previous.get('fingerprint') == digest(_claim(previous))
                and previous.get('invoice_identity') == invoice_identity(invoice), 'native_link_saved_evidence_changed')
        raw_targets = selection.get('existing_obligation_ids') or []
        _require(isinstance(raw_targets, list) and len(raw_targets) <= 1000,
                 'native_link_target_invalid')
        targets = sorted(_id(k) for k in raw_targets)
        _require(len(targets) == len(set(targets)), 'native_link_target_duplicated')
        key = 'native-purchase:' + digest([owner, iid, invoice['liability_id'], invoice['txn_group_id']])
        if previous:
            _require(targets == sorted(previous['obligation_ids']), 'native_link_retarget_forbidden')
        creates = not targets
        if creates:
            targets = [key]
        others = [v for k, v in (state.get(LINKS) or {}).items() if k != iid]
        _require(not any(v.get('invoice_id') == iid or v.get('liability_id') == invoice['liability_id']
            or v.get('purchase_txn_group_id') == invoice['txn_group_id']
            or set(v.get('obligation_ids', [])) & set(targets) for v in others), 'native_link_identity_already_bound')
        current_obligations = state.get('engine', {}).get('obligations') or {}
        if creates:
            _require(key not in current_obligations and not any(r.get('invoice_id') == iid for r in current_obligations.values())
                     and not collisions, 'native_link_existing_invoice_must_be_selected')
        else:
            total = Decimal(0)
            target_documents = set()
            for target in targets:
                row = current_obligations.get(target) or {}
                _require(row.get('id') == target and row.get('kind') == 'supplier'
                    and row.get('party_type') == 'supplier' and row.get('party_id') == sid
                    and row.get('direction') == 'payable' and row.get('currency') == 'SAR'
                    and not row.get('superseded') and not _money(row.get('expected', '0')),
                    'native_link_target_scope_invalid')
                total += _money(row.get('confirmed'))
                if row.get('invoice_id') or row.get('source_invoice_id'):
                    target_documents.add(row.get('invoice_id') or row['source_invoice_id'])
            _require(total == gross, 'native_link_target_amount_mismatch')
            _require(all(c.get('id') in target_documents for c in collisions), 'native_link_unresolved_document_collision')
        payments = source.get('payment_journals')
        _require(isinstance(payments, list) and len(payments) <= 1000, 'native_link_payment_scope_invalid')
        payment_map = {_payment_key(p): p for p in payments}
        _require(len(payment_map) == len(payments) and sum((_money(p['amount_sar']) for p in payments), Decimal(0)) == paid,
                 'native_link_payment_coverage_invalid')
        requested = selection.get('payment_links') or []
        _require(isinstance(requested, list), 'native_link_payment_selection_invalid')
        choices = {_id(p.get('payment_operation_id')): p.get('movement_id') for p in requested}
        _require(len(choices) == len(requested) and set(choices) == set(payment_map),
                 'native_link_payment_selection_incomplete')
        for op, old in (previous or {}).get('payments', {}).items():
            _require(op in payment_map and choices.get(op) == old['movement_id']
                and all(old.get(k) == payment_map[op].get(k) for k in (
                    'txn_group_id', 'bank_account_id', 'amount_sar', 'effective_at', 'payment_operation_id',
                    'supplier_entry_id', 'bank_entry_id')),
                'native_link_saved_payment_changed')
        bank_map = {b['id']: b for b in banks}
        _require(len(bank_map) == len(banks), 'native_link_bank_identity_ambiguous')
        referenced_banks = {p['bank_account_id'] for p in payments}
        for bid in referenced_banks:
            bank = bank_map.get(bid) or {}
            _require(bank.get('kind') == 'bank' and bank.get('currency') == 'SAR'
                and bank.get('ready') is not False and bank.get('settings_complete') is not False,
                'native_link_bank_unavailable')
        proposed = deepcopy(state)
        obligations = proposed.setdefault('engine', {}).setdefault('obligations', {})
        if creates:
            obligations[key] = dict(id=key, kind='supplier', party_type='supplier', party_id=sid,
                name=supplier.get('name', sid), currency='SAR', direction='payable', expected='0.00',
                confirmed=_fmt(gross), invoice_id=iid, invoice_number=invoice.get('invoice_number'),
                source_kind='native_purchase_link', native_liability_id=invoice['liability_id'],
                native_txn_group_id=invoice['txn_group_id'], business_date=instant(invoice['approved_at']).date().isoformat(),
                label='فاتورة شراء مثبتة — '+str(invoice.get('invoice_number') or iid))
        movements = proposed['movements']
        existing_moves = state.get('movements', [])
        selected_moves = set()
        linked_payments = {}
        missing = []
        for op, payment in sorted(payment_map.items()):
            choice = choices[op]
            _require(instant(payment['effective_at']) >= instant(state['started_at']),
                     'native_link_prestart_payment_requires_separate_review')
            matches = [m for m in existing_moves if choice and m.get('id') == choice]
            marked = [m for m in existing_moves if (m.get('native_payment_operation_id') == op
                or m.get('native_txn_group_id') == payment['txn_group_id']
                or m.get('reference') in {op, payment['txn_group_id']})]
            _require(not any(op in v.get('payments', {}) or any(
                p.get('txn_group_id') == payment['txn_group_id'] for p in v.get('payments', {}).values()) for v in others),
                'native_link_payment_already_bound')
            if choice:
                _require(len(matches) == 1 and choice not in selected_moves
                    and all(m.get('id') == choice for m in marked), 'native_link_existing_payment_ambiguous')
                _movement_matches(matches[0], payment, supplier=sid, targets=targets)
                selected_moves.add(choice)
                linked_payments[op] = {**deepcopy(payment), 'movement_id': choice, 'bank_already_recorded': True}
            else:
                _require(not marked, 'native_link_existing_payment_must_be_selected')
                missing.append((op, payment))
        allocated = {m['id'] for m in existing_moves if any(a.get('obligation_id') in targets for a in m.get('allocations', []))}
        _require(allocated == selected_moves, 'native_link_unmatched_operational_payment')
        remaining = {target: _money(obligations[target]['confirmed']) - sum((_money(a['amount'])
            for m in movements for a in m.get('allocations', []) if a['obligation_id'] == target), Decimal(0)) for target in targets}
        _require(all(v >= 0 for v in remaining.values()), 'native_link_target_overpaid')
        for op, payment in missing:
            amount = _money(payment['amount_sar'])
            rest, allocations = amount, []
            for target in targets:
                take = min(remaining[target], rest)
                if take:
                    allocations.append(dict(obligation_id=target, amount=_fmt(take)))
                    remaining[target] -= take
                    rest -= take
            _require(rest == 0, 'native_link_payment_exceeds_remaining')
            mid = 'native-payment:' + digest([owner, op, payment['txn_group_id']])
            _require(not any(m.get('id') == mid for m in movements), 'native_link_movement_identity_collision')
            movements.append(dict(id=mid, actor_id=owner, request_id=mid, party_type='supplier', party_id=sid,
                party_name=supplier.get('name', sid), bank_id=payment['bank_account_id'], bank_kind='bank',
                bank_name=bank_map[payment['bank_account_id']].get('name', payment['bank_account_id']),
                currency='SAR', amount=_fmt(amount), direction='outgoing', kind='settlement', allocations=allocations,
                reference=op, native_payment_operation_id=op, native_txn_group_id=payment['txn_group_id'],
                source='native_purchase_link', occurred_at=payment['effective_at'], created_at=as_of,
                business_date=instant(payment['effective_at']).date().isoformat(), note='مطابقة سداد منفذ سابقًا؛ لا تحويل بنكي جديد'))
            linked_payments[op] = {**deepcopy(payment), 'movement_id': mid, 'bank_already_recorded': False}
        record = _seal(dict(version=VERSION, owner_id=owner, invoice_id=iid,
            liability_id=invoice['liability_id'], purchase_txn_group_id=invoice['txn_group_id'],
            invoice_identity=invoice_identity(invoice), supplier_id=sid, invoice_number=invoice.get('invoice_number'), obligation_ids=targets,
            gross_sar=_fmt(gross), payments=linked_payments, source_evidence_sha256=source['source_evidence_sha256'],
            evidence_ref=selection['evidence_ref'], opening_coverage_basis='owner_confirmed_poststart_exclusion'))
        proposed.setdefault(LINKS, {})[iid] = record
        before = _balances(state, sid, referenced_banks)
        after = _balances(proposed, sid, referenced_banks)
        bank_delta = {k: _fmt(Decimal(after['banks'][k])-Decimal(before['banks'][k])) for k in before['banks']}
        public = dict(invoice_id=iid, supplier_id=sid, supplier_name=supplier.get('name', sid), currency='SAR',
            native_gross_sar=_fmt(gross), native_settled_sar=_fmt(paid), native_outstanding_sar=_fmt(gross-paid),
            creates_obligation=creates, obligation_ids=targets, before=before, after=after,
            supplier_difference_sar=_fmt(Decimal(after['supplier_outstanding_sar'])-Decimal(before['supplier_outstanding_sar'])),
            bank_differences_sar=bank_delta, new_payment_count=len(missing), payments=list(linked_payments.values()),
            state_revision=state['revision'], issues=[], can_approve=True, read_only=True, posting_allowed=False,
            same_invoice_confirmation_required=not creates,
            unrecorded_money_confirmation_required=creates or bool(missing))
        public['preview_hash'] = digest(dict(state=state, invoice=invoice, proof=source, selection=selection,
            supplier=supplier, banks=sorted(banks, key=lambda b:b['id']), collisions=list(collisions)))
        return public, proposed
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        return dict(invoice_id=invoice.get('id'), can_approve=False, read_only=True, posting_allowed=False,
            issues=[str(exc) if str(exc).startswith('native_') else 'native_link_evidence_invalid'],
            supplier_difference_sar=None, bank_differences_sar=None), None


def collision_with_saved_link(state, supplier_id, invoice_number, document_id):
    """An explicit same-vendor-document collision is not a hypothetical opening."""
    if not isinstance(invoice_number, str) or not invoice_number.strip():
        return False
    obligations = state.get('engine', {}).get('obligations') or {}
    for record in (state.get(LINKS) or {}).values():
        if record.get('supplier_id') != supplier_id or str(record.get('invoice_number') or '').strip() != invoice_number.strip():
            continue
        represented = {obligations.get(key, {}).get('source_invoice_id') or obligations.get(key, {}).get('invoice_id')
                       for key in record.get('obligation_ids', [])}
        if document_id not in represented:
            return True
    return False
