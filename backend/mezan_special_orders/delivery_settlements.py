"""Native driver settlements with explicit local-order custody allocations.

One bank movement and one existing-owner journal settle the driver's aggregate.
Local remittance proofs reference that same journal, never post it again. Ordinary
custody may be settled separately; a request cannot silently choose a local order
or allocate a payment twice. Amount/earning offset on this native API are SAR.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from uuid import NAMESPACE_URL, uuid5
from typing import Annotated

from pydantic import Field, StrictInt
from .access import fresh_principal
from .bank_adapter import bank_movement
from .binding import bound, require_bound, transaction
from .contracts import Contract, Key, LedgerProof
from .domain import DomainError, add_event, consume_financial_proof, digest
from .finance_service import persist, verify_document_finance
from .ledger_adapter import (acquire_ledger_fence, ensure_indexes, entry_facts, major, minor,
    post_group, require_cutover, save_event, verify_event)
from .repository import COLLECTION
from .source_hooks import api_error, current_document
from .service import validate_key
from .delivery_bridge import now_iso


class CustodyAllocation(Contract):
    order_id: Key
    parent_movement_id: Key
    amount_minor: Annotated[StrictInt, Field(gt=0,le=10**12)]


def settlement_facts(row):
    fields=('id','user_id','driver_id','settlement_type','amount','account_id','reference',
        'cod_settled_amount','delivery_fee_settled_amount','earning_offset','status',
        'ledger_txn_group_id','created_at','created_by','special_allocations','special_request_digest','evidence_id')
    return {k:deepcopy(row.get(k)) for k in fields}


async def verify_native_settlement(db, row):
    if row.get('special_integrity_digest')!=digest(settlement_facts(row)):
        raise DomainError('native_settlement_integrity_failed')
    facts=row.get('special_ledger_entries') or []
    entries=await db.general_ledger.find({'user_id':row['user_id'],'txn_group_id':row['ledger_txn_group_id']},{'_id':0}).to_list(len(facts)+1)
    if len(entries)!=len(facts) or not facts or any(e.get('status')!='posted' or e.get('currency')!='SAR' for e in entries):
        raise DomainError('native_settlement_ledger_reconciliation_required')
    actual={r['id']:entry_facts(r) for r in entries}
    if any(actual.get(r['id'])!=r for r in facts):
        raise DomainError('native_settlement_ledger_reconciliation_required')


async def local_driver_exists(db, tenant, driver_id):
    binding=bound(db)
    if binding is None or not binding.enablement.reads:
        return False
    return await db.general_ledger.find_one({'user_id':tenant,'entity_type':'store_driver',
        'entity_id':driver_id,'metadata.special_order_id':{'$type':'string'},'status':'posted'},
        {'_id':0,'id':1}) is not None


async def exact_driver_ledger(db, tenant, driver_id):
    rows=await db.general_ledger.find({'user_id':tenant,'entity_type':'store_driver','entity_id':driver_id,
        'status':'posted','sub_account':{'$in':['cod_receivable','delivery_fee_payable']}},{'_id':0}).to_list(10001)
    if len(rows)>10000:
        raise DomainError('driver_ledger_requires_paged_reconciliation')
    totals={'cod_receivable':0,'delivery_fee_payable':0}
    for row in rows:
        if row.get('currency')!='SAR' or (row.get('metadata') or {}).get('operation_id')!='MZ2-FIN-CUTOVER-001':
            raise DomainError('driver_ledger_cutover_reconciliation_required')
        if row['side'] not in {'debit','credit'}:
            raise DomainError('driver_ledger_reconciliation_required')
        totals[row['sub_account']]+=minor(row['amount'])*(1 if row['side']=='debit' else -1)
    if totals['cod_receivable']<0 or totals['delivery_fee_payable']>0:
        raise DomainError('driver_ledger_balance_requires_reconciliation')
    return totals['cod_receivable'],-totals['delivery_fee_payable']


async def local_custody(db, tenant, driver_id):
    rows=await db[COLLECTION].find({'tenant_id':tenant,'payments':{'$elemMatch':{
        'kind':'cod_collection','account_id':driver_id}}},{'_id':0,'order_number':1}).to_list(501)
    if len(rows)>500:
        raise DomainError('driver_custody_requires_paged_reconciliation')
    documents={};parents={};total=0
    for row in rows:
        doc,_=await current_document(db,tenant,row['order_number'],write=True,lock=True)
        await verify_document_finance(db,doc)
        documents[doc['order_id']]=doc
        for parent in doc['payments']:
            if parent['kind']!='cod_collection' or parent['account_id']!=driver_id:
                continue
            event=await verify_event(db,tenant,doc['order_id'],parent['movement_id'])
            if event['extra'].get('collector_kind')!='store_driver':
                continue
            used=sum(p['amount_minor'] for p in doc['payments'] if p.get('parent_movement_id')==parent['movement_id']
                and (p['kind']=='remittance' or p.get('refund_from')=='custody'))
            if used>parent['amount_minor']:
                raise DomainError('local_custody_reconciliation_required')
            def part(value, p=parent):
                return int((Decimal(p['amount_sar_minor'])*value/p['amount_minor']).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            remaining=parent['amount_sar_minor']-part(used)
            total+=remaining
            parents[(doc['order_id'],parent['movement_id'])]=(parent,used,part)
    return documents,parents,total


async def local_native_settlement(db, *, tenant, actor, driver, settlement_type, payload):
    has_local=await local_driver_exists(db,tenant,driver['id'])
    explicit=bool(payload.special_allocations or payload.movement or payload.idempotency_key or payload.evidence)
    if not has_local and not explicit:
        return None
    from store_delivery_settlement_routes import (SETTLEMENTS,_require_accountant,
        ensure_store_delivery_settlement_indexes,_totals)
    from store_delivery_accounting import settlement_journal_entries
    from .evidence import EvidenceStore
    try:
        require_bound(db,write=True,finance=True,tenant_id=tenant)
        validate_key(payload.idempotency_key)
        amount,offset=minor(payload.amount),minor(payload.earning_offset)
        if settlement_type!='net_settlement' and offset:
            raise DomainError('earning_offset_requires_explicit_net_settlement',422)
        if settlement_type=='net_settlement' and offset<=0 or settlement_type!='net_settlement' and amount<=0:
            raise DomainError('settlement_amount_required',422)
        movement=payload.movement
        if amount and (not movement or movement.cash_fx.currency!='SAR'
                or movement.bank_account_id!=payload.account_id):
            raise DomainError('verified_sar_bank_movement_required',422)
        if not amount and movement:
            raise DomainError('zero_cash_settlement_cannot_post_bank',422)
        evidence=movement.evidence if movement else payload.evidence
        if evidence is None:
            raise DomainError('settlement_evidence_required',422)
        if payload.reference and movement and payload.reference.strip()!=movement.bank_reference.strip():
            raise DomainError('settlement_bank_reference_mismatch',422)
        keys=[(a.order_id,a.parent_movement_id) for a in payload.special_allocations]
        if len(keys)!=len(set(keys)):
            raise DomainError('duplicate_custody_allocation',422)
        sid=str(uuid5(NAMESPACE_URL,f'native-special-settlement:{tenant}:{driver["id"]}:{payload.idempotency_key}'))
        fingerprint=digest([settlement_type,payload.model_dump(mode='json')])
        await ensure_indexes(db)
        await ensure_store_delivery_settlement_indexes(db)
        async def apply(scoped):
            principal,merchant=await fresh_principal(scoped,actor)
            if merchant!=tenant:
                raise DomainError('merchant_scope_mismatch',403)
            _require_accountant(principal)
            await scoped.users.update_one({'id':principal['id']},{'$inc':{'special_financial_fence':1}})
            live_driver=await scoped.store_drivers.find_one({'user_id':tenant,'id':driver['id']},{'_id':0})
            if not live_driver:
                raise DomainError('store_driver_not_found',404)
            # An inactive driver may still remit old custody or receive earned pay.
            await scoped.store_drivers.update_one({'user_id':tenant,'id':driver['id']},{'$inc':{'special_financial_fence':1}})
            previous=await scoped[SETTLEMENTS].find_one({'user_id':tenant,'id':sid},{'_id':0})
            if previous:
                if previous.get('special_request_digest')!=fingerprint:
                    raise DomainError('idempotency_payload_conflict')
                await verify_native_settlement(scoped,previous)
                return {'settlement':{k:v for k,v in previous.items() if k not in {'user_id','special_ledger_entries'}},
                    'summary':await _totals(scoped,tenant,driver['id'])}
            await require_cutover(scoped,tenant,movement.occurred_at.isoformat() if movement else None)
            await acquire_ledger_fence(scoped,tenant)
            await EvidenceStore(scoped).verify(tenant,evidence)
            cod_due,fee_due=await exact_driver_ledger(scoped,tenant,driver['id'])
            cod=amount+offset if settlement_type=='net_settlement' else amount if settlement_type=='cod_remittance' else 0
            fee=offset if settlement_type=='net_settlement' else amount if settlement_type=='earning_payment' else 0
            if cod>cod_due or fee>fee_due:
                raise DomainError('store_delivery_settlement_exceeds_ledger_balance')
            docs,parents,local_due=await local_custody(scoped,tenant,driver['id'])
            allocation_rows=[];allocated_sar=0
            for item in payload.special_allocations:
                state=parents.get((item.order_id,item.parent_movement_id))
                if not state:
                    raise DomainError('assigned_driver_custody_parent_required')
                parent,used,part=state
                if used+item.amount_minor>parent['amount_minor']:
                    raise DomainError('amount_exceeds_custody')
                carry=part(used+item.amount_minor)-part(used)
                if carry<=0:
                    raise DomainError('custody_allocation_rounds_to_zero')
                allocation_rows.append({**item.model_dump(mode='json'),'carrying_sar_minor':carry})
                allocated_sar+=carry
            if local_due>cod_due or allocated_sar>cod or cod-allocated_sar>cod_due-local_due:
                raise DomainError('explicit_special_order_custody_allocation_required')
            native,_,_=settlement_journal_entries(driver_id=driver['id'],account_id=payload.account_id or '',
                settlement_type=settlement_type,bank_amount=major(amount),earning_offset=major(offset))
            entries=[{k:r.get(k) for k in ('entity_type','entity_id','sub_account','side')}|
                     {'amount_minor':minor(r['amount'])} for r in native]
            metadata={'source':'store_delivery_settlements','special_order_id':None,
                'native_settlement_id':sid,'driver_id':driver['id'],'settlement_type':settlement_type,
                'cod_settled_sar_minor':cod,'delivery_fee_settled_sar_minor':fee,'special_allocations':allocation_rows}
            facts=None
            if movement:
                # Bank adapter is keyed by the financial settlement identity; no
                # order, workflow or Salla record is fabricated for this transfer.
                subject={'tenant_id':tenant,'order_id':sid,'order_number':'DRIVER-SETTLEMENT-'+sid,
                         'fx':movement.cash_fx.model_dump(mode='json')}
                bank_meta,facts=await bank_movement(scoped,subject,movement,amount_minor=amount,
                    direction='out' if settlement_type=='earning_payment' else 'in',event_id=sid,expected_entries=entries)
                metadata.update(bank_meta)
            if facts is None:
                facts=await post_group(scoped,tenant,principal['id'],order_id=sid,event_id=sid,
                    entries=entries,metadata=metadata)
            now=now_iso()
            row={'id':sid,'user_id':tenant,'driver_id':driver['id'],'driver_name_snapshot':live_driver.get('name'),
                'settlement_type':settlement_type,'amount':major(amount),'earning_offset':major(offset),
                'account_id':payload.account_id or '', 'reference':movement.bank_reference if movement else payload.reference,
                'note':payload.note,'cod_settled_amount':major(cod),'delivery_fee_settled_amount':major(fee),
                'status':'posted','accounting_status':'posted','accounting_operation_id':'MZ2-FIN-CUTOVER-001',
                'ledger_txn_group_id':facts[0]['txn_group_id'],'created_at':now,'created_by':principal['id'],
                'special_request_digest':fingerprint,'special_allocations':allocation_rows,'special_ledger_entries':facts,
                'evidence_id':evidence.object_id}
            row['special_integrity_digest']=digest(settlement_facts(row))
            await scoped[SETTLEMENTS].insert_one(deepcopy(row))
            changed=set()
            for index,allocation in enumerate(allocation_rows):
                doc=docs[allocation['order_id']]
                proof=LedgerProof(tenant_id=tenant,order_id=doc['order_id'],movement_id=f'{sid}:allocation:{index}',
                    kind='remittance',currency=doc['fx']['currency'],amount_minor=allocation['amount_minor'],
                    amount_sar_minor=allocation['carrying_sar_minor'],account_id=payload.account_id or 'driver-netting:'+driver['id'],
                    evidence_id=evidence.object_id,parent_movement_id=allocation['parent_movement_id'])
                consume_financial_proof(doc,proof)
                await save_event(scoped,doc,proof.movement_id,operation='native_driver_settlement',entries=facts,proof=proof,
                    extra={'carrying_sar_minor':allocation['carrying_sar_minor'],'native_settlement':settlement_facts(row)})
                changed.add(doc['order_id'])
            for identity in sorted(changed):
                doc=docs[identity];revision=doc['revision'];doc['revision']+=1
                add_event(doc,'special_order.native_driver_settlement',principal['id'])
                await persist(scoped,doc,revision)
            await verify_native_settlement(scoped,row)
            return {'settlement':{k:v for k,v in row.items() if k not in {'user_id','special_ledger_entries'}},
                    'summary':await _totals(scoped,tenant,driver['id'])}
        return await transaction(db,apply,tenant_id=tenant,scopes=frozenset({"workflow", "financial", "evidence"}))
    except DomainError as exc:
        raise api_error(exc) from None
