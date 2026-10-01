"""Native receiving invoice boundary. No legacy identity or ledger readers.

Caller owns the atomic_owner transaction containing receiving and invoice writes.
Reversal is deliberately unavailable until payment reconciliation is integrated.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from accounting_atomic import SessionDatabase
from accounting_ledger_v2 import post_journal_v2, verify_active_opening_v2, verify_journal_v2, get_journal_v2, query_entries_v2
from accounting_module_contract import accounting_owner_id, require_accounting_permission, require_owner
from accounting_module_readiness import build_accounting_module_status
from accounting_periods import assert_open_journal_periods
from accounting_write_control import fresh_actor
from supplier_invoice_integrity import verify_invoice_totals
from supplier_debit_identity_v2 import resolve_debit, require_opening_identity, require_source

CONTRACT = 'mz2_supplier_invoice_v1'
SUPPLIERS = 'mezan_suppliers_v2'
INVOICES = 'mezan_supplier_invoices_v2'


def fail(code, **details):
    raise HTTPException(409, detail={'code': code, **details})


class PurchaseTax(BaseModel):
    model_config = ConfigDict(extra='forbid')
    treatment: Literal['INPUT_VAT']
    amount_halalas: int = Field(gt=0, strict=True)
    entity_id: str = Field(min_length=1, max_length=200)
    evidence_file_id: str = Field(min_length=1, max_length=200)
    evidence_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed: Literal[True]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), default=str).encode()).hexdigest()


def close_payload_hash(payload):
    values = payload.model_dump(mode='json')
    values.pop('note', None)
    return digest(values)


def invoice_digest(invoice):
    return digest({k: invoice.get(k) for k in (
        'id', 'session_id', 'supplier_id', 'currency', 'lines', 'subtotal_halalas',
        'total_halalas', 'piece_count', 'line_count', 'purchase_tax', 'mz2_effective_at',
        'mz2_close_payload_hash', 'mz2_debit_mapping_snapshots')})


def invoice_total(invoice):
    tax = invoice.get('purchase_tax')
    # Reject tax signals outside the explicit purchase tax contract.
    if any(invoice.get(k) not in (None, 0, False, '') for k in ('tax', 'vat', 'tax_halalas', 'vat_halalas', 'tax_amount', 'vat_amount')):
        fail('MZ2_SUPPLIER_TAX_IDENTITY_REQUIRED')
    tax_amount = 0
    if tax:
        try:
            tax_amount = PurchaseTax.model_validate(tax).amount_halalas
        except ValueError:
            fail('MZ2_SUPPLIER_TAX_IDENTITY_REQUIRED')
    subtotal = invoice.get('subtotal_halalas')
    verify_invoice_totals({**invoice, 'total_halalas': subtotal})
    if not isinstance(invoice.get('total_halalas'), int) or isinstance(invoice['total_halalas'], bool) or invoice['total_halalas'] != subtotal + tax_amount:
        fail('supplier_native_invoice_total_mismatch')
    return invoice['total_halalas']


def effective_time(invoice):
    value = invoice.get('accounting_date')
    if value:
        day = date.fromisoformat(str(value))
        return datetime.combine(day, time.min, ZoneInfo('Asia/Riyadh')).astimezone(timezone.utc).isoformat()
    value = invoice.get('approved_at')
    if not isinstance(value, datetime) or value.tzinfo is None:
        fail('supplier_native_effective_date_required')
    return value.astimezone(timezone.utc).isoformat()


def scoped_database(db, owner, session):
    if session is None:
        fail('supplier_native_owner_transaction_required')
    scoped = SessionDatabase(db, session)
    scoped._owner = owner
    return scoped


async def post_native_invoice(db, *, user_id, actor, invoice, mongo_session):
    scoped = scoped_database(db, user_id, mongo_session)
    current = await fresh_actor(scoped, actor)
    require_accounting_permission(current, 'accounting.purchases.post')
    if accounting_owner_id(current) != user_id or invoice.get('user_id') != user_id:
        fail('supplier_native_owner_mismatch')
    state = await scoped.mz2_atomic_owners.find_one({'_id': user_id})
    if (state or {}).get('writes_paused', True) is not False:
        raise HTTPException(423, detail={'code': 'mz2_writes_paused'})
    if invoice.get('experiment_mode') is not False:
        fail('supplier_native_real_invoice_required')
    supplier = await scoped[SUPPLIERS].find_one({'user_id': user_id, 'id': invoice.get('supplier_id')})
    if (not supplier or supplier.get('status') in {'inactive', 'deleted', 'archived', 'disabled'}
            or any(supplier.get(k) for k in ('deleted_at', 'archived_at', 'deleted', 'archived', 'is_deleted', 'is_archived', 'disabled'))
            or any(supplier.get(k) is False for k in ('active', 'is_active'))):
        fail('supplier_v2_identity_required')
    total = invoice_total(invoice)
    settings = await scoped.settings.find_one({'user_id': user_id}) or {}
    cutover = settings.get('mezan2_financial_cutover') or {}
    verified = await verify_active_opening_v2(db, user_id=user_id, cutover=cutover, mongo_session=mongo_session)
    if not build_accounting_module_status(cutover, opening_posted_verified=verified)['cutover']['safe_active']:
        fail('supplier_native_accounting_not_safe_active')
    at = effective_time(invoice)
    if datetime.fromisoformat(at) > datetime.now(timezone.utc):
        fail('supplier_native_future_accounting_date')
    invoice['mz2_effective_at'] = at
    entries, snapshots = [], []

    def debit(key, mapping, amount):
        snapshots.append({**mapping, 'leg_key': key, 'amount_halalas': amount})
        entries.append(dict(leg_key=key, entity_type=mapping['entity_type'], entity_id=mapping['entity_id'],
            sub_account=mapping.get('sub_account'), side='debit', entry_type='supplier_invoice', amount=format(Decimal(amount) / 100, '.2f')))

    for index, line in enumerate(invoice['lines']):
        source_id = line.get('product_id')
        if not isinstance(source_id, str) or not source_id.strip():
            fail('supplier_native_product_v2_identity_required')
        products = await scoped.mezan_products_v2.find({'user_id': user_id, '$or': [
            {'id': source_id}, {'mezan_product_id': source_id}, {'salla_product_id': source_id},
        ]}).to_list(2)
        if len(products) != 1 or not products[0].get('mezan_product_id'):
            fail('supplier_native_product_v2_identity_required')
        # Exact operational reference resolution within the V2 product catalog;
        # financial mappings always use the canonical product_v2_id.
        product_v2_id = products[0]['mezan_product_id']
        line['product_v2_id'] = product_v2_id
        await require_source(scoped, user_id, 'product', product_v2_id, line.get('variant_id'))
        # A line's price and billed quantity are rechecked, not accepted solely
        # because the caller supplies a matching grand total.
        if line.get('product_total_halalas') != line['quantity'] * line.get('product_unit_price_halalas', -1):
            fail('supplier_native_product_amount_mismatch')
        if line['product_total_halalas']:
            mapping = await resolve_debit(scoped, user_id, 'product', product_v2_id, line.get('variant_id'))
            debit(f'product-{index}', mapping, line['product_total_halalas'])
        for j, service in enumerate(line.get('services', [])):
            await require_source(scoped, user_id, 'service', service.get('service_id'))
            from decimal import ROUND_HALF_UP
            qty = Decimal(str(service.get('quantity_per_piece', 0))) * line['quantity']
            expected = (Decimal(str(service.get('unit_price_halalas', -1))) * qty).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            if not qty.is_finite() or qty <= 0 or expected != service['total_halalas']:
                fail('supplier_native_service_amount_mismatch')
            if service['total_halalas']:
                mapping = await resolve_debit(scoped, user_id, 'service', service.get('service_id'))
                debit(f'service-{index}-{j}', mapping, service['total_halalas'])
    if invoice.get('purchase_tax'):
        require_owner(current)  # Explicit purchase-tax affirmation belongs to owner.
        tax = PurchaseTax.model_validate(invoice['purchase_tax'])
        mapping = await require_opening_identity(scoped, user_id, 'tax', tax.entity_id, 'input_vat')
        source = await scoped.accounting_source_files.find_one({'user_id': user_id, 'file_id': tax.evidence_file_id})
        if not source or not source.get('content') or source.get('sha256') != tax.evidence_sha256 or hashlib.sha256(bytes(source['content'])).hexdigest() != tax.evidence_sha256:
            fail('supplier_native_purchase_tax_evidence_required')
        debit('input-vat', {'entity_type': 'tax', 'entity_id': tax.entity_id, 'sub_account': 'input_vat',
            'financial_treatment': 'INPUT_VAT', 'evidence_file_id': tax.evidence_file_id,
            'evidence_sha256': tax.evidence_sha256, 'confirmed_by': current['id'], **(mapping or {})}, tax.amount_halalas)
    if sum(row['amount_halalas'] for row in snapshots) != total:
        fail('supplier_native_debit_total_mismatch')
    invoice['mz2_debit_mapping_snapshots'] = snapshots
    meta = dict(supplier_invoice_id=invoice['id'], supplier_source=SUPPLIERS, invoice_contract=CONTRACT,
        invoice_digest=invoice_digest(invoice), accounting_at=at)
    entries.append(dict(leg_key='supplier-payable', entity_type='supplier', entity_id=supplier['id'],
        sub_account='payable', side='credit', entry_type='supplier_invoice', amount=format(Decimal(total) / 100, '.2f')))
    for entry in entries:
        entry['metadata'] = meta
    await assert_open_journal_periods(scoped, user_id, entries)
    result = await post_journal_v2(db, user_id=user_id, actor_id=current['id'], actor_name=current['id'],
        idempotency_key='supplier-native-invoice:' + invoice['id'], txn_type='supplier_invoice', source=CONTRACT,
        effective_at=at, entries=entries, metadata=meta, mongo_session=mongo_session)
    invoice['mz2_financial_contract'] = CONTRACT
    invoice['mz2_txn_group_id'] = result['group']['txn_group_id']
    invoice['mz2_reversal_policy'] = 'reconciliation_required_reversal_disabled'
    return dict(txn_group_id=result['group']['txn_group_id'], entry_ids=[r['id'] for r in result['entries']])


async def verify_native_invoice(db, *, invoice, session, mongo_session=None, expected_total=None, actor_id=None):
    owner = invoice['user_id']
    total = invoice_total(invoice)
    if expected_total is not None and expected_total != total:
        fail('supplier_native_invoice_total_mismatch')
    if (invoice.get('mz2_financial_contract') != CONTRACT or invoice.get('experiment_mode') is not False
            or session.get('status') != 'closed' or session.get('supplier_invoice_id') != invoice['id']
            or session.get('supplier_id') != invoice.get('supplier_id')
            or (actor_id and invoice.get('approved_by') != actor_id)):
        fail('supplier_native_invoice_integrity_failed')
    gid = invoice.get('mz2_txn_group_id')
    check = await verify_journal_v2(db, user_id=owner, txn_group_id=gid, mongo_session=mongo_session)
    if not check['verified']:
        fail('supplier_native_invoice_integrity_failed')
    # get_journal_v2 accepts a session-bound database without changing the
    # immutable ledger's API (a frozen integration surface).
    read_db = scoped_database(db, owner, mongo_session) if mongo_session is not None else db
    journal = await get_journal_v2(read_db, user_id=owner, txn_group_id=gid)
    meta = journal['group'].get('metadata') or {}
    if meta.get('invoice_digest') != invoice_digest(invoice) or meta.get('supplier_invoice_id') != invoice['id']:
        fail('supplier_native_invoice_integrity_failed')
    original_ids = {row['id'] for row in journal['entries']}
    after = None
    while True:
        reversals = await query_entries_v2(read_db, user_id=owner, entity_type='supplier',
            entity_id=invoice['supplier_id'], sub_account='payable', entry_type='reversal',
            after_entry_no=after, limit=1000)
        if any((row.get('metadata') or {}).get('reverses_entry_id') in original_ids for row in reversals):
            fail('supplier_native_reversal_reconciliation_required', original_txn_group_id=gid)
        if len(reversals) < 1000:
            break
        after = reversals[-1]['entry_no']
    return invoice
