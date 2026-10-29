"""Standalone owner-triggered preview; shared-router attachment belongs to root."""
import asyncio

from fastapi import Depends, Query
from salla_integration.service import call_salla

from .google_ads_client import problem
from .google_ads_management import principal
from .google_commercial_plan import PLANS, CommercialPlanInput
from .google_live_category_reader import preview_live_category, CategoryProofUnavailable


def attach_google_live_category_routes(router, db, current_user, *, caller=call_salla):
    async def owner(user=Depends(current_user)):
        actor = await principal(db, user)
        if not actor["owner"]:
            raise problem("google_ads_owner_required", "معاينة القسم متاحة للمالك فقط.", 403)
        return actor

    @router.get("/google_ads/commercial-plans/{plan_id}/live-category-preview")
    async def preview(plan_id: str, expected_revision: int = Query(ge=1),
                      category_id: str | None = Query(default=None, max_length=30), actor=Depends(owner)):
        if not 1 <= len(plan_id) <= 128 or not all(c.isascii() and (c.isalnum() or c in "_-") for c in plan_id):
            raise problem("google_commercial_plan_id_invalid", "معرف الخطة غير صالح.", 422)
        scope = {"user_id": actor["user_id"], "plan_id": plan_id}
        projection = {"_id": 0, "revision": 1, "fields": 1}
        try:
            async with asyncio.timeout(45):
                plan = await db[PLANS].find_one(scope, projection)
                if not plan:
                    raise problem("google_commercial_plan_not_found", "الخطة غير موجودة.", 404)
                if type(plan.get("revision")) is not int or plan["revision"] != expected_revision:
                    raise problem("google_commercial_plan_revision_conflict", "تغيرت الخطة؛ أعد قراءتها.", 409)
                fields = CommercialPlanInput.model_validate({**plan["fields"], "expected_revision": expected_revision})
                result = await preview_live_category(db, owner_id=actor["user_id"], fields=fields.model_dump(),
                                                    category_id=category_id, caller=caller)
                latest = await db[PLANS].find_one(scope, projection)
                if not latest or latest.get("revision") != expected_revision or latest.get("fields") != plan["fields"]:
                    raise problem("google_commercial_plan_revision_conflict", "تغيرت الخطة أثناء المعاينة؛ أعد قراءتها.", 409)
                return {"plan_id": plan_id, "plan_revision": expected_revision, "status": "live_membership_observation_only", **result}
        except CategoryProofUnavailable as exc:
            raise problem("google_live_category_unavailable", "تعذر إثبات معاينة القسم لهذا الطلب.", 409, reason=str(exc)) from None
        except (TimeoutError, ValueError, TypeError, KeyError):
            raise problem("google_live_category_unavailable", "معاينة القسم غير مكتملة؛ لا توجد صلاحية اختيار أو تشغيل.", 503, reason="bounded_source_unproved") from None
