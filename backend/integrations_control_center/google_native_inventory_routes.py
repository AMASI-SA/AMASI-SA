"""Standalone explicit owner request, never a product selector or ad gateway."""
import asyncio
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from salla_integration.service import call_salla
from .google_ads_client import problem
from .google_ads_management import principal
from .google_commercial_plan import PLANS, CommercialPlanInput
from .google_live_category_reader import CategoryProofUnavailable
from .google_native_inventory_reader import selected_inventory_preview


class NativeInventoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    category_id: str = Field(pattern=r"^[1-9][0-9]{0,29}$")
    selected_product_ids: list[str] = Field(min_length=1, max_length=5)


def attach_google_native_inventory_routes(router, db, current_user, *, caller=call_salla):
    async def owner(user=Depends(current_user)):
        actor = await principal(db, user)
        if not actor["owner"]:
            raise problem("google_ads_owner_required", "معاينة المخزون متاحة للمالك فقط.", 403)
        return actor

    @router.post("/google_ads/commercial-plans/{plan_id}/native-inventory-preview")
    async def preview(plan_id: str, body: NativeInventoryRequest, actor=Depends(owner)):
        if not 1 <= len(plan_id) <= 128 or not all(c.isascii() and (c.isalnum() or c in "_-") for c in plan_id):
            raise problem("google_commercial_plan_id_invalid", "معرف الخطة غير صالح.", 422)
        scope = {"user_id": actor["user_id"], "plan_id": plan_id}
        projection = {"_id": 0, "revision": 1, "fields": 1}
        try:
            async with asyncio.timeout(45):
                plan = await db[PLANS].find_one(scope, projection)
                if not plan:
                    raise problem("google_commercial_plan_not_found", "الخطة غير موجودة.", 404)
                if type(plan.get("revision")) is not int or plan["revision"] != body.expected_revision:
                    raise problem("google_commercial_plan_revision_conflict", "تغيرت الخطة؛ أعد قراءتها.", 409)
                fields = CommercialPlanInput.model_validate({**plan["fields"], "expected_revision": body.expected_revision})
                result = await selected_inventory_preview(db, owner_id=actor["user_id"], fields=fields.model_dump(),
                    category_id=body.category_id, selected_ids=body.selected_product_ids, caller=caller)
                latest = await db[PLANS].find_one(scope, projection)
                if not latest or latest.get("revision") != body.expected_revision or latest.get("fields") != plan["fields"]:
                    raise problem("google_commercial_plan_revision_conflict", "تغيرت الخطة أثناء قراءة المخزون.", 409)
                return {"plan_id": plan_id, "plan_revision": body.expected_revision, "status": "native_stock_observations_only", **result}
        except CategoryProofUnavailable as exc:
            raise problem("google_native_inventory_unavailable", "تعذر إثبات نطاق معاينة المخزون.", 409, reason=str(exc)) from None
        except (TimeoutError, ValueError, TypeError, KeyError):
            raise problem("google_native_inventory_unavailable", "معاينة المخزون غير مكتملة؛ لا توجد صلاحية تشغيل.", 503, reason="bounded_source_unproved") from None
