"""Customer service return register. Only operational CAS writes; MZ2 read-only."""
from copy import deepcopy
from decimal import Decimal
from fastapi import HTTPException
from operational_balance_store import fail, digest, read, mutate, audit, replay, remember, claim_movement, now
from operational_balance_service import active_gate, entity, money, fmt
from operational_balance_sources import rows, instant, number, PAYMENT_METHODS, shipping_charge


def observed_salla_refund(raw):
    """Read-only Salla refund hints; never infer a payout, ledger debit or refund identity."""
    actions = raw.get('payment_actions') if isinstance(raw.get('payment_actions'), dict) else {}
    refund = actions.get('refund_action') if isinstance(actions.get('refund_action'), dict) else {}
    transactions = refund.get('refunded_transactions')
    if transactions is None:
        return {'amount': '0.00', 'count': 0, 'status': 'not_observed'}
    if not isinstance(transactions, list) or len(transactions) > 100:
        return {'amount': None, 'count': 0, 'status': 'invalid_evidence'}
    amount = Decimal(0)
    try:
        for row in transactions:
            if not isinstance(row, dict):
                raise ValueError('refund_entry_invalid')
            raw_amount = row.get('amount')
            if not isinstance(raw_amount, dict) or raw_amount.get('currency') != 'SAR':
                raise ValueError('refund_currency_unverified')
            value = number(raw_amount)
            if value <= 0 or value != value.quantize(Decimal('.01')):
                raise ValueError('refund_amount_unverified')
            amount += value
    except (ValueError, TypeError, KeyError):
        return {'amount': None, 'count': 0, 'status': 'invalid_evidence'}
    return {'amount': fmt(amount), 'count': len(transactions),
            'status': 'observed_requires_execution_evidence' if transactions else 'not_observed'}


async def order_view(db, owner, number_text):
    matches = []
    for source in await rows(db, owner, 'unified_orders'):
        raw = (source.get('raw_by_source') or {}).get('salla_direct')
        if isinstance(raw, dict) and str(raw.get('reference_id') or raw.get('order_number') or '') == number_text:
            if (source.get('g47_salla_snapshot') or {}).get('requires_authoritative_refresh'):
                fail('customer_return_order_stale', 'بيانات الطلب تحتاج تحديثًا معتمدًا')
            matches.append(raw)
    if len(matches) != 1:
        fail('customer_return_order_missing', 'الطلب غير موجود أو هويته متعارضة في MZ2')
    raw = matches[0]
    items = []
    for item in raw.get('items', []):
        identity = str(item.get('id') or item.get('item_id') or item.get('order_item_id') or '')
        quantity = number(item.get('quantity'))
        if not identity or quantity <= 0 or quantity != int(quantity):
            fail('customer_return_items_incomplete', 'هوية المنتجات أو كمياتها غير مكتملة')
        items.append({'id': identity, 'name': item.get('name') or (item.get('product') or {}).get('name') or 'منتج', 'quantity': int(quantity)})
    if not items or len({i['id'] for i in items}) != len(items) or not raw.get('id'):
        fail('customer_return_items_incomplete', 'بيانات منتجات الطلب غير مكتملة')
    total = number((raw.get('amounts') or {}).get('total', raw.get('total')))
    payment = raw.get('payment') if isinstance(raw.get('payment'), dict) else {}
    method = str(payment.get('method') or raw.get('payment_method') or '').lower()
    provider = next((p for p, aliases in PAYMENT_METHODS.items() if method in aliases), None)
    total_field = (raw.get('amounts') or {}).get('total')
    currency = raw.get('currency') or (total_field.get('currency') if isinstance(total_field, dict) else None)
    if currency != 'SAR':
        fail('customer_return_currency_incomplete', 'هذه الصفحة تتطلب طلبًا بالريال السعودي')
    state = await read(db, owner)
    cases = [r for r in state.get('customer_returns', []) if r['order_id'] == str(raw['id'])]
    for item in items:
        # Documenting a Salla refund never consumes physical product quantities.
        item['remaining'] = item['quantity'] - sum(
            i['quantity'] for r in cases if r.get('case_type', 'item_return') == 'item_return'
            for i in r['items'] if i['id'] == item['id'])
    shipping = raw.get('salla_shipping_current') or raw.get('shipping') or {}
    documented = sum((money(r['amount']) for r in cases
                      if r['status'] == 'salla_refund_recorded'), Decimal(0))
    return {'id': str(raw['id']), 'order_number': number_text, 'currency': currency, 'total': fmt(total),
            'shipping_at': shipping.get('delivered_at'),
            'items': items, 'provider': provider, 'created_at': instant(raw.get('date') or raw.get('created_at') or raw.get('order_date')).isoformat(),
            'refunded': fmt(sum((money(r['amount']) for r in cases if r['status'] == 'refunded'), Decimal(0))),
            # Informational only. This is not subtracted from Salla's current
            # order total or recognized again as a bank/provider cash movement.
            'salla_refunds_documented': fmt(documented),
            'salla_refund_observation': observed_salla_refund(raw)}


