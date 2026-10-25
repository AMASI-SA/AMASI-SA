"""Owner-approved Smart+ child controls, with no model or automatic write path.

ADR-001: additive/flagged (1,3), native account authority (9), encrypted
immutable previews and permanent uncertain-write fences (8,10,11,14).
Only selected native entities are read. No media or customer data is fetched.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from datetime import timedelta, timezone
from typing import Literal
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError

from .tiktok_ad_builder import BuilderApproval, ID_PATTERN, now, single
from .tiktok_campaign_management import _account, _digest, _money, _financial_bound
from .tiktok_creator_api import BoundedCreatorJSONRoute, TikTokCreatorAPI, admission, mongo, problem
from .tiktok_oauth_security import decrypt_tiktok_token, encrypt_tiktok_token
from .tiktok_features import enabled as feature_enabled

PROPOSALS = "mezan_tiktok_child_proposals_v1"
FENCES = "mezan_tiktok_child_fences_v1"


class ChildInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["adgroup", "ad"]
    action: Literal["pause", "enable", "rename", "set_budget", "set_conversion_bid"]
    account_id: str = Field(pattern=ID_PATTERN)
    campaign_id: str = Field(pattern=ID_PATTERN)
    adgroup_id: str = Field(pattern=ID_PATTERN)
    ad_id: str | None = Field(default=None, pattern=ID_PATTERN)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    amount_native: float | None = Field(default=None, strict=True, gt=0, le=10_000_000)
    spend_ceiling_native: float | None = Field(default=None, strict=True, gt=0, le=10_000_000)
    reason: str = Field(min_length=5, max_length=400)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")

    @field_validator("name", "reason")
    @classmethod
    def text(cls, value):
        if value is not None and (not value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("invalid control text")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def action_fields(self):
        if (self.kind == "ad") != (self.ad_id is not None):
            raise ValueError("ad identity does not match kind")
        if (self.action == "rename") != (self.name is not None):
            raise ValueError("name does not match action")
        monetary = self.action in {"set_budget", "set_conversion_bid"}
        if monetary != (self.amount_native is not None) or (monetary and self.kind != "adgroup"):
            raise ValueError("amount does not match action")
        if (monetary or self.action == "enable") != (self.spend_ceiling_native is not None):
            raise ValueError("explicit native spend ceiling required for financial action")
        return self


def enabled():
    if not feature_enabled("child_management"):
        raise problem("tiktok_child_management_disabled", "إدارة مجموعات وإعلانات TikTok غير مفعّلة في هذه البيئة.", 503)


async def indexes(db):
    await mongo(db[PROPOSALS].create_index([("user_id", 1), ("idempotency_key", 1)], unique=True, name="tiktok_child_request_unique", maxTimeMS=1500))
    await mongo(db[PROPOSALS].create_index([("user_id", 1), ("created_at", -1)], name="tiktok_child_history", maxTimeMS=1500))
    await mongo(db[FENCES].create_index([("user_id", 1), ("scope", 1)], unique=True, name="tiktok_child_pending_entity", maxTimeMS=1500))


def identity(value):
    return value.ad_id if value.kind == "ad" else value.adgroup_id


def scope(value):
    return _digest({"account_id": value.account_id, "kind": value.kind, "entity_id": identity(value)})


async def inspect(api, token, value):
    async def read(kind, identifier, fields):
        native = await single(api, token, value.account_id, kind, identifier, fields)
        return {key: native.get(key) for key in fields}

    campaign = await read("campaign", value.campaign_id,
        ["advertiser_id", "campaign_id", "operation_status", "objective_type", "sales_destination", "budget_optimize_on", "budget_auto_adjust_strategy", "budget", "budget_mode"])
    if campaign.get("operation_status") not in {"ENABLE", "DISABLE"} or campaign.get("objective_type") != "WEB_CONVERSIONS" or campaign.get("sales_destination") != "WEBSITE":
        raise problem("tiktok_child_campaign_unproven", "الحملة ليست حملة Smart+ صالحة لمبيعات الموقع.")
    group = await read("adgroup", value.adgroup_id,
        ["advertiser_id", "campaign_id", "adgroup_id", "adgroup_name", "operation_status", "budget", "budget_mode", "bid_type", "conversion_bid_price"])
    if str(group.get("campaign_id")) != value.campaign_id or group.get("operation_status") not in {"ENABLE", "DISABLE"}:
        raise problem("tiktok_child_parent_unproven", "المجموعة غير مثبتة داخل الحملة المحددة.")
    target = group
    if value.kind == "ad":
        target = await read("ad", value.ad_id,
            ["advertiser_id", "campaign_id", "adgroup_id", "smart_plus_ad_id", "ad_name", "operation_status"])
        if str(target.get("campaign_id")) != value.campaign_id or str(target.get("adgroup_id")) != value.adgroup_id or target.get("operation_status") not in {"ENABLE", "DISABLE"}:
            raise problem("tiktok_child_ad_unproven", "الإعلان غير مثبت داخل المجموعة المحددة.")
    return {"campaign": campaign, "adgroup": group, "target": target}


def financial_bound(value, before):
    if value.action in {"enable", "set_budget", "set_conversion_bid"}:
        campaign, group = before["campaign"], before["adgroup"]
        if type(campaign.get("budget_optimize_on")) is not bool:
            raise problem("tiktok_child_budget_mode_unproven", "نوع ميزانية الحملة غير مثبت.")
        if value.action != "enable" and campaign["budget_optimize_on"]:
            raise problem("tiktok_child_cbo_budget", "ميزانية هذه الحملة مشتركة؛ عدّلها من إدارة الحملة.")
        budget_owner = campaign if campaign["budget_optimize_on"] else group
        # Reuse the existing native financial contract, including dynamic
        # daily125% exposure and explicit absence of automatic budget increases.
        basis = {**budget_owner, "flow": "smart_plus", "budget_optimize_on": campaign["budget_optimize_on"], "budget_auto_adjust_strategy": campaign.get("budget_auto_adjust_strategy")}
        return _financial_bound(SimpleNamespace(action="set_budget" if value.action == "set_budget" else "enable", budget_native=value.amount_native, spend_ceiling_native=value.spend_ceiling_native), basis)
    return None


def planned(value, before):
    target = before["target"]
    financial_bound(value, before)
    if value.action in {"pause", "enable"}:
        if value.action == "enable" and (before["campaign"]["operation_status"] != "ENABLE" or value.kind == "ad" and before["adgroup"]["operation_status"] != "ENABLE"):
            raise problem("tiktok_child_parent_paused", "فعّل الحملة والمجموعة المطلوبة أولًا بعد مراجعة سقوفها.")
        return {"operation_status": "ENABLE" if value.action == "enable" else "DISABLE"}
    if value.action == "rename":
        return {"ad_name" if value.kind == "ad" else "adgroup_name": value.name}
    if value.action == "set_conversion_bid":
        if target.get("bid_type") != "BID_TYPE_CUSTOM":
            raise problem("tiktok_child_bid_mode_unproven", "هذه المجموعة لا تستخدم عرض تحويل مخصصًا مثبتًا.")
        return {"conversion_bid_price": float(_money(value.amount_native))}
    return {"budget": float(_money(value.amount_native))}


async def preflight(db, user, value, api):
    _, token, epoch = await _account(db, user, value.account_id)
    info = await api.call("GET", "/advertiser/info/", token=token, params={"advertiser_ids": json.dumps([value.account_id]), "fields": json.dumps(["advertiser_id", "currency"])})
    rows = info.get("list")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or str(rows[0].get("advertiser_id")) != value.account_id:
        raise problem("tiktok_child_account_unproven", "الحساب الإعلاني غير مثبت.")
    currency = rows[0].get("currency")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isupper() or not currency.isalpha():
        raise problem("tiktok_child_currency_unproven", "عملة الحساب غير مثبتة.")
    before = await inspect(api, token, value)
    changes = planned(value, before)
    body = {"advertiser_id": value.account_id, **changes}
    key = "smart_plus_ad_id" if value.kind == "ad" else "adgroup_id"
    if value.action in {"pause", "enable"}:
        body[key + "s"] = [identity(value)]
        path = f"/smart_plus/{value.kind}/status/update/"
    else:
        body[key] = identity(value)
        path = f"/smart_plus/{value.kind}/update/"
    return token, {"auth_epoch": epoch, "currency": currency, "before": before, "changes": changes, "financial_bound": financial_bound(value, before), "path": path, "body": body}


async def owned(db, user, identifier):
    row = await mongo(db[PROPOSALS].find_one({"_id": identifier, "user_id": user}, max_time_ms=1500))
    if not row:
        raise problem("tiktok_child_not_found", "لم نعثر على معاينة الإدارة.", 404)
    return row


def public(row, *, preview=False):
    result = {key: row.get(key) for key in ("kind", "action", "account_id", "entity_id", "currency", "status", "verified", "created_at", "expires_at", "safe_error")}
    result.update({"proposal_id": row["_id"], "automatic_retry_allowed": False})
    if preview:
        plan = json.loads(decrypt_tiktok_token(row["plan_encrypted"]))
        result.update({"confirmation_digest": row["digest"], "before": plan["proof"]["before"]["target"], "changes": plan["proof"]["changes"], "reason": plan["input"]["reason"], "financial_bound": plan["proof"]["financial_bound"]})
    return result


async def preview_child(db, user, value):
    enabled()
    async with admission():
        await indexes(db)
        fingerprint = _digest(value.model_dump(mode="json"))
        old = await mongo(db[PROPOSALS].find_one({"user_id": user, "idempotency_key": value.idempotency_key}, max_time_ms=1500))
        if old:
            if old["fingerprint"] != fingerprint:
                raise problem("tiktok_child_key_conflict", "المفتاح مرتبط بمعاينة مختلفة.")
            return public(old, preview=True)
        async with TikTokCreatorAPI() as api:
            _, proof = await preflight(db, user, value, api)
        plan = {"input": value.model_dump(mode="json"), "proof": proof}
        encoded = json.dumps(plan, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 32768:
            raise problem("tiktok_child_plan_too_large", "بيانات المعاينة أكبر من الحد المسموح.", 413)
        row = {"_id": secrets.token_hex(16), "user_id": user, "kind": value.kind, "action": value.action,
            "account_id": value.account_id, "entity_id": identity(value), "currency": proof["currency"],
            "idempotency_key": value.idempotency_key, "fingerprint": fingerprint, "status": "previewed", "verified": False,
            "created_at": now(), "expires_at": now() + timedelta(minutes=10), "plan_encrypted": encrypt_tiktok_token(encoded), "digest": _digest({"user_id": user, "plan": plan})}
        try:
            await mongo(db[PROPOSALS].insert_one(row))
        except DuplicateKeyError:
            old = await mongo(db[PROPOSALS].find_one({"user_id": user, "idempotency_key": value.idempotency_key}, max_time_ms=1500))
            if not old or old["fingerprint"] != fingerprint:
                raise problem("tiktok_child_key_conflict", "تعارض في حفظ المعاينة.") from None
            row = old
        return public(row, preview=True)


async def finish(db, user, row, value):
    await mongo(db[PROPOSALS].update_one({"_id": row["_id"], "user_id": user}, {"$set": {"status": "completed", "verified": True, "completed_at": now()}}))
    await mongo(db[FENCES].delete_one({"user_id": user, "scope": scope(value), "proposal_id": row["_id"]}))
    return public(await owned(db, user, row["_id"]))


async def verify(api, token, value, proof):
    observed = await inspect(api, token, value)
    planned(value, observed)
    actual = observed["target"]
    for key, wanted in proof["changes"].items():
        found = actual.get(key)
        matches = _money(found) == _money(wanted) if key in {"budget", "conversion_bid_price"} else found == wanted
        if not matches:
            raise problem("tiktok_child_result_unproven", "لم تثبت مطابقة التعديل للمعاينة؛ لا تكرر الإرسال.", 502)


async def execute_child(db, user, identifier, digest):
    enabled()
    async with admission():
        row = await owned(db, user, identifier)
        plan = json.loads(decrypt_tiktok_token(row["plan_encrypted"]))
        if not secrets.compare_digest(digest, row["digest"]) or _digest({"user_id": user, "plan": plan}) != digest:
            raise problem("tiktok_child_digest_changed", "تغيرت المعاينة؛ لا يمكن اعتمادها.")
        value = ChildInput(**plan["input"])
        if row["status"] == "completed":
            return await finish(db, user, row, value)
        if row["status"] != "previewed" or row["expires_at"].replace(tzinfo=timezone.utc) <= now():
            raise problem("tiktok_child_not_executable", "انتهت المعاينة أو بدأ إرسالها؛ راجع السجل.")
        async with TikTokCreatorAPI() as api:
            token, fresh = await preflight(db, user, value, api)
            if fresh != plan["proof"]:
                raise problem("tiktok_child_preflight_changed", "تغير الحساب أو العنصر بعد المعاينة؛ أعد تجهيزها.")
            try:
                await mongo(db[FENCES].insert_one({"user_id": user, "scope": scope(value), "proposal_id": identifier}))
            except DuplicateKeyError:
                raise problem("tiktok_child_pending_write", "هناك تعديل لهذا العنصر لم تثبت نتيجته؛ راجع سجله.") from None
            submitted = False
            try:
                claim = await mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user, "status": "previewed", "digest": digest}, {"$set": {"status": "submitted", "submitted_at": now()}}))
                if claim.modified_count != 1:
                    raise problem("tiktok_child_already_claimed", "سبق بدء تعديل هذا العنصر.")
                submitted = True
                await api.call("POST", fresh["path"], token=token, body=fresh["body"])
                await verify(api, token, value, fresh)
                return await finish(db, user, row, value)
            except BaseException:
                if submitted:
                    try:
                        await asyncio.shield(mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user, "status": {"$ne": "completed"}}, {"$set": {"status": "uncertain", "safe_error": "provider_result_unproven"}})))
                    except BaseException:
                        pass
                else:
                    await mongo(db[FENCES].delete_one({"user_id": user, "scope": scope(value), "proposal_id": identifier}))
                raise


async def reconcile_child(db, user, identifier):
    enabled()
    async with admission():
        row = await owned(db, user, identifier)
        plan = json.loads(decrypt_tiktok_token(row["plan_encrypted"]))
        if _digest({"user_id": user, "plan": plan}) != row["digest"]:
            raise problem("tiktok_child_digest_changed", "تعذر إثبات المعاينة الأصلية.")
        value = ChildInput(**plan["input"])
        if row["status"] == "completed":
            return await finish(db, user, row, value)
        if row["status"] not in {"submitted", "uncertain"}:
            raise problem("tiktok_child_reconcile_unavailable", "لا توجد نتيجة مرسلة تحتاج تحققًا.")
        claim = await mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user, "$or": [{"next_verify_at": {"$exists": False}}, {"next_verify_at": {"$lte": now()}}]}, {"$set": {"next_verify_at": now() + timedelta(seconds=30)}}))
        if claim.modified_count != 1:
            raise problem("tiktok_child_verification_wait", "انتظر30ثانية قبل قراءة النتيجة مرة أخرى.", 429)
        _, token, _ = await _account(db, user, value.account_id)
        async with TikTokCreatorAPI() as api:
            await verify(api, token, value, plan["proof"])
        return await finish(db, user, row, value)


def attach_child_management_routes(router, db, current_user, require_owner):
    routes = APIRouter(prefix="/tiktok_ads/child-management", route_class=BoundedCreatorJSONRoute)

    @routes.post("/proposals", status_code=201)
    async def preview(value: ChildInput, user: dict = Depends(current_user)):
        return await preview_child(db, str(require_owner(user)["id"]), value)

    @routes.get("/proposals")
    async def history(limit: int = Query(default=12, ge=1, le=25), user: dict = Depends(current_user)):
        owner = str(require_owner(user)["id"])
        enabled()
        rows = await mongo(db[PROPOSALS].find({"user_id": owner}, {"plan_encrypted": 0}).sort("created_at", -1).limit(limit).max_time_ms(1500).to_list(limit))
        return {"items": [public(row) for row in rows]}

    @routes.get("/proposals/{identifier}")
    async def detail(identifier: str, user: dict = Depends(current_user)):
        owner = str(require_owner(user)["id"])
        enabled()
        return public(await owned(db, owner, identifier), preview=True)

    @routes.post("/proposals/{identifier}/approve-and-execute")
    async def execute(identifier: str, value: BuilderApproval, user: dict = Depends(current_user)):
        return await execute_child(db, str(require_owner(user)["id"]), identifier, value.confirmation_digest)

    @routes.post("/proposals/{identifier}/reconcile")
    async def reconcile(identifier: str, user: dict = Depends(current_user)):
        return await reconcile_child(db, str(require_owner(user)["id"]), identifier)

    router.include_router(routes)
