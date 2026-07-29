"""Persist secret-safe Snapchat Marketing API projections into Integrations V2."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from .snapchat_oauth_security import (
    SNAPCHAT_CAPABILITY_EVIDENCE,
    SNAPCHAT_CREDENTIALS_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SNAPCHAT_SOURCE_MODE,
    _iso,
    _safe_callback_error,
    _utcnow,
    encrypt_snapchat_token,
    requested_snapchat_scopes,
)


def normalize_snapchat_scopes(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = value
    elif isinstance(value, str):
        raw = value.replace(",", " ").split()
    elif value is None:
        raw = []
    else:
        raw = [value]
    return sorted({str(item).strip() for item in raw if str(item).strip()})


async def persist_snapchat_projection(
    db: Any,
    *,
    user_id: str,
    token_payload: dict[str, Any],
    discovery: dict[str, Any],
    provider_error: str | None = None,
) -> None:
    now = _utcnow()
    now_iso = _iso(now)
    observation_id = str(uuid.uuid4())
    access_token = str(token_payload.get("access_token") or "").strip()
    refresh_token = str(token_payload.get("refresh_token") or "").strip()
    expires_in = int(token_payload.get("expires_in") or 3600)
    scopes = normalize_snapchat_scopes(token_payload.get("scope"))
    if not scopes:
        scopes = list(requested_snapchat_scopes())
    accounts = list(discovery.get("accounts") or [])
    organizations = list(discovery.get("organizations") or [])
    identity = dict(discovery.get("identity") or {})

    await db[SNAPCHAT_CREDENTIALS_COLLECTION].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "access_token_ciphertext": encrypt_snapchat_token(access_token),
                "refresh_token_ciphertext": encrypt_snapchat_token(refresh_token),
                "access_token_expires_at": now + timedelta(seconds=expires_in),
                "scope": scopes,
                "identity": identity,
                "organization_ids": [
                    str(item.get("organization_id"))
                    for item in organizations
                    if item.get("organization_id")
                ],
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    connected = bool(access_token and refresh_token)
    has_accounts = bool(accounts)
    connection_status = "connected" if connected else "not_connected"
    provenance = "api_connection" if connected else "disconnected"
    quality = "good" if has_accounts else "missing" if connected else "unavailable"
    capability_evidence = list(SNAPCHAT_CAPABILITY_EVIDENCE) if has_accounts else []

    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
        {
            "$set": {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "connection_status": connection_status,
                "connection_provenance": provenance,
                "source_mode": SNAPCHAT_SOURCE_MODE,
                "last_sync_at": now_iso if connected else None,
                "data_delay_minutes": 0 if has_accounts else None,
                "data_quality": quality,
                "has_data": has_accounts,
                "capability_evidence": capability_evidence,
                "permissions_observed": connected,
                "permission_observation_id": observation_id,
                "checked_at": now_iso,
                "updated_at": now_iso,
            },
            "$setOnInsert": {"created_at": now_iso},
        },
        upsert=True,
    )

    for permission in scopes:
        await db.mezan_integration_permissions_v2.update_one(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "permission_key": permission,
            },
            {
                "$set": {
                    "user_id": user_id,
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "permission_key": permission,
                    "permission_status": "current" if connected else "missing",
                    "permission_observation_id": observation_id,
                    "source_mode": SNAPCHAT_SOURCE_MODE,
                    "observed_at": now_iso,
                }
            },
            upsert=True,
        )

    await db.mezan_integration_accounts_v2.delete_many(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID}
    )
    account_docs = []
    for account in accounts[:200]:
        account_id = str(account.get("external_account_id") or "").strip()
        if not account_id:
            continue
        account_docs.append(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "mezan_integration_account_id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"mezan-snapchat:{user_id}:{account_id}",
                    )
                ),
                "external_account_id": account_id,
                "ad_account_id": account_id,
                "display_name": account.get("display_name") or account_id,
                "currency": account.get("currency"),
                "timezone": account.get("timezone"),
                "organization_id": account.get("organization_id"),
                "organization_name": account.get("organization_name"),
                "account_status": account.get("account_status"),
                "connection_status": "connected",
                "connection_provenance": "api_connection",
                "permissions": scopes,
                "permissions_observed": True,
                "capability_evidence": list(SNAPCHAT_CAPABILITY_EVIDENCE),
                "has_data": True,
                "last_sync_at": now_iso,
                "data_delay_minutes": 0,
                "health_score": 100,
                "source_mode": SNAPCHAT_SOURCE_MODE,
                "last_observed_at": now_iso,
                "created_at": now_iso,
            }
        )
    if account_docs:
        await db.mezan_integration_accounts_v2.insert_many(account_docs)

    health_score = 100 if has_accounts else 75 if connected else None
    health_status = (
        "healthy" if has_accounts else "degraded" if connected else "not_available"
    )
    await db.mezan_integration_health_v2.insert_one(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "health_status": health_status,
            "health_score": health_score,
            "data_quality": quality,
            "connection_status": connection_status,
            "connection_provenance": provenance,
            "data_delay_minutes": 0 if has_accounts else None,
            "checked_at": now_iso,
            "source_mode": SNAPCHAT_SOURCE_MODE,
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
                "provider": SNAPCHAT_PROVIDER_ID,
                "code": f"snapchat_discovery_{_safe_callback_error(provider_error)}",
                "message": "تعذر اكتشاف حسابات Snapchat المصرح بها؛ الربط محفوظ ويمكن إعادة المحاولة.",
                "occurred_at": now_iso,
                "retryable": True,
                "source_mode": SNAPCHAT_SOURCE_MODE,
                "run_id": observation_id,
            }
        )

    await db.mezan_integration_sync_runs_v2.insert_one(
        {
            "run_id": str(uuid.uuid4()),
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "run_type": "snapchat_oauth_discovery",
            "status": (
                "complete"
                if connected and not provider_error
                else "partial"
                if connected
                else "not_connected"
            ),
            "started_at": now_iso,
            "finished_at": now_iso,
            "source_mode": SNAPCHAT_SOURCE_MODE,
            "summary": {
                "account_count": len(account_docs),
                "organization_count": len(organizations),
                "scope_count": len(scopes),
                "legacy_collection_read": False,
                "legacy_collection_write": False,
                "provider_write_reached": False,
                "accounting_write_reached": False,
                "qoyod_write_reached": False,
            },
            "error": {"error_id": error_id} if error_id else None,
        }
    )
