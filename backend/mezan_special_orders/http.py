"""Authenticated opt-in API composed from concrete existing source/ledger owners.

The legacy generic router is not used here. Every principal is re-read, private
files are streamed only after authorization, and mutations retain real Mongo
transactions. This factory does not enable any flag or mount itself.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import Field, StrictInt, ValidationError

from .access import GRANTS, STAFF_PERMISSIONS, current_actor, label_actor, fresh_principal
from .binding import bound, require_bound, transaction
from .catalog_adapter import ExistingCatalogAdapter
from .contracts import Contract, CreateOrder, Currency, Evidence, FxSnapshot, Key, Minor, Recipient
from .domain import DomainError, digest
from .evidence import EvidenceStore, MAX_BYTES
from .finance_contracts import AccountingPolicy, FinancialCommand
from .finance_service import FinancialService, resolve_party
from .integration import IntegratedOrders, ExistingPorts
from .inventory_costs import InventoryValuation, approve_valuation
from .ledger_adapter import POLICIES, FX_SNAPSHOTS, ensure_indexes
from .repository import COLLECTION
from .service import require
from .source_hooks import api_error, current_document


class SourceCommand(Contract):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    operation: Literal['amend_options','attach_receipt','reject_receipt']
    payload: dict


class LabelAttestation(Contract):
    evidence: Evidence
    carrier_key: Key
    tracking_number: Annotated[str, Field(min_length=1,max_length=128)]
    recipient: Recipient
    currency: Currency
    cod_minor: Minor
    reason: Annotated[str,Field(min_length=5,max_length=1000)]


class FxApproval(Contract):
    snapshot: FxSnapshot
    evidence: Evidence
    reason: Annotated[str,Field(min_length=5,max_length=1000)]


class StaffGrant(Contract):
    permissions: frozenset[str]
    reason: Annotated[str,Field(min_length=5,max_length=1000)]


async def invoke(awaitable):
    try:
        return await awaitable
    except DomainError as exc:
        raise api_error(exc) from None
    except ValidationError:
        raise HTTPException(422,detail={'code':'invalid_special_order_payload'}) from None


def make_integrated_special_orders_router(db, current_user):
    router=APIRouter(prefix='/special-orders-v1',tags=['Mezan special orders'])
    enabled=bound(db)
    if enabled is None or not enabled.enablement.reads:
        return router
    orders=IntegratedOrders(db)

    async def actor(user=Depends(current_user)):
        async def resolve():
            result=await current_actor(db,user)
            require_bound(db,tenant_id=result.tenant_id)
            return result
        return await invoke(resolve())

    @router.get('/capabilities')
    async def capabilities(principal=Depends(actor)):
        require(principal,'special_orders.read')
        return {'source':'mezan','schema_version':1,'permissions':sorted(principal.permissions),
            'creation_enabled':enabled.enablement.creation,'commands_enabled':enabled.enablement.commands,
            'financial_enabled':enabled.enablement.financial,
            'sar_fx':FxSnapshot(currency='SAR',rate_to_sar='1',captured_at=datetime.now(timezone.utc),evidence_id='sar-fixed-1').model_dump(mode='json'),
            'financial_owner':'mezan_v2','supplier_owner':'supplier_receiving_v2',
            'workflow_owner':'order_review_workflows','new_carrier_labels_require_owner_attestation':True}

    @router.get('/catalog/products/{product_id}')
    async def product(product_id:str,variant_id:str|None=None,principal=Depends(actor)):
        async def read():
            require(principal,'special_orders.read')
            return (await ExistingCatalogAdapter(db).product(principal.tenant_id,product_id,variant_id)).model_dump(mode='json')
        return await invoke(read())

    @router.get('/source-orders/{order_number}')
    async def original(order_number:str,principal=Depends(actor)):
        async def read():
            require(principal,'special_orders.read')
            return (await ExistingPorts(db).original(principal.tenant_id,order_number)).model_dump(mode='json')
        return await invoke(read())

    @router.post('/evidence',status_code=201)
    async def upload_evidence(kind:Literal['bank_receipt','carrier_label','cost_document']=Form(...),
                              file:UploadFile=File(...),principal=Depends(actor)):
        async def upload():
            permission='special_orders.receipt_upload' if kind=='bank_receipt' else 'special_orders.admin'
            require(principal,permission)
            require_bound(db,write=True,tenant_id=principal.tenant_id)
            content=await file.read(MAX_BYTES+1)
            if len(content)>MAX_BYTES:raise DomainError('evidence_type_or_size_invalid',422)
            store=EvidenceStore(db);await store.ensure_indexes()
            evidence=await store.upload(principal.tenant_id,principal.actor_id,kind=kind,
                content_type=file.content_type or '',data=content)
            return evidence.model_dump(mode='json')
        try:return await invoke(upload())
        finally:await file.close()

    @router.get('/evidence/{object_id}')
    async def read_evidence(object_id:UUID,principal=Depends(actor)):
        async def read():
            row=await EvidenceStore(db).get(principal.tenant_id,str(object_id))
            owner='special_orders.finance' in principal.permissions
            own_receipt=row['kind']=='bank_receipt' and row['created_by']==principal.actor_id and 'special_orders.receipt_upload' in principal.permissions
            if not owner and not own_receipt:raise DomainError('private_evidence_permission_required',403)
            return Response(bytes(row['content']),media_type=row['content_type'],headers={
                'Cache-Control':'no-store, private','X-Content-Type-Options':'nosniff',
                'Content-Security-Policy':"sandbox; default-src 'none'",'Content-Disposition':'inline; filename="evidence"'})
        return await invoke(read())

    @router.post('/evidence/attest-label')
    async def attest_label(request:LabelAttestation,principal=Depends(actor)):
        async def approve():
            require(principal,'special_orders.admin')
            await resolve_party(db,principal.tenant_id,'courier',request.carrier_key)
            return await EvidenceStore(db).attest_label(principal.tenant_id,principal.actor_id,
                **request.model_dump(exclude={'evidence','recipient'}),evidence=request.evidence,recipient=request.recipient)
        return await invoke(approve())

    @router.post('/accounting-policies',status_code=201)
    async def approve_policy(request:AccountingPolicy,principal=Depends(actor)):
        async def approve():
            require(principal,'special_orders.admin');require_bound(db,write=True,tenant_id=principal.tenant_id)
            await ensure_indexes(db)
            await EvidenceStore(db).verify(principal.tenant_id,request.evidence)
            normalized=request.model_copy(update={'effective_at':request.effective_at.astimezone(timezone.utc)})
            data=normalized.model_dump(mode='json');key=data['effective_at']
            old=await db[POLICIES].find_one({'tenant_id':principal.tenant_id,'effective_at':key},{'_id':0})
            if old:
                if old['policy']!=data:raise DomainError('accounting_policy_immutable_use_new_effective_date')
                return old
            row={'tenant_id':principal.tenant_id,'effective_at':key,'policy':data,
                'approved_by':principal.actor_id,'approved_at':datetime.now(timezone.utc).isoformat()}
            from pymongo.errors import DuplicateKeyError
            try:await db[POLICIES].insert_one(dict(row))
            except DuplicateKeyError:raise DomainError('accounting_policy_approval_conflict') from None
            return row
        return await invoke(approve())

    @router.post('/fx-snapshots',status_code=201)
    async def approve_fx(request:FxApproval,principal=Depends(actor)):
        async def approve():
            require(principal,'special_orders.admin');require_bound(db,write=True,tenant_id=principal.tenant_id)
            if request.snapshot.currency=='SAR':raise DomainError('use_sar_fixed_snapshot',422)
            if request.snapshot.captured_at>datetime.now(timezone.utc):raise DomainError('future_fx_snapshot_not_allowed',422)
            if request.evidence.kind!='cost_document':raise DomainError('fx_document_required',422)
            await ensure_indexes(db);await EvidenceStore(db).verify(principal.tenant_id,request.evidence)
            row={'tenant_id':principal.tenant_id,'evidence_id':request.snapshot.evidence_id,
                'snapshot':request.snapshot.model_dump(mode='json'),'evidence':request.evidence.model_dump(mode='json'),
                'approved_by':principal.actor_id,'approved_at':datetime.now(timezone.utc).isoformat(),'reason':request.reason}
            old=await db[FX_SNAPSHOTS].find_one({'tenant_id':principal.tenant_id,'evidence_id':request.snapshot.evidence_id},{'_id':0})
            if old:
                if old['snapshot']!=row['snapshot'] or old['evidence']!=row['evidence']:raise DomainError('fx_snapshot_immutable')
                return old
            from pymongo.errors import DuplicateKeyError
            try:await db[FX_SNAPSHOTS].insert_one(dict(row))
            except DuplicateKeyError:raise DomainError('fx_snapshot_approval_conflict') from None
            return row
        return await invoke(approve())

    @router.put('/access/{actor_id}')
    async def grant(actor_id:str,request:StaffGrant,principal=Depends(actor)):
        async def apply():
            require(principal,'special_orders.admin');require_bound(db,write=True,tenant_id=principal.tenant_id)
            if request.permissions-STAFF_PERMISSIONS:raise DomainError('financial_permissions_owner_only',422)
            member,tenant=await fresh_principal(db,{'id':actor_id})
            if tenant!=principal.tenant_id or member['id']==tenant:raise DomainError('staff_member_scope_invalid',403)
            await db[GRANTS].create_index([('tenant_id',1),('actor_id',1)],unique=True)
            row={'tenant_id':tenant,'actor_id':actor_id,'permissions':sorted(request.permissions),'status':'active' if request.permissions else 'revoked',
                'changed_by':principal.actor_id,'changed_at':datetime.now(timezone.utc).isoformat(),'reason':request.reason}
            async def save(scoped):
                await scoped[GRANTS].update_one({'tenant_id':tenant,'actor_id':actor_id},{'$set':row},upsert=True)
                await scoped['mezan_special_order_access_audit_v1'].insert_one(dict(row))
                return row
            return await transaction(db,save)
        return await invoke(apply())

    @router.post('/inventory-valuations',status_code=201)
    async def inventory_valuation(request:InventoryValuation,principal=Depends(actor)):
        return await invoke(approve_valuation(db,principal,request))

    @router.post('',status_code=201)
    async def create(request:CreateOrder,principal=Depends(actor),
        idempotency_key:str=Header(alias='Idempotency-Key',min_length=8,max_length=128)):
        return await invoke(orders.create(principal,request,idempotency_key))

    @router.get('')
    async def list_orders(principal=Depends(actor),limit:int=Query(30,ge=1,le=100),
                          before_date:str|None=Query(None,max_length=40),before_id:UUID|None=None):
        async def read():
            if (before_date is None)!=(before_id is None):raise DomainError('complete_cursor_required',422)
            if before_date:
                try:
                    dt=datetime.fromisoformat(before_date)
                    if dt.utcoffset() is None:raise ValueError()
                except ValueError:raise DomainError('invalid_cursor',422) from None
            return await orders.list(principal,limit,(before_date,str(before_id)) if before_id else None)
        return await invoke(read())

    @router.get('/{order_id}')
    async def detail(order_id:UUID,principal=Depends(actor)):
        return await invoke(orders.get(principal,str(order_id)))

    @router.post('/{order_id}/commands')
    async def command(order_id:UUID,request:SourceCommand,principal=Depends(actor),
        idempotency_key:str=Header(alias='Idempotency-Key',min_length=8,max_length=128)):
        return await invoke(orders.command(principal,str(order_id),request.expected_revision,idempotency_key,request.operation,request.payload))

    @router.post('/{order_id}/finance')
    async def finance(order_id:UUID,request:FinancialCommand,principal=Depends(actor),
        idempotency_key:str=Header(alias='Idempotency-Key',min_length=8,max_length=128)):
        return await invoke(FinancialService(db).execute(principal,str(order_id),request.expected_revision,idempotency_key,request.operation,request.payload))

    @router.get('/{order_id}/label')
    async def label(order_id:UUID,user=Depends(current_user)):
        async def read():
            _,tenant=await fresh_principal(db,user)
            require_bound(db,tenant_id=tenant)
            doc=await db[COLLECTION].find_one({'tenant_id':tenant,'order_id':str(order_id)},{'_id':0,'order_number':1})
            if not doc:raise DomainError('order_not_found',404)
            principal=await label_actor(db,user,doc['order_number'])
            doc,workflow=await current_document(db,tenant,doc['order_number'])
            if not workflow or not doc['source_frozen']:raise DomainError('reviewed_source_required')
            from .domain import effective_delivery
            delivery=effective_delivery(doc)
            if delivery['method']!='carrier':raise DomainError('carrier_label_not_found',404)
            evidence=Evidence.model_validate(delivery['label'])
            store=EvidenceStore(db)
            await store.verify(tenant,evidence,carrier_key=delivery['carrier_key'],tracking_number=delivery['tracking_number'])
            row=await store.get(tenant,evidence.object_id)
            # This endpoint displays the authorized immutable label bytes; issuing
            # or reissuing still passes current balance/address guards separately.
            return Response(bytes(row['content']),media_type=row['content_type'],headers={
                'Cache-Control':'no-store, private','X-Content-Type-Options':'nosniff',
                'Content-Security-Policy':"sandbox; default-src 'none'",'Content-Disposition':'inline; filename="shipping-label"'})
        return await invoke(read())

    return router
