"""Owner-configured limits for a future Google automation worker.

ADR-001 1/3/7/8/10/11/14: additive and disabled by default, tenant scoped,
revision fenced, bounded audit history, and no provider mutations or secrets.
Saving this policy does not install a worker or grant a human identity to AI.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError

from .google_ads_client import GoogleAdsClient, identifier, problem
from .google_ads_management import account_client, available_accounts, principal

AUTOMATION_POLICIES = "mezan_google_ads_automation_policies_v1"
PUBLIC_FIELDS = ("schema_version", "account_id", "revision", "enabled", "currency_code", "time_zone",
    "campaign_ids", "allowed_actions", "max_campaign_daily_budget_native", "max_account_daily_budget_native",
    "max_budget_change_percent", "cooldown_seconds", "max_actions_per_day", "expires_at", "updated_by", "updated_at", "events")
AMOUNT_PATTERN = r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$"


def now():
    return datetime.now(timezone.utc)


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AutomationPolicyInput(StrictInput):
    expected_revision: int = Field(ge=0, le=2**63 - 2)
    enabled: bool = False
    campaign_ids: list[str] = Field(default_factory=list, max_length=50)
    allowed_actions: list[Literal["pause", "enable", "set_budget"]] = Field(default_factory=list, max_length=3)
    max_campaign_daily_budget_native: str = Field(pattern=AMOUNT_PATTERN)
    max_account_daily_budget_native: str = Field(pattern=AMOUNT_PATTERN)
    max_budget_change_percent: str = Field(pattern=AMOUNT_PATTERN)
    cooldown_seconds: int = Field(default=18000, ge=3600, le=604800)
    max_actions_per_day: int = Field(default=1, ge=1, le=20)
    expires_at: str | None = Field(default=None, max_length=64)

    @field_validator("campaign_ids")
    @classmethod
    def campaigns(cls, values):
        if len(set(values)) != len(values) or any(not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 30 for value in values):
            raise ValueError("campaign_ids must be unique native identifiers")
        return values

    @model_validator(mode="after")
    def limits(self):
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("allowed_actions must be unique")
        campaign, account, change = (Decimal(getattr(self, key)) for key in
            ("max_campaign_daily_budget_native", "max_account_daily_budget_native", "max_budget_change_percent"))
        if campaign <= 0 or account < campaign or not 0 < change <= 100:
            raise ValueError("positive bounded budgets and percentage are required")
        if self.enabled and (not self.campaign_ids or not self.allowed_actions or not self.expires_at):
            raise ValueError("enabled policy requires explicit campaigns, actions and expiry")
        return self


class AutomationPolicyRevoke(StrictInput):
    expected_revision: int = Field(ge=1, le=2**63 - 2)


def _expiry(value, current):
    if value is None:
        return None
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if expiry.tzinfo is None or not current < expiry <= current + timedelta(days=30):
            raise ValueError()
        return expiry.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        raise problem("google_automation_expiry_invalid", "اختر انتهاءً خلال الثلاثين يومًا القادمة مع المنطقة الزمنية.", 422) from None


def public_policy(row, *, account_id, current):
    result = {key: row[key] for key in PUBLIC_FIELDS if key in row} if row else {
        "schema_version": 1, "account_id": account_id, "revision": 0, "enabled": False,
        "campaign_ids": [], "allowed_actions": [], "expires_at": None,
    }
    expired = False
    if row and row.get("enabled"):
        try:
            expiry = datetime.fromisoformat(str(row.get("expires_at")).replace("Z", "+00:00"))
            expired = expiry.tzinfo is None or expiry <= current
        except (TypeError, ValueError):
            expired = True
    return {**result, "configured": bool(row), "expired": expired,
        "executor_available": False, "effective_enabled": False,
        "status": "executor_not_installed" if row and row.get("enabled") and not expired else "expired" if expired else "disabled",
        "provider_writes": 0, "budget_semantics": "average_daily_not_hard_spend_cap"}


async def _owner(db, user):
    # Re-read the authenticated identity rather than accepting a role supplied
    # by a caller or turning an unprivileged background worker into an owner.
    if not isinstance(user, dict) or not user.get("id"):
        raise problem("google_ads_authentication_required", "سجّل الدخول إلى ميزان.", 401)
    persisted = await db.users.find_one({"id": str(user["id"])}, {"_id": 0})
    actor = await principal(db, persisted, write=True)
    if not actor.get("owner") or actor.get("user_id") != actor.get("actor_id"):
        raise problem("google_ads_owner_required", "إعداد الإدارة الآلية متاح للمالك فقط.", 403)
    return actor


async def _account_scope(db, actor, account_id):
    account_id = identifier(account_id)
    if account_id not in {row["account_id"] for row in await available_accounts(db, actor["user_id"])}:
        raise problem("google_ads_account_not_owned", "حساب جوجل غير مرتبط بمتجرك.", 404)
    return {"user_id": actor["user_id"], "account_id": account_id}


async def _index(db):
    await db[AUTOMATION_POLICIES].create_index([("user_id", 1), ("account_id", 1)], unique=True, name="google_automation_tenant_account")


def _event(actor, revision, enabled, current):
    return {"revision": revision, "enabled": enabled, "actor_id": actor["actor_id"], "occurred_at": current.isoformat()}


def attach_google_ads_automation_policy_routes(router, db, current_user, *, client_factory=GoogleAdsClient):
    """Attach under the same integrations-v2 router as Google management.

    Every route authenticates a persisted owner. Provider access is limited to
    account_metadata on PUT; GET and revocation never send provider writes.
    """
    root = "/google_ads/automation/policy/{account_id}"

    @router.get(root)
    async def read_policy(account_id: str, user=Depends(current_user)):
        actor = await _owner(db, user)
        scope = await _account_scope(db, actor, account_id)
        row = await db[AUTOMATION_POLICIES].find_one(scope, {"_id": 0})
        return public_policy(row, account_id=scope["account_id"], current=now())

    @router.put(root)
    async def save_policy(account_id: str, body: AutomationPolicyInput, user=Depends(current_user)):
        actor = await _owner(db, user)
        scope = await _account_scope(db, actor, account_id)
        current = now()
        expires_at = _expiry(body.expires_at, current)
        client, _ = await account_client(db, actor["user_id"], scope["account_id"], client_factory=client_factory)
        try:
            metadata = await client.account_metadata()
        finally:
            await client.close()
        currency, zone = metadata.get("currency_code"), metadata.get("time_zone")
        try:
            if (metadata.get("identity_verified") is not True or metadata.get("account_id") != scope["account_id"]
                or not isinstance(currency, str) or len(currency) != 3 or not currency.isascii()
                or not currency.isupper() or not currency.isalpha() or not isinstance(zone, str)):
                raise ValueError()
            ZoneInfo(zone)
        except (ValueError, ZoneInfoNotFoundError):
            raise problem("google_automation_account_metadata_unproved", "عملة حساب جوجل أو توقيته غير مثبتة.", 409) from None
        await _index(db)
        revision = body.expected_revision + 1
        payload = body.model_dump(exclude={"expected_revision"})
        payload.update(schema_version=1, revision=revision, expires_at=expires_at,
            currency_code=currency, time_zone=zone, updated_by=actor["actor_id"], updated_at=current.isoformat())
        event = _event(actor, revision, body.enabled, current)
        if body.expected_revision == 0:
            try:
                await db[AUTOMATION_POLICIES].insert_one({**scope, **payload, "events": [event]})
            except DuplicateKeyError:
                raise problem("google_automation_policy_changed", "تغيرت سياسة جوجل؛ حدّثها قبل الحفظ.", 409) from None
        else:
            changed = await db[AUTOMATION_POLICIES].update_one({**scope, "revision": body.expected_revision},
                {"$set": payload, "$push": {"events": {"$each": [event], "$slice": -50}}})
            if not changed.matched_count:
                raise problem("google_automation_policy_changed", "تغيرت سياسة جوجل؛ حدّثها قبل الحفظ.", 409)
        row = await db[AUTOMATION_POLICIES].find_one(scope, {"_id": 0})
        return public_policy(row, account_id=scope["account_id"], current=now())

    @router.post(root + "/revoke")
    async def revoke_policy(account_id: str, body: AutomationPolicyRevoke, user=Depends(current_user)):
        actor = await _owner(db, user)
        # Revocation must remain available after OAuth/account disconnection.
        scope = {"user_id": actor["user_id"], "account_id": identifier(account_id)}
        current, revision = now(), body.expected_revision + 1
        changed = await db[AUTOMATION_POLICIES].update_one({**scope, "revision": body.expected_revision},
            {"$set": {"enabled": False, "revision": revision, "updated_by": actor["actor_id"], "updated_at": current.isoformat()},
             "$push": {"events": {"$each": [_event(actor, revision, False, current)], "$slice": -50}}})
        if not changed.matched_count:
            raise problem("google_automation_policy_changed", "تغيرت سياسة جوجل؛ حدّثها قبل إيقافها.", 409)
        row = await db[AUTOMATION_POLICIES].find_one(scope, {"_id": 0})
        return public_policy(row, account_id=scope["account_id"], current=now())
