"""Owner-reviewed statement evidence, bound to already recorded provider effects.

Uploads preserve immutable source rows. Only exact, independently checked links
enter the operational aggregate. This module never calls a payment, refund,
bank, legacy import, accounting journal or broad refresh writer.
"""
from collections import defaultdict
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import PurePath
import re

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from accounting_source_files import preserve_original
from operational_balance_service import active_gate, accounting_inactive
from operational_balance_store import (AUTHORIZATION_GUARD, STATES, audit, check_authorization, digest, fail,
    initial, mutate, now, read, remember, replay)
from operational_financial_activity_runtime import checked_rows, join_task, prepare_in_thread, preparation_checkpoint
from operational_provider_statement_intake import MAX_FILE_BYTES, MAX_ROWS, normalize_statement

STATEMENTS = 'operational_provider_statements_v1'
RECONCILIATION = 'provider_statement_reconciliation'
PROVIDERS = frozenset({'tabby', 'tamara', 'salla'})
COUNTS = ('matched', 'missing', 'different', 'blocked', 'confirmed')
KINDS = {'sale': 'capture', 'refund': 'refund', 'settlement_fee': 'payout_fee'}
RIYADH = timezone(timedelta(hours=3))
CENT = Decimal('.01')
MAX_MISSING_PREVIEWS = 20
SAVED_FILE_FIELDS = {'_id': 0, **{key: 1 for key in ('id', 'user_id', 'provider', 'filename',
    'uploaded_at', 'file_hash', 'file_size', 'header.statement_id', 'header.invoice_number')}}
ORIGINAL_METADATA_FIELDS = {'_id': 1, 'user_id': 1, 'file_id': 1, 'sha256': 1, 'size': 1}

MESSAGES = {
    'statement_row_incomplete': 'بيانات السطر أو العملة أو العمولة غير مكتملة؛ راجع أصل الكشف.',
    'statement_non_order_event': 'هذا رسم تحويل أو خصم مستقل؛ لا يثبت دفعة عميل أو استرجاعًا ولا يُحوّل للبنك تلقائيًا.',
    'statement_order_missing': 'الطلب غير موجود في لقطة الأرصدة المحفوظة؛ راجع الطلب وإثبات دفعه ثم حدّث الأرصدة من الصفحة الرئيسية.',
    'statement_order_ambiguous': 'رقم الطلب مرتبط بأكثر من هوية في اللقطة؛ يلزم تصحيح الربط قبل المطابقة.',
    'statement_provider_or_currency_conflict': 'جهة الدفع أو العملة الأصلية لا تطابق الطلب المحفوظ.',
    'statement_effect_missing': 'لا يوجد أثر دفع أو استرجاع محفوظ يمكن ربطه بهذا السطر؛ سجل العملية الموثقة في مسارها ثم أعد المراجعة.',
    'statement_amount_difference': 'مبلغ الكشف يختلف عن المبلغ المسجل؛ راجع العملية الأصلية. اعتماد العمولة لا يغيّر أصل الدفع أو الاسترجاع.',
    'statement_identity_review': 'تحتاج هوية العملية أو تاريخها إلى مراجعة؛ اختر السجل الموجود الذي يخص هذا السطر قبل التأكيد.',
    'statement_duplicate_effect': 'أكثر من سطر يغطي العملية المالية نفسها؛ يلزم كشف يميز العمليات دون تكرار.',
    'statement_event_conflict': 'يوجد دليل عمولة معتمد لهذه العملية بمحتوى مختلف؛ راجع الكشفين قبل أي تصحيح.',
    'statement_existing_proof_changed': 'تغير السجل الذي استندت إليه المطابقة السابقة؛ أعد مراجعة العملية الأصلية.',
    'statement_fee_coverage_incomplete': 'الأثر المالي محفوظ، لكن ربط العمولة بأصل التحصيل والاسترجاع غير مكتمل؛ يلزم استكمال المراجعة دون قيد إضافي.',
    'statement_matched': 'طابق الكشف العملية المحفوظة؛ لن تُنشأ دفعة أو عملية استرجاع أخرى.',
    'statement_confirmed': 'سبق اعتماد هذه العملية؛ لن تُكرر العمولة أو الحركة المالية.',
    'statement_missing_refund_ready': 'الكشف يثبت استرجاعًا منفذًا غير محتسب. بعد تأكيد عدم تسجيله سابقًا سيخفض أصل ذمة المنصة فقط، دون تحويل بنكي أو إرجاع مخزون.',
    'statement_missing_capture_ready': 'إثبات التحصيل الأصلي محفوظ ولم يحتسب في الذمة. بعد التأكيد سيضاف أصل التحصيل للمنصة دون تسجيل إيداع بنكي.',
    'statement_native_duplicate_review': 'يوجد سجل دفع أو استرجاع مستقل للطلب؛ يلزم ربطه ومراجعته قبل تسجيل أي أثر آخر.',
    'statement_missing_event_guard': 'لا تكفي أدلة التحصيل الأصلي أو سياسة الاسترجاع أو التاريخ لإضافة أثر آمن؛ استكمل المراجعة في مسار العملية الأصلي.',
    'statement_missing_batch_limit': 'يعرض الكشف كاملًا، وفُحصت 20 عملية جديدة في هذه القراءة. اعتمد المؤهل منها ثم أعد قراءة الكشف لفحص الدفعة التالية.',
    'statement_native_alias_ready': 'ربط مرجع الاسترجاع الوارد بالاسترجاع المحتسب دون خصم إضافي. يلزم اختيار الاسترجاع المعتمد وتأكيد تطابق الهوية والتاريخ.',
    'statement_native_alias_review': 'تغير مرجع الاسترجاع أو بيانات تنفيذه؛ حُفظ المبلغ المعتمد ويحتاج هذا الطلب وحده إلى مطابقة الدليل الكامل من المصدر.',
}


