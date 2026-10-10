"""Supplier accepted credits against recorded operational invoices; no cash movement."""
from copy import deepcopy
from datetime import date
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from operational_balance_store import mutate, fail, digest, now, claim_movement, replay, remember, audit
from operational_balance_service import active_gate, entity, money, fmt
from operational_balance_sources import instant
from operational_inventory_identity import line_identity, variant_fields, compatible_payload


def invoices(state):
    for invoice in state.get('inventory_purchases', []):
        yield 'inventory', invoice
    for case in state.get('customer_exchanges', []):
        for invoice in case['purchases']:
            yield 'exchange', invoice


def credits(state, invoice_id):
    return sum((Decimal(a['gross']) for a in state.get('supplier_adjustments', []) if a['invoice_id']==invoice_id), Decimal(0))


def adjusted_gross(state, invoice):
    return fmt(Decimal(invoice['gross'])-credits(state, invoice['id']))


def invoice_view(state, kind, invoice):
    result=deepcopy(invoice)
    result['invoice_kind']=kind
    result['invoice_id']=invoice['id']
    result['obligation_id']=invoice.get('obligation_id') or 'exchange-purchase:'+invoice['id']
    adjustments=[a for a in state.get('supplier_adjustments', []) if a['invoice_id']==invoice['id']]
    # A replacement invoice records order-item identity; display the matching
    # immutable case snapshot, never a catalogue guess or Legacy lookup.
    parent_items=[]
    if kind=='exchange':
        parents=[case for case in state.get('customer_exchanges', [])
                 if any(p['id']==invoice['id'] for p in case['purchases'])]
        if len(parents)==1:
            parent_items=parents[0]['items']
    for line in result['lines']:
        line['kind']=line.get('kind','product')
        if kind=='exchange':
            snapshots=[item for item in parent_items if item['id']==line['item_id']]
            snapshot=snapshots[0] if len(snapshots)==1 else {}
            line['name']=line.get('name') or snapshot.get('name')
            line['product_id']=line.get('product_id') or snapshot.get('product_id')
            from operational_balance_inventory import image_url
            line['image_url']=image_url(line.get('image_url') or snapshot.get('image_url'))
        used=[l for a in adjustments for l in a['lines'] if line_identity(l)==line_identity(line)]
        line['returned_quantity']=sum(l['quantity'] for l in used)
        line['remaining_quantity']=line['quantity']-line['returned_quantity']
        for key in ('net','tax','gross'):
            line['remaining_'+key]=fmt(Decimal(line[key])-sum((Decimal(l[key]) for l in used),Decimal(0)))
    paid=sum((Decimal(a['amount']) for m in state['movements'] for a in m.get('allocations',[]) if a['obligation_id']==result['obligation_id']),Decimal(0))
    balance=Decimal(adjusted_gross(state,invoice))-paid
    result.update(adjustments=deepcopy(adjustments), adjusted_gross=adjusted_gross(state,invoice), settled=fmt(paid), outstanding=fmt(max(balance,Decimal(0))), credit=fmt(max(-balance,Decimal(0))))
    return result


def lookup(state, supplier_id, number):
    matches=[(k,i) for k,i in invoices(state) if i['supplier_id']==supplier_id and i['invoice_number']==number.strip()]
    if len(matches)!=1: fail('supplier_adjustment_invoice_missing','حدد فاتورة تشغيلية واحدة للمورد',404)
    return invoice_view(state,*matches[0])


def rounded(value):
    return value.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)


