"""Owner-approved creator publishing: bounded metadata, no media transfer or retry."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
from typing import Literal
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError

from .tiktok_creator_accounts import aware, capability_projection, now, verified_creator, _signature
from .tiktok_creator_api import BoundedCreatorJSONRoute, TikTokCreatorAPI, admission, mongo, problem
from .tiktok_oauth_security import decrypt_tiktok_token, encrypt_tiktok_token

PROPOSALS = "mezan_tiktok_content_proposals_v1"
FENCES = "mezan_tiktok_content_fences_v1"
QUOTAS = "mezan_tiktok_content_quotas_v1"
PRIVACY = {"PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"}
FINAL = {"failed", "draft_delivered", "published_public", "published_private", "complete_pending_ids"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def utf16_length(value):
    return len(value.encode("utf-16-le")) // 2


def public_url(value):
    """Validate syntax only. Media bytes and DNS are never read by Mezan."""
    if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise ValueError("invalid public HTTPS URL")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment or not host or host.endswith(".") or "." not in host:
            raise ValueError("invalid public HTTPS URL")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("IP media URLs are unsupported")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or not re.fullmatch(r"[a-z0-9.-]+", host) or any(not label or label.startswith("-") or label.endswith("-") or len(label) > 63 for label in host.split(".")):
            raise ValueError("invalid public media hostname")
        path = parsed.path or "/"
        for _ in range(5):
            if "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path) or any(part in {".", ".."} for part in path.split("/")) or re.search(r"%(?:2f|5c)", path, re.I):
                raise ValueError("ambiguous media path")
            decoded = unquote(path, errors="strict")
            if decoded == path:
                break
            path = decoded
        else:
            raise ValueError("over-encoded media path")
        # Queries may contain signed access parameters, but never control bytes.
        query = parsed.query
        for _ in range(3):
            query = unquote(query, errors="strict")
            if any(ord(c) < 32 or ord(c) == 127 for c in query):
                raise ValueError("invalid media query")
        return value, host, path
    except (UnicodeError, ValueError):
        raise ValueError("invalid public HTTPS URL") from None


class ContentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    creator_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    kind: Literal["video", "photo"]
    delivery: Literal["publish", "draft"]
    video_url: str | None = Field(default=None, max_length=2048)
    photo_urls: list[str] = Field(default_factory=list, max_length=10)
    video_duration_seconds: int | None = Field(default=None, ge=3, le=600, strict=True)
    title: str = Field(default="", max_length=90)
    caption: str = Field(default="", max_length=4000)
    privacy_level: Literal["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"]
    is_brand_organic: bool = Field(strict=True)
    is_branded_content: bool = Field(strict=True)
    is_ai_generated: bool = Field(default=False, strict=True)
    disable_comment: bool = Field(default=True, strict=True)
    disable_duet: bool = Field(default=True, strict=True)
    disable_stitch: bool = Field(default=True, strict=True)
    media_requirements_confirmed: bool = Field(strict=True)

    @field_validator("video_url")
    @classmethod
    def video_address(cls, value):
        if value is not None:
            _, _, path = public_url(value)
            if not path.lower().endswith((".mp4", ".mov", ".webm")):
                raise ValueError("video format must be mp4, mov or webm")
        return value

    @field_validator("photo_urls")
    @classmethod
    def photo_addresses(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("duplicate photo URL")
        for value in values:
            _, _, path = public_url(value)
            if not path.lower().endswith((".jpg", ".jpeg", ".webp")):
                raise ValueError("photo format must be jpg, jpeg or webp")
        return values

    @field_validator("caption", "title")
    @classmethod
    def text_fields(cls, value):
        if any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("invalid post text")
        return value

    @model_validator(mode="after")
    def validate_media(self):
        if not self.media_requirements_confirmed:
            raise ValueError("confirm media requirements before preview")
        if self.kind == "video":
            if not self.video_url or self.photo_urls or self.video_duration_seconds is None or self.title or utf16_length(self.caption) > 2200:
                raise ValueError("invalid video metadata")
            if self.delivery == "publish" and self.privacy_level != "PUBLIC_TO_EVERYONE":
                raise ValueError("video API supports public direct publishing only")
        else:
            if not self.photo_urls or self.video_url or self.video_duration_seconds is not None or self.is_ai_generated or utf16_length(self.caption) > 4000 or utf16_length(self.title) > 90:
                raise ValueError("invalid photo metadata")
        if len(re.findall(r"(?<!\w)@[\w.]+", self.caption)) > 30:
            raise ValueError("too many mentions")
        return self


class ContentApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


async def ensure_indexes(db):
    await mongo(db[PROPOSALS].create_index([("user_id", 1), ("idempotency_key", 1)], unique=True, name="tiktok_content_owner_key", maxTimeMS=1500))
    await mongo(db[PROPOSALS].create_index([("user_id", 1), ("creator_ref", 1), ("created_at", -1)], name="tiktok_content_history", maxTimeMS=1500))
    # Execution fences and submitted/uncertain records deliberately have no TTL.


def settings_proof(data):
    options = data.get("privacy_level_options")
    duration = data.get("max_video_post_duration_sec")
    if not isinstance(options, list) or not 1 <= len(options) <= 4 or any(not isinstance(v, str) or v not in PRIVACY for v in options) or len(set(options)) != len(options) or type(duration) not in (int, float) or not math.isfinite(duration) or not 3 <= duration <= 600:
        raise problem("tiktok_creator_settings_unproven", "تعذر التحقق من إعدادات النشر للحساب.", 502)
    if any(type(data.get(k)) is not bool for k in ("comment_disabled", "duet_disabled", "stitch_disabled")):
        raise problem("tiktok_creator_settings_unproven", "تعذر التحقق من إعدادات التفاعل للحساب.", 502)
    return {"privacy_level_options": sorted(options), "max_video_post_duration_sec": duration, **{k: data[k] for k in ("comment_disabled", "duet_disabled", "stitch_disabled")}}


def property_proof(data):
    rows = data.get("url_property_info_list")
    if not isinstance(rows, list) or len(rows) > 200:
        raise problem("tiktok_media_ownership_unproven", "تعذر التحقق من ملكية روابط الوسائط.", 502)
    result = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get("property_status")) is not int or row["property_status"] != 1 or type(row.get("property_type")) is not int or row["property_type"] not in (1, 2) or not isinstance(row.get("url"), str):
            continue
        try:
            raw = row["url"]
            value = raw if raw.startswith("https://") else "https://" + raw
            _, host, path = public_url(value)
            parsed = urlsplit(value)
            if parsed.query:
                continue
            if row["property_type"] == 1 and parsed.path not in ("", "/"):
                continue
            if row["property_type"] == 2 and (not raw.startswith("https://") or not path.endswith("/")):
                continue
            result.append({"type": row["property_type"], "host": host, "path": "/" if row["property_type"] == 1 else path})
        except ValueError:
            continue
    return sorted(result, key=canonical)


def ownership_for(urls, properties):
    matched = []
    for value in urls:
        _, host, path = public_url(value)
        choices = [p for p in properties if (p["type"] == 1 and (host == p["host"] or host.endswith("." + p["host"]))) or (p["type"] == 2 and host == p["host"] and path.startswith(p["path"]))]
        if not choices:
            raise problem("tiktok_media_url_not_verified", "وثّق نطاق الوسائط أو بادئة الرابط في تطبيق TikTok قبل النشر.")
        matched.append(choices[0])
    return matched


async def context(db, user_id, creator_ref, api, *, required_scope=None):
    row, token, creator_id = await verified_creator(db, user_id, creator_ref, api, required_scope)
    settings = settings_proof(await api.call("GET", "/business/video/settings/", token=token, params={"business_id": creator_id}))
    properties = property_proof(await api.call("GET", "/business/property/list/", params={"app_id": os.environ["TIKTOK_MARKETING_APP_ID"], "secret": os.environ["TIKTOK_MARKETING_APP_SECRET"]}))
    return row, token, creator_id, settings, properties


async def readiness(db, user_id, creator_ref):
    async with admission(), TikTokCreatorAPI() as api:
        row, _, _, settings, properties = await context(db, user_id, creator_ref, api)
        return {"creator_ref": creator_ref, "label": row["label"], "verified_at": now(), "expires_at": row["expires_at"], "scopes": row["scopes"], "capabilities": capability_projection(row["scopes"]), "settings": settings, "verified_properties": properties, "limits": {"photos": 10, "metadata_bytes": 32768, "provider_response_bytes": 131072, "publish_per_minute": 6, "publish_per_24_hours": 15}, "media_transfer_to_mezan": False, "ai_auto_reply_enabled": False}


def planned_publish(payload, settings, properties):
    if payload.delivery == "publish" and payload.privacy_level not in settings["privacy_level_options"]:
        raise problem("tiktok_content_privacy_not_allowed", "اختر خصوصية يسمح بها حساب TikTok.")
    if payload.kind == "video" and payload.video_duration_seconds > settings["max_video_post_duration_sec"]:
        raise problem("tiktok_content_duration_not_allowed", "مدة الفيديو تتجاوز حد الحساب.")
    urls = [payload.video_url] if payload.kind == "video" else payload.photo_urls
    proof = ownership_for(urls, properties)
    if payload.delivery == "draft":
        post_info = {"upload_to_draft": True} if payload.kind == "video" else {"is_draft": True, "title": payload.title, "caption": payload.caption, "privacy_level": payload.privacy_level, "is_brand_organic": payload.is_brand_organic, "is_branded_content": payload.is_branded_content}
        # Photo API requires privacy/disclosure fields, but draft mode ignores
        # them. The preview explicitly describes this provider behavior.
    else:
        post_info = {"caption": payload.caption, "is_brand_organic": payload.is_brand_organic, "is_branded_content": payload.is_branded_content, "disable_comment": payload.disable_comment or settings["comment_disabled"]}
        if payload.kind == "video":
            post_info.update({"disable_duet": payload.disable_duet or settings["duet_disabled"], "disable_stitch": payload.disable_stitch or settings["stitch_disabled"], "is_ai_generated": payload.is_ai_generated, "upload_to_draft": False, "is_ads_only": False})
        else:
            post_info.update({"title": payload.title, "privacy_level": payload.privacy_level, "is_draft": False, "auto_add_music": False})
    body = {"post_info": post_info}
    if payload.kind == "video":
        body["video_url"] = payload.video_url
    else:
        body.update({"photo_images": payload.photo_urls, "photo_cover_index": 0})
    return {"body": body, "path": "/business/video/publish/" if payload.kind == "video" else "/business/photo/publish/", "settings": settings, "ownership": proof, "input": payload.model_dump()}


def public_proposal(row, *, preview=False):
    value = {"proposal_id": row["_id"], "creator_ref": row["creator_ref"], "account_label": row["account_label"], "kind": row["kind"], "delivery": row["delivery"], "status": row["status"], "created_at": row["created_at"], "expires_at": row["expires_at"], "publish_task_id": row.get("publish_task_id"), "post_ids": row.get("post_ids", []), "public_post_verified": row.get("status") == "published_public", "safe_failure_reason": row.get("safe_failure_reason"), "next_status_check_at": row.get("next_status_check_at"), "media_transfer_to_mezan": False, "automatic_retry_allowed": False}
    if preview:
        plan = json.loads(decrypt_tiktok_token(row["plan_encrypted"]))
        value.update({"confirmation_digest": row["confirmation_digest"], "input": plan["input"], "effective_post_info": plan["body"]["post_info"], "settings": plan["settings"], "draft_notice": ("سيصل الفيديو إلى صندوق TikTok لاستكمال النشر؛ النص وإعدادات المنشور تُستكمل هناك." if row["kind"] == "video" else "تصل الصور إلى صندوق TikTok مع العنوان والنص فقط؛ تُستكمل الخصوصية والإفصاحات هناك.") if row["delivery"] == "draft" else None})
    return value


async def owned_proposal(db, user_id, proposal_id):
    if not re.fullmatch(r"[a-f0-9]{32}", proposal_id):
        raise problem("tiktok_content_proposal_not_found", "لم نعثر على عملية النشر.", 404)
    row = await mongo(db[PROPOSALS].find_one({"_id": proposal_id, "user_id": user_id}, max_time_ms=1500))
    if not row:
        raise problem("tiktok_content_proposal_not_found", "لم نعثر على عملية النشر.", 404)
    return row


async def preview_content(db, user_id, payload):
    async with admission():
        await ensure_indexes(db)
        input_hash = hashlib.sha256(canonical(payload.model_dump()).encode()).hexdigest()
        old = await mongo(db[PROPOSALS].find_one({"user_id": user_id, "idempotency_key": payload.idempotency_key}, max_time_ms=1500))
        if old:
            if not hmac.compare_digest(old["input_hash"], input_hash):
                raise problem("tiktok_content_key_conflict", "المعرف مستخدم لمعاينة مختلفة؛ أنشئ معاينة جديدة.")
            return public_proposal(old, preview=True)
        async with TikTokCreatorAPI() as api:
            row, _, _, settings, properties = await context(db, user_id, payload.creator_ref, api, required_scope="video.upload" if payload.delivery == "draft" else "video.publish")
            plan = planned_publish(payload, settings, properties)
        proposal_id = secrets.token_hex(16)
        digest = _signature(canonical({"owner": user_id, "proposal": proposal_id, "epoch": row["auth_epoch"], "plan": plan}), "tiktok-content-approval:")
        instant = now()
        proposal = {"_id": proposal_id, "user_id": user_id, "creator_ref": payload.creator_ref, "account_label": row["label"], "idempotency_key": payload.idempotency_key, "input_hash": input_hash, "auth_epoch": row["auth_epoch"], "plan_encrypted": encrypt_tiktok_token(canonical(plan)), "confirmation_digest": digest, "kind": payload.kind, "delivery": payload.delivery, "privacy_level": payload.privacy_level, "status": "previewed", "created_at": instant, "expires_at": instant + timedelta(minutes=10)}
        try:
            await mongo(db[PROPOSALS].insert_one(proposal))
        except DuplicateKeyError:
            old = await mongo(db[PROPOSALS].find_one({"user_id": user_id, "idempotency_key": payload.idempotency_key}, max_time_ms=1500))
            if not old or old["input_hash"] != input_hash:
                raise problem("tiktok_content_key_conflict", "تغيرت المعاينة؛ أعد تحميلها.") from None
            return public_proposal(old, preview=True)
        return public_proposal(proposal, preview=True)


async def reserve_quota(db, creator_ref):
    instant = now()
    row = await mongo(db[QUOTAS].find_one({"_id": creator_ref}, max_time_ms=1500))
    stamps = [aware(t) for t in (row or {}).get("reservations", []) if aware(t) > instant - timedelta(hours=24)]
    if len(stamps) >= 15 or sum(t > instant - timedelta(seconds=60) for t in stamps) >= 6:
        raise problem("tiktok_content_creator_quota", "بلغ الحساب حد النشر؛ انتظر قبل إنشاء عملية أخرى.", 429)
    if row:
        result = await mongo(db[QUOTAS].update_one({"_id": creator_ref, "revision": row["revision"]}, {"$set": {"reservations": stamps + [instant]}, "$inc": {"revision": 1}}))
        if result.modified_count != 1:
            raise problem("tiktok_content_quota_busy", "تغيرت حصة النشر؛ أعد المعاينة.", 503)
    else:
        try:
            await mongo(db[QUOTAS].insert_one({"_id": creator_ref, "revision": 1, "reservations": [instant]}))
        except DuplicateKeyError:
            raise problem("tiktok_content_quota_busy", "حصة النشر مشغولة؛ أعد المعاينة.", 503) from None


async def release_fence(db, row):
    # Only a proven terminal provider result or a pre-submit failure may release.
    await mongo(db[FENCES].delete_one({"_id": row["creator_ref"], "proposal_id": row["_id"], "claim": row.get("execution_claim")}))


async def approve_content(db, user_id, proposal_id, approval):
    async with admission():
        row = await owned_proposal(db, user_id, proposal_id)
        if not hmac.compare_digest(row["confirmation_digest"], approval.confirmation_digest):
            raise problem("tiktok_content_confirmation_mismatch", "الاعتماد لا يطابق المعاينة المعروضة.")
        # Every state after preview, including an interrupted validation, is
        # durable. A second approval must never send a second provider POST.
        if row["status"] != "previewed":
            return public_proposal(row)
        if aware(row["expires_at"]) <= now():
            raise problem("tiktok_content_preview_expired", "انتهت المعاينة؛ أنشئ معاينة جديدة.")
        claim = secrets.token_hex(16)
        reserved = await mongo(db[PROPOSALS].update_one({"_id": proposal_id, "user_id": user_id, "status": "previewed", "expires_at": {"$gt": now()}}, {"$set": {"status": "validating", "execution_claim": claim}}))
        if reserved.modified_count != 1:
            return public_proposal(await owned_proposal(db, user_id, proposal_id))
        row.update({"status": "validating", "execution_claim": claim})
        sent_marker = False
        accepted = False
        fence_owned = False
        try:
            try:
                await mongo(db[FENCES].insert_one({"_id": row["creator_ref"], "proposal_id": proposal_id, "user_id": user_id, "claim": claim, "created_at": now()}))
                fence_owned = True
            except DuplicateKeyError:
                raise problem("tiktok_content_creator_inflight", "للحساب عملية لم يُحسم وضعها؛ راجع حالتها قبل نشر محتوى آخر.") from None
            plan = json.loads(decrypt_tiktok_token(row["plan_encrypted"]))
            payload = ContentInput.model_validate(plan["input"])
            async with TikTokCreatorAPI() as api:
                creator, token, creator_id, settings, properties = await context(db, user_id, row["creator_ref"], api, required_scope="video.upload" if row["delivery"] == "draft" else "video.publish")
                if creator["auth_epoch"] != row["auth_epoch"] or canonical(planned_publish(payload, settings, properties)) != canonical(plan):
                    raise problem("tiktok_content_preview_changed", "تغير الربط أو إعدادات النشر أو ملكية الرابط؛ أنشئ معاينة جديدة.")
                await reserve_quota(db, row["creator_ref"])
                # Persist before network transmission. Even a DB acknowledgement
                # timeout here keeps the fence; this operation is never retried.
                sent_marker = True
                marker = await mongo(db[PROPOSALS].update_one({"_id": proposal_id, "status": "validating", "execution_claim": claim}, {"$set": {"status": "submitted", "submitted_at": now()}}))
                if marker.modified_count != 1:
                    raise problem("tiktok_content_claim_changed", "تعذر إثبات حجز عملية النشر؛ راجع السجل.", 503)
                body = {**plan["body"], "business_id": creator_id}
                data = await api.call("POST", plan["path"], token=token, body=body)
                task_id = data.get("share_id")
                if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_.~-]{1,200}", task_id):
                    raise problem("tiktok_content_publish_result_unproven", "لم يثبت معرّف مهمة النشر؛ راجع السجل قبل أي محاولة أخرى.", 502)
                recorded = await mongo(db[PROPOSALS].update_one({"_id": proposal_id, "execution_claim": claim, "status": "submitted"}, {"$set": {"status": "accepted", "publish_task_id": task_id, "accepted_at": now(), "next_status_check_at": now() + timedelta(seconds=15)}}))
                if recorded.matched_count != 1:
                    raise problem("tiktok_content_publish_result_unproven", "تعذر حفظ نتيجة النشر؛ راجع السجل.", 503)
                row.update({"status": "accepted", "publish_task_id": task_id, "next_status_check_at": now() + timedelta(seconds=15)})
                accepted = True
            return public_proposal(row)
        except BaseException as exc:
            if accepted:
                # Known acceptance survives a client-close/local finalization
                # error. The retained fence is repaired by read-only status.
                return public_proposal(row)
            stage = "uncertain" if sent_marker else "blocked"
            try:
                saved = await mongo(db[PROPOSALS].update_one({"_id": proposal_id, "execution_claim": claim, "status": {"$in": ["validating", "submitted"]}}, {"$set": {"status": stage, "safe_failure_reason": "publish_result_unproven" if sent_marker else "preflight_rejected"}}))
                if not sent_marker and fence_owned and saved.matched_count == 1:
                    await release_fence(db, row)
            except Exception:
                pass
            if sent_marker:
                row.update({"status": "uncertain", "safe_failure_reason": "publish_result_unproven"})
                if isinstance(exc, Exception):
                    return public_proposal(row)
            raise


async def content_status(db, user_id, proposal_id):
    async with admission():
        row = await owned_proposal(db, user_id, proposal_id)
        if row["status"] in FINAL and row["status"] != "complete_pending_ids":
            try:
                await release_fence(db, row)
            except Exception:
                pass
            return public_proposal(row)
        if not row.get("publish_task_id"):
            # No discover-by-caption, automatic retry or fabricated post ID.
            return public_proposal(row)
        instant = now()
        if aware(row.get("next_status_check_at")) > instant:
            raise problem("tiktok_content_status_throttled", "انتظر قليلًا قبل التحقق من حالة النشر مرة أخرى.", 429)
        ticket = secrets.token_hex(16)
        lock = await mongo(db[PROPOSALS].update_one({"_id": proposal_id, "user_id": user_id, "$or": [{"next_status_check_at": {"$lte": instant}}, {"next_status_check_at": {"$exists": False}}]}, {"$set": {"next_status_check_at": instant + timedelta(seconds=15), "status_read_claim": ticket}}))
        if lock.modified_count != 1:
            raise problem("tiktok_content_status_busy", "التحقق جارٍ؛ حاول بعد قليل.", 429)
        async with TikTokCreatorAPI() as api:
            _, token, creator_id = await verified_creator(db, user_id, row["creator_ref"], api, "video.upload" if row["delivery"] == "draft" else "video.publish")
            data = await api.call("GET", "/business/publish/status/", token=token, params={"business_id": creator_id, "publish_id": row["publish_task_id"]})
        provider_status = data.get("status")
        updates = {"last_status_checked_at": now()}
        if provider_status == "PROCESSING_DOWNLOAD":
            updates["status"] = "processing"
        elif provider_status == "SEND_TO_USER_INBOX" and row["delivery"] == "draft":
            updates["status"] = "draft_delivered"
        elif provider_status == "FAILED":
            reason = data.get("reason")
            updates.update({"status": "failed", "safe_failure_reason": reason if isinstance(reason, str) and re.fullmatch(r"[a-z0-9_]{1,80}", reason) else "provider_publish_failed"})
        elif provider_status == "PUBLISH_COMPLETE" and row["delivery"] == "publish":
            ids = data.get("post_ids", [])
            if not isinstance(ids, list) or len(ids) > 35 or any(not isinstance(v, str) or not re.fullmatch(r"[0-9]{1,32}", v) for v in ids):
                raise problem("tiktok_content_status_unproven", "تعذر إثبات نتيجة النشر؛ راجع الحالة لاحقًا.", 502)
            if row["privacy_level"] == "PUBLIC_TO_EVERYONE":
                updates.update({"status": "published_public" if ids else "complete_pending_ids", "post_ids": list(dict.fromkeys(ids))})
                if not ids:
                    updates["next_status_check_at"] = now() + timedelta(minutes=3)
            else:
                updates.update({"status": "published_private", "post_ids": []})
        else:
            raise problem("tiktok_content_status_unproven", "لم يثبت وضع مهمة النشر؛ أعد التحقق لاحقًا.", 502)
        result = await mongo(db[PROPOSALS].update_one({"_id": proposal_id, "user_id": user_id, "status_read_claim": ticket}, {"$set": updates}))
        if result.matched_count != 1:
            raise problem("tiktok_content_status_changed", "تغير سجل النشر؛ أعد تحميله.", 503)
        row.update(updates)
        if row["status"] in FINAL:
            try:
                await release_fence(db, row)
            except Exception:
                # Durable provider completion remains the source of truth.
                # A subsequent manual read repairs only this owned fence.
                pass
        return public_proposal(row)


async def content_history(db, user_id, creator_ref=None, limit=12):
    query = {"user_id": user_id}
    if creator_ref:
        query["creator_ref"] = creator_ref
    cursor = db[PROPOSALS].find(query).sort("created_at", -1).limit(limit).max_time_ms(1500)
    return {"items": [public_proposal(row) for row in await mongo(cursor.to_list(limit))], "limit": limit}


def attach_tiktok_content_routes(router, db, current_user, require_owner):
    content = APIRouter(prefix="/tiktok/content", route_class=BoundedCreatorJSONRoute)

    @content.post("/creators/{creator_ref}/verify")
    async def verify(creator_ref: str, user: dict = Depends(current_user)):
        owner = require_owner(user)
        if not re.fullmatch(r"[a-f0-9]{64}", creator_ref):
            raise problem("tiktok_creator_not_found", "لم نعثر على حساب النشر.", 404)
        return await readiness(db, str(owner["id"]), creator_ref)

    @content.post("/proposals")
    async def preview(payload: ContentInput, user: dict = Depends(current_user)):
        owner = require_owner(user)
        return await preview_content(db, str(owner["id"]), payload)

    @content.get("/proposals")
    async def history(creator_ref: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"), limit: int = Query(default=12, ge=1, le=25), user: dict = Depends(current_user)):
        owner = require_owner(user)
        return await content_history(db, str(owner["id"]), creator_ref, limit)

    @content.get("/proposals/{proposal_id}")
    async def detail(proposal_id: str, user: dict = Depends(current_user)):
        owner = require_owner(user)
        return public_proposal(await owned_proposal(db, str(owner["id"]), proposal_id), preview=True)

    @content.post("/proposals/{proposal_id}/approve-and-publish")
    async def approve(proposal_id: str, payload: ContentApproval, user: dict = Depends(current_user)):
        owner = require_owner(user)
        return await approve_content(db, str(owner["id"]), proposal_id, payload)

    @content.get("/proposals/{proposal_id}/status")
    async def status(proposal_id: str, user: dict = Depends(current_user)):
        owner = require_owner(user)
        return await content_status(db, str(owner["id"]), proposal_id)

    router.include_router(content)
