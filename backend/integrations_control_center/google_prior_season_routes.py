"""Standalone explicit owner review. Root owns eventual shared attachment."""
import asyncio
from datetime import date, datetime, timezone
import re
from zoneinfo import ZoneInfo

from bson import BSON
from auth import account_is_disabled
from fastapi import Depends, Query

from .google_ads_client import problem
from .google_ads_management import principal
from .google_commercial_plan import PLANS, CommercialPlanFields
from .google_prior_season_reader import read_prior_season_observations


def attach_google_prior_season_routes(router, db, current_user, *, clock=None):
    async def owner(user=Depends(current_user)):
        actor = await principal(db, user)
        if not actor["owner"]:
            raise problem("google_ads_owner_required", "مراجعة الموسم السابق متاحة للمالك فقط.", 403)
        return actor

    async def fresh_owner(actor):
        rows = await db.users.find({"id": actor["user_id"]}, {
            "_id": 0, "id": 1, "role": 1, "disabled": 1, "is_active": 1,
            "deleted_at": 1, "review_access_expires_at": 1,
        }).limit(2).max_time_ms(1000).to_list(length=2)
        if (len(rows) != 1 or rows[0].get("id") != actor["user_id"]
                or rows[0].get("role") != "owner" or account_is_disabled(rows[0])):
            raise problem("google_ads_owner_required", "تعذر إثبات صلاحية المالك الحالية.", 403)

    @router.get("/google_ads/commercial-plans/{plan_id}/prior-season-observations")
    async def review(plan_id: str, expected_revision: int = Query(ge=1), actor=Depends(owner)):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", plan_id):
            raise problem("google_commercial_plan_id_invalid", "معرف الخطة غير صالح.", 422)
        scope = {"user_id": actor["user_id"], "plan_id": plan_id}
        projection = {"_id": 0, "revision": 1, "fields": 1, "updated_by": 1, "updated_at": 1}
        try:
            async with asyncio.timeout(6):
                await fresh_owner(actor)
                plan = await db[PLANS].find_one(scope, projection)
                if not plan:
                    raise problem("google_commercial_plan_not_found", "الخطة غير موجودة.", 404)
                if type(plan.get("revision")) is not int or plan["revision"] != expected_revision:
                    raise problem("google_commercial_plan_revision_conflict", "تغيرت الخطة؛ أعد قراءتها.", 409)
                snapshot = BSON.encode(plan)
                fields = CommercialPlanFields.model_validate(plan["fields"])
                prior = fields.prior_season_context
                as_of = clock() if clock else datetime.now(timezone.utc)
                result = {"plan_id": plan_id, "plan_revision": expected_revision,
                    "status": "prior_season_not_configured" if prior is None else "owner_declared_context_only",
                    "prior_season_context": prior.model_dump() if prior else None,
                    "calendar_alignment_verification": "unknown", "owner_review_provenance": None,
                    "product_ids_reviewed": [], "observations": None,
                    "growth": None, "profitability": None, "provider_writes": 0, "source_writes": 0, "execution_authorized": False}
                if prior is not None:
                    if date.fromisoformat(prior.date_to) >= as_of.astimezone(ZoneInfo("Asia/Riyadh")).date():
                        raise problem("google_prior_season_period_not_closed", "الفترة المحفوظة لم تُغلق بعد؛ لا توجد قراءة مكتملة لها.", 409)
                    updated = plan.get("updated_at")
                    stamp = datetime.fromisoformat(updated.replace("Z", "+00:00")) if isinstance(updated, str) else None
                    if (plan.get("updated_by") != actor["user_id"] or stamp is None or stamp.tzinfo is None
                            or stamp.utcoffset() is None or stamp > as_of):
                        raise problem("google_prior_season_provenance_unproved", "تعذر إثبات سجل مراجعة سياق الموسم المحفوظ.", 409)
                    ids = [p for p in fields.pinned_product_ids if p not in fields.excluded_product_ids]
                    observed = await read_prior_season_observations(db, owner_id=actor["user_id"], product_ids=ids,
                        as_of=as_of, date_from=prior.date_from, date_to=prior.date_to)
                    result.update(product_ids_reviewed=ids, observations=observed, owner_review_provenance={
                        "reviewed_by": plan["updated_by"], "reviewed_at": updated, "plan_revision": expected_revision,
                        "meaning": "owner_declared_context_not_independent_calendar_verification"})
                latest = await db[PLANS].find_one(scope, projection)
                if (not latest or type(latest.get("revision")) is not int
                        or latest["revision"] != expected_revision or BSON.encode(latest) != snapshot):
                    raise problem("google_commercial_plan_revision_conflict", "تغيرت الخطة أثناء قراءة الموسم السابق.", 409)
                await fresh_owner(actor)
                return result
        except (ValueError, TypeError, KeyError, TimeoutError):
            raise problem("google_prior_season_observations_unavailable", "تعذر إثبات قراءة محدودة للموسم السابق.", 503) from None
        except Exception as exc:
            from fastapi import HTTPException
            if isinstance(exc, HTTPException):
                raise
            raise problem("google_prior_season_observations_unavailable", "تعذر إثبات قراءة محدودة للموسم السابق.", 503) from None
