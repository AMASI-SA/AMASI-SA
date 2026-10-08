"""Actual inventory purchase receipts in the temporary operational aggregate only.

MZ2 catalogues are read-only. Quantities are purchased/received quantities, not
the Accounting warehouse's available stock. Payments use the existing movement
API and its atomic allocations, bank assignment, custody and idempotency checks.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit
from fastapi import HTTPException
from operational_balance_store import read, mutate, fail, digest, now, claim_movement, replay, remember, audit
from operational_balance_service import active_gate, entity, money, fmt
from operational_balance_sources import rows, usable, instant
from operational_inventory_identity import line_identity, compatible_payload


def image_url(value):
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    return value if parsed.scheme in {'https', 'http'} and parsed.hostname and not parsed.username and not parsed.password else None


async def catalog(db, owner):
    result = []
    for collection, kind, identity in [('mezan_products_v2','product','mezan_product_id'),
                                        ('mezan_cost_resources_v2','component','id')]:
        seen = set()
        for row in await rows(db, owner, collection):
            if not usable(row) or row.get('archived') or row.get('deleted_at'):
                continue
            if kind == 'component' and row.get('track_inventory') is not True:
                continue
            key = str(row.get(identity) or '').strip()
            if not key or key in seen or not str(row.get('name') or '').strip():
                fail('inventory_catalog_ambiguous', 'هوية أو اسم المنتج غير مكتمل في ميزان 2')
            seen.add(key)
            base = {'id':key, 'kind':kind, 'name':str(row['name']),
                'sku':str(row.get('sku') or row.get('code') or ''),
                'image_url':image_url(row.get('main_image') if kind == 'product' else row.get('image_url')),
                'unit':str(row.get('unit') or 'piece')}
            variants = row.get('variants') if kind == 'product' else []
            if variants is None:
                variants = []
            if kind == 'product':
                count = row.get('variants_count')
                if count is None:
                    count = 0
                if type(count) is not int or count < 0 or not isinstance(variants, list) or count > len(variants):
                    fail('inventory_variants_incomplete', 'خيارات المنتج غير مكتملة في ميزان 2؛ أكمل بيانات المنتج قبل الشراء')
            if not variants:
                result.append(base)
                continue
            seen_variants = set()
            for variant in variants:
                if not isinstance(variant, dict):
                    fail('inventory_variant_ambiguous', 'خيارات المنتج غير مكتملة في ميزان 2')
                variant_id = str(variant.get('id') or '').strip()
                # These labels are canonical MZ2 normalized fields; do not read
                # a raw provider/Legacy fallback or accept a client-supplied name.
                label = str(variant.get('display_name') or variant.get('name') or '').strip()
                if not label:
                    selections = variant.get('selections') or []
                    label = ' — '.join(f"{s['name']}: {s['value']}" for s in selections
                        if isinstance(s, dict) and s.get('name') and s.get('value')) if isinstance(selections, list) else ''
                if not variant_id or variant_id in seen_variants or not label or label.isdigit():
                    fail('inventory_variant_ambiguous', 'هوية أو اسم خيار المنتج غير مكتمل في ميزان 2')
                seen_variants.add(variant_id)
                result.append({**base, 'variant_id':variant_id, 'variant_name':label,
                    'name':base['name']+' — '+label,
                    'sku':str(variant.get('sku') or base['sku']),
                    'image_url':image_url(variant.get('image')) or base['image_url']})
    return sorted(result, key=lambda row:(row['kind'], row['name'], row['id'], row.get('variant_id','')))


def project_inventory(state):
    from operational_supplier_adjustments import adjusted_gross
    obligations = state.setdefault('engine', {}).setdefault('obligations', {})
    for invoice in state.get('inventory_purchases', []):
        key = invoice['obligation_id']
        obligations[key] = {'id':key, 'kind':'supplier', 'party_type':'supplier',
            'party_id':invoice['supplier_id'], 'name':invoice['supplier_name'],
            'currency':'SAR', 'direction':'payable', 'expected':'0.00', 'confirmed':adjusted_gross(state,invoice),
            'business_date':invoice['invoice_date'], 'invoice_id':invoice['id'],
            'invoice_number':invoice['invoice_number'], 'net_amount':invoice['net'],
            'tax_amount':invoice['tax'], 'gross_amount':invoice['gross'],
            'label':'شراء مخزون — '+invoice['invoice_number']}


def purchase_view(state, invoice):
    from operational_supplier_adjustments import invoice_view
    return invoice_view(state,'inventory',invoice)


def inventory_view(state):
    stock = {}
    for invoice in state.get('inventory_purchases', []):
        for line in purchase_view(state,invoice)['lines']:
            key = (*line_identity(line), line['unit'])
            row = stock.setdefault(key, {k:line[k] for k in ('item_id','kind','name','image_url','unit','variant_id','variant_name') if k in line} | {'quantity':0,'returned_quantity':0,'remaining_quantity':0})
            row['quantity'] += line['quantity']
            row['returned_quantity'] += line['returned_quantity']
            row['remaining_quantity'] += line['remaining_quantity']
    return {'items':[purchase_view(state,p) for p in reversed(state.get('inventory_purchases', []))],
            'stock':list(stock.values())}


async def assert_no_source_collision(db, owner, state):
    """A later canonical invoice requires explicit reconciliation, never two debts."""
    keys = {(p['supplier_id'],p['invoice_number']) for p in state.get('inventory_purchases', [])}
    if keys and any((r.get('supplier_id'),str(r.get('invoice_number') or '').strip()) in keys
                    for r in await rows(db,owner,'mezan_supplier_invoices_v2')):
        fail('inventory_invoice_source_conflict', 'فاتورة المخزون أصبحت موجودة في ميزان 2؛ يلزم مطابقتها قبل تحديث الأرصدة')


async def save_purchase(db, owner, actor, payload, *, source='mezan2', clock=None):
    payload = compatible_payload(payload)
    stamp = clock or now()
    await claim_movement(db, owner, actor, {**payload, '_operation':'inventory_purchase'})
    async def apply(state):
        prior = replay(state, 'inventory_purchase:'+actor, payload)
        if prior is not None:
            return prior
        await active_gate(db, owner, state)
        supplier = await entity(db, owner, 'supplier', payload['supplier_id'], 'SAR')
        ref = payload['invoice_number'].strip()
        try:
            issued = date.fromisoformat(payload['invoice_date'])
            if not ref or issued > instant(stamp).date() or issued < instant(state['started_at']).date():
                raise ValueError()
        except (ValueError, TypeError):
            fail('inventory_invoice_date_invalid', 'أدخل رقم وتاريخ فاتورة ضمن فترة التشغيل')
        invoices = state.setdefault('inventory_purchases', [])
        other = [p for c in state.get('customer_exchanges', []) for p in c['purchases']]
        if any(p['supplier_id']==supplier['id'] and p['invoice_number']==ref for p in invoices+other):
            fail('inventory_invoice_duplicate', 'فاتورة المورد مسجلة بالفعل')
        if any(r.get('supplier_id')==supplier['id'] and str(r.get('invoice_number') or '').strip()==ref
               for r in await rows(db, owner, 'mezan_supplier_invoices_v2')):
            fail('inventory_invoice_existing_mz2', 'الفاتورة موجودة في ميزان 2؛ لا يمكن تسجيلها مرة أخرى')
        available = {line_identity(r):r for r in await catalog(db, owner)}
        lines = payload['lines']
        if not lines or len({line_identity(l) for l in lines}) != len(lines):
            fail('inventory_lines_invalid', 'اختر المنتجات دون تكرار البنود')
        normalized = []; totals = {key:Decimal(0) for key in ('net','tax','gross')}
        for line in lines:
            item = available.get(line_identity(line))
            quantity = line['quantity']
            if item is None:
                fail('inventory_item_not_mz2', 'المنتج أو خياره أو المكون غير متاح في ميزان 2؛ اختر الخيار الصحيح')
            if type(quantity) is not int or quantity <= 0 or quantity > 100000:
                fail('inventory_quantity_invalid', 'أدخل عدد وحدات صحيحًا أكبر من صفر', 422)
            unit_price = money(line['unit_price']); net = money(unit_price * quantity)
            tax = money(line['tax'], zero=True); gross = money(net + tax)
            values = {'net':net,'tax':tax,'gross':gross}
            for key, value in values.items():
                totals[key] += value
                money(totals[key], zero=True)
            normalized.append({**item,'item_id':item['id'],'quantity':quantity,
                'unit_price':fmt(unit_price), **{key:fmt(value) for key,value in values.items()}})
        identity = digest(['inventory-invoice',owner,supplier['id'],ref])
        invoice = {'id':identity,'obligation_id':'inventory-purchase:'+identity,
            'supplier_id':supplier['id'],'supplier_name':supplier['name'],
            'invoice_number':ref,'invoice_date':issued.isoformat(),'lines':normalized,
            **{key:fmt(value) for key,value in totals.items()}, 'currency':'SAR',
            'actor_id':actor,'recorded_at':stamp,'source':source,'note':payload.get('note',''),
            'receipt_basis':'actual_supplier_invoice_operational_only'}
        invoices.append(invoice)
        project_inventory(state)
        audit(state,actor,'inventory_purchase_saved',None,invoice,'فاتورة شراء واستلام مخزون تشغيلية',source,stamp)
        result = purchase_view(state,invoice)
        remember(state,'inventory_purchase:'+actor,payload,result)
        return result
    try:
        return await mutate(db,owner,apply)
    except HTTPException as exc:
        if isinstance(exc.detail,dict) and exc.detail.get('code') not in {'operational_request_conflict','operational_request_scope_conflict','operational_concurrent_change'}:
            exc.detail['not_applied']=True
        raise
