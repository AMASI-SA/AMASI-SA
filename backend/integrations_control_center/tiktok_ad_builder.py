"""Paused Smart+ ad-group/video-ad creation and URL-only asset import.

ADR-001: additive/flagged (1,3), one existing native account authority (9),
encrypted immutable plans and durable execution claims (8,10,11,14). No model
or background worker can invoke these owner-approved routes. Media bytes never
enter Mezan. A transmitted request is never repeated after an uncertain result.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import secrets
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError

from .tiktok_campaign_management import _account, _digest, _money
from .tiktok_creator_api import BoundedCreatorJSONRoute, TikTokCreatorAPI, admission, mongo, problem
from .tiktok_content_publishing import public_url
from .tiktok_oauth_security import decrypt_tiktok_token, encrypt_tiktok_token

PROPOSALS = "mezan_tiktok_ad_builder_proposals_v1"
FENCES = "mezan_tiktok_ad_builder_fences_v1"
TTL = timedelta(minutes=10)
ID_PATTERN = r"^[0-9]{1,30}$"


def now():
    return datetime.now(timezone.utc)


class AdBuilderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["video_asset", "adgroup", "ad"]
    account_id: str = Field(pattern=ID_PATTERN)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")
    name: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=5, max_length=400)
    campaign_id: str | None = Field(default=None, pattern=ID_PATTERN)
    adgroup_id: str | None = Field(default=None, pattern=ID_PATTERN)
    video_url: str | None = Field(default=None, max_length=2048)
    video_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    tiktok_item_id: str | None = Field(default=None, pattern=ID_PATTERN)
    identity_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    identity_type: Literal["CUSTOMIZED_USER", "AUTH_CODE", "TT_USER", "BC_AUTH_TT"] | None = None
    landing_page_url: str | None = Field(default=None, max_length=2048)
    ad_text: str | None = Field(default=None, min_length=1, max_length=100)
    call_to_action: Literal["SHOP_NOW", "LEARN_MORE", "ORDER_NOW"] = "SHOP_NOW"
    pixel_id: str | None = Field(default=None, pattern=ID_PATTERN)
    optimization_event: Literal["ON_WEB_ORDER", "COMPLETE_PAYMENT"] = "ON_WEB_ORDER"
    location_ids: list[str] = Field(default_factory=list, max_length=25)
    budget_native: float | None = Field(default=None, strict=True, gt=0, le=10_000_000)
    spend_ceiling_native: float | None = Field(default=None, strict=True, gt=0, le=10_000_000)
    bid_price_native: float | None = Field(default=None, strict=True, gt=0, le=10_000_000)
    schedule_start: datetime | None = None
    schedule_end: datetime | None = None

    @field_validator("name", "reason", "ad_text")
    @classmethod
    def clean_text(cls, value):
        if value is not None and (not value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("invalid text")
        return value.strip() if value is not None else value

    @field_validator("video_url", "landing_page_url")
    @classmethod
    def url(cls, value):
        if value is not None:
            public_url(value)
        return value

    @field_validator("location_ids")
    @classmethod
    def locations(cls, values):
        if len(set(values)) != len(values) or any(not v.isdigit() or len(v) > 30 for v in values):
            raise ValueError("invalid TikTok location IDs")
        return values

    @model_validator(mode="after")
    def fields_for_kind(self):
        groups = {"video_asset": {"video_url"}, "adgroup": {"campaign_id", "pixel_id", "location_ids", "budget_native", "spend_ceiling_native", "schedule_start", "schedule_end"},
                  "ad": {"campaign_id", "adgroup_id", "identity_id", "identity_type", "landing_page_url"}}
        for field in groups[self.kind]:
            if not getattr(self, field):
                raise ValueError(field + " required")
        extra = {"video_asset": {"campaign_id", "adgroup_id", "video_id", "identity_id", "identity_type", "landing_page_url", "ad_text", "pixel_id", "location_ids", "budget_native", "spend_ceiling_native", "bid_price_native", "schedule_start", "schedule_end"},
                 "adgroup": {"adgroup_id", "video_url", "video_id", "identity_id", "identity_type", "landing_page_url", "ad_text"},
                 "ad": {"video_url", "pixel_id", "location_ids", "budget_native", "spend_ceiling_native", "bid_price_native", "schedule_start", "schedule_end"}}
        if any(getattr(self, f) not in (None, []) for f in extra[self.kind]):
            raise ValueError("fields do not match requested operation")
        if self.kind != "ad" and self.tiktok_item_id is not None:
            raise ValueError("post linking only applies to ads")
        if self.kind == "ad":
            if (self.video_id is None) == (self.tiktok_item_id is None):
                raise ValueError("select exactly one native video or TikTok post")
            if self.tiktok_item_id is not None:
                if self.identity_type not in {"TT_USER", "AUTH_CODE"} or self.ad_text is not None:
                    raise ValueError("Spark requires an owned/authorized identity and retains the post caption")
            elif self.ad_text is None:
                raise ValueError("non-Spark ads require text")
        if self.kind == "adgroup":
            if self.schedule_start.tzinfo is None or self.schedule_end and self.schedule_end.tzinfo is None:
                raise ValueError("schedule requires explicit timezone")
            if self.schedule_end and self.schedule_end <= self.schedule_start:
                raise ValueError("schedule end must follow start")
        return self


class BuilderApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


async def indexes(db):
    await mongo(db[PROPOSALS].create_index([("user_id", 1), ("idempotency_key", 1)], unique=True, name="tiktok_builder_request_unique", maxTimeMS=1500))
    await mongo(db[PROPOSALS].create_index([("user_id", 1), ("created_at", -1)], name="tiktok_builder_history", maxTimeMS=1500))
    await mongo(db[FENCES].create_index([("user_id", 1), ("scope", 1)], unique=True, name="tiktok_builder_content_unique", maxTimeMS=1500))


async def single(api, token, account, kind, identifier, fields):
    key = "smart_plus_ad_id" if kind == "ad" else kind + "_id"
    data = await api.call("GET", f"/smart_plus/{kind}/get/", token=token, params={"advertiser_id": account,
        "filtering": json.dumps({key + "s": [identifier]}), "fields": json.dumps(fields), "page": 1, "page_size": 1})
    rows = data.get("list")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or str(rows[0].get(key)) != identifier or str(rows[0].get("advertiser_id")) != account or (data.get("page_info") or {}).get("total_number") != 1:
        raise problem("tiktok_builder_identity_unproven", "تعذر إثبات هوية العنصر داخل الحساب.")
    return rows[0]


async def preflight(db, user, value, api):
    _, token, epoch = await _account(db, user, value.account_id)
    data = await api.call("GET", "/advertiser/info/", token=token, params={"advertiser_ids": json.dumps([value.account_id]), "fields": json.dumps(["advertiser_id", "currency", "timezone"])})
    accounts = data.get("list")
    if not isinstance(accounts, list) or len(accounts) != 1 or str(accounts[0].get("advertiser_id")) != value.account_id:
        raise problem("tiktok_builder_account_unproven", "تعذر إثبات الحساب الإعلاني.")
    metadata = accounts[0]
    currency = metadata.get("currency")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isupper() or not currency.isalpha():
        raise problem("tiktok_builder_currency_unproven", "عملة الحساب غير مثبتة.")
    proof = {"auth_epoch": epoch, "currency": currency}
    body = {"advertiser_id": value.account_id}
    if value.kind == "video_asset":
        return token, proof, {**body, "upload_type": "UPLOAD_BY_URL", "video_url": value.video_url, "file_name": value.name}
    campaign = await single(api, token, value.account_id, "campaign", value.campaign_id,
        ["advertiser_id", "campaign_id", "operation_status", "objective_type", "sales_destination", "budget_optimize_on"])
    if campaign.get("operation_status") != "DISABLE" or campaign.get("objective_type") != "WEB_CONVERSIONS" or campaign.get("sales_destination") != "WEBSITE":
        raise problem("tiktok_builder_parent_not_paused", "اختر حملة Smart+ لمبيعات الموقع موقوفة قبل تجهيز المجموعة والإعلان.")
    proof["campaign"] = campaign
    if value.kind == "adgroup":
        # This slice creates an explicitly bounded group under non-CBO Smart+.
        if campaign.get("budget_optimize_on") is not False:
            raise problem("tiktok_builder_budget_mode_unproven", "هذه المجموعة تتطلب حملة بميزانية مستقلة لكل مجموعة.")
        amount, ceiling = _money(value.budget_native), _money(value.spend_ceiling_native)
        if amount > ceiling:
            raise problem("tiktok_builder_budget_exceeds_ceiling", "ميزانية المجموعة تتجاوز السقف الذي اعتمدته.")
        try:
            zone = ZoneInfo(metadata["timezone"])
        except (KeyError, ValueError, TypeError):
            raise problem("tiktok_builder_timezone_unproven", "المنطقة الزمنية للحساب غير مثبتة.") from None
        if value.schedule_start <= now():
            raise problem("tiktok_builder_schedule_past", "اختر موعد بدء لاحقًا للوقت الحالي ثم أعد المعاينة.")
        proof["timezone"] = metadata["timezone"]
        body.update({"campaign_id": value.campaign_id, "adgroup_name": value.name, "operation_status": "DISABLE",
            "promotion_type": "WEBSITE", "optimization_goal": "CONVERT", "optimization_event": value.optimization_event,
            "billing_event": "OCPM", "pixel_id": value.pixel_id, "budget": float(amount), "budget_mode": "BUDGET_MODE_DAY",
            "bid_type": "BID_TYPE_NO_BID" if value.bid_price_native is None else "BID_TYPE_CUSTOM",
            "schedule_type": "SCHEDULE_START_END",
            "schedule_start_time": value.schedule_start.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S"),
            "targeting_spec": {"location_ids": value.location_ids}, "placement_type": "PLACEMENT_TYPE_NORMAL", "placements": ["PLACEMENT_TIKTOK"]})
        if value.bid_price_native is not None:
            body["conversion_bid_price"] = float(_money(value.bid_price_native))
        if value.schedule_end is not None:
            body["schedule_end_time"] = value.schedule_end.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S")
    else:
        group = await single(api, token, value.account_id, "adgroup", value.adgroup_id,
            ["advertiser_id", "campaign_id", "adgroup_id", "operation_status"])
        if str(group.get("campaign_id")) != value.campaign_id or group.get("operation_status") != "DISABLE":
            raise problem("tiktok_builder_adgroup_unproven", "اختر مجموعة موقوفة تتبع الحملة المحددة.")
        proof["adgroup"] = group
        if value.video_id is not None:
            videos = await api.call("GET", "/file/video/ad/info/", token=token, params={"advertiser_id": value.account_id, "video_ids": json.dumps([value.video_id])})
            rows = videos.get("list")
            if not isinstance(rows, list) or len(rows) != 1 or str(rows[0].get("video_id")) != value.video_id:
                raise problem("tiktok_builder_video_unproven", "الفيديو غير مثبت داخل مكتبة الحساب المحدد.")
        if value.identity_type == "BC_AUTH_TT":
            raise problem("tiktok_builder_identity_bc_not_supported", "هذه الهوية تحتاج ربط Business Center؛ اختر هوية TikTok مباشرة في هذه المرحلة.")
        identities = await api.call("GET", "/identity/get/", token=token, params={"advertiser_id": value.account_id, "identity_type": value.identity_type, "page": 1, "page_size": 50})
        items = identities.get("identity_list")
        rows = [r for r in items if isinstance(r, dict) and str(r.get("identity_id")) == value.identity_id and r.get("identity_type") == value.identity_type] if isinstance(items, list) and len(items) <= 50 else []
        if len(rows) != 1:
            raise problem("tiktok_builder_ad_identity_unproven", "هوية الإعلان غير مثبتة داخل الحساب.")
        creative = {"ad_format": "SINGLE_VIDEO", "identity_id": value.identity_id, "identity_type": value.identity_type}
        if value.tiktok_item_id is not None:
            proof["spark_post"] = await spark_post_proof(api, token, value)
            creative["tiktok_item_id"] = value.tiktok_item_id
        else:
            creative["video_info"] = {"video_id": value.video_id}
        body.update({"adgroup_id": value.adgroup_id, "ad_name": value.name, "operation_status": "DISABLE",
            "creative_list": [{"creative_info": creative}],
            "landing_page_url_list": [{"landing_page_url": value.landing_page_url}],
            "call_to_action_list": [{"call_to_action": value.call_to_action}]})
        if value.ad_text is not None:
            body["ad_text_list"] = [{"ad_text": value.ad_text}]
    return token, proof, body


async def spark_post_proof(api, token, value):
    # Official Business API /identity/video/info/ binds the requested post to
    # the selected advertiser and identity. Never fetch its media preview URLs.
    data = await api.call("GET", "/identity/video/info/", token=token, params={"advertiser_id": value.account_id,
        "identity_type": value.identity_type, "identity_id": value.identity_id, "item_id": value.tiktok_item_id})
    row = data.get("video_detail")
    if not isinstance(row, dict) or str(row.get("item_id")) != value.tiktok_item_id or row.get("status") != "ITEM_STATUS_HESITATE_RECOMMEND" or row.get("item_type") not in (None, "VIDEO") or not isinstance(row.get("video_info"), dict):
        raise problem("tiktok_spark_post_unproven", "المنشور العام غير مثبت ضمن هوية الإعلان المحددة.")
    text = row.get("text")
    if not isinstance(text, str) or len(text.encode("utf-8")) > 16000:
        raise problem("tiktok_spark_caption_unproven", "نص المنشور غير مثبت ضمن حدود المعاينة.")
    auth = row.get("auth_info")
    if value.identity_type == "AUTH_CODE":
        if not isinstance(auth, dict) or auth.get("ad_auth_status") != "AUTHORIZED":
            raise problem("tiktok_spark_authorization_unproven", "تفويض الإعلان لهذا المنشور غير مثبت.")
        try:
            dates = []
            for key in ("auth_start_time", "auth_end_time"):
                parsed = datetime.fromisoformat(auth[key].replace("Z", "+00:00"))
                dates.append(parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc))
            start, end = dates
        except (KeyError, TypeError, ValueError, AttributeError):
            raise problem("tiktok_spark_authorization_unproven", "مدة تفويض المنشور غير مثبتة.") from None
        if not start <= now() < end:
            raise problem("tiktok_spark_authorization_expired", "تفويض هذا المنشور منتهٍ أو لم يبدأ بعد.")
        auth = {"ad_auth_status": "AUTHORIZED", "auth_start_time": start.isoformat(), "auth_end_time": end.isoformat()}
    else:
        auth = None
    return {"item_id": value.tiktok_item_id, "identity_id": value.identity_id, "identity_type": value.identity_type,
            "status": row["status"], "caption": text, "auth_info": auth}


async def owned(db, user, identifier):
    row = await mongo(db[PROPOSALS].find_one({"_id": identifier, "user_id": user}, max_time_ms=1500))
    if not row:
        raise problem("tiktok_builder_not_found", "لم نعثر على المعاينة.", 404)
    return row


def public(row, *, preview=False):
    result = {k: row.get(k) for k in ("kind", "name", "account_id", "status", "created_at", "expires_at", "created_entity_id", "verified", "safe_error")}
    result.update({"proposal_id": row["_id"], "media_transfer_to_mezan": False, "automatic_retry_allowed": False})
    if preview:
        result.update({"confirmation_digest": row["digest"], "plan": json.loads(decrypt_tiktok_token(row["plan_encrypted"]))})
    return result


async def preview_builder(db, user, value):
    async with admission():
        await indexes(db)
        fingerprint = _digest(value.model_dump(mode="json"))
        old = await mongo(db[PROPOSALS].find_one({"user_id": user, "idempotency_key": value.idempotency_key}, max_time_ms=1500))
        if old:
            if old["fingerprint"] != fingerprint:
                raise problem("tiktok_builder_key_conflict", "المفتاح مرتبط بطلب مختلف.")
            return public(old, preview=True)
        async with TikTokCreatorAPI() as api:
            _, proof, body = await preflight(db, user, value, api)
        if value.kind == "adgroup":
            body["request_id"] = str(secrets.randbelow(2**63 - 1) + 1)
        plan = {"input": value.model_dump(mode="json"), "proof": proof, "body": body}
        encoded = json.dumps(plan, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 32768:
            raise problem("tiktok_builder_plan_too_large", "بيانات المعاينة تجاوزت الحد المسموح.", 413)
        row = {"_id": secrets.token_hex(16), "user_id": user, "kind": value.kind, "name": value.name,
            "account_id": value.account_id, "idempotency_key": value.idempotency_key, "fingerprint": fingerprint,
            "status": "previewed", "verified": False, "created_at": now(), "expires_at": now() + TTL,
            "plan_encrypted": encrypt_tiktok_token(encoded), "digest": _digest({"user_id": user, "plan": plan})}
        try:
            await mongo(db[PROPOSALS].insert_one(row))
        except DuplicateKeyError:
            old = await mongo(db[PROPOSALS].find_one({"user_id": user, "idempotency_key": value.idempotency_key}))
            if not old or old["fingerprint"] != fingerprint:
                raise problem("tiktok_builder_key_conflict", "تعارض في حفظ المعاينة.") from None
            row = old
        return public(row, preview=True)


async def execute_builder(db, user, identifier, digest):
    async with admission():
        row = await owned(db, user, identifier)
        plan = json.loads(decrypt_tiktok_token(row["plan_encrypted"]))
        if not secrets.compare_digest(digest, row["digest"]) or _digest({"user_id": user, "plan": plan}) != digest:
            raise problem("tiktok_builder_digest_changed", "تغيرت المعاينة؛ لا يمكن اعتمادها.")
        if row["status"] == "completed":
            return public(row)
        if row["status"] != "previewed" or row["expires_at"].replace(tzinfo=timezone.utc) <= now():
            raise problem("tiktok_builder_not_executable", "المعاينة منتهية أو بدأت عمليتها؛ راجع السجل قبل أي محاولة.")
        if os.environ.get("TIKTOK_AD_BUILDER_ENABLED", "false").lower() not in {"true", "1", "yes", "on"}:
            raise problem("tiktok_builder_disabled", "إنشاء مجموعات وإعلانات TikTok غير مفعّل في بيئة التشغيل.", 503)
        value = AdBuilderInput(**plan["input"])
        async with TikTokCreatorAPI() as api:
            token, proof, body = await preflight(db, user, value, api)
            if proof != plan["proof"] or {k: v for k, v in plan["body"].items() if k != "request_id"} != body:
                raise problem("tiktok_builder_preflight_changed", "تغير الحساب أو العناصر بعد المعاينة؛ أعد تجهيزها.")
            scope = _digest({"account_id": value.account_id, "kind": value.kind, "body": body})
            try:
                # Permanent once submitted, including if response or local save
                # is lost. A new key cannot repeat the same pending creation.
                await mongo(db[FENCES].insert_one({"user_id": user, "scope": scope, "proposal_id": identifier}))
            except DuplicateKeyError:
                raise problem("tiktok_builder_duplicate_creation", "هناك عملية إنشاء مطابقة؛ راجع سجلها ولا تكررها.") from None
            submitted = False
            try:
                claim = await mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user, "status": "previewed", "digest": digest}, {"$set": {"status": "submitted", "submitted_at": now()}}))
                if claim.modified_count != 1:
                    raise problem("tiktok_builder_already_claimed", "سبق بدء تنفيذ المعاينة.")
                submitted = True
                data = await api.call("POST", "/file/video/ad/upload/" if value.kind == "video_asset" else f"/smart_plus/{value.kind}/create/", token=token,
                    **({"form": plan["body"]} if value.kind == "video_asset" else {"body": plan["body"]}))
                key = {"video_asset": "video_id", "adgroup": "adgroup_id", "ad": "smart_plus_ad_id"}[value.kind]
                created = data.get(key)
                if not isinstance(created, (str, int)) or isinstance(created, bool) or not str(created) or len(str(created)) > 128:
                    raise problem("tiktok_builder_created_id_unproven", "معرّف الإنشاء غير مثبت؛ لا تكرر العملية.", 502)
                created = str(created)
                await mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user}, {"$set": {"created_entity_id": created, "status": "verifying"}}))
                await verify_created(api, token, value, created, plan["body"])
                await mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user}, {"$set": {"status": "completed", "verified": True, "completed_at": now()}}))
            except BaseException:
                if submitted:
                    try:
                        await asyncio.shield(mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user, "status": {"$ne": "completed"}}, {"$set": {"status": "uncertain", "safe_error": "provider_result_unproven"}})))
                    except BaseException:
                        pass
                else:
                    await mongo(db[FENCES].delete_one({"user_id": user, "scope": scope, "proposal_id": identifier}))
                raise
        return public(await owned(db, user, identifier))


async def verify_created(api, token, value, created, planned):
    if value.kind == "video_asset":
        data = await api.call("GET", "/file/video/ad/info/", token=token, params={"advertiser_id": value.account_id, "video_ids": json.dumps([created])})
        rows = data.get("list")
        matched = isinstance(rows, list) and len(rows) == 1 and str(rows[0].get("video_id")) == created
    else:
        key = "smart_plus_ad_id" if value.kind == "ad" else "adgroup_id"
        fields = ["advertiser_id", key, *[k for k in planned if k not in {"advertiser_id", "request_id"}]]
        actual = await single(api, token, value.account_id, value.kind, created, fields)
        matched = all(actual.get(k) == v for k, v in planned.items() if k not in {"advertiser_id", "request_id"})
    if not matched:
        raise problem("tiktok_builder_verification_failed", "لم تثبت مطابقة إنشاء TikTok للمعاينة؛ لا تكرر الإرسال.", 502)


async def reconcile_builder(db, user, identifier):
    async with admission():
        row = await owned(db, user, identifier)
        if row["status"] == "completed":
            return public(row)
        if row["status"] not in {"submitted", "verifying", "uncertain"} or not row.get("created_entity_id"):
            raise problem("tiktok_builder_reconciliation_unavailable", "معرّف الإنشاء غير مثبت للقراءة؛ راجع العملية في TikTok ولا تكرر إرسالها.")
        claimed = await mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user, "$or": [{"next_verify_at": {"$exists": False}}, {"next_verify_at": {"$lte": now()}}]}, {"$set": {"next_verify_at": now() + timedelta(seconds=30)}}))
        if claimed.modified_count != 1:
            raise problem("tiktok_builder_verification_wait", "انتظر 30 ثانية قبل قراءة النتيجة مرة أخرى.", 429)
        plan = json.loads(decrypt_tiktok_token(row["plan_encrypted"]))
        if _digest({"user_id": user, "plan": plan}) != row["digest"]:
            raise problem("tiktok_builder_digest_changed", "تعذر إثبات المعاينة الأصلية.")
        value = AdBuilderInput(**plan["input"])
        _, token, _ = await _account(db, user, value.account_id)
        async with TikTokCreatorAPI() as api:
            await verify_created(api, token, value, row["created_entity_id"], plan["body"])
        await mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user}, {"$set": {"status": "completed", "verified": True, "completed_at": now()}}))
        return public(await owned(db, user, identifier))


def attach_ad_builder_routes(router, db, current_user, require_owner):
    routes = APIRouter(prefix="/tiktok_ads/ad-builder", route_class=BoundedCreatorJSONRoute)

    @routes.post("/proposals", status_code=201)
    async def preview(value: AdBuilderInput, user: dict = Depends(current_user)):
        return await preview_builder(db, str(require_owner(user)["id"]), value)

    @routes.get("/proposals")
    async def history(limit: int = Query(default=12, ge=1, le=25), user: dict = Depends(current_user)):
        owner = str(require_owner(user)["id"])
        rows = await mongo(db[PROPOSALS].find({"user_id": owner}, {"plan_encrypted": 0}).sort("created_at", -1).limit(limit).max_time_ms(1500).to_list(limit))
        return {"items": [public(r) for r in rows]}

    @routes.get("/proposals/{identifier}")
    async def detail(identifier: str, user: dict = Depends(current_user)):
        return public(await owned(db, str(require_owner(user)["id"]), identifier), preview=True)

    @routes.post("/proposals/{identifier}/approve-and-execute")
    async def execute(identifier: str, value: BuilderApproval, user: dict = Depends(current_user)):
        return await execute_builder(db, str(require_owner(user)["id"]), identifier, value.confirmation_digest)

    @routes.post("/proposals/{identifier}/reconcile")
    async def reconcile(identifier: str, user: dict = Depends(current_user)):
        return await reconcile_builder(db, str(require_owner(user)["id"]), identifier)

    router.include_router(routes)
