"""Physical receiving linked to an existing operational purchase.

Receipt evidence and placement commit together. The financial aggregate is only
revision-fenced so concurrent closure/credits cannot invalidate a receiving
decision. This capability cannot change liabilities, cash, or valuation.
"""
from copy import deepcopy
from datetime import date
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from inventory_write_gate import admitted, read_control, root_db, standalone_writes_paused
from operational_atomic import operational_owner
from operational_balance_inventory import purchase_view
from operational_balance_service import active_gate
from operational_balance_sources import instant
from operational_balance_store import digest, fail, now, read, STATES
from operational_inventory_identity import line_identity
from operational_opening_inventory import (
    compile_preview, owner_actor, place_compiled, validate_insert as opening_insert,
    validate_update as opening_update,
)
from operational_purchase_metadata import locations
from standalone_inventory_warehouse import allows_standalone_entry

SOURCE = 'standalone_inventory_purchase_receipt'
SCHEMA = 'inventory-purchase-receipt-v1'
RECEIPTS = 'mz2_inventory_purchase_receipts_v1'


class ReceiptLine(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    line_id: str = Field(min_length=1, max_length=200)
    quantity: int = Field(gt=0, le=100000, strict=True)
    location_id: str = Field(min_length=1, max_length=200)


class ReceiptRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: str = Field(min_length=8, max_length=160)
    expected_session_scope: str = Field(pattern=r'^[a-f0-9]{64}$')
    receipt_date: str = Field(min_length=10, max_length=10)
    lines: list[ReceiptLine] = Field(min_length=1, max_length=200)
    note: str = Field(default='', max_length=1000)


class ReceiptApproval(ReceiptRequest):
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


def line_id(invoice_id, index, line):
    return digest([SCHEMA, invoice_id, index, line_identity(line)])


def tracked(invoice):
    return type(invoice.get('receipt_tracking_version')) is int and invoice['receipt_tracking_version'] == 1


async def records(db, owner, invoice_ids):
    if not invoice_ids:
        return []
    result = await db[RECEIPTS].find(
        {'user_id': owner, 'invoice_id': {'$in': list(invoice_ids)}}, {'_id': 0}
    ).sort('recorded_at', -1).limit(20001).to_list(20001)
    if len(result) > 20000:
        fail('purchase_receipt_history_incomplete', 'تعذر قراءة سجل الاستلام كاملًا', 409)
    for record in result:
        if not validate_insert(RECEIPTS, {'_id': record.get('id'), **record}):
            fail('purchase_receipt_history_invalid', 'يحتاج سجل الاستلام إلى مراجعة', 409)
    return result


def decorate(view, receipts):
    result = deepcopy(view)
    enabled = tracked(result)
    if not enabled:
        # Preserve historical responses and their explicit unproven status.
        # Missing old receipt evidence cannot mean zero units were received.
        return result
    relevant = [r for r in receipts if r['invoice_id'] == result['id']]
    received = {}
    for record in relevant:
        for row in record['result']['lines']:
            received[row['invoice_line_id']] = received.get(row['invoice_line_id'], 0) + row['quantity']
    line_ids = set()
    for index, row in enumerate(result['lines']):
        identity = line_id(result['id'], index, row)
        row['line_id'] = identity
        line_ids.add(identity)
        amount = received.get(identity, 0)
        if amount > row['quantity']:
            fail('purchase_receipt_history_invalid', 'كمية الاستلام المسجلة تتجاوز الفاتورة', 409)
        remaining = max(0, row.get('remaining_quantity', row['quantity']) - amount)
        row.update(received_quantity=amount, pending_receipt_quantity=remaining,
                   physical_stock_status='received' if amount == row['quantity'] else 'partial' if amount else 'not_received')
    if set(received) - line_ids or relevant and not enabled:
        fail('purchase_receipt_history_invalid', 'تعذر مطابقة الاستلام ببنود الفاتورة', 409)
    amounts = [row['received_quantity'] for row in result['lines']] if enabled else []
    complete = enabled and all(row['received_quantity'] == row['quantity'] for row in result['lines'])
    pending_lines = sum(row['pending_receipt_quantity'] > 0 for row in result['lines'])
    result['receipt_status'] = ('unproven' if not enabled else 'received' if complete else 'closed' if not pending_lines
                                else 'partial' if any(amounts) else 'not_received')
    result['physical_stock_status'] = result['receipt_status']
    result['receipt_remaining_lines'] = pending_lines
    result['can_receive'] = enabled and bool(pending_lines)
    result['receipt_history'] = [{
        'id': r['id'], 'receipt_date': r['receipt_date'], 'recorded_at': r['recorded_at'], 'note': r['note'],
        'lines': [{'line_id': row['invoice_line_id'], 'name': row['name'], 'quantity': row['quantity'],
                   'unit': row['unit'], 'location_id': row['location_id'], 'location_name': row['location_name']}
                  for row in r['result']['lines']],
    } for r in relevant]
    return result


async def enrich_purchase(db, owner, view):
    if not tracked(view):
        return deepcopy(view)
    return decorate(view, await records(db, owner, [view['id']]))


async def enrich_purchases(db, owner, result):
    history = await records(db, owner, [view['id'] for view in result['items'] if tracked(view)])
    return {**result, 'items': [decorate(view, history) for view in result['items']]}


async def find_invoice(db, owner, invoice_id, *, require_tracking=True):
    state = await read(db, owner)
    matches = [row for row in state.get('inventory_purchases', []) if row['id'] == invoice_id]
    if len(matches) != 1:
        fail('inventory_invoice_missing', 'فاتورة الشراء غير موجودة', 404)
    invoice = matches[0]
    if require_tracking and not tracked(invoice):
        fail('purchase_receipt_historical_reconciliation', 'استلام هذه الفاتورة القديمة غير مثبت؛ طابقها قبل إضافة كمية جديدة', 409)
    return state, invoice


async def eligible_locations(db, owner):
    warehouses = await db['warehouse_locations_warehouses'].find(
        {'user_id': owner, 'status': 'active'}, {'_id': 0}).to_list(20001)
    if len(warehouses) > 20000:
        fail('inventory_locations_incomplete', 'تعذر قراءة المخازن كاملة', 409)
    ids = {row['id'] for row in warehouses if allows_standalone_entry(row)}
    return [row for row in await locations(db, owner) if row['warehouse_id'] in ids]


async def context(db, user, invoice_id):
    actor = await owner_actor(db, user)
    owner = actor['id']
    state, invoice = await find_invoice(db, owner, invoice_id)
    view = await enrich_purchase(db, owner, purchase_view(state, invoice))
    return {'invoice': view, 'locations': await eligible_locations(db, owner),
            'writer_disabled': standalone_writes_paused(await read_control(root_db(db), owner)),
            'operational_active': state['status'] == 'active',
            'financial_effect': 'none', 'fulfillment_eligible': False}


async def compile_receipt(db, owner, invoice_id, request):
    if request['expected_session_scope'] != digest([owner, owner]):
        fail('operational_actor_scope_changed', 'تغير نطاق الحساب؛ أعد فتح الفاتورة', 409)
    state, invoice = await find_invoice(db, owner, invoice_id)
    await active_gate(db, owner, state)
    try:
        day = date.fromisoformat(request['receipt_date'])
        if day < date.fromisoformat(invoice['invoice_date']) or day > instant(now()).astimezone(ZoneInfo('Asia/Riyadh')).date():
            raise ValueError()
    except (ValueError, TypeError):
        fail('purchase_receipt_date_invalid', 'تاريخ الاستلام يجب أن يكون من تاريخ الفاتورة إلى اليوم', 422)
    previous = await records(db, owner, [invoice_id])
    view = decorate(purchase_view(state, invoice), previous)
    available = {row['line_id']: row for row in view['lines']}
    requested = request['lines']
    if len({row['line_id'] for row in requested}) != len(requested):
        fail('purchase_receipt_duplicate_line', 'حدد كل بند مرة واحدة في الاستلام', 422)
    opening_lines = []
    for row in requested:
        original = available.get(row['line_id'])
        if original is None or row['quantity'] > original['pending_receipt_quantity']:
            fail('purchase_receipt_quantity_exceeded', 'كمية الاستلام تتجاوز المتبقي من بند الفاتورة', 422)
        config = original.get('purchase_configuration')
        if original.get('personalizations') or (original['kind'] == 'product' and config is None):
            fail('purchase_receipt_configuration_required', 'مواصفات هذا البند غير مثبتة للاستلام؛ يلزم مطابقته أولًا', 409)
        if original['kind'] == 'component':
            config = {'state': 'raw', 'selections': [], 'inputs': []}
        opening_lines.append({'kind': original['kind'], 'item_id': original['item_id'],
            'location_id': row['location_id'], 'quantity': row['quantity'], 'unit_cost': original['unit_price'],
            'purchase_configuration': deepcopy(config)})
    compiled = await compile_preview(db, owner, {'request_id': request['request_id'], 'lines': opening_lines},
                                     standalone=True, allow_duplicate_lines=True)
    for row, requested_line in zip(compiled['lines'], requested):
        row['invoice_line_id'] = requested_line['line_id']
    receipt_hash = digest([SCHEMA, owner, invoice_id, request, invoice,
        [(r['id'], r['request_hash']) for r in previous],
        [(r['line_id'], r['remaining_quantity']) for r in view['lines']], compiled['preview_hash']])
    result = {**compiled, 'preview_hash': receipt_hash, 'invoice_id': invoice_id,
              'receipt_date': day.isoformat(), 'invoice': view, 'receipt_status': view['receipt_status']}
    return result, state['revision']


async def preview(db, user, invoice_id, payload):
    request = ReceiptRequest.model_validate(payload).model_dump()
    actor = await owner_actor(db, user)
    result, _ = await compile_receipt(db, actor['id'], invoice_id, request)
    result['writer_disabled'] = standalone_writes_paused(await read_control(root_db(db), actor['id']))
    return result


async def save(db, user, invoice_id, payload):
    request = ReceiptApproval.model_validate(payload).model_dump()
    expected = request.pop('preview_hash')
    actor = await owner_actor(db, user)
    owner = actor['id']
    if request['expected_session_scope'] != digest([owner, owner]):
        fail('operational_actor_scope_changed', 'تغير نطاق الحساب؛ أعد فتح الفاتورة', 409)
    receipt_id = digest([SOURCE, owner, request['request_id']])
    fingerprint = digest([invoice_id, request])

    async def commit(scoped):
        await owner_actor(scoped, user, owner)
        previous = await scoped[RECEIPTS].find_one({'_id': receipt_id, 'user_id': owner})
        if previous:
            if previous['request_hash'] != fingerprint or previous['preview_hash'] != expected:
                fail('purchase_receipt_request_conflict', 'مرجع الاستلام مستخدم لبيانات مختلفة', 409)
            return previous['result']
        compiled, revision = await compile_receipt(scoped, owner, invoice_id, request)
        if compiled['preview_hash'] != expected:
            fail('purchase_receipt_preview_changed', 'تغيرت الكمية أو المواصفات أو الموقع؛ أعد المعاينة', 409)
        # Serialize with the operational store's CAS writers. No financial field
        # is updated: this is solely a revision fence within this transaction.
        fence = await scoped[STATES].update_one(
            {'_id': owner, 'owner_id': owner, 'revision': revision}, {'$inc': {'revision': 1}})
        if fence.matched_count != 1:
            fail('purchase_receipt_invoice_changed', 'تغيرت الفاتورة؛ أعد المعاينة', 409)
        timestamp = now()
        result = {**compiled, 'status': 'approved', 'receipt_id': receipt_id, 'batch_id': receipt_id}
        record = {'_id': receipt_id, 'id': receipt_id, 'user_id': owner, 'source_type': SOURCE,
            'schema_version': SCHEMA, 'request_hash': fingerprint, 'preview_hash': expected,
            'invoice_id': invoice_id, 'receipt_date': request['receipt_date'], 'note': request['note'],
            'recorded_by': owner, 'recorded_at': timestamp, 'financial_effect': 'none', 'result': result}
        history = await records(scoped, owner, [invoice_id])
        result['invoice'] = decorate(compiled['invoice'], [record, *history])
        # History is reconstructed from immutable receipt records on reads;
        # embedding all earlier history in each receipt would grow quadratically.
        result['invoice'].pop('receipt_history', None)
        result['receipt_status'] = result['invoice']['receipt_status']
        await scoped[RECEIPTS].insert_one(record)
        await place_compiled(scoped, owner, compiled, timestamp, receipt_id, source=SOURCE)
        return result

    async def apply():
        try:
            return await operational_owner(db, owner, commit, profile='purchase_receiving')
        except HTTPException as exc:
            code = exc.detail.get('code') if isinstance(exc.detail, dict) else None
            # These errors occur after replay lookup and abort the transaction.
            # Admission/ownership/unknown-outcome and conflicting old requests
            # deliberately keep their recovery command for reconciliation.
            if code in {'purchase_receipt_preview_changed', 'purchase_receipt_quantity_exceeded',
                        'purchase_receipt_date_invalid', 'purchase_receipt_invoice_changed',
                        'purchase_receipt_configuration_required', 'opening_inventory_capacity_exceeded',
                        'opening_inventory_location_changed', 'operational_not_active'} or (
                            isinstance(code, str) and code.startswith('inventory_purchase_')):
                exc.detail['not_applied'] = True
            raise
    return await admitted(db, owner, SOURCE, apply, standalone=True)


def validate_insert(name, doc):
    if name != RECEIPTS:
        return opening_insert(name, doc, source=SOURCE)
    fields = {'_id', 'id', 'user_id', 'source_type', 'schema_version', 'request_hash', 'preview_hash',
              'invoice_id', 'receipt_date', 'note', 'recorded_by', 'recorded_at', 'financial_effect', 'result'}
    result = doc.get('result')
    return (set(doc) == fields and doc.get('source_type') == SOURCE and doc.get('schema_version') == SCHEMA
        and doc.get('financial_effect') == 'none' and isinstance(result, dict)
        and doc.get('_id') == doc.get('id') == result.get('receipt_id')
        and result.get('invoice_id') == doc.get('invoice_id') and result.get('status') == 'approved'
        and result.get('financial_effect') == 'none' and result.get('fulfillment_eligible') is False
        and isinstance(result.get('lines'), list) and 0 < len(result['lines']) <= 200
        and all(isinstance(row, dict) and isinstance(row.get('invoice_line_id'), str)
                and type(row.get('quantity')) is int and row['quantity'] > 0 for row in result['lines']))


async def validate_update(db, owner, name, query, update):
    if name != 'warehouse_locations':
        return False
    location = await db[name].find_one({'user_id': owner, 'id': query.get('id')})
    warehouse = await db['warehouse_locations_warehouses'].find_one(
        {'user_id': owner, 'id': (location or {}).get('warehouse_id')})
    if not allows_standalone_entry(warehouse):
        return False
    return await opening_update(db, owner, name, query, update, source=SOURCE)