def _amount(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        result = Decimal(str(value))
        return result if result.is_finite() and abs(result) <= Decimal('999999999999.99') and result == result.quantize(CENT) else None
    except (ValueError, InvalidOperation, TypeError):
        return None


def _day(value):
    try:
        if not isinstance(value, str):
            return None
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if len(value) == 10:
            return stamp.date().isoformat()
        return stamp.astimezone(RIYADH).date().isoformat() if stamp.tzinfo else None
    except ValueError:
        return None


def statement_document(owner, actor, filename, normalized, stamp):
    """Stable identity survives reordered/re-encoded files with the same evidence."""
    reference = normalized.get('statement_reference')
    identity = ['native', normalized.get('provider'), normalized.get('merchant_account'), reference]
    if not reference:
        identity = ['content', normalized.get('provider'), normalized.get('fingerprint')]
    statement_id = digest([owner, identity])
    filename = PurePath(str(filename).replace('\\', '/')).name[:240]
    metadata = {'id': statement_id, 'provider': normalized['provider'],
        'statement_reference': normalized.get('statement_reference'), 'merchant_account': normalized.get('merchant_account'),
        'filename': filename, 'file_hash': normalized['file_hash'], 'native_currency': normalized.get('currency'),
        'row_count': len(normalized['rows']), 'uploaded_at': stamp, 'totals': deepcopy(normalized.get('totals', {}))}
    return {'_id': statement_id, 'id': statement_id, 'owner_id': owner,
        'statement_key': digest(identity), 'filename': filename, 'metadata': metadata, 'metadata_hash': digest(metadata),
        'uploaded_by': actor, 'uploaded_at': stamp, 'statement': deepcopy(normalized),
        'content_hash': digest(normalized),
        'original_file_id': 'operational-statement:' + normalized['file_hash']}


def _validate_document(document, owner):
    data = document.get('statement') if isinstance(document, dict) else None
    if (not data or document.get('owner_id') != owner or document.get('id') != document.get('_id')
            or data.get('provider') not in PROVIDERS or document.get('content_hash') != digest(data)
            or not isinstance(data.get('rows'), list) or not 0 < len(data['rows']) <= MAX_ROWS
            or len({r.get('id') for r in data['rows'] if isinstance(r, dict)}) != len(data['rows'])):
        fail('provider_statement_evidence_invalid', 'تعذر التحقق من أصل الكشف المحفوظ؛ لم تتغير الأرصدة', 409)
    return data


def _frozen_candidates(state):
    """Index the current saved cohort once; never query live orders for a GET."""
    engine = state.get('engine', {})
    orders = engine.get('provider_fee_inputs', {}).get('orders', {})
    by_number, candidates = defaultdict(list), defaultdict(list)
    facts = engine.get('facts', {})
    for oid, order in checked_rows(orders.items()):
        if isinstance(order, dict) and order.get('order_number') is not None:
            by_number[str(order['order_number'])].append(str(oid))
    for key, fact in checked_rows(facts.items()):
        if not isinstance(fact, dict):
            continue
        oid = str(fact.get('order_id') or '')
        order = orders.get(oid) or {}
        payment = order.get('payment') or {}
        kind, provider, reference, value, event_day = None, None, None, None, None
        if key.startswith('capture:'):
            kind, provider, reference, value = 'capture', fact.get('provider'), key[len('capture:'):], fact.get('amount')
            capture = next((c for c in payment.get('captures', []) if str(c.get('id')) == reference), {})
            event_day = _day(capture.get('captured_at'))
        elif key.startswith('refund:') and len(key.split(':', 2)) == 3:
            _, provider, reference = key.split(':', 2)
            kind, value = 'refund', fact.get('amount')
            refund = next((r for r in payment.get('refunds', []) if str(r.get('id')) == reference), {})
            event_day = _day(refund.get('refunded_at'))
        if kind and provider in PROVIDERS and oid in orders and _amount(value) is not None:
            candidates[oid, provider, kind].append({'kind': 'fact', 'id': key,
                'fingerprint': digest(fact), 'amount': value, 'reference': reference,
                'event_date': event_day, 'financial_effect': 'already_recorded',
                'effect_id': key, 'currency': order.get('currency')})
    # A cumulative page fact is a fallback only when individual capture facts
    # are absent. Counting both representations would duplicate one payment.
    for key, fact in checked_rows(facts.items()):
        if not key.startswith('provider_order_balance:') or not isinstance(fact, dict):
            continue
        oid, provider = str(fact.get('order_id') or ''), fact.get('provider')
        if (oid not in orders or provider not in PROVIDERS or candidates[oid, provider, 'capture']
                or _amount(fact.get('paid')) is None):
            continue
        candidates[oid, provider, 'capture'].append({'kind': 'fact', 'id': key,
            'fingerprint': digest(fact), 'amount': fact['paid'],
            'reference': fact.get('transaction_reference'), 'event_date': None,
            'financial_effect': 'already_recorded', 'effect_id': key, 'currency': fact.get('currency')})
    for case in checked_rows(state.get('customer_returns', [])):
        if not isinstance(case, dict):
            continue
        oid = str(case.get('order_id') or '')
        documented = case.get('status') == 'salla_refund_recorded' and case.get('refund_source_type') is None
        provider = ((orders.get(oid) or {}).get('payment') or {}).get('provider') if documented else case.get('refund_source_id')
        if not documented and case.get('refund_source_type') != 'provider':
            continue
        if oid not in orders or provider not in PROVIDERS or not case.get('id'):
            continue
        ref = case.get('refund_reference')
        effect = 'refund:' + provider + ':' + str(ref)
        existing = candidates[oid, provider, 'refund']
        # Bind the employee record itself, retaining its already-booked fact as
        # the economic identity. A case and its fact are one effect, not two.
        aliases = [c for c in existing if ref and c['reference'] == ref]
        if aliases and all(_amount(c['amount']) == _amount(case.get('amount')) for c in aliases):
            existing[:] = [c for c in existing if c not in aliases]
        status = case.get('status')
        existing.append({'kind': 'customer_return', 'id': case['id'], 'fingerprint': digest(case),
            'amount': case.get('amount'), 'reference': ref, 'event_date': _day(case.get('refunded_at')),
            'financial_effect': 'already_recorded' if status in {'refunded', 'salla_refund_confirmed'} else 'evidence_only',
            'effect_id': effect if ref else 'customer_return:' + case['id'], 'currency': case.get('currency')})
    return orders, by_number, candidates


async def _external_evidence(db, owner, document, state):
    """Bounded owner/order reads for an explicit missing-event decision only.

    Old provider/accounting observations are duplicate guards, never imported
    balances. Unknown or oversized source results fail closed.
    """
    data = document['statement']
    numbers = sorted({str(row.get('order_number')) for row in data['rows'] if row.get('order_number') is not None})
    provider_ids = sorted({str(row.get('provider_order_id')) for row in data['rows'] if row.get('provider_order_id')})
    if not numbers:
        return {'available': True, 'by_number': {}, 'fingerprint': digest([])}
    if len(numbers) > 1000:
        return {'available': False, 'by_number': {}, 'fingerprint': digest(['order_read_limit'])}
    fields = {key: 1 for key in ('user_id', 'id', 'provider', 'provider_id', 'provider_payment_id',
        'provider_refund_id', 'order_id', 'order_number', 'order_reference_id', 'amount', 'currency', 'status',
        'txn_group_id', 'bank_reference', 'execution_channel', 'refunded_at', 'captured_amount', 'refunded_amount')}
    fields['_id'] = 0

    async def load(collection, query):
        from operational_read_runtime import load_collections, read_source_cursor
        projection = fields
        if collection == 'unified_orders':
            projection = {'_id': 0, 'user_id': 1, 'order_number': 1,
                **{'raw_by_source.salla_direct.' + key: 1 for key in (
                    'id', 'reference_id', 'order_number', 'currency', 'date', 'created_at', 'updated_at',
                    'status', 'payment', 'payment_methods', 'payment_actions.refund_action',
                    'refunded_amount', 'is_pending_payment')}}
        async def reader(_db, _owner, _name):
            return await read_source_cursor(_db[_name].find(query, projection), 2001)
        result = (await load_collections(db, owner, [collection], reader=reader, per_read_timeout=10))[collection]
        if len(result) > 2000 or any(row.get('user_id') != owner for row in result):
            fail('provider_statement_duplicate_source_incomplete', 'تعذر التحقق من اكتمال سجلات الدفع والاسترجاع؛ لم تتغير الأرصدة')
        return result

    order_query = [{'order_reference_id': {'$in': numbers}}, {'order_number': {'$in': numbers}}]
    if provider_ids:
        order_query.append({'provider_id': {'$in': provider_ids}})
    try:
        transactions = await load('payment_transactions', {'user_id': owner, 'provider': data['provider'], '$or': order_query})
        transaction_orders = defaultdict(set)
        for row in transactions:
            number = str(row.get('order_reference_id') or row.get('order_number') or '')
            if row.get('provider_id') and number:
                transaction_orders[str(row['provider_id'])].add(number)
        ids = sorted(set(provider_ids) | set(transaction_orders))
        refs = sorted({str(row['event_reference']) for row in data['rows'] if row.get('event_reference')})
        refund_query = [{'order_reference_id': {'$in': numbers}}, {'order_number': {'$in': numbers}}]
        if ids:
            refund_query.append({'provider_payment_id': {'$in': ids}})
        if refs:
            refund_query.append({'provider_refund_id': {'$in': refs}})
        tasks = [asyncio.create_task(task) for task in (
            load('payment_refunds', {'user_id': owner, 'provider': data['provider'], '$or': refund_query}),
            load('mz2_customer_refund_payments', {'user_id': owner,
                 '$or': [{'order_number': {'$in': numbers}}, {'provider_refund_id': {'$in': refs}}]}),
            load('unified_orders', {'user_id': owner, 'order_number': {'$in': numbers}}))]
        try:
            refunds, accounting, canonical = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await join_task(asyncio.gather(*tasks, return_exceptions=True))
    except ValueError as exc:
        from operational_read_runtime import SourceReadError
        if not isinstance(exc, SourceReadError):
            raise
        fail('provider_statement_duplicate_source_unavailable', 'تعذر إكمال قراءة سجلات الدفع والاسترجاع؛ أعد المحاولة دون قيد إضافي', 503)
    grouped, records = defaultdict(list), []
    for collection, values in (('payment_transactions', transactions), ('payment_refunds', refunds),
                               ('mz2_customer_refund_payments', accounting), ('unified_orders', canonical)):
        for row in values:
            record = {'collection': collection, 'record': row}
            records.append(record)
            keys = {str(row.get('order_reference_id') or row.get('order_number') or '')}
            keys.update(transaction_orders.get(str(row.get('provider_payment_id') or ''), set()))
            if not keys - {''}:
                # A selected source row without an order binding cannot prove
                # absence of a prior effect for any requested order.
                keys = set(numbers)
            for key in keys - {''}:
                grouped[key].append(record)
    records.sort(key=digest)
    return {'available': True, 'by_number': dict(grouped), 'fingerprint': digest(records)}


def _canonical_capture_matches(state, oid, source, native):
    """Absence of old BNPL rows is not absence of Salla refund observations."""
    canonical = [row['record'] for row in native if row['collection'] == 'unified_orders']
    if len(canonical) != 1:
        return False
    wrapper = canonical[0]
    raw = (wrapper.get('raw_by_source') or {}).get('salla_direct')
    order = state['engine']['provider_fee_inputs']['orders'][oid]
    saved = order.get('payment') or {}
    if (not isinstance(raw, dict) or str(raw.get('id')) != oid
            or str(raw.get('reference_id') or raw.get('order_number') or '') != str(source['order_number'])
            or raw.get('currency') != order.get('currency')
            or (isinstance(raw.get('payment'), dict) and raw['payment'].get('refunds'))):
        return False
    from operational_payment_evidence import normalize_payment
    payment, issues = normalize_payment(raw, order['currency'], saved.get('gross'),
        order['created_at'], state['engine']['as_of'])
    if issues or not payment or payment.get('provider') != source['provider'] or payment.get('refunds'):
        return False
    expected = [(str(c.get('id')), _amount(c.get('amount')), c.get('captured_at')) for c in saved.get('captures', [])]
    observed = [(str(c.get('id')), _amount(c.get('amount')), c.get('captured_at')) for c in payment.get('captures', [])]
    return expected == observed and bool(expected)


def _missing_plan(state, document, source, oid, actor, external, quota=None):
    """Ask the existing pure engine to prove one narrowly scoped new effect."""
    if not external or not external.get('available'):
        return None, 'statement_missing_event_guard'
    kind = KINDS[source['event_type']]
    native = external.get('by_number', {}).get(str(source['order_number']), [])
    if any(row['collection'] not in {'payment_transactions', 'unified_orders'}
           or kind == 'capture' and row['collection'] == 'payment_transactions' for row in native):
        return None, 'statement_native_duplicate_review'
    if not _canonical_capture_matches(state, oid, source, native):
        return None, 'statement_native_duplicate_review'
    if quota is not None:
        if quota['remaining'] <= 0:
            return None, 'statement_missing_batch_limit'
        quota['remaining'] -= 1
    engine = state.get('engine', {})
    order = deepcopy(engine.get('provider_fee_inputs', {}).get('orders', {}).get(oid) or {})
    payment = order.get('payment') or {}
    statement_plan = source.get('plan_id')
    if statement_plan not in (None, ''):
        if payment.get('plan_id') not in (None, '', statement_plan):
            return None, 'statement_missing_event_guard'
        policies = engine.get('provider_fee_inputs', {}).get('policies', [])
        if any(policy.get('status') == 'active' and policy.get('provider') == source['provider']
               and policy.get('currency') == source['native_currency'] and policy.get('plan_id') == statement_plan
               for policy in policies):
            payment['plan_id'] = statement_plan
            payment['plan_evidence'] = {'source': 'operational_provider_statement',
                'statement_id': document['id'], 'file_hash': document['statement']['file_hash'],
                'row_fingerprint': source['row_fingerprint']}
    if (payment.get('provider') != source['provider'] or payment.get('split')
            or order.get('currency') != source['native_currency'] or not payment.get('captures')
            or len(payment['captures']) != 1 or not state.get('started_at')):
        return None, 'statement_missing_event_guard'
    capture = payment['captures'][0]
    try:
        from operational_balance_engine import instant, reconcile
        start, as_of = instant(state['started_at']), instant(engine['as_of'])
        created = instant(order['created_at'])
        captured_at = instant(capture['captured_at'])
        value, gross = _amount(source['principal_amount']), _amount(capture['amount'])
        if value is None or gross is None or not start < created <= captured_at <= as_of:
            return None, 'statement_missing_event_guard'
        claim_id = 'provider:' + oid
        claim = engine.get('obligations', {}).get(claim_id)
        old_confirmed = _amount((claim or {}).get('confirmed', '0'))
        if old_confirmed is None:
            return None, 'statement_missing_event_guard'
        # Any prior cash/refund context for this order must be explicitly linked,
        # not inferred as absent from a different reference or amount.
        if any(m.get('order_id') == oid or str(m.get('order_number') or '') == str(source['order_number'])
               or any(a.get('obligation_id') in {claim_id, 'credit:' + claim_id} for a in m.get('allocations', []))
               for m in state.get('movements', [])):
            return None, 'statement_native_duplicate_review'
        cases = [case for case in state.get('customer_returns', []) if str(case.get('order_id')) == oid]
        pending = [case for case in cases if case.get('status') == 'salla_refund_recorded'
                   and case.get('refund_source_type') is None and _amount(case.get('amount')) == value]
        employee_case = pending[0] if len(cases) == len(pending) == 1 and kind == 'refund' else None
        if cases and employee_case is None:
            return None, 'statement_native_duplicate_review'
        if kind == 'refund':
            fact_key = 'capture:' + str(capture['id'])
            if engine.get('facts', {}).get(fact_key) != {'order_id': oid, 'provider': source['provider'], 'amount': format(gross, '.2f')}:
                return None, 'statement_missing_event_guard'
            if any(key.startswith('refund:') and row.get('order_id') == oid
                   for key, row in engine.get('facts', {}).items() if isinstance(row, dict)):
                return None, 'statement_native_duplicate_review'
            event_day = source.get('event_date')
            if not event_day or not _day(event_day):
                return None, 'statement_missing_event_guard'
            day_start = datetime.fromisoformat(event_day).replace(tzinfo=RIYADH)
            day_end = day_start + timedelta(days=1)
            refund_at = day_start
            precision = 'day'
            if employee_case is not None:
                recorded_at = instant(employee_case.get('refunded_at'))
                if not day_start <= recorded_at < day_end:
                    return None, 'statement_missing_event_guard'
                refund_at, precision = recorded_at, 'instant'
            # A day-only statement cannot prove that a same-day refund followed
            # its capture or preceded an intraday snapshot. Keep the interval
            # explicit; its start is not an invented execution timestamp.
            if not captured_at < day_start or day_end > as_of or value > gross or value > old_confirmed:
                return None, 'statement_missing_event_guard'
            reference = (employee_case or {}).get('refund_reference') or source.get('event_reference')
            if not reference:
                reference = 'statement:' + digest([state['owner_id'], source['provider'], oid,
                    source['native_currency'], event_day, source['principal_amount'], 'refund'])
            from operational_provider_fees import with_recorded_refund
            payment = with_recorded_refund(payment, payment, reference, source['principal_amount'], refunded_at=refund_at.isoformat())
            new_key = 'refund:' + source['provider'] + ':' + str(reference)
            fact = {'order_id': oid, 'amount': source['principal_amount']}
        else:
            if value != gross or 'capture:' + str(capture['id']) in engine.get('facts', {}) or claim is not None:
                return None, 'statement_missing_event_guard'
            if source.get('event_date') and source['event_date'] != _day(capture['captured_at']):
                return None, 'statement_missing_event_guard'
            new_key = 'capture:' + str(capture['id'])
            fact = {'order_id': oid, 'provider': source['provider'], 'amount': source['principal_amount']}
            reference, refund_at = str(capture['id']), None
        order.update(id=oid, payment=payment, items_complete=False, shipping_evidence_held=True)
        projected = reconcile(state, {'orders': [order],
            'issues': deepcopy(engine.get('issues', [])),
            'fee_policies': deepcopy(engine.get('provider_fee_inputs', {}).get('policies', []))}, engine['as_of'])
        resulting = projected.get('engine', {})
        updated = resulting.get('obligations', {}).get(claim_id)
        after = old_confirmed + value if kind == 'capture' else old_confirmed - value
        if (resulting.get('facts', {}).get(new_key) != fact or not updated
                or _amount(updated.get('confirmed')) != after or updated.get('party_id') != source['provider']
                or updated.get('currency') != source['native_currency'] or updated.get('incomplete')
                or projected.get('movements') != state.get('movements') or projected.get('openings') != state.get('openings')
                or any(resulting.get('facts', {}).get(key) != old for key, old in engine.get('facts', {}).items())):
            return None, 'statement_missing_event_guard'
        patch = {'fact_key': new_key, 'fact': fact, 'obligation_id': claim_id, 'obligation': deepcopy(updated),
            'provider_report': deepcopy(resulting.get('provider_reports', {}).get(claim_id)),
            'order_id': oid, 'payment': payment, 'kind': kind, 'employee_case_id': (employee_case or {}).get('id')}
        if kind == 'refund':
            patch['refund_record'] = {'id': digest([state['owner_id'], new_key]), 'source': 'provider_statement',
                'order_id': oid, 'order_number': source['order_number'], 'currency': source['native_currency'],
                'provider': source['provider'], 'amount': source['principal_amount'], 'reference': str(reference),
                'refunded_at': refund_at.isoformat(), 'event_date': event_day, 'date_precision': precision,
                'execution_interval': {'from': day_start.isoformat(), 'before': day_end.isoformat()},
                'fact_key': new_key, 'owner_id': state['owner_id'],
                'file_hash': document['statement']['file_hash'], 'row_fingerprint': source['row_fingerprint'],
                'payment_snapshot': {key: deepcopy(val) for key, val in payment.items() if key != 'refunds'},
                'statement_id': document['id'], 'row_id': source['id'],
                'employee_case_id': (employee_case or {}).get('id'), 'approved_by': actor}
        candidate = {'kind': 'fact', 'id': new_key, 'fingerprint': digest(fact), 'amount': source['principal_amount'],
            'financial_effect': 'already_recorded', 'effect_id': new_key,
            'event_date': source.get('event_date') or _day(capture['captured_at'])}
        event = _event(state, document, source, candidate, oid, 'owner_approved')
        if patch.get('refund_record'):
            patch['refund_record']['event_key'] = event['event_key']
        proposed = deepcopy(state)
        _apply_missing_patch(proposed, patch, document['uploaded_at'])
        from operational_provider_statement_fees import statement_event_issue
        if statement_event_issue(proposed, event) is not None:
            return None, 'statement_missing_event_guard'
        return {'event': event, 'patch': patch, 'action': 'record_missing_' + kind,
            'effect': {'kind': 'provider_' + kind, 'provider': source['provider'],
                'principal_amount': source['principal_amount'], 'native_currency': source['native_currency'],
                'remaining_before': format(old_confirmed, '.2f'), 'remaining_after': format(after, '.2f'),
                'employee_case_id': (employee_case or {}).get('id'),
                'bank_posting_allowed': False}}, None
    except (ValueError, TypeError, KeyError, InvalidOperation):
        return None, 'statement_missing_event_guard'


def _apply_missing_patch(state, patch, stamp):
    """Commit only the engine-proved provider artifacts; no partial-source state."""
    engine = state['engine']
    key = patch['fact_key']
    if key in engine.setdefault('facts', {}) and engine['facts'][key] != patch['fact']:
        fail('provider_statement_event_conflict', MESSAGES['statement_event_conflict'])
    engine['facts'][key] = deepcopy(patch['fact'])
    engine.setdefault('obligations', {})[patch['obligation_id']] = deepcopy(patch['obligation'])
    reports = engine.setdefault('provider_reports', {})
    if patch['provider_report'] is None:
        reports.pop(patch['obligation_id'], None)
    else:
        reports[patch['obligation_id']] = deepcopy(patch['provider_report'])
    engine['provider_fee_inputs']['orders'][patch['order_id']]['payment'] = deepcopy(patch['payment'])
    if patch.get('refund_record'):
        record = {**deepcopy(patch['refund_record']), 'approved_at': stamp}
        if patch.get('employee_case_id'):
            case = next(case for case in state['customer_returns'] if case['id'] == patch['employee_case_id'])
            case.update(status='salla_refund_confirmed', refund_source_type='provider',
                refund_source_id=record['provider'], refund_source_name=record['provider'],
                financial_effect='confirmed_statement_provider_refund_fact',
                statement_refund_record_id=record['id'], payment_snapshot=deepcopy(record['payment_snapshot']),
                refunded_at=record['refunded_at'], confirmed_by=record['approved_by'], confirmed_at=stamp)
            record['employee_case_fingerprint'] = digest(case)
        state.setdefault(RECONCILIATION, {}).setdefault('refund_records', {})[record['id']] = record


def fact_backed_employee_refund(state, case):
    """Distinguish a verified statement fact from the older direct debit flow."""
    if case.get('financial_effect') != 'confirmed_statement_provider_refund_fact':
        return False
    record = state.get(RECONCILIATION, {}).get('refund_records', {}).get(case.get('statement_refund_record_id'))
    if (not record or record.get('employee_case_id') != case.get('id')
            or case.get('status') != 'salla_refund_confirmed' or case.get('refund_source_type') != 'provider'
            or record.get('employee_case_fingerprint') != digest(case)
            or record.get('provider') != case.get('refund_source_id')
            or record.get('amount') != case.get('amount') or record.get('order_id') != case.get('order_id')
            or not _valid_refund_record(state, record)):
        raise ValueError('provider_statement_refund_link_invalid')
    return True


def _valid_refund_record(state, record):
    event = state.get(RECONCILIATION, {}).get('events', {}).get(record.get('event_key'))
    fact = state.get('engine', {}).get('facts', {}).get(record.get('fact_key'))
    return (record.get('source') == 'provider_statement' and record.get('owner_id') == state.get('owner_id')
        and record.get('approved_by') == state.get('owner_id')
        and record.get('id') == digest([state.get('owner_id'), record.get('fact_key')])
        and isinstance(event, dict) and event.get('kind') == 'refund' and event.get('order_id') == record.get('order_id')
        and event.get('provider') == record.get('provider') and event.get('principal_amount') == record.get('amount')
        and event.get('source', {}).get('file_hash') == record.get('file_hash')
        and event.get('source', {}).get('row_fingerprint') == record.get('row_fingerprint')
        and event.get('native_currency') == record.get('currency')
        and fact == {'order_id': record.get('order_id'), 'amount': record.get('amount')}
        and event.get('covered_event') == {'kind': 'fact', 'id': record.get('fact_key'),
            'fingerprint': digest(fact), 'amount': record.get('amount'), 'financial_effect': 'fee_only'})


def statement_refund_cases(state):
    result = []
    for record in state.get(RECONCILIATION, {}).get('refund_records', {}).values():
        if record.get('employee_case_id'):
            continue
        if not _valid_refund_record(state, record):
            raise ValueError('provider_statement_refund_link_invalid')
        result.append({'id': record['id'], 'order_id': record['order_id'], 'status': 'refunded',
            'refund_source_type': 'provider', 'refund_source_id': record['provider'],
            'refund_reference': record['reference'], 'amount': record['amount'],
            'refunded_at': record['refunded_at'], 'payment_snapshot': record['payment_snapshot']})
    return result


def statement_refund_order_index(state):
    result = defaultdict(list)
    for record in (state or {}).get(RECONCILIATION, {}).get('refund_records', {}).values():
        if isinstance(record, dict):
            result[str(record.get('order_id') or '')].append(record)
    return dict(result)


def _instant_text(value):
    if not isinstance(value, str) or len(value) <= 10:
        raise ValueError('statement_native_timestamp_missing')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('statement_native_timestamp_missing')
    return result.astimezone(timezone.utc).isoformat()


def _native_money(value, currency):
    if isinstance(value, dict):
        if value.get('currency') != currency:
            raise ValueError('statement_native_currency_conflict')
        value = value.get('amount')
    parsed = _amount(value)
    if parsed is None or parsed < 0:
        raise ValueError('statement_native_amount_invalid')
    return format(parsed, '.2f')


def _native_refund(value, *, oid, number, provider, currency):
    """A complete executed identity, never an amount-only alias guess."""
    from operational_provider_fees import EXECUTED
    if (not isinstance(value, dict) or value.get('status') not in EXECUTED
            or value.get('provider') not in (None, provider)
            or value.get('currency', currency) != currency
            or value.get('order_id') not in (None, oid)
            or value.get('order_number') not in (None, number)):
        raise ValueError('statement_native_refund_unresolved')
    if any(isinstance(value[key], bool) or not isinstance(value[key], (str, int))
           for key in ('id', 'provider_refund_id') if value.get(key) not in (None, '')):
        raise ValueError('statement_native_refund_identity_missing')
    references = {str(value[key]).strip() for key in ('id', 'provider_refund_id')
                  if value.get(key) not in (None, '')}
    if len(references) != 1:
        raise ValueError('statement_native_refund_identity_missing')
    reference = next(iter(references))
    if not reference or len(reference) > 400:
        raise ValueError('statement_native_refund_identity_missing')
    times = {_instant_text(value[key]) for key in ('refunded_at', 'executed_at') if value.get(key) is not None}
    amount = _native_money(value.get('amount'), currency)
    if len(times) != 1 or Decimal(amount) <= 0:
        raise ValueError('statement_native_refund_unresolved')
    return {'reference': reference, 'order_id': oid, 'order_number': number, 'provider': provider,
        'native_currency': currency, 'amount': amount, 'refunded_at': next(iter(times)), 'status': 'executed'}


def statement_refund_observation(state, raw, *, owner, order_id, order_number, transaction_rows=(), refund_index=None):
    """Carry a small financial proof through source normalization, with no IO.

    The normalizer may withhold payment entirely when a native refund appears.
    Only statement-owned refunds need this separate comparison. No customer,
    card, address, or full raw payload is retained.
    """
    records = (refund_index if refund_index is not None else statement_refund_order_index(state)).get(order_id, [])
    if not records:
        return None
    saved = state.get('engine', {}).get('provider_fee_inputs', {}).get('orders', {}).get(order_id, {})
    payment = saved.get('payment') or {}
    provider, currency = payment.get('provider'), saved.get('currency')
    observation = {'schema': 'provider_statement_refund_observation_v1', 'owner_id': owner,
        'order_id': order_id, 'order_number': order_number, 'provider': provider,
        'native_currency': currency, 'capture': None, 'refunds': [], 'unresolved': False}
    try:
        from operational_payment_evidence import resolve_provider
        if (owner != state.get('owner_id') or not isinstance(raw, dict) or str(raw.get('id')) != order_id
                or str(raw.get('reference_id') or raw.get('order_number') or '') != order_number
                or raw.get('currency') != currency or resolve_provider(raw, require_known=True) != provider):
            raise ValueError('statement_native_binding_invalid')
        native = raw.get('payment')
        if (not isinstance(native, dict) or native.get('status') not in {'captured', 'paid'}
                or native.get('currency') not in (None, currency) or raw.get('is_pending_payment') is True):
            raise ValueError('statement_native_capture_unresolved')
        references = {str(native[key]) for key in ('reference', 'transaction_reference') if native.get(key) not in (None, '')}
        if len(references) != 1 or len(payment.get('captures') or []) != 1:
            raise ValueError('statement_native_capture_unresolved')
        capture = {'id': next(iter(references)), 'amount': _native_money(native.get('paid_amount'), currency),
            'captured_at': _instant_text(native.get('paid_at'))}
        old = payment['captures'][0]
        if capture != {'id': str(old.get('id')), 'amount': _native_money(old.get('amount'), currency),
                       'captured_at': _instant_text(old.get('captured_at'))}:
            raise ValueError('statement_native_capture_changed')
        observation['capture'] = capture
        actions = raw.get('payment_actions') or {}
        action = actions.get('refund_action') or {}
        if not isinstance(action, dict):
            raise ValueError('statement_native_refund_unresolved')
        if action.get('paid_amount') is not None and _native_money(action['paid_amount'], currency) != capture['amount']:
            raise ValueError('statement_native_capture_changed')
        refunds = {}
        for group in (native.get('refunds', []), action.get('refunded_transactions', [])):
            if not isinstance(group, list) or len(group) > 100:
                raise ValueError('statement_native_refund_unresolved')
            for value in group:
                event = _native_refund(value, oid=order_id, number=order_number, provider=provider, currency=currency)
                prior = refunds.get(event['reference'])
                if prior is not None and prior != event:
                    raise ValueError('statement_native_refund_conflict')
                refunds[event['reference']] = event
        observation['refunds'] = [refunds[key] for key in sorted(refunds)]
        total = sum((Decimal(event['amount']) for event in refunds.values()), Decimal(0))
        for container, key in ((raw, 'refunded_amount'), (native, 'refunded_amount'), (action, 'refund_amount')):
            if container.get(key) is not None and Decimal(_native_money(container[key], currency)) != total:
                raise ValueError('statement_native_refund_total_unresolved')
        status = raw.get('status')
        status = status.get('slug') or status.get('name') if isinstance(status, dict) else status
        if status in {'cancelled', 'canceled', 'ملغي', 'ملغى'} or status in {'refunded', 'restored', 'مسترجع'} and not refunds:
            raise ValueError('statement_native_refund_unresolved')
        for row in transaction_rows:
            if (row.get('user_id') != owner or row.get('order_number') != order_number
                    or row.get('provider_order_id') != order_id or row.get('source') != 'salla_transactions'):
                raise ValueError('statement_native_transaction_binding_invalid')
            proofs = row.get('native_transaction_evidence')
            if proofs is None:
                continue  # Display-only legacy rows are not financial proof.
            if not isinstance(proofs, list) or not proofs or len(proofs) > 16:
                raise ValueError('statement_native_transaction_unresolved')
            for proof in proofs:
                if (not isinstance(proof, dict) or proof.get('provider') != provider or proof.get('currency') != currency
                        or proof.get('status') not in {'paid', 'captured'}
                        or proof.get('reference') not in (None, capture['id'])
                        or _native_money(proof.get('amount'), currency) != capture['amount']
                        or proof.get('paid_at') is not None and _instant_text(proof['paid_at']) != capture['captured_at']):
                    raise ValueError('statement_native_transaction_unresolved')
    except (ValueError, TypeError, KeyError, AttributeError):
        observation['unresolved'] = True
    observation['fingerprint'] = digest(observation)
    return observation


def _alias_matches_record(record, event):
    if any(event.get(key) != record.get(other) for key, other in (
            ('order_id', 'order_id'), ('order_number', 'order_number'), ('provider', 'provider'),
            ('native_currency', 'currency'), ('amount', 'amount'))):
        return False
    try:
        stamp = _instant_text(event.get('refunded_at'))
        if record.get('date_precision') == 'instant':
            return stamp == _instant_text(record['refunded_at'])
        interval = record['execution_interval']
        return _instant_text(interval['from']) <= stamp < _instant_text(interval['before'])
    except (ValueError, TypeError, KeyError):
        return False


def _approved_native(record, event):
    alias = record.get('native_aliases', {}).get(event.get('reference'))
    if alias is not None:
        return (alias.get('approved_by') == record.get('owner_id')
            and alias.get('fingerprint') == digest(event)
            and all(alias.get(key) == value for key, value in event.items())
            and _alias_matches_record(record, event))
    return (event.get('reference') == record.get('reference') and _alias_matches_record(record, event)
            and event.get('refunded_at') == _instant_text(record.get('refunded_at')))


def _native_alias_plan(state, record, external):
    if not external or not external.get('available'):
        return None, False
    selected = external.get('by_number', {}).get(str(record['order_number']), [])
    canonical = [entry['record'] for entry in selected if entry['collection'] == 'unified_orders']
    if len(canonical) != 1:
        return None, True
    observed = statement_refund_observation(state, (canonical[0].get('raw_by_source') or {}).get('salla_direct'),
        owner=canonical[0].get('user_id'), order_id=record['order_id'], order_number=str(record['order_number']),
        refund_index={record['order_id']: [record]})
    if not observed or observed['unresolved']:
        return None, True
    events = observed['refunds']
    for entry in selected:
        if entry['collection'] == 'mz2_customer_refund_payments':
            return None, True
        if entry['collection'] == 'payment_refunds':
            row = entry['record']
            try:
                proof = _native_refund({**row, 'id': row.get('provider_refund_id')}, oid=record['order_id'],
                    number=str(record['order_number']), provider=record['provider'], currency=record['currency'])
            except (ValueError, TypeError):
                return None, True
            if proof not in events:
                return None, True
    unbound = [event for event in events if not _approved_native(record, event)]
    if not unbound:
        missing = bool(record.get('native_aliases')) and not events
        return None, missing
    if len(events) != 1 or len(unbound) != 1 or not _alias_matches_record(record, unbound[0]):
        return None, True
    event = unbound[0]
    # A mutated already-linked identity cannot be silently re-approved as an
    # alias. Its contradiction needs source correction, retaining the old proof.
    if event['reference'] in record.get('native_aliases', {}):
        return None, True
    return {'refund_record_id': record['id'], 'event': event,
        'source': 'canonical_salla', 'source_fingerprint': observed['fingerprint']}, True


def _clear_alias_issues(issues, oid, provider):
    return [issue for issue in issues if not (
        issue.get('code') in {'provider_statement_refund_alias_review', 'provider_refund_execution_evidence_required'}
        and str(issue.get('order_id') or str(issue.get('source_id') or '').removeprefix('provider:')) == oid
        and issue.get('provider') in (None, provider))]


def _apply_native_alias(state, patch, actor, stamp):
    record = state.get(RECONCILIATION, {}).get('refund_records', {}).get(patch['refund_record_id'])
    event = patch['event']
    if not record or not _valid_refund_record(state, record) or not _alias_matches_record(record, event):
        fail('provider_statement_preview_stale', 'تغير إثبات الاسترجاع المعتمد؛ أعد القراءة')
    aliases = record.setdefault('native_aliases', {})
    if event['reference'] in aliases and aliases[event['reference']].get('fingerprint') != digest(event):
        fail('provider_statement_preview_stale', 'تغير مرجع الاسترجاع المرتبط؛ أعد القراءة')
    aliases[event['reference']] = {**deepcopy(event), 'fingerprint': digest(event),
        'source': patch['source'], 'source_fingerprint': patch['source_fingerprint'],
        'approved_by': actor, 'approved_at': stamp}
    state['engine']['issues'] = _clear_alias_issues(state['engine'].get('issues', []), record['order_id'], record['provider'])


def guard_statement_refund_sources(state, sources):
    """Hold only a changed provider payment; all other source work may proceed."""
    records_by_order = statement_refund_order_index(state)
    if not records_by_order:
        return
    by_order = defaultdict(list)
    for order in sources.get('orders', []):
        by_order[str(order.get('id'))].append(order)
    for oid, records in records_by_order.items():
        preparation_checkpoint()
        if any(not _valid_refund_record(state, record) for record in records):
            fail('provider_statement_refund_link_invalid', 'تعذر التحقق من ربط الاسترجاع المعتمد؛ احتُفظ بآخر رصيد')
        provider = records[0]['provider']
        saved = state['engine']['provider_fee_inputs']['orders'][oid]['payment']
        orders, unresolved = by_order.get(oid, []), False
        if len(orders) != 1:
            unresolved = True
        for order in orders:
            observation = order.get('statement_refund_observation')
            if observation is not None:
                clean = {key: value for key, value in observation.items() if key != 'fingerprint'}
                valid = (observation.get('fingerprint') == digest(clean)
                    and observation.get('owner_id') == state.get('owner_id') and observation.get('order_id') == oid
                    and observation.get('provider') == provider and not observation.get('unresolved'))
                events = observation.get('refunds', []) if valid else []
                unresolved |= not valid or any(not any(_approved_native(record, event) for record in records) for event in events)
                unresolved |= bool(any(record.get('native_aliases') for record in records)) and not events
            else:
                payment = order.get('payment')
                if (not isinstance(payment, dict) or payment.get('provider') != provider or payment.get('split')
                        or payment.get('currency') != records[0]['currency']
                        or _amount(payment.get('gross')) != _amount(saved.get('gross'))
                        or payment.get('captures') != saved.get('captures')):
                    unresolved = True
                else:
                    try:
                        for value in payment.get('refunds', []):
                            event = _native_refund(value, oid=oid, number=str(records[0]['order_number']),
                                provider=provider, currency=records[0]['currency'])
                            unresolved |= not any(_approved_native(record, event) for record in records)
                    except (ValueError, TypeError):
                        unresolved = True
            # Keep the approved economic representation, including its explicit
            # interval marker or employee timestamp. Native time stays in the
            # separately approved alias proof and is revalidated above.
            order['payment'] = deepcopy(saved)
        sources['issues'] = _clear_alias_issues(sources.get('issues', []), oid, provider)
        if unresolved:
            sources['issues'].append({'code': 'provider_statement_refund_alias_review',
                'source_id': 'provider:' + oid, 'order_id': oid, 'provider': provider,
                'scope': 'provider_payment', 'message': MESSAGES['statement_native_alias_review']})


def _event(state, document, row, candidate, oid, confirmation):
    native = row['native_currency']
    key = digest([state['owner_id'], row['provider'], native, KINDS[row['event_type']], candidate['effect_id']])
    event_day = row.get('event_date') or candidate.get('event_date')
    return {'event_key': key, 'provider': row['provider'], 'order_id': oid,
        'native_currency': native, 'kind': KINDS[row['event_type']],
        'principal_amount': row['principal_amount'], 'fee_net': row['fee_net'],
        'fee_vat': row['fee_vat'], 'fee_total': row['fee_total'],
        'event_date': event_day, 'date_precision': 'day' if event_day else 'snapshot_interval',
        'covered_event': {'kind': candidate['kind'], 'id': candidate['id'], 'fingerprint': candidate['fingerprint'],
            'amount': candidate['amount'], 'financial_effect': 'operational_fee' if candidate['financial_effect'] == 'operational_fee' else 'fee_only'},
        'source': {'file_hash': document['statement']['file_hash'], 'row_fingerprint': row['row_fingerprint'],
            'statement_ref': document['statement'].get('statement_reference') or 'file:' + document['statement']['file_hash'],
            'plan_id': row.get('plan_id'), 'payment_method': row.get('payment_method')},
        'confirmation': confirmation, 'accepted_at': document['uploaded_at']}


def _event_terms(event):
    return {key: event.get(key) for key in ('event_key', 'provider', 'order_id', 'native_currency', 'kind',
        'principal_amount', 'fee_net', 'fee_vat', 'fee_total')}


def _action(row, code):
    row.update(reason_code=code, message=MESSAGES.get(code, MESSAGES['statement_fee_coverage_incomplete']))
    if row['status'] in {'missing', 'different', 'blocked'} and not row['can_confirm']:
        refund = row['kind'] == 'refund'
        row.update(action_url='/operational-balances/customer-returns' if refund else '/operational-balances',
                   action_label='مراجعة الاسترجاعات المسجلة' if refund else 'مراجعة الدفع والأرصدة')


def _review(state, document, actor, external=None):
    data = _validate_document(document, state['owner_id'])
    orders, by_number, indexed = _frozen_candidates(state)
    accepted = state.get(RECONCILIATION, {}).get('events', {})
    refund_records = {record.get('event_key'): record
        for record in state.get(RECONCILIATION, {}).get('refund_records', {}).values()}
    proposals, rows = {}, []
    from operational_provider_statement_fees import prepare_statement_fee_context, statement_event_issue
    fee_context = prepare_statement_fee_context(state)
    quota = {'remaining': MAX_MISSING_PREVIEWS}

    def offer_missing(source, row, oid):
        plan, reason = _missing_plan(state, document, source, oid, actor, external, quota)
        if plan is None:
            _action(row, reason)
            return
        row.update(status='missing', can_confirm=True, allowed_actions=[plan['action']], proposed_effect=plan['effect'])
        _action(row, 'statement_missing_' + KINDS[source['event_type']] + '_ready')
        proposals[row['id']] = {'__missing__': {**plan['event'], '_missing_patch': plan['patch'], '_action': plan['action']}}

    for source in checked_rows(data['rows']):
        kind = KINDS.get(source.get('event_type'), 'other')
        row = {key: source.get(key) for key in ('id', 'order_number', 'provider', 'native_currency',
            'principal_amount', 'fee_net', 'fee_vat', 'fee_total', 'fee_variable_net', 'fee_fixed_net',
            'event_date', 'plan_id', 'payment_method', 'source_row', 'installments')}
        row.update(kind=kind, status='blocked', existing=[], can_confirm=False, allowed_actions=[],
                   source_issues=list(source.get('blockers') or []))
        rows.append(row)
        if kind not in {'capture', 'refund'}:
            _action(row, 'statement_non_order_event'); continue
        amounts = [_amount(source.get(k)) for k in ('principal_amount', 'fee_net', 'fee_vat', 'fee_total')]
        if (source.get('blockers') or source.get('provider') != data['provider']
                or not isinstance(source.get('native_currency'), str) or len(source['native_currency']) != 3
                or any(value is None for value in amounts) or amounts[0] <= 0
                or amounts[1] + amounts[2] != amounts[3]):
            _action(row, 'statement_row_incomplete'); continue
        identities = by_number.get(str(source.get('order_number')), [])
        if len(identities) != 1:
            row['status'] = 'missing' if not identities else 'blocked'
            _action(row, 'statement_order_missing' if not identities else 'statement_order_ambiguous'); continue
        oid = identities[0]
        order = orders[oid]
        payment = order.get('payment') or {}
        providers = {payment.get('provider')}
        providers.update(leg.get('provider') for leg in (payment.get('split') or {}).get('tenders', []))
        if source['provider'] not in providers or source['native_currency'] != order.get('currency'):
            _action(row, 'statement_provider_or_currency_conflict'); continue
        existing = indexed.get((oid, source['provider'], kind), [])
        row['existing'] = [{key: item.get(key) for key in ('kind', 'id', 'amount', 'reference', 'financial_effect')}
                           for item in existing]
        if not existing:
            row['status'] = 'missing'; offer_missing(source, row, oid); continue
        equal = [item for item in existing if _amount(item['amount']) == amounts[0]
                 and item.get('currency') == source['native_currency'] and item['financial_effect'] != 'evidence_only']
        if not equal:
            row['status'] = 'different'
            if any(item['financial_effect'] == 'evidence_only' for item in existing):
                offer_missing(source, row, oid)
            else:
                _action(row, 'statement_amount_difference')
            continue
        reference = source.get('event_reference')
        exact = [item for item in equal if (not reference or item.get('reference') == reference)
                 and (not source.get('event_date') or not item.get('event_date') or source['event_date'] == item['event_date'])]
        auto = len(exact) == 1 and len(equal) == 1
        # Linking an already-approved refund identity is independent of current
        # fee allocation. A later partial payout may correctly withhold its fee
        # credit, while this canonical principal proof still permits metadata
        # linkage with no new fee or principal acceptance.
        canonical_refunds = []
        for item in equal:
            event = _event(state, document, source, item, oid, 'owner_approved')
            old = accepted.get(event['event_key'])
            record = refund_records.get(event['event_key'])
            if old and record and _event_terms(old) == _event_terms(event) and _valid_refund_record(state, record):
                canonical_refunds.append((item['id'], old, record))
        if len(canonical_refunds) == 1:
            existing_id, old, record = canonical_refunds[0]
            alias, needs_review = _native_alias_plan(state, record, external)
            if needs_review:
                row.update(status='different', can_confirm=alias is not None,
                    allowed_actions=['link_native_refund'] if alias is not None else [],
                    fee_evidence_applied=statement_event_issue(state, old, context=fee_context) is None)
                _action(row, 'statement_native_alias_ready' if alias else 'statement_native_alias_review')
                if alias:
                    native = alias['event']
                    row['existing'] = [item for item in row['existing'] if item['id'] == existing_id]
                    row['native_alias'] = {'id': native['reference'], 'reference': native['reference'],
                        'amount': native['amount'], 'native_currency': native['native_currency'],
                        'event_date': _day(native['refunded_at']), 'source': alias['source']}
                    proposals[row['id']] = {existing_id: {**old, '_native_alias_patch': alias,
                        '_action': 'link_native_refund'}}
                continue
        eligible = {}
        for item in equal:
            event = _event(state, document, source, item, oid, 'exact_match' if auto and item == exact[0] else 'owner_approved')
            issue = statement_event_issue(state, event, context=fee_context)
            if issue is None:
                eligible[item['id']] = event
        if not eligible:
            _action(row, 'statement_fee_coverage_incomplete'); continue
        proposals[row['id']] = eligible
        row['can_confirm'], row['allowed_actions'] = True, ['accept_statement']
        candidate = eligible.get(exact[0]['id']) if auto else None
        # Claims are independent of file/row IDs. Reuploading an overlapping
        # weekly statement cannot create another fee for an existing effect.
        previous = [(key, accepted.get(event['event_key'])) for key, event in eligible.items()
                    if event['event_key'] in accepted]
        if previous:
            proposals.pop(row['id'], None)
            good = [(key, old) for key, old in previous if _event_terms(old) == _event_terms(eligible[key])
                    and statement_event_issue(state, old, context=fee_context) is None]
            if len(good) == 1:
                old = good[0][1]
                row.update(status='matched' if old.get('confirmation') == 'exact_match'
                    and old.get('source', {}).get('file_hash') == data['file_hash'] else 'already_confirmed',
                    can_confirm=False, allowed_actions=[], fee_evidence_applied=True)
                _action(row, 'statement_confirmed')
            else:
                row.update(status='different', can_confirm=False, allowed_actions=[])
                _action(row, 'statement_event_conflict')
        elif candidate is not None:
            row['status'] = 'matched'; _action(row, 'statement_matched')
        else:
            row['status'] = 'different'; _action(row, 'statement_identity_review')
    # Distinct source rows cannot each claim the same effect within one upload.
    coverage = defaultdict(set)
    for row_id, choices in proposals.items():
        for event in choices.values():
            coverage[event['event_key']].add(row_id)
    duplicated = set().union(*(ids for ids in coverage.values() if len(ids) > 1)) if coverage else set()
    missing_orders = defaultdict(set)
    for row_id, choices in proposals.items():
        for event in choices.values():
            if event.get('_missing_patch'):
                missing_orders[event['order_id']].add(row_id)
    # Each preview has one independently proved principal delta per order.
    # A batch with multiple new effects for one order needs the full manual
    # event sequence instead of last-writer-wins provider balances.
    duplicated.update(set().union(*(ids for ids in missing_orders.values() if len(ids) > 1)) if missing_orders else set())
    for row in rows:
        if row['id'] in duplicated:
            row.update(status='blocked', can_confirm=False, allowed_actions=[])
            _action(row, 'statement_duplicate_effect'); proposals.pop(row['id'], None)
    counts = {key: 0 for key in COUNTS}
    for row in rows:
        counts['confirmed' if row['status'] == 'already_confirmed' else row['status']] += 1
    binding = {'owner': state['owner_id'], 'actor': actor, 'revision': state['revision'],
        'started_at': state.get('started_at'), 'as_of': state.get('engine', {}).get('as_of'),
        'statement': document['content_hash'], 'external': (external or {}).get('fingerprint'),
        'rows': rows, 'proposals': proposals}
    result = {'session_scope': digest([state['owner_id'], actor]), 'active': state.get('status') == 'active',
        'revision': state['revision'], 'started_at': state.get('started_at'),
        'as_of': state.get('engine', {}).get('as_of'),
        'statement_evidence_as_of': state.get('engine', {}).get('provider_fee_inputs', {}).get('statement_evidence_as_of'),
        'statement': {'id': document['id'], 'provider': data['provider'],
            'statement_reference': data.get('statement_reference'), 'merchant_account': data.get('merchant_account'),
            'filename': document['filename'], 'file_hash': data['file_hash'], 'native_currency': data.get('currency'),
            'row_count': len(data['rows']), 'uploaded_at': document['uploaded_at'], 'totals': deepcopy(data.get('totals', {}))},
        'preview_hash': digest(binding), 'rows': rows, 'counts': counts, 'bank_posting_allowed': False,
        'permissions': {'upload': state.get('status') == 'active', 'confirm': state.get('status') == 'active'}}
    result['statement_average'] = state.get(RECONCILIATION, {}).get('statement_summaries', {}).get(document['id'], {}).get('statement_average')
    return result, proposals


def build_review(state, document, actor, external=None):
    return _review(state, document, actor, external)[0]


async def _owner(db, owner, actor):
    person = await db['users'].find_one({'id': actor}, {'_id': 0})
    if (actor != owner or not person or person.get('role') != 'owner' or person.get('disabled')
            or person.get('is_active') is False or person.get('deleted_at')):
        fail('provider_statement_owner_required', 'رفع الكشوف واعتماد مطابقتها متاح للمالك فقط', 403)
    await check_authorization()


async def _document(db, owner, identity):
    document = await db[STATEMENTS].find_one({'_id': identity, 'owner_id': owner})
    if document is None:
        fail('provider_statement_missing', 'الكشف غير موجود في هذا الحساب', 404)
    _validate_document(document, owner)
    return document


async def _authority(db, owner, state):
    if state.get('status') != 'active':
        return False
    try:
        await accounting_inactive(db, owner)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get('code') == 'operational_accounting_active':
            return False
        raise
    return True


async def review(db, owner, actor, statement_id):
    await _owner(db, owner, actor)
    document, state = await _document(db, owner, statement_id), await read(db, owner)
    external = await _external_evidence(db, owner, document, state)
    result = await prepare_in_thread(build_review, state, document, actor, external)
    result['active'] = await _authority(db, owner, state)
    result['permissions'] = {'upload': result['active'], 'confirm': result['active']}
    await _owner(db, owner, actor)
    return result


async def history(db, owner, actor, *, limit=50):
    await _owner(db, owner, actor)
    if type(limit) is not int or not 1 <= limit <= 100:
        fail('provider_statement_history_limit', 'حد قراءة الكشوف غير صالح', 422)
    from operational_read_runtime import read_source_cursor
    state_rows = await read_source_cursor(db[STATES].find({'_id': owner, 'owner_id': owner}, {
        '_id': 1, 'owner_id': 1, 'revision': 1, 'status': 1, 'started_at': 1, 'engine.as_of': 1,
        RECONCILIATION + '.statement_summaries': 1}).limit(1), 1)
    state = state_rows[0] if state_rows else initial(owner)
    records = await read_source_cursor(db[STATEMENTS].find({'owner_id': owner}, {
        '_id': 1, 'id': 1, 'owner_id': 1, 'metadata': 1, 'metadata_hash': 1,
        'uploaded_at': 1}).sort('uploaded_at', -1).limit(limit + 1), limit + 1)
    summaries = state.get(RECONCILIATION, {}).get('statement_summaries', {})
    items = []
    for document in records[:limit]:
        metadata = document.get('metadata')
        if (document.get('owner_id') != owner or not isinstance(metadata, dict)
                or document.get('metadata_hash') != digest(metadata) or metadata.get('id') != document.get('id')):
            fail('provider_statement_metadata_invalid', 'تعذر التحقق من فهرس الكشف المحفوظ')
        summary = summaries.get(document['id']) or {}
        counts = summary.get('counts')
        items.append({**deepcopy(metadata), 'counts': deepcopy(counts),
            'reviewed_at': summary.get('reviewed_at'), 'review_as_of': summary.get('review_as_of'),
            'statement_average': summary.get('statement_average'),
            'status': 'needs_review' if not counts or any(counts[key] for key in ('missing', 'different', 'blocked')) else 'matched'})
    active = await _authority(db, owner, state)
    await _owner(db, owner, actor)
    return {'session_scope': digest([owner, actor]), 'active': active, 'revision': state['revision'],
        'started_at': state.get('started_at'), 'as_of': state.get('engine', {}).get('as_of'),
        'items': items, 'has_more': len(records) > limit, 'permissions': {'upload': active, 'confirm': active}}


def _file_hash(value):
    return value if isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) else None


