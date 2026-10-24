"""Account-bound operational push outbox. Payloads contain no customer data.

Delivery is at-least-once; event IDs let clients collapse repeats. Provider
acceptance is recorded separately from Expo's downstream delivery receipt.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument

DEVICES = "operational_push_devices_v1"
OUTBOX = "operational_push_outbox_v1"
log = logging.getLogger(__name__)


class PushSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


def now():
    return datetime.now(timezone.utc)


def enabled():
    return os.environ.get("OPERATIONAL_PUSH_ENABLED") == "1"


def web_ready():
    return enabled() and all(os.environ.get(key) for key in (
        "OPERATIONAL_PUSH_VAPID_PUBLIC_KEY", "OPERATIONAL_PUSH_VAPID_PRIVATE_KEY",
        "OPERATIONAL_PUSH_VAPID_SUBJECT"))


async def notification_actor(db, user):
    """Do not turn an owner-scoped native read model into the employee actor."""
    actor_id = user.get("_mobile_actor_id") if user.get("_session_client") == "amasi_mobile" else None
    if not actor_id:
        return user
    actor = await db["users"].find_one({"id": actor_id}, {"_id": 0, "password_hash": 0})
    if not actor or actor.get("disabled") or actor.get("is_active") is False:
        raise HTTPException(403, detail={"code": "notification_actor_unavailable"})
    return {**actor, "_review_session": user.get("_review_session"), "_session_client": "amasi_mobile"}


def validate_subscription(value):
    if not isinstance(value, dict):
        raise ValueError("invalid_subscription")
    endpoint = str(value.get("endpoint") or "")
    url = urlsplit(endpoint)
    host = (url.hostname or "").lower()
    allowed = host in {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
    allowed = allowed or host.endswith(".notify.windows.com")
    if not allowed or url.scheme != "https" or url.port not in (None, 443) or url.username or url.password or url.fragment or len(endpoint) > 2048:
        raise ValueError("unsupported_push_endpoint")
    keys = value.get("keys") or {}
    for key, length in (("p256dh", 65), ("auth", 16)):
        raw = str(keys.get(key) or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", raw):
            raise ValueError("invalid_push_key")
        if len(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))) != length:
            raise ValueError("invalid_push_key")
    return {"endpoint": endpoint, "keys": {key: keys[key] for key in ("p256dh", "auth")}}


class DeviceRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installation_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    kind: str
    token: str | None = Field(default=None, max_length=256)
    subscription: dict | None = None


class DeviceRemoval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installation_id: str = Field(min_length=16, max_length=128)


def make_operational_push_router(db, current_user):
    router = APIRouter(prefix="/operational-notifications", tags=["operational-notifications"])

    @router.get("/web-push-key")
    async def public_key(user=Depends(current_user)):
        return {"enabled": bool(web_ready()), "public_key": os.environ.get("OPERATIONAL_PUSH_VAPID_PUBLIC_KEY", "") if web_ready() else ""}

    @router.post("/devices/register")
    async def register(payload: DeviceRegistration, user=Depends(current_user)):
        from review_session_fence import merchant_for_actor
        user = await notification_actor(db, user)
        if not enabled():
            raise HTTPException(503, detail={"code": "operational_push_not_configured"})
        session = user.get("_review_session") or {}
        if not session.get("origin_session_ref"):
            raise HTTPException(409, detail={"code": "push_reauthentication_required"})
        if payload.kind == "expo":
            if not re.fullmatch(r"(?:ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]+\]", payload.token or ""):
                raise HTTPException(422, detail={"code": "invalid_push_token"})
            destination = {"token": payload.token}
            identity = payload.token
        elif payload.kind == "web":
            if not web_ready():
                raise HTTPException(503, detail={"code": "operational_push_not_configured"})
            try:
                destination = {"subscription": validate_subscription(payload.subscription)}
            except (ValueError, TypeError):
                raise HTTPException(422, detail={"code": "invalid_push_subscription"})
            identity = destination["subscription"]["endpoint"]
        else:
            raise HTTPException(422, detail={"code": "invalid_push_kind"})
        device_id = hashlib.sha256(f"{payload.kind}:{identity}".encode()).hexdigest()
        # Moving the same browser/device to another account invalidates old jobs.
        existing = await db[DEVICES].find_one({"_id": device_id})
        same = existing and existing.get("actor_id") == user["id"] and existing.get("session_id") == session["origin_session_ref"] and existing.get("active")
        generation = existing["generation"] if same else uuid.uuid4().hex
        await db[DEVICES].update_many({"installation_id": payload.installation_id, "_id": {"$ne": device_id}}, {"$set": {"active": False}})
        await db[DEVICES].update_one({"_id": device_id}, {"$set": {
            **destination, "kind": payload.kind, "installation_id": payload.installation_id,
            "actor_id": user["id"], "merchant_id": await merchant_for_actor(db, user),
            "session_id": session["origin_session_ref"], "generation": generation,
            "active": True, "updated_at": now(),
        }}, upsert=True)
        return {"ok": True}

    @router.post("/devices/unregister")
    async def unregister(payload: DeviceRemoval, user=Depends(current_user)):
        user = await notification_actor(db, user)
        await db[DEVICES].update_many({"installation_id": payload.installation_id, "actor_id": user["id"]}, {"$set": {"active": False}})
        return {"ok": True}

    return router


async def enqueue_operational_push(db, *, recipient_id, event_id, order_number=None, instruction_id=None, priority="normal"):
    if not enabled():
        return
    devices = await db[DEVICES].find({"actor_id": recipient_id, "active": True}).to_list(100)
    for device in devices:
        key = hashlib.sha256(f"{event_id}:{device['_id']}:{device['generation']}".encode()).hexdigest()
        await db[OUTBOX].update_one({"_id": key}, {"$setOnInsert": {
            "device_id": device["_id"], "generation": device["generation"],
            "actor_id": recipient_id, "event_id": str(event_id),
            "instruction_id": str(instruction_id or ""), "status": "pending", "attempts": 0,
            "priority": priority,
            "created_at": now(), "next_at": now(), "expires_at": now() + timedelta(days=2),
        }}, upsert=True)


async def device_is_active(db, device):
    if not device or not device.get("active"):
        return False
    user = await db["users"].find_one({"id": device["actor_id"]})
    if not user or user.get("disabled") or user.get("is_active") is False or user.get("deleted_at"):
        return False
    session = await db["order_review_auth_sessions_v2"].find_one({
        "_id": device["session_id"], "actor_id": device["actor_id"],
        "user_id": device["merchant_id"], "state": "active", "expires_at": {"$gt": now()},
    })
    return bool(session)


async def send_to_provider(device, payload, *, ticket=None):
    if device["kind"] == "expo":
        headers = {"Authorization": f"Bearer {os.environ['EXPO_ACCESS_TOKEN']}"} if os.environ.get("EXPO_ACCESS_TOKEN") else {}
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            if ticket:
                response = await client.post("https://exp.host/--/api/v2/push/getReceipts", headers=headers, json={"ids": [ticket]})
                response.raise_for_status()
                result = response.json().get("data", {}).get(ticket)
                if not result:
                    return {"status": "receipt_pending"}
            else:
                response = await client.post("https://exp.host/--/api/v2/push/send", headers=headers, json={
                    "to": device["token"], "title": payload["title"], "body": payload["body"],
                    "data": payload, "sound": "default", "channelId": "operational-instructions", "ttl": 3600,
                    "priority": "high" if payload.get("priority") == "urgent" else "default",
                })
                response.raise_for_status()
                result = response.json().get("data") or {}
            if result.get("status") == "ok":
                return {"status": "provider_accepted" if ticket else "receipt_pending", "ticket": result.get("id", ticket)}
            code = (result.get("details") or {}).get("error", "provider_error")
            return {"status": "invalid_device" if code == "DeviceNotRegistered" else "retry", "error": code,
                "clear_ticket": bool(ticket)}
    # Endpoint is revalidated before any external request. No arbitrary callback URLs.
    subscription = validate_subscription(device["subscription"])
    from pywebpush import webpush, WebPushException
    try:
        def send():
            with PushSession() as session:
                return webpush(subscription_info=subscription, data=json.dumps(payload),
                    vapid_private_key=os.environ["OPERATIONAL_PUSH_VAPID_PRIVATE_KEY"],
                    vapid_claims={"sub": os.environ["OPERATIONAL_PUSH_VAPID_SUBJECT"]}, ttl=3600, timeout=15,
                    requests_session=session)
        response = await asyncio.to_thread(send)
        if response.status_code >= 300:
            return {"status": "retry"}
        return {"status": "provider_accepted"}
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        return {"status": "invalid_device" if status in (404, 410) else "retry"}


async def deliver_one(db, sender=send_to_provider):
    claim = uuid.uuid4().hex
    job = await db[OUTBOX].find_one_and_update({"status": {"$in": ["pending", "retry", "receipt_pending", "sending"]}, "next_at": {"$lte": now()}, "expires_at": {"$gt": now()}},
        {"$set": {"claim": claim, "status": "sending", "next_at": now() + timedelta(minutes=2)}, "$inc": {"attempts": 1}}, return_document=ReturnDocument.AFTER)
    if not job:
        return False
    device = await db[DEVICES].find_one({"_id": job["device_id"], "generation": job["generation"]})
    result = {"status": "discarded"}
    if await device_is_active(db, device):
        try:
            result = await sender(device, {"title": "أماسي — تنبيه مستعجل" if job.get("priority") == "urgent" else "أماسي — تعليمات الطلب", "body": "لديك تحديث على مهمة. افتح التطبيق للاطلاع والتأكيد.",
                "event_id": job["event_id"], "instruction_id": job["instruction_id"], "priority": job.get("priority", "normal")}, ticket=job.get("ticket"))
        except Exception:
            # Provider exception text can contain device credentials; never log it.
            result = {"status": "retry", "error": "transport_error"}
    if result["status"] == "invalid_device":
        await db[DEVICES].update_one({"_id": job["device_id"], "generation": job["generation"]}, {"$set": {"active": False}})
    if result["status"] in {"retry", "receipt_pending"} and job["attempts"] >= 12:
        result["status"] = "failed"
    update = {"$set": {
        **result, "updated_at": now(), "next_at": now() + timedelta(seconds=min(900, 30 * 2 ** min(job["attempts"], 5))),
    }}
    if result.pop("clear_ticket", False):
        update["$unset"] = {"ticket": ""}
    update["$set"].pop("clear_ticket", None)
    await db[OUTBOX].update_one({"_id": job["_id"], "claim": claim}, update)
    return True


async def run(db):
    await db[DEVICES].create_index([("actor_id", 1), ("active", 1)])
    await db[OUTBOX].create_index([("status", 1), ("next_at", 1)])
    await db[OUTBOX].create_index("expires_at", expireAfterSeconds=604800)
    await db["operational_instruction_comments_v1"].create_index([("notification_pending", 1), ("created_at", 1)])
    await db["operational_instruction_comments_v1"].create_index([("merchant_id", 1), ("instruction_id", 1), ("task_id", 1), ("created_at", -1), ("id", -1)])
    cursor = ""
    progress_cursor = ""
    while True:
        try:
            from operational_instruction_comments import retry_comment_notifications
            await retry_comment_notifications(db)
            from operational_instruction_progress import progress_sweep
            progress_cursor = await progress_sweep(db, progress_cursor)
            # Keyset sweep: new assignees receive pending tasks while app is closed.
            devices = await db[DEVICES].find({"active": True, "_id": {"$gt": cursor}}).sort("_id", 1).to_list(25)
            from operational_instruction_notifications import refresh_actor_instructions
            seen = set()
            for device in devices:
                cursor = device["_id"]
                key = (device["merchant_id"], device["actor_id"])
                if key not in seen and await device_is_active(db, device):
                    seen.add(key)
                    await refresh_actor_instructions(db, merchant_id=key[0], actor_id=key[1])
            if not devices:
                cursor = ""
            for _ in range(25 if enabled() else 0):
                if not await deliver_one(db):
                    break
        except Exception:
            log.warning("operational push sweep failed; will retry")
        await asyncio.sleep(20)
