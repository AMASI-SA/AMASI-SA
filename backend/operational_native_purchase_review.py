"""Read-only native purchase proof through the sealed V2 ledger reader.

Verified native journal amounts are evidence, NOT operational balances or a
posting proposal. An opening is never matched by supplier name or amount.
"""
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

from accounting_ledger_v2 import AccountingLedgerV2Error, read_reporting_entries_v2
from operational_balance_sources import instant
from operational_balance_store import digest
from purchase_receiving_service import SCHEMA, purchase_amounts
from operational_financial_activity_runtime import checked_rows, preparation_checkpoint, prepare_in_thread

NATIVE_INVOICE_EVIDENCE_FIELDS = ('id', 'user_id', 'schema_version', 'state', 'supplier_entity_id',
    'supplier_counterparty_id', 'supplier_account_id', 'supplier_id', 'currency', 'approved_at',
    'liability_id', 'txn_group_id', 'subtotal', 'total', 'tax_amount', 'tax_treatment',
    'tax_evidence_ref', 'tax_evidence_verified', 'inventory_account_id',
    'input_vat_account_id', 'lines', 'invoice_number')

LIMIT = 20000
CENT = Decimal('0.01')
ACTION = 'راجع ربط الفاتورة والسداد المسجلين بالحساب التشغيلي؛ لا تُضف الدين أو السداد مرة أخرى'


def _money(value):
    try:
        n = Decimal(str(value))
        if isinstance(value, bool) or not n.is_finite() or n < 0 or n > Decimal('999999999999.99') or n != n.quantize(CENT):
            raise ValueError()
        return n
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('native_purchase_money_invalid') from None