def _original_metadata_problem(source, originals, owner):
    if not _file_hash(source.get('file_hash')):
        return 'بصمة الكشف المحفوظ غير مكتملة؛ أعد رفع الملف الأصلي.'
    size = source.get('file_size')
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_FILE_BYTES:
        return 'حجم الملف المحفوظ غير موثق أو يتجاوز 10 ميغابايت؛ أعد رفع الأصل ضمن الحد المسموح.'
    if not originals:
        return 'لم يُحفظ أصل هذا الكشف؛ أعد رفع الملف الأصلي من جهازك.'
    if len(originals) != 1:
        return 'يوجد أكثر من أصل مرتبط بهذا الكشف؛ يلزم تصحيح الربط قبل استخدامه.'
    blob = originals[0]
    if (blob.get('user_id') != owner or blob.get('file_id') != source['id']
            or blob.get('_id') != sha256((owner + ':' + source['id']).encode()).hexdigest()
            or blob.get('sha256') != source['file_hash']):
        return 'بصمة أصل الكشف لا تطابق الملف المحفوظ؛ أعد رفع الأصل الصحيح.'
    if isinstance(blob.get('size'), bool) or not isinstance(blob.get('size'), int) or blob.get('size') != size:
        return 'حجم أصل الكشف لا يطابق الملف المحفوظ؛ أعد رفع الأصل الصحيح.'
    return None


