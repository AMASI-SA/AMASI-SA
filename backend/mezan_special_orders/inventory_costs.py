"""Expense the verified MZ2 carrying cost of actual reserved inventory.

A valuation allocates an existing inventory-asset debit; it posts no purchase or
payable. Physical consumption and expense recognition share the existing owner's
transaction. Missing carrying-cost evidence fails closed, never falls back to a
current catalogue price or treats unknown inventory as free.
"""
from __future__ import annotations

from datetime import datetime,timezone
from typing import Annotated
from uuid import NAMESPACE_URL,uuid5
from pydantic import Field,StrictInt

from .binding import require_bound,transaction
from .contracts import Contract,Key,Evidence,Minor,CostProof
from .domain import DomainError,consume_financial_proof,digest,add_event
from .evidence import EvidenceStore
from .ledger_adapter import (OPERATION_ID,acquire_ledger_fence,entry_facts,ensure_indexes,leg,post_group,
    save_event,verify_event,require_cutover,minor,counterparty_key)
from .repository import COLLECTION,bounded
from .source_hooks import current_document
from .service import require

VALUATIONS='mezan_special_inventory_valuations_v1'


class InventoryValuation(Contract):
    receipt_id:Key
    inventory_entry_id:Key
    quantity:Annotated[StrictInt,Field(gt=0,le=100000)]
    cost_sar_minor:Minor
    evidence:Evidence
    reason:Annotated[str,Field(min_length=5,max_length=1000)]


def whole(value):
    from decimal import Decimal
    try:
        parsed=Decimal(str(value))
        if isinstance(value,bool) or not parsed.is_finite() or parsed!=parsed.to_integral_value() or parsed<=0:raise ValueError()
        return int(parsed)
    except (ValueError,ArithmeticError):raise DomainError('whole_inventory_units_required') from None


