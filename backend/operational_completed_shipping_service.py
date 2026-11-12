"""Owner review for earlier completion fees; approval is not a money writer.

Each preview is a bounded snapshot of one order and the saved openings/payouts.
The owner CAS records only the exact authorization. Reconciliation revalidates
that evidence before booking the deferred base or the approved prepaid pair.
"""
import asyncio

from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern

from operational_balance_service import active_gate
from operational_balance_store import audit, check_authorization, digest, fail, mutate, now, read, remember, replay
from operational_completed_shipping_admission import APPROVALS, MARKER, approval_record, require_admission, review
from operational_completed_shipping_sources import public_candidate, review_candidate
from operational_supplier_invoice_preview import SnapshotReads
from operational_financial_activity_runtime import prepare_in_thread

MESSAGES = {
    'operational_shipping_completion_existing_fee': 'توجد أجرة أساسية مثبتة لهذا الطلب؛ لا تُحتسب مرة أخرى.',
    'operational_shipping_completion_existing_payout': 'يوجد سداد مرتبط بأجرة هذا الطلب؛ طابقه قبل أي اعتماد.',
    'operational_shipping_completion_unallocated_payout': 'توجد دفعة غير مخصصة بالكامل للشركة؛ طابق تخصيصها أولًا.',
    'operational_shipping_completion_candidate_unavailable': 'لا توجد أجرة تم تنفيذها ومؤهلة لهذا الطلب بالدليل الحالي.',
    'operational_shipping_completion_order_missing': 'لم يوجد هذا الطلب ضمن الطلبات المعتمدة لهذا المتجر.',
    'canonical_order_identity_ambiguous': 'هوية الطلب متكررة أو غير متطابقة؛ طابق المصدر أولًا.',
    'canonical_salla_refresh_required': 'مصدر الطلب يحتاج تحديثًا موثقًا من سلة قبل المراجعة.',
    'operational_shipping_completion_order_identity_conflict': 'رقم الطلب لا يطابق هويته في المصدر المعتمد.',
    'operational_shipping_completion_time_invalid': 'وقت الحدث خارج الفترة التشغيلية المعتمدة.',
    'shipping_setup_ambiguous': 'يوجد أكثر من إعداد شحن معتمد؛ طابق الإعدادات أولًا.',
    'operational_courier_tariff_authority_conflict': 'تعرفة شركة الشحن متعارضة؛ طابق التعرفة المعتمدة أولًا.',
}


async def _owner(db, owner, actor):
    user = await db.users.find_one({'id': actor}, {'_id': 0, 'role': 1, 'disabled': 1, 'is_active': 1, 'deleted_at': 1})
    if (actor != owner or not user or user.get('role') != 'owner' or user.get('disabled')
            or user.get('deleted_at') or user.get('is_active') is False):
        fail('completed_shipping_owner_required', 'مراجعة أجور تم التنفيذ متاحة للمالك فقط', 403)
    await check_authorization()


def _problem(value):
    if value.startswith(('operational_', 'canonical_', 'shipping_')):
        return {'code': value, 'message': MESSAGES.get(value, 'تحتاج بيانات تنفيذ الطلب وتعرفة الشحن إلى مطابقة.')}
    return {'code': 'operational_shipping_completion_native_overlap', 'message': value}


