"""Owner-only physical opening stock; no accounting activation or journals.

The initial batch is immutable. Existing stock requires reconciliation, never
an overwrite. Recorded costs are operational evidence, not native valuation.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal

from operational_balance_store import digest, now, fail
from operational_purchase_metadata import products, locations, configuration
from product_inventory_rules import canonical_specifications, build_inventory_configuration_key
from inventory_write_gate import admitted, read_control, root_db, writes_paused
from operational_atomic import operational_owner

BATCHES = 'mz2_operational_inventory_openings_v1'
RECEIPTS = 'mezan_inventory_receipts_v2'
LOCATIONS = 'warehouse_locations'
SOURCE = 'operational_opening_inventory'
SCHEMA = 'operational-opening-inventory-v1'
PENDING = 'PENDING_FINANCIAL_VALUATION'


class OpeningLine(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    kind: Literal['product', 'component'] = 'product'
    item_id: str = Field(min_length=1, max_length=200)
    location_id: str = Field(min_length=1, max_length=200)
    quantity: int | float = Field(gt=0, le=100000)
    unit_cost: str = Field(min_length=1, max_length=30)
    purchase_configuration: dict

    @field_validator('quantity', mode='before')
    @classmethod
    def numeric_quantity(cls, value):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('opening_quantity_numeric_required')
        return value


class OpeningRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: str = Field(min_length=8, max_length=160)
    lines: list[OpeningLine] = Field(min_length=1, max_length=200)


class OpeningApproval(OpeningRequest):
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


async def owner_actor(db, user, expected=None):
    # Refresh the actual login identity, including mobile-shaped principals.
    actor = await db['users'].find_one({'id': user.get('_mobile_actor_id') or user.get('id')})
    if (not actor or actor.get('role') != 'owner' or actor.get('disabled')
            or actor.get('is_active') is False or actor.get('deleted_at')
            or expected is not None and actor['id'] != expected):
        fail('opening_inventory_owner_required', 'المخزون الافتتاحي متاح للمالك فقط', 403)
    return actor


def money(value):
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount <= 0 or amount > Decimal('1000000000') or amount != amount.quantize(Decimal('.01')):
            raise ValueError()
        return amount
    except (ValueError, InvalidOperation, TypeError):
        fail('opening_inventory_cost_invalid', 'أدخل تكلفة وحدة موجبة بمنزلتين عشريتين بحد أقصى', 422)


async def records(db, owner, name):
    result = await db[name].find({'user_id': owner}).to_list(20001)
    if len(result) > 20000:
        fail('opening_inventory_scope_incomplete', 'تتطلب بيانات المخزون مراجعة قبل الافتتاحية')
    return result


FRACTIONAL_UNITS = frozenset({'m', 'kg', 'meter', 'metre', 'kilogram', 'متر', 'كجم', 'كيلوجرام'})


def quantity_value(value, kind, unit):
    if type(value) not in (int, float) or not math.isfinite(value):
        fail('opening_inventory_quantity_invalid', 'الكمية غير صالحة', 422)
    amount = Decimal(str(value))
    precision = Decimal('.000001') if kind == 'component' and unit in FRACTIONAL_UNITS else Decimal(1)
    if amount <= 0 or amount > 100000 or amount != amount.quantize(precision):
        fail('opening_inventory_quantity_invalid', 'تحقق من دقة الكمية ووحدة المكون؛ المنتجات تسجل بوحدات صحيحة', 422)
    return amount


def stored_quantity(value):
    return int(value) if value == value.to_integral_value() else float(value)


def line_total(cost, quantity):
    return format((cost * Decimal(str(quantity))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP), '.2f')


async def components(db, owner):
    from collections import Counter
    from operational_balance_sources import usable
    from component_status_policy import component_is_active
    from operational_balance_inventory import image_url
    raw = await records(db, owner, 'mezan_cost_resources_v2')
    counts = Counter(row.get('id') for row in raw if isinstance(row.get('id'), str))
    result = []
    for row in raw:
        if (not usable(row) or not component_is_active(row) or row.get('deleted_at')
                or row.get('track_inventory') is not True or row.get('kind') not in (None, '', 'stock_component')):
            continue
        if not isinstance(row.get('id'), str) or not row['id'] or counts[row['id']] != 1:
            fail('opening_inventory_identity_ambiguous', 'هوية مكون المخزون مكررة أو غير صالحة', 409)
        unit = row.get('unit') or 'piece'
        if not isinstance(unit, str) or not unit.strip() or not row.get('name'):
            fail('opening_inventory_component_incomplete', 'اسم أو وحدة المكون غير مكتمل', 422)
        result.append({'id': row['id'], 'kind': 'component', 'name': row['name'],
            'image_url': image_url(row.get('image_url')), 'sku': str(row.get('code') or row.get('sku') or ''),
            # Native resource-catalog serialization derives this for older
            # rows from explicit track_inventory=True; unknown kinds stay out.
            'component_type': 'stock_component', 'unit': unit,
            'quantity_precision': 6 if unit in FRACTIONAL_UNITS else 0})
    return result


async def context(db, user):
    actor = await owner_actor(db, user)
    owner = actor['id']
    batch = await db[BATCHES].find_one({'_id': owner, 'user_id': owner})
    return {'products': await products(db, owner, opening_cached_options=True), 'components': await components(db, owner), 'locations': await locations(db, owner),
            'writer_disabled': writes_paused(await read_control(root_db(db), owner)),
            'approved': bool(batch), 'batch_id': (batch or {}).get('id'),
            'financial_effect': 'none', 'fulfillment_eligible': False,
            'fulfillment_reason': 'inventory_valuation_reconciliation_required'}


async def compile_preview(db, owner, request):
    if await db[BATCHES].find_one({'_id': owner, 'user_id': owner}):
        fail('opening_inventory_already_approved', 'اعتمد المخزون الافتتاحي مسبقًا؛ يلزم مسار تسوية مستقل')
    locs = await records(db, owner, LOCATIONS)
    # No ambiguous zero/negative legacy records or previous receipts can be
    # adopted as a new opening. This is intentionally stricter than addition.
    for location in locs:
        occupancy = location.get('occupancy')
        if occupancy is not None and (not isinstance(occupancy, dict)
                or occupancy.get('items') != []
                or type(occupancy.get('total_quantity', 0)) not in (int, float)
                or occupancy.get('total_quantity', 0) != 0):
            fail('opening_inventory_existing_stock', 'يوجد مخزون سابق؛ يلزم مطابقته قبل تسجيل افتتاحية')
    for name in (RECEIPTS, 'mezan_inventory_reservations_v2', 'mezan_component_consumption_units_v1',
                 'mz2_opening_inventory_initializations', 'mz2_inventory_cost_states'):
        if await db[name].find_one({'user_id': owner}):
            fail('opening_inventory_existing_stock', 'توجد حركة أو قيمة مخزون سابقة؛ يلزم مسار مطابقة')
    metadata = await products(db, owner, opening_cached_options=True)
    candidates = {(p['kind'], p['id']): p for p in metadata + await components(db, owner)}
    raw_products = await records(db, owner, 'mezan_products_v2')
    raw_components = await records(db, owner, 'mezan_cost_resources_v2')
    raw_warehouses = await records(db, owner, 'warehouse_locations_warehouses')
    available = {l['id']: l for l in await locations(db, owner)}
    rows = []; used = set(); capacity = {}; snapshots = []
    for line in request['lines']:
        product = candidates.get((line['kind'], line['item_id']))
        location = available.get(line['location_id'])
        if not product or not location:
            fail('opening_inventory_identity_invalid', 'اختر منتجًا وموقعًا متاحين من ميزان 2', 422)
        # Resolve against the full tenant catalog, not only visible metadata.
        # An archived/disabled duplicate is still an ambiguous physical identity
        # and would be excluded by the inventory projection after approval.
        source_rows, source_key = (raw_products, 'mezan_product_id') if line['kind'] == 'product' else (raw_components, 'id')
        product_matches = [p for p in source_rows if p.get(source_key) == product['id']]
        location_matches = [l for l in locs if l.get('id') == location['id']]
        warehouse_matches = [w for w in raw_warehouses if w.get('id') == location['warehouse_id']]
        if len(product_matches) != 1 or len(location_matches) != 1 or len(warehouse_matches) != 1:
            fail('opening_inventory_identity_ambiguous', 'هوية المنتج أو الموقع مكررة؛ يلزم تصحيحها قبل الافتتاحية', 409)
        raw = product_matches[0]
        stored = location_matches[0]
        warehouse = warehouse_matches[0]
        from operational_balance_sources import usable
        if not usable(warehouse) or warehouse.get('status') == 'disabled' or warehouse.get('deleted_at'):
            fail('opening_inventory_warehouse_unavailable', 'المستودع غير متاح؛ اختر موقعًا في مستودع نشط', 409)
        if stored.get('purpose') in {'returns', 'damaged'}:
            fail('opening_inventory_location_held', 'لا تسجل مخزونًا افتتاحيًا في موقع تالف أو مرتجعات', 422)
        if line['kind'] == 'product':
            if product.get('configuration_issues'):
                fail('opening_inventory_options_incomplete', 'أكمل خيارات المنتج في ميزان 2 قبل إدخال افتتاحيته', 422)
            definition = await db['mezan_bundle_definitions_v2'].find_one({'user_id': owner, 'product_id': product['id']})
            if (raw.get('product_type') in {'service', 'digital', 'composite', 'bundle'}
                    or raw.get('type') in {'service', 'digital'} or definition and definition.get('product_type') == 'composite'):
                fail('opening_inventory_nonphysical_product', 'هذا المنتج يتطلب مسار مكونات أو منتج غير مخزني', 422)
            config, labels = configuration(line['purchase_configuration'], product)
            normalized_names = [next(iter(canonical_specifications({v['name']: 'x'})), '') for v in labels]
            if len(set(normalized_names)) != len(normalized_names) or '' in normalized_names:
                fail('opening_inventory_ambiguous_options', 'أسماء خيارات المنتج متعارضة؛ يلزم تصحيح تعريفها', 422)
            variants = raw.get('variants') or []
            if not isinstance(variants, list) or raw.get('variants_count', len(variants)) != len(variants):
                fail('opening_inventory_options_incomplete', 'تركيبات المنتج غير مكتملة', 422)
            variant = None
            if variants:
                from operational_purchase_metadata import combinations
                matches = []
                for candidate in variants:
                    combos = combinations({**raw, 'variants': [candidate], 'variants_count': 1}, product['options'])
                    variant_options = {v['option_id'] for v in combos[0]} if combos else set()
                    selected_variant = [v for v in config['selections'] if v['option_id'] in variant_options]
                    if combos and sorted(combos[0], key=lambda v: v['option_id']) == selected_variant:
                        matches.append(candidate)
                if len(matches) != 1 or not isinstance(matches[0].get('id'), str) or not matches[0]['id']:
                    fail('opening_inventory_variant_required', 'اختر تركيبة منتج واحدة مثبتة في ميزان 2', 422)
                variant = matches[0]['id']
            state = 'ready_complete' if config['state'] == 'ready' else 'requires_preparation'
            specs = canonical_specifications({v['name']: v.get('value', '') for v in labels})
            sku = str((matches[0].get('sku') if variants else None) or raw.get('sku') or product['id'])
            key = build_inventory_configuration_key(sku=sku, preparation_state=state, specifications=specs)
            identity = {'item_type': 'product', 'product_id': product['id'], 'mezan_product_id': product['id'],
                        'salla_variant_id': variant, 'sku': sku, 'configuration_key': key}
        else:
            config = line['purchase_configuration']
            if config != {'state': 'raw', 'selections': [], 'inputs': []}:
                fail('opening_inventory_component_configuration', 'المكون المخزني يسجل خامًا دون تخصيصات المنتج', 422)
            state = 'requires_preparation'; specs = {}; definition = None
            sku = product['sku']
            identity = {'item_type': 'stock_component', 'resource_id': product['id'],
                        'sku': sku, 'configuration_key': digest(['opening-component', product['id']])}
        fingerprint = digest([identity, location['id']])
        if fingerprint in used:
            fail('opening_inventory_duplicate_line', 'اجمع كمية المنتج وخياراته في الموقع نفسه بسطر واحد', 422)
        used.add(fingerprint)
        unit = product.get('unit') or 'piece'
        decimal_quantity = quantity_value(line['quantity'], line['kind'], unit)
        quantity = stored_quantity(decimal_quantity); cost = money(line['unit_cost'])
        capacity[location['id']] = capacity.get(location['id'], Decimal(0)) + decimal_quantity
        maximum = stored.get('max_items', 1000000000)
        if maximum is None:
            maximum = 1000000000  # Native location contract: null means unlimited.
        if type(maximum) not in (int, float) or not math.isfinite(maximum) or Decimal(str(maximum)) < capacity[location['id']]:
            fail('opening_inventory_capacity_exceeded', 'تتجاوز الكمية سعة الموقع', 422)
        rows.append({'name': product['name'], 'item_id': product['id'], 'kind': line['kind'],
                     'sku': sku, 'image_url': product.get('image_url'), 'unit': unit,
                     'component_type': product.get('component_type'),
                     'location_id': location['id'], 'warehouse_id': location['warehouse_id'],
                     'location_name': location['name'], 'quantity': quantity, 'unit_cost': format(cost, '.2f'),
                     'total_cost': line_total(cost, quantity), 'inventory_identity': identity,
                     'preparation_state': state, 'specifications': specs, 'purchase_configuration': config})
        snapshots.append([raw, stored, definition, location, warehouse])
    by_unit = {}
    for row in rows:
        by_unit[row['unit']] = by_unit.get(row['unit'], Decimal(0)) + Decimal(str(row['quantity']))
    totals = [{'unit': unit, 'quantity': stored_quantity(value)} for unit, value in sorted(by_unit.items())]
    result = {'lines': rows, 'quantity_totals': totals,
              'total_quantity': totals[0]['quantity'] if len(totals) == 1 else None,
              'total_cost': format(sum((Decimal(r['total_cost']) for r in rows), Decimal(0)), '.2f'),
              'currency': 'SAR', 'financial_effect': 'none', 'fulfillment_eligible': False,
              'fulfillment_reason': 'inventory_valuation_reconciliation_required'}
    result['preview_hash'] = digest([SCHEMA, owner, request, result, snapshots])
    return result


async def preview(db, user, payload):
    request = OpeningRequest.model_validate(payload).model_dump()
    actor = await owner_actor(db, user)
    result = await compile_preview(db, actor['id'], request)
    result['writer_disabled'] = writes_paused(await read_control(root_db(db), actor['id']))
    return result


async def approve(db, user, payload):
    request = OpeningApproval.model_validate(payload).model_dump()
    expected = request.pop('preview_hash')
    actor = await owner_actor(db, user); owner = actor['id']
    fingerprint = digest(request)
    async def commit(scoped):
        await owner_actor(scoped, user, owner)
        previous = await scoped[BATCHES].find_one({'_id': owner, 'user_id': owner})
        if previous:
            if previous.get('request_hash') != fingerprint or previous.get('preview_hash') != expected:
                fail('opening_inventory_already_approved', 'المخزون الافتتاحي معتمد؛ لا يمكن إعادة إدخاله')
            return previous['result']
        compiled = await compile_preview(scoped, owner, request)
        if compiled['preview_hash'] != expected:
            fail('opening_inventory_preview_changed', 'تغيرت بيانات المنتج أو الموقع؛ أعد المعاينة')
        timestamp = now(); batch_id = digest([SOURCE, owner])
        result = {**compiled, 'status': 'approved', 'batch_id': batch_id}
        await scoped[BATCHES].insert_one({'_id': owner, 'id': batch_id, 'user_id': owner,
            'source_type': SOURCE, 'schema_version': SCHEMA, 'request_hash': fingerprint,
            'preview_hash': expected, 'result': result, 'financial_effect': 'none',
            'recorded_by': owner, 'recorded_at': timestamp})
        for index, line in enumerate(compiled['lines']):
            rid = digest([SOURCE, owner, index]); identity = line['inventory_identity']
            proof = {'_id': rid, 'id': rid, 'user_id': owner, 'source_type': SOURCE,
                'schema_version': SCHEMA, 'source_id': batch_id, 'source_line_id': str(index),
                'status': 'posted', 'valuation_status': PENDING, 'financial_effect': 'none',
                'inventory_identity': identity, **identity, 'quantity': line['quantity'], 'unit': line['unit'],
                'location_id': line['location_id'], 'warehouse_id': line['warehouse_id'],
                'preparation_state': line['preparation_state'], 'specifications': line['specifications'],
                'operational_unit_cost': line['unit_cost'], 'operational_total_cost': line['total_cost'],
                'currency': 'SAR', 'recorded_by': owner, 'created_at': timestamp}
            await scoped[RECEIPTS].insert_one(proof)
            item = {**identity, 'receipt_id': rid, 'lot_id': rid, 'source_type': SOURCE,
                    'source_id': batch_id, 'source_line_id': str(index), 'valuation_status': PENDING,
                    'quantity': line['quantity'], 'unit': line['unit'], 'preparation_state': line['preparation_state'],
                    'specifications': line['specifications'], 'condition': 'good', 'placed_at': timestamp}
            # Explicit owner selection is not a barcode scan. Preserve scan
            # evidence rather than manufacturing last_verified_scan metadata.
            await scoped[LOCATIONS].update_one({'id': line['location_id'], 'user_id': owner, 'occupancy': None},
                {'$set': {'occupancy': {'items': [], 'total_quantity': 0}}})
            placed = await scoped[LOCATIONS].update_one({'id': line['location_id'], 'user_id': owner,
                'state': {'$ne': 'disabled'}, 'occupancy.items.receipt_id': {'$ne': rid}},
                {'$push': {'occupancy.items': item}, '$inc': {'occupancy.total_quantity': line['quantity']},
                 '$set': {'state': 'occupied', 'updated_at': timestamp}})
            if placed.matched_count != 1:
                fail('opening_inventory_location_changed', 'تغير الموقع؛ أعد المعاينة')
            await scoped['warehouse_location_events'].insert_one({'_id': rid, 'id': rid, 'user_id': owner,
                'event_type': SOURCE, 'receipt_id': rid, 'quantity': line['quantity'], 'recorded_by': owner,
                'occurred_at': timestamp})
        return result
    return await admitted(db, owner, SOURCE, lambda: operational_owner(db, owner, commit, profile='opening_inventory'))


def validate_insert(name, doc):
    if name == BATCHES:
        return (set(doc) == {'_id','id','user_id','source_type','schema_version','request_hash','preview_hash','result','financial_effect','recorded_by','recorded_at'}
                and doc.get('source_type') == SOURCE and doc.get('schema_version') == SCHEMA and doc.get('financial_effect') == 'none')
    if name == RECEIPTS:
        allowed = {'_id','id','user_id','source_type','schema_version','source_id','source_line_id','status','valuation_status','financial_effect','inventory_identity','quantity','location_id','warehouse_id','preparation_state','specifications','operational_unit_cost','operational_total_cost','currency','recorded_by','created_at'} | identity_fields(doc)
        if 'unit' in doc:
            allowed.add('unit')
        return (valid_quantity(doc) and set(doc) == allowed and doc.get('source_type') == SOURCE and doc.get('schema_version') == SCHEMA
                and doc.get('valuation_status') == PENDING and doc.get('financial_effect') == 'none' and doc.get('status') == 'posted')
    return name == 'warehouse_location_events' and set(doc) == {'_id','id','user_id','event_type','receipt_id','quantity','recorded_by','occurred_at'} and doc.get('event_type') == SOURCE


def identity_fields(doc):
    common = {'item_type', 'sku', 'configuration_key'}
    return common | ({'resource_id'} if doc.get('item_type') == 'stock_component' else
                     {'product_id', 'mezan_product_id', 'salla_variant_id'})


def valid_quantity(doc):
    try:
        if doc.get('item_type') not in {'stock_component', 'product'}:
            return False
        quantity_value(doc.get('quantity'), 'component' if doc['item_type'] == 'stock_component' else 'product', doc.get('unit') or 'piece')
        return True
    except HTTPException:
        return False


async def validate_update(db, owner, name, query, update):
    if name != LOCATIONS:
        return False
    if update == {'$set': {'occupancy': {'items': [], 'total_quantity': 0}}}:
        return query.get('occupancy', 'absent') is None
    if set(update) != {'$push', '$inc', '$set'} or set(update['$push']) != {'occupancy.items'} or set(update['$inc']) != {'occupancy.total_quantity'}:
        return False
    item = update['$push']['occupancy.items']
    if not isinstance(item, dict):
        return False
    allowed = {'receipt_id','lot_id','source_type','source_id','source_line_id','valuation_status','quantity','preparation_state','specifications','condition','placed_at'} | identity_fields(item)
    if 'unit' in item:
        allowed.add('unit')
    if (not isinstance(item, dict) or set(item) != allowed or item.get('source_type') != SOURCE
            or item.get('valuation_status') != PENDING or not valid_quantity(item)
            or update['$inc']['occupancy.total_quantity'] != item['quantity']
            or set(update['$set']) != {'state','updated_at'}
            or update['$set']['state'] != 'occupied'):
        return False
    location = await db[LOCATIONS].find_one({'id': query.get('id'), 'user_id': owner})
    occupancy = (location or {}).get('occupancy') or {}
    maximum = (location or {}).get('max_items', 1000000000)
    if maximum is None:
        maximum = 1000000000
    total = occupancy.get('total_quantity', 0)
    if (not location or location.get('state') == 'disabled' or type(maximum) not in (int, float)
            or not math.isfinite(maximum) or type(total) not in (int, float) or not math.isfinite(total)
            or total < 0 or Decimal(str(total)).quantize(Decimal('.000001')) + Decimal(str(item['quantity'])) > Decimal(str(maximum))
            or any(row.get('receipt_id') == item['receipt_id'] for row in occupancy.get('items', []))):
        return False
    proof = await db[RECEIPTS].find_one({'_id': item['receipt_id'], 'user_id': owner})
    return bool(proof and validate_insert(RECEIPTS, {k:v for k,v in proof.items()})
        and proof['location_id'] == query.get('id') and item['lot_id'] == proof['id']
        and all(item.get(k) == v for k,v in proof['inventory_identity'].items())
        and all(item.get(k) == proof.get(k) for k in ('quantity','unit','source_id','source_line_id','preparation_state','specifications')))


def projected_evidence(location, item, witnesses):
    """Expose declared cost only alongside exact original operational proof.

    Native MWA unit_cost/inventory_value remain unknown. No sale permission is
    derived from this evidence, and legacy/adopted native sources are unchanged.
    """
    if item.get('source_type') != SOURCE or len(witnesses) != 1:
        return {}
    proof = witnesses[0]
    if (not validate_insert(RECEIPTS, proof) or proof.get('id') != item.get('receipt_id')
            or proof.get('location_id') != location.get('id')
            or proof.get('warehouse_id') != location.get('warehouse_id')
            or any(item.get(k) != v for k,v in proof['inventory_identity'].items())
            or any(item.get(k) != proof.get(k) for k in ('quantity','unit','source_id','source_line_id','preparation_state','specifications','valuation_status'))):
        return {}
    try:
        cost = money(proof['operational_unit_cost'])
        if line_total(cost, item['quantity']) != proof['operational_total_cost']:
            return {}
    except (HTTPException, TypeError, InvalidOperation):
        return {}
    return {'operational_unit_cost': format(cost, '.2f'),
            'operational_inventory_value': proof['operational_total_cost'],
            'operational_cost_basis': SCHEMA, 'operational_currency': 'SAR',
            **({'unit': proof['unit']} if proof.get('unit') else {}),
            'financial_effect': 'none', 'fulfillment_eligible': False}
