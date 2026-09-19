"""Order evidence updates non-financial refund drafts, never ledger entries."""
from datetime import datetime, timezone
from decimal import Decimal
from accounting_atomic import atomic_owner
from accounting_receivable_service import digest, managed_owner
from accounting_sales_tax import decimal_value, TaxError
from accounting_recognition_evidence import REFUNDED


async def observe_refund(db, *, owner, order_number, source, payload=None, _queued=False):
    if not await managed_owner(db, owner):
        return {'state':'not_managed','items':[]}
    if not _queued:
        from accounting_ingress import ingest
        data = payload or {}
        return await ingest(db, owner=owner, kind="refund_observation", evidence={
            "order_number": str(order_number), "source": source,
            "payload": {key: data[key] for key in ("status", "payment_actions") if key in data},
        })
    async def write(scoped):
        originals=await scoped.mz2_recognition_events.find({'user_id':owner,'status':'posted',
            'proposal.event.kind':'sale','proposal.event.order_number':str(order_number)}).to_list(101)
        results=[]
        if len(originals)!=1:
            results=[{'state':'needs_review','reason':'unique_original_payment_required'}]
        else:
            original=originals[0];event=original['proposal']['event']
            provider=event['provider'];payment=event['provider_payment_id']
            external=await scoped.payment_refunds.find({'user_id':owner,'provider':provider,
                'provider_payment_id':payment,'status':{'$in':list(REFUNDED)}}).to_list(1001)
            movements=await scoped.mz2_customer_refund_payments.find({'user_id':owner,
                'original_key':original['_id'],'status':'posted'}).to_list(1001)
            historical=await scoped.mz2_recognition_events.find({'user_id':owner,
                'original_key':original['_id'],'status':'posted','proposal.event.kind':'refund'}).to_list(1001)
            bank_paid=any(x['execution_channel']=='bank' for x in movements)
            if external and bank_paid:
                reason='provider_refund_after_bank_payment_possible_double_payment'
                await scoped.mz2_customer_refunds.update_many({'user_id':owner,'original_key':original['_id']},
                    {'$set':{'state':'conflict','conflict_reason':reason}})
                results.append({'state':'needs_review','reason':reason,'provider':provider})
            data=payload or {};status=data.get('status') or {}
            status=status.get('slug') if isinstance(status,dict) else status
            action=(data.get('payment_actions') or {}).get('refund_action') or {}
            raw=action.get('refund_amount')
            if isinstance(raw,dict):
                raw=raw.get('amount') if raw.get('currency')=='SAR' else None
            target=None
            try:
                if status in {'canceled','cancelled','refunded'}:
                    target=Decimal(original['proposal']['tax']['gross'])
                elif raw is not None:
                    target=decimal_value(raw)
                elif external and all(x.get('provider_refund_id') for x in external):
                    identities={x['provider_refund_id']:decimal_value(x['amount']) for x in external}
                    if len(identities)!=len(external):
                        raise TaxError('duplicate_source_refund_identity')
                    target=sum(identities.values(),Decimal(0))
                if target is not None and (target<=0 or target>Decimal(original['proposal']['tax']['gross'])):
                    raise TaxError('refund_source_amount_conflict')
            except (TaxError,KeyError):
                target=None
            cases=await scoped.mz2_customer_refunds.find({'user_id':owner,'original_key':original['_id']}).to_list(1001)
            if target is None:
                results.append({'state':'needs_review','reason':'refund_amount_or_identity_required','provider':provider})
            elif not bank_paid or not external:
                internal_reference='order-refund-review:'+original['_id']
                key=digest([owner,'customer_refund_case',internal_reference])
                existing=next((x for x in cases if x['id']==key),None)
                others=[x for x in cases if x['id']!=key]
                other_total=sum((Decimal(x['amount']) for x in others),Decimal(0)) + sum(
                    (Decimal(x['proposal']['tax']['gross']) for x in historical),Decimal(0))
                # A cumulative update matching already recorded daily cases is
                # evidence on those cases, not a second refund identity.
                if target<=other_total:
                    for row in others:
                        await scoped.mz2_customer_refunds.update_one({'_id':row['_id']},
                            {'$addToSet':{'order_evidence':source}})
                    results.append({'state':'matched_daily_movements','case_ids':[x['id'] for x in others]})
                else:
                    wanted=target-other_total
                    if existing and existing.get('recognized'):
                        # Confirmed entitlement is immutable. A later snapshot
                        # may require a separate additional entitlement review.
                        await scoped.mz2_customer_refunds.update_one({'_id':key},
                            {'$addToSet':{'order_evidence':source}})
                        if wanted > Decimal(existing['amount']):
                            await scoped.mz2_customer_refunds.update_one({'_id':key},{'$set':{
                                'state':'conflict','conflict_reason':'additional_entitlement_requires_review'}})
                            results.append({'state':'needs_review','reason':'additional_entitlement_requires_review'})
                        else:
                            results.append({'state':'matched_confirmed_entitlement','case_id':key})
                    elif existing:
                        # Out-of-order stale snapshots cannot shrink a draft or
                        # a previously confirmed payment.
                        wanted=max(wanted,Decimal(existing['amount']))
                        changes={'amount':format(wanted,'.2f'),
                            'remaining':format(wanted-Decimal(existing['paid']),'.2f')}
                        if changes['remaining']!='0.00' and existing['state']!='conflict':
                            changes['state']='awaiting_execution_confirmation'
                        await scoped.mz2_customer_refunds.update_one({'_id':key},
                            {'$set':changes,'$addToSet':{'order_evidence':source}})
                    else:
                        now=datetime.now(timezone.utc).isoformat()
                        await scoped.mz2_customer_refunds.insert_one(dict(_id=key,id=key,user_id=owner,
                            original_key=original['_id'],case_reference=internal_reference,
                            original_provider=provider,original_payment_id=payment,order_number=str(order_number),
                            amount=format(wanted,'.2f'),remaining=format(wanted,'.2f'),paid='0.00',recognized=False,
                            recognized_at=now,created_at=now,created_by='verified_order_evidence',
                            state='awaiting_execution_confirmation',reason='Order evidence only; execution unconfirmed',
                            identity_kind='internal_order_review_not_provider_id',order_evidence=[source]))
                    results.append({'state':'awaiting_execution_confirmation','case_id':key,'amount':format(wanted,'.2f')})
            for refund in external:
                rid=refund.get('provider_refund_id')
                matches=[x for x in movements if x.get('provider_refund_id')==rid and x['execution_channel']==provider] if rid else []
                matches += [x for x in historical if x['proposal']['event']['canonical_event_id']==rid] if rid else []
                results.append({'state':'matched_daily_movement' if len(matches)==1 else 'needs_review',
                    'reason':None if len(matches)==1 else 'awaiting_daily_refund_recording',
                    'refund_id':rid,'provider':provider})
        result={'state':'needs_review' if any(x['state']=='needs_review' for x in results) else 'awaiting_execution_confirmation',
            'items':results,'order_number':str(order_number),'source':source,'financial_write':False}
        await scoped.mz2_order_refund_reviews.update_one({'_id':digest([owner,str(order_number)])},
            {'$set':{'user_id':owner,**result},'$addToSet':{'sources':source}},upsert=True)
        return result
    return await atomic_owner(db,owner,write)
