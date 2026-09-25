"""Documented, untaxed customer advances; never a sales-return shortcut.

This narrow path requires an explicit review of the original tax treatment.
Previously booked cash/tax requires reconciliation, not a second capture.
"""
from datetime import datetime, timezone
from decimal import Decimal
import re

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from accounting_atomic import atomic_owner
from accounting_customer_refunds import money, public
from accounting_module_contract import accounting_owner_id, require_accounting_permission
from accounting_receivable_service import OPERATION, cutoff_for, digest, source_documents
from accounting_recognition_evidence import (
    CAPTURED, REFUNDED, PROVIDERS, EvidenceError, canonical_identity,
    project_source_evidence, require, timestamp,
)
from accounting_sales_tax import TaxError, decimal_value
from accounting_write_control import fresh_actor

CANCELLED = {'cancelled', 'canceled', 'ملغي', 'ملغى'}


async def authority(db, actor, owner, permission):
    current = await fresh_actor(db, actor)
    require_accounting_permission(current, permission)
    if accounting_owner_id(current) != owner:
        raise HTTPException(403, 'advance_owner_mismatch')
    return current


async def event_date(db, owner, value, earliest=None):
    when = timestamp(value)
    require(timestamp(await cutoff_for(db, owner)) <= when <= datetime.now(timezone.utc),
            'advance_date_outside_cutover_or_future')
    if earliest:
        require(when >= timestamp(earliest), 'advance_date_before_original_event')
    return when.isoformat(timespec='microseconds')


def evidence(value):
    value = str(value or '').strip()
    require(bool(value), 'advance_documented_evidence_required')
    return value


async def post_group(db, owner, actor, row, kind, at, entries, reference, extra=None):
    from ledger_core import post_txn_group
    return await post_txn_group(db, user_id=owner, actor_id=actor['id'],
        actor_name=actor.get('name', actor['id']), entries=entries,
        txn_type=kind, notes='Customer advance: ' + reference,
        metadata=dict(operation_id=OPERATION, accounting_at=at,
            customer_advance_id=row['id'], order_reference_id=row['order_number'],
            provider=row['provider'], provider_id=row['payment_id'],
            advance_tax_treatment='reviewed_no_tax_previously_recognized',
            evidence_ref=reference, **(extra or {})))


def leg(entity, identifier, sub, side, amount, kind):
    return dict(entity_type=entity, entity_id=identifier, sub_account=sub,
                side=side, amount=amount, entry_type=kind)


