"""Owner-only preview; approval enters the existing visible refresh endpoint."""
from typing import Literal

from fastapi import Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.requests import Request

from operational_balance_store import digest, fail
from operational_visible_report_read import run_visible_report_read
from operational_ad_corrections import validate_selection


class AdCorrectionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    account_id: str = Field(min_length=1, max_length=200)
    date_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def bounded_period(self):
        validate_selection(self.model_dump())
        return self


class AdCorrectionApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    selection: AdCorrectionSelection
    days: list[str] = Field(min_length=1, max_length=31)
    expected_revision: int = Field(ge=1, strict=True)
    expected_preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_session_scope: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    reason: str = Field(min_length=5, max_length=500)
    confirm_reviewed: Literal[True]

    @field_validator("confirm_reviewed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("explicit owner review required")
        return value

    @model_validator(mode="after")
    def unique_selected_days(self):
        days = validate_selection(self.selection.model_dump())
        if len(set(self.days)) != len(self.days) or not set(self.days) <= set(days):
            raise ValueError("selected days must be unique and inside the preview")
        return self


def require_correction_owner(actor, owner):
    if actor.get("id") != owner or actor.get("role") != "owner":
        fail("ad_correction_owner_required", "اعتماد تصحيح الإعلان متاح للمالك الحالي فقط", 403)


def attach_ad_correction_routes(router, db, current_user, scope):
    @router.get("/ad-spend-corrections/preview")
    async def correction_preview(request: Request, response: Response, account_id: str = Query(min_length=1, max_length=200),
            date_from: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
            date_to: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
            expected_session_scope: str = Query(pattern=r"^[a-f0-9]{64}$"), user=Depends(current_user)):
        response.headers["Cache-Control"] = "private, no-store"
        async def read_page():
            from operational_ad_corrections import preview
            actor, owner, _ = await scope(db, user, "reports")
            require_correction_owner(actor, owner)
            session_scope = digest([owner, actor["id"]])
            if expected_session_scope != session_scope:
                fail("operational_actor_scope_changed", "تغير الحساب؛ أعد فتح صفحة التصحيح", 403)
            selection = {"account_id": account_id, "date_from": date_from, "date_to": date_to}
            try:
                validate_selection(selection)
            except (ValueError, TypeError):
                fail("ad_correction_period_invalid", "اختر فترة صحيحة لا تتجاوز ٣١ يومًا", 422)
            result = await preview(db, owner, selection)
            fresh_actor, fresh_owner, _ = await scope(db, user, "reports")
            require_correction_owner(fresh_actor, fresh_owner)
            if (fresh_owner, fresh_actor["id"]) != (owner, actor["id"]):
                fail("operational_actor_scope_changed", "تغير الحساب؛ أعد فتح صفحة التصحيح", 403)
            return {**result, "session_scope": session_scope}
        return await run_visible_report_read(request, read_page)

    @router.get("/ad-spend-corrections/receipts/{request_id}")
    async def correction_receipt(request: Request, response: Response, request_id: str,
            expected_preview_hash: str = Query(pattern=r"^[a-f0-9]{64}$"),
            expected_session_scope: str = Query(pattern=r"^[a-f0-9]{64}$"), user=Depends(current_user)):
        response.headers["Cache-Control"] = "private, no-store"
        async def read_page():
            import re
            from operational_ad_corrections import saved_approval
            if re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id) is None:
                fail("ad_correction_request_invalid", "مرجع الاعتماد غير صالح", 422)
            actor, owner, _ = await scope(db, user, "reports")
            require_correction_owner(actor, owner)
            bound = digest([owner, actor["id"]])
            if bound != expected_session_scope:
                fail("operational_actor_scope_changed", "تغير الحساب؛ أعد فتح صفحة التصحيح", 403)
            value = await saved_approval(db, owner, actor["id"], request_id, expected_preview_hash)
            current_actor, current_owner, _ = await scope(db, user, "reports")
            require_correction_owner(current_actor, current_owner)
            if (current_owner, current_actor["id"]) != (owner, actor["id"]):
                fail("operational_actor_scope_changed", "تغير الحساب؛ أعد فتح صفحة التصحيح", 403)
            return {**value, "session_scope": bound}
        return await run_visible_report_read(request, read_page)