async def shipping_quote(db, owner, kind, identity, at, order_number):
    if kind == 'none':
        return {'kind': 'none', 'amount': '0.00'}
    party = await entity(db, owner, kind, identity, 'SAR')
    if kind == 'store_driver':
        return {'kind': kind, 'id': identity, 'name': party['name'], 'amount': '0.00'}
    order = await order_view(db, owner, order_number)
    facts = (await read(db, owner)).get('engine', {}).get('facts', {})
    original = facts.get('shipping:' + order['id'], {})
    component = facts.get('shipping:' + order['id'] + ':base_shipping')
    if original.get('id') == identity and original.get('party_type') == 'courier' and component is not None:
        return {'kind': kind, 'id': identity, 'name': party['name'], 'amount': fmt(money(component['cost'], zero=True)),
                'order_id': order['id'], 'source': 'confirmed_original_shipping'}
    # Reconstruct the original base service price only from dated MZ2 evidence.
    # Never substitute today's tariff or an aggregate that includes COD fees.
    at = original.get('delivered_at') or order.get('shipping_at')
    if not at:
        fail('customer_return_shipping_incomplete', 'تاريخ الشحن الأصلي غير مكتمل؛ لا يمكن تخمين تكلفة الاسترجاع')
    setups = await rows(db, owner, 'mz2_shipping_setup_v2')
    if len(setups) != 1:
        fail('customer_return_shipping_incomplete', 'عقد شركة الشحن غير مكتمل')
    rates = [r for r in setups[0].get('contracts', []) if r.get('status') == 'approved' and r.get('party_type') == 'courier'
             and r.get('party_id') == identity and r.get('context') == 'delivery'
             and instant(r['effective_from']) <= instant(at) and (not r.get('effective_to') or instant(at) < instant(r['effective_to']))]
    if len(rates) != 1:
        fail('customer_return_shipping_incomplete', 'لا يوجد عقد شحن واحد معتمد وساري')
    try:
        quote = shipping_charge(rates[0], owner, identity, instant(at), None, setups[0])
        base = next(p for p in quote['fee_components'] if p['id'] == 'base_shipping' and p['complete'])
    except (ValueError, KeyError, StopIteration):
        fail('customer_return_shipping_incomplete', 'تكلفة الشحن المعتمدة غير مكتملة')
    return {'kind': kind, 'id': identity, 'name': party['name'], 'amount': fmt(base['amount']), 'contract_id': rates[0]['id']}


def attach_returns(state, sources):
    """Feed platform refunds through the same provider projection, never a bank debit."""
    for case in state.get('customer_returns', []):
        if case['status'] != 'refunded' or case['refund_source_type'] != 'provider':
            continue
        order = next((o for o in sources.get('orders', []) if o['id'] == case['order_id']), None)
        if order is None:
            continue
        payment = order.setdefault('payment', deepcopy(case['payment_snapshot']))
        refunds = payment.setdefault('refunds', [])
        if not any(str(r['id']) == case['refund_reference'] for r in refunds):
            refunds.append({'id': case['refund_reference'], 'amount': case['amount'], 'status': 'executed'})
    return sources


