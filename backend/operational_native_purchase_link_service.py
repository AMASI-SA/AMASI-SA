"""Owner-approved linkage of existing native proofs; no native financial writer.

A source/state preview is read in one Mongo snapshot. An explicit owner approval
may add only the reviewed operational debt/payment mirrors through the existing
aggregate CAS; source invoices, original journals, stock and old cash never
change. Stale/reversed/ambiguous evidence fails closed. No automatic backfill.
"""
from copy import deepcopy
from pymongo.read_concern import ReadConcern
from pymongo.errors import PyMongoError

from operational_balance_store import (read, now, fail, digest, mutate, replay, remember,
    audit, check_authorization)
from operational_balance_service import active_gate
from operational_balance_sources import entities, rows
from operational_native_purchase_review import load_native_purchase_reviews
from operational_native_purchase_link import build_link_plan, validate_saved_link, LINKS
from operational_supplier_invoice_preview import SnapshotReads, native_purchase_inventory
from operational_financial_activity_runtime import checked_rows, preparation_checkpoint, prepare_in_thread


async def _owner(db, owner, actor):
    user = await db.users.find_one({'id': actor}, {'_id': 0})
    if (actor != owner or not user or user.get('role') != 'owner' or user.get('disabled')
            or user.get('is_active') is False or user.get('deleted_at')):
        fail('native_link_owner_required', 'اعتماد مطابقة الفواتير والسداد متاح للمالك فقط', 403)
    await check_authorization()


async def _read_context(db, owner, selection, stamp):
    try:
        async with await db.client.start_session() as session:
            async with session.start_transaction(read_concern=ReadConcern('snapshot')):
                scoped = SnapshotReads(db, owner, session)
                state = await read(scoped, owner)
                await active_gate(scoped, owner, state)
                invoices = await scoped['purchase_invoices'].find(
                    {'user_id': owner, 'id': selection['invoice_id']}, {'_id': 0}).to_list(length=2)
                if len(invoices) != 1:
                    fail('native_link_invoice_missing_or_duplicated', 'هوية فاتورة الشراء غير موجودة أو مكررة')
                inv = invoices[0]
                proof = (await load_native_purchase_reviews(db, owner, invoices, session=session,
                    started_at=state['started_at'], as_of=stamp))[inv['id']]
                suppliers = [r for r in await entities(scoped, owner, 'supplier')
                             if r['id'] == inv.get('supplier_entity_id')]
                if len(suppliers) != 1:
                    fail('native_link_supplier_unavailable', 'هوية المورد غير مكتملة في المحاسبة التشغيلية')
                banks = await entities(scoped, owner, 'bank')
                preparation = await rows(scoped, owner, 'mezan_supplier_invoices_v2')
                inventory = await prepare_in_thread(native_purchase_inventory, state, preparation, invoices, owner=owner, as_of=stamp)
                collisions = inventory[0]['possible_existing_documents']
                return state, inv, proof, suppliers[0], banks, collisions
    except PyMongoError:
        fail('native_link_snapshot_unavailable', 'تعذر قراءة الفاتورة والسداد من لقطة متسقة؛ لم تتغير الأرصدة', 503)
    except ValueError:
        fail('native_link_source_incomplete', 'بيانات مصدر الفاتورة أو الجهة غير مكتملة؛ لم تتغير الأرصدة')


async def preview(db, owner, actor, selection, *, clock=None):
    await _owner(db, owner, actor)
    stamp = clock or now()
    context = await _read_context(db, owner, selection, stamp)
    state, inv, source, supplier, banks, collisions = context
    result, _ = await prepare_in_thread(build_link_plan, state, inv, source, selection, supplier=supplier,
                                       banks=banks, as_of=stamp, collisions=collisions)
    saved_match = await prepare_in_thread(validate_saved_link, state, inv, source)
    result.update(session_scope=digest([owner, actor]), snapshot_consistent=True,
        source_issues=source.get('issues', []) if not source.get('source_verified') else [],
        possible_existing_documents=collisions,
        saved_match=saved_match)
    await _owner(db, owner, actor)
    await active_gate(db, owner, state)
    return result


async def candidates(db, owner, actor, invoice_id, *, clock=None):
    """Read IDs/amounts for deliberate selection, never auto-match by amount."""
    await _owner(db, owner, actor)
    stamp = clock or now()
    selection = {'invoice_id': invoice_id}
    state, inv, source, supplier, banks, collisions = await _read_context(db, owner, selection, stamp)
    result = await prepare_in_thread(_candidate_result, state, invoice_id, source, supplier, collisions, owner=owner, actor=actor)
    await _owner(db, owner, actor)
    await active_gate(db, owner, state)
    return result


def _candidate_result(state, invoice_id, source, supplier, collisions, *, owner, actor):
    preparation_checkpoint()
    obligations = [deepcopy(row) for row in checked_rows(state.get('engine', {}).get('obligations', {}).values())
        if row.get('party_type') == 'supplier' and row.get('party_id') == supplier['id'] and row.get('currency') == 'SAR']
    moves = [deepcopy(m) for m in checked_rows(state['movements']) if m.get('party_type') == 'supplier'
        and m.get('party_id') == supplier['id'] and m.get('direction') == 'outgoing' and m.get('currency') == 'SAR']
    allowed_obligation = ('id', 'invoice_id', 'invoice_number', 'confirmed', 'expected', 'business_date', 'label')
    allowed_movement = ('id', 'amount', 'bank_id', 'bank_name', 'occurred_at', 'reference', 'allocations')
    result = dict(invoice_id=invoice_id, supplier_name=supplier.get('name', supplier['id']), source=source,
        existing_obligations=[{k: o.get(k) for k in allowed_obligation} for o in checked_rows(obligations)],
        existing_movements=[{k: m.get(k) for k in allowed_movement} for m in checked_rows(moves)],
        saved_link=deepcopy((state.get(LINKS) or {}).get(invoice_id)),
        possible_existing_documents=collisions, snapshot_consistent=True, read_only=True,
        posting_allowed=False, session_scope=digest([owner, actor]))
    return result


