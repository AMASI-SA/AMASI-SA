"""Secure Meta Business OAuth primitives for Apps & Integrations V2.

One Meta Business app belongs to Mezan. Each merchant receives an encrypted,
tenant-scoped long-lived user grant. App credentials and access tokens never
enter public V2 projections, browser storage, or API responses.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

META_PROVIDER_ID = "meta_ads"
META_STATE_PURPOSE = "mezan_integrations_v2_meta_business_oauth"
META_STATE_TTL = timedelta(minutes=15)
META_SOURCE_MODE = "meta_business_oauth_v2"
META_CREDENTIALS_COLLECTION = "mezan_meta_oauth_credentials_v2"
META_STATES_COLLECTION = "mezan_meta_oauth_states_v2"
META_ASSETS_COLLECTION = "mezan_meta_assets_v2"
META_DEFAULT_GRAPH_VERSION = "v25.0"
META_DEFAULT_SCOPES = (
    "ads_read",
    "ads_management",
    "business_management",
    "catalog_management",
    "pages_show_list",
    "pages_read_engagement",
    "leads_retrieval",
    "instagram_basic",
    "instagram_manage_insights",
)
META_CAPABILITY_EVIDENCE = (
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


def meta_graph_version() -> str:
    value = os.environ.get("META_GRAPH_API_VERSION", META_DEFAULT_GRAPH_VERSION).strip()
    if not re.fullmatch(r"v\d{1,2}\.\d", value):
        raise RuntimeError("META_GRAPH_API_VERSION must look like v25.0")
    return value


def meta_graph_base() -> str:
    return f"https://graph.facebook.com/{meta_graph_version()}"


def meta_authorization_url_base() -> str:
    return f"https://www.facebook.com/{meta_graph_version()}/dialog/oauth"


def _redirect_uri() -> str:
    explicit = os.environ.get("META_BUSINESS_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    public_backend = (
        os.environ.get("BACKEND_PUBLIC_URL", "").strip()
        or os.environ.get("BACKEND_URL", "").strip()
    )
    if public_backend:
        return public_backend.rstrip("/") + "/api/integrations-v2/meta/callback"
    return ""


def meta_oauth_missing_configuration() -> list[str]:
    required = {
        "META_BUSINESS_APP_ID": os.environ.get("META_BUSINESS_APP_ID", ""),
        "META_BUSINESS_APP_SECRET": os.environ.get("META_BUSINESS_APP_SECRET", ""),
        "META_TOKEN_ENC_KEY": os.environ.get("META_TOKEN_ENC_KEY", ""),
        "JWT_SECRET": os.environ.get("JWT_SECRET", ""),
        "META_BUSINESS_REDIRECT_URI": _redirect_uri(),
    }
    return [key for key, value in required.items() if not str(value or "").strip()]


def meta_oauth_configured() -> bool:
    return not meta_oauth_missing_configuration()


def _state_secret() -> str:
    value = (
        os.environ.get("META_OAUTH_STATE_SECRET", "").strip()
        or os.environ.get("JWT_SECRET", "").strip()
    )
    if not value:
        raise RuntimeError("Meta OAuth state signing secret is required")
    return value


def _fernet() -> Any:
    from cryptography.fernet import Fernet, MultiFernet

    primary = os.environ.get("META_TOKEN_ENC_KEY", "").strip()
    if not primary:
        raise RuntimeError("META_TOKEN_ENC_KEY is required")
    keys = [Fernet(primary.encode("utf-8"))]
    rotation = os.environ.get("META_TOKEN_ENC_KEY_OLD", "").strip()
    if rotation:
        keys.append(Fernet(rotation.encode("utf-8")))
    return MultiFernet(keys)


def encrypt_meta_token(value: str | None) -> bytes | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8"))


def decrypt_meta_token(value: bytes | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Meta token decryption failed") from exc


def meta_appsecret_proof(access_token: str) -> str:
    secret = os.environ.get("META_BUSINESS_APP_SECRET", "").strip()
    if not secret:
        raise RuntimeError("META_BUSINESS_APP_SECRET is required")
    return hmac.new(
        secret.encode("utf-8"), access_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _encode_state(user_id: str, nonce: str) -> str:
    now = _utcnow()
    payload = {
        "purpose": META_STATE_PURPOSE,
        "user_id": str(user_id),
        "nonce": nonce,
        "iat": int(now.timestamp()),
        "exp": int((now + META_STATE_TTL).timestamp()),
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
        raise ValueError("invalid_meta_oauth_state") from exc
    if payload.get("purpose") != META_STATE_PURPOSE:
        raise ValueError("invalid_meta_oauth_state_purpose")
    if not payload.get("user_id") or not payload.get("nonce"):
        raise ValueError("invalid_meta_oauth_state_payload")
    if int(payload.get("exp") or 0) <= int(_utcnow().timestamp()):
        raise ValueError("meta_oauth_state_expired")
    return payload


def requested_meta_scopes() -> tuple[str, ...]:
    explicit = os.environ.get("META_BUSINESS_SCOPES", "").strip()
    if explicit:
        return tuple(
            dict.fromkeys(scope for scope in explicit.replace(",", " ").split() if scope)
        )
    return META_DEFAULT_SCOPES


def _authorization_url(state: str) -> str:
    params = {
        "client_id": os.environ["META_BUSINESS_APP_ID"],
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": ",".join(requested_meta_scopes()),
        "state": state,
        "auth_type": "rerequest",
    }
    return f"{meta_authorization_url_base()}?{urlencode(params)}"


async def ensure_meta_connection_indexes(db: Any) -> None:
    await db[META_CREDENTIALS_COLLECTION].create_index(
        [("user_id", 1)], unique=True, name="meta_oauth_credentials_user_unique"
    )
    await db[META_STATES_COLLECTION].create_index(
        [("nonce", 1)], unique=True, name="meta_oauth_state_nonce_unique"
    )
    await db[META_STATES_COLLECTION].create_index(
        [("expires_at", 1)], expireAfterSeconds=0, name="meta_oauth_state_ttl"
    )
    await db[META_ASSETS_COLLECTION].create_index(
        [("user_id", 1), ("asset_type", 1), ("external_asset_id", 1)],
        unique=True,
        name="meta_assets_user_type_external_unique",
    )


async def start_meta_connection(db: Any, user_id: str) -> dict[str, Any]:
    missing = meta_oauth_missing_configuration()
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "meta_oauth_not_configured",
                "message": "يجب ضبط إعدادات Meta Business OAuth في Backend أولًا.",
                "missing": missing,
            },
        )
    await ensure_meta_connection_indexes(db)
    nonce = uuid.uuid4().hex
    now = _utcnow()
    expires_at = now + META_STATE_TTL
    await db[META_STATES_COLLECTION].insert_one(
        {
            "nonce": nonce,
            "user_id": str(user_id),
            "purpose": META_STATE_PURPOSE,
            "status": "pending",
            "created_at": now,
            "expires_at": expires_at,
        }
    )
    state_token = _encode_state(str(user_id), nonce)
    return {
        "authorization_url": _authorization_url(state_token),
        "expires_at": _iso(expires_at),
        "provider": META_PROVIDER_ID,
        "scopes": list(requested_meta_scopes()),
        "graph_version": meta_graph_version(),
    }


async def _consume_state(db: Any, state_token: str) -> dict[str, Any]:
    payload = _decode_state(state_token)
    now = _utcnow()
    query = {
        "nonce": payload["nonce"],
        "user_id": str(payload["user_id"]),
        "purpose": META_STATE_PURPOSE,
        "status": "pending",
        "expires_at": {"$gt": now},
    }
    state_doc = await db[META_STATES_COLLECTION].find_one(
        query, {"_id": 0, "nonce": 1}
    )
    if not state_doc:
        raise ValueError("meta_oauth_state_expired_or_used")
    result = await db[META_STATES_COLLECTION].update_one(
        query, {"$set": {"status": "used", "used_at": now}}
    )
    if int(getattr(result, "modified_count", 0) or 0) != 1:
        raise ValueError("meta_oauth_state_expired_or_used")
    return payload


async def _exchange_code(code: str) -> dict[str, Any]:
    params = {
        "client_id": os.environ["META_BUSINESS_APP_ID"],
        "client_secret": os.environ["META_BUSINESS_APP_SECRET"],
        "redirect_uri": _redirect_uri(),
        "code": code,
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.get(
            f"{meta_graph_base()}/oauth/access_token", params=params
        )
    if response.status_code >= 400:
        raise RuntimeError(f"meta_token_exchange_http_{response.status_code}")
    payload = response.json()
    if not payload.get("access_token"):
        raise RuntimeError("meta_token_exchange_missing_access_token")
    return payload


async def _exchange_long_lived_token(short_token: str) -> dict[str, Any]:
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": os.environ["META_BUSINESS_APP_ID"],
        "client_secret": os.environ["META_BUSINESS_APP_SECRET"],
        "fb_exchange_token": short_token,
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.get(
            f"{meta_graph_base()}/oauth/access_token", params=params
        )
    if response.status_code >= 400:
        raise RuntimeError(f"meta_long_lived_exchange_http_{response.status_code}")
    payload = response.json()
    if not payload.get("access_token"):
        raise RuntimeError("meta_long_lived_exchange_missing_access_token")
    return payload


async def debug_meta_token(access_token: str) -> dict[str, Any]:
    app_access_token = (
        f"{os.environ['META_BUSINESS_APP_ID']}|"
        f"{os.environ['META_BUSINESS_APP_SECRET']}"
    )
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{meta_graph_base()}/debug_token",
            params={
                "input_token": access_token,
                "access_token": app_access_token,
            },
        )
    if response.status_code >= 400:
        raise RuntimeError(f"meta_debug_token_http_{response.status_code}")
    data = (response.json() or {}).get("data") or {}
    if not data.get("is_valid"):
        raise RuntimeError("meta_debug_token_invalid")
    if str(data.get("app_id") or "") != os.environ["META_BUSINESS_APP_ID"]:
        raise RuntimeError("meta_debug_token_wrong_app")
    return data


def _safe_callback_error(value: str | None) -> str:
    safe = str(value or "unknown").strip().lower().replace(" ", "_")
    return "".join(ch for ch in safe if ch.isalnum() or ch in {"_", "-"})[:64] or "unknown"


def _callback_redirect(*, outcome: str, code: str | None = None) -> str:
    params = {"meta": outcome}
    if code:
        params["code"] = _safe_callback_error(code)
    return f"{_frontend_url()}/integrations-v2?{urlencode(params)}"
