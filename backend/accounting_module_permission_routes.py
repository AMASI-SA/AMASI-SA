"""Owner-only assignment endpoints for independent accounting permissions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from fastapi import Depends, HTTPException

from accounting_module_contract import (
    ACCOUNTING_ACTIONS,
    ACCOUNTING_PAGES,
    ACCOUNTING_PERMISSION_KEYS,
    AccountingPermissionsUpdate,
    OPERATION_ID,
    accounting_user_view,
    is_owner,
    require_owner,
)
from accounting_module_status_routes import fresh_accounting_user


def _team_scope(owner_id: str) -> dict:
    return {"$or": [{"id": owner_id}, {"created_by": owner_id}]}


def install_accounting_permission_routes(router, db, current_user: Callable):
    @router.get("/accounting-module/permissions/catalogue")
    async def accounting_permissions_catalogue(user: dict = Depends(current_user)):
        fresh = await fresh_accounting_user(db, user)
        require_owner(fresh)
        return {
            "operation_id": OPERATION_ID,
            "default_policy": "deny_all_non_owner",
            "pages": list(ACCOUNTING_PAGES),
            "actions": list(ACCOUNTING_ACTIONS),
        }

    @router.get("/accounting-module/permissions/users")
    async def accounting_permissions_users(user: dict = Depends(current_user)):
        fresh = await fresh_accounting_user(db, user)
        require_owner(fresh)
        rows = await db.users.find(
            _team_scope(fresh["id"]),
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "email": 1,
                "role": 1,
                "is_owner": 1,
                "created_by": 1,
                "accounting_permissions": 1,
                "created_at": 1,
            },
        ).sort("created_at", -1).to_list(5000)
        return {
            "operation_id": OPERATION_ID,
            "users": [accounting_user_view(row) for row in rows],
        }

    @router.put("/accounting-module/permissions/users/{user_id}")
    async def update_accounting_permissions(
        user_id: str,
        payload: AccountingPermissionsUpdate,
        user: dict = Depends(current_user),
    ):
        fresh = await fresh_accounting_user(db, user)
        require_owner(fresh)
        unknown = sorted(set(payload.permissions) - ACCOUNTING_PERMISSION_KEYS)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "unknown_accounting_permissions",
                    "permissions": unknown,
                },
            )
        target = await db.users.find_one({
            "$and": [
                {"id": user_id},
                _team_scope(fresh["id"]),
            ],
        })
        if not target:
            raise HTTPException(status_code=404, detail="المستخدم غير موجود ضمن فريق المالك")
        if is_owner(target):
            raise HTTPException(
                status_code=409,
                detail="صلاحيات المالك المحاسبية كاملة ولا تُخفض",
            )

        selected = sorted(set(payload.permissions))
        now = datetime.now(timezone.utc).isoformat()
        await db.users.update_one(
            {"id": user_id, "created_by": fresh["id"]},
            {"$set": {
                "accounting_permissions": selected,
                "accounting_permissions_updated_at": now,
                "accounting_permissions_updated_by": fresh["id"],
            }},
        )
        updated = await db.users.find_one(
            {"id": user_id, "created_by": fresh["id"]},
            {"_id": 0},
        )
        return {
            "operation_id": OPERATION_ID,
            "user": accounting_user_view(updated or target),
        }

    return router
