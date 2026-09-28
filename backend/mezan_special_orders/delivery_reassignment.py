"""Source-aware reassignment using the existing driver assignment owner."""
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5
from .access import fresh_principal
from .binding import require_bound, transaction
from .canonical_adapter import is_local_order_number
from .delivery_bridge import now_iso, prepare_local_assignment, verify_local_assignment
from .domain import DomainError, digest
from .source_hooks import WORKFLOWS, api_error


async def local_reassignment(db, *, tenant, actor, old, payload):
    if not is_local_order_number(old.get('order_number')):
        return None
    from store_delivery_handover_routes import ASSIGNMENTS, EVENTS, ensure_store_delivery_handover_indexes
    from store_delivery_driver_app_routes import DRIVER_COLLECTIONS, DRIVER_EARNINGS
    from store_delivery_customer_instruction_routes import STORE_DELIVERY_INSTRUCTIONS
    from store_delivery_reassignment_routes import _require_operator
    from store_delivery_domain import assignment_snapshot, StoreDeliveryRuleError
    try:
        require_bound(db,write=True,tenant_id=tenant)
        await ensure_store_delivery_handover_indexes(db)
        fingerprint=digest(payload.model_dump(mode='json'))
        new_id=str(uuid5(NAMESPACE_URL,f'local-driver-reassign:{tenant}:{old["id"]}'))
        async def apply(scoped):
            principal,merchant=await fresh_principal(scoped,actor)
            if merchant!=tenant:
                raise DomainError('merchant_scope_mismatch',403)
            _require_operator(principal)
            await scoped.users.update_one({'id':principal['id']},{'$inc':{'special_assignment_fence':1}})
            previous=await scoped[ASSIGNMENTS].find_one({'user_id':tenant,'id':old['id']},{'_id':0})
            if not previous:
                raise DomainError('store_delivery_assignment_not_found',404)
            if previous.get('status')=='reassigned':
                newer=await scoped[ASSIGNMENTS].find_one({'user_id':tenant,'id':new_id},{'_id':0,'user_id':0})
                if (previous.get('special_reassignment_request_digest')!=fingerprint or not newer
                        or newer.get('special_reassignment_request_digest')!=fingerprint):
                    raise DomainError('idempotency_payload_conflict')
                return {'ok':True,'old_assignment_id':old['id'],'assignment':newer}
            if not previous.get('active') or previous.get('status') not in {'assigned','out_for_delivery'}:
                raise DomainError('delivered_or_closed_assignment_cannot_be_reassigned')
            if previous['driver_id']==payload.driver_id:
                raise DomainError('assignment_already_with_driver')
            prior_driver=await scoped.store_drivers.find_one({'user_id':tenant,'id':previous['driver_id']},{'_id':0})
            if not prior_driver:
                raise DomainError('prior_driver_source_required')
            doc,workflow=await verify_local_assignment(scoped,tenant,prior_driver,previous,prior_driver.get('account_user_id'),lock=True)
            for name in (DRIVER_COLLECTIONS,DRIVER_EARNINGS):
                if await scoped[name].find_one({'user_id':tenant,'assignment_id':previous['id']},{'_id':0,'id':1}):
                    raise DomainError('collected_or_earned_assignment_requires_reconciliation')
            driver=await scoped.store_drivers.find_one({'user_id':tenant,'id':payload.driver_id,'status':'active'},{'_id':0})
            if not driver or not driver.get('account_user_id'):
                raise DomainError('active_driver_account_required',403)
            driver_user,driver_tenant=await fresh_principal(scoped,{'id':driver['account_user_id']})
            if driver_tenant!=tenant or driver_user.get('role')!='store_driver':
                raise DomainError('active_driver_account_required',403)
            await scoped.store_drivers.update_one({'user_id':tenant,'id':driver['id'],'status':'active'},
                {'$inc':{'special_assignment_fence':1}})
            snapshot=assignment_snapshot(driver=driver,shipping_city=doc['recipient']['address']['city'])
            now=now_iso()
            new={'id':new_id,'user_id':tenant,'session_id':None,
                'order_id':doc['order_id'],'order_number':doc['order_number'],'barcode':previous['barcode'],**snapshot,
                'status':'assigned','active':True,'assigned_at':now,'assigned_by':principal['id'],'delivered_at':None,
                'reassigned_from_assignment_id':old['id'],'reassignment_reason':payload.reason,
                'special_reassignment_request_digest':fingerprint}
            # Reassignment is a native return to pickup, not a change to the
            # source order or an invented Salla transition. One transaction keeps
            # the new assignment and the existing workflow coherent.
            if workflow['stage'] not in {'completed','delivering'}:
                raise DomainError('driver_reassignment_workflow_conflict')
            await scoped[WORKFLOWS].update_one({'user_id':tenant,'order_number':doc['order_number']},
                {'$set':{'stage':'completed'}})
            new.update(await prepare_local_assignment(scoped,tenant,driver,new))
            await scoped[ASSIGNMENTS].insert_one(deepcopy(new))
            changed=await scoped[ASSIGNMENTS].update_one({'user_id':tenant,'id':old['id'],'active':True,'status':previous['status']},
                {'$set':{'active':False,'status':'reassigned','reassigned_at':now,'reassigned_by':principal['id'],
                    'reassigned_to_assignment_id':new_id,'reassignment_reason':payload.reason,'updated_at':now,
                    'special_reassignment_request_digest':fingerprint}})
            if changed.modified_count!=1:
                raise DomainError('assignment_reassign_conflict')
            await scoped[STORE_DELIVERY_INSTRUCTIONS].update_many({'user_id':tenant,'order_id':doc['order_id'],'status':'active'},
                {'$set':{'driver_id':driver['id'],'driver_name_snapshot':driver.get('name'),
                    'acknowledged_at':None,'acknowledged_by_driver_id':None,'last_reminder_code':None,
                    'last_reminder_at':None,'updated_at':now,'updated_by':principal['id']}})
            await scoped[WORKFLOWS].update_one({'user_id':tenant,'order_number':doc['order_number']},
                {'$set':{'store_delivery_assignment_id':new_id,'store_delivery_driver_id':driver['id'],
                    'store_delivery_driver_name':driver.get('name'),'store_delivery_fee_snapshot':new['delivery_fee_snapshot'],
                    'store_delivery_status':'assigned','store_delivery_updated_at':now,'store_delivery_assigned_at':now,
                    'store_courier_assignment_state':'assigned_waiting_pickup','store_courier_assignee_id':driver['account_user_id'],
                    'store_courier_assignee_name':driver.get('name'),'store_courier_driver_profile_id':driver['id'],
                    'store_courier_assigned_at':now,'store_courier_assigned_by_id':principal['id'],
                    'special_delivery_assignment_digest':new['special_assignment_digest'],'updated_at':now},
                 '$unset':{'store_courier_picked_up_at':'','store_courier_picked_up_by_id':''}})
            await scoped[EVENTS].insert_one({'id':str(uuid5(NAMESPACE_URL,'local-reassign-event:'+new_id)),
                'user_id':tenant,'event_type':'store_delivery_reassigned','order_id':doc['order_id'],
                'order_number':doc['order_number'],'source_provider':'mezan','old_assignment_id':old['id'],
                'new_assignment_id':new_id,'old_driver_id':previous['driver_id'],'new_driver_id':driver['id'],
                'reason':payload.reason,'actor_id':principal['id'],'occurred_at':now})
            return {'ok':True,'old_assignment_id':old['id'],'assignment':{k:v for k,v in new.items() if k!='user_id'}}
        return await transaction(db,apply)
    except DomainError as exc:
        raise api_error(exc) from None
    except StoreDeliveryRuleError as exc:
        raise api_error(DomainError(str(exc))) from None
