"""Owner-approved FILE_UPLOAD sessions; only JSON crosses Mezan's boundary.

Native upload URLs are encrypted capabilities for the browser, not server
fetch targets. An unproven native initialization is never automatically repeated.
"""
import asyncio
from datetime import timedelta
import hashlib
import json
import os
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo.errors import DuplicateKeyError

from .tiktok_creator_accounts import CREDENTIALS, _signature, aware, now, verified_creator
from .tiktok_creator_api import BoundedCreatorJSONRoute, TikTokCreatorAPI, admission, mongo, problem
from .tiktok_content_publishing import settings_proof
from .tiktok_file_api import MAX_VIDEO_BYTES, REJECTIONS, REJECTION_MESSAGES, TikTokFileAPI, source_info, upload_url
from .tiktok_oauth_security import decrypt_tiktok_token, encrypt_tiktok_token

PROPOSALS = "mezan_tiktok_file_proposals_v1"
FENCES = "mezan_tiktok_file_fences_v1"
TERMINAL = {"published_public", "published_private", "draft_delivered", "failed"}
PRIVACY = {"PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"}


def enabled():
    return os.getenv("TIKTOK_FILE_UPLOAD_ENABLED", "true").strip().lower() in {"true", "1"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class FilePostInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    creator_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,80}$")
    file_name: str = Field(min_length=1, max_length=160)
    file_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    file_size: int = Field(strict=True, ge=1, le=MAX_VIDEO_BYTES)
    mime_type: str = Field(pattern=r"^video/(mp4|quicktime|webm)$")
    duration_seconds: float = Field(strict=True, ge=3, le=600)
    width: int = Field(strict=True, ge=360, le=4096)
    height: int = Field(strict=True, ge=360, le=4096)
    delivery: str = Field(default="draft", pattern=r"^(direct|draft)$")
    privacy_level: str | None = None
    caption: str = Field(default="", max_length=2200)
    disclosure: str = Field(default="none", pattern=r"^(none|own_business|paid_partnership)$")
    is_aigc: bool = Field(default=False, strict=True)
    disable_comment: bool = Field(default=True, strict=True)
    disable_duet: bool = Field(default=True, strict=True)
    disable_stitch: bool = Field(default=True, strict=True)
    media_rights_confirmed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def consent_and_metadata(self):
        if not self.media_rights_confirmed or any(ord(c) < 32 or c in "/\\" for c in self.file_name):
            raise ValueError("file name and media rights confirmation required")
        if len(self.caption.encode("utf-16-le")) // 2 > 2200:
            raise ValueError("caption too long")
        if self.delivery == "direct" and self.privacy_level not in PRIVACY:
            raise ValueError("choose a privacy option")
        if self.delivery == "draft" and (self.privacy_level is not None or self.caption or self.disclosure != "none" or self.is_aigc):
            raise ValueError("complete draft metadata inside TikTok")
        if self.disclosure == "paid_partnership" and self.privacy_level != "PUBLIC_TO_EVERYONE":
            raise ValueError("paid partnership requires public visibility")
        return self


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


async def context(db, user, creator, *, delivery="direct", business_factory=TikTokCreatorAPI, api_factory=TikTokFileAPI):
    if not enabled():
        raise problem("tiktok_file_disabled", "رفع الفيديو من الجهاز غير مفعّل.", 503)
    async with business_factory() as business:
        row, token, identity = await verified_creator(db, user, creator, business, "video.publish")
    if "user.info.basic" not in row["scopes"] or delivery == "draft" and "video.upload" not in row["scopes"]:
        raise problem("tiktok_file_scope_missing", "أعد تفويض حساب النشر بصلاحيات رفع الفيديو ونشره.")
    async with api_factory() as api:
        profile = await api.call("GET", "/v2/user/info/", token=token, params={"fields": "open_id,display_name"})
        native = profile.get("user")
        if not isinstance(native, dict) or native.get("open_id") != identity:
            raise problem("tiktok_file_identity_unproven", "ربط رفع الملفات لا يطابق حساب النشر المختار.")
        settings = settings_proof(await api.call("POST", "/v2/post/publish/creator_info/query/", token=token, body={}))
    proof = {"creator_ref": creator, "auth_epoch": row["auth_epoch"], "app_id": row["app_id"],
        "identity_digest": _signature(identity, "tiktok-file-identity:"), "settings": settings}
    return row, token, proof