async def saved_files(db, owner, actor, *, limit=50):
    """Visible metadata only: no original bytes, totals, parsing or mutations."""
    await _owner(db, owner, actor)
    if type(limit) is not int or not 1 <= limit <= 100:
        fail('provider_statement_history_limit', 'حد قراءة الكشوف غير صالح', 422)
    from operational_read_runtime import read_source_cursor
    states = await read_source_cursor(db[STATES].find({'_id': owner, 'owner_id': owner}, {
        '_id': 1, 'owner_id': 1, 'revision': 1, 'status': 1, 'started_at': 1, 'engine.as_of': 1}).limit(1), 1)
    state = states[0] if states else initial(owner)
    files = await read_source_cursor(db['settlement_files'].find({'user_id': owner,
        'provider': {'$in': sorted(PROVIDERS)}}, SAVED_FILE_FIELDS).sort('uploaded_at', -1).limit(limit + 1), limit + 1)
    selected = files[:limit]
    if any(row.get('user_id') != owner or row.get('provider') not in PROVIDERS
           or not isinstance(row.get('id'), str) or not 0 < len(row['id']) <= 160 for row in selected):
        fail('provider_statement_saved_metadata_invalid', 'تعذر التحقق من فهرس الملفات المحفوظة', 503)
    ids = [row['id'] for row in selected]
    blobs = await read_source_cursor(db['accounting_source_files'].find({'user_id': owner,
        'file_id': {'$in': ids}}, ORIGINAL_METADATA_FIELDS).limit(2 * limit + 1), 2 * limit + 1) if ids else []
    if len(blobs) > 2 * limit:
        fail('provider_statement_saved_metadata_invalid', 'تعذر التحقق من اكتمال فهرس الأصول المحفوظة', 503)
    by_file = defaultdict(list)
    for blob in blobs:
        by_file[blob.get('file_id')].append(blob)
    active = await _authority(db, owner, state)
    items, seen = [], set()
    for source in selected:
        identity = source['id']
        if identity in seen:
            continue
        seen.add(identity)
        originals = by_file.get(identity, [])
        reason = _original_metadata_problem(source, originals, owner)
        if ids.count(identity) > 1:
            reason = 'هوية الكشف المحفوظ مكررة؛ يلزم تصحيح الربط قبل استخدامه.'
        if not reason and not active:
            reason = 'استخدام الكشف متاح بعد تفعيل فترة الأرصدة التشغيلية.'
        header = source.get('header') if isinstance(source.get('header'), dict) else {}
        reference = header.get('statement_id') or header.get('invoice_number')
        uploaded = source.get('uploaded_at')
        items.append({'id': identity, 'provider': source['provider'],
            'filename': PurePath(str(source.get('filename') or 'statement.xlsx').replace('\\', '/')).name[:240],
            'statement_reference': str(reference) if reference is not None else None,
            'uploaded_at': uploaded.isoformat() if isinstance(uploaded, datetime) else uploaded if isinstance(uploaded, str) else None,
            'file_hash': _file_hash(source.get('file_hash')), 'original_available': len(originals) == 1,
            'can_import': active and reason is None, 'unavailable_reason': reason})
    await _owner(db, owner, actor)
    return {'session_scope': digest([owner, actor]), 'started_at': state.get('started_at'),
        'as_of': state.get('engine', {}).get('as_of'), 'active': active, 'items': items,
        'has_more': len(files) > limit, 'permissions': {'upload': active}}


