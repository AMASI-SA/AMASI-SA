"""Unattached owner-only saved-plan campaign context review route.

The root coordinator owns the small shared-router attachment after review.
"""
import asyncio
from datetime import datetime, timezone

from fastapi import Depends, Query

from .google_ads_client import problem
from .google_ads_management import principal
from .google_commercial_plan import PLANS, CommercialPlanFields
from .google_linked_campaign_reader import _bounded, linked_campaign_evidence


def attach_google_linked_campaign_routes(router, db, current_user, *, clock=None):
    async def owner(user=Depends(current_user)):
        actor = await principal(db, user)
        if not actor["owner"]:
            raise problem("google_ads_owner_required", "مراجعة أدلة الحملات متاحة للمالك فقط.", 403)
        return actor

    @router.get("/google_ads/commercial-plans/{plan_id}/linked-campaign-evidence")
    async def review(plan_id: str, expected_revision: int = Query(ge=1), actor=Depends(owner)):
        if not 1 <= len(plan_id) <= 128 or not all(c.isascii() and (c.isalnum() or c in "_-") for c in plan_id):
            raise problem("google_commercial_plan_id_invalid", "معرف الخطة غير صالح.", 422)
        scope = {"user_id": actor["user_id"], "plan_id": plan_id}
        try:
            async with asyncio.timeout(8):
                rows = await _bounded(db[PLANS], scope, 1, bytes_limit=131072)
                if not rows:
                    raise problem("google_commercial_plan_not_found", "الخطة غير موجودة.", 404)
                plan = rows[0]
                if type(plan.get("revision")) is not int or plan["revision"] != expected_revision:
                    raise problem("google_commercial_plan_revision_conflict", "تغيرت الخطة؛ أعد قراءتها.", 409)
                fields = CommercialPlanFields.model_validate(plan["fields"])
                excluded = set(fields.excluded_product_ids)
                selected = [p for p in fields.pinned_product_ids if p not in excluded]
                result = await linked_campaign_evidence(db, owner_id=actor["user_id"], product_ids=selected,
                    as_of=clock() if clock else datetime.now(timezone.utc))
                latest = await _bounded(db[PLANS], scope, 1, bytes_limit=131072)
                if not latest or latest[0].get("revision") != expected_revision or latest[0].get("fields") != plan["fields"]:
                    raise problem("google_commercial_plan_revision_conflict", "تغيرت الخطة أثناء قراءة الأدلة؛ أعد قراءتها.", 409)
                return {"plan_id": plan_id, "plan_revision": expected_revision, "status": "bounded_campaign_context_review",
                        "snapshot_immutable": False, "catalog_identity_verified": False, **result}
        except (TimeoutError, ValueError, TypeError, KeyError):
            raise problem("google_linked_campaign_evidence_unavailable", "تعذر إثبات نطاق الأدلة المحفوظة؛ لا توجد نتيجة مكتملة.", 503) from None
