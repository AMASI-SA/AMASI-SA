"""One consistent, read-only supplier reconciliation snapshot; never a writer.

Preview approved preparation invoices against the SAVED operational balance.
Native purchase invoices remain a separate evidence inventory: an approved
native invoice/stock receipt is not permission to create a second payable.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from pymongo.read_concern import ReadConcern
from pymongo.errors import PyMongoError

from operational_balance_engine import reconcile
from operational_balance_sources import ALLOWED_COLLECTIONS, collect_sources, instant, rows
from operational_balance_store import AUDIT_PAGES, STATES, digest, fail, read
from operational_supplier_reconciliation import compare_supplier_invoices
from operational_financial_activity_runtime import checked_rows, preparation_checkpoint, prepare_in_thread

READS = ALLOWED_COLLECTIONS | {STATES, AUDIT_PAGES, 'mz2_atomic_owners'}
NATIVE_LIMIT = 20000
ACTION = 'طابق الفاتورة واستلامها وسدادها وتغطيتها في الافتتاحي قبل اعتماد أي فرق؛ لا تعِد إنشاء الفاتورة'


class SnapshotReads:
    """Capability limited to owner-scoped reads on the supplied snapshot session."""
    def __init__(self, db, owner, session):
        self.db, self.owner, self.session = db, owner, session

    def __getitem__(self, name):
        if name not in READS:
            raise ValueError('supplier_preview_source_not_allowed')
        collection, owner, session = self.db[name], self.owner, self.session
        if name == AUDIT_PAGES:
            class AuditPage:
                async def find_one(self, query):
                    if (not isinstance(query, dict) or set(query) != {'_id', 'owner_id'}
                            or query['owner_id'] != owner or not isinstance(query['_id'], str) or not query['_id']):
                        raise ValueError('supplier_preview_owner_scope_required')
                    return await collection.find_one(query, session=session)
            return AuditPage()

        def check(query):
            field = 'owner_id' if name == STATES else '_id' if name == 'mz2_atomic_owners' else 'user_id'
            if not isinstance(query, dict) or query.get(field) != owner:
                raise ValueError('supplier_preview_owner_scope_required')
            if name == STATES and query.get('_id') != owner:
                raise ValueError('supplier_preview_owner_scope_required')
        class Collection:
            def find(self, query, *args, **kwargs):
                check(query)
                return collection.find(query, *args, session=session, **kwargs)
            async def find_one(self, query, *args, **kwargs):
                check(query)
                return await collection.find_one(query, *args, session=session, **kwargs)
            def aggregate(self, pipeline, **kwargs):
                if not pipeline or not isinstance(pipeline[0], dict) or set(pipeline[0]) != {'$match'}:
                    raise ValueError('supplier_preview_owner_scope_required')
                check(pipeline[0]['$match'])
                # No $out/$merge or write-capable nested stage can enter a read preview.
                def safe(value):
                    if isinstance(value, dict):
                        return not ({'$out', '$merge', '$lookup', '$unionWith', '$graphLookup'} & set(value)) and all(safe(v) for v in value.values())
                    return not isinstance(value, list) or all(safe(v) for v in value)
                if not safe(pipeline):
                    raise ValueError('supplier_preview_write_or_unscoped_stage_forbidden')
                return collection.aggregate(pipeline, session=session, **kwargs)
        return Collection()


def supplier_proposal(current, receipts, as_of):
    """Apply the existing pure supplier reducer to an isolated slice, not all finance.

    Preserve every other fact, opening, movement, return and salary/provider row.
    This result must never be persisted; only the comparator receives it.
    """
    preparation_checkpoint()
    isolated = deepcopy(current)
    old_engine = current.get('engine', {})
    isolated['engine'] = {
        'obligations': {k: deepcopy(v) for k, v in checked_rows(old_engine.get('obligations', {}).items()) if v.get('kind') == 'supplier'},
        'facts': {k: deepcopy(v) for k, v in checked_rows(old_engine.get('facts', {}).items()) if k.startswith(('receipt:', 'return:'))},
    }
    projected = reconcile(isolated, {'supplier_receipts': receipts}, as_of)['engine']
    preparation_checkpoint()
    proposed = deepcopy(current)
    preparation_checkpoint()
    engine = proposed.setdefault('engine', {})
    engine.setdefault('facts', {}).update(projected.get('facts', {}))
    for key, row in checked_rows(projected.get('obligations', {}).items()):
        if row.get('kind') == 'supplier':
            engine.setdefault('obligations', {})[key] = row
    engine['as_of'] = as_of
    return proposed, projected.get('issues', [])


def _reference(value):
    return value.strip() if isinstance(value, str) else ''


def native_purchase_inventory(state, preparation, native, *, owner, as_of):
    """Find exact document collisions, never infer identity from price or name.

    Source-declared amounts/GL references are NOT verified ledger balances.
    Native purchase proof, settlement and opening matching remain explicit gates.
    """
    preparation_checkpoint()
    if not isinstance(native, list) or len(native) > NATIVE_LIMIT:
        raise ValueError('supplier_preview_native_scope_incomplete')
    counts = Counter(r.get('id') for r in checked_rows(native) if isinstance(r, dict))
    references = {}
    def add(supplier, reference, kind, identity):
        ref = _reference(reference)
        if supplier and ref:
            references.setdefault((supplier, ref), set()).add((kind, str(identity)))
    for invoice in checked_rows(preparation):
        add(invoice.get('supplier_id'), invoice.get('invoice_number'), 'preparation_invoice', invoice.get('id'))
    for invoice in checked_rows(state.get('inventory_purchases', [])):
        # New operational sequence numbers must NOT stand in for the vendor's reference.
        ref = invoice.get('supplier_invoice_number') if 'supplier_invoice_number' in invoice else invoice.get('invoice_number')
        add(invoice.get('supplier_id'), ref, 'operational_purchase', invoice.get('id'))
    for case in checked_rows(state.get('customer_exchanges', [])):
        for invoice in checked_rows(case.get('purchases', [])):
            add(invoice.get('supplier_id'), invoice.get('invoice_number'), 'exchange_purchase', invoice.get('id'))
    out = []
    for invoice in checked_rows(native):
        if not isinstance(invoice, dict) or invoice.get('user_id') != owner:
            raise ValueError('supplier_preview_native_owner_mismatch')
        iid, supplier = invoice.get('id'), invoice.get('supplier_counterparty_id') or invoice.get('supplier_id')
        reasons = ['native_purchase_posting_payment_and_opening_review_required']
        if not iid or counts[iid] != 1:
            reasons.append('native_purchase_identity_ambiguous')
        if not supplier or any(invoice.get(k) not in (None, supplier) for k in ('supplier_entity_id', 'supplier_account_id', 'supplier_id')):
            reasons.append('native_purchase_supplier_identity_conflict')
        approved = None
        try:
            approved = instant(invoice.get('approved_at')).isoformat()
            if instant(approved) > instant(as_of):
                reasons.append('native_purchase_after_review_time')
        except (ValueError, TypeError, OverflowError):
            reasons.append('native_purchase_approval_time_missing')
        amount = None
        try:
            value = invoice.get('total')
            n = Decimal(str(value))
            if isinstance(value, bool) or not n.is_finite() or n < 0 or n > Decimal('999999999999.99') or n != n.quantize(Decimal('.01')):
                raise ValueError()
            if invoice.get('currency') != 'SAR':
                raise ValueError()
            amount = format(n, '.2f')
        except (InvalidOperation, ValueError, TypeError):
            reasons.append('native_purchase_declared_amount_unverified')
        collisions = sorted(references.get((supplier, _reference(invoice.get('invoice_number'))), set()))
        if collisions:
            reasons.append('native_purchase_possible_duplicate_document')
        out.append({'invoice_id': iid, 'supplier_id': supplier, 'invoice_number': invoice.get('invoice_number'),
            'approved_at': approved, 'source_state': invoice.get('state'),
            'source_declared_gross_sar': amount, 'verified_native_payable_sar': None,
            'liability_reference': invoice.get('liability_id'), 'journal_reference': invoice.get('txn_group_id'),
            'possible_existing_documents': [{'source': s, 'id': i} for s, i in collisions],
            'difference_sar': None, 'posting_allowed': False, 'issues': reasons, 'required_action': ACTION})
    return sorted(out, key=lambda row: str(row['invoice_id']))


async def _preview(scoped, owner, as_of):
    from operational_balance_service import accounting_inactive
    await accounting_inactive(scoped, owner)
    state = await read(scoped, owner)
    if state.get('status') != 'active' or not state.get('started_at'):
        fail('supplier_review_not_active', 'المحاسبة التشغيلية غير مفعلة؛ لا يمكن عرض مقارنة أرصدة حالية')
    stamp = instant(as_of).isoformat()
    sources = await collect_sources(scoped, owner, state['started_at'], stamp, operational_state=state)
    invoices = await rows(scoped, owner, 'mezan_supplier_invoices_v2')
    proposal, reducer_issues = await prepare_in_thread(supplier_proposal, state, sources.get('supplier_receipts', []), stamp)
    result = await prepare_in_thread(compare_supplier_invoices, state, proposal, invoices, owner_id=owner, as_of=stamp)
    native = await scoped['purchase_invoices'].find({'user_id': owner, 'state': 'approved'}, {
        '_id': 0, 'user_id': 1, 'id': 1, 'invoice_number': 1, 'state': 1, 'schema_version': 1,
        'supplier_counterparty_id': 1, 'supplier_entity_id': 1, 'supplier_account_id': 1, 'supplier_id': 1,
        'total': 1, 'currency': 1, 'approved_at': 1, 'liability_id': 1, 'txn_group_id': 1,
        'subtotal': 1, 'tax_amount': 1, 'tax_treatment': 1, 'lines': 1,
        'inventory_account_id': 1, 'input_vat_account_id': 1, 'tax_evidence_ref': 1, 'tax_evidence_verified': 1,
    }).to_list(length=NATIVE_LIMIT + 1)
    candidates = await prepare_in_thread(native_purchase_inventory, state, invoices, native, owner=owner, as_of=stamp)
    from operational_native_purchase_review import load_native_purchase_reviews
    native_proof = await load_native_purchase_reviews(scoped.db, owner, native,
        session=scoped.session, started_at=state['started_at'], as_of=stamp)
    return await prepare_in_thread(_complete_preview, state, sources, result, reducer_issues,
        native, candidates, native_proof, invoices, owner=owner, stamp=stamp)


def _complete_preview(state, sources, result, reducer_issues, native, candidates, native_proof,
                      invoices, *, owner, stamp):
    """Finish exact source matching and its manifest in the reader's CPU scope."""
    preparation_checkpoint()
    # Keep source diagnostics, including the original reference and scope. Missing
    # receipt evidence is not made complete by a perfectly balanced arithmetic sum.
    source_issues = sources.get('issues', [])
    blocking = [p for p in checked_rows(source_issues + reducer_issues)
                if str(p.get('source_id', '')).startswith(('receipt:', 'supplier:'))
                or str(p.get('code', '')).startswith(('supplier_', 'preparation_'))]
    for problem in checked_rows(blocking):
        if problem not in result['issues']:
            result['issues'].append(deepcopy(problem))
    if blocking:
        result['arithmetic_complete'], result['totals'] = False, None
    from operational_native_purchase_link import validate_saved_link
    native_by_id = {row['id']: row for row in checked_rows(native)}
    for candidate in checked_rows(candidates):
        candidate['native_ledger_review'] = native_proof.get(candidate['invoice_id'])
        match = validate_saved_link(state, native_by_id[candidate['invoice_id']], candidate['native_ledger_review'] or {})
        candidate['operational_link_review'] = match
        represented = {state.get('engine', {}).get('obligations', {}).get(key, {}).get('source_invoice_id')
            or state.get('engine', {}).get('obligations', {}).get(key, {}).get('invoice_id')
            for key in match.get('obligation_ids', [])}
        if match.get('matched') and any(c['id'] not in represented for c in candidate['possible_existing_documents']):
            match.update(matched=False, issues=['native_link_unresolved_document_collision'])
        if match.get('matched'):
            candidate['verified_native_payable_sar'] = candidate['native_ledger_review']['native_outstanding_sar']
            candidate['difference_sar'] = '0.00'
            candidate['issues'] = []
    result.update(read_only=True, snapshot_consistent=True, state_revision=state.get('revision', 0),
        saved_balance_as_of=state.get('engine', {}).get('as_of'), native_purchase_review=candidates,
        native_financial_matching_complete=all(c['operational_link_review']['matched'] for c in candidates), source_issues=deepcopy(source_issues),
        approval_required=True, proposed_financial_write_allowed=False,
        scope='approved_preparation_invoices; native_purchases_separate_unverified',
        review_manifest_sha256=digest({'owner': owner, 'state': state, 'receipts': sources.get('supplier_receipts', []),
                                      'invoices': invoices, 'native': native, 'native_proof': native_proof, 'as_of': stamp}))
    preparation_checkpoint()
    for row in checked_rows(result['rows']):
        row['required_action'] = ACTION if row.get('difference') != '0.00' or row.get('issues') else 'لا تعِد تسجيل الفاتورة؛ راجع السداد عند الحاجة'
    return result


