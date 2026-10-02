"""Explicit confirmed entitlement, distinct from notification and execution."""
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import HTTPException
from accounting_atomic import atomic_owner
from accounting_customer_refunds import public, money, original_sale, validate_date, case_tax
from accounting_receivable_service import digest, OPERATION
from accounting_sales_tax import instant
from accounting_recognition_native import verify_event_journal


async def recognize_entitlement(db, *, owner, actor, case_id, amount, recognized_at, reason, evidence_ref):
    facts = dict(amount=money(amount), recognized_at=instant(recognized_at).isoformat(timespec='microseconds'),
        reason=reason.strip(), evidence_ref=evidence_ref.strip())
    if not facts['reason'] or not facts['evidence_ref']:
        raise HTTPException(422, 'confirmed_entitlement_evidence_required')

    async def write(scoped):
        row = await scoped.mz2_customer_refunds.find_one({'id':case_id,'user_id':owner})
        if not row:
            raise HTTPException(404, 'refund_case_not_found')
        if row.get('accounting_version') == 2 and row.get('recognized'):
            if row.get('confirmation') != facts:
                raise HTTPException(409, 'confirmed_entitlement_is_immutable')
            await verify_event_journal(scoped, owner, row.get('due_txn_group_id'),
                refund_case_id=case_id, evidence_ref=facts['evidence_ref'], sales_tax=row['tax'])
            return public(row)
        # Never convert previously posted daily/cash-basis history in place.
        if row.get('recognized') or Decimal(row.get('paid','0')) != 0:
            raise HTTPException(409, 'legacy_paid_refund_requires_separate_review')
        if row.get('state') == 'conflict' or row['amount'] != facts['amount']:
            raise HTTPException(409, 'refund_draft_changed_or_conflicted_refresh_required')
        original = await original_sale(scoped, owner, row['original_key'])
        await validate_date(scoped, owner, facts['recognized_at'], original)
        if instant(facts['recognized_at']) < instant(original['proposal']['event']['recognized_at']):
            raise HTTPException(409, 'entitlement_before_original_sale')
        # An independently supplied evidence reference cannot accrue twice
        # under two different internal case references.
        evidence_key = digest([owner, row['original_key'], 'refund_entitlement', facts['evidence_ref']])
        if await scoped.mz2_refund_entitlements.find_one({'_id':evidence_key}):
            raise HTTPException(409, 'entitlement_evidence_already_recognized')
        tax = await case_tax(scoped, owner, original, facts['amount'])
        entries = [dict(entity_type=entity,entity_id=identifier,side='debit',amount=value,
            entry_type='customer_refund_due') for entity,identifier,value in
            [('revenue','bnpl_sales',tax['net']),('tax','sales_vat_payable',tax['tax'])] if Decimal(value)]
        entries.append(dict(entity_type='liability',entity_id=case_id,sub_account='customer_refund_payable',
            side='credit',amount=facts['amount'],entry_type='customer_refund_due'))
        from accounting_recognition_native import post_recognition_journal
        group = await post_recognition_journal(scoped,user_id=owner,actor_id=actor['id'],actor_name=actor.get('name',actor['id']),
            idempotency_key='refund_entitlement:' + evidence_key, effective_at=facts['recognized_at'],
            permission='accounting.refunds.recognize',
            entries=entries,txn_type='customer_refund_due',notes=facts['reason'],metadata=dict(
                operation_id=OPERATION,refund_accounting_version=2,refund_case_id=case_id,
                original_recognition_key=row['original_key'],accounting_at=facts['recognized_at'],
                recognized_at=facts['recognized_at'],evidence_ref=facts['evidence_ref'],sales_tax=tax))
        now = datetime.now(timezone.utc).isoformat(timespec='microseconds')
        await scoped.mz2_refund_entitlements.insert_one(dict(_id=evidence_key,user_id=owner,
            case_id=case_id,original_key=row['original_key'],confirmation=facts,
            txn_group_id=group['txn_group_id'],approved_by=actor['id'],recorded_at=now))
        changes = dict(recognized=True,accounting_version=2,state='due',tax=tax,
            recognized_at=facts['recognized_at'],confirmation=facts,due_txn_group_id=group['txn_group_id'],
            confirmed_by=actor['id'],confirmed_at=now)
        await scoped.mz2_customer_refunds.update_one({'_id':row['_id']},{'$set':changes})
        return public({**row,**changes})
    return await atomic_owner(db, owner, write)


async def period_journal(db, *, owner, from_at, to_at):
    start, end = instant(from_at), instant(to_at)
    if start >= end:
        raise HTTPException(422, 'increasing_period_required')
    # Accounting date is distinct from the real insertion/audit timestamp.
    # Half-open periods avoid counting a midnight entry in both months.
    from accounting_mz2_reports import read_mz2_ledger
    scope = await read_mz2_ledger(db, owner=owner)
    if scope['status'] != 'available':
        return {**scope, 'from_at': start.isoformat(), 'to_at': end.isoformat()}
    from accounting_recognition_native import native_rows
    native = await native_rows(db, owner)
    refund_ids = {row['id'] for row in native
                  if (row.get('metadata') or {}).get('refund_accounting_version') == 2}
    # Native reversals deliberately retain their own immutable metadata/date.
    # Follow their verified original-leg identity, including when the original
    # refund belongs to an earlier reporting period; never rewrite that history.
    rows = [row for row in native
            if (row['id'] in refund_ids or (row.get('entry_type') == 'reversal'
                and (row.get('metadata') or {}).get('reverses_entry_id') in refund_ids))
            and start <= instant(row['effective_at']) < end]
    return dict(from_at=start.isoformat(timespec='microseconds'),to_at=end.isoformat(timespec='microseconds'),items=rows,
        scope='confirmed_refund_entitlements_and_payments_v2')