def shipping_obligations(state):
    obligations = state.setdefault('engine', {}).setdefault('obligations', {})
    for case in state.get('customer_returns', []):
        shipping = case['shipping']
        if shipping['kind'] != 'courier':
            continue
        key = 'customer-return-shipping:' + case['id']
        confirmed = shipping['status'] == 'completed'
        obligations[key] = {'id': key, 'kind': 'shipping', 'party_type': 'courier', 'party_id': shipping['id'], 'name': shipping['name'],
                            'currency': 'SAR', 'direction': 'payable', 'expected': '0.00' if confirmed else shipping['amount'],
                            'confirmed': shipping['amount'] if confirmed else '0.00', 'business_date': case['created_at'][:10],
                            'return_case_id': case['id'], 'shipment_reference': shipping['reference']}


async def save_case(db, owner, actor, payload, *, case_id=None, clock=None):
    stamp = clock or now()
    await claim_movement(db, owner, actor, {**payload, '_operation': 'customer_return', '_case_id': case_id})
    async def apply(state):
        prior = replay(state, 'customer_return:' + actor, payload)
        if prior is not None:
            return prior
        await active_gate(db, owner, state)
        cases = state.setdefault('customer_returns', [])
        existing = next((r for r in cases if r['id'] == case_id), None) if case_id else None
        if case_id and not existing:
            fail('customer_return_missing', 'المرتجع غير موجود')
        if payload.get('case_type', 'item_return') == 'order_compensation':
            if case_id is not None or payload['status'] != 'salla_refund_recorded':
                fail('customer_compensation_mode_invalid',
                     'التعويض الذي سبق تنفيذه بسلة يُوثق كعملية مستقلة فقط', 422)
            if (payload.get('already_refunded_in_salla') is not True
                    or payload.get('items') or payload.get('shipping_kind') != 'none'
                    or payload.get('shipping_id') or payload.get('shipment_reference')
                    or payload.get('shipment_completed') or payload.get('shipping_quote_hash')
                    or payload.get('refund_source_type') is not None
                    or payload.get('refund_source_id') is not None):
                fail('customer_compensation_not_record_only',
                     'توثيق تعويض سلة لا يتضمن منتجًا أو شحنًا أو حركة بنك أو منصة جديدة', 422)
            note = (payload.get('note') or '').strip()
            ref = (payload.get('refund_reference') or '').strip()
            if len(note) < 5:
                fail('customer_compensation_reason_required', 'أدخل سبب التعويض والاتفاق مع العميل', 422)
            if len(ref) < 3:
                fail('customer_compensation_reference_required',
                     'أدخل مرجع الاسترداد السابق في سلة لعدم تكرار توثيقه', 422)
            value = money(payload.get('amount'))
            try:
                refunded_at = instant(payload.get('refunded_at'))
            except (ValueError, TypeError):
                fail('customer_compensation_date_invalid', 'أدخل تاريخ تنفيذ الاسترداد في سلة', 422)
            order = await order_view(db, owner, payload['order_number'])
            if refunded_at > instant(stamp) or refunded_at < instant(order['created_at']):
                fail('customer_compensation_date_invalid', 'تاريخ الاسترداد غير صحيح', 422)
            if any((row['order_id'] == order['id']
                    and row.get('refund_reference') == ref)
                   for row in cases):
                fail('customer_compensation_duplicate', 'مرجع الاسترداد مسجل على هذا الطلب مسبقًا', 409)
            case = {
                'id': digest([owner, actor, payload['request_id']]),
                'case_type': 'order_compensation',
                'order_id': order['id'],
                'order_number': order['order_number'],
                'order_total_after_refund_snapshot': order['total'],
                'items': [],
                'currency': 'SAR',
                'status': 'salla_refund_recorded',
                'amount': fmt(value),
                'refund_reference': ref,
                'refunded_at': refunded_at.isoformat(),
                'note': note,
                'shipping': {'kind': 'none', 'id': None, 'amount': '0.00',
                             'status': 'completed', 'reference': ''},
                'financial_effect': 'none_already_refunded_in_salla',
                'created_by': actor,
                'created_at': stamp,
            }
            cases.append(case)
            audit(state, actor, 'salla_customer_compensation_documented',
                  None, case, note, 'employee_app', stamp)
            remember(state, 'customer_return:' + actor, payload, case)
            # No bank debit, no payment-provider refund, no product return,
            # no stock mutation, and no update to Salla's already-net total.
            return case
        if payload.get('case_type', 'item_return') != 'item_return' or payload['status'] == 'salla_refund_recorded':
            fail('customer_return_mode_invalid', 'حالة رد المبلغ لا تطابق نوع العملية', 422)
        before = deepcopy(existing)
        if existing:
            if payload['order_number'] != existing['order_number'] or payload['items'] != existing['items']:
                fail('customer_return_immutable', 'لا يمكن تغيير الطلب أو المنتجات بعد تسجيل المرتجع')
            case = existing
        else:
            order = await order_view(db, owner, payload['order_number'])
            selected = payload['items']
            if not selected or len({i['id'] for i in selected}) != len(selected):
                fail('customer_return_selection_invalid', 'اختر منتجات وكميات صحيحة')
            for item in selected:
                original = next((r for r in order['items'] if r['id'] == item['id']), None)
                used = sum(i['quantity'] for r in cases if r['order_id'] == order['id'] for i in r['items'] if i['id'] == item['id'])
                if not original or item['quantity'] <= 0 or used + item['quantity'] > original['quantity']:
                    fail('customer_return_quantity_exceeded', 'الكمية سبق تسجيلها أو تتجاوز كمية الطلب')
            shipping = await shipping_quote(db, owner, payload['shipping_kind'], payload.get('shipping_id'), stamp, payload['order_number'])
            if shipping['kind'] != 'none' and payload.get('shipping_quote_hash') != digest(shipping):
                fail('customer_return_quote_changed', 'تغيرت تكلفة الاسترجاع؛ أعد قراءة التكلفة قبل الحفظ')
            shipping.update(reference=payload.get('shipment_reference', ''), status='completed' if payload.get('shipment_completed') else 'pending')
            if shipping['kind'] != 'none' and not shipping['reference']:
                fail('customer_return_shipment_reference_required', 'أدخل مرجع شحنة الاسترجاع')
            if shipping['kind'] != 'none' and any(r['shipping'].get('reference') == shipping['reference'] and r['shipping'].get('id') == shipping.get('id') for r in cases):
                fail('customer_return_shipment_duplicate', 'شحنة الاسترجاع مسجلة بالفعل')
            case = {'id': digest([owner, actor, payload['request_id']]), 'case_type': 'item_return',
                    'order_id': order['id'], 'order_number': order['order_number'],
                    'order_total': order['total'], 'order_created_at': order['created_at'], 'original_provider': order['provider'],
                    'items': deepcopy(selected), 'currency': 'SAR', 'status': 'pending', 'shipping': shipping,
                    'created_at': stamp, 'created_by': actor, 'note': payload.get('note', '')}
            cases.append(case)
        if payload.get('shipment_completed') and case['shipping']['kind'] != 'none':
            case['shipping']['status'] = 'completed'
        if payload['status'] == 'refunded' and case['status'] != 'refunded':
            current_order = await order_view(db, owner, case['order_number'])
            if current_order['id'] != case['order_id'] or current_order['total'] != case['order_total'] or current_order['provider'] != case['original_provider']:
                fail('customer_return_order_changed', 'تغيرت بيانات الطلب؛ يلزم مراجعة المرتجع قبل رد المبلغ')
            value = money(payload['amount'])
            paid = sum((money(r['amount']) for r in cases if r['order_id'] == case['order_id'] and r['status'] == 'refunded'), Decimal(0))
            if paid + value > money(case['order_total']):
                fail('customer_return_amount_exceeded', 'المبلغ يتجاوز المتبقي القابل للاسترداد')
            kind, identity = payload['refund_source_type'], payload['refund_source_id']
            source = await entity(db, owner, kind, identity, 'SAR')
            ref = payload['refund_reference'].strip()
            if not ref:
                fail('customer_return_reference_required', 'أدخل مرجع عملية رد المبلغ')
            if any(r['status'] == 'refunded' and r['refund_source_type'] == kind and r['refund_source_id'] == identity and r['refund_reference'] == ref for r in cases):
                fail('customer_return_refund_duplicate', 'عملية رد المبلغ مسجلة بالفعل')
            if kind == 'bank' and any(m.get('bank_id') == identity and m.get('reference') == ref and m.get('direction') == 'outgoing' for m in state['movements']):
                fail('customer_return_refund_duplicate', 'الاسترداد مسجل كحركة بنكية بالفعل')
            try:
                refunded_at = instant(payload['refunded_at'])
            except (ValueError, TypeError):
                fail('customer_return_date_invalid', 'أدخل تاريخ رد المبلغ', 422)
            if refunded_at > instant(stamp) or refunded_at < instant(case['order_created_at']):
                fail('customer_return_date_invalid', 'تاريخ رد المبلغ غير صحيح')
            if kind == 'provider':
                if identity != case['original_provider'] or instant(case['order_created_at']) <= instant(state['started_at']):
                    fail('customer_return_provider_mismatch', 'يلزم ربط الاسترداد بمنصة الطلب وبعد بداية النظام')
                from operational_balance_engine import provider_projection, RIYADH
                policies = [p for document in await rows(db, owner, 'mz2_provider_fee_policies_v2')
                            for p in document.get('policies', []) if p.get('user_id') in (None, owner)]
                payment = {'provider': identity, 'currency': 'SAR', 'gross': case['order_total'], 'cancelled': '0', 'refunds': [{'id':ref,'amount':fmt(value),'status':'executed'}]}
                try:
                    projection = provider_projection(payment, policies, instant(case['order_created_at']).astimezone(RIYADH).date())
                    policy = next(p for p in policies if p['id'] == projection['policy_id'])
                    if policy.get('refund_fee_treatment') != 'retain':
                        raise ValueError('refund_fee_not_retained')
                except (ValueError, KeyError, StopIteration):
                    fail('customer_return_fee_policy_incomplete', 'إعداد العمولة غير مكتمل أو لا يثبت بقاء عمولة الدفع')
                case['payment_snapshot'] = {k:v for k,v in payment.items() if k != 'refunds'}
            case.update(status='refunded', amount=fmt(value), refund_source_type=kind, refund_source_id=identity,
                        refund_source_name=source['name'], refund_reference=ref, refunded_at=refunded_at.isoformat(), confirmed_by=actor)
        elif case['status'] == 'refunded':
            # A second confirmation cannot change an executed refund or reverse it.
            if payload['status'] != 'refunded' or any(str(payload.get(k)) != str(case.get(k)) for k in ['amount','refund_source_type','refund_source_id','refund_reference']):
                fail('customer_return_already_refunded', 'رد المبلغ مؤكد؛ لا يمكن تغييره')
        if case['status'] == 'refunded' and case['refund_source_type'] == 'provider':
            from operational_balance_sources import collect_sources
            from operational_balance_engine import reconcile
            sources = await collect_sources(db, owner, state['started_at'], stamp, baselines=state.get('source_baselines'))
            sources.setdefault('supplier_returns', []).extend(state.get('supplier_returns', []))
            if not any(o['id'] == case['order_id'] for o in sources.get('orders', [])):
                fail('customer_return_order_unavailable', 'تعذر مطابقة الطلب قبل تسجيل الاسترداد')
            state.update(reconcile(state, attach_returns(state, sources), stamp))
            fact = state.get('engine', {}).get('facts', {}).get('refund:' + case['refund_source_id'] + ':' + case['refund_reference'])
            if fact != {'order_id': case['order_id'], 'amount': case['amount']}:
                fail('customer_return_provider_incomplete', 'إثبات الاسترداد غير مكتمل أو متعارض')
            if state.get('engine', {}).get('obligations', {}).get('provider:' + case['order_id'], {}).get('incomplete'):
                fail('customer_return_provider_incomplete', 'تعذر احتساب أثر الاسترداد على المنصة')
        shipping_obligations(state)
        audit(state, actor, 'customer_return_saved', before, case, payload.get('note', ''), 'mezan2', stamp)
        remember(state, 'customer_return:' + actor, payload, case)
        return case
    try:
        return await mutate(db, owner, apply)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get('code') not in {'operational_request_conflict', 'operational_request_scope_conflict', 'operational_concurrent_change'}:
            exc.detail['not_applied'] = True
        raise
