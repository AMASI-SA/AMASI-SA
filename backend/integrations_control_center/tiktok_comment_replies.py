"""Explicit owner-reviewed public text replies. No automatic AI, retries or media.

The global CI channel stays receive-only. This narrow manual operation binds an
encrypted preview to the current native comment and suggestion version. A unique
attempt per source comment survives timeouts/restarts and prevents double posts.
"""
from datetime import timedelta
import hashlib
import hmac
import json
import re
import secrets

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from customer_identity import decrypt_private_payload, encrypt_private_payload
from customer_intelligence.reply_suggestions import (
    CustomerIntelligenceActor, ReplySuggestionService, ReplySuggestionError,
    REPLY_SUGGESTIONS_COLLECTION,
)
from .tiktok_comments_ingress import BINDINGS, POST_ID, _context
from .tiktok_creator_accounts import aware, now
from .tiktok_creator_api import BoundedCreatorJSONRoute, TikTokCreatorAPI, admission, mongo, problem

PROPOSALS = "mezan_tiktok_comment_reply_proposals_v1"
ATTEMPTS = "mezan_tiktok_comment_reply_attempts_v1"


class ReplyPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    conversation_id: str = Field(min_length=1, max_length=200)
    suggestion_id: str = Field(min_length=1, max_length=200)
    version: int = Field(strict=True, ge=1)
    text: str = Field(min_length=1, max_length=1200)


class ReplyApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReplyHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    conversation_id: str = Field(min_length=1, max_length=200)


def _actor(user_id):
    return CustomerIntelligenceActor(actor_id=user_id, owner_user_id=user_id, permissions=frozenset(), is_owner=True)


def _scope(conversation):
    return {key: conversation[key] for key in ("user_id", "merchant_id", "conversation_id")}


def _attempt_key(conversation, source):
    return hashlib.sha256(json.dumps([conversation["user_id"], conversation["merchant_id"],
        conversation["channel_id"], source["message_id"]], separators=(",", ":")).encode()).hexdigest()


async def _basis(db, user_id, payload):
    service = ReplySuggestionService(db)
    actor = _actor(user_id)
    try:
        conversation = await mongo(service._conversation(actor=actor, conversation_id=payload.conversation_id))
        pending = await mongo(service.latest(actor=actor, conversation_id=payload.conversation_id, pending_only=True))
        if not pending or pending.suggestion_id != payload.suggestion_id or pending.version != payload.version:
            raise problem("tiktok_reply_draft_changed", "تغيرت المسودة أو المحادثة؛ أعد المعاينة.")
        timeline = await mongo(service._timeline(conversation=conversation))
        source = service._latest_customer_message(timeline)
    except ReplySuggestionError:
        raise problem("tiktok_reply_draft_unavailable", "المسودة غير متاحة لهذا الحساب.", 404) from None
    if not source or source.get("source_event") != "tiktok.comments.public.manual" or service._employee_replied_after(timeline, source):
        raise problem("tiktok_reply_native_comment_required", "يلزم تعليق عام مستورد مباشرة من TikTok دون رد أحدث.")
    content = decrypt_private_payload(source.get("content_ciphertext")) or {}
    data = content.get("payload", {})
    ids = data.get("tiktok_comment", {})
    if data.get("surface") != "comment" or not isinstance(data.get("text"), str) or any(
        not isinstance(ids.get(key), str) or not re.fullmatch(POST_ID, ids[key]) for key in ("video_id", "comment_id")
    ):
        raise problem("tiktok_reply_source_unproven", "تعذر إثبات التعليق الأصلي.")
    return conversation, source, {"video_id": ids["video_id"], "comment_id": ids["comment_id"], "text": data["text"]}