async def use_saved_file(db, owner, actor, file_id, payload):
    """Verify one preserved original, then use the existing upload transaction."""
    await _owner(db, owner, actor)
    if payload.get('expected_session_scope') != digest([owner, actor]):
        fail('operational_actor_scope_changed', 'تغير الحساب؛ أعد فتح صفحة الكشوف', 409)
    state = await read(db, owner)
    await active_gate(db, owner, state)
    if payload.get('expected_started_at') != state.get('started_at'):
        fail('provider_statement_period_changed', 'تغيرت فترة الأرصدة؛ أعد فتح صفحة الكشوف', 409)
    identity = {'request_id': payload['request_id'], 'expected_session_scope': payload['expected_session_scope'],
        'expected_started_at': payload['expected_started_at'], 'file_hash': payload.get('expected_file_hash'),
        'source_file_id': file_id}
    prior = replay(state, 'provider_statement_saved_file_use:' + actor, identity)
    if prior is not None:
        result = await _replay_review(db, owner, actor, state, prior)
        await _owner(db, owner, actor)
        return result
    from operational_read_runtime import read_source_cursor
    files = await read_source_cursor(db['settlement_files'].find({'user_id': owner, 'id': file_id,
        'provider': {'$in': sorted(PROVIDERS)}}, SAVED_FILE_FIELDS).limit(2), 2)
    if not files:
        fail('provider_statement_saved_file_missing', 'الكشف المحفوظ غير موجود في هذا الحساب', 404)
    if len(files) != 1 or files[0].get('user_id') != owner or files[0].get('id') != file_id:
        fail('provider_statement_saved_metadata_invalid', 'تعذر التحقق من هوية الكشف المحفوظ', 409)
    source = files[0]
    if not _file_hash(payload.get('expected_file_hash')) or source.get('file_hash') != payload['expected_file_hash']:
        fail('provider_statement_saved_file_changed', 'تغيرت بصمة الكشف المحفوظ؛ أعد قراءة قائمة الملفات', 409)
    originals = await read_source_cursor(db['accounting_source_files'].find({'user_id': owner,
        'file_id': file_id}, ORIGINAL_METADATA_FIELDS).limit(2), 2)
    reason = _original_metadata_problem(source, originals, owner)
    if reason:
        fail('provider_statement_saved_original_invalid' if originals else 'provider_statement_saved_original_unavailable', reason, 409)
    blobs = await read_source_cursor(db['accounting_source_files'].find({'_id': originals[0]['_id'],
        'user_id': owner, 'file_id': file_id}, {**ORIGINAL_METADATA_FIELDS, 'content': 1}).limit(1), 1)
    blob = blobs[0] if blobs else {}
    content = blob.get('content')
    if (_original_metadata_problem(source, [blob], owner) is not None
            or not isinstance(content, bytes) or not 0 < len(content) <= MAX_FILE_BYTES
            or len(content) != source['file_size'] or len(content) != blob.get('size')
            or sha256(content).hexdigest() != source['file_hash']
            or blob.get('sha256') != source['file_hash']):
        fail('provider_statement_saved_original_invalid', 'تعذر التحقق من بايتات أصل الكشف وحجمه وبصمته؛ لم تتغير الأرصدة', 409)
    from excel_upload_security import validate_xlsx_archive
    await prepare_in_thread(validate_xlsx_archive, content)
    await _owner(db, owner, actor)
    return await upload(db, owner, actor, content=content, filename=source.get('filename') or 'statement.xlsx',
        request_id=payload['request_id'], expected_session_scope=payload['expected_session_scope'],
        expected_started_at=payload['expected_started_at'], saved_source={
            'file_id': file_id, 'provider': source['provider'], 'file_hash': source['file_hash']})