def cost_after(quantity,amount,total):
    from decimal import Decimal,ROUND_HALF_UP
    return int((Decimal(quantity)*amount/total).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


async def approve_valuation(db,actor,request:InventoryValuation):
    require(actor,'special_orders.finance');require_bound(db,write=True,finance=True)
    if request.evidence.kind!='cost_document':raise DomainError('cost_document_required',422)
    await ensure_indexes(db)
    await db[VALUATIONS].create_index([('tenant_id',1),('receipt_id',1)],unique=True)
    async def apply(scoped):
        await require_cutover(scoped,actor.tenant_id);await acquire_ledger_fence(scoped,actor.tenant_id)
        await EvidenceStore(scoped).verify(actor.tenant_id,request.evidence)
        fingerprint=digest(request.model_dump(mode='json'))
        existing=await scoped[VALUATIONS].find_one({'tenant_id':actor.tenant_id,'receipt_id':request.receipt_id},{'_id':0})
        if existing:
            if existing['fingerprint']!=fingerprint:raise DomainError('inventory_valuation_immutable')
            return {k:v for k,v in existing.items() if k not in {'source_entries'}}
        from product_inventory_receipt_routes import INVENTORY_RECEIPTS
        from warehouse_location_routes import LOCATIONS
        receipt=await scoped[INVENTORY_RECEIPTS].find_one({'user_id':actor.tenant_id,'id':request.receipt_id,'status':'posted'},{'_id':0})
        if not receipt:raise DomainError('posted_inventory_receipt_required')
        locations=await scoped[LOCATIONS].find({'user_id':actor.tenant_id,'occupancy.items.receipt_id':request.receipt_id},
            {'_id':0,'occupancy.items':1}).to_list(1001)
        if len(locations)>1000:raise DomainError('inventory_valuation_scope_too_large')
        on_hand=sum(whole(item['quantity']) for location in locations for item in location.get('occupancy',{}).get('items',[])
            if item.get('receipt_id')==request.receipt_id and float(item.get('quantity') or 0)>0)
        if on_hand!=request.quantity:raise DomainError('valuation_quantity_must_match_current_physical_stock')
        entry=await scoped.general_ledger.find_one({'user_id':actor.tenant_id,'id':request.inventory_entry_id,
            'entity_type':'inventory','side':'debit','currency':'SAR','status':'posted','metadata.operation_id':OPERATION_ID},{'_id':0})
        if not entry:raise DomainError('posted_mz2_inventory_asset_required')
        rows=await scoped.general_ledger.find({'user_id':actor.tenant_id,'txn_group_id':entry['txn_group_id']},{'_id':0}).to_list(129)
        if not 2<=len(rows)<=128 or any(r.get('status')!='posted' or r.get('currency')!='SAR' for r in rows):
            raise DomainError('inventory_source_group_invalid')
        if sum(minor(r['amount'])*(1 if r['side']=='debit' else -1) for r in rows)!=0:
            raise DomainError('inventory_source_group_unbalanced')
        allocations=await scoped[VALUATIONS].find({'tenant_id':actor.tenant_id,'inventory_entry_id':entry['id']},{'_id':0,'cost_sar_minor':1}).to_list(10001)
        if len(allocations)>10000 or sum(r['cost_sar_minor'] for r in allocations)+request.cost_sar_minor>minor(entry['amount']):
            raise DomainError('inventory_asset_cost_already_allocated')
        row={**request.model_dump(mode='json'),'tenant_id':actor.tenant_id,'approved_by':actor.actor_id,
            'approved_at':datetime.now(timezone.utc).isoformat(),'fingerprint':fingerprint,'issued_quantity':0,
            'source_entries':[entry_facts(r) for r in rows],
            'inventory_account':{'entity_type':entry['entity_type'],'entity_id':entry['entity_id'],'sub_account':entry.get('sub_account')},
            'receipt_product_id':str(receipt.get('salla_product_id') or receipt.get('product_id') or ''),
            'receipt_variant_id':str(receipt.get('salla_variant_id') or '')}
        await scoped[VALUATIONS].insert_one(row)
        return {k:v for k,v in row.items() if k not in {'_id','source_entries'}}
    return await transaction(db,apply)


async def verify_valuation(db,tenant,receipt_id):
    valuation=await db[VALUATIONS].find_one({'tenant_id':tenant,'receipt_id':receipt_id},{'_id':0})
    if not valuation:raise DomainError('approved_inventory_carrying_cost_required')
    request_fields={k:valuation[k] for k in InventoryValuation.model_fields}
    if digest(InventoryValuation.model_validate(request_fields).model_dump(mode='json'))!=valuation.get('fingerprint'):
        raise DomainError('inventory_valuation_integrity_failed')
    if type(valuation.get('issued_quantity')) is not int or not 0<=valuation['issued_quantity']<=valuation['quantity']:
        raise DomainError('inventory_valuation_issued_quantity_invalid')
    expected=valuation['source_entries']
    rows=await db.general_ledger.find({'user_id':tenant,'id':{'$in':[r['id'] for r in expected]}},{'_id':0}).to_list(len(expected)+1)
    by_id={r['id']:entry_facts(r) for r in rows if r.get('status')=='posted'}
    if len(rows)!=len(expected) or any(by_id.get(r['id'])!=r for r in expected):
        raise DomainError('inventory_valuation_source_reconciliation_required')
    funding=by_id.get(valuation['inventory_entry_id'])
    account={k:funding.get(k) for k in ('entity_type','entity_id','sub_account')} if funding else None
    if not funding or funding['side']!='debit' or account!=valuation['inventory_account']:
        raise DomainError('inventory_valuation_account_integrity_failed')
    await EvidenceStore(db).verify(tenant,Evidence.model_validate(valuation['evidence']))
    return valuation


async def before_inventory_consumed(db,*,tenant_id,actor_id,reservations,batch_id):
    from .canonical_adapter import is_local_order_number
    local=[r for r in reservations if is_local_order_number(r.get('order_number'))]
    if not local:return
    binding=require_bound(db,write=True,finance=True)
    if binding.session is None:raise DomainError('real_mongo_transaction_required')
    await require_cutover(db,tenant_id);await acquire_ledger_fence(db,tenant_id)
    for reservation in local:
        doc,_=await current_document(db,tenant_id,reservation['order_number'],write=True,lock=True)
        item=next((i for i in doc['items'] if i['order_item_id']==reservation['line_key']),None)
        if not item or not doc['source_frozen']:raise DomainError('inventory_source_item_not_frozen')
        allocations=reservation.get('allocations',[])
        if sum(whole(a['quantity']) for a in allocations)!=item['quantity']:
            raise DomainError('inventory_reservation_quantity_mismatch')
        total=0;credits={};source_entries={};receipts=[]
        for allocation in allocations:
            rid=allocation.get('receipt_id')
            if not rid:raise DomainError('inventory_receipt_identity_required')
            quantity=whole(allocation['quantity']);valuation=await verify_valuation(db,tenant_id,rid)
            if valuation['receipt_product_id']!=item['product']['product_id'] or valuation['receipt_variant_id']!=str(item['product'].get('variant_id') or ''):
                raise DomainError('inventory_valuation_product_mismatch')
            used=valuation['issued_quantity'];all_units=valuation['quantity']
            if used+quantity>all_units:raise DomainError('inventory_valuation_quantity_exceeded')
            cost=cost_after(used+quantity,valuation['cost_sar_minor'],all_units)-cost_after(used,valuation['cost_sar_minor'],all_units)
            total+=cost;account=valuation['inventory_account'];key=(account['entity_type'],account['entity_id'],account['sub_account'])
            credits[key]=credits.get(key,0)+cost
            source_entries.update({r['id']:r for r in valuation['source_entries']});receipts.append({'receipt_id':rid,'quantity':quantity,'cost_sar_minor':cost,'valuation_fingerprint':valuation['fingerprint']})
            result=await db[VALUATIONS].update_one({'tenant_id':tenant_id,'receipt_id':rid,'issued_quantity':used},{'$inc':{'issued_quantity':quantity}})
            if result.matched_count!=1:raise DomainError('inventory_cost_consumption_conflict')
        event=str(uuid5(NAMESPACE_URL,f'special-inventory:{tenant_id}:{reservation["id"]}'))
        proof=CostProof(tenant_id=tenant_id,order_id=doc['order_id'],movement_id=event,kind='product',target_key=item['line_key'],
            unit_indices=tuple(range(1,item['quantity']+1)),cost_sar_minor=total,counterparty_id='inventory',origin='inventory_issue',
            expense_bucket=doc['policy']['expense_bucket'],evidence_id='inventory-reservation:'+reservation['id'])
        consume_financial_proof(doc,proof)
        entries=([leg('expense',counterparty_key(proof.expense_bucket),'debit',total)] if total else [])
        entries.extend(leg(t,i,'credit',amount,sub) for (t,i,sub),amount in credits.items() if amount)
        facts=await post_group(db,tenant_id,actor_id,order_id=doc['order_id'],event_id=event,entries=entries,
            metadata={'recognition_kind':'inventory_issue','reservation_id':reservation['id'],'batch_id':batch_id,
                'purpose':doc['purpose'],'expense_bucket':proof.expense_bucket,'new_supplier_payable':False})
        await save_event(db,doc,event,operation='inventory_issue',entries=[*facts,*source_entries.values()],proof=proof,
            extra={'verified_zero_cost':total==0,'inventory_receipts':receipts,'reservation_id':reservation['id'],'batch_id':batch_id})
        previous=doc['revision'];doc['revision']+=1;doc.pop('costs_finalized',None);add_event(doc,'special_order.inventory_cost_recognized',actor_id);bounded(doc)
        result=await db[COLLECTION].replace_one({'tenant_id':tenant_id,'order_id':doc['order_id'],'revision':previous},doc)
        if result.matched_count!=1:raise DomainError('revision_conflict')
