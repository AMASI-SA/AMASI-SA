"""Bind native non-cash review to a verified MZ2 bank movement, atomically.

Driver evidence is a claim, not bank money. Existing accountant authorization is
read again from persisted users. A receipt approval cannot silently skip the
ledger; the bank transaction, customer receivable and native review commit once.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from .access import fresh_principal
from .binding import require_bound, transaction
from .canonical_adapter import is_local_order_number
from .contracts import ReceiptClaim, Evidence
from .domain import DomainError, add_event, add_receipt, balances, digest
from .evidence import EvidenceStore
from .finance_contracts import BankCollection
from .finance_service import FinancialService, ensure_agreement, persist, verify_document_finance
from .ledger_adapter import acquire_ledger_fence, ensure_indexes, require_cutover
from .source_hooks import WORKFLOWS, api_error, current_document
from .delivery_bridge import now_iso, assignment_facts


def review_identity(review):
    keys=('id','user_id','assignment_id','driver_id','order_id','order_number','amount_native_minor',
        'currency','payment_method','receipt_reference','special_receipt_evidence','bank_account_id','revision')
    return {k:deepcopy(review.get(k)) for k in keys}


async def local_payment_review(db, *, actor, tenant, assignment_id, review, payload):
    if not is_local_order_number(review.get('order_number')):
        if payload.movement is not None:
            raise api_error(DomainError('bank_movement_not_supported_by_ordinary_review',422))
        return None
    from store_delivery_payment_review_routes import (_require_accountant, PAYMENT_EVENTS,
        ensure_store_delivery_payment_review_indexes)
    from store_delivery_handover_routes import ASSIGNMENTS
    from store_delivery_driver_app_routes import DRIVER_COLLECTIONS, DRIVER_PAYMENT_REVIEWS
    from store_delivery_payment_evidence_routes import RECEIPTS
    try:
        require_bound(db,write=True,finance=payload.decision=='approved',tenant_id=tenant)
        if payload.decision=='approved' and payload.movement is None:
            raise DomainError('verified_bank_movement_required_for_approval',422)
        if payload.decision=='rejected' and payload.movement is not None:
            raise DomainError('rejected_evidence_cannot_post_money',422)
        await ensure_indexes(db)
        await ensure_store_delivery_payment_review_indexes(db)
        async def apply(scoped):
            principal, merchant = await fresh_principal(scoped, actor)
            if merchant != tenant:
                raise DomainError('merchant_scope_mismatch',403)
            _require_accountant(principal)
            # A concurrent permission revocation must conflict with this command,
            # not be accepted from a stale user snapshot just before bank posting.
            await scoped.users.update_one({'id':principal['id']},{'$inc':{'special_financial_fence':1}})
            live=await scoped[DRIVER_PAYMENT_REVIEWS].find_one({'user_id':tenant,'assignment_id':assignment_id},{'_id':0})
            if not live or live.get('id')!=review.get('id'):
                raise DomainError('store_delivery_payment_review_not_found',404)
            doc, workflow=await current_document(scoped,tenant,live['order_number'],write=True,lock=True)
            await verify_document_finance(scoped,doc)
            assignment=await scoped[ASSIGNMENTS].find_one({'user_id':tenant,'id':assignment_id},{'_id':0})
            if (not assignment or assignment.get('status')!='delivered' or live['order_id']!=doc['order_id']
                or assignment.get('order_id')!=doc['order_id'] or assignment.get('driver_id')!=live['driver_id']
                or assignment.get('special_assignment_digest')!=digest(assignment_facts(assignment))):
                raise DomainError('local_payment_assignment_integrity_failed')
            if live.get('special_review_source_digest')!=digest(review_identity(live)):
                raise DomainError('native_payment_review_source_changed')
            fingerprint=digest([review_identity(live),payload.model_dump(mode='json')])
            if live.get('status')!='pending':
                if live.get('special_review_digest')!=fingerprint:
                    raise DomainError('payment_review_already_final')
                return {'assignment_id':assignment_id,'decision':live['status'],
                    'payment_status':live['payment_status'],'review':{k:v for k,v in live.items() if k!='user_id'}}
            if live.get('payment_method') not in {'bank_transfer','card_terminal'}:
                raise DomainError('payment_review_not_required')
            evidence=Evidence.model_validate(live['special_receipt_evidence'])
            await EvidenceStore(scoped).verify(tenant,evidence)
            approved=payload.decision=='approved'
            movement_id=None
            if approved:
                amount=live['amount_native_minor']
                if type(amount) is not int or amount<=0 or live['currency']!=doc['fx']['currency'] or amount!=balances(doc)['remaining_minor']:
                    raise DomainError('delivery_payment_amount_changed_requires_reconciliation')
                movement=payload.movement
                if live['payment_method']=='bank_transfer' and movement.bank_account_id!=live.get('bank_account_id'):
                    raise DomainError('payment_review_bank_account_mismatch')
                # The actual bank movement may have separate evidence from a POS
                # slip. Both private evidence identities remain in the audit.
                await require_cutover(scoped,tenant,movement.occurred_at.isoformat())
                await acquire_ledger_fence(scoped,tenant)
                await ensure_agreement(scoped,doc,principal['id'])
                claim=ReceiptClaim(evidence=movement.evidence,bank_account_id=movement.bank_account_id,
                    amount_minor=amount,transferred_at=movement.occurred_at,reference=movement.bank_reference)
                claim_id=add_receipt(doc,claim)
                movement_id=str(uuid5(NAMESPACE_URL,f'native-review:{tenant}:{live["id"]}:{live.get("revision",1)}'))
                await FinancialService(scoped)._bank_collection(scoped,doc,principal['id'],movement_id,
                    BankCollection(receipt_claim_id=claim_id,movement=movement))
                revision=doc['revision'];doc['revision']+=1
                add_event(doc,'special_order.native_payment_approved',principal['id'])
                await persist(scoped,doc,revision)
            now=now_iso()
            status='paid' if approved else 'payment_evidence_rejected'
            patch={'status':payload.decision,'payment_status':status,'reviewed_at':now,
                'reviewed_by':principal['id'],'review_note':payload.note.strip(),
                'special_review_digest':fingerprint,'special_bank_movement_id':movement_id}
            result=await scoped[DRIVER_PAYMENT_REVIEWS].update_one({'user_id':tenant,'assignment_id':assignment_id,'status':'pending'}, {'$set':patch})
            if result.matched_count!=1:
                raise DomainError('payment_review_concurrent_update')
            audit={'review_status':payload.decision,'payment_status':status,'payment_confirmed':approved,
                   'reviewed_at':now,'reviewed_by':principal['id'],'review_note':payload.note.strip(),
                   'special_bank_movement_id':movement_id}
            await scoped[DRIVER_COLLECTIONS].update_one({'user_id':tenant,'assignment_id':assignment_id},{'$set':audit})
            await scoped[ASSIGNMENTS].update_one({'user_id':tenant,'id':assignment_id},{'$set':{
                **audit,'payment_review_status':payload.decision,'updated_at':now}})
            await scoped[WORKFLOWS].update_one({'user_id':tenant,'order_number':doc['order_number']},{'$set':{
                'store_delivery_payment_status':status,'store_delivery_payment_confirmed':approved,
                'store_delivery_payment_review_status':payload.decision,'updated_at':now}})
            await scoped[RECEIPTS].update_one({'user_id':tenant,'token':live['receipt_reference']},{'$set':{
                'review_status':payload.decision,'reviewed_at':now,'reviewed_by':principal['id']}})
            await scoped[PAYMENT_EVENTS].insert_one({'id':str(uuid5(NAMESPACE_URL,f'native-review-audit:{tenant}:{fingerprint}')),
                'user_id':tenant,'assignment_id':assignment_id,'order_id':doc['order_id'],'driver_id':live['driver_id'],
                'decision':payload.decision,'note':payload.note.strip(),'actor_id':principal['id'],'occurred_at':now,
                'review_snapshot':review_identity(live),'bank_movement':payload.movement.model_dump(mode='json') if payload.movement else None,
                'special_bank_movement_id':movement_id})
            return {'assignment_id':assignment_id,'decision':payload.decision,'payment_status':status,
                    'review':{k:v for k,v in {**live,**patch}.items() if k!='user_id'}}
        return await transaction(db,apply,tenant_id=tenant,scopes=frozenset({"workflow", "financial", "evidence"}))
    except DomainError as exc:
        raise api_error(exc) from None


async def local_payment_resubmit(db, *, actor, tenant, driver, assignment, payload):
    if not is_local_order_number(assignment.get('order_number')):
        return None
    from store_delivery_handover_routes import ASSIGNMENTS
    from store_delivery_driver_app_routes import DRIVER_COLLECTIONS, DRIVER_PAYMENT_REVIEWS
    from store_delivery_payment_evidence_routes import RECEIPTS, validate_receipt_reference
    from store_delivery_payment_review_routes import PAYMENT_EVENTS, ensure_store_delivery_payment_review_indexes
    from .delivery_bridge import verify_local_assignment
    import hashlib
    try:
        require_bound(db,write=True,tenant_id=tenant)
        await EvidenceStore(db).ensure_indexes()
        await ensure_store_delivery_payment_review_indexes(db)
        async def apply(scoped):
            principal, merchant=await fresh_principal(scoped,actor)
            if merchant!=tenant or principal.get('role')!='store_driver' or driver.get('account_user_id')!=principal['id']:
                raise DomainError('store_driver_account_required',403)
            active=await scoped.store_drivers.update_one({'user_id':tenant,'id':driver['id'],
                'account_user_id':principal['id'],'status':'active'},{'$inc':{'special_financial_fence':1}})
            if active.matched_count!=1:
                raise DomainError('active_driver_account_required',403)
            live_assignment=await scoped[ASSIGNMENTS].find_one({'user_id':tenant,'id':assignment['id'],
                'driver_id':driver['id'],'active':True,'status':'delivered'},{'_id':0})
            if not live_assignment:
                raise DomainError('driver_assignment_not_found',404)
            doc,_=await verify_local_assignment(scoped,tenant,driver,live_assignment,principal['id'],lock=True)
            live=await scoped[DRIVER_PAYMENT_REVIEWS].find_one({'user_id':tenant,'assignment_id':assignment['id']},{'_id':0})
            if not live or live.get('special_review_source_digest')!=digest(review_identity(live)):
                raise DomainError('native_payment_review_source_changed')
            request_hash=digest(payload.model_dump(mode='json'))
            if live.get('status')=='pending' and live.get('special_resubmit_digest')==request_hash:
                await EvidenceStore(scoped).verify(tenant,Evidence.model_validate(live['special_receipt_evidence']))
                return {k:v for k,v in live.items() if k!='user_id'}
            if live.get('status')!='rejected':
                raise DomainError('payment_review_not_rejected')
            if balances(doc)['remaining_minor']!=live['amount_native_minor']:
                raise DomainError('delivery_payment_amount_changed_requires_reconciliation')
            await validate_receipt_reference(scoped,user_id=tenant,driver_id=driver['id'],
                assignment_id=assignment['id'],receipt_reference=payload.receipt_reference)
            source=await scoped[RECEIPTS].find_one({'user_id':tenant,'driver_id':driver['id'],
                'assignment_id':assignment['id'],'token':payload.receipt_reference,'status':'uploaded'})
            data=bytes((source or {}).get('content',b''))
            if not source or hashlib.sha256(data).hexdigest()!=source.get('sha256') or len(data)!=source.get('size'):
                raise DomainError('native_receipt_integrity_failed')
            evidence=await EvidenceStore(scoped).upload(tenant,principal['id'],kind='bank_receipt',
                content_type=source['content_type'],data=data)
            bank_id=payload.bank_account_id if live['payment_method']=='bank_transfer' else None
            if live['payment_method']=='bank_transfer':
                bank=await scoped.accounts.find_one({'user_id':tenant,'id':bank_id,'account_type':'bank','status':'active'},{'_id':0,'id':1})
                if not bank:
                    raise DomainError('business_bank_account_invalid',422)
            now=now_iso()
            patch={'status':'pending','payment_status':'pending_accountant_review','receipt_reference':payload.receipt_reference,
                'receipt_url':f'/api/store-delivery/evidence/receipt/{payload.receipt_reference}',
                'special_receipt_evidence':evidence.model_dump(mode='json'),'bank_account_id':bank_id,
                'submitted_at':now,'resubmitted_at':now,'revision':int(live.get('revision') or 1)+1,
                'reviewed_at':None,'reviewed_by':None,'review_note':'','special_review_digest':None,
                'special_resubmit_digest':request_hash,'special_bank_movement_id':None}
            patch['special_review_source_digest']=digest(review_identity({**live,**patch}))
            result=await scoped[DRIVER_PAYMENT_REVIEWS].update_one({'user_id':tenant,
                'assignment_id':assignment['id'],'status':'rejected'},{'$set':patch})
            if result.matched_count!=1:
                raise DomainError('payment_review_resubmit_conflict')
            # Original receipt/bank fields remain immutable proof of the delivery
            # event. Current review fields change with explicit attempt history.
            await scoped[DRIVER_COLLECTIONS].update_one({'user_id':tenant,'assignment_id':assignment['id']},{'$set':{
                'review_status':'pending_accountant_review','payment_status':'pending_accountant_review','payment_confirmed':False,
                'receipt_reference':payload.receipt_reference,'bank_account_id':bank_id,'special_receipt_evidence':patch['special_receipt_evidence']}})
            await scoped[ASSIGNMENTS].update_one({'user_id':tenant,'id':assignment['id']},{'$set':{
                'payment_review_status':'pending_accountant_review','payment_status':'pending_accountant_review','updated_at':now}})
            await scoped[WORKFLOWS].update_one({'user_id':tenant,'order_number':doc['order_number']},{'$set':{
                'store_delivery_payment_review_status':'pending_accountant_review','store_delivery_payment_status':'pending_accountant_review','updated_at':now}})
            await scoped[RECEIPTS].update_one({'user_id':tenant,'token':payload.receipt_reference,'status':'uploaded'},
                {'$set':{'status':'bound','bound_at':now}})
            await scoped[RECEIPTS].update_one({'user_id':tenant,'token':live['receipt_reference']},
                {'$set':{'status':'superseded','superseded_at':now,'superseded_by':payload.receipt_reference}})
            await scoped[PAYMENT_EVENTS].insert_one({'id':str(uuid5(NAMESPACE_URL,f'resubmit:{tenant}:{live["id"]}:{request_hash}')),
                'user_id':tenant,'assignment_id':assignment['id'],'order_id':doc['order_id'],'driver_id':driver['id'],
                'decision':'resubmitted','actor_id':principal['id'],'occurred_at':now,
                'previous_review':deepcopy(live),'replacement_review_source':review_identity({**live,**patch})})
            return {k:v for k,v in {**live,**patch}.items() if k!='user_id'}
        return await transaction(db,apply,tenant_id=tenant,scopes=frozenset({"workflow", "financial", "evidence"}))
    except DomainError as exc:
        raise api_error(exc) from None
