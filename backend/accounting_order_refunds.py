"""Mezan 2 refund ingress: Salla is an order source, not a payment provider.

Only approved daily movements create new refund journals. Order totals,
status, notification hashes and statement row numbers are never execution IDs.
"""
from decimal import Decimal
from accounting_receivable_service import prepare, digest
from accounting_recognition_evidence import EvidenceError
from accounting_sales_tax import TaxError


async def process_order_refunds(db, *, owner, order_number, source, payload=None):
    """Verified notifications only maintain drafts/review; never post a journal."""
    from accounting_refund_drafts import observe_refund
    return await observe_refund(db, owner=owner, order_number=order_number, source=source, payload=payload)


def refund_amount(row):
    return Decimal(str(row.get('actual_refund_amount') or 0)) + Decimal(str(row.get('actual_partial_refund_amount') or 0))


async def refund_review_reasons(db, owner, draft):
    if not draft.get('source_file_id'):
        return []
    rows = await db.settlement_entries.find({'user_id': owner,
        'file_id': draft['source_file_id']}).to_list(10001)
    if len(rows) > 10000:
        return [{'code':'refund_evidence_limit', 'message':'عدد سطور الكشف يحتاج مراجعة قبل الترحيل'}]
    reasons = []
    for row in rows:
        if refund_amount(row) <= 0:
            continue
        links = await db.mz2_statement_refund_links.find({
            'user_id': owner, 'draft_id': draft['id'], 'entry_id': row['id']}).to_list(10001)
        if not links or sum(Decimal(x['amount']) for x in links) != refund_amount(row):
            reasons.append({'code': 'refund_identity_review:' + row['id'],
                'message': 'استرداد الطلب ' + str(row.get('order_number') or '') +
                           ' بانتظار تسجيل الاسترداد واعتماد حركته اليومية وربطها؛ رقم الطلب وحده لا يكفي'})
    return reasons


async def link_statement_refund(db, *, owner, draft_id, entry_id, actor, refund_id=None, refund_ids=None):
    from fastapi import HTTPException
    from accounting_atomic import atomic_owner
    identities = refund_ids if refund_ids is not None else [refund_id]
    if not identities or len(identities)>100 or any(not x for x in identities) or len(set(identities))!=len(identities):
        raise HTTPException(422,'distinct_refund_identities_required')
    async def commit(scoped):
        draft = await scoped.accounting_settlements_v2.find_one({'user_id': owner, 'id': draft_id})
        if not draft:
            raise HTTPException(404, 'statement_not_found')
        if draft['status'] not in {'draft', 'needs_review', 'rejected'}:
            raise HTTPException(409, 'statement_not_editable')
        row = await scoped.settlement_entries.find_one({'user_id': owner, 'id': entry_id,
            'file_id': draft.get('source_file_id')})
        if not row or refund_amount(row)<=0:
            raise HTTPException(409,'refund_statement_row_required')
        proposals = []
        for rid in identities:
            payments = await scoped.mz2_customer_refund_payments.find({'user_id':owner,
                'execution_channel':draft['provider'], 'status':'posted',
                '$or':[{'id':rid},{'provider_refund_id':rid}]}).to_list(2)
            if payments:
                if len(payments)!=1 or payments[0]['order_number']!=str(row.get('order_number')):
                    raise HTTPException(409,'daily_refund_identity_or_order_conflict')
                payment=payments[0]
                case=await scoped.mz2_customer_refunds.find_one({'id':payment['case_id'],'user_id':owner})
                if not case or case['state']=='conflict':
                    raise HTTPException(409,'refund_execution_conflict_requires_review')
                proposal={'state':'already_posted','event_key':'daily-refund:'+payment['id'],
                    'txn_group_id':payment['txn_group_id'],'event':{'order_number':payment['order_number'],
                        'provider':payment['execution_channel'],'amount':payment['amount']}}
                proposals.append((rid,proposal))
                continue
            refunds = await scoped.payment_refunds.find({'user_id': owner, 'provider': draft['provider'],
                'provider_refund_id': rid}).to_list(2)
            if len(refunds) != 1:
                raise HTTPException(409, 'unique_refund_identity_required')
            refund = refunds[0]
            proposal = await prepare(scoped, owner=owner, provider=draft['provider'],
                payment_id=refund['provider_payment_id'], refund_id=rid)
            event = proposal['event']
            if proposal['state'] != 'already_posted' or event['order_number'] != str(row.get('order_number')):
                raise HTTPException(409, 'refund_source_or_journal_conflict')
            proposals.append((rid,proposal))
        if sum(Decimal(p['event']['amount']) for _,p in proposals) != refund_amount(row):
            raise HTTPException(409,'refund_source_or_journal_conflict')
        keys = {digest([owner,p['event_key']]) for _,p in proposals}
        prior_row = await scoped.mz2_statement_refund_links.find({'user_id':owner,'draft_id':draft_id,'entry_id':entry_id}).to_list(101)
        if prior_row and {x['_id'] for x in prior_row} != keys:
            raise HTTPException(409,'statement_row_already_linked')
        for rid,proposal in proposals:
            event = proposal['event'];key=digest([owner,proposal['event_key']])
            prior = await scoped.mz2_statement_refund_links.find_one({'_id': key})
            if prior and (prior['draft_id'], prior['entry_id']) != (draft_id, entry_id):
                raise HTTPException(409, 'refund_already_linked_to_statement')
            await scoped.mz2_statement_refund_links.update_one({'_id':key}, {'$setOnInsert':dict(
                user_id=owner, draft_id=draft_id, entry_id=entry_id, refund_id=rid,
                provider=event['provider'], event_key=proposal['event_key'], amount=event['amount'],
                txn_group_id=proposal['txn_group_id'], linked_by=actor['id'])}, upsert=True)
        from accounting_settlement_routes import _recomputed_draft
        updated = await _recomputed_draft(scoped, owner_id=owner, draft=draft)
        await scoped.accounting_settlements_v2.update_one({'id':draft_id,'user_id':owner},
            {'$set': {'review_reasons':updated['review_reasons']}, '$inc':{'version':1}})
        return {'linked':True,'txn_group_id':proposals[0][1]['txn_group_id'],
                'txn_group_ids':[p['txn_group_id'] for _,p in proposals],'financial_write':False}
    return await atomic_owner(db, owner, commit)