async def _snapshot(db, owner, actor, number, stamp):
    async with await db.client.start_session() as session:
        async with session.start_transaction(read_concern=ReadConcern('snapshot')):
            scoped = SnapshotReads(db, owner, session)
            state = await read(scoped, owner)
            await active_gate(scoped, owner, state)
            event, blocks, applied = await review_candidate(scoped, owner, state, number, stamp)
            check = await prepare_in_thread(review, state, event) if event and not applied else None
            saved = state.get(APPROVALS, {}).get(digest(number)) or {}
            same_approval = False
            if event and check and check['requires_review'] and saved:
                try:
                    await prepare_in_thread(require_admission, state, event)
                    same_approval = True
                except (ValueError, KeyError, TypeError, ArithmeticError):
                    pass
            issues = list(dict.fromkeys([*blocks, *((check or {}).get('issues') or [])]))
            candidate = public_candidate(event)
            prepaid = candidate and candidate['payment_mode'] == 'prepaid'
            result = {'order_number': number, 'candidate': candidate,
                'openings': (check or {}).get('openings', []), 'payouts': (check or {}).get('payouts', []),
                'issues': [_problem(value) for value in issues],
                'can_approve': bool(event and check and check['requires_review'] and not issues and not same_approval),
                'already_approved': bool(applied or same_approval),
                'approval_state': 'applied' if applied else 'pending_reconciliation' if same_approval else
                                  'requires_review' if (check or {}).get('requires_review') else 'not_required',
                'period_start': state['started_at'],
                'activation_at': (state.get('engine', {}).get(MARKER) or {}).get('activated_at'),
                'effects': {'expense_increase': event['amount'], 'currency': event['currency'],
                            'courier_payable_increase': '0.00' if prepaid else event['amount'],
                            'cash_outflow': event['amount'] if prepaid else '0.00',
                            'opening_change': '0.00'} if event else None,
                'session_scope': digest([owner, actor])}
            result['preview_hash'] = digest([state['revision'], (check or {}).get('preview_hash'), result])
            return state, result, event, check


async def _preview(db, owner, actor, number, stamp):
    try:
        # This deadline encloses read-only snapshot work, never the owner CAS.
        return await asyncio.wait_for(_snapshot(db, owner, actor, number, stamp), timeout=20)
    except asyncio.TimeoutError:
        fail('completed_shipping_snapshot_timeout', 'استغرقت قراءة أدلة هذا الطلب وقتًا طويلًا؛ أعد المعاينة', 503)
    except PyMongoError:
        fail('completed_shipping_snapshot_unavailable', 'تعذرت قراءة أدلة الطلب من لقطة متسقة', 503)
    except (ValueError, TypeError, KeyError, ArithmeticError) as error:
        code = str(error)
        if not code.startswith(('operational_', 'canonical_', 'shipping_')) or not all(c.islower() or c == '_' for c in code):
            code = 'operational_shipping_completion_source_invalid'
        fail(code, MESSAGES.get(code, 'طابق هوية الشركة ووقت تم التنفيذ والتعرفة المعتمدة والسداد السابق.'))


async def preview(db, owner, actor, number, *, clock=None):
    await _owner(db, owner, actor)
    state, result, _, _ = await _preview(db, owner, actor, number, clock or now())
    await active_gate(db, owner, state)
    await _owner(db, owner, actor)
    return result


async def approve(db, owner, actor, payload, *, clock=None):
    await _owner(db, owner, actor)
    if payload.get('expected_session_scope') != digest([owner, actor]):
        fail('operational_actor_scope_changed', 'تغير الحساب؛ أعد المعاينة', 403)
    if payload.get('confirm_opening_and_payouts') is not True or len(str(payload.get('evidence_ref') or '').strip()) < 3:
        fail('completed_shipping_confirmation_required', 'يلزم مرجع وتأكيد مراجعة الافتتاح والسداد السابق', 422)
    number, stamp = payload['order_number'], clock or now()
    action = 'approve_completed_shipping_fee:' + actor

    async def apply(state):
        await _owner(db, owner, actor)
        old = replay(state, action, payload)
        if old is not None:
            return old
        await active_gate(db, owner, state)
        snapshot, result, event, check = await _preview(db, owner, actor, number, stamp)
        if (not result['can_approve'] or digest(snapshot) != digest(state)
                or payload['expected_preview_hash'] != result['preview_hash']):
            fail('completed_shipping_preview_stale', 'تغيرت بيانات المطابقة؛ أعد معاينة الأثر قبل الاعتماد')
        record = approval_record(state, event, actor=actor, approved_at=stamp,
            evidence_ref=payload['evidence_ref'], expected_preview_hash=check['preview_hash'])
        await _owner(db, owner, actor)
        await active_gate(db, owner, state)
        previous = state.setdefault(APPROVALS, {}).get(digest(number))
        state[APPROVALS][digest(number)] = record
        audit(state, actor, 'approve_completed_shipping_fee', previous, record, payload['evidence_ref'],
              'completed_shipping_fee_review', stamp)
        response = {'ok': True, 'order_number': number, 'pending_reconciliation': True,
                    'session_scope': digest([owner, actor])}
        remember(state, action, payload, response)
        return response

    result = await mutate(db, owner, apply)
    await _owner(db, owner, actor)
    return result
