"""Secure Snapchat Marketing API OAuth primitives for Integrations V2.

The platform owns one Snapchat OAuth application. Every Mezan tenant receives a
separate encrypted access/refresh-token grant; app credentials never enter
public V2 projections, browser storage, or API responses.
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

SNAPCHAT_PROVIDER_ID = "snapchat_ads"
SNAPCHAT_AUTH_URL = "https://accounts.snapchat.com/login/oauth2/authorize"
SNAPCHAT_TOKEN_URL = "https://accounts.snapchat.com/login/oauth2/access_token"
SNAPCHAT_STATE_PURPOSE = "mezan_integrations_v2_snapchat_marketing_oauth"
SNAPCHAT_STATE_TTL = timedelta(minutes=15)
SNAPCHAT_SOURCE_MODE = "snapchat_marketing_oauth_v2"
SNAPCHAT_CREDENTIALS_COLLECTION = "mezan_snapchat_oauth_credentials_v2"
SNAPCHAT_STATES_COLLECTION = "mezan_snapchat_oauth_states_v2"
SNAPCHAT_REQUESTED_SCOPES = (
    "snapchat-marketing-api",
    "snapchat-offline-conversions-api",
)
SNAPCHAT_CAPABILITY_EVIDENCE = (
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
    explicit = os.environ.get("SNAPCHAT_MARKETING_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    public_backend = (
        os.environ.get("BACKEND_PUBLIC_URL", "").strip()
        or os.environ.get("BACKEND_URL", "").strip()
    )
    if public_backend:
        return public_backend.rstrip("/") + "/api/integrations-v2/snapchat/callback"
    return ""


def snapchat_oauth_missing_configuration() -> list[str]:
    required = {
        "SNAPCHAT_MARKETING_CLIENT_ID": os.environ.get(
            "SNAPCHAT_MARKETING_CLIENT_ID", ""
        ),
        "SNAPCHAT_MARKETING_CLIENT_SECRET": os.environ.get(
            "SNAPCHAT_MARKETING_CLIENT_SECRET", ""
        ),
        "SNAPCHAT_TOKEN_ENC_KEY": os.environ.get("SNAPCHAT_TOKEN_ENC_KEY", ""),
        "JWT_SECRET": os.environ.get("JWT_SECRET", ""),
        "SNAPCHAT_MARKETING_REDIRECT_URI": _redirect_uri(),
    }
    return [key for key, value in required.items() if not str(value or "").strip()]


def snapchat_oauth_configured() -> bool:
    return not snapchat_oauth_missing_configuration()


def _state_secret() -> str:
    value = (
        os.environ.get("SNAPCHAT_OAUTH_STATE_SECRET", "").strip()
        or os.environ.get("JWT_SECRET", "").strip()
    )
    if not value:
        raise RuntimeError("Snapchat OAuth state signing secret is required")
    return value


def _fernet() -> Any:
    from cryptography.fernet import Fernet, MultiFernet

    primary = os.environ.get("SNAPCHAT_TOKEN_ENC_KEY", "").strip()
    if not primary:
        raise RuntimeError("SNAPCHAT_TOKEN_ENC_KEY is required")
    keys = [Fernet(primary.encode("utf-8"))]
    rotation = os.environ.get("SNAPCHAT_TOKEN_ENC_KEY_OLD", "").strip()
    if rotation:
        keys.append(Fernet(rotation.encode("utf-8")))
    return MultiFernet(keys)


def encrypt_snapchat_token(value: str | None) -> bytes | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8"))


def decrypt_snapchat_token(value: bytes | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Snapchat token decryption failed") from exc


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _encode_state(user_id: str, nonce: str) -> str:
    now = _utcnow()
    payload = {
        "purpose": SNAPCHAT_STATE_PURPOSE,
        "user_id": str(user_id),
        "nonce": nonce,
        "iat": int(now.timestamp()),
        "exp": int((now + SNAPCHAT_STATE_TTL).timestamp()),
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
        raise ValueError("invalid_snapchat_oauth_state") from exc
    if payload.get("purpose") != SNAPCHAT_STATE_PURPOSE:
        raise ValueError("invalid_snapchat_oauth_state_purpose")
    if not payload.get("user_id") or not payload.get("nonce"):
        raise ValueError("invalid_snapchat_oauth_state_payload")
    if int(payload.get("exp") or 0) <= int(_utcnow().timestamp()):
        raise ValueError("snapchat_oauth_state_expired")
    return payload


def requested_snapchat_scopes() -> tuple[str, ...]:
    explicit = os.environ.get("SNAPCHAT_MARKETING_SCOPES", "").strip()
    if explicit:
        return tuple(
            dict.fromkeys(scope for scope in explicit.replace(",", " ").split() if scope)
        )
    return SNAPCHAT_REQUESTED_SCOPES


def _authorization_url(state: str) -> str:
    params = {
        "client_id": os.environ["SNAPCHAT_MARKETING_CLIENT_ID"],
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(requested_snapchat_scopes()),
        "state": state,
    }
    return f"{SNAPCHAT_AUTH_URL}?{urlencode(params)}"


async def ensure_snapchat_connection_indexes(db: Any) -> None:
    await db[SNAPCHAT_CREDENTIALS_COLLECTION].create_index(
        [("user_id", 1)], unique=True, name="snapchat_oauth_credentials_user_unique"
    )
    await db[SNAPCHAT_STATES_COLLECTION].create_index(
        [("nonce", 1)], unique=True, name="snapchat_oauth_state_nonce_unique"
    )
    await db[SNAPCHAT_STATES_COLLECTION].create_index(
        [("expires_at", 1)], expireAfterSeconds=0, name="snapchat_oauth_state_ttl"
    )


async def start_snapchat_connection(db: Any, user_id: str) -> dict[str, Any]:
    missing = snapchat_oauth_missing_configuration()
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "snapchat_oauth_not_configured",
                "message": "يجب ضبط إعدادات Snapchat Marketing API في Backend أولًا.",
                "missing": missing,
            },
        )
    await ensure_snapchat_connection_indexes(db)
    nonce = uuid.uuid4().hex
    now = _utcnow()
    expires_at = now + SNAPCHAT_STATE_TTL
    await db[SNAPCHAT_STATES_COLLECTION].insert_one(
        {
            "nonce": nonce,
            "user_id": str(user_id),
            "purpose": SNAPCHAT_STATE_PURPOSE,
            "status": "pending",
            "created_at": now,
            "expires_at": expires_at,
        }
    )
    state_token = _encode_state(str(user_id), nonce)
    return {
        "authorization_url": _authorization_url(state_token),
        "expires_at": _iso(expires_at),
        "provider": SNAPCHAT_PROVIDER_ID,
        "scopes": list(requested_snapchat_scopes()),
    }


async def _consume_state(db: Any, state_token: str) -> dict[str, Any]:
    payload = _decode_state(state_token)
    now = _utcnow()
    query = {
        "nonce": payload["nonce"],
        "user_id": str(payload["user_id"]),
        "purpose": SNAPCHAT_STATE_PURPOSE,
        "status": "pending",
        "expires_at": {"$gt": now},
    }
    state_doc = await db[SNAPCHAT_STATES_COLLECTION].find_one(
        query, {"_id": 0, "nonce": 1}
    )
    if not state_doc:
        raise ValueError("snapchat_oauth_state_expired_or_used")
    result = await db[SNAPCHAT_STATES_COLLECTION].update_one(
        query, {"$set": {"status": "used", "used_at": now}}
    )
    if int(getattr(result, "modified_count", 0) or 0) != 1:
        raise ValueError("snapchat_oauth_state_expired_or_used")
    return payload


async def _exchange_code(code: str) -> dict[str, Any]:
    form_data = {
        "code": code,
        "client_id": os.environ["SNAPCHAT_MARKETING_CLIENT_ID"],
        "client_secret": os.environ["SNAPCHAT_MARKETING_CLIENT_SECRET"],
        "grant_type": "authorization_code",
        "redirect_uri": _redirect_uri(),
    }
    basic_data = {
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": _redirect_uri(),
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.post(SNAPCHAT_TOKEN_URL, data=form_data)
        if response.status_code == 400 and "invalid_client" in (
            response.text or ""
        ).lower():
            response = await client.post(
                SNAPCHAT_TOKEN_URL,
                data=basic_data,
                auth=(
                    os.environ["SNAPCHAT_MARKETING_CLIENT_ID"],
                    os.environ["SNAPCHAT_MARKETING_CLIENT_SECRET"],
                ),
            )
    if response.status_code >= 400:
        raise RuntimeError(f"snapchat_token_exchange_http_{response.status_code}")
    payload = response.json()
    if not payload.get("access_token") or not payload.get("refresh_token"):
        raise RuntimeError("snapchat_token_exchange_missing_tokens")
    return payload


def _safe_callback_error(value: str | None) -> str:
    safe = str(value or "unknown").strip().lower().replace(" ", "_")
    return "".join(ch for ch in safe if ch.isalnum() or ch in {"_", "-"})[:64] or "unknown"


def _callback_redirect(*, outcome: str, code: str | None = None) -> str:
    params = {"snapchat": outcome}
    if code:
        params["code"] = _safe_callback_error(code)
    return f"{_frontend_url()}/integrations-v2?{urlencode(params)}"