async def recognize_advance(db, *, owner, actor, provider, payment_id, evidence_ref,
                            original_tax_amount, tax_review_ref):
    facts = dict(provider=provider, payment_id=canonical_identity(payment_id),
        evidence_ref=evidence(evidence_ref), tax_review_ref=evidence(tax_review_ref),
        original_tax_amount=format(decimal_value(original_tax_amount), '.2f'))
    require(decimal_value(original_tax_amount) == 0, 'advance_tax_allocation_requires_review')
    payment_id = facts['payment_id']
    key = digest([owner, 'customer_advance', provider, facts['payment_id']])

    async def write(scoped):
        current = await authority(scoped, actor, owner, 'accounting.advances.recognize')
        prior = await scoped.mz2_customer_advances.find_one({'_id': key})
        if prior:
            require(prior['capture_facts'] == facts, 'advance_capture_identity_conflict')
            return public(prior)
        order, payment, _ = await source_documents(scoped, owner, provider, payment_id)
        order, payment, _ = project_source_evidence(order, payment)
        require(provider in PROVIDERS and payment.get('provider') == provider, 'unsupported_provider')
        require(order.get('user_id') == payment.get('user_id') == owner, 'owner_mismatch')
        require(order.get('currency') == payment.get('currency') == 'SAR', 'sar_evidence_required')
        require(str(order.get('payment_method') or '').lower() in PROVIDERS[provider], 'order_payment_method_mismatch')
        # This is an evidenced capture before fulfilment, not a delivered sale
        # or a status-only webhook. Cancellation is checked again separately.
        require(str(order.get('order_status') or '').lower() in CANCELLED | {'pending', 'processing', 'under_review', 'new'},
                'advance_requires_unfulfilled_order')
        require(not order.get('delivered_at') and not order.get('completed_at'), 'advance_delivery_evidence_conflict')
        require(str(payment.get('status') or '').lower() in CAPTURED | REFUNDED, 'capture_not_confirmed')
        require(not payment.get('synthesised') and not (payment.get('raw') or {}).get('_fetch_error'), 'provider_evidence_unverified')
        require(payment.get('id') and (payment.get('source') or payment.get('status_source')), 'source_provenance_required')
        value = money(payment.get('captured_amount'))
        require(Decimal(value) == decimal_value(payment.get('amount')) == decimal_value(order.get('total_amount')),
                'advance_capture_principal_conflict')
        at = await event_date(scoped, owner, payment.get('captured_at'), order.get('order_created_at'))
        number = str(order['order_number'])
        alternatives = [{'metadata.order_reference_id': number}, {'metadata.order_number': number},
            {'metadata.provider': provider, 'metadata.provider_id': payment_id},
            {'metadata.idempotency_key': f'bnpl_sale:{provider}:{payment_id}'}]
        if order.get('id') or order.get('order_id'):
            alternatives.append({'metadata.source_order_id': str(order.get('id') or order['order_id'])})
        existing = await scoped.general_ledger.find_one({'user_id': owner,
            'status': {'$in': ['posted', 'reversed']}, '$or': alternatives})
        recognition = await scoped.mz2_recognition_events.find_one({'user_id': owner,
            'proposal.event.provider': provider, 'proposal.event.provider_payment_id': payment_id})
        require(not existing and not recognition and not any(order.get(k) for k in
            ('pre_cutover_qoyod_invoice_id', 'sales_journal_id', 'revenue_txn_group_id', 'tax_journal_id')),
            'advance_existing_capture_or_tax_requires_reconciliation')
        row = dict(_id=key, id=key, user_id=owner, provider=provider, payment_id=payment_id,
            order_number=number, amount=value, captured_at=at, capture_facts=facts,
            paid='0.00', remaining=value, state='advance', created_by=current['id'],
            created_at=datetime.now(timezone.utc).isoformat(timespec='microseconds'))
        kind = 'customer_advance_capture'
        result = await post_group(scoped, owner, current, row, kind, at, [
            leg('payment_gateway', provider, 'receivable', 'debit', value, kind),
            leg('liability', key, 'customer_advance', 'credit', value, kind)], facts['evidence_ref'])
        row['capture_txn_group_id'] = result['txn_group_id']
        await scoped.mz2_customer_advances.insert_one(row)
        return public(row)
    return await atomic_owner(db, owner, write)


async def cancel_advance(db, *, owner, actor, advance_id, accounting_at, evidence_ref):
    reference = evidence(evidence_ref)
    async def write(scoped):
        current = await authority(scoped, actor, owner, 'accounting.advances.refund')
        row = await scoped.mz2_customer_advances.find_one({'_id': advance_id, 'user_id': owner})
        if not row:
            raise HTTPException(404, 'customer_advance_not_found')
        at = await event_date(scoped, owner, accounting_at, row['captured_at'])
        facts = dict(accounting_at=at, evidence_ref=reference)
        if row.get('cancellation'):
            require(row['cancellation'] == facts, 'advance_cancellation_immutable')
            return public(row)
        order, _, _ = await source_documents(scoped, owner, row['provider'], row['payment_id'])
        require(str(order.get('order_status') or '').lower() in CANCELLED, 'confirmed_order_cancellation_required')
        from ledger_core import compute_balance
        balance = await compute_balance(scoped, user_id=owner, entity_type='liability', entity_id=advance_id, sub_account='customer_advance')
        require(-Decimal(str(balance['net_balance'])) == Decimal(row['amount']), 'advance_balance_requires_reconciliation')
        kind = 'customer_advance_cancellation'
        result = await post_group(scoped, owner, current, row, kind, at, [
            leg('liability', advance_id, 'customer_advance', 'debit', row['amount'], kind),
            leg('liability', advance_id, 'customer_refund_payable', 'credit', row['amount'], kind)], reference)
        changes = dict(cancellation=facts, due_txn_group_id=result['txn_group_id'], state='due')
        await scoped.mz2_customer_advances.update_one({'_id': advance_id}, {'$set': changes})
        return public({**row, **changes})
    return await atomic_owner(db, owner, write)