async def _receiving_only(scoped, owner, observed_at):
    """Two tenant reads plus the accounting-mode guard inside a snapshot, no order scan or ledger."""
    from operational_balance_service import accounting_inactive
    from operational_receiving_coverage import compare_receiving
    from operational_balance_archive import SNAPSHOT, ArchiveError, unpack_financial_snapshot
    await accounting_inactive(scoped, owner)
    state = await scoped[STATES].find_one({"_id": owner, "owner_id": owner},
        {"_id": 0, "owner_id": 1, "status": 1, "started_at": 1, "revision": 1,
         "engine.as_of": 1, "engine.obligations": 1, "engine.facts": 1, SNAPSHOT: 1})
    if state:
        try:
            state = await prepare_in_thread(unpack_financial_snapshot, state, selected={"obligations", "facts"})
        except ArchiveError:
            fail("operational_audit_archive_invalid", "تعذر التحقق من لقطة الأرصدة المحفوظة", 503)
    if not state or state.get("status") != "active" or not state.get("engine", {}).get("as_of"):
        fail("supplier_review_no_saved_snapshot", "لا توجد لقطة تشغيلية نشطة يمكن مطابقتها.")
    fields = ("id user_id supplier_id invoice_number approved_at currency status experiment_mode experiment_run_id "
              "total_halalas subtotal_halalas purchase_tax tax vat tax_halalas vat_halalas tax_amount vat_amount "
              "lines.product_price_authority lines.quantity lines.product_charge_eligible lines.piece_ids "
              "lines.product_unit_price_halalas lines.services.service_id lines.services.unit_price_halalas "
              "lines.services.quantity_per_piece lines.product_total_halalas lines.services.total_halalas lines.total_halalas").split()
    # Approval timestamps may be BSON dates or ISO strings with offsets. Compare in UTC
    # in the reducer rather than silently losing older schemas in a typed date query.
    invoices = await scoped["mezan_supplier_invoices_v2"].find(
        {"user_id": owner, "status": {"$in": [None, "payable_posted"]}, "approved_at": {"$ne": None},
         "experiment_mode": {"$ne": True}},
        {"_id": 0, **dict.fromkeys(fields, 1)}).to_list(length=NATIVE_LIMIT + 1)
    result = await prepare_in_thread(compare_receiving, state, invoices, owner=owner, observed_at=observed_at)
    result.update(snapshot_consistent=True, state_revision=state.get("revision"))
    return result


