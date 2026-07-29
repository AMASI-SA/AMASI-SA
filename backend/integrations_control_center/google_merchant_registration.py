"""Owner-only, one-time Merchant API developer registration for Amasi.

This module is deliberately narrow:

* it can register only the configured Amasi Merchant Center account;
* the developer email must match the Google identity already connected to Mezan;
* it never returns OAuth tokens or provider response bodies;
* it only refreshes the Merchant Center projection in Integrations V2;
* it does not touch products, campaigns, accounting, Salla, Qoyod, or orders.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from .google_oauth_security import (
    GOOGLE_CAPABILITY_EVIDENCE,
    GOOGLE_CREDENTIALS_COLLECTION,
    GOOGLE_PERMISSION_ALIAS,
    GOOGLE_SCOPE_BY_PROVIDER,
    GOOGLE_SOURCE_MODE,
    GOOGLE_TOKEN_URL,
    _iso,
    _utcnow,
    decrypt_google_token,
    encrypt_google_token,
)

MERCHANT_PROVIDER = "google_merchant_center"
DEFAULT_MERCHANT_ACCOUNT_ID = "626368690"
DEFAULT_MERCHANT_DEVELOPER_EMAIL = "amasi.jewelery@gmail.com"
MERCHANT_ACCOUNTS_URL = "https://merchantapi.googleapis.com/accounts/v1/accounts"
MERCHANT_REGISTERED_ACCOUNT_URL = (
    "https://merchantapi.googleapis.com/accounts/v1/"
    "accounts:getAccountForGcpRegistration"
)


def _merchant_account_id() -> str:
    value = (
        os.environ.get("GOOGLE_MERCHANT_ACCOUNT_ID", "").strip()
        or DEFAULT_MERCHANT_ACCOUNT_ID
    )
    if not value.isdigit() or not 6 <= len(value) <= 20:
        raise RuntimeError("GOOGLE_MERCHANT_ACCOUNT_ID is invalid")
    return value


def _approved_developer_email() -> str:
    return (
        os.environ.get("GOOGLE_MERCHANT_DEVELOPER_EMAIL", "").strip().lower()
        or DEFAULT_MERCHANT_DEVELOPER_EMAIL
    )


def _registration_url(account_id: str) -> str:
    return (
        "https://merchantapi.googleapis.com/accounts/v1/accounts/"
        f"{account_id}/developerRegistration:registerGcp"
    )


def _scope_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {item for item in value.split() if item}
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_http_code(prefix: str, status_code: int) -> str:
    return f"{prefix}_http_{int(status_code)}"


async def _fresh_google_context(db: Any, user_id: str) -> dict[str, Any]:
    credentials = await db[GOOGLE_CREDENTIALS_COLLECTION].find_one(
        {"user_id": user_id},
        {
            "_id": 0,
            "access_token_ciphertext": 1,
            "refresh_token_ciphertext": 1,
            "expires_at": 1,
            "scope": 1,
            "google_subject": 1,
            "google_email": 1,
            "token_type": 1,
        },
    )
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "google_oauth_connection_missing",
                "message": "اربط حساب Google أولًا قبل تسجيل Merchant API.",
            },
        )

    granted_scopes = _scope_set(credentials.get("scope"))
    required_scope = GOOGLE_SCOPE_BY_PROVIDER[MERCHANT_PROVIDER]
    if required_scope not in granted_scopes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "google_merchant_content_scope_missing",
                "message": "صلاحية Merchant Center غير ممنوحة للحساب المرتبط.",
            },
        )

    google_email = str(credentials.get("google_email") or "").strip().lower()
    approved_email = _approved_developer_email()
    if not google_email or google_email != approved_email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "google_merchant_developer_email_mismatch",
                "message": (
                    "يجب تنفيذ التسجيل من حساب Google المعتمد لمتجر أماسي."
                ),
            },
        )

    access_token = decrypt_google_token(
        credentials.get("access_token_ciphertext")
    )
    expires_at = _as_utc(credentials.get("expires_at"))
    now = _utcnow()
    if access_token and expires_at and expires_at > now + timedelta(minutes=2):
        return {
            "access_token": access_token,
            "granted_scopes": granted_scopes,
            "identity": {
                "sub": credentials.get("google_subject"),
                "email": google_email,
            },
            "expires_in": max(60, int((expires_at - now).total_seconds())),
        }

    refresh_token = decrypt_google_token(
        credentials.get("refresh_token_ciphertext")
    )
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "google_refresh_token_missing",
                "message": "أعد ربط Google للحصول على جلسة طويلة الأجل.",
            },
        )

    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
                "client_secret": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": _safe_http_code(
                    "google_refresh_token", response.status_code
                ),
                "message": "انتهت جلسة Google؛ أعد الربط ثم أعد المحاولة.",
            },
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "google_refresh_token_invalid_json",
                "message": "تعذر تحديث جلسة Google.",
            },
        ) from exc
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "google_refresh_token_missing_access_token",
                "message": "لم تعد Google رمز وصول جديدًا.",
            },
        )
    expires_in = max(60, int(payload.get("expires_in") or 3600))
    refreshed_at = _utcnow()
    await db[GOOGLE_CREDENTIALS_COLLECTION].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "access_token_ciphertext": encrypt_google_token(access_token),
                "expires_at": refreshed_at + timedelta(seconds=expires_in),
                "token_type": str(payload.get("token_type") or "Bearer")[:32],
                "updated_at": refreshed_at,
            }
        },
    )
    return {
        "access_token": access_token,
        "granted_scopes": granted_scopes,
        "identity": {
            "sub": credentials.get("google_subject"),
            "email": google_email,
        },
        "expires_in": expires_in,
    }


async def _registration_state(
    client: httpx.AsyncClient,
    *,
    headers: dict[str, str],
    account_id: str,
) -> str | None:
    response = await client.get(MERCHANT_REGISTERED_ACCOUNT_URL, headers=headers)
    if response.status_code >= 400:
        return None
    try:
        name = str((response.json() or {}).get("name") or "")
    except ValueError:
        return None
    return name if name == f"accounts/{account_id}" else name or None


async def _register_project(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    account_id: str,
    developer_email: str,
) -> tuple[str, str | None]:
    headers = {"Authorization": f"Bearer {access_token}"}
    response = await client.post(
        _registration_url(account_id),
        headers=headers,
        json={"developerEmail": developer_email},
    )
    if 200 <= response.status_code < 300:
        try:
            registration_name = str((response.json() or {}).get("name") or "")
        except ValueError:
            registration_name = ""
        return "registered", registration_name or None
    if response.status_code == 409:
        registered_account = await _registration_state(
            client, headers=headers, account_id=account_id
        )
        if registered_account == f"accounts/{account_id}":
            return "already_registered", (
                f"accounts/{account_id}/developerRegistration"
            )
        if registered_account:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "google_merchant_project_registered_elsewhere",
                    "message": "مشروع Google Cloud مسجل على حساب Merchant آخر.",
                },
            )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": _safe_http_code(
                "google_merchant_registration", response.status_code
            ),
            "message": "تعذر تسجيل مشروع Google Cloud لدى Merchant Center.",
        },
    )


async def _discover_merchant_accounts(
    client: httpx.AsyncClient,
    *,
    access_token: str,
) -> tuple[list[dict[str, Any]], str | None]:
    headers = {"Authorization": f"Bearer {access_token}"}
    response = None
    for delay_seconds in (0, 2, 5):
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        response = await client.get(
            MERCHANT_ACCOUNTS_URL,
            headers=headers,
            params={"pageSize": 100},
        )
        if response.status_code != 401:
            break
    if response is None:
        return [], "network_error"
    if response.status_code >= 400:
        return [], f"http_{response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return [], "invalid_json"
    accounts: list[dict[str, Any]] = []
    for account in (payload or {}).get("accounts") or []:
        resource = str(account.get("name") or "")
        account_id = resource.rsplit("/", 1)[-1] if resource else ""
        if not account_id:
            continue
        accounts.append(
            {
                "external_account_id": account_id,
                "display_name": account.get("accountName")
                or account.get("displayName")
                or account_id,
                "timezone": (account.get("timeZone") or {}).get("id")
                if isinstance(account.get("timeZone"), dict)
                else None,
                "currency": None,
            }
        )
    return accounts, None


async def _persist_merchant_result(
    db: Any,
    *,
    user_id: str,
    accounts: list[dict[str, Any]],
    provider_error: str | None,
    registration_state: str,
) -> str:
    now = _utcnow()
    now_iso = _iso(now)
    run_id = str(uuid.uuid4())
    observation_id = str(uuid.uuid4())
    has_data = bool(accounts)
    data_quality = "good" if has_data else "missing"
    health_status = "healthy" if has_data else "degraded"
    health_score = 100 if has_data else 75
    capability_evidence = (
        GOOGLE_CAPABILITY_EVIDENCE[MERCHANT_PROVIDER] if has_data else []
    )
    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": MERCHANT_PROVIDER},
        {
            "$set": {
                "user_id": user_id,
                "provider": MERCHANT_PROVIDER,
                "connection_status": "connected",
                "connection_provenance": "api_connection",
                "source_mode": GOOGLE_SOURCE_MODE,
                "last_sync_at": now_iso,
                "data_delay_minutes": 0 if has_data else None,
                "data_quality": data_quality,
                "has_data": has_data,
                "capability_evidence": capability_evidence,
                "permissions_observed": True,
                "permission_observation_id": observation_id,
                "checked_at": now_iso,
                "updated_at": now_iso,
            },
            "$setOnInsert": {"created_at": now_iso},
        },
        upsert=True,
    )
    permission_alias = GOOGLE_PERMISSION_ALIAS[MERCHANT_PROVIDER]
    await db.mezan_integration_permissions_v2.update_one(
        {
            "user_id": user_id,
            "provider": MERCHANT_PROVIDER,
            "permission_key": permission_alias,
        },
        {
            "$set": {
                "user_id": user_id,
                "provider": MERCHANT_PROVIDER,
                "permission_key": permission_alias,
                "permission_status": "current",
                "permission_observation_id": observation_id,
                "source_mode": GOOGLE_SOURCE_MODE,
                "observed_at": now_iso,
            }
        },
        upsert=True,
    )
    await db.mezan_integration_accounts_v2.delete_many(
        {"user_id": user_id, "provider": MERCHANT_PROVIDER}
    )
    if accounts:
        await db.mezan_integration_accounts_v2.insert_many(
            [
                {
                    "user_id": user_id,
                    "provider": MERCHANT_PROVIDER,
                    "mezan_integration_account_id": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            (
                                "mezan-google:"
                                f"{user_id}:{MERCHANT_PROVIDER}:"
                                f"{account['external_account_id']}"
                            ),
                        )
                    ),
                    "external_account_id": account["external_account_id"],
                    "ad_account_id": None,
                    "display_name": account.get("display_name")
                    or account["external_account_id"],
                    "currency": account.get("currency"),
                    "timezone": account.get("timezone"),
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                    "permissions": [permission_alias],
                    "permissions_observed": True,
                    "capability_evidence": capability_evidence,
                    "has_data": True,
                    "last_sync_at": now_iso,
                    "data_delay_minutes": 0,
                    "health_score": 100,
                    "source_mode": GOOGLE_SOURCE_MODE,
                    "last_observed_at": now_iso,
                    "created_at": now_iso,
                }
                for account in accounts[:200]
            ]
        )
    await db.mezan_integration_health_v2.insert_one(
        {
            "user_id": user_id,
            "provider": MERCHANT_PROVIDER,
            "health_status": health_status,
            "health_score": health_score,
            "data_quality": data_quality,
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "data_delay_minutes": 0 if has_data else None,
            "checked_at": now_iso,
            "source_mode": GOOGLE_SOURCE_MODE,
            "run_id": run_id,
        }
    )
    error_ref = None
    if provider_error:
        error_ref = str(uuid.uuid4())
        await db.mezan_integration_errors_v2.insert_one(
            {
                "error_id": error_ref,
                "user_id": user_id,
                "provider": MERCHANT_PROVIDER,
                "code": f"google_discovery_{provider_error}",
                "message": (
                    "تم تسجيل Merchant API، لكن اكتشاف الحساب ما زال قيد "
                    "التهيئة ويمكن إعادة المحاولة."
                ),
                "occurred_at": now_iso,
                "retryable": True,
                "source_mode": GOOGLE_SOURCE_MODE,
                "run_id": run_id,
            }
        )
    else:
        # Remove only resolved, retryable discovery diagnostics so the card no
        # longer displays a stale red error after successful registration.
        await db.mezan_integration_errors_v2.delete_many(
            {
                "user_id": user_id,
                "provider": MERCHANT_PROVIDER,
                "retryable": True,
                "code": {"$in": [
                    "google_discovery_http_401",
                    "google_discovery_http_403",
                    "google_discovery_http_404",
                ]},
            }
        )
    await db.mezan_integration_sync_runs_v2.insert_one(
        {
            "run_id": run_id,
            "user_id": user_id,
            "provider": MERCHANT_PROVIDER,
            "run_type": "google_merchant_developer_registration",
            "status": "complete" if has_data else "partial",
            "started_at": now_iso,
            "finished_at": now_iso,
            "source_mode": GOOGLE_SOURCE_MODE,
            "summary": {
                "registration_state": registration_state,
                "account_count": len(accounts),
                "source_only": True,
                "accounting_write_reached": False,
                "qoyod_write_reached": False,
                "campaign_write_reached": False,
            },
            "error": {"error_id": error_ref} if error_ref else None,
        }
    )
    return run_id


async def register_google_merchant_developer(
    db: Any,
    user_id: str,
) -> dict[str, Any]:
    account_id = _merchant_account_id()
    context = await _fresh_google_context(db, user_id)
    developer_email = str(context["identity"].get("email") or "").lower()
    access_token = str(context["access_token"])
    async with httpx.AsyncClient(timeout=25.0) as client:
        registration_state, registration_name = await _register_project(
            client,
            access_token=access_token,
            account_id=account_id,
            developer_email=developer_email,
        )
        accounts, provider_error = await _discover_merchant_accounts(
            client,
            access_token=access_token,
        )
    run_id = await _persist_merchant_result(
        db,
        user_id=user_id,
        accounts=accounts,
        provider_error=provider_error,
        registration_state=registration_state,
    )
    target_found = any(
        account.get("external_account_id") == account_id for account in accounts
    )
    return {
        "provider": MERCHANT_PROVIDER,
        "status": "complete" if target_found else "registered_pending_discovery",
        "run_id": run_id,
        "merchant_account_id": account_id,
        "developer_email_verified": True,
        "registration_state": registration_state,
        "registration_name": registration_name,
        "account_count": len(accounts),
        "target_account_found": target_found,
        "retry_after_seconds": 0 if target_found else 300,
        "source_only": True,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
        "campaign_write_reached": False,
    }


def attach_google_merchant_registration_route(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.post(
        "/google_merchant_center/register-developer",
        name="register_google_merchant_developer",
    )
    async def google_merchant_register_developer(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await register_google_merchant_developer(db, str(owner["id"]))