def _remember_review(state, action, payload, result, document, stamp):
    state.setdefault(RECONCILIATION, {}).setdefault('statement_summaries', {})[document['id']] = {
        'counts': deepcopy(result['counts']), 'reviewed_at': stamp, 'review_as_of': result['as_of'],
        'statement_average': result.get('statement_average')}
    result['reviewed_at'] = stamp
    remember(state, action, payload, {'statement_id': document['id'], 'started_at': state['started_at'],
        'applied_row_ids': list(result['applied_row_ids']), 'accepted_at': stamp, 'duplicate': result.get('duplicate', False)})


async def _replay_review(db, owner, actor, state, receipt):
    if receipt.get('started_at') != state.get('started_at'):
        fail('provider_statement_period_changed', 'العملية محفوظة في فترة سابقة؛ راجعها في سجل الكشف', 409)
    document = await _document(db, owner, receipt['statement_id'])
    external = await _external_evidence(db, owner, document, state)
    result = await prepare_in_thread(build_review, state, document, actor, external)
    result.update(applied_row_ids=list(receipt['applied_row_ids']), replayed=True,
                  duplicate=receipt.get('duplicate', False), reviewed_at=receipt['accepted_at'])
    return result


def _save_events(state, events, document, actor, stamp):
    from operational_provider_statement_fees import freeze_statement_fee_events, statement_event_issues
    stamped = {key: {**{name: value for name, value in event.items() if not name.startswith('_')},
                     'accepted_at': stamp} for key, event in events.items()}
    if any(statement_event_issues(state, list(stamped.values()))):
        fail('provider_statement_fee_coverage_incomplete', MESSAGES['statement_fee_coverage_incomplete'])
    records = state.setdefault(RECONCILIATION, {})
    accepted = records.setdefault('events', {})
    for row_id, event in stamped.items():
        prior = accepted.get(event['event_key'])
        if prior is not None and _event_terms(prior) != _event_terms(event):
            fail('provider_statement_event_conflict', MESSAGES['statement_event_conflict'])
        if prior is None:
            accepted[event['event_key']] = deepcopy(event)
        records.setdefault('decisions', {})[digest([document['id'], row_id])] = {
            'statement_id': document['id'], 'row_id': row_id, 'event_key': event['event_key'],
            'confirmed_by': actor, 'confirmed_at': stamp, 'confirmation': event['confirmation']}
    if events:
        freeze_statement_fee_events(state, accepted_at=stamp)