def body_for(value, proof):
    if value.duration_seconds > proof["settings"]["max_video_post_duration_sec"]:
        raise problem("tiktok_file_duration_exceeded", "مدة الفيديو أطول من الحد المتاح لهذا الحساب.", 422)
    body = {"source_info": source_info(value.file_size)}
    if value.delivery == "direct":
        if value.privacy_level not in proof["settings"]["privacy_level_options"]:
            raise problem("tiktok_file_privacy_changed", "اختر خصوصية متاحة من إعدادات الحساب.", 422)
        for interaction in ("comment", "duet", "stitch"):
            if proof["settings"][interaction + "_disabled"] and not getattr(value, "disable_" + interaction):
                raise problem("tiktok_file_interaction_denied", "إعدادات الحساب تمنع أحد خيارات التفاعل المختارة.", 422)
        body["post_info"] = {"title": value.caption, "privacy_level": value.privacy_level,
            "disable_comment": value.disable_comment, "disable_duet": value.disable_duet,
            "disable_stitch": value.disable_stitch, "brand_content_toggle": value.disclosure == "paid_partnership",
            "brand_organic_toggle": value.disclosure == "own_business", "is_aigc": value.is_aigc}
    return body


async def indexes(db):
    await mongo(db[PROPOSALS].create_index([("user_id", 1), ("idempotency_key", 1)], unique=True, name="tiktok_file_owner_key", maxTimeMS=1500))
    await mongo(db[PROPOSALS].create_index([("user_id", 1), ("created_at", -1)], name="tiktok_file_history", maxTimeMS=1500))


def plan(row):
    value = json.loads(decrypt_tiktok_token(row["plan_encrypted"]))
    if digest({"id": row["_id"], "user": row["user_id"], "plan": value, "expires": aware(row["expires_at"]).isoformat()}) != row["confirmation_digest"]:
        raise problem("tiktok_file_plan_changed", "تغيرت معاينة الفيديو؛ لم يُرسل طلب جديد.")
    return value


def public(row):
    value = plan(row)
    result = {"proposal_id": row["_id"], "creator_ref": row["creator_ref"], "status": row["status"],
        "confirmation_digest": row["confirmation_digest"], "input": value["input"], "settings": value["proof"]["settings"],
        "source_info": value["body"]["source_info"], "expires_at": row["expires_at"],
        "uploaded_bytes": row.get("uploaded_bytes"), "public_post_ids": row.get("public_post_ids", []),
        "upload_expires_at": row.get("upload_expires_at"), "created_at": row["created_at"]}
    if isinstance(row.get("initialization_diagnostic"), dict):
        result["initialization_diagnostic"] = initialization_diagnostic(row["initialization_diagnostic"])
    return result


def initialization_diagnostic(value):
    """Only fixed codes and messages may leave persistence; provider text is private."""
    messages = {
        "tiktok_file_authorization_changed": "تغير تفويض الحساب قبل تهيئة الرفع. أعد التحقق من ربط الحساب.",
        "tiktok_file_already_submitted": "سبق بدء هذه العملية. حدّث حالتها قبل أي إرسال جديد.",
        "tiktok_file_provider_rejected": "رفض TikTok تهيئة رفع الفيديو لهذا الحساب.",
        "tiktok_file_provider_http_error": "لم تثبت استجابة TikTok. حدّث حالة العملية قبل أي محاولة أخرى.",
        "tiktok_file_response_unproven": "تعذر التحقق من استجابة TikTok. لا تكرر تهيئة الرفع.",
        "tiktok_file_transport_failed": "انقطع الاتصال أثناء تهيئة TikTok. حدّث حالة العملية قبل أي محاولة أخرى.",
        "tiktok_file_upload_destination_unproven": "تعذر إثبات وجهة رفع الملف. حدّث حالة العملية قبل أي محاولة أخرى.",
        "tiktok_file_initialization_unproven": "لم تثبت نتيجة تهيئة الرفع. حدّث حالة العملية قبل أي محاولة أخرى.",
    }
    code = value.get("code")
    if not isinstance(code, str) or code not in messages:
        code = "tiktok_file_initialization_unproven"
    stage = value.get("stage")
    result = {"code": code, "stage": stage if stage in ("before_initialization", "native_initialization") else "unknown", "message": messages[code]}
    provider = value.get("provider_code")
    if code == "tiktok_file_provider_rejected" and isinstance(provider, str) and provider in REJECTIONS:
        result.update({"provider_code": provider, "message": REJECTION_MESSAGES[provider]})
    return result