async def save_adjustment(db,owner,actor,payload,*,source='mezan2',clock=None):
    payload=compatible_payload(payload)
    stamp=clock or now()
    await claim_movement(db,owner,actor,{**payload,'_operation':'supplier_adjustment'})
    async def apply(state):
        prior=replay(state,'supplier_adjustment:'+actor,payload)
        if prior is not None:return prior
        await active_gate(db,owner,state)
        matches=[(k,i) for k,i in invoices(state) if i['id']==payload['invoice_id']]
        if len(matches)!=1:fail('supplier_adjustment_invoice_missing','الفاتورة التشغيلية غير موجودة',404)
        kind,invoice=matches[0]
        await entity(db,owner,'supplier',invoice['supplier_id'],'SAR')
        reference=payload['reference'].strip()
        try:
            day=date.fromisoformat(payload['business_date'])
            if not reference or day<date.fromisoformat(invoice['invoice_date']) or day>instant(stamp).astimezone(ZoneInfo('Asia/Riyadh')).date():raise ValueError()
        except (ValueError,TypeError):fail('supplier_adjustment_date_invalid','أدخل مرجع قبول المورد وتاريخًا صحيحًا')
        adjustments=state.setdefault('supplier_adjustments',[])
        if any(a['invoice_id']==invoice['id'] and a['reference']==reference for a in adjustments):fail('supplier_adjustment_duplicate','واقعة المورد مسجلة بالفعل')
        if payload.get('accepted') is not True:fail('supplier_adjustment_not_accepted','يلزم قبول المورد الفعلي')
        view=invoice_view(state,kind,invoice); normalized=[]
        if payload['kind']=='return':
            lines=payload.get('lines',[])
            if payload.get('amount') is not None or not lines or len({line_identity(l) for l in lines})!=len(lines):fail('supplier_return_lines_invalid','حدد بنود الإرجاع دون تكرار')
            for requested in lines:
                line=next((l for l in view['lines'] if line_identity(l)==line_identity(requested)),None)
                q=requested['quantity']
                if line is None or type(q) is not int or q<=0 or q>line['remaining_quantity']:fail('supplier_return_quantity_exceeded','كمية الإرجاع تتجاوز المتبقي')
                ratio=Decimal(q)/line['remaining_quantity']
                gross=rounded(Decimal(line['remaining_gross'])*ratio)
                net=min(gross,rounded(Decimal(line['remaining_net'])*ratio))
                normalized.append({'item_id':line['item_id'],'kind':line['kind'],**variant_fields(line),'quantity':q,'net':fmt(net),'tax':fmt(gross-net),'gross':fmt(gross)})
        elif payload['kind']=='discount':
            if payload.get('lines'):fail('supplier_discount_lines_invalid','الخصم مبلغ ثابت على الفاتورة')
            amount=money(payload.get('amount')); remaining=Decimal(view['adjusted_gross'])
            if amount>remaining:fail('supplier_credit_exceeded','الخصم يتجاوز قيمة الفاتورة المتبقية')
            eligible=[l for l in view['lines'] if Decimal(l['remaining_gross'])>0]
            left=amount; weight=remaining
            for index,line in enumerate(eligible):
                gross=min(left,Decimal(line['remaining_gross']),rounded(left*Decimal(line['remaining_gross'])/weight)) if index<len(eligible)-1 else left
                net=min(gross,Decimal(line['remaining_net']),rounded(gross*Decimal(line['remaining_net'])/Decimal(line['remaining_gross'])))
                normalized.append({'item_id':line['item_id'],'kind':line['kind'],**variant_fields(line),'quantity':0,'net':fmt(net),'tax':fmt(gross-net),'gross':fmt(gross)})
                left-=gross
                weight-=Decimal(line['remaining_gross'])
        else:fail('supplier_adjustment_kind_invalid','نوع العملية غير صالح')
        totals={key:sum((Decimal(l[key]) for l in normalized),Decimal(0)) for key in ('net','tax','gross')}
        if totals['gross']>Decimal(view['adjusted_gross']):fail('supplier_credit_exceeded','القيمة تتجاوز المتبقي')
        record={'id':digest(['supplier-adjustment',owner,invoice['id'],reference]),'invoice_id':invoice['id'],'supplier_id':invoice['supplier_id'],'kind':payload['kind'],'reference':reference,'business_date':day.isoformat(),'accepted':True,'lines':normalized,**{k:fmt(v) for k,v in totals.items()},'actor_id':actor,'recorded_at':stamp,'source':source,'note':payload.get('note','')}
        adjustments.append(record)
        from operational_balance_inventory import project_inventory
        from operational_balance_exchanges import project_exchanges
        project_inventory(state);project_exchanges(state)
        audit(state,actor,'supplier_'+payload['kind'],None,record,'تخفيض التزام المورد المقبول',source,stamp)
        result=invoice_view(state,kind,invoice)
        remember(state,'supplier_adjustment:'+actor,payload,result)
        return result
    try:return await mutate(db,owner,apply)
    except HTTPException as exc:
        if isinstance(exc.detail,dict) and exc.detail.get('code') not in {'operational_request_conflict','operational_request_scope_conflict','operational_concurrent_change'}:exc.detail['not_applied']=True
        raise
