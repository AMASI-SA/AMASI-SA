"""Persist secret-safe Meta Business projections into Integrations V2."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .meta_oauth_security import (
    META_ASSETS_COLLECTION,
    META_CAPABILITY_EVIDENCE,
    META_CREDENTIALS_COLLECTION,
    META_PROVIDER_ID,
    META_SOURCE_MODE,
    _iso,
    _safe_callback_error,
    _utcnow,
    encrypt_meta_token,
    meta_graph_version,
    requested_meta_scopes,
)


def normalize_meta_scopes(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = value
    elif isinstance(value, str):
        raw = value.replace(",", " ").split()
    elif value is None:
        raw = []
    else:
        raw = [value]
    return sorted({str(item).strip() for item in raw if str(item).strip()})


def _expiry_from_debug(debug_data: dict[str, Any], token_payload: dict[str, Any]) -> datetime:
    expires_at = int(debug_data.get("expires_at") or 0)
    if expires_at > 0:
        return datetime.fromtimestamp(expires_at, tz=timezone.utc)
    return _utcnow() + timedelta(seconds=int(token_payload.get("expires_in") or 5184000))


def _asset_doc(
    *,
    user_id: str,
    asset_type: str,
    item: dict[str, Any],
    now_iso: str,
) -> dict[str, Any] | None:
    asset_id = str(item.get("external_asset_id") or "").strip()
    if not asset_id:
        return None
    safe_fields = {
        key: value
        for key, value in item.items()
        if key
        not in {
            "access_token",
            "token",
            "secret",
            "client_secret",
            "funding_source_details",
        }
    }
    return {
        "user_id": user_id,
        "provider": META_PROVIDER_ID,
        "asset_type": asset_type,
        "external_asset_id": asset_id,
        "display_name": item.get("display_name") or asset_id,
        "connection_status": "connected",
        "connection_provenance": "api_connection",
        "source_mode": META_SOURCE_MODE,
        "last_observed_at": now_iso,
        "updated_at": now_iso,
        **safe_fields,
    }


async def persist_meta_projection(
    db: Any,
    *,
    user_id: str,
    token_payload: dict[str, Any],
    debug_data: dict[str, Any],
    discovery: dict[str, Any],
    provider_error: str | None = None,
) -> None:
    now = _utcnow()
    now_iso = _iso(now)
    observation_id = str(uuid.uuid4())
    access_token = str(token_payload.get("access_token") or "").strip()
    scopes = normalize_meta_scopes(debug_data.get("scopes"))
    if not scopes:
        scopes = normalize_meta_scopes(token_payload.get("scope"))
    if not scopes:
        scopes = list(requested_meta_scopes())

    accounts = list(discovery.get("accounts") or [])
    businesses = list(discovery.get("businesses") or [])
    pixels = list(discovery.get("pixels") or [])
    catalogs = list(discovery.get("catalogs") or [])
    instagram_accounts = list(discovery.get("instagram_accounts") or [])
    discovery_errors = list(discovery.get("errors") or [])
    identity = dict(discovery.get("identity") or {})

    await db[META_CREDENTIALS_COLLECTION].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "provider": META_PROVIDER_ID,
                "access_token_ciphertext": encrypt_meta_token(access_token),
                "access_token_expires_at": _expiry_from_debug(
                    debug_data, token_payload
                ),
                "scope": scopes,
                "identity": identity,
                "external_user_id": debug_data.get("user_id"),
                "graph_version": meta_graph_version(),
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    connected = bool(access_token and debug_data.get("is_valid"))
    has_accounts = bool(accounts)
    connection_status = "connected" if connected else "not_connected"
    provenance = "api_connection" if connected else "disconnected"
    quality = "good" if has_accounts else "missing" if connected else "unavailable"
    capability_evidence = list(META_CAPABILITY_EVIDENCE) if has_accounts else []

    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": META_PROVIDER_ID},
        {
            "$set": {
                "user_id": user_id,
                "provider": META_PROVIDER_ID,
                "connection_status": connection_status,
                "connection_provenance": provenance,
                "source_mode": META_SOURCE_MODE,
                "last_sync_at": now_iso if connected else None,
                "data_delay_minutes": 0 if has_accounts else None,
                "data_quality": quality,
                "has_data": has_accounts,
                "capability_evidence": capability_evidence,
                "permissions_observed": connected,
                "permission_observation_id": observation_id,
                "asset_counts": {
                    "businesses": len(businesses),
                    "ad_accounts": len(accounts),
                    "pixels": len(pixels),
                    "catalogs": len(catalogs),
                    "instagram_accounts": len(instagram_accounts),
                },
                "graph_version": meta_graph_version(),
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
                "provider": META_PROVIDER_ID,
                "permission_key": permission,
            },
            {
                "$set": {
                    "user_id": user_id,
                    "provider": META_PROVIDER_ID,
                    "permission_key": permission,
                    "permission_status": "current" if connected else "missing",
                    "permission_observation_id": observation_id,
                    "source_mode": META_SOURCE_MODE,
                    "observed_at": now_iso,
                }
            },
            upsert=True,
        )

    await db.mezan_integration_accounts_v2.delete_many(
        {"user_id": user_id, "provider": META_PROVIDER_ID}
    )
    account_docs = []
    for account in accounts[:200]:
        account_id = str(account.get("external_account_id") or "").strip()
        if not account_id:
            continue
        account_docs.append(
            {
                "user_id": user_id,
                "provider": META_PROVIDER_ID,
                "mezan_integration_account_id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"mezan-meta:{user_id}:{account_id}",
                    )
                ),
                "external_account_id": account_id,
                "ad_account_id": account_id,
                "display_name": account.get("display_name") or account_id,
                "currency": account.get("currency"),
                "timezone": account.get("timezone"),
                "account_status": account.get("account_status"),
                "business_id": account.get("business_id"),
                "business_name": account.get("business_name"),
                "amount_spent_minor": account.get("amount_spent_minor"),
                "balance_minor": account.get("balance_minor"),
                "spend_cap_minor": account.get("spend_cap_minor"),
                "funding_source_present": bool(
                    account.get("funding_source_present")
                ),
                "connection_status": "connected",
                "connection_provenance": "api_connection",
                "permissions": scopes,
                "permissions_observed": True,
                "capability_evidence": list(META_CAPABILITY_EVIDENCE),
                "has_data": True,
                "last_sync_at": now_iso,
                "data_delay_minutes": 0,
                "health_score": 100,
                "source_mode": META_SOURCE_MODE,
                "last_observed_at": now_iso,
                "created_at": now_iso,
            }
        )
    if account_docs:
        await db.mezan_integration_accounts_v2.insert_many(account_docs)

    await db[META_ASSETS_COLLECTION].delete_many(
        {"user_id": user_id, "provider": META_PROVIDER_ID}
    )
    asset_docs = []
    for asset_type, rows in (
        ("business", businesses),
        ("pixel", pixels),
        ("catalog", catalogs),
        ("instagram_account", instagram_accounts),
    ):
        for item in rows[:500]:
            doc = _asset_doc(
                user_id=user_id,
                asset_type=asset_type,
                item=item,
                now_iso=now_iso,
            )
            if doc:
                asset_docs.append(doc)
    if asset_docs:
        await db[META_ASSETS_COLLECTION].insert_many(asset_docs)

    health_score = 100 if has_accounts else 75 if connected else None
    health_status = (
        "healthy" if has_accounts else "degraded" if connected else "not_available"
    )
    await db.mezan_integration_health_v2.insert_one(
        {
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
            "health_status": health_status,
            "health_score": health_score,
            "data_quality": quality,
            "connection_status": connection_status,
            "connection_provenance": provenance,
            "data_delay_minutes": 0 if has_accounts else None,
            "checked_at": now_iso,
            "source_mode": META_SOURCE_MODE,
            "run_id": observation_id,
        }
    )

    error_ids = []
    combined_errors = list(discovery_errors)
    if provider_error:
        combined_errors.append({"asset": "oauth_discovery", "code": provider_error})
    for error in combined_errors[:25]:
        error_id = str(uuid.uuid4())
        error_ids.append(error_id)
        await db.mezan_integration_errors_v2.insert_one(
            {
                "error_id": error_id,
                "user_id": user_id,
                "provider": META_PROVIDER_ID,
                "code": f"meta_discovery_{_safe_callback_error(error.get('code'))}",
                "message": "تعذر اكتشاف أحد أصول Meta المصرح بها؛ الربط محفوظ ويمكن إعادة المحاولة.",
                "asset": str(error.get("asset") or "unknown")[:120],
                "occurred_at": now_iso,
                "retryable": True,
                "source_mode": META_SOURCE_MODE,
                "run_id": observation_id,
            }
        )

    await db.mezan_integration_sync_runs_v2.insert_one(
        {
            "run_id": str(uuid.uuid4()),
            "user_id": user_id,
            "provider": META_PROVIDER_ID,
            "run_type": "meta_oauth_discovery",
            "status": (
                "complete"
                if connected and not combined_errors
                else "partial"
                if connected
                else "not_connected"
            ),
            "started_at": now_iso,
            "finished_at": now_iso,
            "source_mode": META_SOURCE_MODE,
            "summary": {
                "account_count": len(account_docs),
                "business_count": len(businesses),
                "pixel_count": len(pixels),
                "catalog_count": len(catalogs),
                "instagram_account_count": len(instagram_accounts),
                "scope_count": len(scopes),
                "error_count": len(error_ids),
                "legacy_collection_read": False,
                "legacy_collection_write": False,
                "provider_write_reached": False,
                "accounting_write_reached": False,
                "qoyod_write_reached": False,
            },
            "error": {"error_ids": error_ids} if error_ids else None,
        }
    )
