"""Allocate already-recorded supplier money to an inventory invoice.

The existing movement's cash identity is immutable. Only its allocation list is
changed, under the operational aggregate's CAS and ordinary audit/idempotency
contract. This command never calls a bank, creates a movement, or pays again.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from operational_balance_store import (
    AUTHORIZATION_GUARD, audit, claim_movement, digest, fail, mutate, now, read, remember, replay,
)
from operational_balance_service import accounting_inactive, active_gate, entity, fmt, money, report
from operational_balance_sources import instant
from operational_financial_activity_runtime import checked_rows, prepare_in_thread
from operational_supplier_adjustments import adjusted_gross, invoice_view


class AllocationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: str = Field(min_length=8, max_length=160, strict=True)
    expected_session_scope: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    movement_id: str = Field(min_length=1, max_length=200, strict=True)
    amount: str = Field(min_length=1, max_length=30, strict=True)
    business_date: str = Field(min_length=10, max_length=10, strict=True)


async def _owner(db, user, expected=None):
    actor = await db['users'].find_one({'id': user.get('_mobile_actor_id') or user.get('id')})
    if (not actor or actor.get('role') != 'owner' or actor.get('disabled')
            or actor.get('is_active') is False or actor.get('deleted_at')
            or expected is not None and actor['id'] != expected):
        fail('inventory_payment_owner_required', 'تخصيص دفعة سابقة متاح للمالك فقط', 403)
    return actor['id']


def _invoice(state, invoice_id):
    matches = [row for row in checked_rows(state.get('inventory_purchases', []))
               if row.get('id') == invoice_id]
    if len(matches) != 1:
        fail('inventory_payment_invoice_missing', 'اختر فاتورة مخزون محفوظة واحدة', 404)
    invoice = matches[0]
    obligation = state.get('engine', {}).get('obligations', {}).get(invoice.get('obligation_id')) or {}
    if (invoice.get('currency') != 'SAR' or obligation.get('id') != invoice.get('obligation_id')
            or obligation.get('party_type') != 'supplier' or obligation.get('direction') != 'payable'
            or obligation.get('party_id') != invoice.get('supplier_id') or obligation.get('currency') != 'SAR'
            or obligation.get('superseded') or obligation.get('derived_credit')
            or money(obligation.get('confirmed'), zero=True) != money(adjusted_gross(state, invoice), zero=True)):
        fail('inventory_payment_invoice_changed', 'تعذر مطابقة التزام الفاتورة؛ أعد تحميل المشتريات', 409)
    return invoice


def _linked_movements(state):
    return {payment.get('movement_id')
            for record in (state.get('native_purchase_links') or {}).values()
            for payment in (record.get('payments') or {}).values()}


def _candidate(movement, supplier_id, linked):
    """Return only ordinary, unambiguous recorded principal and its residual."""
    if (movement.get('party_type') != 'supplier' or movement.get('party_id') != supplier_id
            or movement.get('currency') != 'SAR' or movement.get('direction') != 'outgoing'
            or movement.get('kind') not in {'payment', 'settlement'}):
        return None, None
    if (not isinstance(movement.get('id'), str) or not movement['id']
            or movement.get('bank_kind') not in {'bank', 'cash', 'employee_custody'}
            or not isinstance(movement.get('bank_id'), str) or not movement['bank_id']):
        return None, 'مصدر الدفع الفعلي غير مثبت'
    if (movement['id'] in linked or movement.get('native_payment_operation_id')
            or movement.get('source') == 'native_purchase_link' or movement.get('exchange_id')
            or movement.get('order_number') or movement.get('external_operation')
            or any(value for key, value in movement.items() if key.startswith('automatic_'))):
        return None, 'الدفعة مرتبطة بعملية أخرى'
    if (movement.get('status') in {'reversed', 'void', 'voided', 'cancelled', 'canceled'}
            or movement.get('reversed_at') or movement.get('reversal_id') or movement.get('voided_at')
            or movement.get('settlement_scope') not in {None, 'account'}):
        return None, 'حالة الدفعة لا تسمح بالتخصيص'
    try:
        amount = money(movement.get('amount'))
        if (movement.get('bank_currency') not in (None, 'SAR')
                or money(movement.get('bank_amount') or movement['amount']) != amount
                or money(movement.get('bank_fee_amount') or '0', zero=True)
                or money(movement.get('actual_fee_amount') or '0', zero=True)):
            return None, 'دفعة بعملة أو رسوم تحتاج مطابقة مستقلة'
        allocations = movement.get('allocations', [])
        if not isinstance(allocations, list):
            return None, 'تخصيصات الدفعة غير مكتملة'
        keys = set()
        used = Decimal(0)
        for allocation in allocations:
            if (not isinstance(allocation, dict) or set(allocation) != {'obligation_id', 'amount'}
                    or not isinstance(allocation['obligation_id'], str) or not allocation['obligation_id']
                    or allocation['obligation_id'] in keys):
                return None, 'تخصيصات الدفعة غير مكتملة'
            keys.add(allocation['obligation_id'])
            used += money(allocation['amount'])
        if used > amount:
            return None, 'تخصيصات الدفعة تتجاوز أصل المبلغ'
        if used == amount:
            return None, None
        day = movement.get('business_date')
        if day is not None:
            if date.fromisoformat(day).isoformat() != day:
                return None, 'تاريخ الدفعة غير مثبت'
        else:
            day = instant(movement['occurred_at']).astimezone(ZoneInfo('Asia/Riyadh')).date().isoformat()
    except (HTTPException, KeyError, TypeError, ValueError, ArithmeticError):
        return None, 'بيانات مبلغ الدفعة أو تاريخها غير مكتملة'
    return {'id': movement['id'], 'amount': fmt(amount), 'allocated': fmt(used),
            'available': fmt(amount - used), 'currency': 'SAR', 'business_date': day,
            'reference': movement.get('reference') or '', 'bank_id': movement['bank_id'],
            'bank_kind': movement['bank_kind'], 'bank_name': movement.get('bank_name') or '',
            'settlement_scope': movement.get('settlement_scope')}, None


def _candidates(state, supplier_id):
    linked = _linked_movements(state)
    ids = [row.get('id') for row in checked_rows(state.get('movements', []))]
    counts = {}
    for identity in ids:
        counts[identity] = counts.get(identity, 0) + 1
    items, excluded = [], {}
    for movement in checked_rows(state.get('movements', [])):
        item, reason = _candidate(movement, supplier_id, linked)
        if item and counts.get(item['id']) != 1:
            item, reason = None, 'هوية الدفعة مكررة وتحتاج مطابقة'
        if item:
            items.append(item)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
    items.sort(key=lambda row: (row['business_date'], row['id']), reverse=True)
    return {'supplier_id': supplier_id, 'items': items[:1000], 'truncated': len(items) > 1000,
            'excluded': [{'reason': reason, 'count': count} for reason, count in sorted(excluded.items())],
            'financial_effect': 'allocation_only', 'cash_effect': '0.00'}


async def candidates(db, user, invoice_id=None, *, supplier_id=None):
    owner = await _owner(db, user)
    state = await read(db, owner)
    await active_gate(db, owner, state)
    invoice = _invoice(state, invoice_id) if invoice_id else None
    if invoice is not None:
        if supplier_id is not None and supplier_id != invoice['supplier_id']:
            fail('inventory_payment_supplier_mismatch', 'الدفعة والفاتورة لا تتبعان المورد نفسه', 422)
        supplier_id = invoice['supplier_id']
    if not supplier_id:
        fail('inventory_payment_supplier_required', 'اختر المورد أولًا', 422)
    await entity(db, owner, 'supplier', supplier_id, 'SAR')
    result = await prepare_in_thread(_candidates, state, supplier_id)
    if invoice is not None:
        result['invoice'] = invoice_view(state, 'inventory', invoice)
    await _owner(db, user, owner)
    return result


def _financial_identity(state):
    # Invoice allocation may move gross payable/advance presentation. The net
    # balance, money already paid, and all cash/custody positions must not move.
    snapshot = report({**state, 'audit': [], 'requests': {},
                       'engine': {**state.get('engine', {}), 'audit': []}})
    return {(row['party_type'], row['party_id'], row['currency']): (
        Decimal(row['outstanding_payable']) - Decimal(row['outstanding_receivable']),
        Decimal(row['actual']), Decimal(row['expense_paid']),
        Decimal(row['custody_funded']), Decimal(row['custody_spent']),
        Decimal(row['custody_returned']), Decimal(row['custody_remaining']),
        Decimal(row['custody_adjustments']),
    ) for row in snapshot['parties']}


async def allocate(db, user, invoice_id, payload, *, clock=None):
    command = AllocationRequest.model_validate(payload).model_dump()
    owner = await _owner(db, user)
    request = {**command, 'invoice_id': invoice_id}
    stamp = clock or now()
    action = 'inventory_payment_allocation:' + owner

    async def apply(state):
        await _owner(db, user, owner)
        prior = replay(state, action, request)
        if prior is not None:
            return prior
        await active_gate(db, owner, state)
        invoice = _invoice(state, invoice_id)
        await entity(db, owner, 'supplier', invoice['supplier_id'], 'SAR')
        matches = [row for row in state['movements'] if row.get('id') == command['movement_id']]
        if len(matches) != 1:
            fail('inventory_payment_movement_missing', 'اختر دفعة سابقة واحدة للمورد', 404)
        movement = matches[0]
        candidate, _ = _candidate(movement, invoice['supplier_id'], _linked_movements(state))
        if candidate is None:
            fail('inventory_payment_unavailable', 'الدفعة غير متاحة أو خُصصت بالفعل؛ أعد تحميل الدفعات', 409)
        amount = money(command['amount'])
        if amount > money(candidate['available']):
            fail('inventory_payment_allocation_exceeded', 'المبلغ يتجاوز المتاح غير المخصص من الدفعة', 422)
        view = invoice_view(state, 'inventory', invoice)
        if amount > money(view['outstanding'], zero=True):
            fail('inventory_payment_invoice_exceeded', 'المبلغ يتجاوز المتبقي على الفاتورة', 422)
        try:
            day = date.fromisoformat(command['business_date'])
            lower = max(instant(state['started_at']).astimezone(ZoneInfo('Asia/Riyadh')).date(),
                        date.fromisoformat(invoice['invoice_date']), date.fromisoformat(candidate['business_date']))
            upper = instant(stamp).astimezone(ZoneInfo('Asia/Riyadh')).date()
            if day.isoformat() != command['business_date'] or day < lower or day > upper:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            fail('inventory_payment_date_invalid', 'اختر تاريخ تخصيص ضمن فترة التشغيل وبعد الفاتورة والدفعة، دون تاريخ مستقبلي', 422)
        before_balance = await prepare_in_thread(_financial_identity, state)
        before = deepcopy(movement)
        allocations = deepcopy(movement.get('allocations', []))
        previous = next((row for row in allocations if row['obligation_id'] == invoice['obligation_id']), None)
        if previous:
            previous['amount'] = fmt(money(previous['amount']) + amount)
        else:
            allocations.append({'obligation_id': invoice['obligation_id'], 'amount': fmt(amount)})
        movement['allocations'] = allocations
        if await prepare_in_thread(_financial_identity, state) != before_balance:
            fail('inventory_payment_balance_changed', 'تعذر تخصيص الدفعة دون تغيير الرصيد؛ يلزم مراجعة المطابقة', 409)
        result = {'id': digest(['inventory-payment-allocation', owner, command['request_id']]),
                  'invoice_id': invoice['id'], 'movement_id': movement['id'],
                  'allocated_amount': fmt(amount), 'available': fmt(money(candidate['available']) - amount),
                  'business_date': day.isoformat(), 'financial_effect': 'allocation_only',
                  'cash_effect': '0.00', 'supplier_balance_effect': '0.00',
                  'invoice': invoice_view(state, 'inventory', invoice)}
        await prepare_in_thread(audit, state, owner, 'inventory_payment_allocated',
                                {'movement': before}, {'movement': deepcopy(movement), 'allocation': result},
                                'ربط دفعة مسجلة بفاتورة مخزون دون دفع جديد', 'inventory_purchase', stamp)
        remember(state, action, request, result)
        return result

    previous_guard = AUTHORIZATION_GUARD.get()

    async def verify_authority():
        if previous_guard is not None:
            await previous_guard()
        await _owner(db, user, owner)
        await accounting_inactive(db, owner)

    token = AUTHORIZATION_GUARD.set(verify_authority)
    try:
        await claim_movement(db, owner, owner, {**request, '_operation': 'inventory_payment_allocation'})
        return await mutate(db, owner, apply)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get('code') not in {
                'operational_request_conflict', 'operational_request_scope_conflict', 'operational_concurrent_change'}:
            exc.detail['not_applied'] = True
        raise
    finally:
        AUTHORIZATION_GUARD.reset(token)
