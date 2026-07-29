"""Google OAuth foundation for Apps & Integrations Control Center V2.

The module owns one Google OAuth grant and projects its verified read-side
capabilities into the four Google provider cards. OAuth credentials are stored
only in a dedicated encrypted collection and never enter the public V2
collections or API responses.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

GOOGLE_PROVIDER_IDS = frozenset(
    {
        "google_analytics_4",
        "google_search_console",
        "google_merchant_center",
        "google_ads",
    }
)
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_GA4_SUMMARIES_URL = (
    "https://analyticsadmin.googleapis.com/v1beta/accountSummaries"
)
GOOGLE_SEARCH_CONSOLE_SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"
GOOGLE_MERCHANT_ACCOUNTS_URL = (
    "https://merchantapi.googleapis.com/accounts/v1/accounts"
)
GOOGLE_STATE_PURPOSE = "mezan_integrations_v2_google_oauth"
GOOGLE_STATE_TTL = timedelta(minutes=15)
GOOGLE_SOURCE_MODE = "google_oauth_v2"
GOOGLE_CREDENTIALS_COLLECTION = "mezan_google_oauth_credentials_v2"
GOOGLE_STATES_COLLECTION = "mezan_google_oauth_states_v2"

# Merchant API currently exposes one OAuth scope for both reads and writes.
# Mezan requests it but exposes only read-side capabilities; all provider writes
# remain blocked by the V2 mutation policy.
GOOGLE_SCOPE_BY_PROVIDER = {
    "google_analytics_4": "https://www.googleapis.com/auth/analytics.readonly",
    "google_search_console": "https://www.googleapis.com/auth/webmasters.readonly",
    "google_merchant_center": "https://www.googleapis.com/auth/content",
    "google_ads": "https://www.googleapis.com/auth/adwords",
}
GOOGLE_PERMISSION_ALIAS = {
    "google_analytics_4": "analytics.readonly",
    "google_search_console": "webmasters.readonly",
    # Internal read-policy alias backed by Google's broader content scope.
    "google_merchant_center": "content.readonly",
    "google_ads": "adwords",
}
GOOGLE_IDENTITY_SCOPES = (
    "openid",
    "email",
    "profile",
)
GOOGLE_REQUESTED_SCOPES = (
    *GOOGLE_IDENTITY_SCOPES,
    *GOOGLE_SCOPE_BY_PROVIDER.values(),
)
GOOGLE_CAPABILITY_EVIDENCE = {
    "google_analytics_4": ["analytics.read", "conversions.read"],
    "google_search_console": ["search_performance.read"],
    "google_merchant_center": ["products.read", "diagnostics.read"],
    "google_ads": ["campaigns.read", "insights.read", "conversions.read"],
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")


def _redirect_uri() -> str:
    explicit = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    public_backend = (
        os.environ.get("BACKEND_PUBLIC_URL", "").strip()
        or os.environ.get("BACKEND_URL", "").strip()
    )
    if public_backend:
        return (
            public_backend.rstrip("/")
            + "/api/integrations-v2/google/callback"
        )
    return ""


def google_oauth_missing_configuration() -> list[str]:
    required = {
        "GOOGLE_OAUTH_CLIENT_ID": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
        "GOOGLE_OAUTH_CLIENT_SECRET": os.environ.get(
            "GOOGLE_OAUTH_CLIENT_SECRET", ""
        ),
        "GOOGLE_TOKEN_ENC_KEY": os.environ.get("GOOGLE_TOKEN_ENC_KEY", ""),
        "JWT_SECRET": os.environ.get("JWT_SECRET", ""),
        "GOOGLE_OAUTH_REDIRECT_URI": _redirect_uri(),
    }
    return [key for key, value in required.items() if not str(value or "").strip()]


def google_oauth_configured() -> bool:
    return not google_oauth_missing_configuration()


def _state_secret() -> str:
    value = os.environ.get("JWT_SECRET", "").strip()
    if not value:
        raise RuntimeError("JWT_SECRET is required for Google OAuth state signing")
    return value


def _fernet() -> Any:
    from cryptography.fernet import Fernet, MultiFernet

    primary = os.environ.get("GOOGLE_TOKEN_ENC_KEY", "").strip()
    if not primary:
        raise RuntimeError("GOOGLE_TOKEN_ENC_KEY is required")
    keys = [Fernet(primary.encode("utf-8"))]
    rotation = os.environ.get("GOOGLE_TOKEN_ENC_KEY_OLD", "").strip()
    if rotation:
        keys.append(Fernet(rotation.encode("utf-8")))
    return MultiFernet(keys)


def encrypt_google_token(value: str | None) -> bytes | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8"))


def decrypt_google_token(value: bytes | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Google token decryption failed") from exc


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _encode_state(user_id: str, nonce: str) -> str:
    now = _utcnow()
    payload = {
        "purpose": GOOGLE_STATE_PURPOSE,
        "user_id": str(user_id),
        "nonce": nonce,
        "iat": int(now.timestamp()),
        "exp": int((now + GOOGLE_STATE_TTL).timestamp()),
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
        raise ValueError("invalid_google_oauth_state") from exc
    if payload.get("purpose") != GOOGLE_STATE_PURPOSE:
        raise ValueError("invalid_google_oauth_state_purpose")
    if not payload.get("user_id") or not payload.get("nonce"):
        raise ValueError("invalid_google_oauth_state_payload")
    if int(payload.get("exp") or 0) <= int(_utcnow().timestamp()):
        raise ValueError("google_oauth_state_expired")
    return payload


def _authorization_url(state: str) -> str:
    params = {
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(GOOGLE_REQUESTED_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def ensure_google_connection_indexes(db: Any) -> None:
    await db[GOOGLE_CREDENTIALS_COLLECTION].create_index(
        [("user_id", 1)], unique=True, name="google_oauth_credentials_user_unique"
    )
    await db[GOOGLE_STATES_COLLECTION].create_index(
        [("nonce", 1)], unique=True, name="google_oauth_state_nonce_unique"
    )
    await db[GOOGLE_STATES_COLLECTION].create_index(
        [("expires_at", 1)], expireAfterSeconds=0, name="google_oauth_state_ttl"
    )


async def start_google_connection(db: Any, user_id: str) -> dict[str, Any]:
    missing = google_oauth_missing_configuration()
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "google_oauth_not_configured",
                "message": "يجب ضبط إعدادات Google OAuth في بيئة Backend أولًا.",
                "missing": missing,
            },
        )
    await ensure_google_connection_indexes(db)
    nonce = uuid.uuid4().hex
    now = _utcnow()
    expires_at = now + GOOGLE_STATE_TTL
    await db[GOOGLE_STATES_COLLECTION].insert_one(
        {
            "nonce": nonce,
            "user_id": str(user_id),
            "purpose": GOOGLE_STATE_PURPOSE,
            "status": "pending",
            "created_at": now,
            "expires_at": expires_at,
        }
    )
    state_token = _encode_state(str(user_id), nonce)
    return {
        "authorization_url": _authorization_url(state_token),
        "expires_at": _iso(expires_at),
        "providers": sorted(GOOGLE_PROVIDER_IDS),
    }


async def _consume_state(db: Any, state_token: str) -> dict[str, Any]:
    payload = _decode_state(state_token)
    now = _utcnow()
    query = {
        "nonce": payload["nonce"],
        "user_id": str(payload["user_id"]),
        "purpose": GOOGLE_STATE_PURPOSE,
        "status": "pending",
        "expires_at": {"$gt": now},
    }
    state_doc = await db[GOOGLE_STATES_COLLECTION].find_one(
        query, {"_id": 0, "nonce": 1}
    )
    if not state_doc:
        raise ValueError("google_oauth_state_expired_or_used")
    result = await db[GOOGLE_STATES_COLLECTION].update_one(
        query, {"$set": {"status": "used", "used_at": now}}
    )
    if int(getattr(result, "modified_count", 0) or 0) != 1:
        raise ValueError("google_oauth_state_expired_or_used")
    return payload


async def _exchange_code(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
    if response.status_code >= 400:
        raise RuntimeError(f"google_token_exchange_http_{response.status_code}")
    payload = response.json()
    if not payload.get("access_token"):
        raise RuntimeError("google_token_exchange_missing_access_token")
    return payload


def _safe_callback_error(value: str | None) -> str:
    safe = str(value or "unknown").strip().lower().replace(" ", "_")
    return "".join(ch for ch in safe if ch.isalnum() or ch in {"_", "-"})[:64] or "unknown"


def _callback_redirect(*, outcome: str, code: str | None = None) -> str:
    params = {"google": outcome}
    if code:
        params["code"] = _safe_callback_error(code)
    return f"{_frontend_url()}/integrations-v2?{urlencode(params)}"
