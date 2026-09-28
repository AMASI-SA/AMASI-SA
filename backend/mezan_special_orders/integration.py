"""Concrete local source composition, with atomic creation and shared review.

No route or database is activated by importing this module. The existing workflow
owns progression; accounting commands are separately delegated to MZ2.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import NAMESPACE_URL, uuid5

from .bank_adapter import verify_fx
from .binding import require_bound, transaction
from .catalog_adapter import ExistingCatalogAdapter
from .contracts import Evidence, FxSnapshot
from .domain import DomainError, digest, cost_report
from .evidence import EvidenceStore
from .finance_service import verify_document_finance
from .ledger_adapter import payment_proof, cost_proof
from .repository import COLLECTION, MongoStore
from .service import Gates, SpecialOrderService, require, public_view, validate_key
from .source_hooks import WORKFLOWS,current_document


class ExistingPorts:
    def __init__(self, db):
        self.db=db
        self.catalog=ExistingCatalogAdapter(db)
        self.evidence_store=EvidenceStore(db)

    async def original(self, tenant_id, number):
        original=await self.catalog.original(tenant_id,number)
        if original.source=='mezan':
            doc,_=await current_document(self.db,tenant_id,number)
            label=(doc.get('delivery') or {}).get('label') or {}
            original=original.model_copy(update={'label_object_id':label.get('object_id')})
        return original

    async def product(self, tenant_id, product_id, variant_id):
        return await self.catalog.product(tenant_id,product_id,variant_id)

    async def evidence(self, tenant_id, evidence, **kwargs):
        return await self.evidence_store.verify(tenant_id,evidence,**kwargs)

    async def fx(self, tenant_id, snapshot):
        return await verify_fx(self.db,tenant_id,FxSnapshot.model_validate(snapshot))

    async def payment(self,tenant_id,order_id,movement_id):
        return await payment_proof(self.db,tenant_id,order_id,movement_id)

    async def cost(self,tenant_id,order_id,movement_id):
        return await cost_proof(self.db,tenant_id,order_id,movement_id)

    async def workflow(self,*args):
        # No second state machine or user-submitted observation is installed.
        raise DomainError('workflow_is_read_from_existing_owner',405)

    async def publish(self,tenant_id,event_id,event,order):
        # Creation and review membership commit together. Outbox consumption is
        # an invalidation notification, never an older workflow snapshot write.
        key=str(uuid5(NAMESPACE_URL,f'special-notification:{tenant_id}:{event_id}'))
        await self.db['mezan_special_order_notifications_v1'].update_one({'_id':key},
            {'$setOnInsert':{'tenant_id':tenant_id,'event_id':event_id,'order_id':order['order_id'],
                'source_revision':order['source_revision'],'revision':event['revision'],
                'topic':event['topic'],'published_at':datetime.now(timezone.utc).isoformat()}},upsert=True)


def integrated_view(document, actor):
    view=public_view(document,actor)
    view['currency']=document['fx']['currency']
    view['reason']=document['reason']
    if 'special_orders.finance' in actor.permissions:
        report=view['cost_report']
        finalized=document.get('costs_finalized')
        final=bool(finalized and finalized.get('cost_digest')==digest(document['costs']) and report['costs_complete'])
        report['costs_finalized']=final
        report['provisional']=not final
        if not final:
            report['final_net_store_burden_minor']=None
        view['accounting_agreement']=deepcopy(document.get('financial_agreement'))
        view['credit_notes']=deepcopy(document.get('financial_credit_notes',[]))
        view['costs_finalized']=deepcopy(finalized)
    return view


class IntegratedOrders:
    def __init__(self,db):
        self.db=db

    def core(self,db=None):
        db=self.db if db is None else db
        enabled=require_bound(db).enablement
        return SpecialOrderService(MongoStore(db),ExistingPorts(db),Gates(enabled.creation,enabled.commands,enabled.commands))

    async def ensure_indexes(self):
        await MongoStore(self.db).ensure_indexes()
        await EvidenceStore(self.db).ensure_indexes()
        await self.db[WORKFLOWS].create_index([('user_id',1),('order_number',1)],unique=True)

    async def _document(self,actor,order_id,db=None):
        db=self.db if db is None else db
        row=await db[COLLECTION].find_one({'tenant_id':actor.tenant_id,'order_id':order_id},{'_id':0,'order_number':1})
        if not row:
            raise DomainError('order_not_found',404)
        return (await current_document(db,actor.tenant_id,row['order_number']))[0]

    async def get(self,actor,order_id):
        require(actor,'special_orders.read')
        doc=await self._document(actor,order_id)
        await verify_document_finance(self.db,doc)
        return integrated_view(doc,actor)

    async def list(self,actor,limit=30,before=None):
        require(actor,'special_orders.read')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DomainError('invalid_page_limit',422)
        documents=await MongoStore(self.db).list(actor.tenant_id,limit+1,before)
        page=documents[:limit]
        # One shared-workflow batch read, not stale local aggregate observations.
        from .canonical_adapter import LocalOrderReader
        current=await LocalOrderReader(self.db).get_many(user_id=actor.tenant_id,order_numbers=[d['order_number'] for d in page])
        by_number={d['order_number']:d for d in current}
        reader=actor.model_copy(update={'permissions':actor.permissions-{'special_orders.finance'}})
        items=[]
        for document in page:
            effective=by_number.get(document['order_number'])
            if effective is None:raise DomainError('local_order_discovery_inconsistent')
            document['stage']=effective['stage']
            items.append(integrated_view(document,reader))
        return {'items':items,'next':(page[-1]['created_at'],page[-1]['order_id']) if len(documents)>limit else None}

    async def create(self,actor,request,key):
        require(actor,'special_orders.create'); validate_key(key)
        binding=require_bound(self.db)
        await self.ensure_indexes()
        identity=str(uuid5(NAMESPACE_URL,json.dumps(['mezan-special-v1',actor.tenant_id,key])))
        fingerprint=digest(request.model_dump(mode='json'))
        async def apply(db):
            out=await self.core(db).create(actor,request,key)
            doc=await db[COLLECTION].find_one({'tenant_id':actor.tenant_id,'order_id':out['order_id']},{'_id':0})
            await EvidenceStore(db).verify_delivery(doc, planned=True)
            existing=await db[WORKFLOWS].find_one({'user_id':actor.tenant_id,'order_number':doc['order_number']},{'_id':0})
            if existing:
                if existing.get('order_id')!=doc['order_id'] or existing.get('source_provider')!='mezan':
                    raise DomainError('workflow_identity_conflict')
            else:
                await db[WORKFLOWS].insert_one({'user_id':actor.tenant_id,'order_id':doc['order_id'],
                    'order_number':doc['order_number'],'stage':'pending_review','revision':0,
                    'source_provider':'mezan','special_source_digest':doc['snapshot_digest'],
                    'special_source_revision':doc['source_revision'],'order_purpose':doc['purpose'],
                    'items':[],'operational_items':[],'created_at':doc['created_at'],
                    'created_by':actor.actor_id,'updated_at':doc['created_at']})
            return integrated_view(doc,actor)
        old=await self.db[COLLECTION].find_one({'tenant_id':actor.tenant_id,'order_id':identity},{'_id':0})
        if old:
            if old['create_fingerprint']!=fingerprint:
                raise DomainError('idempotency_payload_conflict')
            current=await self._document(actor,identity)
            await verify_document_finance(self.db,current)
            return integrated_view(current,actor)
        if not binding.enablement.creation:
            raise DomainError('creation_disabled',403)
        from pymongo.errors import DuplicateKeyError
        try:
            return await transaction(self.db,apply)
        except DuplicateKeyError:
            old=await self.db[COLLECTION].find_one({'tenant_id':actor.tenant_id,'order_id':identity},{'_id':0})
            if old and old['create_fingerprint']==fingerprint:
                return integrated_view(await self._document(actor,identity),actor)
            raise DomainError('creation_identity_or_label_conflict') from None

    async def command(self,actor,order_id,expected_revision,key,operation,payload):
        if operation not in {'amend_options','attach_receipt','reject_receipt'}:
            raise DomainError('use_existing_workflow_or_financial_command',405)
        # Receipt attachment is permitted separately from posting/reconciliation.
        command_actor=actor
        if operation=='attach_receipt' and 'special_orders.receipt_upload' in actor.permissions:
            command_actor=actor.model_copy(update={'permissions':actor.permissions|{'special_orders.finance'}})
        async def apply(db):
            doc=await self._document(actor,order_id,db)
            if doc['stage'] in {'cancelled','refunded'}:
                raise DomainError('order_closed')
            if operation=='amend_options' and (doc['stage']!='pending_review' or doc['source_frozen']):
                raise DomainError('options_frozen_for_fulfillment')
            await verify_document_finance(db,doc)
            before_source=doc['source_revision']
            if operation=='amend_options':
                await current_document(db,actor.tenant_id,doc['order_number'],write=True,lock=True)
            await self.core(db).command(command_actor,order_id,expected_revision,key,operation,payload)
            new=await db[COLLECTION].find_one({'tenant_id':actor.tenant_id,'order_id':order_id},{'_id':0})
            if new['source_revision']!=before_source:
                result=await db[WORKFLOWS].update_one({'user_id':actor.tenant_id,'order_number':new['order_number'],
                    'stage':'pending_review','special_source_revision':before_source},
                    {'$set':{'special_source_revision':new['source_revision'],'special_source_digest':new['snapshot_digest']},'$inc':{'revision':1}})
                if result.matched_count!=1:
                    raise DomainError('workflow_changed_during_option_edit')
            current,_=await current_document(db,actor.tenant_id,new['order_number'])
            return integrated_view(current,actor)
        return await transaction(self.db,apply)
