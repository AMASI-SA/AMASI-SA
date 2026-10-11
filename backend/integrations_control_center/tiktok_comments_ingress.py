"""Owner-initiated native public comment reads into the encrypted CI gateway.

One metadata page per request. No webhook claim, media fetch, polling, provider
write, or automatic external AI analysis is performed by this adapter.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pymongo.errors import DuplicateKeyError

from customer_intelligence.channel_gateway import (
    ChannelGateway, ChannelGatewayError, NormalizedInboundMessage,
    TrustedChannelContext, build_channel_account_key,
)
from customer_intelligence.foundation import (
    CHANNELS_COLLECTION, ChannelRecord, ensure_customer_intelligence_foundation_indexes,
)
from .tiktok_creator_accounts import now, verified_creator
from .tiktok_creator_api import BoundedCreatorJSONRoute, TikTokCreatorAPI, admission, mongo, problem

BINDINGS = "mezan_tiktok_comment_bindings_v1"
PAGE_SIZE = 20
REF = r"^[a-f0-9]{64}$"
POST_ID = r"^[0-9]{1,30}$"
MAX_CURSOR = 9_007_199_254_740_991
READ_ONLY = {"receive_only": True, "provider_write_allowed": False, "ai_auto_reply_allowed": False, "ai_analysis_requested": False, "messaging_enabled": False}


class CommentConnect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_receive_only: bool = Field(strict=True)


class CommentPull(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_id: str = Field(pattern=POST_ID)
    cursor: int = Field(default=0, strict=True, ge=0, le=MAX_CURSOR)


async def _merchant(db, user_id):
    cursor = db.salla_integrations.find({"user_id": user_id, "status": "connected"}, {"store_id": 1}).limit(2).max_time_ms(1500)
    rows = await mongo(cursor.to_list(2))
    # Two rows are ambiguous even if the first two happen to share an ID;
    # a bounded read must never assume a third store does not exist.
    value = str(rows[0].get("store_id") or "").strip() if len(rows) == 1 else ""
    if not value:
        raise problem("tiktok_comments_unique_store_required", "يلزم متجر سلة واحد متصل لتحديد صندوق التعليقات.")
    return value


def _safe_channel(row, *, user_id, merchant_id, account_key):
    return bool(row and row.get("user_id") == user_id and row.get("merchant_id") == merchant_id
                and row.get("provider") == "tiktok" and row.get("external_account_key") == account_key
                and row.get("status") == "connected" and row.get("ingress_enabled") is True
                and row.get("egress_mode") == "disabled" and row.get("send_allowed") is False
                and row.get("ai_auto_reply_allowed") is False and row.get("channel_id"))


async def connect_comments(db, user_id, creator_ref, payload):
    if payload.confirm_receive_only is not True:
        raise problem("tiktok_comments_confirmation_required", "اعتمد استيراد التعليقات العامة إلى ميزان للقراءة ومسودات الردود فقط.", 422)
    async with admission(), TikTokCreatorAPI() as api:
        creator, _, business_id = await verified_creator(db, user_id, creator_ref, api, "comment.list")
        if "video.list" not in creator["scopes"]:
            raise problem("tiktok_creator_missing_permission", "يلزم تفويض قراءة منشورات الحساب لإثبات ملكية الفيديو.")
        merchant_id = await _merchant(db, user_id)
        account_key = build_channel_account_key("tiktok", business_id)
        channel_id = "tiktok-" + account_key.rsplit(":", 1)[-1][:24]
        await mongo(ensure_customer_intelligence_foundation_indexes(db))
        channels = db[CHANNELS_COLLECTION]
        old = await mongo(channels.find_one({"provider": "tiktok", "external_account_key": account_key}, max_time_ms=1500))
        if old:
            if not _safe_channel(old, user_id=user_id, merchant_id=merchant_id, account_key=account_key):
                raise problem("tiktok_comments_binding_conflict", "الحساب مرتبط بصندوق آخر أو إعداداته لا تسمح باستقبال التعليقات.")
            channel_id = old["channel_id"]
        else:
            instant = now()
            record = ChannelRecord(user_id=user_id, merchant_id=merchant_id, channel_id=channel_id,
                                   provider="tiktok", external_account_key=account_key, status="connected",
                                   ingress_enabled=True, created_at=instant, updated_at=instant).model_dump()
            try:
                await mongo(channels.insert_one(record))
            except DuplicateKeyError:
                raise problem("tiktok_comments_binding_conflict", "تغير ربط حساب التعليقات؛ أعد التحقق.") from None
        existing = await mongo(db[BINDINGS].find_one({"_id": creator_ref}, max_time_ms=1500))
        if existing and (existing.get("user_id") != user_id or existing.get("merchant_id") != merchant_id or existing.get("channel_id") != channel_id):
            raise problem("tiktok_comments_binding_conflict", "لا يمكن نقل ربط التعليقات إلى صندوق آخر.")
        try:
            await mongo(db[BINDINGS].update_one({"_id": creator_ref, "user_id": user_id, "merchant_id": merchant_id, "channel_id": channel_id},
                                              {"$set": {"status": "connected", "updated_at": now()}, "$setOnInsert": {"created_at": now()}}, upsert=True))
        except DuplicateKeyError:
            raise problem("tiktok_comments_binding_conflict", "تغير ربط حساب التعليقات؛ أعد التحقق.") from None
        return {"status": "connected", "source": "owner_initiated_api", "creator_ref": creator_ref, "channel_ref": channel_id, **READ_ONLY}


async def _context(db, user_id, creator_ref, api):
    binding = await mongo(db[BINDINGS].find_one({"_id": creator_ref, "user_id": user_id, "status": "connected"}, max_time_ms=1500))
    if not binding:
        raise problem("tiktok_comments_not_connected", "اربط التعليقات بصندوق ميزان أولًا.")
    creator, token, business_id = await verified_creator(db, user_id, creator_ref, api, "comment.list")
    if "video.list" not in creator["scopes"]:
        raise problem("tiktok_creator_missing_permission", "الحساب لم يمنح صلاحية قراءة منشوراته.")
    merchant_id = await _merchant(db, user_id)
    if binding.get("merchant_id") != merchant_id:
        raise problem("tiktok_comments_binding_conflict", "تغير متجر التعليقات؛ راجع الربط.")
    account_key = build_channel_account_key("tiktok", business_id)
    channel = await mongo(db[CHANNELS_COLLECTION].find_one({"user_id": user_id, "merchant_id": merchant_id, "channel_id": binding["channel_id"], "provider": "tiktok"}, max_time_ms=1500))
    if not _safe_channel(channel, user_id=user_id, merchant_id=merchant_id, account_key=account_key):
        raise problem("tiktok_comments_binding_conflict", "ربط التعليقات غير جاهز للاستقبال الآمن.")
    context = TrustedChannelContext(user_id=user_id, merchant_id=merchant_id, channel_id=binding["channel_id"], provider="tiktok")
    return context, token, business_id


async def _reserve_read(db, user_id, creator_ref, kind):
    instant = now()
    field = kind + "_next_read_at"
    result = await mongo(db[BINDINGS].update_one({"_id": creator_ref, "user_id": user_id, "status": "connected", "$or": [{field: {"$exists": False}}, {field: {"$lte": instant}}]},
                                               {"$set": {field: instant + timedelta(seconds=15)}}))
    if result.modified_count != 1:
        raise problem("tiktok_comments_read_throttled", "انتظر 15 ثانية بين قراءة دفعتين من النوع نفسه.", 429)


def _bad_page():
    return problem("tiktok_comments_response_unproven", "تعذر إثبات بيانات TikTok ضمن حدود الدفعة.", 502)


def _page(data, field, *, cursor=None, comment=False):
    rows, next_cursor, more = data.get(field), data.get("cursor"), data.get("has_more")
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE or type(more) is not bool or type(next_cursor) is not int or not 0 <= next_cursor <= MAX_CURSOR:
        raise _bad_page()
    if more and (not rows or next_cursor == 0 or (cursor is not None and (next_cursor <= cursor if comment else next_cursor >= cursor))):
        raise _bad_page()
    return rows, next_cursor, more


def _post(row):
    if not isinstance(row, dict) or not isinstance(row.get("item_id"), str) or not re.fullmatch(POST_ID, row["item_id"]):
        raise _bad_page()
    kind, caption = row.get("media_type"), row.get("caption", "")
    if kind not in {"VIDEO", "PHOTO"} or not isinstance(caption, str) or len(caption) > 4000:
        raise _bad_page()
    return {"video_id": row["item_id"], "media_type": kind, "caption": caption[:120]}


async def list_comment_posts(db, user_id, creator_ref, cursor=None):
    async with admission(), TikTokCreatorAPI() as api:
        _, token, business_id = await _context(db, user_id, creator_ref, api)
        await _reserve_read(db, user_id, creator_ref, "posts")
        params = {"business_id": business_id, "fields": json.dumps(["item_id", "media_type", "caption"], separators=(",", ":")), "max_count": PAGE_SIZE}
        if cursor is not None:
            params["cursor"] = cursor
        data = await api.call("GET", "/business/video/list/", token=token, params=params)
        rows, next_cursor, more = _page(data, "videos", cursor=cursor)
        posts = [_post(row) for row in rows]
        return {"items": [row for row in posts if row["media_type"] == "VIDEO"], "inspected": len(posts), "has_more": more, "next_cursor": next_cursor if more else None, "limit": PAGE_SIZE, **READ_ONLY}


def _comment(row, video_id):
    if not isinstance(row, dict) or row.get("video_id") != video_id or type(row.get("owner")) is not bool or row.get("status") not in {"PUBLIC", "HIDDEN"}:
        raise _bad_page()
    if row["owner"] or row["status"] != "PUBLIC" or row.get("parent_comment_id"):
        return None
    text = row.get("text")
    if not isinstance(text, str) or len(text) > 4000:
        raise _bad_page()
    if not text.strip():
        return None  # Image-only comments stay in TikTok; no media is fetched.
    comment_id, identity, created = row.get("comment_id"), row.get("unique_identifier"), row.get("create_time")
    if not isinstance(comment_id, str) or not re.fullmatch(POST_ID, comment_id) or not isinstance(identity, str) or not 1 <= len(identity) <= 512 or any(ord(c) < 32 for c in identity) or not identity.strip():
        raise _bad_page()
    if not isinstance(created, str) or not re.fullmatch(r"[0-9]{1,11}", created):
        raise _bad_page()
    seconds = int(created)
    if not 946684800 <= seconds <= int(now().timestamp()) + 300:
        raise _bad_page()
    display = row.get("display_name", "")
    if not isinstance(display, str) or len(display) > 200:
        raise _bad_page()
    return NormalizedInboundMessage(provider="tiktok", external_conversation_id=SecretStr(video_id + ":" + identity),
                                    external_message_id=SecretStr(comment_id), external_customer_id=SecretStr(identity),
                                    customer_profile={"name": display[:120]} if display else {}, content_type="text",
                                    content_payload={"text": text, "surface": "comment", "tiktok_comment": {"video_id": video_id, "comment_id": comment_id}},
                                    occurred_at=datetime.fromtimestamp(seconds, timezone.utc), source_event="tiktok.comments.public.manual",
                                    analysis_status="not_requested")


async def pull_comments(db, user_id, creator_ref, payload):
    async with admission(), TikTokCreatorAPI() as api:
        context, token, business_id = await _context(db, user_id, creator_ref, api)
        await _reserve_read(db, user_id, creator_ref, "comments")
        # Never accept an arbitrary video ID from the browser as ownership.
        owned = await api.call("GET", "/business/video/list/", token=token, params={"business_id": business_id,
                               "fields": json.dumps(["item_id", "media_type"], separators=(",", ":")),
                               "filters": json.dumps({"video_ids": [payload.video_id]}, separators=(",", ":")), "max_count": 1})
        rows = owned.get("videos")
        if not isinstance(rows, list) or len(rows) != 1 or _post(rows[0])["video_id"] != payload.video_id or rows[0]["media_type"] != "VIDEO":
            raise problem("tiktok_comments_owned_video_required", "تعذر إثبات أن الفيديو المحدد يخص حساب TikTok الحالي.")
        data = await api.call("GET", "/business/comment/list/", token=token, params={"business_id": business_id, "video_id": payload.video_id,
                              "status": "PUBLIC", "include_replies": "false", "sort_field": "create_time", "sort_order": "desc", "cursor": payload.cursor, "max_count": PAGE_SIZE})
        rows, next_cursor, more = _page(data, "comments", cursor=payload.cursor, comment=True)
        # Validate the complete bounded page before persisting any of it.
        normalized = [_comment(row, payload.video_id) for row in rows]
        imported, duplicates = 0, 0
        gateway = ChannelGateway(db)
        for message in normalized:
            if message is None:
                continue
            try:
                result = await mongo(gateway.ingest_inbound(context=context, message=message))
            except ChannelGatewayError:
                raise problem("tiktok_comments_gateway_not_ready", "صندوق ميزان غير جاهز لاستقبال التعليقات؛ راجع إعدادات الربط.", 503) from None
            duplicates += int(result.duplicate)
            imported += int(not result.duplicate)
        return {"imported": imported, "duplicates": duplicates, "skipped": sum(row is None for row in normalized), "inspected": len(rows),
                "has_more": more, "next_cursor": next_cursor if more else None, "limit": PAGE_SIZE, "source": "owner_initiated_api", **READ_ONLY}


def attach_tiktok_comment_routes(router, db, current_user, require_owner):
    comments = APIRouter(prefix="/tiktok/content", route_class=BoundedCreatorJSONRoute)

    @comments.post("/creators/{creator_ref}/comments/connect")
    async def connect(creator_ref: str, payload: CommentConnect, response: Response, user: dict = Depends(current_user)):
        # Path parameters are validated explicitly without accepting aliases.
        owner = require_owner(user)
        if not isinstance(creator_ref, str) or not re.fullmatch(REF, creator_ref):
            raise problem("tiktok_creator_invalid_reference", "اختر حساب TikTok المرتبط.", 422)
        response.headers["Cache-Control"] = "no-store"
        return await connect_comments(db, str(owner["id"]), creator_ref, payload)

    @comments.get("/creators/{creator_ref}/comments/posts")
    async def posts(response: Response, creator_ref: str, cursor: int | None = Query(default=None, ge=0, le=MAX_CURSOR), user: dict = Depends(current_user)):
        owner = require_owner(user)
        if not re.fullmatch(REF, creator_ref):
            raise problem("tiktok_creator_invalid_reference", "اختر حساب TikTok المرتبط.", 422)
        response.headers["Cache-Control"] = "no-store"
        return await list_comment_posts(db, str(owner["id"]), creator_ref, cursor)

    @comments.post("/creators/{creator_ref}/comments/pull")
    async def pull(creator_ref: str, payload: CommentPull, response: Response, user: dict = Depends(current_user)):
        owner = require_owner(user)
        if not re.fullmatch(REF, creator_ref):
            raise problem("tiktok_creator_invalid_reference", "اختر حساب TikTok المرتبط.", 422)
        response.headers["Cache-Control"] = "no-store"
        return await pull_comments(db, str(owner["id"]), creator_ref, payload)

    router.include_router(comments)