async def approve(db, owner, actor, payload, *, clock=None):
    stamp = clock or now()
    await _owner(db, owner, actor)
    if payload.get('expected_session_scope') != digest([owner, actor]):
        fail('operational_actor_scope_changed', 'تغير الحساب؛ أعد المعاينة', 409)
    if payload.get('confirm_reviewed_identities') is not True:
        fail('native_link_identity_confirmation_required', 'يلزم تأكيد هويات الفاتورة والسداد المختارة', 422)
    selection = payload['selection']
    action = 'native_purchase_link:' + actor
    async def apply(state):
        await _owner(db, owner, actor)
        old = replay(state, action, payload)
        if old is not None:
            return old
        await active_gate(db, owner, state)
        snapshot, inv, source, supplier, banks, collisions = await _read_context(db, owner, selection, stamp)
        if digest(snapshot) != digest(state):
            fail('native_link_preview_stale', 'تغير الرصيد؛ أعد المعاينة قبل الاعتماد')
        check, proposed = build_link_plan(snapshot, inv, source, selection, supplier=supplier,
                                         banks=banks, as_of=stamp, collisions=collisions)
        if not check['can_approve']:
            fail('native_link_evidence_incomplete', 'المطابقة غير مكتملة: ' + '، '.join(check['issues']))
        if check['preview_hash'] != payload.get('expected_preview_hash'):
            fail('native_link_preview_stale', 'تغيرت الفاتورة أو السداد أو الأرصدة؛ أعد المعاينة')
        if check['unrecorded_money_confirmation_required'] and payload.get('confirm_unrecorded_money') is not True:
            fail('native_link_unrecorded_confirmation_required',
                 'يلزم تأكيد أن المبالغ المقترحة غير مسجلة سابقًا ولا مشمولة في رصيد البداية', 422)
        # Preserve every old opening, movement and obligation byte-for-byte. Only
        # new reviewed obligations/payment mirrors and the explicit link are added.
        for key, value in state.get('engine', {}).get('obligations', {}).items():
            if proposed['engine']['obligations'].get(key) != value:
                fail('native_link_history_mutation_forbidden')
        if proposed['movements'][:len(state['movements'])] != state['movements'] or proposed['openings'] != state['openings']:
            fail('native_link_history_mutation_forbidden')
        await _owner(db, owner, actor)
        await active_gate(db, owner, state)
        before = deepcopy(state.get(LINKS, {}).get(inv['id']))
        state['engine'] = proposed['engine']
        state['movements'] = proposed['movements']
        state[LINKS] = proposed[LINKS]
        record = state[LINKS][inv['id']]
        audit(state, actor, 'native_purchase_link_approved', before, record,
              selection['evidence_ref'], 'native_purchase_review', stamp)
        result = dict(ok=True, invoice_id=inv['id'], operational_balance_matched=True,
            native_ledger_changed=False, actual_bank_transfer_executed=False,
            before=check['before'], after=check['after'], supplier_difference_sar=check['supplier_difference_sar'],
            bank_differences_sar=check['bank_differences_sar'], obligation_ids=check['obligation_ids'],
            new_payment_count=check['new_payment_count'], request_id=payload['request_id'])
        remember(state, action, payload, result)
        return result
    result = await mutate(db, owner, apply)
    # Owner-only action: a late access revocation must not disclose new balances.
    await _owner(db, owner, actor)
    return result


async def verified_saved_links(db, owner, state, *, as_of):
    """Re-verify linked native sources before declaring received-cost complete.

    No links => no extra DB/ledger work for normal stores. A source changed by
    a later payment/reversal becomes visibly incomplete, never auto-replayed.
    """
    identities = sorted((state.get(LINKS) or {}).keys()) if state else []
    if not identities:
        return {}
    if len(identities) > 2000 or state.get('owner_id') != owner:
        raise ValueError('native_link_source_scope_incomplete')
    async def load(raw, session):
        invoices = await raw.purchase_invoices.find({'user_id': owner, 'id': {'$in': identities}},
                                                    {'_id': 0}, session=session).to_list(length=2001)
        if len(invoices) > 2000:
            raise ValueError('native_link_source_scope_incomplete')
        proof = await load_native_purchase_reviews(raw, owner, invoices, session=session,
                                                   started_at=state['started_at'], as_of=as_of)
        result = {key: {'matched': False, 'issues': ['native_link_source_missing']} for key in identities}
        seen = set()
        for invoice in invoices:
            key = invoice['id']
            result[key] = validate_saved_link(state, invoice, proof.get(key, {})) if key not in seen else {
                'matched': False, 'issues': ['native_link_source_duplicated']}
            seen.add(key)
        return result
    if isinstance(db, SnapshotReads):
        return await load(db.db, db.session)
    async with await db.client.start_session() as session:
        async with session.start_transaction(read_concern=ReadConcern('snapshot')):
            return await load(db, session)
