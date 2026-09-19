"""Non-ledger bank receipt intake shared by web and future mobile clients.

Every mutation uses the existing owner transaction. Receipt identities and
explicit one-to-one links survive lost responses without a second deposit.
"""
import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from accounting_atomic import atomic_owner


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def money(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number != number.quantize(Decimal('.01')):
            raise ValueError()
        return format(number, '.2f')
    except (InvalidOperation, ValueError):
        raise HTTPException(422, 'receipt_amount_invalid') from None


def bank_reference(message):
    # Only explicitly labelled references; never infer an identity from amounts,
    # account numbers, dates or an unlabelled number in a bank message.
    matches = re.findall(r'(?:مرجع|المرجع|رقم العملية|reference|ref|transaction\s*id)\s*[:#：-]\s*([A-Za-z0-9][A-Za-z0-9/_-]{2,79})', message, re.I)
    values = sorted(set(x.upper() for x in matches))
    if len(values) > 1:
        raise HTTPException(409, 'ambiguous_bank_reference')
    return values[0] if values else None


def public(row):
    return {k: v for k, v in row.items() if k != '_id'}


async def create_receipt(db, *, owner, actor, provider, amount, bank_message, received_on, request_id):
    from accounting_settlement_routes import _verified_binding_bank_id, _find_bank
    if provider not in {'salla', 'tamara', 'tabby', 'emkan'}:
        raise HTTPException(422, 'unsupported_provider')
    value = money(amount)
    try:
        received_on = date.fromisoformat(received_on).isoformat()
    except ValueError:
        raise HTTPException(422, 'receipt_date_invalid') from None
    message = bank_message.strip()
    if not message:
        raise HTTPException(422, 'bank_message_required')
    reference = bank_reference(message)

    async def commit(scoped):
        bank_id = await _verified_binding_bank_id(scoped, owner, provider)
        bank = await _find_bank(scoped, owner, bank_id)
        if not bank or bank.get('account_type') != 'bank':
            raise HTTPException(409, 'verified_provider_bank_required')
        facts = dict(provider=provider, amount=value, bank_message=message,
                     received_on=received_on, bank_account_id=bank_id)
        fingerprint = digest(facts)
        request_key = digest([owner, request_id])
        request = await scoped.mz2_bank_receipt_requests.find_one({'_id': request_key})
        if request:
            if request['fingerprint'] != fingerprint:
                raise HTTPException(409, 'receipt_request_conflict')
            return public(await scoped.mz2_bank_receipts.find_one({'id': request['receipt_id'], 'user_id': owner}))
        identity = digest([owner, bank_id, 'reference', reference] if reference else
                          [owner, bank_id, 'message', ' '.join(message.split())])
        previous = await scoped.mz2_bank_receipts.find_one({'_id': identity})
        if previous and previous['fingerprint'] != fingerprint:
            raise HTTPException(409, 'bank_receipt_identity_conflict')
        # A missing bank reference is valid draft evidence. Similar amounts
        # are candidates for explicit reconciliation, never an identity.
        if reference:
            prior_bank = await scoped.account_transactions.find_one({
                'user_id': owner, 'account_id': bank_id, 'reference': reference,
                'status': {'$nin': ['cancelled', 'deleted', 'reversed']},
            })
            if prior_bank:
                raise HTTPException(409, 'bank_reference_already_recorded')
        row = previous or dict(_id=identity, id=str(uuid.uuid4()), user_id=owner,
            **facts, currency='SAR', bank_account_name=bank['name'], bank_reference=reference,
            fingerprint=fingerprint, status='waiting_statement', settlement_id=None,
            created_by=actor['id'], created_at=datetime.now(timezone.utc).isoformat(),
            source='manual_bank_message', ledger_txn_group_id=None)
        if not previous:
            await scoped.mz2_bank_receipts.insert_one(row)
        await scoped.mz2_bank_receipt_requests.insert_one(dict(_id=request_key,
            fingerprint=fingerprint, receipt_id=row['id'], user_id=owner))
        return public(row)
    return await atomic_owner(db, owner, commit)


async def receipt_reasons(db, owner, draft):
    reasons = []
    if not draft.get('source_file_id') or not draft.get('source_file_hash'):
        reasons.append(dict(code='statement_required', message='يجب رفع أصل كشف المنصة'))
    rid = draft.get('bank_receipt_id')
    receipt = await db.mz2_bank_receipts.find_one({'user_id': owner, 'id': rid}) if rid else None
    if not receipt:
        return reasons + [dict(code='bank_receipt_required', message='بانتظار تسجيل المبلغ الواصل وربطه من الحركات المالية اليومية')]
    if receipt.get('settlement_id') != draft.get('id') or receipt.get('status') != 'linked':
        reasons.append(dict(code='receipt_link_invalid', message='ربط المبلغ الواصل غير صالح أو استُخدم سابقًا'))
    if receipt.get('provider') != draft.get('provider') or receipt.get('bank_account_id') != draft.get('bank_account_id') or receipt.get('currency') != draft.get('currency'):
        reasons.append(dict(code='receipt_scope_conflict', message='المنصة أو البنك أو العملة لا تطابق الكشف'))
    if Decimal(receipt['amount']) != Decimal(str((draft.get('amounts') or {}).get('reported_net') or 0)):
        reasons.append(dict(code='receipt_amount_difference', message='المبلغ الواصل لا يطابق صافي الكشف'))
    return reasons


async def link_receipt(db, *, owner, actor, draft_id, receipt_id):
    async def commit(scoped):
        draft = await scoped.accounting_settlements_v2.find_one({'id': draft_id, 'user_id': owner})
        receipt = await scoped.mz2_bank_receipts.find_one({'id': receipt_id, 'user_id': owner})
        if not draft or not receipt:
            raise HTTPException(404, 'receipt_or_statement_not_found')
        if draft.get('bank_receipt_id') == receipt_id and receipt.get('settlement_id') == draft_id:
            return public(draft)
        if draft.get('status') not in {'draft', 'needs_review', 'rejected'}:
            raise HTTPException(409, 'statement_not_editable')
        if receipt.get('settlement_id') or receipt.get('status') != 'waiting_statement':
            raise HTTPException(409, 'receipt_already_linked')
        if draft.get('bank_receipt_id') or draft.get('bank_transaction_id'):
            raise HTTPException(409, 'statement_already_linked')
        if receipt['provider'] != draft['provider'] or receipt['bank_account_id'] != draft['bank_account_id']:
            raise HTTPException(409, 'receipt_scope_conflict')
        linked_at = datetime.now(timezone.utc).isoformat()
        await scoped.mz2_bank_receipts.update_one({'id': receipt_id, 'user_id': owner}, {'$set': dict(
            settlement_id=draft_id, status='linked', linked_by=actor['id'], linked_at=linked_at)})
        update = dict(bank_receipt_id=receipt_id, status='draft', workflow_state='draft',
                      updated_at=linked_at, updated_by=actor['id'], receipt_linked_at=linked_at)
        # Persist fresh blockers with the link: the UI reads the saved draft,
        # so a stale "receipt required" reason would disable submission.
        from accounting_settlement_routes import _recomputed_draft
        refreshed = await _recomputed_draft(scoped, owner_id=owner, draft={**draft, **update})
        update.update({key: refreshed[key] for key in ('review_reasons', 'calculation', 'journal_preview')})
        await scoped.accounting_settlements_v2.update_one({'id': draft_id, 'user_id': owner},
            {'$set': update, '$inc': {'version': 1}})
        return public({**draft, **update, 'version': int(draft.get('version', 1)) + 1})
    return await atomic_owner(db, owner, commit)


async def consume_receipt(db, owner, draft, group):
    reasons = await receipt_reasons(db, owner, draft)
    if reasons:
        raise HTTPException(409, {'code': 'settlement_receipt_blocked', 'reasons': reasons})
    result = await db.mz2_bank_receipts.update_one(
        {'id': draft['bank_receipt_id'], 'user_id': owner, 'status': 'linked', 'settlement_id': draft['id']},
        {'$set': {'status': 'posted', 'ledger_txn_group_id': group}})
    if result.matched_count != 1:
        raise HTTPException(409, 'receipt_consumption_conflict')


def install_accounting_receipt_routes(router, db, current_user):
    from fastapi import Depends
    from pydantic import BaseModel, ConfigDict, Field
    from accounting_settlement_routes import _scope, _binding_view

    class ReceiptIn(BaseModel):
        model_config = ConfigDict(extra='forbid')
        provider: str
        amount: str = Field(max_length=30)
        bank_message: str = Field(min_length=1, max_length=4000)
        received_on: str = Field(min_length=10, max_length=10)
        request_id: str = Field(min_length=16, max_length=100)

    class LinkIn(BaseModel):
        model_config = ConfigDict(extra='forbid')
        receipt_id: str = Field(min_length=1, max_length=120)

    async def view_scope(user):
        from accounting_module_contract import accounting_permissions_for_user
        from accounting_module_status_routes import fresh_accounting_user
        actor = await fresh_accounting_user(db, user)
        permissions = accounting_permissions_for_user(actor)
        permission = 'accounting.movements.view' if 'accounting.movements.view' in permissions else 'accounting.settlements.view'
        return await _scope(db, user, permission)

    @router.get('/accounting-module/bank-receipts')
    async def list_receipts(user: dict = Depends(current_user)):
        _, owner = await view_scope(user)
        rows = await db.mz2_bank_receipts.find({'user_id': owner}, {'_id': 0}).sort('created_at', -1).to_list(200)
        bindings = [await _binding_view(db, owner, provider) for provider in ['salla', 'tamara', 'tabby', 'emkan']]
        return dict(items=rows, bindings=bindings, auto_link=False)

    @router.post('/accounting-module/bank-receipts')
    async def add_receipt(payload: ReceiptIn, user: dict = Depends(current_user)):
        actor, owner = await _scope(db, user, 'accounting.receipts.create')
        return await create_receipt(db, owner=owner, actor=actor, **payload.model_dump())

    @router.put('/accounting-module/settlements/drafts/{draft_id}/receipt')
    async def attach(draft_id: str, payload: LinkIn, user: dict = Depends(current_user)):
        actor, owner = await _scope(db, user, 'accounting.drafts.create')
        return await link_receipt(db, owner=owner, actor=actor, draft_id=draft_id, receipt_id=payload.receipt_id)