async def _correction_preview(scoped, owner, observed_at, invoice_id, expected_as_of, expected_revision):
    from operational_balance_service import accounting_inactive
    from operational_receiving_correction import load_correction
    from operational_receiving_coverage import compare_receiving
    from operational_supplier_reconciliation import _id

    await accounting_inactive(scoped, owner)
    state = await read(scoped, owner)
    saved = state.get("engine", {}).get("as_of")
    if state.get("status") != "active" or not saved:
        fail("supplier_review_no_saved_snapshot", "لا توجد لقطة تشغيلية نشطة يمكن مطابقتها.")
    cutoff = instant(saved).isoformat()
    if cutoff != expected_as_of or state.get("revision") != expected_revision:
        fail("supplier_review_stale_snapshot", "تغيرت اللقطة التشغيلية؛ أعد فحص فواتير الاستلام.")
    invoices = await scoped["mezan_supplier_invoices_v2"].find(
        {"user_id": owner, "id": _id(invoice_id)}, {"_id": 0}).to_list(length=2)
    comparison = await prepare_in_thread(compare_receiving, state, invoices, owner=owner, observed_at=observed_at)
    if len(invoices) != 1 or len(comparison["rows"]) != 1:
        fail("supplier_review_invoice_unavailable", "لم توجد هذه الفاتورة ضمن فترة اللقطة.", 404)
    row = comparison["rows"][0]
    if row["source_issue"]:
        result = dict(invoice_id=invoice_id, status="blocked", legs=[], reasons=["invoice_evidence_incomplete"])
    else:
        result = await load_correction(scoped, owner, state, invoices[0], row, cutoff)
    result.update(schema="receiving_correction_preview_v1", read_only=True, posting_allowed=False,
                  snapshot_consistent=True, state_revision=expected_revision, saved_balance_as_of=cutoff)
    return result


