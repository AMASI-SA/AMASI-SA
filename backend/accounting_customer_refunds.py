"""Customer refund entitlement and execution are separate economic events.

An order edit is never evidence of a completed payment. Explicit merchant
case identities are not provider refund IDs. All financial writes share the
Mezan 2 transaction boundary with recognition and provider settlement.
"""
from datetime import datetime, timezone
from decimal import Decimal
import base64
import hashlib
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
    if when < instant(await cutoff_for(db, owner)) or when < instant(original['proposal']['event']['recognized_at']):
        raise HTTPException(409, 'refund_date_before_recognition_or_cutoff')
    if when > datetime.now(timezone.utc):
        raise HTTPException(409, 'future_refund_date')


async def case_tax(db, owner, original, amount, exclude=None):
    prior = await db.mz2_recognition_events.find({'user_id': owner,
        'original_key': original['_id'], 'status': 'posted'}).to_list(10001)
    cases = await db.mz2_customer_refunds.find({'user_id': owner,
        'original_key': original['_id'], 'recognized': True}).to_list(10001)
    if len(prior) > 10000 or len(cases) > 10000:
        raise HTTPException(409, 'refund_history_limit')
    return refund_split(original['proposal']['tax'],
        [r['proposal']['tax'] for r in prior] + [r['tax'] for r in cases if r['id'] != exclude], amount)


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
            state='awaiting_entitlement_approval', paid='0.00', remaining=facts['amount'],
            created_by=actor['id'], created_at=datetime.now(timezone.utc).isoformat())
        await scoped.mz2_customer_refunds.insert_one(row)
        return public(row)
    return await atomic_owner(db, owner, write)


async def post_case(db, *, owner, actor, case_id):
    async def write(scoped):
        row = await scoped.mz2_customer_refunds.find_one({'id': case_id, 'user_id': owner})
        if not row:
            raise HTTPException(404, 'refund_case_not_found')
        if row['recognized']:
            return public(row)
        original = await original_sale(scoped, owner, row['original_key'])
        tax = await case_tax(scoped, owner, original, row['amount'])
        if tax != row['tax_preview']:
            raise HTTPException(409, 'refund_allocation_changed_review_required')
        # Existing confirmed provider refunds must be reconciled before a new
        # entitlement is approved; never silently count the same return twice.
        if await scoped.payment_refunds.find_one({'user_id': owner, 'provider': row['original_provider'],
            'provider_payment_id': row['original_payment_id'], 'status': {'$in': ['completed', 'succeeded', 'refunded']}}):
            raise HTTPException(409, 'existing_provider_refund_requires_review')
        entries = [dict(entity_type='customer', entity_id=case_id, sub_account='refund_payable',
            side='credit', amount=tax['gross'], entry_type='customer_refund_due')]
        for entity, identifier, value in [('revenue','bnpl_sales',tax['net']), ('tax','sales_vat_payable',tax['tax'])]:
            if Decimal(value):
                entries.append(dict(entity_type=entity, entity_id=identifier, side='debit',
                    amount=value, entry_type='customer_refund_due'))
        from ledger_core import post_txn_group
        result = await post_txn_group(scoped, user_id=owner, actor_id=actor['id'],
            actor_name=actor.get('name', actor['id']), entries=entries, txn_type='customer_refund_due',
            notes='ميزان 2 — إثبات مستحق استرداد للعميل', metadata=dict(
                refund_case_id=case_id, original_recognition_key=row['original_key'],
                order_reference_id=row['order_number'], original_provider=row['original_provider'],
                sales_tax=tax, recognized_at=row['recognized_at']))
        changes=dict(recognized=True, state='due', tax=tax, due_txn_group_id=result['txn_group_id'], approved_by=actor['id'])
        await scoped.mz2_customer_refunds.update_one({'_id':row['_id']},{'$set':changes})
        return public({**row, **changes})
    return await atomic_owner(db, owner, write)


