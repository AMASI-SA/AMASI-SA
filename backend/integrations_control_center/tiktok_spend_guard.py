"""Opt-in owner spend monitoring in the existing Campaign AI cycle.

ADR-001: additive/flagged (1,3), native account/report authority (9), immutable
policy versions and one-shot audit claims (8,10,11,14). Only defensive PAUSE
is authorized. No model output, profit estimate or conversion count can
authorize a budget increase, campaign activation or content publication.
The five-hour polling trigger is not an instantaneous spend guarantee.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import secrets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo.errors import DuplicateKeyError

from .tiktok_campaign_management import (_account, _digest, _money, TikTokCampaignProvider,
    TikTokCampaignProposalInput, preview_tiktok_campaign, execute_tiktok_campaign)
from .tiktok_creator_api import BoundedCreatorJSONRoute, TikTokCreatorAPI, mongo, problem
from .tiktok_features import enabled as feature_enabled

POLICIES = "mezan_tiktok_spend_guard_policies_v1"
VERSIONS = "mezan_tiktok_spend_guard_policy_versions_v1"
CYCLES = "mezan_tiktok_spend_guard_cycles_v1"
ACTIONS = "mezan_tiktok_spend_guard_actions_v1"
CURSORS = "mezan_tiktok_spend_guard_cursors_v1"
MAX_POLICIES = 20
MAX_CHECKS = 3
_slot = asyncio.Semaphore(1)


def now():
    return datetime.now(timezone.utc)


class SpendRule(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    campaign_id: str = Field(pattern=r"^[0-9]{1,30}$")
    pause_at_daily_spend_native: float = Field(strict=True, gt=0, le=10_000_000)


class SpendPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(pattern=r"^[0-9]{1,30}$")
    expected_revision: int = Field(strict=True, ge=0)
    enabled: bool = Field(strict=True)
    rules: list[SpendRule] = Field(default_factory=list, max_length=3)
    polling_and_report_delay_acknowledged: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def exact_authorization(self):
        if len({rule.campaign_id for rule in self.rules}) != len(self.rules):
            raise ValueError("duplicate campaign rule")
        if self.enabled and (not self.rules or not self.polling_and_report_delay_acknowledged):
            raise ValueError("explicit rules and delayed-polling acknowledgement required")
        for rule in self.rules:
            _money(rule.pause_at_daily_spend_native)
        return self


async def indexes(db):
    await mongo(db[POLICIES].create_index([("user_id", 1), ("account_id", 1)], unique=True, name="tiktok_spend_policy_owner", maxTimeMS=1500))
    await mongo(db[CYCLES].create_index([("user_id", 1), ("snapshot_id", 1)], unique=True, name="tiktok_spend_cycle_once", maxTimeMS=1500))
    await mongo(db[ACTIONS].create_index([("user_id", 1), ("snapshot_id", 1), ("account_id", 1), ("campaign_id", 1)], unique=True, name="tiktok_spend_action_once", maxTimeMS=1500))
    await mongo(db[ACTIONS].create_index([("user_id", 1), ("created_at", -1)], name="tiktok_spend_action_history", maxTimeMS=1500))


async def metadata(api, token, account):
    data = await api.call("GET", "/advertiser/info/", token=token, params={"advertiser_ids": json.dumps([account]), "fields": json.dumps(["advertiser_id", "currency", "timezone"])})
    rows = data.get("list")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or str(rows[0].get("advertiser_id")) != account:
        raise problem("tiktok_guard_account_unproven", "الحساب الإعلاني غير مثبت.")
    currency, zone = rows[0].get("currency"), rows[0].get("timezone")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha() or not currency.isupper():
        raise problem("tiktok_guard_currency_unproven", "عملة الحساب غير مثبتة.")
    try:
        ZoneInfo(zone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        raise problem("tiktok_guard_timezone_unproven", "توقيت يوم الصرف للحساب غير مثبت.") from None
    return {"currency": currency, "timezone": zone}


async def current_policy(db, user, account):
    pointer = await mongo(db[POLICIES].find_one({"user_id": user, "account_id": account}, max_time_ms=1500))
    if not pointer:
        return {"account_id": account, "revision": 0, "enabled": False, "rules": [], "currency": None, "timezone": None, "allowed_actions": ["pause"], "interval_hours": 5}
    version = await mongo(db[VERSIONS].find_one({"_id": pointer["version_id"], "user_id": user, "account_id": account, "revision": pointer["revision"]}, max_time_ms=1500))
    if not version or version["enabled"] is not pointer["enabled"]:
        raise problem("tiktok_guard_policy_unproven", "نسخة سياسة الحماية غير مثبتة.")
    return {key: version.get(key) for key in ("account_id", "revision", "enabled", "rules", "currency", "timezone", "updated_at", "allowed_actions", "interval_hours")}


async def save_policy(db, user, value, *, api_factory=TikTokCreatorAPI, provider_factory=TikTokCampaignProvider):
    if value.enabled and not feature_enabled("spend_guard"):
        raise problem("tiktok_guard_disabled", "حماية الصرف الآلية غير مفعّلة.", 503)
    async with asyncio.timeout(50):
        await indexes(db)
        old = await current_policy(db, user, value.account_id)
        if old["revision"] != value.expected_revision:
            raise problem("tiktok_guard_policy_changed", "تغيرت سياسة الحماية؛ أعد قراءتها قبل الحفظ.")
        meta = {key: old.get(key) for key in ("currency", "timezone")}
        if value.enabled:
            _, token, _ = await _account(db, user, value.account_id)
            async with api_factory() as api:
                meta = await metadata(api, token, value.account_id)
            provider = provider_factory(token)
            try:
                for rule in value.rules:
                    campaign = await provider.read(value.account_id, rule.campaign_id)
                    if campaign.get("operation_status") not in {"ENABLE", "DISABLE"}:
                        raise problem("tiktok_guard_campaign_unproven", "إحدى حملات الحماية غير متاحة في الحساب.")
            finally:
                await provider.close()
        version = {"_id": secrets.token_hex(16), "user_id": user, "account_id": value.account_id,
            "revision": old["revision"] + 1, "enabled": value.enabled, "rules": [rule.model_dump() for rule in value.rules],
            **meta, "updated_at": now(), "allowed_actions": ["pause"], "interval_hours": 5,
            "polling_and_report_delay_acknowledged": value.polling_and_report_delay_acknowledged}
        # Persist immutable authority before activating its pointer. Orphaned
        # versions from a lost CAS grant no permission and never execute.
        await mongo(db[VERSIONS].insert_one(version))
        pointer = {"user_id": user, "account_id": value.account_id, "revision": version["revision"], "version_id": version["_id"], "enabled": value.enabled, "updated_at": now()}
        if old["revision"] == 0:
            try:
                await mongo(db[POLICIES].insert_one(pointer))
            except DuplicateKeyError:
                raise problem("tiktok_guard_policy_changed", "سبق حفظ سياسة لهذا الحساب؛ أعد قراءتها.") from None
        else:
            changed = await mongo(db[POLICIES].update_one({"user_id": user, "account_id": value.account_id, "revision": old["revision"]}, {"$set": pointer}))
            if changed.modified_count != 1:
                raise problem("tiktok_guard_policy_changed", "تغيرت السياسة أثناء الحفظ؛ أعد قراءتها.")
        return await current_policy(db, user, value.account_id)


async def fresh_spend(api, token, account, campaign, native_day):
    data = await api.call("GET", "/report/integrated/get/", token=token, params={"advertiser_id": account, "report_type": "BASIC", "data_level": "AUCTION_CAMPAIGN",
        "dimensions": json.dumps(["campaign_id", "stat_time_day"]), "metrics": json.dumps(["spend"]),
        "filtering": json.dumps([{"field_name": "campaign_ids", "filter_type": "IN", "filter_value": json.dumps([campaign])}]),
        "start_date": native_day, "end_date": native_day, "page": 1, "page_size": 1})
    rows, page = data.get("list"), data.get("page_info")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or not isinstance(page, dict) or any(type(page.get(key)) is not int or page[key] != 1 for key in ("page", "total_page", "total_number")):
        raise problem("tiktok_guard_report_unproven", "تقرير صرف الحملة غير مكتمل؛ لم تُرسل عملية إيقاف.")
    dimensions, metrics = rows[0].get("dimensions"), rows[0].get("metrics")
    if not isinstance(dimensions, dict) or str(dimensions.get("campaign_id")) != campaign or not isinstance(dimensions.get("stat_time_day"), str) or dimensions["stat_time_day"][:10] != native_day or not isinstance(metrics, dict):
        raise problem("tiktok_guard_report_unproven", "هوية تقرير صرف الحملة غير مثبتة.")
    value = metrics.get("spend")
    try:
        amount = Decimal(str(value))
        if isinstance(value, bool) or not amount.is_finite() or not 0 <= amount <= 10_000_000_000:
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        raise problem("tiktok_guard_report_unproven", "مبلغ الصرف غير مثبت.") from None
    return amount


async def run_spend_guard(db, user, snapshot, *, current=None, api_factory=TikTokCreatorAPI, provider_factory=TikTokCampaignProvider):
    if not feature_enabled("spend_guard") or not isinstance(snapshot, dict) or not snapshot.get("snapshot_id") or _slot.locked():
        return snapshot
    from campaign_ai_monitor_legacy import RECOMMENDATION_COLLECTION
    scope = {"user_id": user, "snapshot_id": snapshot["snapshot_id"]}
    async with _slot:
        if not await mongo(db[RECOMMENDATION_COLLECTION].find_one(scope, {"snapshot_id": 1}, max_time_ms=1500)):
            return snapshot
        await indexes(db)
        try:
            await mongo(db[CYCLES].insert_one({**scope, "status": "running", "created_at": now()}))
        except DuplicateKeyError:
            return snapshot
        actions, candidates, selected = [], [], []
        inventory_known = False
        try:
            async with asyncio.timeout(120):
                pointers = await mongo(db[POLICIES].find({"user_id": user, "enabled": True}).sort("account_id", 1).limit(MAX_POLICIES + 1).max_time_ms(1500).to_list(MAX_POLICIES + 1))
                if len(pointers) > MAX_POLICIES:
                    raise problem("tiktok_guard_policy_limit", "عدد سياسات الحماية يتجاوز حد الفحص.")
                versions = await mongo(db[VERSIONS].find({"user_id": user, "_id": {"$in": [p["version_id"] for p in pointers]}}).limit(MAX_POLICIES + 1).max_time_ms(1500).to_list(MAX_POLICIES + 1))
                versions = {v["_id"]: v for v in versions}
                for pointer in pointers:
                    version = versions.get(pointer["version_id"])
                    if not version or version.get("revision") != pointer["revision"] or version.get("account_id") != pointer["account_id"] or version.get("enabled") is not True:
                        raise problem("tiktok_guard_policy_unproven", "نسخة سياسة الحماية غير مثبتة.")
                    candidates.extend((pointer, version, rule) for rule in version["rules"])
                inventory_known = True
                cursor = await mongo(db[CURSORS].find_one({"_id": user, "user_id": user}, max_time_ms=1500)) or {}
                offset = cursor.get("offset", 0)
                offset = offset if type(offset) is int and offset >= 0 else 0
                selected = [candidates[(offset + i) % len(candidates)] for i in range(min(MAX_CHECKS, len(candidates)))] if candidates else []
                await mongo(db[CURSORS].update_one({"_id": user, "user_id": user}, {"$set": {"offset": (offset + len(selected)) % max(1, len(candidates))}}, upsert=True))
                for pointer, version, rule in selected:
                    result = await _check_rule(db, user, snapshot["snapshot_id"], pointer, version, rule, current=current, api_factory=api_factory, provider_factory=provider_factory)
                    actions.append(result)
        except Exception:
            actions.append({"status": "guard_unavailable", "provider_write_reached": None})
        summary = {"status": "complete" if not any(a["status"] == "guard_unavailable" for a in actions) else "partial", "checks": actions, "interval_hours": 5,
            "scope": "selected_rules_only", "configured_rules": len(candidates) if inventory_known else None,
            "checked_rules": sum("campaign_id" in action for action in actions),
            "coverage_exhaustive": inventory_known and len(selected) == len(candidates) and not any(action["status"] == "guard_unavailable" for action in actions),
            "maximum_checks": MAX_CHECKS, "profit_sar": None, "financial_orders": None,
            "budget_increases_allowed": False, "instantaneous_spend_guarantee": False}
        confirmed = any(action["status"] == "paused_verified" for action in actions)
        uncertain = any(action.get("provider_write_reached") is True or action["status"] == "guard_unavailable" for action in actions)
        if snapshot.get("writes_performed") is True or confirmed:
            writes_performed = True
        elif uncertain:
            writes_performed = None
        else:
            writes_performed = snapshot.get("writes_performed", False)
        summary.update({"mode": "owner_configured_defensive_pause", "confirmed_pauses": sum(action["status"] == "paused_verified" for action in actions), "writes_performed": writes_performed})
        await mongo(db[CYCLES].update_one(scope, {"$set": {"status": summary["status"], "summary": summary, "finished_at": now()}}))
        await mongo(db[RECOMMENDATION_COLLECTION].update_one(scope, {"$set": {"tiktok_spend_guard": summary, "writes_performed": writes_performed}}))
        return {**snapshot, "tiktok_spend_guard": summary, "writes_performed": writes_performed}


async def _check_rule(db, user, snapshot_id, pointer, version, rule, *, current, api_factory, provider_factory):
    account, campaign = pointer["account_id"], rule["campaign_id"]
    stamp = (current() if current else now()).astimezone(timezone.utc)
    day = stamp.astimezone(ZoneInfo(version["timezone"])).date().isoformat()
    action = {"_id": secrets.token_hex(16), "user_id": user, "snapshot_id": snapshot_id, "account_id": account, "campaign_id": campaign,
        "policy_revision": pointer["revision"], "policy_version_id": pointer["version_id"], "created_at": now(), "native_day": day, "currency": version["currency"], "status": "checking", "provider_write_reached": False}
    try:
        await mongo(db[ACTIONS].insert_one(action))
    except DuplicateKeyError:
        return {"account_id": account, "campaign_id": campaign, "status": "already_claimed"}
    try:
        _, token, _ = await _account(db, user, account)
        async with api_factory() as api:
            meta = await metadata(api, token, account)
            if meta != {"currency": version["currency"], "timezone": version["timezone"]}:
                raise problem("tiktok_guard_account_changed", "تغيرت عملة الحساب أو توقيته؛ أعد اعتماد السياسة.")
            spend = await fresh_spend(api, token, account, campaign, day)
        threshold = _money(rule["pause_at_daily_spend_native"])
        action.update({"reported_spend_native": str(spend), "pause_at_daily_spend_native": str(threshold)})
        if spend < threshold:
            action["status"] = "below_trigger"
        else:
            key = "spend_guard_" + _digest({"user": user, "snapshot": snapshot_id, "account": account, "campaign": campaign, "revision": pointer["revision"]})[:40]
            preview = await preview_tiktok_campaign(db, user, TikTokCampaignProposalInput(action="pause", account_id=account, campaign_id=campaign, reason="إيقاف آلي وفق حد الصرف اليومي الذي اعتمده المالك", idempotency_key=key), provider_factory=provider_factory)
            action["native_proposal_id"] = preview["proposal_id"]
            if preview.get("before", {}).get("operation_status") == "DISABLE":
                action["status"] = "already_paused"
                await mongo(db[ACTIONS].update_one({"_id": action["_id"], "user_id": user}, {"$set": {k: v for k, v in action.items() if k not in {"_id", "user_id"}}}))
                return {k: action[k] for k in ("account_id", "campaign_id", "status", "provider_write_reached")}

            async def authorize():
                fresh = (current() if current else now()).astimezone(timezone.utc)
                if fresh.astimezone(ZoneInfo(version["timezone"])).date().isoformat() != day or not feature_enabled("spend_guard"):
                    raise problem("tiktok_guard_authority_expired", "تغير يوم الصرف أو توقفت الحماية؛ لم تُرسل عملية إيقاف.")
                approved = await mongo(db[POLICIES].update_one({"user_id": user, "account_id": account, "enabled": True, "revision": pointer["revision"], "version_id": pointer["version_id"]}, {"$set": {"last_authorized_action_id": action["_id"]}}))
                if approved.matched_count != 1:
                    raise problem("tiktok_guard_policy_changed", "تغيرت سياسة المالك؛ لم تُرسل عملية إيقاف.")
                action["provider_write_reached"] = True
                await mongo(db[ACTIONS].update_one({"_id": action["_id"], "user_id": user}, {"$set": {"status": "submitted", "provider_write_reached": True, "native_proposal_id": preview["proposal_id"]}}))

            result = await execute_tiktok_campaign(db, user, preview["proposal_id"], preview["confirmation_digest"], provider_factory=provider_factory, authorization_check=authorize)
            action["status"] = "paused_verified" if result.get("verified") is True and result.get("status") == "completed" else "uncertain"
    except BaseException as error:
        action["status"] = "uncertain" if action["provider_write_reached"] else "blocked"
        if isinstance(error, asyncio.CancelledError):
            await asyncio.shield(mongo(db[ACTIONS].update_one({"_id": action["_id"], "user_id": user}, {"$set": {"status": action["status"], "provider_write_reached": action["provider_write_reached"]}})))
            raise
    await mongo(db[ACTIONS].update_one({"_id": action["_id"], "user_id": user}, {"$set": {k: v for k, v in action.items() if k not in {"_id", "user_id"}}}))
    return {k: action[k] for k in ("account_id", "campaign_id", "status", "provider_write_reached")}


def attach_spend_guard_routes(router, db, current_user, require_owner):
    routes = APIRouter(prefix="/tiktok_ads/spend-guard", route_class=BoundedCreatorJSONRoute)

    @routes.get("/policy")
    async def read(account_id: str = Query(pattern=r"^[0-9]{1,30}$"), user: dict = Depends(current_user)):
        return await current_policy(db, str(require_owner(user)["id"]), account_id)

    @routes.put("/policy")
    async def save(value: SpendPolicyInput, user: dict = Depends(current_user)):
        return await save_policy(db, str(require_owner(user)["id"]), value)

    @routes.get("/actions")
    async def history(limit: int = Query(default=12, ge=1, le=25), account_id: str | None = Query(default=None, pattern=r"^[0-9]{1,30}$"), campaign_id: str | None = Query(default=None, pattern=r"^[0-9]{1,30}$"), user: dict = Depends(current_user)):
        owner = str(require_owner(user)["id"])
        query = {"user_id": owner}
        if account_id: query["account_id"] = account_id
        if campaign_id:
            if not account_id:
                raise problem("tiktok_guard_account_required", "اختر الحساب مع الحملة لقراءة سجل الحماية.", 422)
            query["campaign_id"] = campaign_id
        rows = await mongo(db[ACTIONS].find(query, {"user_id": 0}).sort("created_at", -1).limit(limit).max_time_ms(1500).to_list(limit))
        return {"items": rows, "budget_increases_allowed": False, "profit_sar": None}

    router.include_router(routes)
