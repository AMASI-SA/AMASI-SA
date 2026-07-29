"""Persist secret-safe TikTok Marketing API projections into Integrations V2."""
from __future__ import annotations

import uuid
from typing import Any, Iterable

from .tiktok_oauth_security import (
    TIKTOK_CAPABILITY_EVIDENCE,
    TIKTOK_CREDENTIALS_COLLECTION,
    TIKTOK_PERMISSION_ALIAS,
    TIKTOK_PROVIDER_ID,
    TIKTOK_SOURCE_MODE,
    _iso,
    _safe_callback_error,
    _utcnow,
    encrypt_tiktok_token,
)


def normalize_tiktok_scopes(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = value
    elif isinstance(value, str):
        raw = value.replace(",", " ").split()
    elif value is None:
        raw = []
    else:
        raw = [value]
    return sorted({str(item).strip() for item in raw if str(item).strip()})


async def persist_tiktok_projection(
    db: Any,
    *,
    user_id: str,
    token_payload: dict[str, Any],
    advertisers: list[dict[str, Any]],
    provider_error: str | None = None,
) -> None:
    now = _utcnow()
    now_iso = _iso(now)
    observation_id = str(uuid.uuid4())
    access_token = str(token_payload.get("access_token") or "").strip()
    advertiser_ids = sorted(
        {
            str(item).strip()
            for item in (token_payload.get("advertiser_ids") or [])
            if str(item).strip()
        }
    )
    scopes = normalize_tiktok_scopes(token_payload.get("scope"))

    await db[TIKTOK_CREDENTIALS_COLLECTION].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "provider": TIKTOK_PROVIDER_ID,
                "access_token_ciphertext": encrypt_tiktok_token(access_token),
                "advertiser_ids": advertiser_ids,
                "scope": scopes,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    connected = bool(access_token)
    has_accounts = bool(advertisers)
    connection_status = "connected" if connected else "not_connected"
    provenance = "api_connection" if connected else "disconnected"
    quality = "good" if has_accounts else "missing" if connected else "unavailable"
    capability_evidence = list(TIKTOK_CAPABILITY_EVIDENCE) if has_accounts else []

    integration_doc = {
        "user_id": user_id,
        "provider": TIKTOK_PROVIDER_ID,
        "connection_status": connection_status,
        "connection_provenance": provenance,
        "source_mode": TIKTOK_SOURCE_MODE,
        "last_sync_at": now_iso if connected else None,
        "data_delay_minutes": 0 if has_accounts else None,
        "data_quality": quality,
        "has_data": has_accounts,
        "capability_evidence": capability_evidence,
        "permissions_observed": connected,
        "permission_observation_id": observation_id,
        "checked_at": now_iso,
        "updated_at": now_iso,
    }
    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": TIKTOK_PROVIDER_ID},
        {"$set": integration_doc, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )

    permission_rows = [TIKTOK_PERMISSION_ALIAS, *(f"scope:{item}" for item in scopes)]
    for permission in permission_rows:
        await db.mezan_integration_permissions_v2.update_one(
            {
                "user_id": user_id,
                "provider": TIKTOK_PROVIDER_ID,
                "permission_key": permission,
            },
            {
                "$set": {
                    "user_id": user_id,
                    "provider": TIKTOK_PROVIDER_ID,
                    "permission_key": permission,
                    "permission_status": "current" if connected else "missing",
                    "permission_observation_id": observation_id,
                    "source_mode": TIKTOK_SOURCE_MODE,
                    "observed_at": now_iso,
                }
            },
            upsert=True,
        )

    await db.mezan_integration_accounts_v2.delete_many(
        {"user_id": user_id, "provider": TIKTOK_PROVIDER_ID}
    )
    account_docs = []
    for advertiser in advertisers[:200]:
        advertiser_id = str(advertiser.get("external_account_id") or "").strip()
        if not advertiser_id:
            continue
        account_docs.append(
            {
                "user_id": user_id,
                "provider": TIKTOK_PROVIDER_ID,
                "mezan_integration_account_id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"mezan-tiktok:{user_id}:{advertiser_id}",
                    )
                ),
                "external_account_id": advertiser_id,
                "ad_account_id": advertiser_id,
                "display_name": advertiser.get("display_name") or advertiser_id,
                "currency": advertiser.get("currency"),
                "timezone": advertiser.get("timezone"),
                "connection_status": "connected",
                "connection_provenance": "api_connection",
                "permissions": permission_rows,
                "permissions_observed": True,
                "capability_evidence": list(TIKTOK_CAPABILITY_EVIDENCE),
                "has_data": True,
                "last_sync_at": now_iso,
                "data_delay_minutes": 0,
                "health_score": 100,
                "source_mode": TIKTOK_SOURCE_MODE,
                "last_observed_at": now_iso,
                "created_at": now_iso,
            }
        )
    if account_docs:
        await db.mezan_integration_accounts_v2.insert_many(account_docs)

    health_score = 100 if has_accounts else 75 if connected else None
    health_status = "healthy" if has_accounts else "degraded" if connected else "not_available"
    await db.mezan_integration_health_v2.insert_one(
        {
            "user_id": user_id,
            "provider": TIKTOK_PROVIDER_ID,
            "health_status": health_status,
            "health_score": health_score,
            "data_quality": quality,
            "connection_status": connection_status,
            "connection_provenance": provenance,
            "data_delay_minutes": 0 if has_accounts else None,
            "checked_at": now_iso,
            "source_mode": TIKTOK_SOURCE_MODE,
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
                "provider": TIKTOK_PROVIDER_ID,
                "code": f"tiktok_discovery_{_safe_callback_error(provider_error)}",
                "message": "تعذر اكتشاف حسابات TikTok المصرح بها؛ الربط محفوظ ويمكن إعادة المحاولة.",
                "occurred_at": now_iso,
                "retryable": True,
                "source_mode": TIKTOK_SOURCE_MODE,
                "run_id": observation_id,
            }
        )

    await db.mezan_integration_sync_runs_v2.insert_one(
        {
            "run_id": str(uuid.uuid4()),
            "user_id": user_id,
            "provider": TIKTOK_PROVIDER_ID,
            "run_type": "tiktok_oauth_discovery",
            "status": "complete" if connected and not provider_error else "partial" if connected else "not_connected",
            "started_at": now_iso,
            "finished_at": now_iso,
            "source_mode": TIKTOK_SOURCE_MODE,
            "summary": {
                "account_count": len(account_docs),
                "scope_count": len(scopes),
                "make_data_used": False,
                "legacy_collection_read": False,
            },
            "error": {"error_id": error_id} if error_id else None,
        }
    )
