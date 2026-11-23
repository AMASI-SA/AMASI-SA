"""Owner-only health reads; no source acquisition or financial mutation."""
from fastapi import Depends, Response
from pymongo.errors import PyMongoError
from starlette.requests import Request

from operational_balance_store import fail
from operational_visible_report_read import run_visible_report_read


def attach_freshness_routes(router, db, current_user, scope):
    @router.get("/freshness")
    async def freshness(request: Request, response: Response, user=Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        async def read_page():
            actor, owner, _ = await scope(db, user, "reports")
            if actor.get("role") != "owner" or actor.get("id") != owner:
                fail("operational_freshness_owner_required", "حالة التحديث الشاملة متاحة للمالك فقط", 403)
            from operational_balance_freshness import read_freshness
            try:
                result = await read_freshness(db, owner, actor["id"])
            except (TimeoutError, PyMongoError, ValueError, TypeError):
                fail("operational_freshness_unavailable", "تعذر التحقق من حداثة الأرصدة؛ تظهر آخر نتيجة محفوظة.", 503)
            latest, latest_owner, _ = await scope(db, user, "reports")
            if latest_owner != owner or latest.get("id") != actor["id"] or latest.get("role") != "owner":
                fail("operational_actor_scope_changed", "تغيرت جلسة الحساب؛ أعد فتح الصفحة", 403)
            return result
        return await run_visible_report_read(request, read_page)

    return router