async def supplier_invoice_preview(db, owner, *, as_of=None, receiving_only=False,
                                   invoice_id=None, expected_as_of=None, expected_revision=None):
    """No refresh, no compare-and-swap, no journal call and no standalone fallback."""
    if not isinstance(owner, str) or not owner or owner != owner.strip():
        raise ValueError('supplier_preview_owner_scope_required')
    stamp = as_of or datetime.now(timezone.utc).isoformat()
    try:
        async with await db.client.start_session() as session:
            async with session.start_transaction(read_concern=ReadConcern('snapshot')):
                scoped = SnapshotReads(db, owner, session)
                if invoice_id is not None:
                    return await _correction_preview(scoped, owner, stamp, invoice_id, expected_as_of, expected_revision)
                if receiving_only:
                    return await _receiving_only(scoped, owner, stamp)
                return await _preview(scoped, owner, stamp)
    except ValueError as exc:
        code = str(exc)
        if not code.startswith(('supplier_', 'source_', 'operational_')):
            code = 'supplier_preview_evidence_incomplete'
        fail(code, 'تعذر إثبات مقارنة الفواتير كاملة؛ لم تتغير الأرصدة')
    except PyMongoError:
        fail('supplier_preview_snapshot_unavailable', 'تعذر فتح لقطة قراءة متسقة؛ لم تتغير الأرصدة', 503)
