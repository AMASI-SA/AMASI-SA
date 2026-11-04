"""Evidenced driver bank/POS approval into the independent operational state.

Bank arrival and POS receivables are distinct; neither is driver cash custody.
No sales, supplier cost, shipping fee, opening or native journal is replayed.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import Literal
from pydantic import Field, StrictInt

from accounting_driver_payment_review import DriverReviewInput as NativeInput
from operational_balance_store import STATES, digest, fail, audit, logical_state, now, read
from operational_salary_start import instant, RIYADH
from operational_driver_review_store import EVENTS, transact

REVIEWS = 'store_delivery_payment_reviews'
COLLECTIONS = 'store_delivery_collections'
RECEIPTS = 'store_delivery_receipts'
FACTS = 'driver_payment_review_facts'


class ReviewInput(NativeInput):
    expected_financial_source: Literal['operational', 'native'] | None = None
    expected_review_revision: StrictInt | None = Field(default=None, ge=1)
    expected_session_scope: str | None = Field(default=None, min_length=1, max_length=128)


def exact_money(value):
    if isinstance(value, bool) or value is None:
        fail('operational_driver_review_amount_invalid')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number != number.quantize(Decimal('.01')):
            raise ValueError()
        return format(number, '.2f')
    except (ValueError, ArithmeticError):
        fail('operational_driver_review_amount_invalid')


def sealed(row):
    return bool(row) and row.get('seal') == digest({k: v for k, v in row.items() if k not in {'_id', 'seal'}})


async def single(db, collection, query, code):
    rows = await db[collection].find(query).limit(2).to_list(2)
    if len(rows) != 1:
        fail(code)
    return rows[0]


async def authorize(db, owner, actor):
    user = await db.users.find_one({'id': actor})
    if (not user or user.get('disabled') or user.get('is_active') is False or user.get('deleted_at')
            or (actor != owner and user.get('created_by') != owner)
            or (actor == owner and user.get('role') != 'owner')
            or 'store_delivery.payments.review' in (user.get('denied_permissions') or [])
            or (user.get('role') not in {'owner', 'admin', 'accountant'}
                and 'store_delivery.payments.review' not in (user.get('extra_permissions') or []))):
        fail('accountant_permission_required', status=403)


async def financial_source(db, owner):
    state = await db[STATES].find_one({'_id': owner, 'owner_id': owner}) or {}
    control = await db.mz2_atomic_owners.find_one({'_id': owner}) or {}
    settings = await db.settings.find_one({'user_id': owner}) or {}
    native = (control.get('ledger_backend_state') == 'v2_active'
              or (settings.get('mezan2_financial_cutover') or {}).get('status') == 'active')
    # Native activation takes priority. An obsolete active operational flag
    # cannot authorize a second financial writer.
    return 'native' if native else 'operational' if state.get('status') == 'active' else 'unavailable'


async def _evidence(db, owner, assignment_id, state, stamp):
    review = await single(db, REVIEWS, {'user_id': owner, 'assignment_id': assignment_id}, 'driver_payment_review_required')
    assignment = await single(db, 'store_delivery_assignments', {'user_id': owner, 'id': assignment_id}, 'driver_delivery_required')
    collection = await single(db, COLLECTIONS, {'user_id': owner, 'assignment_id': assignment_id}, 'driver_collection_required')
    order = await single(db, 'unified_orders', {'user_id': owner, 'order_number': assignment.get('order_number')},
                         'operational_driver_order_identity_required')
    raw = (order.get('raw_by_source') or {}).get('salla_direct') or {}
    # A contradictory/refunded order needs reconciliation, not an automatic
    # receipt approval based solely on the surviving delivery label.
    from operational_balance_sources import _old_delivery_cod
    try:
        _old_delivery_cod(raw)
    except (ValueError, KeyError, TypeError, ArithmeticError):
        fail('operational_driver_order_reconciliation_required')
    assignments = await db.store_delivery_assignments.find({'user_id': owner, '$or': [
        {'order_number': assignment.get('order_number')}, {'order_id': assignment.get('order_id')}]}).limit(1001).to_list(1001)
    if len(assignments) > 1000:
        fail('operational_source_scope_too_large')
    from operational_delivery_evidence import resolve_delivery_event
    try:
        event = resolve_delivery_event(raw, source=order, owner=owner, assignments=assignments,
            setup={}, started_at=state['started_at'], as_of=stamp)
        if not event or event.get('assignment_id') != assignment_id:
            fail('operational_cutover_reconciliation_required')
        totals = [v.get('amount') if isinstance(v, dict) else v for v in
                  ((raw.get('amounts') or {}).get('total'), raw.get('total'), raw.get('total_amount')) if v is not None]
        if not totals or len({exact_money(v) for v in totals}) != 1:
            fail('operational_driver_review_amount_conflict')
        total_value = totals[0]
        value = exact_money(review.get('amount'))
        if Decimal(value) > Decimal(exact_money(total_value)):
            fail('operational_driver_review_amount_conflict')
        from operational_balance_sources import _event_collection_valid
        _event_collection_valid(collection, event, Decimal(exact_money(total_value)), instant(stamp))
        start, end = instant(state['started_at']), instant(stamp)
        if not start < instant(review['submitted_at']) <= end:
            fail('operational_cutover_reconciliation_required')
    except (ValueError, KeyError, TypeError, ArithmeticError):
        fail('operational_driver_delivery_evidence_required')
    driver = event['party_id']
    method = review.get('payment_method')
    if (review.get('status') != 'pending' or not review.get('id') or not collection.get('id')
            or type(review.get('revision', 1)) is not int or review.get('revision', 1) < 1
            or method not in {'bank_transfer', 'card_terminal'}
            or collection.get('payment_method') != method
            or exact_money(collection.get('amount')) != value
            or collection.get('payment_confirmed') is True
            or any(str(r.get('order_id')) != event['order_id'] or r.get('order_number') != event['order_number']
                   or r.get('driver_id') != driver for r in (review, assignment, collection))
            or any(r.get('currency', 'SAR') != 'SAR' for r in (review, assignment, collection))):
        fail('driver_payment_review_source_conflict')
    for row in (review, assignment, collection):
        if row.get('financial_txn_group_id') or row.get('ledger_txn_group_id'):
            fail('operational_driver_native_effect_already_exists')
    native = await db.mz2_courier_delivery_evidence_v1.find_one({'user_id': owner, 'assignment_id': assignment_id,
                                                              'txn_group_id': {'$exists': True, '$ne': None}})
    if native:
        fail('operational_driver_native_effect_already_exists')
    for movement in state.get('movements', []):
        if (movement.get('direction') == 'incoming' and (movement.get('order_number') == event['order_number']
                or movement.get('order_id') == event['order_id'])):
            fail('operational_driver_prior_payment_reconciliation_required')
    if any(r.get('order_id') == event['order_id'] for r in state.get(FACTS, {}).values()):
        fail('operational_driver_prior_payment_reconciliation_required')
    return review, assignment, collection, order, event, value


async def _receipt(db, owner, review, collection, event, state, stamp):
    token = review.get('receipt_reference')
    if not token or collection.get('receipt_reference') != token:
        fail('driver_payment_receipt_required')
    receipt = await single(db, RECEIPTS, {'user_id': owner, 'driver_id': event['party_id'],
        'assignment_id': event['assignment_id'], 'token': token, 'status': 'bound'}, 'driver_payment_receipt_required')
    content = bytes(receipt.get('content') or b'')
    hashed = sha256(content).hexdigest()
    if (not content or len(content) > 8 * 1024 * 1024 or receipt.get('sha256') != hashed
            or receipt.get('review_status') == 'approved' or receipt.get('financial_txn_group_id')
            or receipt.get('operational_event_id')):
        fail('driver_payment_receipt_integrity_failure')
    try:
        if not instant(state['started_at']) < instant(receipt['bound_at']) <= instant(stamp):
            fail('operational_cutover_reconciliation_required')
    except (ValueError, KeyError, TypeError):
        fail('operational_driver_receipt_time_required')
    if any(row.get('receipt_hash') == hashed for row in state.get('movements', [])):
        fail('driver_payment_receipt_already_consumed')
    return receipt, hashed


async def _bank_proof(db, owner, bank_id, movement_id, value, state, stamp):
    """Same original-bytes contract as native intake, without its journal session."""
    from accounting_financial_identity import list_financial_accounts
    from accounting_advertising_contract import digest as bank_digest
    banks = await list_financial_accounts(db, owner, account_types=('bank',), currency='SAR')
    bank = next((b for b in banks if b['id'] == bank_id), None)
    if not bank:
        fail('MZ2_LINK_REQUIRED')
    movement = await single(db, 'mz2_daily_movements', {'user_id': owner, 'id': movement_id},
                            'native_bank_statement_evidence_required')
    if (movement.get('status') != 'unclassified' or movement.get('direction') != 'in'
            or movement.get('bank_account_id') != bank_id or movement.get('currency') != 'SAR'
            or movement.get('source') not in {'bank_statement_import', 'manual_reconciled_bank_statement'}
            or any(movement.get(k) for k in ('receipt_id', 'accounting_event_id', 'operational_event_id',
                                            'confirmed_provider', 'explicit_provider', 'suggested_provider'))):
        fail('native_bank_statement_evidence_required')
    if any(m.get('direction') == 'incoming' and (
            m.get('bank_movement_id') == movement['id'] or
            m.get('reference') in {movement['id'], movement.get('reference')} - {None, ''})
            for m in state.get('movements', [])):
        fail('operational_driver_prior_payment_reconciliation_required')
    source = await single(db, 'mz2_daily_movement_files', {'user_id': owner, 'id': movement.get('file_id')},
                          'native_bank_statement_evidence_required')
    blob = await single(db, 'accounting_source_files', {'user_id': owner, 'file_id': movement.get('file_id')},
                        'native_bank_statement_evidence_required')
    content = bytes(blob.get('content') or b'')
    hashed = sha256(content).hexdigest()
    if (not content or source.get('bank_account_id') != bank_id or source.get('source') != 'mz2_bank_statement'
            or any(hashed != h for h in (source.get('file_hash'), blob.get('sha256'), movement.get('file_hash')))):
        fail('native_bank_statement_evidence_required')
    from accounting_daily_movements import parse_daily_movement_xlsx
    try:
        facts = [r for r in parse_daily_movement_xlsx(content)['rows'] if r['row_no'] == movement.get('row_no')]
        if len(facts) != 1:
            fail('native_bank_statement_evidence_required')
        fact = facts[0]
        identity = (bank_digest([owner, bank_id, 'reference', fact['reference']]) if fact.get('reference') else
                    bank_digest([owner, movement['file_id'], fact['row_no'], fact['row_hash']]))
        if (movement['_id'] != identity or exact_money(fact['amount']) != value
                or exact_money(movement['amount']) != value or fact['direction'] != 'in'
                or fact['date'] != movement.get('movement_date') or fact.get('reference') != movement.get('reference')
                or fact.get('explicit_provider') or fact.get('suggested_provider')):
            fail('native_bank_statement_evidence_required')
        day = date.fromisoformat(fact['date'])
        if not instant(state['started_at']).astimezone(RIYADH).date() < day <= instant(stamp).astimezone(RIYADH).date():
            fail('operational_cutover_reconciliation_required')
    except (ValueError, KeyError, TypeError, ArithmeticError):
        fail('native_bank_statement_evidence_required')
    return bank, movement, {'source_namespace': 'mz2_daily_movements', 'source_record_id': movement['id'],
                           'source_revision': bank_digest([owner, bank_id, hashed, fact])}


async def options(db, *, owner, actor, assignment_id, clock=None):
    await authorize(db, owner, actor)
    if await financial_source(db, owner) != 'operational':
        fail('operational_driver_review_phase_changed')
    state = await read(db, owner)
    stamp = clock or now()
    review = await single(db, REVIEWS, {'user_id': owner, 'assignment_id': assignment_id}, 'driver_payment_review_required')
    value = exact_money(review.get('amount'))
    blocker = None
    can_reject = False
    from fastapi import HTTPException
    try:
        review, _, collection, _, event, value = await _evidence(db, owner, assignment_id, state, stamp)
        can_reject = True
        await _receipt(db, owner, review, collection, event, state, stamp)
    except HTTPException as error:
        if error.status_code != 409 or not isinstance(error.detail, dict):
            raise
        blocker = error.detail.get('code')

    from operational_pos_identity import pos_candidates
    from accounting_onboarding_ssot import FACTS as POS_FACTS
    rows = await db[POS_FACTS].find({'user_id': owner, 'status': 'active', 'category': 'other_receivable'}).limit(1001).to_list(1001)
    if len(rows) > 1000:
        fail('operational_source_scope_too_large')
    destinations = pos_candidates(rows, owner) if review['payment_method'] == 'card_terminal' else []
    return {'assignment_id': assignment_id, 'payment_method': review['payment_method'], 'amount': value,
            'financial_source': 'operational', 'can_approve': blocker is None, 'can_reject': can_reject,
            'blocker': blocker, 'expected_review_revision': review.get('revision', 1),
            'session_scope': digest([owner, actor]), 'bank_account_id': review.get('bank_account_id'),
            'pos_receivables': [{'id': r['id'], 'display_name': r['name']} for r in destinations],
            'bank_movements': [], 'started_at': state['started_at'],
            'note': 'الشبكة ذمة للمتجر وليست نقدًا؛ التحويل يتطلب حركة بنك مثبتة بعد يوم القطع.'}


async def review_payment(db, *, owner, actor, assignment_id, payload, clock=None):
    stamp = clock or now()
    if payload.expected_financial_source != 'operational' or payload.expected_session_scope != digest([owner, actor]):
        fail('operational_driver_review_scope_required')
    if payload.decision == 'rejected' and not payload.note.strip():
        fail('payment_review_rejection_reason_required', status=422)
    request_hash = digest(payload.model_dump(mode='json'))

    async def commit(scoped):
        review = await single(scoped, REVIEWS, {'user_id': owner, 'assignment_id': assignment_id}, 'driver_payment_review_required')
        revision = review.get('revision', 1)
        if type(revision) is not int or revision != payload.expected_review_revision:
            fail('operational_driver_review_revision_changed')
        key = digest([owner, 'operational-driver-review', review.get('id'), revision])
        prior = await scoped[EVENTS].find_one({'user_id': owner, '_id': key})
        if prior:
            if not sealed(prior) or prior.get('request_hash') != request_hash:
                fail('driver_payment_review_idempotency_conflict')
            # A receipt alone is not success if its saved monetary effect is missing.
            if prior.get('decision') == 'approved':
                from operational_driver_review_report import validated_review_facts
                saved = await read(scoped, owner)
                verified, _ = validated_review_facts(saved)
                if not any(r == prior.get('fact') and r.get('id') == key for r in verified):
                    fail('operational_driver_review_effect_conflict')
            # A completed identical request never reposts money after phase change.
            return deepcopy(prior['result'])
        if await financial_source(scoped, owner) != 'operational':
            fail('operational_driver_review_phase_changed')
        state = await single(scoped, STATES, {'_id': owner, 'owner_id': owner}, 'operational_not_active')
        # Check the history in this transaction, and retain its compact representation
        # for the revision update. This decision writes only present-day monetary fields.
        await logical_state(scoped, owner, state)
        original_revision = state['revision']
        review, assignment, collection, order, event, value = await _evidence(scoped, owner, assignment_id, state, stamp)
        receipt = movement = None
        fact = None
        if payload.decision == 'approved':
            receipt, hashed = await _receipt(scoped, owner, review, collection, event, state, stamp)
            method, target = review['payment_method'], payload.destination_financial_id
            if not target:
                fail('driver_payment_destination_required')
            if method == 'bank_transfer':
                if target != review.get('bank_account_id') or target != collection.get('bank_account_id'):
                    fail('driver_payment_destination_proof_invalid')
                bank, movement, proof = await _bank_proof(scoped, owner, target, payload.settlement_reference, value, state, stamp)
                name, dest_type = bank.get('name') or target, 'bank'
            else:
                from accounting_driver_payment_port import require_driver_payment_destination
                proof = await require_driver_payment_destination(scoped, owner, payment_method=method,
                    financial_account_id=target, evidence_reference=payload.settlement_reference, amount=value,
                    receipt_hash=hashed, review=review, receipt=receipt, actor_id=actor,
                    reviewed_amount=payload.pos_reviewed_amount)
                name, dest_type = proof['display_name'], 'pos_receivable'
            # Consume both source reference and actual receipt bytes across all destinations.
            for namespace, identity in [('reference', [proof['source_namespace'], proof['source_record_id']]), ('receipt', hashed)]:
                claim_id = digest([owner, 'operational-driver-review-claim', namespace, identity])
                if await scoped[EVENTS].find_one({'user_id': owner, '_id': claim_id}):
                    fail('driver_payment_reference_already_consumed')
                if await scoped.mz2_shipping_native_events_v2.find_one({'user_id': owner, '$or': [
                        {'receipt_hash': hashed}, {'destination.source_namespace': proof['source_namespace'],
                         'destination.source_record_id': proof['source_record_id']}]}):
                    fail('operational_driver_native_effect_already_exists')
                await scoped[EVENTS].insert_one({'_id': claim_id, 'user_id': owner, 'kind': 'claim', 'event_id': key})
            fact = {'id': key, 'financial_source': 'operational', 'review_id': review['id'], 'review_revision': revision,
                'assignment_id': assignment_id, 'order_id': event['order_id'], 'order_number': event['order_number'],
                'driver_id': event['party_id'], 'source_collection_id': collection['id'], 'payment_method': method,
                'amount': value, 'currency': 'SAR', 'destination_id': target, 'destination_type': dest_type,
                'destination_name': name, 'receipt_reference': review['receipt_reference'], 'receipt_hash': hashed,
                'collection_at': collection['collected_at'], 'delivered_at': event['delivered_at'],
                'approved_at': stamp, 'approved_by': actor, 'movement_id': None, 'obligation_id': None,
                'bank_movement_id': movement['id'] if movement else None,
                'bank_movement_date': movement['movement_date'] if movement else None,
                'transaction_reference': payload.settlement_reference,
                **{k: proof[k] for k in ('source_namespace', 'source_record_id', 'source_revision')}}
            if method == 'bank_transfer':
                fact['movement_id'] = 'driver-review:' + key
                item = {'id': fact['movement_id'], 'request_id': fact['movement_id'],
                    'party_type': 'bank', 'party_id': target, 'bank_kind': 'bank', 'bank_id': target,
                    'bank_name': name, 'name': name, 'currency': 'SAR', 'amount': value, 'direction': 'incoming',
                    'kind': 'collection', 'allocations': [], 'actual_fee_amount': '0.00', 'actor_id': actor,
                    'occurred_at': stamp, 'source': 'operational_driver_review', 'order_id': event['order_id'],
                    'order_number': event['order_number'], 'reference': payload.settlement_reference,
                    'receipt_hash': hashed, 'driver_review_event_id': key, 'business_date': movement['movement_date']}
                state['movements'].append(item)
                changed = await scoped.mz2_daily_movements.update_one({'_id': movement['_id'], 'user_id': owner, 'status': 'unclassified'},
                    {'$set': {'status': 'operational_consumed', 'operational_event_id': key, 'operational_consumed_at': stamp}})
                if changed.matched_count != 1:
                    fail('shipping_movement_changed')
            else:
                fact['obligation_id'] = 'pos:driver-review:' + key
                state.setdefault('engine', {}).setdefault('obligations', {})[fact['obligation_id']] = {
                    'id': fact['obligation_id'], 'kind': 'driver_payment_review', 'party_type': 'pos_receivable',
                    'party_id': target, 'name': name, 'currency': 'SAR', 'direction': 'receivable',
                    'expected': '0.00', 'confirmed': value, 'business_date': instant(stamp).astimezone(RIYADH).date().isoformat(),
                    'order_id': event['order_id'], 'order_number': event['order_number'], 'driver_review_event_id': key}
            fact['seal'] = digest(fact)
            state.setdefault(FACTS, {})[key] = fact
        confirmed = payload.decision == 'approved'
        meta = {'financial_source': 'operational', 'operational_event_id': key, 'operational_review_revision': revision,
                'operational_approval_status': payload.decision, 'reviewed_at': stamp, 'reviewed_by': actor}
        for name, row, extra in [
            (REVIEWS, review, {'status': payload.decision, 'review_note': payload.note}),
            (COLLECTIONS, collection, {'review_status': payload.decision, 'payment_confirmed': confirmed,
                                      'payment_status': 'paid' if confirmed else 'payment_evidence_rejected'}),
            ('store_delivery_assignments', assignment, {'payment_review_status': payload.decision,
                'payment_confirmed': confirmed, 'payment_status': 'paid' if confirmed else 'payment_evidence_rejected',
                'payment_reviewed_at': stamp, 'payment_reviewed_by': actor}),
        ]:
            query = {'_id': row['_id'], 'user_id': owner}
            if name == REVIEWS:
                query['status'] = 'pending'
            changed = await scoped[name].update_one(query, {'$set': {**meta, **extra}})
            if changed.matched_count != 1:
                fail('driver_payment_review_changed')
        if receipt:
            await scoped[RECEIPTS].update_one({'_id': receipt['_id'], 'user_id': owner}, {'$set': {**meta, 'review_status': payload.decision}})
        await scoped.unified_orders.update_one({'_id': order['_id'], 'user_id': owner}, {'$set': {
            'store_delivery_payment_review_status': payload.decision, 'store_delivery_payment_confirmed': confirmed,
            'store_delivery_payment_status': 'paid' if confirmed else 'payment_evidence_rejected',
            'store_delivery_operational_event_id': key}})
        result = {'state': 'operational_recorded' if confirmed else 'rejected', 'decision': payload.decision,
                  'financial_source': 'operational', 'event_id': key, 'review_id': review['id'],
                  'movement_id': fact['movement_id'] if fact else None, 'obligation_id': fact['obligation_id'] if fact else None}
        decision = {'_id': key, 'user_id': owner, 'kind': 'decision', 'request_hash': request_hash,
                    'result': result, 'fact': fact, 'decision': payload.decision, 'actor_id': actor, 'at': stamp}
        decision['seal'] = digest({k: v for k, v in decision.items() if k != '_id'})
        await scoped[EVENTS].insert_one(decision)
        audit(state, actor, 'driver_payment_review_' + payload.decision, None, fact or result,
              payload.note or 'مطابقة تحصيل المندوب في النظام التشغيلي', 'operational_driver_review', stamp)
        state['revision'] = original_revision + 1
        changed = await scoped[STATES].replace_one({'_id': owner, 'owner_id': owner, 'revision': original_revision}, state)
        if changed.matched_count != 1:
            fail('operational_concurrent_change')
        return result
    return await transact(db, owner, commit, lambda scoped: authorize(scoped, owner, actor))
