"""Owner-selected Aramex orders. Preview and commit share the canonical prepaid writer."""
from collections import Counter
from copy import deepcopy
from decimal import Decimal

from operational_aramex_status_shipping import owner_rule_active
from operational_balance_service import active_gate
from operational_balance_store import audit, digest, fail, mutate, now, read, remember, replay
from operational_courier_tariffs import with_tariffs
from operational_delivery_evidence import _instant
from operational_prepaid_shipping import approved_aramex_charge, apply_events

ACTION = 'aramex_order_status_reconciliation'


async def reconcile_selected(db, owner, actor, numbers, expected_scope, *, preview_hash=None, request_id=None):
    if not owner or owner != actor or expected_scope != digest([owner, actor]):
        fail('aramex_owner_scope_required', 'اعتماد أجرة أرامكس متاح لمالك المتجر', 403)
    numbers = sorted(set(numbers))
    if not 1 <= len(numbers) <= 250 or not all(n.isascii() and n.isdigit() and 1 <= len(n) <= 24 for n in numbers):
        fail('aramex_order_list_invalid', 'أدخل حتى 250 رقم طلب صحيحًا', 422)
    committing = preview_hash is not None
    command = {'request_id': request_id, 'numbers': numbers, 'preview_hash': preview_hash}

    async def run(state):
        await active_gate(db, owner, state)
        if committing and (previous := replay(state, ACTION, command)):
            return previous
        stamp = now()
        start, end = _instant(state['started_at']), _instant(stamp)
        settings = await db.mz2_shipping_setup_v2.find({'user_id': owner}, {'_id': 0}).limit(2).to_list(2)
        if len(settings) > 1:
            fail('aramex_setup_ambiguous', 'تحتاج إعدادات الشحن إلى مطابقة')
        setup, conflicts = with_tariffs(settings[0] if settings else {}, owner, state)
        rates = [r for r in setup.get('contracts', []) if r.get('party_id') == 'aramex' and
                 r.get('context') == 'order_completed' and r.get('payment_mode') == 'prepaid']
        if 'aramex' in conflicts or not rates:
            fail('aramex_approved_tariff_required', 'يلزم عقد أرامكس مقدم معتمد مرتبط بصندوق')
        accounts = await db.mz2_financial_accounts.find({'user_id': owner, 'id': {'$in': [
            r['cash_account']['id'] for r in rates if r.get('cash_account')] }}, {'_id': 0}).to_list(None)
        data = {'mz2_financial_accounts': accounts}
        ids = numbers + [int(n) for n in numbers if len(n) < 19]
        fields = ['id','reference_id','order_number','date','created_at','order_date','status','shipping',
                  'shipment','shipments','shipping_company','shipping_company_code','salla_shipping_current']
        projection = {'_id': 0, 'user_id': 1, 'order_number': 1, 'salla_order_id': 1, 'salla_shipping_current': 1,
                      **{'raw_by_source.salla_direct.' + f: 1 for f in fields}}
        query = {'user_id': owner, '$or': [{field: {'$in': ids}} for field in
                 ('order_number','raw_by_source.salla_direct.reference_id','raw_by_source.salla_direct.order_number')]}
        records = await db.unified_orders.find(query, projection).limit(2 * len(numbers) + 1).to_list(2 * len(numbers) + 1)
        groups, order_ids = {}, Counter()
        for source in records:
            raw = (source.get('raw_by_source') or {}).get('salla_direct') or {}
            number = str(raw.get('reference_id') or raw.get('order_number') or '')
            groups.setdefault(number, []).append((source, raw))
            order_ids[str(raw.get('id') or '')] += 1
        existing = {}
        for movement in state.get('movements', []):
            if movement.get('automatic_prepaid_shipping'):
                existing.setdefault(str(movement.get('order_number')), []).append(movement)
        funds = {r['cash_account']['id'] for r in rates if r.get('cash_account')}
        events, skipped, already = [], [], []
        for number in numbers:
            matches = groups.get(number, [])
            if number in existing:
                saved = existing[number]
                if (len(saved) == 1 and saved[0].get('party_id') == 'aramex' and saved[0].get('bank_id') in funds
                        and saved[0].get('direction') == 'outgoing'):
                    already.append(number)
                else:
                    skipped.append([number, 'existing_prepaid_movement_requires_review'])
                continue
            if len(matches) != 1:
                skipped.append([number, 'canonical_order_missing_or_duplicate'])
                continue
            source, raw = matches[0]
            oid = str(raw.get('id') or '')
            if not oid or order_ids[oid] != 1:
                skipped.append([number, 'canonical_order_identity_ambiguous'])
                continue
            try:
                current = source.get('salla_shipping_current') or raw.get('salla_shipping_current') or {}
                event = approved_aramex_charge(data, setup, source, raw, owner, start, end, current, rates)
                if event:
                    events.append(event)
                else:
                    skipped.append([number, 'salla_status_carrier_or_period_ineligible'])
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                code = str(exc)
                skipped.append([number, code if code.startswith('operational_') else 'invalid_source'])
        # Scope enrollment and both posting legs commit together. A preview only mutates its local copy.
        if not owner_rule_active(state, owner):
            state['aramex_order_status_rule'] = {'version': 1, 'approved_by': owner, 'approved_at': stamp,
                                                 'started_at': state['started_at']}
        before = len(state['movements'])
        state['engine'].setdefault('facts', {})
        state['engine'].setdefault('obligations', {})
        refs = {e['order_id']: e['order_number'] for e in events}
        apply_events(state, events, start, end, lambda code, oid: skipped.append([refs.get(oid, oid), code]))
        posted = state['movements'][before:]
        rows = sorted([[m['order_number'], m['order_id'], m['bank_id'], m['tariff_version_id'], m['amount'],
                        m.get('shipment_id'), m.get('waybill')] for m in posted])
        check = digest([owner, state['started_at'], rows, sorted(already), sorted(skipped)])
        result = {'preview_hash': check, 'applied': committing, 'count': len(rows), 'existing': len(already),
                  'total': format(sum((Decimal(r[4]) for r in rows), Decimal(0)), '.2f'), 'skipped': skipped}
        if committing:
            if check != preview_hash:
                fail('aramex_preview_changed', 'تغيرت نتيجة المطابقة؛ أعد عرضها قبل الاعتماد')
            audit(state, actor, ACTION, None, {'count': len(rows), 'total': result['total'], 'preview_hash': check},
                  'اعتماد أجرة أرامكس حسب حالة طلب سلة منذ بداية التشغيل ولو كان رصيد الصندوق سالبًا', 'mezan2', stamp)
            remember(state, ACTION, command, result)
        return result

    return await mutate(db, owner, run) if committing else await run(deepcopy(await read(db, owner)))
