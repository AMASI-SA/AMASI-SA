"""Persist secret-safe Google connection projections into Integrations V2."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from .google_oauth_security import (
    GOOGLE_CAPABILITY_EVIDENCE,
    GOOGLE_CREDENTIALS_COLLECTION,
    GOOGLE_PERMISSION_ALIAS,
    GOOGLE_PROVIDER_IDS,
    GOOGLE_SCOPE_BY_PROVIDER,
    GOOGLE_SOURCE_MODE,
    _iso,
    _safe_callback_error,
    _utcnow,
    encrypt_google_token,
)


async def _persist_google_projection(
    db: Any,
    *,
    user_id: str,
    token_payload: dict[str, Any],
    identity: dict[str, Any],
    granted_scopes: set[str],
    accounts: dict[str, list[dict[str, Any]]],
    errors: dict[str, str],
) -> None:
    now = _utcnow()
    now_iso = _iso(now)
    observation_id = str(uuid.uuid4())
    credentials_collection = db[GOOGLE_CREDENTIALS_COLLECTION]
    prior = await credentials_collection.find_one(
        {"user_id": user_id}, {"_id": 0, "refresh_token_ciphertext": 1}
    )
    refresh_ciphertext = encrypt_google_token(token_payload.get("refresh_token"))
    if not refresh_ciphertext and prior:
        refresh_ciphertext = prior.get("refresh_token_ciphertext")
    expires_in = int(token_payload.get("expires_in") or 3600)
    await credentials_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "provider": "google",
                "access_token_ciphertext": encrypt_google_token(
                    token_payload.get("access_token")
                ),
                "refresh_token_ciphertext": refresh_ciphertext,
                "token_type": str(token_payload.get("token_type") or "Bearer")[:32],
                "scope": sorted(granted_scopes),
                "expires_at": now + timedelta(seconds=max(expires_in, 60)),
                "google_subject": str(identity.get("sub") or "") or None,
                "google_email": str(identity.get("email") or "") or None,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    for provider in sorted(GOOGLE_PROVIDER_IDS):
        required_scope = GOOGLE_SCOPE_BY_PROVIDER[provider]
        permission_alias = GOOGLE_PERMISSION_ALIAS[provider]
        scope_granted = required_scope in granted_scopes
        provider_accounts = accounts.get(provider) or []
        provider_error = errors.get(provider)
        connection_status = "connected" if scope_granted else "not_connected"
        provenance = "api_connection" if scope_granted else "disconnected"
        quality = "good" if provider_accounts else "missing" if scope_granted else "unavailable"
        integration_doc = {
            "user_id": user_id,
            "provider": provider,
            "connection_status": connection_status,
            "connection_provenance": provenance,
            "source_mode": GOOGLE_SOURCE_MODE,
            "last_sync_at": now_iso if scope_granted else None,
            "data_delay_minutes": 0 if provider_accounts else None,
            "data_quality": quality,
            "has_data": bool(provider_accounts),
            "capability_evidence": (
                GOOGLE_CAPABILITY_EVIDENCE[provider] if provider_accounts else []
            ),
            "permissions_observed": True,
            "permission_observation_id": observation_id,
            "checked_at": now_iso,
            "updated_at": now_iso,
        }
        await db.mezan_integrations_v2.update_one(
            {"user_id": user_id, "provider": provider},
            {"$set": integration_doc, "$setOnInsert": {"created_at": now_iso}},
            upsert=True,
        )
        await db.mezan_integration_permissions_v2.update_one(
            {
                "user_id": user_id,
                "provider": provider,
                "permission_key": permission_alias,
            },
            {
                "$set": {
                    "user_id": user_id,
                    "provider": provider,
                    "permission_key": permission_alias,
                    "permission_status": "current" if scope_granted else "missing",
                    "permission_observation_id": observation_id,
                    "source_mode": GOOGLE_SOURCE_MODE,
                    "observed_at": now_iso,
                }
            },
            upsert=True,
        )
        await db.mezan_integration_accounts_v2.delete_many(
            {"user_id": user_id, "provider": provider}
        )
        if provider_accounts:
            docs = []
            for account in provider_accounts[:200]:
                external_id = str(account.get("external_account_id") or "").strip()
                if not external_id:
                    continue
                docs.append(
                    {
                        "user_id": user_id,
                        "provider": provider,
                        "mezan_integration_account_id": str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"mezan-google:{user_id}:{provider}:{external_id}",
                            )
                        ),
                        "external_account_id": external_id,
                        "ad_account_id": account.get("ad_account_id"),
                        "display_name": account.get("display_name") or external_id,
                        "currency": account.get("currency"),
                        "timezone": account.get("timezone"),
                        "connection_status": "connected",
                        "connection_provenance": "api_connection",
                        "permissions": [permission_alias],
                        "permissions_observed": True,
                        "capability_evidence": GOOGLE_CAPABILITY_EVIDENCE[provider],
                        "has_data": True,
                        "last_sync_at": now_iso,
                        "data_delay_minutes": 0,
                        "health_score": 100,
                        "source_mode": GOOGLE_SOURCE_MODE,
                        "last_observed_at": now_iso,
                        "created_at": now_iso,
                    }
                )
            if docs:
                await db.mezan_integration_accounts_v2.insert_many(docs)
        health_score = 100 if provider_accounts else 75 if scope_granted else None
        health_status = "healthy" if provider_accounts else "degraded" if scope_granted else "not_available"
        await db.mezan_integration_health_v2.insert_one(
            {
                "user_id": user_id,
                "provider": provider,
                "health_status": health_status,
                "health_score": health_score,
                "data_quality": quality,
                "connection_status": connection_status,
                "connection_provenance": provenance,
                "data_delay_minutes": 0 if provider_accounts else None,
                "checked_at": now_iso,
                "source_mode": GOOGLE_SOURCE_MODE,
                "run_id": observation_id,
            }
        )
        error_id = None
        if provider_error:
            error_id = str(uuid.uuid4())
            await db.mezan_integration_errors_v2.insert_one(
                {
                    "error_id": error_id,
                    "user_id": user_id,
                    "provider": provider,
                    "code": f"google_discovery_{_safe_callback_error(provider_error)}",
                    "message": "تعذر اكتشاف حسابات Google لهذه الخدمة؛ الربط محفوظ ويمكن إعادة المحاولة.",
                    "occurred_at": now_iso,
                    "retryable": True,
                    "source_mode": GOOGLE_SOURCE_MODE,
                    "run_id": observation_id,
                }
            )
        await db.mezan_integration_sync_runs_v2.insert_one(
            {
                "run_id": str(uuid.uuid4()),
                "user_id": user_id,
                "provider": provider,
                "run_type": "google_oauth_discovery",
                "status": "complete" if scope_granted and not provider_error else "partial" if scope_granted else "not_connected",
                "started_at": now_iso,
                "finished_at": now_iso,
                "source_mode": GOOGLE_SOURCE_MODE,
                "summary": {
                    "scope_granted": scope_granted,
                    "account_count": len(provider_accounts),
                    "google_email_present": bool(identity.get("email")),
                },
                "error": {"error_id": error_id} if error_id else None,
            }
        )
