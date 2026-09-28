"""Fresh server-owned principals; no client tenant/role or inferred owner grants."""
from __future__ import annotations

from .contracts import Actor
from .domain import DomainError

GRANTS='mezan_special_order_access_v1'
STAFF_PERMISSIONS=frozenset({'special_orders.read','special_orders.create','special_orders.edit',
    'special_orders.review','special_orders.receipt_upload','special_orders.labels'})
OWNER_PERMISSIONS=STAFF_PERMISSIONS|frozenset({'special_orders.finance','special_orders.reconcile',
    'special_orders.integrate','special_orders.admin'})


async def fresh_principal(db, session_user):
    if not isinstance(session_user,dict) or not isinstance(session_user.get('id'),str):
        raise DomainError('authentication_required',401)
    row=await db.users.find_one({'id':session_user['id']},{'_id':0})
    if not row or row.get('disabled') is True or row.get('deleted') is True or row.get('is_active') is False or str(row.get('status') or '').casefold() in {'disabled','inactive','blocked','deleted'}:
        raise DomainError('active_user_required',403)
    # Session provenance remains owned by the application's JWT dependency. A
    # fresh persisted role controls new financial authority, never `is_owner`.
    role=str(row.get('role') or '').casefold()
    tenant=str(row['id'] if role=='owner' else row.get('created_by') or '')
    if not tenant:
        raise DomainError('merchant_scope_required',403)
    if role!='owner':
        owner=await db.users.find_one({'id':tenant,'role':'owner'},{'_id':0,'id':1,'disabled':1,'status':1})
        if not owner or owner.get('disabled') is True or owner.get('status') in {'disabled','inactive','blocked'}:
            raise DomainError('active_merchant_required',403)
    return row,tenant


async def current_actor(db, session_user):
    row,tenant=await fresh_principal(db,session_user)
    if str(row.get('role') or '').casefold()=='owner':
        permissions=OWNER_PERMISSIONS
    else:
        grant=await db[GRANTS].find_one({'tenant_id':tenant,'actor_id':row['id'],'status':'active'},{'_id':0})
        permissions=frozenset((grant or {}).get('permissions',()))&STAFF_PERMISSIONS
    return Actor(tenant_id=tenant,actor_id=row['id'],permissions=permissions)


async def label_actor(db, session_user, order_number):
    actor=await current_actor(db,session_user)
    if 'special_orders.labels' in actor.permissions:
        return actor
    # The existing courier permission allows only his assigned shipment, not the
    # bank receipts, finance commands, another courier's label or customer lists.
    from store_courier_dispatch_routes import _actor_context, DELIVER_PERMISSION
    fresh,_=await fresh_principal(db,session_user)
    context=await _actor_context(db,fresh)
    permissions=set(context.get('permissions') or ())
    if DELIVER_PERMISSION not in permissions:
        raise DomainError('label_permission_required',403)
    row=await db['order_review_workflows'].find_one({'user_id':actor.tenant_id,'order_number':order_number,
        'store_courier_assignee_id':actor.actor_id},{'_id':0,'stage':1})
    if not row or row.get('stage') in {'cancelled','refunded'}:
        raise DomainError('assigned_shipment_required',403)
    return actor
