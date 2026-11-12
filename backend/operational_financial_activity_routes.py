"""HTTP attachment for the permission-scoped daily financial activity reader."""
from typing import Literal

from fastapi import Depends, Query, Request

from operational_balance_store import fail
from operational_financial_activity import (
    daily_financial_movements,
    require_browser_activity_permission,
)
from operational_financial_activity_runtime import run_visible_daily_read


def attach_financial_activity_routes(router, db, current_user, scope):
    async def authorized(principal):
        actor, owner, source = await scope(db, principal, "reports")
        if actor.get("role") == "store_driver":
            fail("financial_movements_permission_required", "لا توجد صلاحية لعرض الحركات المالية", 403)
        if source != "employee_app":
            require_browser_activity_permission(actor, "accounting.movements.view")
        return actor, owner, source

    @router.get("/daily-financial-movements")
    async def daily_activity(
        request: Request,
        from_date: str,
        to_date: str,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=10, ge=10, le=10),
        snapshot: str | None = Query(default=None, min_length=1, max_length=128),
        account_type: Literal["bank", "cash"] | None = None,
        account_id: str | None = Query(default=None, min_length=1, max_length=200),
        user=Depends(current_user),
    ):
        async def read_page():
            actor, owner, source = await authorized(user)
            result = await daily_financial_movements(
                db, owner=owner, actor=actor, from_date=from_date, to_date=to_date,
                page=page, page_size=page_size, snapshot=snapshot,
                account_type=account_type, account_id=account_id,
            )
            fresh_actor, fresh_owner, fresh_source = await authorized(user)
            if (fresh_actor["id"], fresh_owner, fresh_source) != (actor["id"], owner, source):
                fail("financial_movements_scope_changed", "تغير ارتباط الحساب؛ أعد تسجيل الدخول", 403)
            return result
        return await run_visible_daily_read(request, read_page)

    return router