def _id(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 300:
        raise ValueError('native_purchase_reference_missing')
    return value


def _require(condition, code):
    preparation_checkpoint()
    if not condition:
        raise ValueError(code)


def _base():
    return dict(source_verified=False, verification_scope='native_journal_only',
        native_invoice_gross_sar=None, native_settled_sar=None, native_outstanding_sar=None,
        payment_journals=[], issues=[], operational_balance_matched=False,
        opening_coverage_verified=False, difference_sar=None, posting_allowed=False,
        required_action=ACTION)


class _LedgerEvidence:
    """Index one already-verified owner snapshot once, not once per invoice."""
    def __init__(self, entries, owner):
        self.owner, self.groups = owner, defaultdict(list)
        self.invoices, self.liabilities = defaultdict(set), defaultdict(set)
        self.reversals, self.rows = set(), {}
        for row in checked_rows(entries):
            _require(isinstance(row, dict) and row.get('user_id') == owner
                     and row.get('currency') == 'SAR' and row.get('status') == 'posted',
                     'native_purchase_ledger_scope_mismatch')
            rid, gid = _id(row.get('id')), _id(row.get('txn_group_id'))
            _require(rid not in self.rows, 'native_purchase_duplicate_ledger_entry')
            meta = row.get('metadata') or {}
            _require(isinstance(meta, dict), 'native_purchase_ledger_metadata_invalid')
            self.rows[rid] = row
            self.groups[gid].append(row)
            for key, target in (('invoice_id', self.invoices), ('liability_id', self.liabilities)):
                value = meta.get(key)
                if value is not None:
                    target[_id(value)].add(gid)
            value = meta.get('reverses_entry_id')
            if value is not None:
                self.reversals.add(_id(value))


def review_native_purchase(invoice, liabilities, entries, *, owner, started_at, as_of):
    """Validate one source against already group/audit-verified ledger entries.

    Call only with the output of the sealed ledger reader from the same snapshot.
    This intentionally does not approve an operational correction or migration.
    """
    result = _base()
    try:
        _require(isinstance(invoice, dict) and invoice.get('user_id') == owner,
                 'native_purchase_owner_mismatch')
        iid, supplier, group, lid = map(_id, (invoice.get('id'), invoice.get('supplier_entity_id'),
                                            invoice.get('txn_group_id'), invoice.get('liability_id')))
        _require(invoice.get('schema_version') == SCHEMA and invoice.get('state') == 'approved',
                 'native_purchase_contract_unverified')
        _require(invoice.get('currency') == 'SAR', 'native_purchase_currency_unverified')
        _require(all(invoice.get(k) == supplier for k in ('supplier_counterparty_id', 'supplier_account_id'))
                 and invoice.get('supplier_id', supplier) == supplier,
                 'native_purchase_supplier_identity_conflict')
        at, start, upper = map(instant, (invoice.get('approved_at'), started_at, as_of))
        _require(at <= upper and start <= upper, 'native_purchase_after_review_time')
        result['approval_period'] = 'before_operational_start' if at < start else 'since_operational_start'
        result['order_creation_used_for_eligibility'] = False
        # The stored invoice must itself reconcile; a plausible header is not proof.
        lines = invoice.get('lines')
        _require(isinstance(lines, list) and 0 < len(lines) <= 100
                 and all(isinstance(line, dict) for line in lines), 'native_purchase_lines_incomplete')
        _require(all(not isinstance(line.get(k), bool) for line in lines for k in ('quantity', 'unit_cost')),
                 'native_purchase_lines_incomplete')
        _money(invoice.get('tax_amount'))
        for line in checked_rows(lines):
            _id(line.get('id'))
        totals = purchase_amounts(lines, invoice.get('tax_amount'), invoice.get('tax_treatment'))
        gross = _money(invoice.get('total'))
        _require(gross > 0 and gross == totals['total']
                 and _money(invoice.get('subtotal')) == totals['subtotal'], 'native_purchase_header_total_mismatch')
        _require(len(liabilities) == 1, 'native_purchase_liability_missing_or_duplicated')
        liability = liabilities[0]
        _require(liability.get('user_id') == owner and liability.get('id') == lid
                 and liability.get('schema_version') == SCHEMA and liability.get('kind') == 'supplier'
                 and liability.get('source') == 'purchase_invoice'
                 and liability.get('purchase_invoice_id') == iid
                 and liability.get('accounting_txn_group_id') == group
                 and liability.get('supplier_entity_id') == liability.get('counterparty_id') == supplier,
                 'native_purchase_liability_identity_mismatch')
        _require(_money(liability.get('expected_amount')) == gross, 'native_purchase_liability_gross_mismatch')
        declared_paid = _money(liability.get('paid_amount'))
        _require(declared_paid <= gross, 'native_purchase_paid_exceeds_invoice')
        indexed = entries if isinstance(entries, _LedgerEvidence) else _LedgerEvidence(entries, owner)
        _require(indexed.owner == owner, 'native_purchase_ledger_scope_mismatch')
        groups, reversals = indexed.groups, indexed.reversals
        linked = indexed.invoices.get(iid, set()) | indexed.liabilities.get(lid, set())
        result['source_evidence_sha256'] = digest({'invoice': {key: invoice.get(key) for key in NATIVE_INVOICE_EVIDENCE_FIELDS}, 'liabilities': liabilities,
            'entries': sorted([row for gid in linked | {group} for row in groups.get(gid, [])], key=lambda r: r['id']),
            'reversed_entries': sorted(reversals & {row['id'] for gid in linked | {group} for row in groups.get(gid, [])})})
        purchase = groups.get(group, [])
        _require(len(purchase) == (3 if totals['input_vat'] else 2), 'native_purchase_journal_incomplete')
        _require(all(row.get('source') == 'purchase_invoice' and instant(row.get('effective_at')) == at
                     for row in purchase), 'native_purchase_journal_provenance_mismatch')
        _require(not any(row['id'] in reversals for row in purchase), 'native_purchase_journal_reversed')
        expected = [dict(entity_type='asset', entity_id=_id(invoice.get('inventory_account_id')),
                        sub_account='inventory', entry_type='purchase_inventory', side='debit',
                        amount=totals['inventory_total']),
                    dict(entity_type='supplier', entity_id=supplier, sub_account='payable',
                        entry_type='purchase_payable', side='credit', amount=gross)]
        if totals['input_vat']:
            expected.append(dict(entity_type='tax', entity_id=_id(invoice.get('input_vat_account_id')),
                sub_account='input_vat', entry_type='purchase_input_vat', side='debit', amount=totals['input_vat']))
        for exp in checked_rows(expected):
            hits = [row for row in purchase if all(row.get(k) == exp[k] for k in
                    ('entity_type', 'entity_id', 'sub_account', 'entry_type', 'side'))]
            _require(len(hits) == 1 and _money(hits[0].get('amount')) == exp['amount'],
                     'native_purchase_journal_amount_or_account_mismatch')
            meta = hits[0].get('metadata') or {}
            if exp['entry_type'] != 'purchase_input_vat':
                _require(meta.get('invoice_id') == iid, 'native_purchase_journal_invoice_link_mismatch')
            else:
                _require(invoice.get('tax_evidence_verified') is True
                         and meta.get('evidence_ref') == _id(invoice.get('tax_evidence_ref')),
                         'native_purchase_tax_evidence_mismatch')
            _require(instant(meta.get('accounting_at')) == at, 'native_purchase_journal_date_mismatch')
        paid = Decimal(0)
        operations = set()
        payment_rows = []
        for gid in checked_rows(sorted(linked - {group})):
            payment = groups[gid]
            _require(len(payment) == 2 and all(row.get('source') == 'supplier_payment'
                     and row.get('entry_type') == 'supplier_payment' for row in payment),
                     'native_purchase_unsupported_linked_journal')
            _require(not any(row['id'] in reversals for row in payment), 'native_purchase_payment_reversed')
            supplier_legs = [row for row in payment if row.get('entity_type') == 'supplier'
                and row.get('entity_id') == supplier and row.get('sub_account') == 'payable' and row.get('side') == 'debit']
            bank_legs = [row for row in payment if row.get('entity_type') == 'bank'
                and row.get('sub_account') == 'main' and row.get('side') == 'credit']
            _require(len(supplier_legs) == len(bank_legs) == 1, 'native_purchase_payment_bank_pair_invalid')
            amount = _money(supplier_legs[0].get('amount'))
            _require(amount > 0 and _money(bank_legs[0].get('amount')) == amount,
                     'native_purchase_payment_amount_mismatch')
            meta = supplier_legs[0].get('metadata') or {}
            op = _id(meta.get('payment_operation_id'))
            _require(op not in operations, 'native_purchase_payment_reference_duplicate')
            operations.add(op)
            payment_at = instant(supplier_legs[0].get('effective_at'))
            _require(payment_at <= upper, 'native_purchase_payment_after_review_time')
            for row in checked_rows(payment):
                m = row.get('metadata') or {}
                _require(m.get('invoice_id') == iid and m.get('liability_id') == lid
                         and m.get('supplier_entity_id') == supplier and m.get('payment_operation_id') == op,
                         'native_purchase_payment_identity_mismatch')
                _require(instant(row.get('effective_at')) == payment_at
                         and instant(m.get('accounting_at')) == payment_at, 'native_purchase_payment_date_mismatch')
            paid += amount
            payment_rows.append(dict(txn_group_id=gid, payment_operation_id=op,
                                     supplier_entry_id=supplier_legs[0]['id'], bank_entry_id=bank_legs[0]['id'],
                                     bank_account_id=_id(bank_legs[0].get('entity_id')),
                                     amount_sar=format(amount, '.2f'), effective_at=payment_at.isoformat()))
        _require(paid == declared_paid and paid <= gross, 'native_purchase_paid_projection_mismatch')
        expected_status = 'paid' if paid == gross else 'partial' if paid else 'unpaid'
        _require(liability.get('status') == expected_status, 'native_purchase_liability_status_mismatch')
        result.update(source_verified=True, native_invoice_gross_sar=format(gross, '.2f'),
            native_settled_sar=format(paid, '.2f'), native_outstanding_sar=format(gross-paid, '.2f'),
            payment_journals=payment_rows,
            issues=['native_purchase_operational_link_and_opening_not_verified'])
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError, HTTPException) as exc:
        code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('native_purchase_') else 'native_purchase_source_evidence_invalid'
        result['issues'] = [code]
    return result


