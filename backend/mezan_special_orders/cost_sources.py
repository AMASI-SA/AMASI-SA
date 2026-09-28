"""Consume real supplier-invoice facts and reclassify, never duplicate a payable."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import NAMESPACE_URL,uuid5

from .binding import bound,require_bound
from .contracts import CostProof
from .domain import DomainError,consume_financial_proof,digest,distribute_minor,add_event
from .ledger_adapter import acquire_ledger_fence,entry_facts,leg,post_group,save_event,verify_event,counterparty_key,require_cutover
from .repository import COLLECTION,bounded
from .source_hooks import current_document


def invoice_facts(invoice):
    fields=('id','session_id','supplier_id','invoice_number','currency','status','total_halalas','subtotal_halalas',
        'piece_count','line_count','lines','ledger_txn_group_id','ledger_entry_ids','approved_by','approved_at',
        'financial_integrity_contract','financial_integrity_verified','financial_invoice_created','liability_created')
    facts={k:deepcopy(invoice.get(k)) for k in fields}
    if isinstance(facts.get('approved_at'),datetime):facts['approved_at']=facts['approved_at'].isoformat()
    return facts


async def verified_invoice(db,tenant,invoice_id):
    from supplier_invoice_integrity import INVOICES,verify_persisted_supplier_invoice
    source=await db[INVOICES].find_one({'user_id':tenant,'id':invoice_id},{'_id':0})
    if not source:raise DomainError('supplier_invoice_not_found',404)
    invoice=await verify_persisted_supplier_invoice(db,user_id=tenant,invoice_id=invoice_id,
        session_id=source.get('session_id'),supplier_id=source.get('supplier_id'))
    approved=invoice['approved_at']
    if approved.tzinfo is None:approved=approved.replace(tzinfo=timezone.utc)
    await require_cutover(db,tenant,approved.isoformat())
    return invoice


def service_target(doc,line,resource_id,label,unit):
    target='component:'+digest([line['line_key'],resource_id])[:40]
    if not any(r['target_key']==target for r in doc.get('cost_targets',[])):
        doc.setdefault('cost_targets',[]).append({'key':'service:'+target,'kind':'service','target_key':target,
            'line_key':line['line_key'],'resource_id':resource_id,'label':label,
            'amount_minor':0,'amount_sar_minor':0,'source':'verified_supplier_invoice','required_unit_indices':[]})
    row=next(r for r in doc['cost_targets'] if r['target_key']==target)
    row['required_unit_indices']=sorted(set(row.get('required_unit_indices',[]))|{unit})
    return target


async def recognize_supplier_invoice_for_order(db,doc,actor_id,invoice_id):
    binding=require_bound(db,write=True,finance=True)
    if binding.session is None:raise DomainError('real_mongo_transaction_required')
    invoice=await verified_invoice(db,doc['tenant_id'],invoice_id)
    from preparation_piece_operations import PIECES
    wanted=[identity for row in invoice['lines'] for identity in row['piece_ids']]
    pieces=await db[PIECES].find({'user_id':doc['tenant_id'],'piece_id':{'$in':wanted}}, {'_id':0}).to_list(len(wanted)+1)
    if len(pieces)!=len(wanted):raise DomainError('supplier_invoice_piece_source_missing')
    by_id={row['piece_id']:row for row in pieces}
    by_item={row['order_item_id']:row for row in doc['items']}
    plans=[]
    for row in invoice['lines']:
        ids=row['piece_ids']
        components=[]
        if row.get('product_charge_eligible') is not False:
            components.append(('product',None,None,row['product_total_halalas']))
        elif row['product_total_halalas']:
            raise DomainError('ineligible_product_was_charged')
        components.extend(('service',str(c['service_id']),c.get('service_name') or str(c['service_id']),c['total_halalas']) for c in row.get('services',[]))
        for kind,resource,label,total in components:
            shares=distribute_minor(total,[1]*len(ids))
            groups={}
            for index,identity in enumerate(ids):
                piece=by_id[identity]
                if piece.get('order_number')!=doc['order_number']:continue
                item=by_item.get(piece.get('order_item_id'))
                if not item or str(piece.get('product_id') or '')!=item['product']['product_id'] or str(row.get('product_id') or '')!=item['product']['product_id']:
                    raise DomainError('supplier_invoice_piece_identity_mismatch')
                unit=piece.get('unit_index')
                if type(unit) is not int or not 1<=unit<=item['quantity']:
                    raise DomainError('supplier_invoice_unit_identity_invalid')
                target=item['line_key'] if kind=='product' else service_target(doc,item,resource,label,unit)
                group=groups.setdefault((item['line_key'],target),{'item':item,'target':target,'units':[],'amount':0,'pieces':[]})
                group['units'].append(unit);group['pieces'].append(identity);group['amount']+=shares[index]
            for group in groups.values():
                movement=str(uuid5(NAMESPACE_URL, f"special-supplier:{doc['tenant_id']}:{invoice_id}:{doc['order_id']}:{kind}:{group['target']}"))
                if any(u['key']=='mz2:'+movement for u in doc['financial_uses']):
                    await verify_event(db,doc['tenant_id'],doc['order_id'],movement)
                    continue
                proof=CostProof(tenant_id=doc['tenant_id'],order_id=doc['order_id'],movement_id=movement,
                    kind=kind,target_key=group['target'],unit_indices=tuple(sorted(group['units'])),
                    cost_sar_minor=group['amount'],counterparty_id=invoice['supplier_id'],
                    origin='supplier_receipt' if kind=='product' else 'service_receipt',
                    expense_bucket=doc['policy']['expense_bucket'],evidence_id='supplier-invoice:'+invoice_id)
                consume_financial_proof(doc,proof)
                plans.append((proof,group))
    if not plans:
        if any(c['evidence_id']=='supplier-invoice:'+invoice_id for c in doc['costs']):return 0
        raise DomainError('supplier_invoice_does_not_contain_order')
    cost=sum(p.cost_sar_minor for p,_ in plans)
    event='supplier-reclass:'+str(uuid5(NAMESPACE_URL,f'{doc["tenant_id"]}:{doc["order_id"]}:{invoice_id}'))
    # Original invoice already debited expense/inventory and credited supplier.
    # Reclassification moves only its local-order expense slice. The supplier's
    # balance is untouched and the source verifier still finds exactly two legs.
    entries=[] if not cost else [leg('expense',counterparty_key(doc['policy']['expense_bucket']),'debit',cost),
                                leg('expense','inventory','credit',cost)]
    facts=await post_group(db,doc['tenant_id'],actor_id,order_id=doc['order_id'],event_id=event,entries=entries,
        metadata={'recognition_kind':'supplier_expense_reclassification','original_supplier_invoice_id':invoice_id,
            'purpose':doc['purpose'],'expense_bucket':doc['policy']['expense_bucket'],'new_supplier_payable':False})
    source_entries=await db.general_ledger.find({'user_id':doc['tenant_id'],'id':{'$in':invoice['ledger_entry_ids']}},{'_id':0}).to_list(3)
    for proof,group in plans:
        await save_event(db,doc,proof.movement_id,operation='supplier_invoice',entries=[*facts,*[entry_facts(r) for r in source_entries]],proof=proof,
            extra={'verified_zero_cost':proof.cost_sar_minor==0,'source_invoice':invoice_facts(invoice),
                'piece_ids':group['pieces'],'new_supplier_payable':False,'reclassified_total_sar_minor':cost})
    doc.pop('costs_finalized',None)
    return len(plans)


async def after_supplier_invoice_closed(db,*,tenant_id,actor_id,invoice_id,mongo_session):
    """Called INSIDE the existing supplier close transaction after its verifier."""
    binding=bound(db)
    if binding is None or not binding.enablement.reads:return
    from supplier_invoice_integrity import INVOICES
    from preparation_piece_operations import PIECES
    invoice=await db[INVOICES].find_one({'user_id':tenant_id,'id':invoice_id},{'_id':0},session=mongo_session)
    ids=[p for row in (invoice or {}).get('lines',[]) for p in row.get('piece_ids',[])]
    pieces=await db[PIECES].find({'user_id':tenant_id,'piece_id':{'$in':ids}},
        {'_id':0,'order_number':1},session=mongo_session).to_list(len(ids)+1)
    from .canonical_adapter import is_local_order_number
    numbers=sorted({r['order_number'] for r in pieces if is_local_order_number(r.get('order_number'))})
    if not numbers:return
    scoped=binding.in_session(mongo_session)
    await require_cutover(scoped,tenant_id)
    await acquire_ledger_fence(scoped,tenant_id)
    for number in numbers:
        doc,_=await current_document(scoped,tenant_id,number,write=True,lock=True)
        revision=doc['revision']
        count=await recognize_supplier_invoice_for_order(scoped,doc,actor_id,invoice_id)
        if count:
            doc['revision']+=1;add_event(doc,'special_order.supplier_cost_recognized',actor_id);bounded(doc)
            result=await scoped[COLLECTION].replace_one({'tenant_id':tenant_id,'order_id':doc['order_id'],'revision':revision},doc)
            if result.matched_count!=1:raise DomainError('revision_conflict')