def _save_statement_average(state, document, actor, stamp):
    from operational_provider_statement_fees import (derive_statement_average_profile,
        store_statement_average_profile, freeze_statement_fee_events, statement_average_summary)
    data = document['statement']
    profile = derive_statement_average_profile(state, data, statement_id=document['id'], accepted_at=document['uploaded_at'])
    if profile is None:
        sales = [row for row in data['rows'] if row.get('event_type') == 'sale']
        if not sales:
            code, message = 'statement_average_no_sales', 'لا يحتوي هذا الكشف على مبيعات صالحة لاشتقاق متوسط العمولة.'
        elif any(row.get('fee_variable_net') is None or row.get('fee_fixed_net') is None for row in sales):
            code, message = 'statement_average_component_split_missing', 'لا يفصل الكشف العمولة المتغيرة عن الرسم الثابت؛ لم يُفترض أي متوسط.'
        else:
            code, message = 'statement_average_sale_evidence_incomplete', 'بيانات المبيعات أو العملة أو مكونات الرسوم غير مكتملة أو متعارضة؛ لم يُعتمد متوسط لهذا الكشف.'
        return {'available': False, 'reason_code': code, 'message': message}
    try:
        changed = store_statement_average_profile(state, profile)
    except ValueError as exc:
        fail(str(exc), 'تعذر اعتماد نطاق متوسط العمولة؛ لم تتغير الأرصدة')
    if changed:
        freeze_statement_fee_events(state, accepted_at=stamp)
        audit(state, actor, 'provider_statement_average_saved', None,
            {'statement_id': document['id'], 'profile_id': profile['profile_id'],
             'provider': profile['provider'], 'native_currency': profile['native_currency'],
             'calculation': profile['calculation']},
            'حفظ متوسط العمولة المتغيرة قبل الضريبة من الكشف مع فصل الرسم الثابت والضريبة', 'provider_statement', stamp)
    return statement_average_summary(profile)


