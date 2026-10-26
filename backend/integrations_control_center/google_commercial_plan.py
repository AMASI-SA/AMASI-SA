"""Owner-reviewed commercial context; never an advertising authorization.

The original instruction and explicit fields are saved together. Category URLs
and product suggestions are claims to verify, not catalog membership or profit
proof. This bounded vertical does not extract arbitrary prose or rank products.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError

from .google_ads_client import problem
from .google_ads_management import principal

PLANS = "mezan_google_commercial_plans_v1"


class CommercialLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    currency_code: str = Field(pattern=r"^[A-Z]{3}$")
    daily_budget_native: str | None = Field(default=None, pattern=r"^[0-9]{1,12}(\.[0-9]{1,6})?$")
    total_budget_native: str | None = Field(default=None, pattern=r"^[0-9]{1,12}(\.[0-9]{1,6})?$")
    minimum_margin_percent: str | None = Field(default=None, pattern=r"^[0-9]{1,3}(\.[0-9]{1,6})?$")
    target_roas: str | None = Field(default=None, pattern=r"^[0-9]{1,6}(\.[0-9]{1,6})?$")

    @model_validator(mode="after")
    def ranges(self):
        for key in ("daily_budget_native", "total_budget_native", "target_roas"):
            value = getattr(self, key)
            if value is not None and Decimal(value) <= 0:
                raise ValueError(f"{key} must be positive when supplied")
        if self.minimum_margin_percent is not None and Decimal(self.minimum_margin_percent) > 100:
            raise ValueError("margin must be at most 100 percent")
        return self


class CommercialPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    instruction: str = Field(min_length=1, max_length=6000)
    season: str = Field(min_length=1, max_length=160)
    starts_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    ends_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    market: str = Field(pattern=r"^[A-Z]{2}$")
    audience: str = Field(min_length=1, max_length=1000)
    message: str = Field(min_length=1, max_length=1000)
    category_url: str | None = Field(default=None, max_length=2000)
    category_ids: list[str] = Field(default_factory=list, max_length=40)
    pinned_product_ids: list[str] = Field(default_factory=list, max_length=100)
    excluded_product_ids: list[str] = Field(default_factory=list, max_length=100)
    exclusions: list[str] = Field(default_factory=list, max_length=40)
    selection_mode: Literal["pinned_only", "review_category_candidates"] = "pinned_only"
    calendar: Literal["gregorian", "hijri_season"] = "gregorian"
    limits: CommercialLimits | None = None
    acknowledge_review: bool

    @field_validator("instruction", "season", "audience", "message")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("blank text")
        return value.strip()

    @field_validator("category_ids", "pinned_product_ids", "excluded_product_ids", "exclusions")
    @classmethod
    def identifiers(cls, values):
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 240 for value in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("empty, duplicate or oversized selection")
        return cleaned

    @field_validator("category_url")
    @classmethod
    def public_url(cls, value):
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("category must be an HTTPS URL without credentials or fragment")
        return value

    @model_validator(mode="after")
    def reviewed(self):
        start, end = date.fromisoformat(self.starts_on), date.fromisoformat(self.ends_on)
        if start > end or (end - start).days > 730:
            raise ValueError("invalid season interval")
        if not self.acknowledge_review:
            raise ValueError("review the structured fields before saving")
        if set(self.pinned_product_ids) & set(self.excluded_product_ids):
            raise ValueError("a product cannot be pinned and excluded")
        if self.selection_mode == "review_category_candidates" and not self.category_url and not self.category_ids:
            raise ValueError("category selection requires a category")
        return self


def review_context(body: CommercialPlanInput, current: datetime) -> dict:
    """Complete closed-day comparison windows; no missing data becomes zero."""
    last = current.astimezone(ZoneInfo("Asia/Riyadh")).date() - timedelta(days=1)
    def window(start, end):
        return {"from": start.isoformat(), "to": end.isoformat(), "timezone": "Asia/Riyadh", "status": "not_collected"}
    return {
        "sales_last_14_days": window(last - timedelta(days=13), last),
        "sales_previous_14_days": window(last - timedelta(days=27), last - timedelta(days=14)),
        "trend_last_30_days": window(last - timedelta(days=29), last),
        "trend_last_90_days": window(last - timedelta(days=89), last),
        "prior_season": {"status": "alignment_required" if body.calendar == "hijri_season" else "not_collected", "calendar": body.calendar},
        "cost_quality": "unknown", "profitability": None,
        "inventory": "not_verified", "category_membership": "not_verified",
        "merchant_offer_mapping": "not_verified", "cross_platform_results": [],
        "attribution_rule": "Keep provider/account/currency/period separate; campaign-product associations are not order attribution; never sum provider conversions as store orders.",
        "cross_platform_success_rule": "Candidate for a Google test only; no guarantee of Google success.",
        "new_products": "May be proposed without sales history; execution requires separately approved test budget and eligibility proofs.",
    }


def public_plan(row):
    return {key: row[key] for key in ("plan_id", "revision", "fields", "review_context", "created_at", "updated_at", "updated_by") if key in row} | {
        "provider_writes": 0, "execution_authorized": False,
        "status": "reviewed_context_only", "natural_language_extraction_available": False,
    }


async def save_commercial_plan(db, actor, plan_id, body: CommercialPlanInput, *, current=None):
    current = current or datetime.now(timezone.utc)
    collection = db[PLANS]
    await collection.create_index([("user_id", 1), ("plan_id", 1)], unique=True, name="google_commercial_plan_owner_id")
    scope = {"user_id": actor["user_id"], "plan_id": plan_id}
    prior = await collection.find_one(scope, {"_id": 0})
    if (prior or {}).get("revision", 0) != body.expected_revision:
        raise problem("google_commercial_plan_revision_conflict", "تغيرت الخطة؛ أعد قراءتها قبل الحفظ.", 409)
    fields = body.model_dump(exclude={"expected_revision", "acknowledge_review"})
    stamp = current.isoformat()
    row = {**scope, "revision": body.expected_revision + 1, "fields": fields, "review_context": review_context(body, current),
           "created_at": prior["created_at"] if prior else stamp, "updated_at": stamp, "updated_by": actor["actor_id"]}
    audit = {"revision": row["revision"], "fields": fields, "updated_at": stamp, "updated_by": actor["actor_id"]}
    if prior:
        result = await collection.update_one({**scope, "revision": body.expected_revision}, {"$set": row, "$push": {"reviews": {"$each": [audit], "$slice": -50}}})
        if result.matched_count != 1:
            raise problem("google_commercial_plan_revision_conflict", "تغيرت الخطة؛ أعد قراءتها قبل الحفظ.", 409)
    else:
        try:
            await collection.insert_one({**row, "reviews": [audit]})
        except DuplicateKeyError:
            raise problem("google_commercial_plan_revision_conflict", "تغيرت الخطة؛ أعد قراءتها قبل الحفظ.", 409) from None
    return public_plan(row)


def attach_google_commercial_plan_routes(router, db, current_user):
    async def owner(user=Depends(current_user)):
        actor = await principal(db, user, write=True)
        if not actor["owner"]:
            raise problem("google_ads_owner_required", "الخطة التجارية متاحة للمالك فقط.", 403)
        return actor

    @router.get("/google_ads/commercial-plans/{plan_id}")
    async def read_plan(plan_id: str, actor=Depends(owner)):
        row = await db[PLANS].find_one({"user_id": actor["user_id"], "plan_id": plan_id}, {"_id": 0})
        if not row:
            raise problem("google_commercial_plan_not_found", "الخطة غير موجودة.", 404)
        return public_plan(row)

    @router.put("/google_ads/commercial-plans/{plan_id}")
    async def write_plan(plan_id: str, body: CommercialPlanInput, actor=Depends(owner)):
        if not 1 <= len(plan_id) <= 128 or not all(char.isascii() and (char.isalnum() or char in "_-") for char in plan_id):
            raise problem("google_commercial_plan_id_invalid", "معرف الخطة غير صالح.", 422)
        return await save_commercial_plan(db, actor, plan_id, body)
