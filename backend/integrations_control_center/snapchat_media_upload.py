"""Owner-approved Snapchat video uploads without persistent file storage."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterable, Callable
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from .snapchat_campaign_management import (
    SnapchatManagementProvider, _identifier, _selected_account,
    snapchat_campaign_mutations_enabled,
)
from .snapchat_native_data_common import (
    SNAPCHAT_API_BASE, SNAPCHAT_PROVIDER_ID, SnapchatNativeSyncError, _collection,
)

MEDIA_UPLOAD_COLLECTION = "mezan_snapchat_media_uploads_v1"
MAX_VIDEO_BYTES = 32_000_000
BODY_TIMEOUT_SECONDS = 45
STALE_ATTEMPT_SECONDS = 180
RETRYABLE_FILE_STATES = {"previewed", "awaiting_file"}
INFLIGHT_STATES = {"receiving", "creating", "media_created", "uploading"}
VIDEO_TYPES = {"video/mp4": ".mp4", "video/quicktime": ".mov"}
BUSINESS_API_BASE = "https://businessapi.snapchat.com/v1"
# One byte-holding lifecycle per application worker; durable CAS separately
# prevents a second write for the same source across workers/replicas.
_UPLOAD_SLOTS = asyncio.BoundedSemaphore(1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


def _failure(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code})


def _date(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


class SnapchatMediaPreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    name: str = Field(min_length=1, max_length=200)
    file_name: str = Field(min_length=1, max_length=180)
    size_bytes: StrictInt = Field(ge=12, le=MAX_VIDEO_BYTES)
    sha256: str
    content_type: str
    ai_generated: bool = False

    @field_validator("account_id")
    @classmethod
    def valid_account(cls, value: str) -> str:
        return _identifier(value, field="account_id")

    @field_validator("sha256")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("sha256 must contain 64 hexadecimal characters")
        return value

    @field_validator("content_type")
    @classmethod
    def valid_type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in VIDEO_TYPES:
            raise ValueError("only MP4 and QuickTime video uploads are supported")
        return value

    @field_validator("name", "file_name")
    @classmethod
    def valid_text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("invalid media name")
        return value

    @field_validator("file_name")
    @classmethod
    def safe_file_name(cls, value: str) -> str:
        if "/" in value or "\\" in value:
            raise ValueError("file_name must be a basename")
        return value


def _media_object(payload: Any, *, uploaded: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _failure(502, "snapchat_media_invalid_response")
    if str(payload.get("request_status") or "").upper() not in {"SUCCESS", "OK"}:
        raise _failure(502, "snapchat_media_provider_request_failed")
    if uploaded:
        entity = payload.get("result")
    else:
        wrappers = payload.get("media")
        if (not isinstance(wrappers, list) or len(wrappers) != 1
                or not isinstance(wrappers[0], dict)
                or str(wrappers[0].get("sub_request_status") or "").upper()
                not in {"SUCCESS", "OK"}):
            raise _failure(502, "snapchat_media_invalid_response")
        entity = wrappers[0].get("media")
    if not isinstance(entity, dict):
        raise _failure(502, "snapchat_media_invalid_response")
    return entity


def _checked_media(
    entity: Any, row: dict[str, Any], *, media_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(entity, dict):
        raise _failure(502, "snapchat_media_invalid_response")
    try:
        entity_id = _identifier(entity.get("id"), field="media_id")
    except ValueError as exc:
        raise _failure(502, "snapchat_media_identity_invalid") from exc
    if ((media_id is not None and entity_id != media_id)
            or str(entity.get("ad_account_id") or "") != row["account_id"]
            or entity.get("type") != "VIDEO" or entity.get("deleted") is True):
        raise _failure(409, "snapchat_media_identity_conflict")
    provider_status = str(entity.get("media_status") or "").upper()
    if provider_status not in {"PENDING_UPLOAD", "READY"}:
        raise _failure(409, "snapchat_media_status_unavailable")
    if provider_status == "READY":
        size = entity.get("file_size_in_bytes")
        if size is not None and (
            isinstance(size, bool) or not isinstance(size, (str, int))
            or not str(size).isdigit() or int(size) != row["size_bytes"]
        ):
            raise _failure(409, "snapchat_media_file_size_conflict")
        duration = entity.get("duration_in_seconds")
        if duration is not None:
            try:
                duration = float(duration)
            except (TypeError, ValueError) as exc:
                raise _failure(409, "snapchat_media_video_specs_conflict") from exc
            if not math.isfinite(duration) or not 3 <= duration <= 180:
                raise _failure(409, "snapchat_media_video_specs_conflict")
        metadata = entity.get("video_metadata")
        if isinstance(metadata, dict):
            for key, expected in (("width_px", 1080), ("height_px", 1920)):
                if metadata.get(key) is not None and metadata[key] != expected:
                    raise _failure(409, "snapchat_media_video_specs_conflict")
    return entity


async def _access_token(provider: Any) -> str:
    try:
        return await provider.context.access_token()
    except SnapchatNativeSyncError as exc:
        raise _failure(409, "snapchat_media_needs_reauth") from exc


async def available_public_profile_ids(provider: Any, account_id: str) -> set[str]:
    """Read complete account-scoped sharing proof from the fixed Business API."""
    account_id = _identifier(account_id, field="account_id")
    path = f"/adaccounts/{account_id}/sharing_policies"
    url = f"{BUSINESS_API_BASE}{path}"
    params: dict[str, Any] | None = {"shared_resource_types": "public_profiles"}
    headers = {"Authorization": f"Bearer {await _access_token(provider)}", "Accept": "application/json"}
    result: set[str] = set()
    seen: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=35.0, follow_redirects=False) as client:
            for _ in range(10):
                if url in seen:
                    raise _failure(409, "snapchat_media_profile_catalog_incomplete")
                seen.add(url)
                response = await client.get(url, headers=headers, params=params)
                if response.status_code in {401, 403}:
                    raise _failure(409, "snapchat_media_needs_reauth_or_role")
                if response.status_code != 200:
                    raise _failure(502, "snapchat_media_profile_catalog_unavailable")
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise _failure(502, "snapchat_media_profile_catalog_incomplete") from exc
                if (not isinstance(payload, dict)
                        or str(payload.get("request_status") or "").upper() not in {"SUCCESS", "OK"}
                        or not isinstance(payload.get("sharing_policies"), list)):
                    raise _failure(502, "snapchat_media_profile_catalog_incomplete")
                for policy in payload["sharing_policies"]:
                    source = policy.get("source") if isinstance(policy, dict) else None
                    if not isinstance(source, dict):
                        raise _failure(502, "snapchat_media_profile_catalog_incomplete")
                    if source.get("resource_type") != "public_profiles":
                        continue
                    target = policy.get("target")
                    if target is not None and (not isinstance(target, dict)
                            or target.get("resource_type") != "adaccounts"
                            or str(target.get("resource_id") or "") != account_id):
                        continue
                    try:
                        result.add(_identifier(source.get("resource_id"), field="profile_id"))
                    except ValueError as exc:
                        raise _failure(502, "snapchat_media_profile_catalog_incomplete") from exc
                    if len(result) > 1000:
                        raise _failure(409, "snapchat_media_profile_catalog_incomplete")
                paging = payload.get("paging", {})
                if not isinstance(paging, dict):
                    raise _failure(502, "snapchat_media_profile_catalog_incomplete")
                next_link = paging.get("next_link")
                if next_link is None or next_link == "":
                    if paging.get("next_page_id") not in (None, ""):
                        raise _failure(409, "snapchat_media_profile_catalog_incomplete")
                    return result
                if not isinstance(next_link, str):
                    raise _failure(502, "snapchat_media_profile_catalog_incomplete")
                try:
                    parsed = urlsplit(next_link)
                    hostname = parsed.hostname
                except ValueError as exc:
                    raise _failure(502, "snapchat_media_profile_catalog_incomplete") from exc
                if (parsed.scheme != "https" or hostname != "businessapi.snapchat.com"
                        or parsed.netloc != "businessapi.snapchat.com"
                        or parsed.path != f"/v1{path}" or parsed.fragment):
                    raise _failure(502, "snapchat_media_profile_catalog_incomplete")
                url, params = next_link, None
    except httpx.HTTPError as exc:
        raise _failure(502, "snapchat_media_profile_catalog_unavailable") from exc
    raise _failure(409, "snapchat_media_profile_catalog_incomplete")


async def verify_creative_media_dependencies(
    provider: Any, *, account_id: str, operation: dict[str, Any],
) -> dict[str, Any]:
    """Fresh provider proof at preview and immediately before creative POST."""
    account_id = _identifier(account_id, field="account_id")
    values = (operation.get("body") or {}).get("creatives")
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise _failure(409, "snapchat_media_creative_intent_invalid")
    media_id = _identifier(values[0].get("top_snap_media_id"), field="media_id")
    profile = values[0].get("profile_properties")
    profile_id = _identifier(profile.get("profile_id") if isinstance(profile, dict) else None, field="profile_id")
    # Profile reads precede the final media read so no stale cached READY
    # status can authorize publication while the sharing catalogue is loaded.
    profiles = await available_public_profile_ids(provider, account_id)
    if profile_id not in profiles:
        raise _failure(409, "snapchat_media_profile_not_available_for_account")
    media = _media_object(await provider._request("GET", f"/media/{media_id}"))
    if (str(media.get("id") or "") != media_id
            or str(media.get("ad_account_id") or "") != account_id
            or media.get("type") not in {"VIDEO", "IMAGE"}
            or media.get("media_status") != "READY" or media.get("deleted") is True):
        raise _failure(409, "snapchat_media_creative_dependency_unavailable")
    usages = media.get("media_usages")
    if usages is not None and (not isinstance(usages, list) or "TOP_SNAP" not in usages):
        raise _failure(409, "snapchat_media_creative_dependency_unavailable")
    return {"verified": True, "account_id": account_id, "media_id": media_id,
            "media_status": "READY", "profile_id": profile_id, "verified_at": _iso()}


class SnapchatMediaProvider(SnapchatManagementProvider):
    async def create_media(self, row: dict[str, Any]) -> dict[str, Any]:
        entity = {"name": row["provider_name"], "type": "VIDEO", "ad_account_id": row["account_id"]}
        if row["ai_generated"]:
            entity["ai_content_source"] = "USER_AI_GEN"
        return _media_object(await self._request(
            "POST", f"/adaccounts/{row['account_id']}/media", body={"media": [entity]},
        ))

    async def read_media(self, media_id: str) -> dict[str, Any]:
        media_id = _identifier(media_id, field="media_id")
        return _media_object(await self._request("GET", f"/media/{media_id}"))

    async def upload_media(self, media_id: str, data: bytes, content_type: str) -> dict[str, Any]:
        media_id = _identifier(media_id, field="media_id")
        if content_type not in VIDEO_TYPES or not 12 <= len(data) <= MAX_VIDEO_BYTES:
            raise _failure(422, "snapchat_media_file_invalid")
        headers = {"Authorization": f"Bearer {await _access_token(self)}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0, read=35.0), follow_redirects=False,
            ) as client:
                response = await client.post(
                    f"{SNAPCHAT_API_BASE}/media/{media_id}/upload", headers=headers,
                    files={"file": (f"mezan-video{VIDEO_TYPES[content_type]}", data, content_type)},
                )
        except httpx.HTTPError as exc:
            raise _failure(502, "snapchat_media_upload_outcome_unknown") from exc
        if response.status_code in {401, 403}:
            raise _failure(409, "snapchat_media_needs_reauth_or_role")
        if response.status_code >= 300:
            raise _failure(502, "snapchat_media_upload_outcome_unknown")
        try:
            payload = response.json()
        except ValueError as exc:
            raise _failure(502, "snapchat_media_invalid_response") from exc
        return _media_object(payload, uploaded=True)


def _jobs(db: Any) -> Any:
    return _collection(db, MEDIA_UPLOAD_COLLECTION)


def _public(row: dict[str, Any], *, token: str | None = None) -> dict[str, Any]:
    result = {key: row.get(key) for key in (
        "upload_id", "account_id", "name", "file_name", "size_bytes", "sha256",
        "content_type", "ai_generated", "status", "created_at", "updated_at", "expires_at",
        "provider_media_id", "provider_media_status", "provider_upload_attempted", "error_code",
    )}
    result.update({"provider": SNAPCHAT_PROVIDER_ID,
        "can_upload": row.get("status") in RETRYABLE_FILE_STATES and row.get("provider_upload_attempted") is not True,
        "ready_for_creative": row.get("status") == "ready" and row.get("source_verified") is True
            and row.get("provider_readback_verified") is True,
        "recovery_action": "reconcile_only" if row.get("status") in {
            "creating", "create_unknown", "media_created", "uploading", "upload_unknown", "processing",
        } else None,
    })
    if token is not None:
        result["confirm_token"] = token
    return result


async def _load(db: Any, user_id: str, upload_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", str(upload_id)):
        raise _failure(404, "snapchat_media_upload_not_found")
    row = await _jobs(db).find_one({"_id": upload_id, "user_id": user_id})
    if not row:
        raise _failure(404, "snapchat_media_upload_not_found")
    return row


async def _state(db: Any, row: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    values = {**values, "updated_at": _iso(), "revision": row["revision"] + 1}
    event = {"status": values.get("status", row["status"]), "at": values["updated_at"]}
    if values.get("error_code"):
        event["error_code"] = values["error_code"]
    result = await _jobs(db).update_one(
        {"_id": row["_id"], "user_id": row["user_id"], "revision": row["revision"], "claim_id": row.get("claim_id")},
        {"$set": values, "$push": {"events": {"$each": [event], "$slice": -20}}},
    )
    if result.matched_count != 1:
        raise _failure(409, "snapchat_media_upload_state_changed")
    return {**row, **values}


def _assert_mutations() -> None:
    if not snapchat_campaign_mutations_enabled():
        raise _failure(409, "snapchat_campaign_mutations_disabled")


async def _guard(db: Any, user_id: str, account_id: str, provider: Any, *, writes: bool) -> None:
    if writes:
        _assert_mutations()
    account = await _selected_account(db, user_id, account_id)
    cache = getattr(provider, "_management_roles_cache", None)
    if isinstance(cache, dict):
        cache.pop(account_id, None)
    role = await provider.management_role(account, "creative.create")
    if role.get("allowed") is not True:
        raise _failure(409, "snapchat_media_creative_role_missing")
    if writes:
        _assert_mutations()


async def create_media_upload_preview(
    db: Any, user_id: str, payload: SnapchatMediaPreviewInput, *, provider: Any = None,
) -> dict[str, Any]:
    provider = provider or SnapchatMediaProvider(db, user_id)
    await _guard(db, user_id, payload.account_id, provider, writes=False)
    metadata = payload.model_dump(mode="json")
    fingerprint = hashlib.sha256(json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    upload_id = hashlib.sha256(json.dumps([user_id, payload.account_id, payload.sha256], separators=(",", ":")).encode()).hexdigest()
    at = _iso()
    document = {"_id": upload_id, "upload_id": upload_id, "user_id": user_id,
        **metadata, "request_fingerprint": fingerprint,
        "provider_name": f"MZVideo-{upload_id[:24]}-{payload.sha256[:16]}",
        "status": "previewed", "revision": 0, "claim_id": None, "confirm_token_hash": None,
        "provider_media_id": None, "provider_upload_attempted": False, "source_verified": False,
        "provider_readback_verified": False, "created_at": at, "updated_at": at,
        "events": [{"status": "previewed", "at": at}],
    }
    await _jobs(db).update_one({"_id": upload_id}, {"$setOnInsert": document}, upsert=True)
    row = await _load(db, user_id, upload_id)
    if row.get("request_fingerprint") != fingerprint:
        raise _failure(409, "snapchat_media_preview_content_conflict")
    if row["status"] not in RETRYABLE_FILE_STATES:
        return _public(row)
    token = secrets.token_urlsafe(32)
    row = await _state(db, row, {"confirm_token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "expires_at": (_now() + timedelta(minutes=30)).isoformat()})
    return _public(row, token=token)


async def _read_source(source: AsyncIterable[bytes], row: dict[str, Any]) -> bytes:
    buffer = bytearray()
    try:
        async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
            async for chunk in source:
                if not isinstance(chunk, bytes):
                    raise _failure(422, "snapchat_media_file_invalid")
                if len(buffer) + len(chunk) > min(MAX_VIDEO_BYTES, row["size_bytes"]):
                    raise _failure(413, "snapchat_media_file_too_large")
                buffer.extend(chunk)
        if len(buffer) != row["size_bytes"]:
            raise _failure(422, "snapchat_media_file_size_mismatch")
        if not hmac.compare_digest(hashlib.sha256(buffer).hexdigest(), row["sha256"]):
            raise _failure(422, "snapchat_media_file_digest_mismatch")
        if len(buffer) < 12 or buffer[4:8] != b"ftyp":
            raise _failure(422, "snapchat_media_container_unsupported")
        return bytes(buffer)
    except TimeoutError as exc:
        raise _failure(408, "snapchat_media_body_timeout") from exc
    finally:
        buffer.clear()


def _ready_values(row: dict[str, Any], entity: dict[str, Any]) -> dict[str, Any]:
    if entity["media_status"] == "READY":
        if row.get("provider_upload_attempted") is not True or row.get("source_verified") is not True:
            raise _failure(409, "snapchat_media_unexpected_ready")
        return {"status": "ready", "provider_media_status": "READY",
                "provider_readback_verified": True, "error_code": None}
    return {"status": "processing", "provider_media_status": "PENDING_UPLOAD", "provider_readback_verified": False}


async def _upload_media_body_claimed(
    db: Any, user_id: str, upload_id: str, confirm_token: str,
    source: AsyncIterable[bytes], content_type: str, *, provider: Any = None,
) -> dict[str, Any]:
    row = await _load(db, user_id, upload_id)
    if row["status"] == "ready":
        return _public(row)
    if row["status"] not in RETRYABLE_FILE_STATES or row.get("provider_upload_attempted") is True:
        raise _failure(409, "snapchat_media_reconciliation_required")
    expires = _date(row.get("expires_at"))
    if expires is None or expires <= _now():
        raise _failure(409, "snapchat_media_preview_expired")
    if not hmac.compare_digest(hashlib.sha256(str(confirm_token).encode()).hexdigest(), str(row.get("confirm_token_hash") or "")):
        raise _failure(409, "snapchat_media_confirmation_invalid")
    if content_type.split(";", 1)[0].strip().lower() != row["content_type"]:
        raise _failure(415, "snapchat_media_content_type_mismatch")
    provider = provider or SnapchatMediaProvider(db, user_id)
    await _guard(db, user_id, row["account_id"], provider, writes=True)
    row = await _state(db, row, {"status": "receiving", "claim_id": str(uuid.uuid4()),
        "attempt_started_at": _iso(), "error_code": None})
    media_id = row.get("provider_media_id")
    create_attempted = upload_attempted = False
    data = b""
    try:
        data = await _read_source(source, row)
        await _guard(db, user_id, row["account_id"], provider, writes=True)
        if not media_id:
            row = await _state(db, row, {"status": "creating", "source_verified": True, "attempt_started_at": _iso()})
            _assert_mutations()
            create_attempted = True
            entity = _checked_media(await provider.create_media(row), row)
            if entity.get("name") != row["provider_name"] or entity["media_status"] != "PENDING_UPLOAD":
                raise _failure(409, "snapchat_media_create_identity_conflict")
            media_id = entity["id"]
            row = await _state(db, row, {"status": "media_created", "provider_media_id": media_id, "provider_media_status": "PENDING_UPLOAD"})
        else:
            entity = _checked_media(await provider.read_media(media_id), row, media_id=media_id)
            if entity["media_status"] != "PENDING_UPLOAD":
                raise _failure(409, "snapchat_media_unexpected_ready")
            row = await _state(db, row, {"status": "media_created", "source_verified": True})
        await _guard(db, user_id, row["account_id"], provider, writes=True)
        row = await _state(db, row, {"status": "uploading", "provider_upload_attempted": True, "attempt_started_at": _iso()})
        _assert_mutations()
        upload_attempted = True
        _checked_media(await provider.upload_media(media_id, data, row["content_type"]), row, media_id=media_id)
        entity = _checked_media(await provider.read_media(media_id), row, media_id=media_id)
        row = await _state(db, row, _ready_values(row, entity))
        return _public(row)
    except BaseException as exc:
        status = "upload_unknown" if upload_attempted else "awaiting_file" if media_id else "create_unknown" if create_attempted else "previewed"
        code = "snapchat_media_upload_interrupted"
        if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
            candidate = str(exc.detail.get("code") or "")
            if re.fullmatch(r"[a-z0-9_]{1,100}", candidate):
                code = candidate
        try:
            await asyncio.shield(_state(db, row, {"status": status, "error_code": code,
                "provider_media_id": media_id, "provider_upload_attempted": upload_attempted}))
        except Exception:
            pass  # Never overwrite a newer fenced state; stale recovery stays GET-only.
        if isinstance(exc, (HTTPException, asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        raise _failure(502, "snapchat_media_upload_interrupted") from exc
    finally:
        data = b""


async def upload_media_body(
    db: Any, user_id: str, upload_id: str, confirm_token: str,
    source: AsyncIterable[bytes], content_type: str, *, provider: Any = None,
) -> dict[str, Any]:
    try:
        await asyncio.wait_for(_UPLOAD_SLOTS.acquire(), timeout=0.25)
    except TimeoutError as exc:
        raise _failure(429, "snapchat_media_upload_capacity_busy") from exc
    try:
        return await _upload_media_body_claimed(db, user_id, upload_id, confirm_token, source, content_type, provider=provider)
    finally:
        _UPLOAD_SLOTS.release()


async def reconcile_media_upload(db: Any, user_id: str, upload_id: str, *, provider: Any = None) -> dict[str, Any]:
    row = await _load(db, user_id, upload_id)
    provider = provider or SnapchatMediaProvider(db, user_id)
    await _guard(db, user_id, row["account_id"], provider, writes=False)
    if row["status"] in INFLIGHT_STATES:
        started = _date(row.get("attempt_started_at"))
        if started is None or (_now() - started).total_seconds() < STALE_ATTEMPT_SECONDS:
            return _public(row)
        replacement = {"receiving": "previewed", "creating": "create_unknown",
                       "media_created": "awaiting_file", "uploading": "upload_unknown"}[row["status"]]
        row = await _state(db, row, {"status": replacement, "claim_id": None, "error_code": "snapchat_media_attempt_interrupted"})
    media_id = row.get("provider_media_id")
    if not media_id:
        if row["status"] == "create_unknown":
            row = await _state(db, row, {"error_code": "snapchat_media_create_unresolved"})
        return _public(row)
    entity = _checked_media(await provider.read_media(media_id), row, media_id=media_id)
    if row.get("provider_upload_attempted") is not True:
        if entity["media_status"] == "READY":
            raise _failure(409, "snapchat_media_unexpected_ready")
        return _public(row)
    return _public(await _state(db, row, _ready_values(row, entity)))


def attach_snapchat_media_upload_routes(
    router: APIRouter, db: Any, current_user: Callable, require_owner: Callable[[Any], dict],
    *, provider_factory: Callable[..., Any] = SnapchatMediaProvider,
) -> None:
    base = f"/{SNAPCHAT_PROVIDER_ID}/management/media-uploads"

    @router.get(f"{base}/profiles")
    async def profiles(account_id: str = Query(..., min_length=1, max_length=160),
                       user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        try:
            account_id = _identifier(account_id, field="account_id")
        except ValueError as exc:
            raise _failure(422, "snapchat_media_account_invalid") from exc
        provider = provider_factory(db, user_id)
        await _guard(db, user_id, account_id, provider, writes=False)
        profile_ids = sorted(await available_public_profile_ids(provider, account_id))
        return {"provider": SNAPCHAT_PROVIDER_ID, "account_id": account_id,
                "complete": True, "profiles": [{"profile_id": value,
                    "display_name": f"ملف سناب · {value[-8:]}"} for value in profile_ids]}

    @router.post(f"{base}/preview", status_code=201)
    async def preview(payload: SnapchatMediaPreviewInput, user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        return await create_media_upload_preview(db, user_id, payload, provider=provider_factory(db, user_id))

    @router.post(f"{base}/{{upload_id}}/upload")
    async def upload(upload_id: str, request: Request, user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        return await upload_media_body(db, user_id, upload_id,
            request.headers.get("X-Mezan-Confirm-Token", ""), request.stream(),
            request.headers.get("Content-Type", ""), provider=provider_factory(db, user_id))

    @router.get(f"{base}/{{upload_id}}")
    async def status(upload_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        return _public(await _load(db, str(owner["id"]), upload_id))

    @router.post(f"{base}/{{upload_id}}/reconcile")
    async def reconcile(upload_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        return await reconcile_media_upload(db, user_id, upload_id, provider=provider_factory(db, user_id))
