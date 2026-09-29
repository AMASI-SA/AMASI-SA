"""Read-only authorization for inherited sensitive HTTP boundaries.

This does not initialize accounts/settings, alter Qoyod's shared data owner,
or participate in the automatic sender. Native employee sessions must be
authorized as the actor, never as the owner-shaped request context.
"""
from fastapi import HTTPException

from accounting_module_contract import require_owner
from ai_store_access_contract import effective_permissions, find_role_assignment


async def _security_actor(db, user):
    actor_id = user.get("_mobile_actor_id") or user.get("id")
    if not isinstance(actor_id, str) or not actor_id:
        raise HTTPException(403, "security_actor_unavailable")
    actor = await db.users.find_one({"id": actor_id}, {
        "_id": 0, "id": 1, "role": 1, "created_by": 1,
        "is_active": 1, "disabled": 1,
    })
    if (not actor or actor.get("id") != actor_id
            or actor.get("is_active") is False or actor.get("disabled") is True):
        raise HTTPException(403, "security_actor_unavailable")
    return actor


async def require_security_owner(db, user):
    actor = await _security_actor(db, user)
    require_owner(actor)
    return actor


async def require_qoyod_security_owner(db, user):
    actor = await require_security_owner(db, user)
    # Explicit owner-approved authority contract. Never infer main ownership
    # from role, creation time, integration credentials, or another setting.
    settings = await db.qoyod_settings.find_one(
        {"user_id": "main"}, {"_id": 0, "security_owner_id": 1}
    )
    linked_owner = (settings or {}).get("security_owner_id")
    if type(linked_owner) is not str or linked_owner != actor["id"]:
        raise HTTPException(403, "qoyod_security_owner_required")
    return actor


async def require_product_permission(db, user, permission):
    actor = await _security_actor(db, user)
    if actor.get("role") == "owner":
        return actor["id"]
    merchant_id = actor.get("created_by")
    if not isinstance(merchant_id, str) or not merchant_id:
        raise HTTPException(403, "product_permission_required")
    assignment = await find_role_assignment(
        db, owner_user_id=merchant_id, user_id=actor["id"]
    )
    if permission not in effective_permissions(assignment):
        raise HTTPException(403, "product_permission_required")
    return merchant_id

