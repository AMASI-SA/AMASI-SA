"""Live review authority for one real actor and one authenticated client.

App grants never become Mezan browser permissions. ``request_client`` is
supplied by the authenticated route, not by a request body or a grant snapshot.
This helper is read-only and is also called inside the financial transaction.
"""
from fastapi import HTTPException

from mobile_app_permissions import (
    MOBILE_APP_ACCESS, MOBILE_APP_CLIENT, effective_mobile_app_permissions,
)

REVIEW_PERMISSION = 'store_delivery.payments.review'
APP_REVIEW_PERMISSIONS = frozenset({'app.page.couriers', 'app.action.couriers.payment_review'})


def _deny():
    raise HTTPException(403, detail={'code': 'accountant_permission_required'})


async def require_review_actor(db, owner, actor_id, *, request_client=None):
    if (not isinstance(owner, str) or not owner or not isinstance(actor_id, str) or not actor_id
            or request_client not in (None, MOBILE_APP_CLIENT)):
        _deny()
    actor = await db.users.find_one({'id': actor_id}, {'_id': 0, 'id': 1, 'role': 1,
        'created_by': 1, 'name': 1, 'email': 1, 'is_active': 1, 'disabled': 1, 'deleted_at': 1,
        'extra_permissions': 1, 'denied_permissions': 1})
    if (not actor or actor.get('disabled') or actor.get('is_active') is False or actor.get('deleted_at')
            or REVIEW_PERMISSION in (actor.get('denied_permissions') or [])):
        _deny()
    role = str(actor.get('role') or '').strip().casefold()
    if role == 'store_driver':
        _deny()
    if actor_id == owner:
        if role != 'owner':
            _deny()
        return actor
    if role == 'owner':
        _deny()
    linked_owner = actor.get('created_by')
    if not linked_owner and request_client == MOBILE_APP_CLIENT:
        links = await db.mezan_employees_v2.find({'user_id': owner,
            'account_user_id': actor_id}, {'_id': 0, 'user_id': 1}).limit(2).to_list(2)
        if len(links) == 1:
            linked_owner = links[0]['user_id']
    if linked_owner != owner:
        _deny()
    if request_client == MOBILE_APP_CLIENT:
        access = await db[MOBILE_APP_ACCESS].find_one({'owner_user_id': owner, 'user_id': actor_id})
        if not APP_REVIEW_PERMISSIONS <= set(effective_mobile_app_permissions(access)):
            _deny()
    elif role not in {'admin', 'accountant'} and REVIEW_PERMISSION not in (actor.get('extra_permissions') or []):
        _deny()
    # Restore the server-owned legacy employee link without lending owner identity.
    return {**actor, 'created_by': owner}


async def review_bank_ids(db, owner, actor):
    """Account assignment narrows review authority; it never grants that authority."""
    if actor.get('role') == 'owner' and actor.get('id') == owner:
        return None
    access = await db[MOBILE_APP_ACCESS].find_one({'owner_user_id': owner, 'user_id': actor['id']}) or {}
    ids = (access.get('operational_banks') or {}).get('bank_ids') or []
    return {value for value in ids if isinstance(value, str) and value}


async def require_review_bank(db, owner, actor, bank_id):
    allowed = await review_bank_ids(db, owner, actor)
    if allowed is not None and bank_id not in allowed:
        raise HTTPException(403, detail={'code': 'operational_bank_not_assigned'})


async def require_review_scope(db, owner, actor_id, assignment_id, *, request_client=None):
    actor = await require_review_actor(db, owner, actor_id, request_client=request_client)
    review = await db.store_delivery_payment_reviews.find_one({'user_id': owner, 'assignment_id': assignment_id})
    if not review and not (actor.get('role') == 'owner' and actor.get('id') == owner):
        raise HTTPException(403, detail={'code': 'operational_bank_not_assigned'})
    if review and (review.get('payment_method') == 'bank_transfer' or review.get('bank_account_id')):
        await require_review_bank(db, owner, actor, review.get('bank_account_id'))
    return actor