async def create_bank_payment(db, *, owner, actor, original_key, case_reference, bank_account_id,
                              amount, paid_at, bank_reference, proof_name, proof_base64):
    from accounting_settlement_routes import _find_bank
    reference = bank_reference.strip()
    if not reference or not case_reference.strip():
        raise HTTPException(422, 'explicit_bank_and_refund_identity_required')
    try:
        proof = base64.b64decode(proof_base64, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(422, 'invalid_payment_proof') from None
    if not proof or len(proof) > 1024*1024 or not proof_name.strip():
        raise HTTPException(422, 'payment_proof_required_max_1mb')
    key = digest([owner, bank_account_id, 'customer_refund_payment', reference])
    facts = dict(original_key=original_key, case_reference=case_reference.strip(),
        bank_account_id=bank_account_id, amount=money(amount), paid_at=paid_at,
        bank_reference=reference, proof_name=proof_name, proof_sha256=hashlib.sha256(proof).hexdigest())
    async def write(scoped):
        original = await original_sale(scoped, owner, original_key)
        await validate_date(scoped, owner, paid_at, original)
        bank = await _find_bank(scoped, owner, bank_account_id)
        if not bank or bank.get('account_type') != 'bank':
            raise HTTPException(409, 'owned_bank_required')
        prior = await scoped.mz2_customer_refund_payments.find_one({'_id':key})
        if prior:
            if any(prior[k] != v for k,v in facts.items()):
                raise HTTPException(409, 'bank_payment_identity_conflict')
            return public(prior)
        if await scoped.account_transactions.find_one({'user_id':owner,'account_id':bank_account_id,'reference':reference}):
            raise HTTPException(409,'bank_reference_already_recorded')
        row=dict(_id=key,id=key,user_id=owner,**facts,proof_bytes=proof,
            case_id=digest([owner,'customer_refund_case',case_reference.strip()]),
            original_provider=original['proposal']['event']['provider'],
            order_number=original['proposal']['event']['order_number'], execution_channel='bank',
            bank_account_name=bank['name'], status='awaiting_entitlement_and_approval',
            created_by=actor['id'], created_at=datetime.now(timezone.utc).isoformat())
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
        row = await scoped.mz2_customer_refunds.find_one({'id':payment['case_id'],'user_id':owner,'recognized':True})
        if not row or row['original_key']!=payment['original_key']:
            raise HTTPException(409,'matching_approved_refund_entitlement_required')
        if row.get('state')=='conflict':
            raise HTTPException(409,'refund_execution_conflict_requires_review')
        if Decimal(payment['amount'])>Decimal(row['remaining']):
            raise HTTPException(409,'payment_exceeds_customer_remaining')
        if await scoped.payment_refunds.find_one({'user_id':owner,'provider':row['original_provider'],
            'provider_payment_id':row['original_payment_id'],'status':{'$in':['completed','succeeded','refunded']}}):
            raise HTTPException(409,'provider_execution_requires_review_before_bank_payment')
        from ledger_core import post_txn_group, compute_balance
        balance = await compute_balance(scoped,user_id=owner,entity_type='bank',entity_id=payment['bank_account_id'],sub_account='main')
        if Decimal(str(balance['net_balance'])) < Decimal(payment['amount']):
            raise HTTPException(409,'insufficient_bank_balance')
        entries=[dict(entity_type='customer',entity_id=row['id'],sub_account='refund_payable',side='debit',amount=payment['amount'],entry_type='customer_refund_payment'),
                 dict(entity_type='bank',entity_id=payment['bank_account_id'],sub_account='main',side='credit',amount=payment['amount'],entry_type='customer_refund_payment')]
        result=await post_txn_group(scoped,user_id=owner,actor_id=actor['id'],actor_name=actor.get('name',actor['id']),
            entries=entries,txn_type='customer_refund_payment',notes='ميزان 2 — تسجيل تحويل استرداد منفذ من البنك',
            metadata=dict(refund_case_id=row['id'],refund_payment_id=payment_id,bank_reference=payment['bank_reference'],
                original_provider=row['original_provider'],execution_channel='bank',proof_sha256=payment['proof_sha256'],paid_at=payment['paid_at']))
        paid=Decimal(row['paid'])+Decimal(payment['amount']);remaining=Decimal(row['amount'])-paid
        await scoped.mz2_customer_refunds.update_one({'_id':row['_id']},{'$set':dict(paid=format(paid,'.2f'),remaining=format(remaining,'.2f'),state='paid' if remaining==0 else 'partially_paid')})
        changes=dict(status='posted',txn_group_id=result['txn_group_id'],approved_by=actor['id'])
        await scoped.mz2_customer_refund_payments.update_one({'_id':payment['_id']},{'$set':changes})
        return public({**payment,**changes})
    return await atomic_owner(db,owner,write)


async def provider_case_guard(db, owner, original_key, refund):
    """No guessing: an external payment can overlap any return on this sale."""
    cases=await db.mz2_customer_refunds.find({'user_id':owner,'original_key':original_key}).to_list(10001)
    payments=await db.mz2_customer_refund_payments.find({'user_id':owner,'original_key':original_key}).to_list(10001)
    if not cases and not payments:
        return
    from accounting_recognition_evidence import EvidenceError
    if any(x.get('status')=='posted' for x in payments):
        raise EvidenceError('provider_refund_after_bank_payment_possible_double_payment')
    raise EvidenceError('provider_refund_requires_explicit_customer_case_reconciliation')