async def _provider_context(db, user_id, conversation, source, api):
    bindings = await mongo(db[BINDINGS].find({"user_id": user_id, "merchant_id": conversation["merchant_id"],
        "channel_id": conversation["channel_id"], "status": "connected"}).limit(2).max_time_ms(1500).to_list(2))
    if len(bindings) != 1:
        raise problem("tiktok_reply_binding_unproven", "أعد التحقق من ربط حساب التعليقات.")
    context, token, business_id = await _context(db, user_id, bindings[0]["_id"], api, required_scope="comment.list.manage")
    if context.merchant_id != conversation["merchant_id"] or context.channel_id != conversation["channel_id"]:
        raise problem("tiktok_reply_binding_changed", "تغير حساب التعليقات؛ أعد المعاينة.")
    videos = await api.call("GET", "/business/video/list/", token=token, params={"business_id": business_id,
        "fields": '["item_id","media_type"]', "filters": json.dumps({"video_ids": [source["video_id"]]}), "max_count": 1})
    rows = videos.get("videos")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("item_id") != source["video_id"] or rows[0].get("media_type") != "VIDEO":
        raise problem("tiktok_reply_video_unproven", "لم تثبت ملكية الفيديو للحساب الحالي.")
    data = await api.call("GET", "/business/comment/list/", token=token, params={"business_id": business_id,
        "video_id": source["video_id"], "comment_ids": json.dumps([source["comment_id"]]),
        "status": "PUBLIC", "include_replies": "false", "max_count": 1, "cursor": 0})
    rows = data.get("comments")
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or rows[0].get("comment_id") != source["comment_id"] or rows[0].get("video_id") != source["video_id"]
            or rows[0].get("status") != "PUBLIC" or rows[0].get("owner") is not False
            or rows[0].get("parent_comment_id") or rows[0].get("text") != source["text"]):
        raise problem("tiktok_reply_comment_changed", "التعليق غير متاح أو تغير لدى TikTok؛ أعد استيراده.")
    return token, business_id


def _public(row):
    private = decrypt_private_payload(row.get("preview_ciphertext")) or {}
    return {"proposal_id": row["_id"], "status": row["status"], "text": private.get("text", ""),
        "source_text": private.get("source", {}).get("text", ""), "expires_at": row["expires_at"],
        "confirmation_digest": row["confirmation_digest"], "automatic_reply_enabled": False,
        "public_visibility_verified": row.get("public_visibility_verified") is True}