async def load_native_purchase_reviews(db, owner, invoices, *, session, started_at, as_of):
    """Bounded, snapshot-only evidence. No ledger raw access or writer calls."""
    _id(owner)
    if session is None or not getattr(session, 'in_transaction', False):
        raise ValueError('supplier_preview_snapshot_required')
    if not isinstance(invoices, list) or len(invoices) > LIMIT:
        raise ValueError('supplier_preview_native_scope_incomplete')
    if not invoices:
        return {}
    if any(not isinstance(row, dict) or row.get('user_id') != owner for row in invoices):
        raise ValueError('supplier_preview_native_owner_mismatch')
    identities = [row.get('id') for row in invoices if isinstance(row, dict)]
    refs = sorted({row['liability_id'] for row in invoices if isinstance(row, dict)
                   and isinstance(row.get('liability_id'), str) and row['liability_id']})
    liabilities = []
    for offset in range(0, len(refs), 250):
        chunk = await db['liabilities'].find({'user_id': owner, 'id': {'$in': refs[offset:offset+250]}},
            {'_id': 0}, session=session).to_list(length=LIMIT+1)
        liabilities.extend(chunk)
        if len(liabilities) > LIMIT:
            raise ValueError('supplier_preview_native_scope_incomplete')
    try:
        # The public API verifies WHOLE groups/audits, including zero-leg damage.
        # This manual preview reads the ledger once, not once per bundle leaf.
        upper = (instant(as_of) + timedelta(microseconds=1)).isoformat()
        entries = await read_reporting_entries_v2(db, user_id=owner,
            effective_before=upper, mongo_session=session)
    except AccountingLedgerV2Error as error:
        return {iid: {**_base(), 'issues': ['native_purchase_ledger_verification_failed', error.code]}
                for iid in identities if isinstance(iid, str)}
    return await prepare_in_thread(_review_native_batch, invoices, liabilities, entries,
        owner=owner, started_at=started_at, as_of=as_of)


def _review_native_batch(invoices, liabilities, entries, *, owner, started_at, as_of):
    preparation_checkpoint()
    identities = [row.get('id') for row in checked_rows(invoices) if isinstance(row, dict)]
    by_liability = defaultdict(list)
    for row in checked_rows(liabilities):
        by_liability[row.get('id')].append(row)
    try:
        indexed = _LedgerEvidence(entries, owner)
    except ValueError as error:
        return {iid: {**_base(), 'issues': [str(error)]} for iid in identities if isinstance(iid, str)}
    result, counts = {}, Counter(identities)
    for invoice in checked_rows(invoices):
        iid = invoice.get('id') if isinstance(invoice, dict) else None
        if not isinstance(iid, str):
            continue
        if counts[iid] != 1:
            result[iid] = {**_base(), 'issues': ['native_purchase_identity_ambiguous']}
            continue
        result[iid] = review_native_purchase(invoice, by_liability[invoice.get('liability_id')], indexed,
            owner=owner, started_at=started_at, as_of=as_of)
    return result