async def owned(db, user, identifier):
    row = await mongo(db[PROPOSALS].find_one({"_id": identifier, "user_id": user}, max_time_ms=1500))
    if not row:
        raise problem("tiktok_file_not_found", "عملية رفع الفيديو غير موجودة.", 404)
    plan(row)
    return row


async def preview(db, user, value, **factories):
    async with admission():
        await indexes(db)
        old = await mongo(db[PROPOSALS].find_one({"user_id": user, "idempotency_key": value.idempotency_key}, max_time_ms=1500))
        if old:
            if plan(old)["input"] != value.model_dump():
                raise problem("tiktok_file_key_changed", "تغير الملف أو إعداداته؛ أنشئ معاينة جديدة.")
            return public(old)
        pending = await mongo(db[PROPOSALS].count_documents({"user_id": user, "status": "previewed", "expires_at": {"$gt": now()}}, limit=20, maxTimeMS=1500))
        if pending >= 20:
            raise problem("tiktok_file_preview_limit", "أكمل المعاينات المفتوحة أو انتظر انتهاءها.", 429)
        _, _, proof = await context(db, user, value.creator_ref, delivery=value.delivery, **factories)
        native = body_for(value, proof)
        value_plan = {"input": value.model_dump(), "proof": proof, "body": native}
        instant = now()
        instant = instant.replace(microsecond=instant.microsecond // 1000 * 1000)
        row = {"_id": secrets.token_hex(16), "user_id": user, "creator_ref": value.creator_ref,
            "idempotency_key": value.idempotency_key, "status": "previewed", "created_at": instant, "expires_at": instant + timedelta(minutes=10)}
        row["confirmation_digest"] = digest({"id": row["_id"], "user": user, "plan": value_plan, "expires": row["expires_at"].isoformat()})
        row["plan_encrypted"] = encrypt_tiktok_token(json.dumps(value_plan))
        try:
            await mongo(db[PROPOSALS].insert_one(row))
        except DuplicateKeyError:
            old = await mongo(db[PROPOSALS].find_one({"user_id": user, "idempotency_key": value.idempotency_key}, max_time_ms=1500))
            if not old or plan(old)["input"] != value.model_dump():
                raise problem("tiktok_file_key_changed", "تغيرت معاينة الملف.") from None
            return public(old)
        return public(row)


async def execute(db, user, identifier, confirmation, *, business_factory=TikTokCreatorAPI, api_factory=TikTokFileAPI):
    async with admission():
        row = await owned(db, user, identifier)
        frozen = plan(row)
        if not secrets.compare_digest(row["confirmation_digest"], confirmation):
            raise problem("tiktok_file_approval_changed", "الاعتماد لا يطابق معاينة الفيديو.")
        if row["status"] != "previewed":
            raise problem("tiktok_file_already_submitted", "سبق بدء العملية؛ حدّث حالتها قبل أي إرسال جديد.")
        if aware(row["expires_at"]) <= now():
            raise problem("tiktok_file_preview_expired", "انتهت المعاينة؛ راجع الملف والإعدادات مجددًا.")
        value = FilePostInput(**frozen["input"])
        _, token, fresh = await context(db, user, value.creator_ref, delivery=value.delivery, business_factory=business_factory, api_factory=api_factory)
        if fresh != frozen["proof"] or body_for(value, fresh) != frozen["body"]:
            raise problem("tiktok_file_context_changed", "تغير ربط الحساب أو إعداداته؛ أنشئ معاينة جديدة.")
        fence = _signature(user + ":" + value.creator_ref + ":" + value.file_manifest_sha256, "tiktok-file-fence:")
        try:
            await mongo(db[FENCES].insert_one({"_id": fence, "user_id": user, "proposal_id": identifier, "active": True}))
        except DuplicateKeyError:
            replaced = await mongo(db[FENCES].update_one({"_id": fence, "user_id": user, "active": False}, {"$set": {"proposal_id": identifier, "active": True}}))
            if replaced.modified_count != 1:
                raise problem("tiktok_file_outcome_unproven", "هناك رفع سابق لهذا الملف لم تثبت نتيجته؛ راجع سجله.") from None
        submitted = False
        try:
            claimed = await mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user, "status": "previewed"}, {"$set": {"status": "validating", "fence_id": fence}}))
            if claimed.modified_count != 1:
                raise problem("tiktok_file_already_submitted", "سبق بدء العملية.")
            credential = await mongo(db[CREDENTIALS].find_one({"_id": value.creator_ref, "user_id": user, "status": "connected", "auth_epoch": fresh["auth_epoch"]}, {"_id": 1}, max_time_ms=1500))
            if not credential or not enabled():
                raise problem("tiktok_file_authorization_changed", "تغير تفويض الحساب؛ لم يُرسل الملف.")
            submitted = True
            await mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user}, {"$set": {"status": "submitted", "submitted_at": now()}}))
            endpoint = "/v2/post/publish/video/init/" if value.delivery == "direct" else "/v2/post/publish/inbox/video/init/"
            async with api_factory() as api:
                result = await api.call("POST", endpoint, token=token, body=frozen["body"])
            publish_id = result.get("publish_id")
            if not isinstance(publish_id, str) or not re.fullmatch(r"[a-zA-Z0-9_~.:-]{1,128}", publish_id):
                raise problem("tiktok_file_initialization_unproven", "لم تثبت نتيجة تهيئة TikTok؛ لا تكرر الإرسال.", 502)
            await mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user}, {"$set": {"publish_id_encrypted": encrypt_tiktok_token(publish_id)}}))
            destination = upload_url(result.get("upload_url"))
            fields = {"status": "awaiting_upload", "upload_url_encrypted": encrypt_tiktok_token(destination), "upload_expires_at": now() + timedelta(minutes=55), "uploaded_bytes": 0}
            await mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user}, {"$set": fields}))
            return {**public({**row, **fields}), "transfer": {"url": destination, "expires_at": fields["upload_expires_at"]}}
        except BaseException as error:
            rejected = isinstance(error, HTTPException) and isinstance(error.detail, dict) and error.detail.get("native_rejected") is True
            status = "blocked" if not submitted or rejected else "uncertain"
            detail = error.detail if isinstance(error, HTTPException) and isinstance(error.detail, dict) else {}
            diagnostic = initialization_diagnostic({"code": detail.get("code"),
                "provider_code": detail.get("provider_code") if rejected else None,
                "stage": "native_initialization" if submitted else "before_initialization"})
            async def persist():
                await mongo(db[PROPOSALS].update_one({"_id": identifier, "user_id": user, "status": {"$in": ["previewed", "validating", "submitted"]}}, {"$set": {"status": status, "initialization_diagnostic": diagnostic}}))
                if status == "blocked":
                    await mongo(db[FENCES].update_one({"_id": fence, "user_id": user, "proposal_id": identifier}, {"$set": {"active": False}}))
            await asyncio.shield(persist())
            raise