async def upload(db, owner, actor, *, content, filename, request_id, expected_session_scope, expected_started_at, clock=None, saved_source=None):
    await _owner(db, owner, actor)
    if expected_session_scope != digest([owner, actor]):
        fail('operational_actor_scope_changed', 'تغير الحساب؛ أعد فتح صفحة الكشوف', 409)
    if not content or len(content) > MAX_FILE_BYTES:
        fail('provider_statement_size_invalid', 'حجم الكشف غير مقبول', 413)
    state = await read(db, owner)
    await active_gate(db, owner, state)
    if state.get('started_at') != expected_started_at:
        fail('provider_statement_period_changed', 'تغيرت فترة الأرصدة؛ أعد فتح صفحة الكشوف', 409)
    action = ('provider_statement_saved_file_use:' if saved_source else 'provider_statement_upload:') + actor
    payload = {'request_id': request_id, 'expected_session_scope': expected_session_scope,
               'expected_started_at': expected_started_at, 'file_hash': sha256(content).hexdigest()}
    if saved_source:
        payload['source_file_id'] = saved_source['file_id']
    prior = replay(state, action, payload)
    if prior is not None:
        result = await _replay_review(db, owner, actor, state, prior)
        await _owner(db, owner, actor)
        return result
    try:
        normalized = await prepare_in_thread(normalize_statement, content)
    except ValueError as exc:
        fail('provider_statement_parse_invalid', str(exc), 422)
    if saved_source and (normalized['provider'] != saved_source['provider'] or normalized['file_hash'] != saved_source['file_hash']):
        fail('provider_statement_saved_provider_conflict', 'هوية المنصة في الملف الأصلي لا تطابق فهرس الكشف المحفوظ؛ لم تتغير الأرصدة', 409)
    stamp = clock or now()
    document = statement_document(owner, actor, filename, normalized, stamp)
    await _owner(db, owner, actor)
    # These indexes are created only by an explicit upload, never by GET.
    await db[STATEMENTS].create_index([('owner_id', 1), ('statement_key', 1)], unique=True)
    await db[STATEMENTS].create_index([('owner_id', 1), ('uploaded_at', -1)])
    previous = await db[STATEMENTS].find_one({'_id': document['id'], 'owner_id': owner})
    duplicate = previous is not None
    if previous is not None:
        _validate_document(previous, owner)
        if previous['statement']['fingerprint'] != normalized['fingerprint']:
            fail('provider_statement_reference_conflict', 'مرجع الكشف مستخدم بمحتوى مختلف؛ لم تتغير الأرصدة')
        document = previous
    else:
        await check_authorization()
        await preserve_original(db, owner, document['original_file_id'], content)
        await check_authorization()
        try:
            await db[STATEMENTS].insert_one(document)
        except DuplicateKeyError:
            duplicate = True
            previous = await _document(db, owner, document['id'])
            if previous['statement']['fingerprint'] != normalized['fingerprint']:
                fail('provider_statement_reference_conflict', 'مرجع الكشف مستخدم بمحتوى مختلف؛ لم تتغير الأرصدة')
            document = previous

    async def apply(current):
        await _owner(db, owner, actor)
        await active_gate(db, owner, current)
        if current.get('started_at') != expected_started_at:
            fail('provider_statement_period_changed', 'تغيرت فترة الأرصدة؛ أعد فتح صفحة الكشوف', 409)
        prior = replay(current, action, payload)
        if prior is not None:
            return await _replay_review(db, owner, actor, current, prior)
        if saved_source:
            key = digest(saved_source['file_id'])
            links = current.setdefault(RECONCILIATION, {}).setdefault('source_links', {})
            prior_link = links.get(key)
            proof = {**saved_source, 'statement_id': document['id']}
            if prior_link and any(prior_link.get(name) != value for name, value in proof.items()):
                fail('provider_statement_saved_file_changed', 'تغير أصل الكشف المرتبط بالمطابقة؛ أعد المراجعة', 409)
            if not prior_link:
                links[key] = {**proof, 'linked_by': actor, 'linked_at': stamp}
                audit(current, actor, 'provider_statement_saved_source_linked', None, proof,
                    'استخدام الأصل المحفوظ لإعادة المطابقة التشغيلية دون إعادة الاستيراد أو الترحيل', 'provider_statement', stamp)
        external = await _external_evidence(db, owner, document, current)
        # This new receipt always commits one aggregate revision. Compute its
        # preview once at that revision; a missing-only upload must not run its
        # bounded principal previews a second time just to update the header.
        view, choices = await prepare_in_thread(_review, {**current, 'revision': current['revision'] + 1}, document, actor, external)
        selected = {row['id']: next(iter(choices[row['id']].values())) for row in view['rows']
                    if row['status'] == 'matched' and row['id'] in choices and len(choices[row['id']]) == 1}
        _save_events(current, selected, document, actor, stamp)
        average = _save_statement_average(current, document, actor, stamp)
        if selected:
            audit(current, actor, 'provider_statement_exact_matches', None,
                {'statement_id': document['id'], 'event_keys': [e['event_key'] for e in selected.values()]},
                'مطابقة كشف المنصة مع العمليات المحفوظة دون تكرار الدفع أو الاسترجاع', 'provider_statement', stamp)
        result = (await prepare_in_thread(build_review, {**current, 'revision': current['revision'] + 1}, document, actor, external)) if selected else view
        result['statement_evidence_as_of'] = current.get('engine', {}).get('provider_fee_inputs', {}).get('statement_evidence_as_of')
        result.update(applied_row_ids=list(selected), replayed=False, duplicate=duplicate, statement_average=average)
        _remember_review(current, action, payload, result, document, stamp)
        return result
    result = await mutate(db, owner, apply)
    await _owner(db, owner, actor)
    return result


async def confirm(db, owner, actor, statement_id, payload, *, clock=None):
    await _owner(db, owner, actor)
    if payload.get('expected_session_scope') != digest([owner, actor]):
        fail('operational_actor_scope_changed', 'تغير الحساب؛ أعد قراءة المطابقة', 409)
    if payload.get('confirmed') is not True or not payload.get('decisions'):
        fail('provider_statement_confirmation_required', 'اختر العمليات وأكد مطابقتها', 422)
    document = await _document(db, owner, statement_id)
    action, stamp = 'provider_statement_confirm:' + actor, clock or now()
    request = {**payload, 'statement_id': statement_id}
    expected_external = {'value': None, 'state': None}

    async def apply(current):
        await _owner(db, owner, actor)
        await active_gate(db, owner, current)
        prior = replay(current, action, request)
        if prior is not None:
            return await _replay_review(db, owner, actor, current, prior)
        fresh = await _document(db, owner, statement_id)
        external = await _external_evidence(db, owner, fresh, current)
        view, choices = await prepare_in_thread(_review, current, fresh, actor, external)
        if view['preview_hash'] != payload.get('expected_preview_hash'):
            fail('provider_statement_preview_stale', 'تغير الكشف أو العملية أو الأرصدة؛ أعد قراءة المطابقة')
        selected, public = {}, {row['id']: row for row in view['rows']}
        for decision in payload['decisions']:
            row_id = decision.get('row_id')
            if (row_id in selected or row_id not in choices or not public[row_id]['can_confirm']
                    or decision.get('action') not in public[row_id]['allowed_actions']):
                fail('provider_statement_decision_invalid', 'هذا السطر غير مؤهل للاعتماد أو مكرر', 422)
            missing = decision.get('action') in {'record_missing_capture', 'record_missing_refund'}
            if missing and payload.get('confirmed_unrecorded') is not True:
                fail('provider_statement_unrecorded_confirmation_required', 'أكد أن العملية غير مسجلة سابقًا وخارج الرصيد الافتتاحي', 422)
            chosen = choices[row_id].get('__missing__' if missing else decision.get('existing_id'))
            if chosen is None:
                fail('provider_statement_existing_selection_required', 'اختر العملية المسجلة المطابقة لهذا السطر', 422)
            if missing and decision.get('existing_id') != chosen['_missing_patch'].get('employee_case_id'):
                fail('provider_statement_existing_selection_required', 'اختر إثبات الموظف الذي سيُربط بالاسترجاع دون إنشاء سجل ثانٍ', 422)
            selected[row_id] = {**chosen, 'confirmation': 'owner_approved'}
        if len({e['event_key'] for e in selected.values()}) != len(selected):
            fail('provider_statement_duplicate_effect', MESSAGES['statement_duplicate_effect'])
        before_cases, linked_aliases = [], []
        for event in selected.values():
            if event.get('_missing_patch') or event.get('_native_alias_patch'):
                expected_external.update(value=external['fingerprint'], state=deepcopy(current))
            if event.get('_missing_patch'):
                case_id = event['_missing_patch'].get('employee_case_id')
                if case_id:
                    before_cases.extend(deepcopy(case) for case in current.get('customer_returns', []) if case['id'] == case_id)
                _apply_missing_patch(current, event['_missing_patch'], stamp)
            if event.get('_native_alias_patch'):
                _apply_native_alias(current, event['_native_alias_patch'], actor, stamp)
                linked_aliases.append(deepcopy(event['_native_alias_patch']))
        _save_events(current, {key: event for key, event in selected.items() if not event.get('_native_alias_patch')}, fresh, actor, stamp)
        audit(current, actor, 'provider_statement_confirmed', {'employee_cases': before_cases},
            {'statement_id': statement_id, 'event_keys': [e['event_key'] for e in selected.values()],
             'native_alias_links': linked_aliases,
             'employee_cases': [deepcopy(case) for case in current.get('customer_returns', [])
                                if case.get('id') in {old['id'] for old in before_cases}]},
            'اعتماد ربط سطور الكشف بالعمليات المسجلة؛ لا تحويل بنكي جديد', 'provider_statement', stamp)
        result = await prepare_in_thread(build_review, {**current, 'revision': current['revision'] + 1}, fresh, actor, external)
        result.update(applied_row_ids=list(selected), replayed=False)
        _remember_review(current, action, request, result, fresh, stamp)
        return result
    outer_guard = AUTHORIZATION_GUARD.get()
    async def verify_sources():
        if outer_guard is not None:
            await outer_guard()
        if expected_external['value'] is not None:
            latest = await _external_evidence(db, owner, document, expected_external['state'])
            if latest['fingerprint'] != expected_external['value']:
                fail('provider_statement_preview_stale', 'ظهر سجل دفع أو استرجاع جديد؛ أعد المراجعة قبل الاعتماد')
    token = AUTHORIZATION_GUARD.set(verify_sources)
    try:
        result = await mutate(db, owner, apply)
    finally:
        AUTHORIZATION_GUARD.reset(token)
    await _owner(db, owner, actor)
    return result
