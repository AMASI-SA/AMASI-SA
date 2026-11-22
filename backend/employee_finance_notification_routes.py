"""Self-only employee financial notices, independent from reporting grants."""
from fastapi import APIRouter, Depends, Query, Response

from employee_finance_notifications import (
    financial_notification_detail, financial_notification_list, mark_financial_notification_read,
)


def make_employee_finance_notification_router(db, current_user):
    router = APIRouter(prefix="/employee-finance", tags=["employee-finance"])

    @router.get("/notifications/me")
    async def notifications(response: Response, limit: int = Query(default=10, ge=1, le=10),
                            cursor: str | None = Query(default=None, max_length=400), user=Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        return await financial_notification_list(db, user, limit=limit, cursor=cursor)

    @router.get("/notifications/{identity}")
    async def detail(identity: str, response: Response, user=Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        return await financial_notification_detail(db, user, identity)

    @router.post("/notifications/{identity}/read")
    async def read(identity: str, response: Response, user=Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        return await mark_financial_notification_read(db, user, identity)

    return router