async def pay_advance(db, *, owner, actor, advance_id, amount, paid_at, execution_channel,
                      execution_reference, bank_account_id='', provider_refund_id=''):
    value, reference = money(amount), evidence(execution_reference)
    require(execution_channel in {'bank', *PROVIDERS}, 'unsupported_refund_execution_channel')
    if execution_channel != 'bank':
        provider_refund_id = canonical_identity(provider_refund_id)
    async def write(scoped):
        current = await authority(scoped, actor, owner, 'accounting.advances.refund')
        row = await scoped.mz2_customer_advances.find_one({'_id': advance_id, 'user_id': owner})
        if not row:
            raise HTTPException(404, 'customer_advance_not_found')
        require(row.get('cancellation'), 'confirmed_advance_cancellation_required')
        at = await event_date(scoped, owner, paid_at, row['cancellation']['accounting_at'])
        channel = execution_channel
        identity = reference.upper() if channel == 'bank' else canonical_identity(provider_refund_id)
        key = digest([owner, 'advance_payment', channel, bank_account_id if channel == 'bank' else '', identity])
        facts = dict(advance_id=advance_id, amount=value, paid_at=at, execution_channel=channel,
            execution_reference=reference, bank_account_id=bank_account_id, provider_refund_id=provider_refund_id)
        prior = await scoped.mz2_customer_advance_payments.find_one({'_id': key})
        if prior:
            require(prior['facts'] == facts, 'advance_payment_identity_conflict')
            return public(prior)
        require(Decimal(value) <= Decimal(row['remaining']), 'payment_exceeds_advance_remaining')
        other = await scoped.mz2_customer_advance_payments.find_one({'user_id': owner,
            'advance_id': advance_id, 'execution_channel': {'$ne': channel}, 'status': 'posted'})
        require(not other, 'mixed_execution_channels_require_review')
        confirmed = await scoped.payment_refunds.find({'user_id': owner, 'provider': row['provider'],
            'provider_payment_id': row['payment_id'], 'status': {'$in': list(REFUNDED)}}).to_list(1001)
        require(len(confirmed) <= 1000, 'advance_refund_evidence_limit')
        if channel == 'bank':
            from accounting_settlement_routes import _find_bank
            bank = await _find_bank(scoped, owner, bank_account_id)
            require(bank and bank.get('account_type') == 'bank', 'owned_bank_required')
            require(not confirmed, 'provider_execution_evidence_conflicts_with_bank_payment')
            reference_match = {'$regex': '^' + re.escape(reference) + '$', '$options': 'i'}
            recorded = await scoped.account_transactions.find_one({'user_id': owner, 'account_id': bank_account_id, 'reference': reference_match})
            recorded_ledger = await scoped.general_ledger.find_one({'user_id': owner, 'entity_type': 'bank',
                'entity_id': bank_account_id, 'metadata.bank_reference': reference_match})
            require(not recorded and not recorded_ledger, 'bank_reference_already_recorded')
            duplicate = {'execution_channel': 'bank', 'bank_account_id': bank_account_id, 'bank_reference': reference_match}
        else:
            require(channel == row['provider'], 'execution_provider_differs_from_original_requires_review')
            matching = [r for r in confirmed if r.get('provider_refund_id') == provider_refund_id]
            require(len(matching) == 1, 'confirmed_provider_refund_required')
            refund = matching[0]
            require(not refund.get('synthesised') and refund.get('currency') == 'SAR'
                and decimal_value(refund.get('amount')) == Decimal(value)
                and timestamp(refund.get('refunded_at')) == timestamp(at), 'provider_refund_evidence_conflict')
            duplicate = {'execution_channel': channel, 'provider_refund_id': provider_refund_id}
            legacy = await scoped.mz2_recognition_events.find_one({'user_id': owner,
                'proposal.event.provider': channel, 'proposal.event.canonical_event_id': provider_refund_id})
            require(not legacy, 'provider_refund_already_accounted')
        require(not await scoped.mz2_customer_refund_payments.find_one({'user_id': owner, 'status': 'posted', **duplicate}),
                'refund_execution_already_accounted')
        from ledger_core import compute_balance
        target, identifier, sub = ('bank', bank_account_id, 'main') if channel == 'bank' else ('payment_gateway', channel, 'receivable')
        balance = await compute_balance(scoped, user_id=owner, entity_type=target, entity_id=identifier, sub_account=sub)
        payable = await compute_balance(scoped, user_id=owner, entity_type='liability', entity_id=advance_id, sub_account='customer_refund_payable')
        require(Decimal(str(balance['net_balance'])) >= Decimal(value), 'insufficient_refund_execution_balance')
        require(-Decimal(str(payable['net_balance'])) == Decimal(row['remaining']), 'advance_payable_requires_reconciliation')
        kind = 'customer_advance_payment'
        result = await post_group(scoped, owner, current, row, kind, at, [
            leg('liability', advance_id, 'customer_refund_payable', 'debit', value, kind),
            leg(target, identifier, sub, 'credit', value, kind)], reference,
            dict(bank_reference=reference if channel == 'bank' else '', provider_refund_id=provider_refund_id,
                 execution_channel=channel))
        paid = Decimal(row['paid']) + Decimal(value)
        remaining = Decimal(row['amount']) - paid
        await scoped.mz2_customer_advances.update_one({'_id': advance_id}, {'$set': dict(
            paid=format(paid, '.2f'), remaining=format(remaining, '.2f'), state='paid' if not remaining else 'partially_paid')})
        payment = dict(_id=key, id=key, user_id=owner, **facts, facts=facts,
            bank_reference=reference if channel == 'bank' else '', status='posted', txn_group_id=result['txn_group_id'])
        await scoped.mz2_customer_advance_payments.insert_one(payment)
        return public(payment)
    return await atomic_owner(db, owner, write)


class CaptureInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: str = Field(pattern='^(salla|tamara|tabby|emkan)$')
    payment_id: str = Field(min_length=1, max_length=200)
    evidence_ref: str = Field(min_length=1, max_length=200)
    original_tax_amount: str = Field(min_length=1, max_length=30)
    tax_review_ref: str = Field(min_length=1, max_length=200)


class CancelInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    accounting_at: str = Field(min_length=1, max_length=40)
    evidence_ref: str = Field(min_length=1, max_length=200)


class AdvancePaymentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    amount: str = Field(min_length=1, max_length=30)
    paid_at: str = Field(min_length=1, max_length=40)
    execution_channel: str = Field(pattern='^(bank|salla|tamara|tabby|emkan)$')
    execution_reference: str = Field(min_length=1, max_length=200)
    bank_account_id: str = Field(default='', max_length=100)
    provider_refund_id: str = Field(default='', max_length=200)


def install_customer_advance_routes(router, db, current_user):
    base = '/accounting-module/customer-advances'
    async def scope(user, permission):
        actor = await fresh_actor(db, user)
        require_accounting_permission(actor, permission)
        return actor, accounting_owner_id(actor)

    async def call(fn, user, permission, **payload):
        actor, owner = await scope(user, permission)
        try:
            return await fn(db, owner=owner, actor=actor, **payload)
        except (EvidenceError, TaxError) as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get(base)
    async def read(user: dict = Depends(current_user)):
        _, owner = await scope(user, 'accounting.movements.view')
        rows = await db.mz2_customer_advances.find({'user_id': owner}, {'_id': 0}).sort('created_at', -1).to_list(200)
        payments = await db.mz2_customer_advance_payments.find({'user_id': owner}, {'_id': 0}).to_list(1000)
        banks = await db.accounts.find({'user_id': owner, 'account_type': 'bank'},
            {'_id': 0, 'id': 1, 'name': 1}).limit(100).to_list(100)
        return dict(items=rows, payments=payments, banks=banks)

    @router.post(base)
    async def capture(payload: CaptureInput, user: dict = Depends(current_user)):
        return await call(recognize_advance, user, 'accounting.advances.recognize', **payload.model_dump())

    @router.post(base + '/{advance_id}/cancel')
    async def cancel(advance_id: str, payload: CancelInput, user: dict = Depends(current_user)):
        return await call(cancel_advance, user, 'accounting.advances.refund', advance_id=advance_id, **payload.model_dump())

    @router.post(base + '/{advance_id}/payments')
    async def pay(advance_id: str, payload: AdvancePaymentInput, user: dict = Depends(current_user)):
        return await call(pay_advance, user, 'accounting.advances.refund', advance_id=advance_id, **payload.model_dump())
