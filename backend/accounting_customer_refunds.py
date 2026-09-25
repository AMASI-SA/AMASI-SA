"""Customer refund entitlement and execution are separate economic events.

An order edit is never evidence of a completed payment. Explicit merchant
case identities are not provider refund IDs. All financial writes share the
Mezan 2 transaction boundary with recognition and provider settlement.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import base64
import hashlib
import re
from fastapi import HTTPException
from accounting_atomic import atomic_owner
from accounting_receivable_service import digest, cutoff_for
from accounting_sales_tax import decimal_value, instant, refund_split


def public(row):
    return {k: v for k, v in row.items() if k not in {'_id', 'proof_bytes'}}


def money(value):
    amount = decimal_value(value)
    if amount <= 0:
        raise HTTPException(422, 'positive_amount_required')
    return format(amount, '.2f')


async def original_sale(db, owner, key):
    row = await db.mz2_recognition_events.find_one({'_id': key, 'user_id': owner,
        'status': 'posted', 'proposal.event.kind': 'sale'})
    if not row:
        raise HTTPException(409, 'posted_original_sale_required')
    return row


async def validate_date(db, owner, value, original):
    when = instant(value)
    zone=timezone(timedelta(hours=3))
    if when < instant(await cutoff_for(db, owner)) or when.astimezone(zone).date() < instant(original['proposal']['event']['recognized_at']).astimezone(zone).date():
        raise HTTPException(409, 'refund_date_before_recognition_or_cutoff')
    if when > datetime.now(timezone.utc):
        raise HTTPException(409, 'future_refund_date')


async def case_tax(db, owner, original, amount, exclude=None):
    prior = await db.mz2_recognition_events.find({'user_id': owner,
        'original_key': original['_id'], 'status': 'posted'}).to_list(10001)
    payments = await db.mz2_customer_refund_payments.find({'user_id': owner,
        'original_key': original['_id'], 'status': 'posted',
        'entitlement_txn_group_id': {'$exists': False}}).to_list(10001)
    entitlements = await db.mz2_customer_refunds.find({'user_id': owner,
        'original_key': original['_id'], 'accounting_version': 2, 'recognized': True}).to_list(10001)
    if max(len(prior), len(payments), len(entitlements)) > 10000:
        raise HTTPException(409, 'refund_history_limit')
    return refund_split(original['proposal']['tax'],
        [r['proposal']['tax'] for r in prior] + [r['tax'] for r in payments if r.get('tax')]
        + [r['tax'] for r in entitlements], amount)



async def create_case(db, *, owner, actor, original_key, case_reference, amount, recognized_at, reason):
    reference = case_reference.strip()
    if not reference or not reason.strip():
        raise HTTPException(422, 'explicit_refund_identity_and_reason_required')
    key = digest([owner, 'customer_refund_case', reference])
    facts = dict(original_key=original_key, case_reference=reference, amount=money(amount),
        recognized_at=recognized_at, reason=reason.strip())
    async def write(scoped):
        original = await original_sale(scoped, owner, original_key)
        await validate_date(scoped, owner, recognized_at, original)
        prior = await scoped.mz2_customer_refunds.find_one({'_id': key})
        if prior:
            if any(prior[k] != v for k, v in facts.items()):
                raise HTTPException(409, 'refund_case_identity_conflict')
            return public(prior)
        tax = await case_tax(scoped, owner, original, facts['amount'])
        event = original['proposal']['event']
        row = dict(_id=key, id=key, user_id=owner, **facts,
            original_provider=event['provider'], original_payment_id=event['provider_payment_id'],
            order_number=event['order_number'], tax_preview=tax, recognized=False,
            state='awaiting_execution_confirmation', paid='0.00', remaining=facts['amount'],
            created_by=actor['id'], created_at=datetime.now(timezone.utc).isoformat(timespec='microseconds'))
        await scoped.mz2_customer_refunds.insert_one(row)
        return public(row)
    return await atomic_owner(db, owner, write)


async def create_bank_payment(db, *, owner, actor, original_key, case_reference, bank_account_id,
                              amount, paid_at, bank_reference, proof_name, proof_base64, execution_channel="bank", provider_refund_id=None):
    from accounting_settlement_routes import _find_bank
    reference = bank_reference.strip()
    if not case_reference.strip() or (not reference and not proof_base64):
        raise HTTPException(422, 'explicit_bank_and_refund_identity_required')
    try:
        proof = base64.b64decode(proof_base64 or '', validate=True)
    except (ValueError, TypeError):
        raise HTTPException(422, 'invalid_payment_proof') from None
    if len(proof) > 1024*1024 or (proof and not proof_name.strip()):
        raise HTTPException(422, 'payment_proof_required_max_1mb')
    if execution_channel not in {'bank','salla','tamara','tabby','emkan'}:
        raise HTTPException(422,'unsupported_refund_execution_channel')
    if execution_channel != 'bank' and provider_refund_id is not None:
        from accounting_recognition_evidence import canonical_identity, EvidenceError
        try:
            provider_refund_id = canonical_identity(provider_refund_id)
        except EvidenceError as exc:
            raise HTTPException(422, str(exc)) from exc
    identity = reference or hashlib.sha256(proof).hexdigest()
    key = digest([owner, bank_account_id if execution_channel=='bank' else execution_channel, 'customer_refund_payment', identity.upper()])
    facts = dict(original_key=original_key, case_reference=case_reference.strip(),
        bank_account_id=bank_account_id, amount=money(amount), paid_at=paid_at,
        bank_reference=reference, proof_name=proof_name, proof_sha256=hashlib.sha256(proof).hexdigest(),
        execution_channel=execution_channel, provider_refund_id=provider_refund_id)
    async def write(scoped):
        original = await original_sale(scoped, owner, original_key)
        await validate_date(scoped, owner, paid_at, original)
        bank = await _find_bank(scoped, owner, bank_account_id) if execution_channel=='bank' else None
        if execution_channel=='bank' and (not bank or bank.get('account_type') != 'bank'):
            raise HTTPException(409, 'owned_bank_required')
        prior = await scoped.mz2_customer_refund_payments.find_one({'_id':key})
        if prior:
            if any(prior[k] != v for k,v in facts.items()):
                raise HTTPException(409, 'bank_payment_identity_conflict')
            return public(prior)
        if execution_channel=='bank' and reference and await scoped.account_transactions.find_one({'user_id':owner,'account_id':bank_account_id,'reference':reference}):
            raise HTTPException(409,'bank_reference_already_recorded')
        row=dict(_id=key,id=key,user_id=owner,**facts,proof_bytes=proof,
            case_id=digest([owner,'customer_refund_case',case_reference.strip()]),
            original_provider=original['proposal']['event']['provider'],
            order_number=original['proposal']['event']['order_number'],
            bank_account_name=bank['name'] if bank else None, status='awaiting_entitlement_and_approval',
            created_by=actor['id'], created_at=datetime.now(timezone.utc).isoformat(timespec='microseconds'))
        await scoped.mz2_customer_refund_payments.insert_one(row)
        return public(row)
    return await atomic_owner(db, owner, write)


async def post_bank_payment(db, *, owner, actor, payment_id):
    async def write(scoped):
        payment = await scoped.mz2_customer_refund_payments.find_one({'id':payment_id,'user_id':owner})
        if not payment:
            raise HTTPException(404,'refund_payment_not_found')
        if payment['status']=='posted':
            return public(payment)
        row = await scoped.mz2_customer_refunds.find_one({'id':payment['case_id'],'user_id':owner})
        if not row or row['original_key']!=payment['original_key']:
            raise HTTPException(409,'matching_refund_case_required')
        if row.get('state')=='conflict':
            raise HTTPException(409,'refund_execution_conflict_requires_review')
        if not row.get('recognized') or row.get('accounting_version') != 2:
            raise HTTPException(409,'confirmed_refund_entitlement_required')
        if instant(payment['paid_at']) < instant(row['recognized_at']):
            raise HTTPException(409,'payment_before_refund_entitlement')
        if Decimal(payment['amount'])>Decimal(row['remaining']):
            raise HTTPException(409,'payment_exceeds_customer_remaining')
        channel=payment['execution_channel']
        if channel != 'bank' and payment.get('provider_refund_id'):
            from accounting_recognition_evidence import canonical_identity, EvidenceError
            try:
                canonical = canonical_identity(payment['provider_refund_id'])
            except EvidenceError as exc:
                raise HTTPException(409, str(exc)) from exc
            if canonical != payment['provider_refund_id']:
                raise HTTPException(409, 'provider_refund_identity_requires_canonical_draft')
        provider_identity = ({'$regex': r'^\s*' + re.escape(payment['provider_refund_id']) + r'\s*$'}
            if payment.get('provider_refund_id') else None)
        advance_identity = {'user_id':owner, 'status':'posted', 'execution_channel':channel}
        if channel == 'bank':
            advance_identity.update(bank_account_id=payment['bank_account_id'],
                bank_reference={'$regex': '^' + re.escape(payment['bank_reference']) + '$', '$options':'i'})
        else:
            advance_identity['provider_refund_id'] = provider_identity
        if await scoped.mz2_customer_advance_payments.find_one(advance_identity):
            raise HTTPException(409, 'refund_execution_already_accounted_as_advance')
        different_provider = channel != 'bank' and channel != row['original_provider']
        if different_provider:
            proof = payment.get('proof_bytes')
            if (not proof or not payment.get('proof_name', '').strip()
                    or not payment.get('bank_reference', '').strip()
                    or not str(payment.get('provider_refund_id') or '').strip()
                    or hashlib.sha256(proof).hexdigest() != payment.get('proof_sha256')):
                raise HTTPException(409,'documented_execution_provider_proof_required')
        from accounting_recognition_evidence import REFUNDED
        confirmed=await scoped.payment_refunds.find({'user_id':owner,'provider':row['original_provider'],
            'provider_payment_id':row['original_payment_id'],'status':{'$in':list(REFUNDED)}}).to_list(1001)
        if different_provider and confirmed:
            raise HTTPException(409,'original_provider_execution_conflicts_with_documented_payment')
        if different_provider:
            execution = await scoped.payment_refunds.find({'user_id':owner, 'provider':channel,
                'provider_refund_id':provider_identity}).to_list(2)
            if execution:
                evidence = execution[0]
                try:
                    matches = (len(execution) == 1 and not evidence.get('synthesised')
                        and evidence.get('status') in REFUNDED
                        and bool(evidence.get('source') or evidence.get('status_source'))
                        and not (evidence.get('raw') or {}).get('_fetch_error')
                        and evidence.get('currency') == 'SAR'
                        and decimal_value(evidence.get('amount')) == Decimal(payment['amount'])
                        and instant(evidence.get('refunded_at')) == instant(payment['paid_at']))
                except (ValueError, TypeError, HTTPException):
                    matches = False
                if not matches:
                    raise HTTPException(409,'documented_provider_execution_evidence_conflict')
        if channel=='bank' and confirmed:
            raise HTTPException(409,'provider_execution_evidence_conflicts_with_bank_payment')
        if channel!='bank' and confirmed:
            matching=[r for r in confirmed if str(r.get('provider_refund_id') or '').strip()==payment.get('provider_refund_id')]
            if len(matching)!=1 or Decimal(str(matching[0]['amount']))!=Decimal(payment['amount']):
                raise HTTPException(409,'provider_refund_identity_or_amount_requires_review')
        other=await scoped.mz2_customer_refund_payments.find_one({'user_id':owner,'case_id':row['id'],
            'status':'posted','execution_channel':{'$ne':channel}})
        if other:
            raise HTTPException(409,'mixed_execution_channels_require_review')
        if payment.get('provider_refund_id'):
            prior=await scoped.mz2_customer_refund_payments.find_one({'user_id':owner,'status':'posted',
                'execution_channel':channel,'provider_refund_id':provider_identity})
            legacy=await scoped.mz2_recognition_events.find_one({'user_id':owner,'status':'posted',
                'proposal.event.provider':channel,'proposal.event.canonical_event_id':provider_identity})
            if prior or legacy:
                raise HTTPException(409,'provider_refund_already_accounted')
        original=await original_sale(scoped,owner,row['original_key'])
        await validate_date(scoped, owner, payment['paid_at'], original)
        from ledger_core import post_txn_group
        from accounting_mz2_balances import read_mz2_write_balances
        target_type='bank' if channel=='bank' else 'payment_gateway'
        target_id=payment['bank_account_id'] if channel=='bank' else channel
        target_sub='main' if channel=='bank' else 'receivable'
        balances=await read_mz2_write_balances(scoped,owner=owner,
            required_accounts=[(target_type,target_id,target_sub)])
        balance=balances.net_balance(entity_type=target_type,entity_id=target_id,sub_account=target_sub)
        if balance<Decimal(payment['amount']):
            raise HTTPException(409,'insufficient_refund_execution_balance')
        liability=balances.net_balance(entity_type='liability',
            entity_id=row['id'],sub_account='customer_refund_payable')
        if -liability != Decimal(row['remaining']):
            raise HTTPException(409,'refund_liability_reconciliation_required')
        entries=[dict(entity_type='liability',entity_id=row['id'],sub_account='customer_refund_payable',
            side='debit',amount=payment['amount'],entry_type='customer_refund_payment')]
        entries.append(dict(entity_type=target_type,entity_id=target_id,sub_account=target_sub,side='credit',amount=payment['amount'],entry_type='customer_refund_payment'))
        result=await post_txn_group(scoped,user_id=owner,actor_id=actor['id'],actor_name=actor.get('name',actor['id']),
            entries=entries,txn_type='customer_refund_payment',notes='Mezan 2 approved daily customer refund',
            metadata=dict(refund_case_id=row['id'],refund_payment_id=payment_id,bank_reference=payment['bank_reference'],
                original_provider=row['original_provider'],execution_channel=channel,proof_sha256=payment['proof_sha256'],
                provider_refund_id=payment.get('provider_refund_id'),
                execution_evidence_basis='accountant_reviewed_provider_document' if different_provider else 'existing_execution_contract',
                paid_at=payment['paid_at'],accounting_at=instant(payment['paid_at']).isoformat(timespec='microseconds'),
                operation_id='MZ2-FIN-CUTOVER-001',refund_accounting_version=2,
                entitlement_txn_group_id=row['due_txn_group_id']))
        paid=Decimal(row['paid'])+Decimal(payment['amount']);remaining=Decimal(row['amount'])-paid
        changes=dict(paid=format(paid,'.2f'),remaining=format(remaining,'.2f'),state='paid' if remaining==0 else 'partially_paid')
        await scoped.mz2_customer_refunds.update_one({'_id':row['_id']},{'$set':changes})
        changes=dict(status='posted',txn_group_id=result['txn_group_id'],approved_by=actor['id'],
            entitlement_txn_group_id=row['due_txn_group_id'],accounting_version=2)
        await scoped.mz2_customer_refund_payments.update_one({'_id':payment['_id']},{'$set':changes})
        return public({**payment,**changes})
    return await atomic_owner(db,owner,write)
