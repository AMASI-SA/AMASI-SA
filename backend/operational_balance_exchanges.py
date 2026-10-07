"""Temporary replacement register for original orders; no order/inventory writers."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from fastapi import HTTPException
from operational_balance_store import read, mutate, fail, digest, now, claim_movement, replay, remember, audit
from operational_balance_service import active_gate, entity, money, fmt
from operational_balance_sources import rows, cost_components, unique_index, instant
from operational_customer_returns import order_view, shipping_quote


async def exchange_order(db, owner, number):
    order = await order_view(db, owner, number)
    originals = [s['raw_by_source']['salla_direct'] for s in await rows(db, owner, 'unified_orders')
                 if str((s.get('raw_by_source', {}).get('salla_direct') or {}).get('id')) == order['id']]
    if len(originals)!=1:fail('exchange_order_ambiguous','هوية الطلب الأصلي غير مكتملة أو متعارضة')
    raw = originals[0]
    data = {name: await rows(db, owner, name) for name in (
        'mezan_product_cost_profiles_v2', 'mezan_product_resource_bindings_v2',
        'mezan_product_option_cost_bindings_v2', 'mezan_cost_resources_v2')}
    profiles = unique_index(data['mezan_product_cost_profiles_v2'], 'salla_product_id')
    cases = (await read(db, owner)).get('customer_exchanges', [])
    for item in order['items']:
        source = next(i for i in raw['items'] if str(i.get('id') or i.get('item_id') or i.get('order_item_id')) == item['id'])
        product = str(source.get('parent_product_id') or source.get('product_id') or (source.get('product') or {}).get('id') or '')
        item['product_id'] = product
        item['remaining'] = item['quantity'] - sum(i['quantity'] for c in cases if c['order_id'] == order['id'] for i in c['items'] if i['id'] == item['id'])
        try:
            value, _ = cost_components(data, product, source, profiles.get(product, {}))
            item['unit_estimate'] = fmt(money(value, zero=True))
        except (ValueError, KeyError, TypeError, HTTPException):
            item['unit_estimate'] = None
    return order


def project_exchanges(state):
    obligations = state.setdefault('engine', {}).setdefault('obligations', {})
    for case in state.get('customer_exchanges', []):
        common = {'currency':'SAR', 'direction':'payable', 'exchange_id':case['id'], 'business_date':case['created_at'][:10]}
        expected, confirmed = Decimal(0), Decimal(0)
        for item in case['items']:
            bought = sum(l['quantity'] for p in case['purchases'] for l in p['lines'] if l['item_id']==item['id'])
            item['remaining_to_buy'] = item['quantity'] - bought
            cost = money(item['unit_estimate'], zero=True) * item['remaining_to_buy']
            expected += cost
            key = 'exchange-expected:' + case['id'] + ':' + item['id']
            obligations[key] = {**common, 'id':key,'kind':'supplier','party_type':'supplier','party_id':None,
                                'name':'تكلفة بدل غير مسندة','expected':fmt(cost),'confirmed':'0.00'}
        for invoice in case['purchases']:
            key = 'exchange-purchase:' + invoice['id']
            confirmed += money(invoice['gross'])
            obligations[key] = {**common, 'id':key, 'kind':'supplier','party_type':'supplier','party_id':invoice['supplier_id'],
                'name':invoice['supplier_name'],'expected':'0.00','confirmed':invoice['gross'],
                'net_amount':invoice['net'],'tax_amount':invoice['tax'],'gross_amount':invoice['gross'],
                'invoice_number':invoice['invoice_number'],'invoice_id':invoice['id']}
        ship = case['shipping']; key = 'exchange-shipping:' + case['id']
        done = ship['status']=='completed'
        obligations[key] = {**common,'id':key,'kind':'shipping','party_type':'courier','party_id':ship['id'],'name':ship['name'],
                            'expected':'0.00' if done else ship['amount'],'confirmed':ship['amount'] if done else '0.00'}
        contribution = sum((money(c['amount']) for c in case['contributions']), Decimal(0))
        case['summary'] = {'expected_products':fmt(expected),'confirmed_products':fmt(confirmed),
            'shipping':ship['amount'],'customer_contribution':fmt(contribution),
            'net_cost':fmt(expected+confirmed+money(ship['amount'],zero=True)-contribution)}
        case['purchase_status'] = 'purchased' if all(i['remaining_to_buy']==0 for i in case['items']) else ('partial' if case['purchases'] else 'pending')


async def add_contribution(db, owner, actor, state, case, payload, stamp):
    bank = await entity(db, owner, 'bank', payload['bank_id'], 'SAR')
    value = money(payload['amount']); ref = payload['reference'].strip()
    try:
        paid = instant(payload['paid_at'])
        if not ref or paid > instant(stamp) or paid < instant(case['order_created_at']):
            raise ValueError()
    except (ValueError, TypeError):
        fail('exchange_contribution_invalid', 'أدخل مرجع وتاريخ الدفع الفعلي الصحيح')
    all_contributions = [c for r in state['customer_exchanges'] for c in r['contributions']]
    if any(c['bank_id']==bank['id'] and c['reference']==ref for c in all_contributions):
        fail('exchange_contribution_duplicate', 'المساهمة مسجلة بالفعل')
    existing_id = payload.get('existing_movement_id')
    existing = next((m for m in state['movements'] if m['id']==existing_id), None) if existing_id else None
    if existing_id:
        if (not existing or existing.get('bank_kind')!='bank' or existing.get('bank_id')!=bank['id']
                or existing.get('direction')!='incoming' or existing.get('kind')!='collection'
                or existing.get('party_type')!='bank' or existing.get('allocations')
                or existing.get('automatic_order_bank') or existing.get('order_number')
                or existing.get('exchange_id') or existing.get('reference')!=ref
                or money(existing['amount'])!=value
                or any(c['movement_id']==existing_id for c in all_contributions)):
            fail('exchange_contribution_link_invalid', 'الحركة غير مؤهلة أو مرتبطة مسبقًا')
        movement_id = existing_id
    else:
        if any(m.get('bank_id')==bank['id'] and m.get('reference')==ref for m in state['movements']):
            fail('exchange_contribution_existing', 'الحركة موجودة؛ اربطها بالاستبدال بدل تسجيل وارد جديد')
        if any(r.get('refund_source_id')==bank['id'] and r.get('refund_reference')==ref for r in state.get('customer_returns', [])):
            fail('exchange_contribution_reference_conflict', 'المرجع مستخدم لاسترداد سابق')
        movement_id = digest([owner,'exchange-contribution',bank['id'],ref])
        state['movements'].append({'id':movement_id,'request_id':movement_id,'party_type':'bank','party_id':bank['id'],
            'bank_kind':'bank','bank_id':bank['id'],'bank_name':bank['name'],'name':bank['name'],
            'amount':fmt(value),'currency':'SAR','direction':'incoming','kind':'collection','allocations':[],
            'actual_fee_amount':'0.00','receipt_id':None,'reference':ref,'order_number':None,
            'original_order_number':case['order_number'],'exchange_id':case['id'],'source':'mezan2',
            'actor_id':actor,'occurred_at':stamp,'business_date':paid.date().isoformat(),
            'note':'مساهمة عميل في استبدال الطلب '+case['order_number']})
    case['contributions'].append({'movement_id':movement_id,'bank_id':bank['id'],'bank_name':bank['name'],
        'amount':fmt(value),'reference':ref,'paid_at':paid.isoformat(),'actor_id':actor,'linked_existing':bool(existing_id)})


async def save_exchange(db, owner, actor, payload, *, case_id=None, clock=None):
    stamp = clock or now()
    await claim_movement(db, owner, actor, {**payload,'_operation':'customer_exchange','_case_id':case_id})
    async def apply(state):
        previous = replay(state,'customer_exchange:'+actor,payload)
        if previous is not None:return previous
        await active_gate(db,owner,state)
        cases = state.setdefault('customer_exchanges',[])
        case = next((c for c in cases if c['id']==case_id),None) if case_id else None
        before = deepcopy(case)
        if case_id and not case:fail('exchange_missing','الاستبدال غير موجود')
        if not case_id:
            order = await exchange_order(db,owner,payload['order_number'])
            selected = payload['items']
            if not selected or len({i['id'] for i in selected})!=len(selected):fail('exchange_items_invalid','اختر المنتجات والكميات')
            items=[]
            for chosen in selected:
                item=next((i for i in order['items'] if i['id']==chosen['id']),None)
                used=sum(i['quantity'] for c in cases if c['order_id']==order['id'] for i in c['items'] if i['id']==chosen['id'])
                if not item or not isinstance(chosen['quantity'],int) or chosen['quantity']<=0 or used+chosen['quantity']>item['quantity']:
                    fail('exchange_quantity_exceeded','كمية الاستبدال تتجاوز المتبقي من الطلب الأصلي')
                if item['unit_estimate'] is None:fail('exchange_cost_incomplete','تكلفة المنتج غير مكتملة في MZ2')
                items.append({k:item[k] for k in ('id','name','product_id','unit_estimate')}|{'quantity':chosen['quantity']})
            ship=await shipping_quote(db,owner,'courier',payload['shipping_id'],stamp,order['order_number'])
            if digest(ship)!=payload['shipping_quote_hash']:fail('exchange_shipping_changed','أعد قراءة تكلفة الشحن قبل الحفظ')
            ship.update(status='pending',reference=payload.get('shipment_reference',''))
            if ship['reference'] and any(c['shipping']['id']==ship['id'] and c['shipping']['reference']==ship['reference'] for c in cases):
                fail('exchange_shipment_duplicate','الشحنة مرتبطة باستبدال آخر')
            case={'id':digest([owner,actor,payload['request_id']]),'order_id':order['id'],'order_number':order['order_number'],
                'order_created_at':order['created_at'],'created_at':stamp,'actor_id':actor,'items':items,
                'shipping':ship,'purchases':[],'contributions':[],'future_replacement_order_id':None}
            cases.append(case)
            if payload.get('contribution'):await add_contribution(db,owner,actor,state,case,payload['contribution'],stamp)
        elif payload['action']=='contribution':
            await add_contribution(db,owner,actor,state,case,payload['contribution'],stamp)
        elif payload['action']=='shipping_completed':
            ref=payload['shipment_reference'].strip()
            if not ref:fail('exchange_shipment_reference_required','أدخل مرجع شحنة البدل المنفذة')
            if case['shipping']['status']=='completed' and case['shipping']['reference']!=ref:
                fail('exchange_shipment_immutable','الشحنة مؤكدة بالفعل')
            if any(c['id']!=case['id'] and c['shipping']['id']==case['shipping']['id'] and c['shipping']['reference']==ref for c in cases):
                fail('exchange_shipment_duplicate','الشحنة مرتبطة باستبدال آخر')
            if case['shipping']['status']!='completed':
                case['shipping'].update(status='completed',reference=ref,confirmed_at=stamp,confirmed_by=actor)
        elif payload['action']=='purchase':
            supplier=await entity(db,owner,'supplier',payload['supplier_id'],'SAR')
            ref=payload['invoice_number'].strip()
            try:
                issued=date.fromisoformat(payload['invoice_date'])
                if not ref or issued>instant(stamp).date() or issued<instant(case['order_created_at']).date():raise ValueError()
            except (TypeError,ValueError):fail('exchange_invoice_invalid','أدخل رقم وتاريخ الفاتورة الفعلية الصحيح')
            if any(p['supplier_id']==supplier['id'] and p['invoice_number']==ref for c in cases for p in c['purchases']):
                fail('exchange_invoice_duplicate','فاتورة المورد مسجلة بالفعل')
            if any(i.get('supplier_id')==supplier['id'] and str(i.get('invoice_number'))==ref for i in await rows(db,owner,'mezan_supplier_invoices_v2')):
                fail('exchange_invoice_existing_mz2','الفاتورة موجودة في MZ2؛ يلزم ربطها دون إنشاء التزام ثانٍ')
            lines=payload['lines']
            if not lines or len({l['item_id'] for l in lines})!=len(lines):fail('exchange_purchase_items_invalid','اختر منتجات الفاتورة')
            totals={k:Decimal(0) for k in ('net','tax','gross')};normalized=[]
            for line in lines:
                item=next((i for i in case['items'] if i['id']==line['item_id']),None)
                used=sum(l['quantity'] for p in case['purchases'] for l in p['lines'] if l['item_id']==line['item_id'])
                if not item or not isinstance(line['quantity'],int) or line['quantity']<=0 or used+line['quantity']>item['quantity']:
                    fail('exchange_purchase_quantity_exceeded','الكمية مشتراة بالفعل أو تتجاوز المتبقي')
                values={k:money(line[k],zero=k=='tax') for k in totals}
                if values['net']+values['tax']!=values['gross']:fail('exchange_invoice_totals','صافي البند وضريبته لا يطابقان إجماليه')
                for k in totals:totals[k]+=values[k]
                normalized.append({**line,**{k:fmt(v) for k,v in values.items()}})
            if any(totals[k]!=money(payload[k],zero=k=='tax') for k in totals):fail('exchange_invoice_totals','إجمالي الفاتورة لا يطابق البنود')
            case['purchases'].append({'id':digest([owner,supplier['id'],ref]),'supplier_id':supplier['id'],'supplier_name':supplier['name'],
                'invoice_number':ref,'invoice_date':issued.isoformat(),'lines':normalized,**{k:fmt(v) for k,v in totals.items()},
                'actor_id':actor,'recorded_at':stamp,'source':'actual_supplier_invoice_operational_only'})
        else:fail('exchange_action_invalid','إجراء غير مسموح')
        project_exchanges(state)
        audit(state,actor,'customer_exchange_saved',before,case,'استبدال مرتبط بالطلب الأصلي','mezan2',stamp)
        remember(state,'customer_exchange:'+actor,payload,case)
        return case
    try:return await mutate(db,owner,apply)
    except HTTPException as exc:
        if isinstance(exc.detail,dict) and exc.detail.get('code') not in {'operational_request_conflict','operational_request_scope_conflict','operational_concurrent_change'}:
            exc.detail['not_applied']=True
        raise