async def preview_reply(db, user_id, payload):
    if any(ord(char) < 32 and char not in "\n\t" for char in payload.text):
        raise problem("tiktok_reply_invalid_text", "أرسل نصًا واضحًا دون رموز تحكم.", 422)
    async with admission(), TikTokCreatorAPI() as api:
        conversation, source, target = await _basis(db, user_id, payload)
        attempt_key = _attempt_key(conversation, source)
        if await mongo(db[ATTEMPTS].find_one({"_id": attempt_key}, max_time_ms=1500)):
            raise problem("tiktok_reply_already_attempted", "سبق إرسال هذا التعليق أو نتيجته غير مؤكدة؛ راجع سجل الرد ولا تكرره.")
        await mongo(db[PROPOSALS].create_index([("user_id", 1), ("created_at", -1)], name="tiktok_reply_owner_recent", maxTimeMS=1500))
        count = await mongo(db[PROPOSALS].count_documents({"user_id": user_id, "created_at": {"$gt": now() - timedelta(minutes=1)}}, limit=6, maxTimeMS=1500))
        if count >= 6:
            raise problem("tiktok_reply_preview_throttled", "انتظر دقيقة قبل معاينات إضافية.", 429)
        await _provider_context(db, user_id, conversation, target, api)
        proposal_id = secrets.token_hex(16)
        private = {**payload.model_dump(), "source": target}
        digest = hashlib.sha256((proposal_id + json.dumps(private, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
        row = {"_id": proposal_id, **_scope(conversation), "channel_id": conversation["channel_id"],
            "attempt_key": attempt_key, "status": "previewed", "created_at": now(), "expires_at": now() + timedelta(minutes=10),
            "confirmation_digest": digest, "preview_ciphertext": encrypt_private_payload(private)}
        await mongo(db[PROPOSALS].insert_one(row))
        return _public(row)


async def _proposal(db, user_id, proposal_id):
    row = await mongo(db[PROPOSALS].find_one({"_id": proposal_id, "user_id": user_id}, max_time_ms=1500))
    if not row:
        raise problem("tiktok_reply_not_found", "الرد غير متاح لهذا الحساب.", 404)
    return row


async def execute_reply(db, user_id, proposal_id, approval):
    async with admission(), TikTokCreatorAPI() as api:
        row = await _proposal(db, user_id, proposal_id)
        if not hmac.compare_digest(approval.confirmation_digest, row["confirmation_digest"]):
            raise problem("tiktok_reply_approval_changed", "اعتمد النص الحالي من المعاينة.")
        if row["status"] != "previewed":
            return _public(row)  # Never retry a consumed provider operation.
        if aware(row["expires_at"]) <= now():
            raise problem("tiktok_reply_preview_expired", "انتهت المعاينة؛ أنشئ معاينة جديدة.")
        private = decrypt_private_payload(row["preview_ciphertext"])
        payload = ReplyPreview(**{key: private[key] for key in ReplyPreview.model_fields})
        conversation, source, target = await _basis(db, user_id, payload)
        if _scope(conversation) != {key: row[key] for key in _scope(conversation)} or _attempt_key(conversation, source) != row["attempt_key"] or target != private["source"]:
            raise problem("tiktok_reply_source_changed", "تغير أساس الرد؛ أعد المعاينة.")
        token, business_id = await _provider_context(db, user_id, conversation, target, api)
        # All remote read/permission checks precede the unique write reservation.
        await _basis(db, user_id, payload)
        try:
            await mongo(db[ATTEMPTS].insert_one({"_id": row["attempt_key"], **_scope(conversation),
                "proposal_id": proposal_id, "created_at": now(), "status": "reserved"}))
        except DuplicateKeyError:
            raise problem("tiktok_reply_already_attempted", "سبق بدء إرسال هذا التعليق؛ لا تكرره.") from None
        reserved = await mongo(db[PROPOSALS].update_one({"_id": proposal_id, "user_id": user_id, "status": "previewed"},
            {"$set": {"status": "sending", "approved_at": now(), "approved_by": user_id}}))
        if reserved.modified_count != 1:
            raise problem("tiktok_reply_already_attempted", "بدأت عملية الرد بالفعل؛ راجع حالتها.")
        row["status"] = "sending"
        try:
            # Reserve the exact draft version. A concurrent review/regeneration
            # cannot change the approved text and still reach the provider.
            claimed = await mongo(db[REPLY_SUGGESTIONS_COLLECTION].update_one({**_scope(conversation),
                "suggestion_id": payload.suggestion_id, "source_message_id": source["message_id"],
                "version": payload.version, "status": "pending_approval", "generation_status": "ready"},
                {"$set": {"generation_status": "sending"}, "$inc": {"version": 1}}))
            if claimed.modified_count != 1:
                raise problem("tiktok_reply_draft_changed", "تغيرت المسودة قبل الإرسال.")
            data = await api.call("POST", "/business/comment/reply/create/", token=token,
                body={"business_id": business_id, "video_id": target["video_id"], "comment_id": target["comment_id"], "text": payload.text})
            if (not isinstance(data.get("comment_id"), str) or not re.fullmatch(POST_ID, data["comment_id"])
                    or data["comment_id"] == target["comment_id"] or data.get("parent_comment_id") != target["comment_id"]
                    or data.get("video_id") != target["video_id"] or data.get("text") != payload.text):
                raise problem("tiktok_reply_receipt_unproven", "لم تثبت نتيجة الرد؛ لا تعاود الإرسال.", 502)
            row.update(status="accepted", receipt_ciphertext=encrypt_private_payload({key: data[key] for key in ("comment_id", "parent_comment_id", "video_id", "text")}))
        except Exception:
            row["status"] = "uncertain"
        # If persistence fails, the durable unique attempt still prevents retry.
        await mongo(db[PROPOSALS].update_one({"_id": proposal_id, "user_id": user_id, "status": "sending"},
            {"$set": {"status": row["status"], "receipt_ciphertext": row.get("receipt_ciphertext"), "updated_at": now()}}))
        await mongo(db[ATTEMPTS].update_one({"_id": row["attempt_key"]}, {"$set": {"status": row["status"]}}))
        if row["status"] == "accepted":
            await mongo(db[REPLY_SUGGESTIONS_COLLECTION].update_one({**_scope(conversation), "suggestion_id": payload.suggestion_id,
                "version": payload.version + 1, "status": "pending_approval", "generation_status": "sending"},
                {"$set": {"status": "reviewed", "generation_status": "ready", "review_decision": "approve",
                    "reviewed_by": user_id, "reviewed_at": now(), "text_ciphertext": encrypt_private_payload({"text": payload.text}),
                    "tiktok_reply_proposal_id": proposal_id}, "$inc": {"version": 1}}))
        return _public(row)


async def reply_status(db, user_id, proposal_id):
    async with admission(), TikTokCreatorAPI() as api:
        row = await _proposal(db, user_id, proposal_id)
        if row["status"] not in {"accepted", "published_public", "hidden"}:
            return _public(row)
        reserved = await mongo(db[PROPOSALS].update_one({"_id": proposal_id, "user_id": user_id,
            "$or": [{"next_check_at": {"$exists": False}}, {"next_check_at": {"$lte": now()}}]},
            {"$set": {"next_check_at": now() + timedelta(seconds=15)}}))
        if reserved.modified_count != 1:
            raise problem("tiktok_reply_status_throttled", "انتظر 15 ثانية بين التحققين.", 429)
        receipt = decrypt_private_payload(row.get("receipt_ciphertext"))
        bindings = await mongo(db[BINDINGS].find({"user_id": user_id, "merchant_id": row["merchant_id"], "channel_id": row["channel_id"], "status": "connected"}).limit(2).max_time_ms(1500).to_list(2))
        if len(bindings) != 1:
            raise problem("tiktok_reply_binding_unproven", "أعد التحقق من ربط حساب التعليقات.")
        context, token, business_id = await _context(db, user_id, bindings[0]["_id"], api)
        if context.merchant_id != row["merchant_id"] or context.channel_id != row["channel_id"]:
            raise problem("tiktok_reply_binding_changed", "تغير حساب التعليقات.")
        data = await api.call("GET", "/business/comment/list/", token=token, params={"business_id": business_id,
            "video_id": receipt["video_id"], "comment_ids": json.dumps([receipt["comment_id"]]), "status": "ALL",
            "include_replies": "false", "max_count": 1, "cursor": 0})
        rows = data.get("comments")
        if not isinstance(rows, list) or len(rows) > 1:
            raise problem("tiktok_reply_visibility_unproven", "تعذر إثبات ظهور الرد لدى TikTok.", 502)
        row.update(status="accepted", public_visibility_verified=False)
        if rows:
            reply = rows[0]
            if not isinstance(reply, dict) or any(reply.get(key) != receipt[key] for key in receipt) or reply.get("owner") is not True or reply.get("status") not in {"PUBLIC", "HIDDEN"}:
                raise problem("tiktok_reply_visibility_unproven", "استجابة TikTok لا تطابق الرد المعتمد.", 502)
            row.update(status="published_public" if reply["status"] == "PUBLIC" else "hidden", public_visibility_verified=reply["status"] == "PUBLIC")
        await mongo(db[PROPOSALS].update_one({"_id": proposal_id, "user_id": user_id}, {"$set": {
            "status": row["status"], "public_visibility_verified": row["public_visibility_verified"], "last_checked_at": now()}}))
        return _public(row)


async def reply_history(db, user_id, conversation_id):
    try:
        conversation = await mongo(ReplySuggestionService(db)._conversation(actor=_actor(user_id), conversation_id=conversation_id))
    except ReplySuggestionError:
        raise problem("tiktok_reply_not_found", "المحادثة غير متاحة.", 404) from None
    rows = await mongo(db[PROPOSALS].find(_scope(conversation)).sort("created_at", -1).limit(20).max_time_ms(1500).to_list(20))
    return {"items": [_public(row) for row in rows], "limit": 20, "automatic_reply_enabled": False}


def attach_tiktok_reply_routes(router, db, current_user, require_owner):
    replies = APIRouter(prefix="/tiktok/content/comment-replies", route_class=BoundedCreatorJSONRoute)

    @replies.post("/preview")
    async def preview(payload: ReplyPreview, response: Response, user: dict = Depends(current_user)):
        owner = require_owner(user)
        response.headers["Cache-Control"] = "no-store"
        return await preview_reply(db, str(owner["id"]), payload)

    @replies.post("/history")
    async def history(payload: ReplyHistory, response: Response, user: dict = Depends(current_user)):
        owner = require_owner(user)
        response.headers["Cache-Control"] = "no-store"
        return await reply_history(db, str(owner["id"]), payload.conversation_id)

    @replies.post("/{proposal_id}/execute")
    async def execute(proposal_id: str, payload: ReplyApproval, response: Response, user: dict = Depends(current_user)):
        owner = require_owner(user)
        if not re.fullmatch(r"[a-f0-9]{32}", proposal_id):
            raise problem("tiktok_reply_not_found", "الرد غير متاح.", 404)
        response.headers["Cache-Control"] = "no-store"
        return await execute_reply(db, str(owner["id"]), proposal_id, payload)

    @replies.post("/{proposal_id}/status")
    async def status(proposal_id: str, response: Response, user: dict = Depends(current_user)):
        owner = require_owner(user)
        if not re.fullmatch(r"[a-f0-9]{32}", proposal_id):
            raise problem("tiktok_reply_not_found", "الرد غير متاح.", 404)
        response.headers["Cache-Control"] = "no-store"
        return await reply_status(db, str(owner["id"]), proposal_id)

    router.include_router(replies)