async def reconcile(db, user, identifier, *, business_factory=TikTokCreatorAPI, api_factory=TikTokFileAPI):
    async with admission():
        row = await owned(db, user, identifier)
        if not row.get("publish_id_encrypted"):
            return public(row)
        value = FilePostInput(**plan(row)["input"])
        async with business_factory() as business:
            _, token, _ = await verified_creator(db, user, value.creator_ref, business, "video.publish" if value.delivery == "direct" else "video.upload")
        async with api_factory() as api:
            native = await api.call("POST", "/v2/post/publish/status/fetch/", token=token, body={"publish_id": decrypt_tiktok_token(row["publish_id_encrypted"])})
        state = native.get("status")
        if state not in {"PROCESSING_UPLOAD", "SEND_TO_USER_INBOX", "PUBLISH_COMPLETE", "FAILED"} or state == "SEND_TO_USER_INBOX" and value.delivery != "draft":
            raise problem("tiktok_file_status_unproven", "تعذر إثبات حالة الملف لدى TikTok.", 502)
        amount = native.get("uploaded_bytes")
        if type(amount) is not int or not 0 <= amount <= value.file_size or state in {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"} and amount != value.file_size:
            raise problem("tiktok_file_progress_unproven", "تعذر إثبات مقدار الفيديو المستلم.", 502)
        ids = native.get("publicaly_available_post_id", [])
        if not isinstance(ids, list) or len(ids) > 20 or any(type(i) not in (str, int) or not re.fullmatch(r"[0-9]{1,30}", str(i)) for i in ids):
            raise problem("tiktok_file_post_identity_unproven", "تعذر إثبات معرّف المنشور.", 502)
        if state == "FAILED": status = "failed"
        elif state == "SEND_TO_USER_INBOX" and value.delivery == "draft": status = "draft_delivered"
        elif state == "PUBLISH_COMPLETE":
            status = "published_public" if ids else "published_private" if value.delivery == "direct" and value.privacy_level != "PUBLIC_TO_EVERYONE" else "complete_pending_ids"
        else: status = "awaiting_upload" if state == "PROCESSING_UPLOAD" and amount < value.file_size else "processing"
        fields = {"status": status, "uploaded_bytes": amount, "public_post_ids": [str(i) for i in ids], "last_checked_at": now()}
        query = {"_id": identifier, "user_id": user, "status": row["status"], "last_checked_at": row.get("last_checked_at")}
        stored = await mongo(db[PROPOSALS].update_one(query, {"$set": fields}))
        if stored.matched_count != 1:
            return public(await owned(db, user, identifier))
        if status in TERMINAL and row.get("fence_id"):
            await mongo(db[FENCES].update_one({"_id": row["fence_id"], "user_id": user, "proposal_id": identifier}, {"$set": {"active": False}}))
        return public({**row, **fields})


async def transfer(db, user, identifier, fingerprint, **factories):
    # A lost response or interrupted browser never initializes another post.
    # Native byte progress must prove an exact original chunk boundary first.
    await reconcile(db, user, identifier, **factories)
    row = await owned(db, user, identifier)
    frozen = plan(row)
    if not secrets.compare_digest(frozen["input"]["file_manifest_sha256"], fingerprint):
        raise problem("tiktok_file_selected_file_changed", "اختر نفس ملف المعاينة لاستكمال العملية.")
    amount = row.get("uploaded_bytes")
    chunk = frozen["body"]["source_info"]["chunk_size"]
    if row["status"] != "awaiting_upload" or type(amount) is not int or amount % chunk or aware(row.get("upload_expires_at")) <= now():
        raise problem("tiktok_file_resume_unproven", "لا يمكن استكمال الرفع الآن؛ راجع حالة TikTok دون إنشاء طلب مكرر.")
    return {**public(row), "transfer": {"url": upload_url(decrypt_tiktok_token(row["upload_url_encrypted"])),
        "expires_at": row["upload_expires_at"], "offset": amount}}


def attach_file_publishing_routes(router, db, current_user, require_owner):
    routes = APIRouter(prefix="/tiktok/content/files", route_class=BoundedCreatorJSONRoute)

    @routes.post("/creators/{creator_ref}/verify")
    async def verify(creator_ref: str, response: Response, user: dict = Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        owner = str(require_owner(user)["id"])
        if not re.fullmatch(r"[a-f0-9]{64}", creator_ref):
            raise problem("tiktok_file_creator_invalid", "اختر حساب النشر.", 422)
        async with admission():
            _, _, proof = await context(db, owner, creator_ref)
        return {"ready": True, "creator_ref": creator_ref, "settings": proof["settings"], "max_video_bytes": MAX_VIDEO_BYTES}

    @routes.post("/proposals")
    async def prepare(value: FilePostInput, response: Response, user: dict = Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        return await preview(db, str(require_owner(user)["id"]), value)

    @routes.post("/proposals/{identifier}/approve-and-initialize")
    async def initialize(identifier: str, approval: Approval, response: Response, user: dict = Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        return await execute(db, str(require_owner(user)["id"]), identifier, approval.confirmation_digest)

    @routes.get("/proposals/{identifier}")
    async def read(identifier: str, response: Response, user: dict = Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        return public(await owned(db, str(require_owner(user)["id"]), identifier))

    @routes.post("/proposals/{identifier}/status")
    async def status(identifier: str, response: Response, user: dict = Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        return await reconcile(db, str(require_owner(user)["id"]), identifier)

    @routes.post("/proposals/{identifier}/transfer")
    async def resume(identifier: str, value: TransferRequest, response: Response, user: dict = Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        return await transfer(db, str(require_owner(user)["id"]), identifier, value.file_manifest_sha256)

    @routes.get("/proposals")
    async def history(response: Response, limit: int = Query(default=12, ge=1, le=20), user: dict = Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        owner = str(require_owner(user)["id"])
        rows = await mongo(db[PROPOSALS].find({"user_id": owner}).sort("created_at", -1).limit(limit).max_time_ms(1500).to_list(limit))
        return {"items": [public(row) for row in rows]}

    router.include_router(routes)
