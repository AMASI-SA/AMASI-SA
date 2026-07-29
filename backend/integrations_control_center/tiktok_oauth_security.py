"""Secure TikTok Marketing API OAuth primitives for Integrations V2.

Credentials are encrypted in a dedicated tenant-scoped collection and never
projected into public V2 collections or API responses.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

TIKTOK_PROVIDER_ID = "tiktok_ads"
TIKTOK_AUTH_URL = "https://ads.tiktok.com/marketing_api/auth"
TIKTOK_TOKEN_URL = (
    "https://business-api.tiktok.com/open_api/v1.3/oauth2/access_token/"
)
TIKTOK_STATE_PURPOSE = "mezan_integrations_v2_tiktok_marketing_oauth"
TIKTOK_STATE_TTL = timedelta(minutes=15)
TIKTOK_SOURCE_MODE = "tiktok_marketing_oauth_v2"
TIKTOK_CREDENTIALS_COLLECTION = "mezan_tiktok_oauth_credentials_v2"
TIKTOK_STATES_COLLECTION = "mezan_tiktok_oauth_states_v2"
TIKTOK_PERMISSION_ALIAS = "tiktok_marketing_api"
TIKTOK_CAPABILITY_EVIDENCE = (
    "campaigns.read",
    "budgets.read",
    "ads.read",
    "creatives.read",
    "audiences.read",
    "insights.read",
    "conversions.read",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")


def _redirect_uri() -> str:
    explicit = os.environ.get("TIKTOK_MARKETING_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    public_backend = (
        os.environ.get("BACKEND_PUBLIC_URL", "").strip()
        or os.environ.get("BACKEND_URL", "").strip()
    )
    if public_backend:
        return public_backend.rstrip("/") + "/api/integrations-v2/tiktok/callback"
    return ""


def tiktok_oauth_missing_configuration() -> list[str]:
    required = {
        "TIKTOK_MARKETING_APP_ID": os.environ.get(
            "TIKTOK_MARKETING_APP_ID", ""
        ),
        "TIKTOK_MARKETING_APP_SECRET": os.environ.get(
            "TIKTOK_MARKETING_APP_SECRET", ""
        ),
        "TIKTOK_TOKEN_ENC_KEY": os.environ.get("TIKTOK_TOKEN_ENC_KEY", ""),
        "JWT_SECRET": os.environ.get("JWT_SECRET", ""),
        "TIKTOK_MARKETING_REDIRECT_URI": _redirect_uri(),
    }
    return [key for key, value in required.items() if not str(value or "").strip()]


def tiktok_oauth_configured() -> bool:
    return not tiktok_oauth_missing_configuration()


def _state_secret() -> str:
    value = (
        os.environ.get("TIKTOK_OAUTH_STATE_SECRET", "").strip()
        or os.environ.get("JWT_SECRET", "").strip()
    )
    if not value:
        raise RuntimeError("TikTok OAuth state signing secret is required")
    return value


def _fernet() -> Any:
    from cryptography.fernet import Fernet, MultiFernet

    primary = os.environ.get("TIKTOK_TOKEN_ENC_KEY", "").strip()
    if not primary:
        raise RuntimeError("TIKTOK_TOKEN_ENC_KEY is required")
    keys = [Fernet(primary.encode("utf-8"))]
    rotation = os.environ.get("TIKTOK_TOKEN_ENC_KEY_OLD", "").strip()
    if rotation:
        keys.append(Fernet(rotation.encode("utf-8")))
    return MultiFernet(keys)


def encrypt_tiktok_token(value: str | None) -> bytes | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8"))


def decrypt_tiktok_token(value: bytes | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise ValueError("TikTok token decryption failed") from exc


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _encode_state(user_id: str, nonce: str) -> str:
    now = _utcnow()
    payload = {
        "purpose": TIKTOK_STATE_PURPOSE,
        "user_id": str(user_id),
        "nonce": nonce,
        "iat": int(now.timestamp()),
        "exp": int((now + TIKTOK_STATE_TTL).timestamp()),
    }
    encoded = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(
        _state_secret().encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded}.{_b64url_encode(signature)}"


def _decode_state(value: str) -> dict[str, Any]:
    try:
        encoded, signature_text = value.split(".", 1)
        expected = hmac.new(
            _state_secret().encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        provided = _b64url_decode(signature_text)
        if not hmac.compare_digest(expected, provided):
            raise ValueError("signature_mismatch")
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid_tiktok_oauth_state") from exc
    if payload.get("purpose") != TIKTOK_STATE_PURPOSE:
        raise ValueError("invalid_tiktok_oauth_state_purpose")
    if not payload.get("user_id") or not payload.get("nonce"):
        raise ValueError("invalid_tiktok_oauth_state_payload")
    if int(payload.get("exp") or 0) <= int(_utcnow().timestamp()):
        raise ValueError("tiktok_oauth_state_expired")
    return payload


def _authorization_url(state: str) -> str:
    params = {
        "app_id": os.environ["TIKTOK_MARKETING_APP_ID"],
        "state": state,
        "redirect_uri": _redirect_uri(),
    }
    # Omitting scope asks TikTok to grant every permission enabled for the
    # approved developer app. A deployment may deliberately narrow it.
    explicit_scope = os.environ.get("TIKTOK_MARKETING_SCOPE", "").strip()
    if explicit_scope:
        params["scope"] = explicit_scope
    return f"{TIKTOK_AUTH_URL}?{urlencode(params)}"


async def ensure_tiktok_connection_indexes(db: Any) -> None:
    await db[TIKTOK_CREDENTIALS_COLLECTION].create_index(
        [("user_id", 1)], unique=True, name="tiktok_oauth_credentials_user_unique"
    )
    await db[TIKTOK_STATES_COLLECTION].create_index(
        [("nonce", 1)], unique=True, name="tiktok_oauth_state_nonce_unique"
    )
    await db[TIKTOK_STATES_COLLECTION].create_index(
        [("expires_at", 1)], expireAfterSeconds=0, name="tiktok_oauth_state_ttl"
    )


async def start_tiktok_connection(db: Any, user_id: str) -> dict[str, Any]:
    missing = tiktok_oauth_missing_configuration()
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "tiktok_oauth_not_configured",
                "message": "يجب ضبط إعدادات TikTok Marketing API في Backend أولًا.",
                "missing": missing,
            },
        )
    await ensure_tiktok_connection_indexes(db)
    nonce = uuid.uuid4().hex
    now = _utcnow()
    expires_at = now + TIKTOK_STATE_TTL
    await db[TIKTOK_STATES_COLLECTION].insert_one(
        {
            "nonce": nonce,
            "user_id": str(user_id),
            "purpose": TIKTOK_STATE_PURPOSE,
            "status": "pending",
            "created_at": now,
            "expires_at": expires_at,
        }
    )
    state_token = _encode_state(str(user_id), nonce)
    return {
        "authorization_url": _authorization_url(state_token),
        "expires_at": _iso(expires_at),
        "provider": TIKTOK_PROVIDER_ID,
    }


async def _consume_state(db: Any, state_token: str) -> dict[str, Any]:
    payload = _decode_state(state_token)
    now = _utcnow()
    query = {
        "nonce": payload["nonce"],
        "user_id": str(payload["user_id"]),
        "purpose": TIKTOK_STATE_PURPOSE,
        "status": "pending",
        "expires_at": {"$gt": now},
    }
    state_doc = await db[TIKTOK_STATES_COLLECTION].find_one(
        query, {"_id": 0, "nonce": 1}
    )
    if not state_doc:
        raise ValueError("tiktok_oauth_state_expired_or_used")
    result = await db[TIKTOK_STATES_COLLECTION].update_one(
        query, {"$set": {"status": "used", "used_at": now}}
    )
    if int(getattr(result, "modified_count", 0) or 0) != 1:
        raise ValueError("tiktok_oauth_state_expired_or_used")
    return payload


def _tiktok_payload(response: httpx.Response, operation: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise RuntimeError(f"{operation}_http_{response.status_code}")
    payload = response.json()
    if int(payload.get("code") or 0) != 0:
        code = str(payload.get("code") or "provider_error")
        raise RuntimeError(f"{operation}_provider_{code}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{operation}_missing_data")
    return data


async def _exchange_code(auth_code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.post(
            TIKTOK_TOKEN_URL,
            json={
                "app_id": os.environ["TIKTOK_MARKETING_APP_ID"],
                "secret": os.environ["TIKTOK_MARKETING_APP_SECRET"],
                "auth_code": auth_code,
            },
            headers={"Content-Type": "application/json"},
        )
    data = _tiktok_payload(response, "tiktok_token_exchange")
    if not data.get("access_token"):
        raise RuntimeError("tiktok_token_exchange_missing_access_token")
    return data


def _safe_callback_error(value: str | None) -> str:
    safe = str(value or "unknown").strip().lower().replace(" ", "_")
    return "".join(ch for ch in safe if ch.isalnum() or ch in {"_", "-"})[:64] or "unknown"


def _callback_redirect(*, outcome: str, code: str | None = None) -> str:
    params = {"tiktok": outcome}
    if code:
        params["code"] = _safe_callback_error(code)
    return f"{_frontend_url()}/integrations-v2?{urlencode(params)}"
