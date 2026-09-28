"""Mezan source adapter for the existing Amasi Delivery workflow and ledger.

The existing assignment, driver-earning, collection and review collections remain
owners of delivery facts. No local order is inserted into unified_orders; no cash
collection is credited as another sale. All local delivery effects commit together.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5
import hashlib

from .binding import bound, require_bound, transaction
from .canonical_adapter import is_local_order_number
from .contracts import CostProof, EXPONENTS, FxSnapshot, LedgerProof
from .domain import (DomainError, add_event, balances, consume_financial_proof,
                     digest, dispatch_blockers, effective_delivery)
from .evidence import EvidenceStore
from .finance_service import ensure_agreement, persist, verify_document_finance
from .ledger_adapter import (acquire_ledger_fence, counterparty_key, ensure_indexes,
    leg, major, minor, post_group, require_cutover, save_event, verify_event)
from .source_hooks import WORKFLOWS, current_document


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def native_major(amount, currency):
    if type(amount) is not int or amount < 0:
        raise DomainError('invalid_native_minor_amount')
    return float(Decimal(amount) / (Decimal(10) ** EXPONENTS[currency]))


def local_order_projection(document, workflow=None):
    """Native delivery DTO; source_provider explicitly identifies non-Salla facts."""
    recipient = document['recipient']
    address = recipient.get('address') or {}
    balance = balances(document)
    currency = document['fx']['currency']
    return {**{k: deepcopy(v) for k, v in (workflow or {}).items() if k.startswith('store_delivery_')},
        'source_provider': 'mezan', 'order_id': document['order_id'], 'order_number': document['order_number'],
        'customer_name': recipient['name'], 'customer_mobile': recipient['mobile'],
        'shipping_address': deepcopy(address), 'shipping_city': address.get('city'),
        'shipping_district': address.get('district'), 'shipping_street': address.get('formatted'),
        'currency': currency, 'remaining_amount_minor': balance['remaining_minor'],
        'remaining_amount': native_major(balance['remaining_minor'], currency),
        'paid_amount': native_major(balance['net_collected_minor'], currency),
        'total_amount': native_major(document['customer_agreed_minor'], currency),
        'has_remaining_amount': bool(balance['remaining_minor']),
        'payment_status': 'paid' if not balance['remaining_minor'] else 'pending',
        'purpose_badge': document['badge'], 'original_order_number': (document.get('original') or {}).get('order_number')}


async def local_order_for_number(db, tenant, number):
    if not is_local_order_number(number):
        return None
    document, workflow = await current_document(db, tenant, number)
    await verify_document_finance(db, document)
    return local_order_projection(document, workflow)


async def delivery_order_lookup(db, tenant, barcode, candidates):
    local = await local_order_for_number(db, tenant, barcode)
    if local is not None:
        return local
    from store_delivery_handover_routes import ORDERS
    return await db[ORDERS].find_one({'user_id': tenant, '$or': candidates}, {'_id': 0})


def order_metadata_collection(db, number):
    from store_delivery_handover_routes import ORDERS
    # Native routing writes only delivery metadata to the shared workflow. The
    # immutable local recipient/options/charges are never changed by a courier.
    return db[WORKFLOWS if is_local_order_number(number) else ORDERS]


def assignment_facts(row):
    fields = ('id', 'user_id', 'order_id', 'order_number', 'driver_id',
              'driver_name_snapshot', 'driver_city_snapshot', 'shipping_city_snapshot',
              'delivery_fee_snapshot', 'coverage_mode_snapshot', 'assigned_at', 'assigned_by')
    return {k: deepcopy(row.get(k)) for k in fields}


async def prepare_local_assignment(db, tenant, driver, row):
    if not is_local_order_number(row.get('order_number')):
        return {}
    binding = require_bound(db, write=True, tenant_id=tenant)
    if binding.session is None:
        raise DomainError('real_mongo_transaction_required')
    doc, workflow = await current_document(db, tenant, row['order_number'], write=True, lock=True)
    await verify_document_finance(db, doc)
    if row['order_id'] != doc['order_id'] or effective_delivery(doc)['method'] != 'courier':
        raise DomainError('local_driver_assignment_source_mismatch')
    problems = dispatch_blockers(doc)
    if problems:
        raise DomainError(problems[0])
    if not workflow or workflow.get('stage') != 'completed' or workflow.get('assembly_status') != 'completed':
        raise DomainError('assembly_completion_required')
    if not driver.get('account_user_id'):
        raise DomainError('active_driver_account_required')
    city = doc['recipient'].get('address', {}).get('city')
    from store_delivery_domain import assert_driver_can_take_shipment
    assert_driver_can_take_shipment(driver=driver, shipping_city=city)
    if row.get('shipping_city_snapshot') != city:
        raise DomainError('shipping_address_changed_rescan_required')
    # Fees are snapshotted SAR, not taken from any client request or current rate
    # when delivery later completes. This rejects nonfinite/negative fee input.
    minor(row['delivery_fee_snapshot'])
    return {'source_provider': 'mezan', 'special_assignment_digest': digest(assignment_facts(row)),
            'special_order_purpose': doc['purpose'], 'currency': doc['fx']['currency']}


async def verify_local_assignment(db, tenant, driver, assignment, actor_id, *, lock=False):
    doc, workflow = await current_document(db, tenant, assignment['order_number'], write=True, lock=lock)
    expected = digest(assignment_facts(assignment))
    if (assignment.get('source_provider') != 'mezan' or assignment.get('special_assignment_digest') != expected
        or not workflow or workflow.get('special_delivery_assignment_digest') != expected
        or workflow.get('store_delivery_assignment_id') != assignment['id']
        or workflow.get('store_courier_driver_profile_id') != driver['id']
        or workflow.get('store_courier_assignee_id') != actor_id
        or driver.get('account_user_id') != actor_id or driver.get('user_id') != tenant
        or assignment.get('driver_id') != driver['id'] or assignment.get('order_id') != doc['order_id']):
        raise DomainError('local_driver_assignment_integrity_failed', 403)
    if effective_delivery(doc)['method'] != 'courier':
        raise DomainError('local_order_is_not_store_delivery')
    await verify_document_finance(db, doc)
    return doc, workflow


def delivery_request_fingerprint(payload):
    # Deprecated client outstanding_amount is intentionally neither accepted as
    # money nor allowed to change replay identity. The server alone sets the due.
    return digest({'target_status': payload.target_status, 'payment_method': payload.payment_method,
                   'receipt_reference': payload.receipt_reference, 'bank_account_id': payload.bank_account_id})


def native_collection_facts(row):
    fields = ('id', 'user_id', 'assignment_id', 'order_id', 'order_number', 'driver_id', 'amount',
              'currency', 'amount_native_minor', 'amount_sar_minor', 'payment_method', 'cod_custody_amount',
              'cod_native_minor', 'original_receipt_reference', 'original_bank_account_id', 'collected_at')
    return {k: deepcopy(row.get(k)) for k in fields}


def native_earning_facts(row):
    return {k: deepcopy(row.get(k)) for k in ('id', 'user_id', 'assignment_id', 'order_id',
        'order_number', 'driver_id', 'amount', 'earned_at')}


async def post_local_delivery(db, doc, driver, assignment, actor_id, collection, earning):
    await require_cutover(db, doc['tenant_id'])
    await acquire_ledger_fence(db, doc['tenant_id'])
    await ensure_agreement(db, doc, actor_id)
    cash_native = collection['cod_native_minor']
    cash_sar = minor(collection['cod_custody_amount'])
    fee_sar = minor(earning['amount'])
    event_id = str(uuid5(NAMESPACE_URL, f'local-native-delivery:{doc["tenant_id"]}:{assignment["id"]}'))
    proofs = []
    entries = []
    if cash_native:
        proof = LedgerProof(tenant_id=doc['tenant_id'], order_id=doc['order_id'], movement_id=event_id+':cash',
            kind='cod_collection', currency=doc['fx']['currency'], amount_minor=cash_native, amount_sar_minor=cash_sar,
            account_id=driver['id'], evidence_id='driver-collection:'+collection['id'])
        consume_financial_proof(doc, proof)
        proofs.append(('cod_collection', proof))
        entries.extend([leg('store_driver', driver['id'], 'debit', cash_sar, 'cod_receivable'),
                        leg('special_order_receivable', doc['order_id'], 'credit', cash_sar, 'receivable')])
    fee = CostProof(tenant_id=doc['tenant_id'], order_id=doc['order_id'], movement_id=event_id+':fee',
        kind='shipping', target_key='shipping', cost_sar_minor=fee_sar, counterparty_id=driver['id'],
        origin='carrier_charge', expense_bucket=doc['policy']['expense_bucket'],
        evidence_id='driver-earning:'+earning['id'])
    consume_financial_proof(doc, fee)
    proofs.append(('delivery_fee', fee))
    if fee_sar:
        entries.extend([leg('expense', counterparty_key(doc['policy']['expense_bucket']), 'debit', fee_sar),
                        leg('store_driver', driver['id'], 'credit', fee_sar, 'delivery_fee_payable')])
    facts = await post_group(db, doc['tenant_id'], actor_id, order_id=doc['order_id'], event_id=event_id,
        entries=entries, metadata={'recognition_kind':'native_store_delivery','assignment_id':assignment['id'],
            'purpose':doc['purpose'],'expense_bucket':doc['policy']['expense_bucket'], 'new_sales_order':False})
    extra = {'native_assignment':assignment_facts(assignment), 'native_collection':native_collection_facts(collection),
             'native_earning':native_earning_facts(earning), 'verified_zero_cost':not fee_sar,
             'collector_kind':'store_driver', 'collector_id':driver['id']}
    for operation, proof in proofs:
        await save_event(db, doc, proof.movement_id, operation=operation, entries=facts, proof=proof, extra=extra)
    return {'accounting_status':'posted' if facts else 'not_required',
        'ledger_txn_group_id':facts[0]['txn_group_id'] if facts else None,
        'accounting_operation_id':'MZ2-FIN-CUTOVER-001', 'special_financial_event_ids':[p.movement_id for _,p in proofs]}


async def _local_driver_status(db, *, tenant, actor_id, driver, assignment, payload):
    """Run the existing native transition for a local source in one transaction."""
    if not is_local_order_number(assignment.get('order_number')):
        return None
    from store_delivery_driver_app_routes import (DRIVER_COLLECTIONS, DRIVER_EARNINGS, DRIVER_PAYMENT_REVIEWS,
        ensure_store_delivery_driver_app_indexes)
    from store_delivery_handover_routes import ASSIGNMENTS, EVENTS as NATIVE_EVENTS, ensure_store_delivery_handover_indexes
    from store_delivery_domain import (assert_delivery_status_transition, DELIVERY_STATUS_DELIVERED,
        DELIVERY_STATUS_OUT_FOR_DELIVERY, driver_earning)
    from store_delivery_payment_evidence_routes import RECEIPTS, validate_receipt_reference
    require_bound(db, write=True, tenant_id=tenant)
    await ensure_indexes(db)
    await EvidenceStore(db).ensure_indexes()
    await ensure_store_delivery_driver_app_indexes(db)
    await ensure_store_delivery_handover_indexes(db)
    fingerprint = delivery_request_fingerprint(payload)
    async def apply(scoped):
        from .access import fresh_principal
        principal, merchant = await fresh_principal(scoped, {'id':actor_id})
        if merchant != tenant or principal.get('role') != 'store_driver':
            raise DomainError('store_driver_account_required',403)
        active = await scoped.store_drivers.update_one({'user_id':tenant,'id':driver['id'],
            'account_user_id':actor_id,'status':'active'},{'$inc':{'special_financial_fence':1}})
        if active.matched_count != 1:
            raise DomainError('active_driver_account_required',403)
        live = await scoped[ASSIGNMENTS].find_one({'user_id':tenant,'id':assignment['id'],
            'driver_id':driver['id'],'active':True}, {'_id':0})
        if not live:
            raise DomainError('driver_assignment_not_found',404)
        doc, workflow = await verify_local_assignment(scoped,tenant,driver,live,actor_id,lock=True)
        target = payload.target_status
        current = live['status']
        assert_delivery_status_transition(current,target,allow_replay=True)
        if current == target:
            expected = live.get('special_delivery_request_digest' if target == DELIVERY_STATUS_DELIVERED else 'special_pickup_request_digest')
            if expected != fingerprint:
                raise DomainError('idempotency_payload_conflict')
            return {k:v for k,v in live.items() if k not in {'_id','user_id'}}
        from order_tracking_notes import enforce_stage_instructions
        await enforce_stage_instructions(scoped,user_id=tenant,order_number=live['order_number'],
            stage='store_courier',actor_id=actor_id,order_wide=True)
        problems = dispatch_blockers(doc)
        if problems:
            raise DomainError(problems[0])
        now = now_iso()
        if target == DELIVERY_STATUS_OUT_FOR_DELIVERY:
            if workflow['stage'] != 'completed' or workflow.get('store_courier_assignment_state') != 'assigned_waiting_pickup':
                raise DomainError('driver_pickup_workflow_conflict')
            await scoped[WORKFLOWS].update_one({'user_id':tenant,'order_number':live['order_number']},
                {'$set':{'stage':'delivering','store_courier_assignment_state':'delivering',
                    'store_courier_picked_up_at':now,'store_courier_picked_up_by_id':actor_id,'updated_at':now}})
            await scoped[ASSIGNMENTS].update_one({'user_id':tenant,'id':live['id'],'status':current},
                {'$set':{'status':target,'out_for_delivery_at':now,'updated_at':now,'special_pickup_request_digest':fingerprint}})
        else:
            if workflow['stage'] != 'delivering' or workflow.get('store_courier_assignment_state') != 'delivering':
                raise DomainError('driver_delivery_workflow_conflict')
            require_bound(scoped,write=True,finance=True,tenant_id=tenant)
            due = balances(doc)['remaining_minor']
            currency = doc['fx']['currency']
            method = payload.payment_method if due else None
            if due and method not in {'cash','bank_transfer','card_terminal'}:
                raise DomainError('collection_method_required',422)
            evidence = None
            if due and method != 'cash':
                await validate_receipt_reference(scoped,user_id=tenant,driver_id=driver['id'],assignment_id=live['id'],
                    receipt_reference=payload.receipt_reference or '')
                source = await scoped[RECEIPTS].find_one({'user_id':tenant,'assignment_id':live['id'],
                    'driver_id':driver['id'],'token':payload.receipt_reference,'status':'uploaded'})
                data = bytes((source or {}).get('content',b''))
                if not source or hashlib.sha256(data).hexdigest() != source.get('sha256') or len(data) != source.get('size'):
                    raise DomainError('native_receipt_integrity_failed')
                evidence = await EvidenceStore(scoped).upload(tenant,actor_id,kind='bank_receipt',
                    content_type=source['content_type'],data=data)
                if method == 'bank_transfer':
                    account = await scoped.accounts.find_one({'user_id':tenant,'id':payload.bank_account_id,
                        'account_type':'bank','status':'active'},{'_id':0,'id':1})
                    if not account:
                        raise DomainError('business_bank_account_invalid',422)
            fx = FxSnapshot.model_validate(doc['fx'])
            previous = balances(doc)['net_collected_minor']
            carry = fx.to_sar_minor(previous+due)-fx.to_sar_minor(previous)
            amount = native_major(due,currency)
            review_status = 'pending_accountant_review' if due and method != 'cash' else 'not_required'
            earning = {'id':str(uuid5(NAMESPACE_URL,'local-driver-earning:'+live['id'])), 'user_id':tenant,
                'assignment_id':live['id'],'order_id':doc['order_id'],'order_number':doc['order_number'],
                'driver_id':driver['id'],'driver_name_snapshot':live.get('driver_name_snapshot'),
                'amount':driver_earning(assignment=live,delivered=True),'status':'due','earned_at':now}
            collection = {'id':str(uuid5(NAMESPACE_URL,'local-driver-collection:'+live['id'])), 'user_id':tenant,
                'assignment_id':live['id'],'order_id':doc['order_id'],'order_number':doc['order_number'],
                'driver_id':driver['id'],'source_provider':'mezan','amount':amount,'currency':currency,
                'amount_native_minor':due,'amount_sar_minor':carry,'amount_source':'mezan_special_orders_v1.verified_remaining',
                'payment_method':method,'cod_custody_amount':major(carry) if method=='cash' else 0,
                'cod_native_minor':due if method=='cash' else 0, 'receipt_reference':payload.receipt_reference,
                'bank_account_id':payload.bank_account_id,'review_status':review_status,'collected_at':now,
                'original_receipt_reference':payload.receipt_reference,'original_bank_account_id':payload.bank_account_id,
                'special_receipt_evidence':evidence.model_dump(mode='json') if evidence else None}
            await scoped[DRIVER_EARNINGS].insert_one(deepcopy(earning))
            await scoped[DRIVER_COLLECTIONS].insert_one(deepcopy(collection))
            if evidence:
                review_row={'id':str(uuid5(NAMESPACE_URL,'local-payment-review:'+live['id'])),
                    'user_id':tenant,'assignment_id':live['id'],'driver_id':driver['id'],
                    'order_id':doc['order_id'],'order_number':doc['order_number'],'source_provider':'mezan',
                    'amount':amount,'currency':currency,'amount_native_minor':due,'amount_sar_minor':carry,
                    'amount_source':collection['amount_source'],'payment_method':method,'receipt_reference':payload.receipt_reference,
                    'receipt_url':f'/api/store-delivery/evidence/receipt/{payload.receipt_reference}',
                    'special_receipt_evidence':evidence.model_dump(mode='json'),'bank_account_id':payload.bank_account_id,
                    'status':'pending','submitted_at':now}
                from .delivery_payments import review_identity
                review_row['special_review_source_digest']=digest(review_identity(review_row))
                await scoped[DRIVER_PAYMENT_REVIEWS].insert_one(review_row)
                await scoped[RECEIPTS].update_one({'user_id':tenant,'token':payload.receipt_reference,'status':'uploaded'},
                    {'$set':{'status':'bound','bound_at':now}})
            accounting = await post_local_delivery(scoped,doc,driver,live,actor_id,collection,earning)
            for name in (DRIVER_EARNINGS,DRIVER_COLLECTIONS):
                await scoped[name].update_one({'user_id':tenant,'assignment_id':live['id']},{'$set':accounting})
            revision=doc['revision'];doc['revision']+=1;doc.pop('costs_finalized',None)
            add_event(doc,'special_order.native_driver_delivered',actor_id)
            await persist(scoped,doc,revision)
            await scoped[ASSIGNMENTS].update_one({'user_id':tenant,'id':live['id'],'status':current},
                {'$set':{'status':target,'delivered_at':now,'updated_at':now,'collection_amount':amount,
                    'collection_method':method,'payment_review_status':review_status,
                    'special_delivery_request_digest':fingerprint,**accounting}})
            await scoped[WORKFLOWS].update_one({'user_id':tenant,'order_number':doc['order_number']},
                {'$set':{'stage':'delivered','store_courier_assignment_state':'delivered',
                    'store_courier_delivered_at':now,'store_courier_delivered_by_id':actor_id,
                    'store_delivery_status':'delivered','store_delivery_payment_review_status':review_status,'updated_at':now}})
        await scoped[NATIVE_EVENTS].insert_one({'id':str(uuid5(NAMESPACE_URL, f'local-driver-transition:{live["id"]}:{target}')),
            'user_id':tenant,'event_type':'store_delivery_'+target,'assignment_id':live['id'],
            'driver_id':driver['id'],'order_id':doc['order_id'],'order_number':doc['order_number'],
            'source_provider':'mezan','actor_id':actor_id,'occurred_at':now})
        return await scoped[ASSIGNMENTS].find_one({'user_id':tenant,'id':live['id']},{'_id':0,'user_id':0})
    try:
        return await transaction(db,apply)
    except DomainError as exc:
        from .source_hooks import api_error
        raise api_error(exc) from None


async def local_driver_status(db, **values):
    from .source_hooks import api_error
    try:
        return await _local_driver_status(db, **values)
    except DomainError as exc:
        raise api_error(exc) from None
