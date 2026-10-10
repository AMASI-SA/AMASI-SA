"""Actual inventory purchase receipts in the temporary operational aggregate only.

MZ2 catalogues are read-only. Quantities are purchased/received quantities, not
the Accounting warehouse's available stock. Payments use the existing movement
API and its atomic allocations, bank assignment, custody and idempotency checks.
"""
from copy import deepcopy
import unicodedata
from datetime import date
from zoneinfo import ZoneInfo
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


def customization_contract(row, variant=None):
    """Only normalized MZ2 options; never infer text fields from labels/raw data."""
    options = row.get('options', [])
    fields = []; issues = []; seen = set()
    if not isinstance(options, list) or type(row.get('options_count', len(options))) is not int or row.get('options_count', len(options)) < 0 or row.get('options_count', len(options)) > len(options):
        return [], ['خيارات المنتج غير مكتملة في ميزان 2']
    for option in options:
        if not isinstance(option, dict):
            issues.append('خيار منتج غير مكتمل في ميزان 2'); continue
        key = option.get('id'); name = option.get('name'); kind = option.get('type')
        if not isinstance(key, str) or not key.strip() or len(key) > 200 or key in seen or not isinstance(name, str) or not name.strip() or not isinstance(kind, str) or type(option.get('required')) is not bool:
            issues.append('هوية أو اسم أو إلزام خيار المنتج غير مكتمل'); continue
        seen.add(key)
        if kind in {'text', 'textarea', 'string'}:
            fields.append({'id':key, 'name':name, 'type':kind, 'required':option['required']})
        elif kind in {'select', 'radio', 'checkbox', 'color', 'image'}:
            selections = (variant or {}).get('selections', [])
            choices = option.get('values', [])
            matches = [selection for selection in selections if isinstance(selection, dict)
                and (selection.get('option_id') == key or selection.get('name') == name)] if isinstance(selections, list) else []
            covered = False
            if len(matches) == 1 and isinstance(choices, list):
                selected = matches[0]
                covered = any(isinstance(choice, dict) and (
                    (isinstance(choice.get('id'), str) and bool(choice['id']) and selected.get('value_id') == choice['id']) or
                    (isinstance(choice.get('name'), str) and bool(choice['name']) and selected.get('value') == choice['name'])) for choice in choices)
            if not covered:
                issues.append('خيار '+name+' غير مثبت في تركيبة المنتج المختارة')
        else:
            issues.append('نوع خيار المنتج غير مدعوم: '+str(kind))
    return fields, issues


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
            if kind == 'product':
                base['customization_fields'], base['customization_issues'] = customization_contract(row)
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
                fields, issues = customization_contract(row, variant)
                result.append({**base, 'customization_fields':fields, 'customization_issues':issues, 'variant_id':variant_id, 'variant_name':label,
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
    result = invoice_view(state,'inventory',invoice)
    result['physical_stock_status'] = 'unproven'
    for line in result['lines']:
        line['physical_stock_status'] = 'unproven'
        line.setdefault('personalizations', [])
        # Original purchase distribution, not an attribution of subsequent returns.
        line['unallocated_quantity'] = line['quantity'] - sum(p['quantity'] for p in line['personalizations'])
    return result


def inventory_view(state):
    stock = {}
    for invoice in state.get('inventory_purchases', []):
        for line in purchase_view(state,invoice)['lines']:
            key = (*line_identity(line), line['unit'])
            row = stock.setdefault(key, {k:line[k] for k in ('item_id','kind','name','image_url','unit','variant_id','variant_name','purchase_line_key','purchase_configuration','purchase_option_labels','location','location_name','physical_stock_status') if k in line} | {'quantity':0,'returned_quantity':0,'remaining_quantity':0})
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


def personalize(line, quantity, item):
    allocations = line.get('personalizations', [])
    if not isinstance(allocations, list) or len(allocations) > 100:
        fail('inventory_personalizations_invalid', 'توزيع الخيارات غير صالح', 422)
    if allocations and line['kind'] != 'product':
        fail('inventory_personalizations_product_only', 'توزيع الخيارات للمنتجات فقط', 422)
    fields = {f['id']:f for f in item.get('customization_fields', [])}
    if allocations and (not fields or item.get('customization_issues')):
        fail('inventory_customization_unavailable', 'خيارات التخصيص غير متاحة أو غير مكتملة في ميزان 2', 422)
    normalized = []; combinations = set()
    for allocation in allocations:
        if not isinstance(allocation, dict) or set(allocation) not in ({'name', 'quantity'}, {'values', 'quantity'}):
            fail('inventory_personalizations_invalid', 'توزيع الخيارات غير صالح', 422)
        count = allocation['quantity']
        old_name = 'name' in allocation
        if old_name:
            if len(fields) != 1 or next(iter(fields.values()))['type'] == 'textarea':
                fail('inventory_customization_required', 'حدد حقول المنتج المعتمدة', 422)
            values = [{'option_id':next(iter(fields)), 'value':allocation['name']}]
        else:
            values = allocation['values']
        if not isinstance(values, list) or not values or len(values) > 100:
            fail('inventory_customization_invalid', 'قيم خيارات المنتج غير صالحة', 422)
        selected = {}; snapshots = []
        for value in values:
            if not isinstance(value, dict) or set(value) != {'option_id','value'} or not isinstance(value['option_id'], str):
                fail('inventory_customization_invalid', 'قيم خيارات المنتج غير صالحة', 422)
            field = fields.get(value['option_id']); text = value['value']
            if field is None or value['option_id'] in selected or not isinstance(text, str) or any(unicodedata.category(c).startswith('C') and not c.isspace() for c in text):
                fail('inventory_customization_invalid', 'خيار المنتج أو قيمته غير صالح', 422)
            text = ' '.join(unicodedata.normalize('NFC', text).split())
            if not text or len(text) > (1000 if field['type'] == 'textarea' else 100):
                fail('inventory_customization_invalid', 'قيمة خيار المنتج فارغة أو أطول من المسموح', 422)
            selected[field['id']] = text.casefold()
            snapshots.append({'option_id':field['id'], 'option_name':field['name'], 'type':field['type'], 'value':text})
        if any(f['required'] and f['id'] not in selected for f in fields.values()):
            fail('inventory_customization_required', 'أكمل خيارات المنتج المطلوبة لهذا التوزيع', 422)
        combination = tuple(sorted(selected.items()))
        if combination in combinations or type(count) is not int or count <= 0 or count > 100000:
            fail('inventory_personalization_quantity_invalid', 'أدخل توزيعًا دون تكرار وكمية صحيحة أكبر من صفر', 422)
        combinations.add(combination)
        # Existing clients with a single canonical name field retain their readback.
        normalized.append({'name':snapshots[0]['value'], 'quantity':count} if old_name else {'quantity':count, 'values':snapshots})
    if sum(p['quantity'] for p in normalized) > quantity:
        fail('inventory_personalizations_exceed_quantity', 'توزيع الخيارات يتجاوز كمية الخيار المحدد', 422)
    return normalized


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
            if not ref or issued > instant(stamp).astimezone(ZoneInfo('Asia/Riyadh')).date() or issued < instant(state['started_at']).astimezone(ZoneInfo('Asia/Riyadh')).date():
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
        if not lines:
            fail('inventory_lines_invalid', 'اختر المنتجات دون تكرار البنود')
        from operational_purchase_metadata import products, locations, describe
        bases={(p['kind'],p['id']):p for p in await products(db,owner)}
        bases.update({(p['kind'],p['id']):p for p in available.values() if p['kind']=='component'})
        location_rows=await locations(db,owner)
        normalized = []; identities=set(); totals = {key:Decimal(0) for key in ('net','tax','gross')}
        for line in lines:
            item = await describe(db,owner,line,bases,location_rows) if line.get('purchase_configuration') else available.get(line_identity(line))
            if not line.get('purchase_configuration') and line.get('location_id'):
                location=next((l for l in location_rows if l['id']==line['location_id']),None)
                if not location:fail('inventory_purchase_location_invalid','اختر موقعًا معتمدًا في ميزان 2',422)
                if item:item={**item,'location_id':location['id'],'location':location,'location_name':location['name']}
            quantity = line['quantity']
            if item is None:
                fail('inventory_item_not_mz2', 'المنتج أو خياره أو المكون غير متاح في ميزان 2؛ اختر الخيار الصحيح')
            if type(quantity) is not int or quantity <= 0 or quantity > 100000:
                fail('inventory_quantity_invalid', 'أدخل عدد وحدات صحيحًا أكبر من صفر', 422)
            identity=line_identity(item)
            if identity in identities:fail('inventory_lines_invalid','اختر المنتجات دون تكرار البنود')
            identities.add(identity)
            personalizations = personalize(line, quantity, item)
            unit_price = money(line['unit_price']); net = money(unit_price * quantity)
            tax = money(line['tax'], zero=True); gross = money(net + tax)
            values = {'net':net,'tax':tax,'gross':gross}
            for key, value in values.items():
                totals[key] += value
                money(totals[key], zero=True)
            normalized.append({**item,'item_id':item['id'],'quantity':quantity,
                'personalizations':personalizations,
                'unallocated_quantity':quantity-sum(p['quantity'] for p in personalizations),
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
