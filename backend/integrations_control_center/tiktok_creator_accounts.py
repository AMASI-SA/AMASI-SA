"""Creator authorization is separate from Marketing advertiser credentials."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
import secrets
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .tiktok_creator_api import BoundedCreatorJSONRoute, TikTokCreatorAPI, admission, mongo, problem
from .tiktok_oauth_security import (
    _b64url_decode, _b64url_encode, _frontend_url, _redirect_uri, _state_secret,
    decrypt_tiktok_token, encrypt_tiktok_token,
)

CREDENTIALS = "mezan_tiktok_creator_credentials_v1"
STATES = "mezan_tiktok_creator_states_v1"
STATE_PURPOSE = "mezan_tiktok_creator_oauth_v1"
STATE_PREFIX = "ttc."
COOKIE = "mezan_tiktok_creator_binding"
CALLBACK_PATH = "/api/integrations-v2/tiktok/callback"
SCOPES = ("user.info.basic", "user.info.profile", "user.info.username", "user.account.type", "video.publish", "video.upload", "video.list", "comment.list", "comment.list.manage")
MAX_CREATORS = 20


def now():
    return datetime.now(timezone.utc)


def aware(value):
    if not isinstance(value, datetime):
        return datetime.min.replace(tzinfo=timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def creator_redirect_uri():
    # Keep an existing registered callback exact. Both slash variants are
    # routed, but we never silently change the developer app registration.
    return os.environ.get("TIKTOK_CREATOR_REDIRECT_URI", "").strip() or _redirect_uri()


def missing_configuration():
    values = {key: os.environ.get(key, "").strip() for key in (
        "TIKTOK_MARKETING_APP_ID", "TIKTOK_MARKETING_APP_SECRET", "TIKTOK_TOKEN_ENC_KEY")}
    values["TIKTOK_OAUTH_STATE_SECRET"] = os.environ.get("TIKTOK_OAUTH_STATE_SECRET", "").strip() or os.environ.get("JWT_SECRET", "").strip()
    values["FRONTEND_URL"] = os.environ.get("FRONTEND_URL", "").strip()
    values["TIKTOK_CREATOR_REDIRECT_URI"] = creator_redirect_uri()
    missing = [key for key, value in values.items() if not value]
    for key in ("FRONTEND_URL", "TIKTOK_CREATOR_REDIRECT_URI"):
        value = values[key]
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme == "https" and bool(parsed.hostname) and parsed.port is None and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
            if key == "TIKTOK_CREATOR_REDIRECT_URI":
                valid = valid and parsed.path in {CALLBACK_PATH, CALLBACK_PATH + "/"}
        except ValueError:
            valid = False
        if not valid:
            missing.append(key)
    return sorted(set(missing))


def _configured():
    missing = missing_configuration()
    if missing:
        raise problem("tiktok_creator_not_configured", "أكمل إعدادات تطبيق TikTok وربط حساب النشر.", 503)


def _signature(value, purpose):
    return hmac.new(_state_secret().encode(), (purpose + value).encode(), hashlib.sha256).hexdigest()


def browser_binding(state):
    return _signature(state, "tiktok-creator-browser:")


def _state(nonce):
    instant = now()
    payload = {"purpose": STATE_PURPOSE, "nonce": nonce, "iat": int(instant.timestamp()), "exp": int((instant + timedelta(minutes=10)).timestamp())}
    body = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return STATE_PREFIX + body + "." + _signature(body, "tiktok-creator-state:")


def _decode_state(state):
    if not isinstance(state, str) or not state.startswith(STATE_PREFIX) or len(state) > 2000:
        raise ValueError("invalid_creator_state")
    try:
        body, mac = state[len(STATE_PREFIX):].split(".", 1)
        if not hmac.compare_digest(mac, _signature(body, "tiktok-creator-state:")):
            raise ValueError("signature")
        data = json.loads(_b64url_decode(body))
        if data.get("purpose") != STATE_PURPOSE or not re.fullmatch(r"[a-f0-9]{32}", str(data.get("nonce", ""))) or type(data.get("exp")) is not int or data["exp"] <= int(now().timestamp()):
            raise ValueError("state")
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("invalid_creator_state") from None
    return data


def _scope_list(value):
    if not isinstance(value, str) or len(value) > 6000:
        raise problem("tiktok_creator_scope_unproven", "تعذر التحقق من صلاحيات حساب TikTok.", 502)
    scopes = sorted(set(part.strip() for part in value.split(",") if part.strip()))
    if len(scopes) > 64 or any(not re.fullmatch(r"[a-z][a-z0-9_.]{0,79}", part) for part in scopes):
        raise problem("tiktok_creator_scope_unproven", "تعذر التحقق من صلاحيات حساب TikTok.", 502)
    return scopes


def _token_fields(data, creator_id):
    access, refresh = data.get("access_token"), data.get("refresh_token")
    if not isinstance(access, str) or not 1 <= len(access) <= 4096 or not isinstance(refresh, str) or not 1 <= len(refresh) <= 4096 or data.get("open_id") != creator_id:
        raise problem("tiktok_creator_token_identity_mismatch", "تعذر إثبات هوية حساب TikTok.", 502)
    access_seconds, refresh_seconds = data.get("expires_in"), data.get("refresh_token_expires_in")
    if type(access_seconds) is not int or not 1 <= access_seconds <= 86400 or type(refresh_seconds) is not int or not 1 <= refresh_seconds <= 31_536_000:
        raise problem("tiktok_creator_token_expiry_unproven", "تعذر إثبات صلاحية رمز TikTok.", 502)
    instant = now()
    return {"access_token_encrypted": encrypt_tiktok_token(access), "refresh_token_encrypted": encrypt_tiktok_token(refresh), "expires_at": instant + timedelta(seconds=access_seconds), "refresh_expires_at": instant + timedelta(seconds=refresh_seconds), "creator_id_encrypted": encrypt_tiktok_token(creator_id)}


async def token_proof(api, access, creator_id):
    data = await api.call("POST", "/tt_user/token_info/get/", body={"app_id": os.environ["TIKTOK_MARKETING_APP_ID"], "access_token": access})
    if type(data.get("app_id")) not in (str, int) or str(data["app_id"]) != os.environ["TIKTOK_MARKETING_APP_ID"] or data.get("creator_id") != creator_id:
        raise problem("tiktok_creator_token_identity_mismatch", "رمز TikTok لا يطابق التطبيق والحساب المحددين.", 409)
    return _scope_list(data.get("scope"))


async def ensure_indexes(db):
    await mongo(db[CREDENTIALS].create_index([("user_id", 1), ("created_at", -1)], name="tiktok_creator_owner_history", maxTimeMS=1500))
    await mongo(db[STATES].create_index([("expires_at", 1)], expireAfterSeconds=0, name="tiktok_creator_state_ttl", maxTimeMS=1500))
    await mongo(db[STATES].create_index([("user_id", 1), ("status", 1), ("expires_at", 1)], name="tiktok_creator_pending_owner", maxTimeMS=1500))


class CreatorStartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="حساب TikTok", min_length=1, max_length=80)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("invalid account label")
        return value


async def start_creator_connection(db, user_id, payload):
    _configured()
    async with admission():
        await ensure_indexes(db)
        pending = await mongo(db[STATES].count_documents({"user_id": user_id, "status": "pending", "expires_at": {"$gt": now()}}, limit=6, maxTimeMS=1500))
        if pending >= 6:
            raise problem("tiktok_creator_authorization_busy", "أكمل جلسة ربط TikTok المفتوحة أو انتظر انتهاءها.", 429)
        nonce, instant = secrets.token_hex(16), now()
        await mongo(db[STATES].insert_one({"_id": nonce, "purpose": STATE_PURPOSE, "user_id": user_id, "label": payload.label, "status": "pending", "expires_at": instant + timedelta(minutes=10)}))
        state = _state(nonce)
        params = {"client_key": os.environ["TIKTOK_MARKETING_APP_ID"], "redirect_uri": creator_redirect_uri(), "response_type": "code", "scope": ",".join(SCOPES), "state": state, "disable_auto_auth": "1"}
        return {"authorization_url": "https://www.tiktok.com/v2/auth/authorize?" + urlencode(params), "requested_scopes": list(SCOPES), "expires_at": (instant + timedelta(minutes=10)).isoformat(), "state": state}


async def handle_creator_callback(db, *, auth_code, state, binding, provider_error=None):
    target = _frontend_url().rstrip("/") + "/ads-manager?provider=tiktok&tab=content"
    try:
        _configured()
        data = _decode_state(state)
        if provider_error or not isinstance(auth_code, str) or not 1 <= len(auth_code) <= 2000 or not isinstance(binding, str) or not hmac.compare_digest(binding, browser_binding(state)):
            raise ValueError("creator_callback_invalid")
        async with admission():
            state_doc = await mongo(db[STATES].find_one({"_id": data["nonce"], "purpose": STATE_PURPOSE, "status": "pending", "expires_at": {"$gt": now()}}, {"user_id": 1, "label": 1}, max_time_ms=1500))
            if not state_doc:
                raise ValueError("creator_callback_used")
            actor = await mongo(db.users.find_one({"id": state_doc["user_id"]}, {"role": 1, "is_owner": 1}, max_time_ms=1500))
            if not actor or not (str(actor.get("role", "")).lower() == "owner" or actor.get("is_owner") is True):
                raise ValueError("creator_callback_owner_revoked")
            consumed = await mongo(db[STATES].update_one({"_id": data["nonce"], "status": "pending", "expires_at": {"$gt": now()}}, {"$set": {"status": "used", "used_at": now()}}))
            if consumed.modified_count != 1:
                raise ValueError("creator_callback_used")
            async with TikTokCreatorAPI() as api:
                token = await api.call("POST", "/tt_user/oauth2/token/", body={"client_id": os.environ["TIKTOK_MARKETING_APP_ID"], "client_secret": os.environ["TIKTOK_MARKETING_APP_SECRET"], "grant_type": "authorization_code", "auth_code": auth_code, "redirect_uri": creator_redirect_uri()})
                creator_id = token.get("open_id")
                if not isinstance(creator_id, str) or not 1 <= len(creator_id) <= 128:
                    raise ValueError("creator_identity_missing")
                fields = _token_fields(token, creator_id)
                scopes = await token_proof(api, token["access_token"], creator_id)
            creator_ref = _signature(os.environ["TIKTOK_MARKETING_APP_ID"] + ":" + creator_id, "tiktok-creator-ref:")
            old = await mongo(db[CREDENTIALS].find_one({"_id": creator_ref}, {"user_id": 1}, max_time_ms=1500))
            if old and old.get("user_id") != state_doc["user_id"]:
                raise ValueError("creator_already_bound")
            fields.update({"app_id": os.environ["TIKTOK_MARKETING_APP_ID"], "label": state_doc["label"], "status": "connected", "scopes": scopes, "scope_verified_at": now(), "auth_epoch": secrets.token_hex(16), "updated_at": now()})
            if old:
                result = await mongo(db[CREDENTIALS].update_one({"_id": creator_ref, "user_id": state_doc["user_id"]}, {"$set": fields, "$unset": {"refresh_claim": ""}}))
                if result.matched_count != 1:
                    raise ValueError("creator_binding_changed")
            else:
                creators = await mongo(db[CREDENTIALS].count_documents({"user_id": state_doc["user_id"]}, limit=MAX_CREATORS, maxTimeMS=1500))
                if creators >= MAX_CREATORS:
                    raise problem("tiktok_creator_account_limit", "بلغت حد حسابات النشر في ميزان.", 429)
                await mongo(db[CREDENTIALS].insert_one({"_id": creator_ref, "user_id": state_doc["user_id"], "created_at": now(), **fields}))
        target += "&creator_connected=1"
    except Exception:
        # No auth code, token, open_id, provider message or URL in redirects/logs.
        target += "&creator_error=authorization_failed"
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(COOKIE, path=CALLBACK_PATH, secure=True, httponly=True, samesite="lax")
    return response


async def _refresh(db, row, api):
    if aware(row.get("refresh_expires_at")) <= now() or not row.get("refresh_token_encrypted"):
        raise problem("tiktok_creator_reauthorization_required", "أعد تفويض حساب TikTok لتجديد صلاحياته.")
    claim = secrets.token_hex(16)
    query = {"_id": row["_id"], "user_id": row["user_id"], "status": "connected", "auth_epoch": row["auth_epoch"]}
    reserved = await mongo(db[CREDENTIALS].update_one(query, {"$set": {"status": "refreshing", "refresh_claim": claim}}))
    if reserved.modified_count != 1:
        raise problem("tiktok_creator_refresh_busy", "تجديد حساب TikTok جارٍ؛ حاول بعد قليل.", 503)
    refresh_query = {"_id": row["_id"], "user_id": row["user_id"], "status": "refreshing", "refresh_claim": claim, "auth_epoch": row["auth_epoch"]}
    try:
        creator_id = decrypt_tiktok_token(row["creator_id_encrypted"])
        data = await api.call("POST", "/tt_user/oauth2/refresh_token/", body={"client_id": os.environ["TIKTOK_MARKETING_APP_ID"], "client_secret": os.environ["TIKTOK_MARKETING_APP_SECRET"], "grant_type": "refresh_token", "refresh_token": decrypt_tiktok_token(row["refresh_token_encrypted"])})
        fields = _token_fields(data, creator_id)
        scopes = await token_proof(api, data["access_token"], creator_id)
        fields.update({"status": "connected", "scopes": scopes, "scope_verified_at": now(), "auth_epoch": secrets.token_hex(16), "updated_at": now()})
        stored = await mongo(db[CREDENTIALS].update_one(refresh_query, {"$set": fields, "$unset": {"refresh_claim": ""}}))
        if stored.matched_count != 1:
            raise problem("tiktok_creator_authorization_changed", "تغير ربط الحساب؛ أعد تحميله.")
        return {**row, **fields}
    except BaseException:
        # A rotating refresh token may have been consumed. Never retry it.
        try:
            await mongo(db[CREDENTIALS].update_one(refresh_query, {"$set": {"status": "needs_reauth", "safe_error": "refresh_result_unproven"}}))
        except Exception:
            pass
        raise


async def verified_creator(db, user_id, creator_ref, api, required_scope=None):
    _configured()
    row = await mongo(db[CREDENTIALS].find_one({"_id": creator_ref, "user_id": user_id}, max_time_ms=1500))
    if not row or row.get("status") != "connected" or row.get("app_id") != os.environ["TIKTOK_MARKETING_APP_ID"]:
        raise problem("tiktok_creator_not_connected", "اربط حساب TikTok للنشر وأعد التحقق من صلاحياته.")
    if aware(row.get("expires_at")) <= now() + timedelta(seconds=60):
        row = await _refresh(db, row, api)
        scopes = row["scopes"]
    else:
        token, creator_id = decrypt_tiktok_token(row.get("access_token_encrypted")), decrypt_tiktok_token(row.get("creator_id_encrypted"))
        if not token or not creator_id:
            raise problem("tiktok_creator_not_connected", "أعد تفويض حساب TikTok.")
        scopes = await token_proof(api, token, creator_id)
        updated = await mongo(db[CREDENTIALS].update_one({"_id": creator_ref, "user_id": user_id, "status": "connected", "auth_epoch": row["auth_epoch"]}, {"$set": {"scopes": scopes, "scope_verified_at": now()}}))
        if updated.matched_count != 1:
            raise problem("tiktok_creator_authorization_changed", "تغير ربط الحساب؛ أعد تحميله.")
    if required_scope and required_scope not in scopes:
        raise problem("tiktok_creator_missing_permission", "الحساب لم يمنح صلاحية هذه العملية. أعد التفويض بالصلاحيات المطلوبة.")
    row["scopes"] = scopes
    return row, decrypt_tiktok_token(row["access_token_encrypted"]), decrypt_tiktok_token(row["creator_id_encrypted"])


def capability_projection(scopes):
    scopes = set(scopes)
    return {"publish": "video.publish" in scopes, "draft_upload": "video.upload" in scopes, "comments_read": {"video.list", "comment.list"} <= scopes, "comments_manage": "comment.list.manage" in scopes, "messaging_read": "message.read" in scopes, "messaging_send": "message.send" in scopes, "ai_auto_reply_enabled": False}


async def list_creators(db, user_id):
    cursor = db[CREDENTIALS].find({"user_id": user_id}, {"label": 1, "status": 1, "scopes": 1, "expires_at": 1, "refresh_expires_at": 1, "scope_verified_at": 1}).sort("created_at", -1).limit(MAX_CREATORS).max_time_ms(1500)
    rows = await mongo(cursor.to_list(MAX_CREATORS))
    return {"configured": not missing_configuration(), "items": [{"creator_ref": row["_id"], "label": row.get("label", "حساب TikTok"), "status": row.get("status", "needs_reauth"), "scopes": row.get("scopes", []), "capabilities": capability_projection(row.get("scopes", [])), "expires_at": row.get("expires_at"), "scope_verified_at": row.get("scope_verified_at"), "scope_evidence": "stored_token_info"} for row in rows], "limit": MAX_CREATORS, "ai_auto_reply_enabled": False}


def attach_creator_account_routes(router, db, current_user, require_owner):
    content = APIRouter(prefix="/tiktok/content", route_class=BoundedCreatorJSONRoute)

    @content.post("/creators/connect/start")
    async def connect(response: Response, payload: CreatorStartInput = CreatorStartInput(), user: dict = Depends(current_user)):
        owner = require_owner(user)
        result = await start_creator_connection(db, str(owner["id"]), payload)
        response.set_cookie(COOKIE, browser_binding(result.pop("state")), max_age=600, secure=True, httponly=True, samesite="lax", path=CALLBACK_PATH)
        return result

    @content.get("/creators")
    async def creators(user: dict = Depends(current_user)):
        owner = require_owner(user)
        return await list_creators(db, str(owner["id"]))

    router.include_router(content)
