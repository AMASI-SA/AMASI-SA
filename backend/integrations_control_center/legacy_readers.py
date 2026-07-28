"""Bounded, tenant-scoped, secret-safe readers for transitional sources.

These readers never mutate a legacy collection and never issue a provider
network request.  Every Mongo projection is an explicit allowlist.  Connection
credential *presence* is tested in the query predicate while the credential
value itself is never projected into Python.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timezone
from typing import Any, Iterable

from .catalog import ProviderDefinition


MAX_ACCOUNTS_PER_PROVIDER = 50
MAX_SAFE_TEXT_LENGTH = 1000

_SECRET_KEY_RE = re.compile(
    r"(?:^|[_-])(?:"
    r"access[_-]?token|refresh[_-]?token|token|secret|client[_-]?secret|"
    r"api[_-]?key|api[_-]?key[_-]?enc|authorization|cookie|password|"
    r"credential|ciphertext|private[_-]?key|signing[_-]?key"
    r")(?:$|[_-])",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"access[\s_-]*token|refresh[\s_-]*token|token|"
    r"api[\s_-]*key|client[\s_-]*secret|app[\s_-]*secret|secret|"
    r"authorization|password|cookie|credential"
    r")\s*[:=]\s*(?:bearer\s+)?[^\s,;&]+"
)


# Explicit allowlists.  Secret-bearing fields are deliberately absent.
LEGACY_PROJECTIONS: dict[str, dict[str, int]] = {
    "salla_integrations": {
        "_id": 0,
        "user_id": 1,
        "status": 1,
        "store_id": 1,
        "store_name": 1,
        "store_domain": 1,
        "store_plan": 1,
        "store_status": 1,
        "scope": 1,
        "expires_at": 1,
        "last_refreshed_at": 1,
        "last_error_at": 1,
        "created_at": 1,
        "updated_at": 1,
        "install_mode": 1,
    },
    "salla_sync_logs": {
        "_id": 0,
        "id": 1,
        "user_id": 1,
        "kind": 1,
        "status": 1,
        "started_at": 1,
        "ended_at": 1,
        "created": 1,
        "updated": 1,
        "skipped": 1,
        "errors_count": 1,
        "pages_fetched": 1,
    },
    "snapchat_connections": {
        "_id": 0,
        "user_id": 1,
        "ad_account_id": 1,
        "ad_account_name": 1,
        "ad_account_currency": 1,
        "ad_account_timezone": 1,
        "updated_at": 1,
    },
    "snapchat_ad_accounts": {
        "_id": 0,
        "user_id": 1,
        "ad_account_id": 1,
        "name": 1,
        "currency": 1,
        "currency_native": 1,
        "timezone": 1,
        "organization_id": 1,
        "organization_name": 1,
        "enabled": 1,
        "last_sync_at": 1,
        "updated_at": 1,
    },
    "snapchat_account_daily": {
        "_id": 0,
        "user_id": 1,
        "ad_account_id": 1,
        "account_id": 1,
        "date": 1,
        "updated_at": 1,
        "synced_at": 1,
        "campaign_id": 1,
        "campaign_name": 1,
        "spend": 1,
        "spend_sar": 1,
        "spend_native": 1,
        "impressions": 1,
        "clicks": 1,
        "purchases": 1,
        "revenue_native": 1,
        "revenue_sar": 1,
    },
    "tiktok_connections": {
        "_id": 0,
        "user_id": 1,
        "advertiser_id": 1,
        "advertiser_name": 1,
        "currency": 1,
        "timezone": 1,
        "connection_status": 1,
        "last_sync_at": 1,
        "last_error_at": 1,
        "scope": 1,
        "updated_at": 1,
    },
    "tiktok_ads_daily": {
        "_id": 0,
        "user_id": 1,
        "advertiser_id": 1,
        "advertiser_name": 1,
        "date": 1,
        "campaign_id": 1,
        "campaign_name": 1,
        "platform": 1,
        "spend": 1,
        "clicks": 1,
        "impressions": 1,
        "reach": 1,
        "video_views": 1,
        "cpa": 1,
        "purchases": 1,
        "conversions": 1,
        "revenue": 1,
        "updated_at": 1,
        "created_at": 1,
    },
    "meta_connections": {
        "_id": 0,
        "user_id": 1,
        "ad_account_id": 1,
        "ad_account_name": 1,
        "currency": 1,
        "timezone": 1,
        "connection_status": 1,
        "last_sync_at": 1,
        "last_error_at": 1,
        "scope": 1,
        "updated_at": 1,
    },
    "meta_ads_daily": {
        "_id": 0,
        "user_id": 1,
        "account_id": 1,
        "ad_account_id": 1,
        "date": 1,
        "campaign_id": 1,
        "campaign_name": 1,
        "adset_id": 1,
        "ad_id": 1,
        "spend": 1,
        "impressions": 1,
        "clicks": 1,
        "cpc": 1,
        "cpm": 1,
        "ctr": 1,
        "purchases": 1,
        "purchase_value": 1,
        "updated_at": 1,
        "created_at": 1,
    },
    "ads_accounts": {
        "_id": 0,
        "id": 1,
        "user_id": 1,
        "provider": 1,
        "external_account_id": 1,
        "display_name": 1,
        "currency_native": 1,
        "timezone": 1,
        "connection_status": 1,
        "platform_check_status": 1,
        "platform_last_checked_at": 1,
        "last_sync_at": 1,
        "updated_at": 1,
        "soft_deleted": 1,
    },
    "qoyod_credentials": {
        "_id": 0,
        "user_id": 1,
        "fingerprint": 1,
        "last_verified_at": 1,
        "updated_at": 1,
        "rotated_at": 1,
    },
    "qoyod_settings": {
        "_id": 0,
        "user_id": 1,
        "enabled": 1,
        "auto_send": 1,
        "auto_receipt": 1,
        "dry_run_mode": 1,
        "production_writes_locked": 1,
        "selective_live_send_enabled": 1,
        "legacy_pipeline_frozen": 1,
        "last_verified_at": 1,
        "updated_at": 1,
    },
    "qoyod_invoices": {
        "_id": 0,
        "user_id": 1,
        "status": 1,
        "pipeline_stage": 1,
        "error_code": 1,
        "sent_at": 1,
        "updated_at": 1,
        "created_at": 1,
    },
}


def _is_secret_key(key: object) -> bool:
    text = str(key or "").strip()
    if _SECRET_KEY_RE.search(text):
        return True
    # Also cover provider payloads that use camelCase/PascalCase keys.
    compact = re.sub(r"[^a-z0-9]", "", text.lower())
    sensitive_fragments = (
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "appsecret",
        "apikey",
        "authorization",
        "password",
        "credential",
        "ciphertext",
        "privatekey",
        "signingkey",
    )
    return compact in {"token", "secret", "cookie"} or any(
        fragment in compact for fragment in sensitive_fragments
    )


def _sanitize_text(value: str) -> str:
    text = value[:MAX_SAFE_TEXT_LENGTH]
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _JWT_RE.sub("[REDACTED_JWT]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    return text


def sanitize_for_output(value: Any, *, _depth: int = 0) -> Any:
    """Recursively remove secret-shaped keys and redact token-shaped text."""
    if _depth > 12:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            str(key): sanitize_for_output(item, _depth=_depth + 1)
            for key, item in value.items()
            if not _is_secret_key(key) and str(key) != "_id"
        }
    if isinstance(value, (list, tuple, set)):
        return [
            sanitize_for_output(item, _depth=_depth + 1)
            for item in list(value)[:100]
        ]
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, bytes):
        return "[redacted_binary]"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_text(str(value))


def _safe_legacy_error(
    *,
    provider_label: str,
    code: Any,
    occurred_at: Any,
    source_mode: str,
) -> dict:
    """Represent legacy failures without ever reading or copying raw messages."""
    raw_code = str(code or "").strip()
    safe_code = (
        raw_code
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", raw_code)
        else "legacy_provider_error"
    )
    return {
        "code": safe_code,
        "message": (
            f"أبلغ موصل {provider_label} عن خطأ. حُجبت التفاصيل الحساسة؛ "
            "راجع سجل الموصل المعتمد."
        ),
        "occurred_at": occurred_at,
        "source_mode": source_mode,
    }


def _collection(db: Any, name: str) -> Any:
    try:
        return db[name]
    except (KeyError, TypeError, AttributeError):
        return getattr(db, name)


async def _find_one(
    db: Any,
    collection_name: str,
    query: dict,
    *,
    sort: list[tuple[str, int]] | None = None,
) -> dict | None:
    collection = _collection(db, collection_name)
    projection = LEGACY_PROJECTIONS[collection_name]
    if sort:
        doc = await collection.find_one(query, projection, sort=sort)
    else:
        doc = await collection.find_one(query, projection)
    return sanitize_for_output(doc) if doc else None


async def _credential_present(
    db: Any,
    collection_name: str,
    query: dict,
) -> bool:
    """Check secret presence without reading the secret value."""
    collection = _collection(db, collection_name)
    doc = await collection.find_one(query, {"_id": 1})
    return bool(doc)


async def _find_many(
    db: Any,
    collection_name: str,
    query: dict,
    *,
    sort: list[tuple[str, int]] | None = None,
    limit: int = MAX_ACCOUNTS_PER_PROVIDER,
) -> list[dict]:
    cursor = _collection(db, collection_name).find(
        query,
        LEGACY_PROJECTIONS[collection_name],
    )
    if sort:
        cursor = cursor.sort(sort)
    cursor = cursor.limit(min(max(int(limit), 1), MAX_ACCOUNTS_PER_PROVIDER))
    if hasattr(cursor, "to_list"):
        rows = await cursor.to_list(length=limit)
    else:
        rows = [row async for row in cursor]
    return [sanitize_for_output(row) for row in rows]


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min, tzinfo=timezone.utc)
    elif value:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            try:
                parsed = datetime.combine(
                    date.fromisoformat(text[:10]),
                    time.min,
                    tzinfo=timezone.utc,
                )
            except (TypeError, ValueError):
                return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_timestamp(*values: Any) -> str | None:
    parsed = [_parse_datetime(value) for value in values]
    available = [item for item in parsed if item is not None]
    return max(available).isoformat() if available else None


def data_delay_minutes(last_sync_at: Any, *, now: datetime | None = None) -> int | None:
    parsed = _parse_datetime(last_sync_at)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0, int((current.astimezone(timezone.utc) - parsed).total_seconds() // 60))


def _split_permissions(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[\s,]+", value)
    elif isinstance(value, Iterable) and not isinstance(value, (dict, bytes)):
        raw = [str(item) for item in value]
    else:
        raw = []
    return sorted({item.strip() for item in raw if item and item.strip()})


def _account_id(user_id: str, provider: str, external_id: Any) -> str:
    identity = f"{user_id}:{provider}:{external_id or 'default'}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mezan-integration:{identity}"))


def _account(
    user_id: str,
    provider: str,
    *,
    external_account_id: Any = None,
    store_id: Any = None,
    ad_account_id: Any = None,
    display_name: Any = None,
    currency: Any = None,
    timezone_name: Any = None,
) -> dict:
    external = external_account_id or store_id or ad_account_id
    return {
        "mezan_integration_account_id": _account_id(user_id, provider, external),
        "provider": provider,
        "external_account_id": str(external) if external not in (None, "") else None,
        "store_id": str(store_id) if store_id not in (None, "") else None,
        "ad_account_id": str(ad_account_id) if ad_account_id not in (None, "") else None,
        "display_name": str(display_name) if display_name not in (None, "") else None,
        "currency": str(currency) if currency not in (None, "") else None,
        "timezone": str(timezone_name) if timezone_name not in (None, "") else None,
    }


def _deduplicate_accounts(accounts: Iterable[dict]) -> list[dict]:
    output: list[dict] = []
    seen: set[str] = set()
    for account in accounts:
        identity = str(account.get("mezan_integration_account_id") or "")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        output.append(account)
    return output[:MAX_ACCOUNTS_PER_PROVIDER]


_INSIGHT_FIELDS = frozenset(
    {
        "spend",
        "spend_sar",
        "spend_native",
        "impressions",
        "clicks",
        "reach",
        "video_views",
        "cpc",
        "cpm",
        "ctr",
        "cpa",
    }
)
_CONVERSION_FIELDS = frozenset(
    {
        "purchases",
        "conversions",
        "revenue",
        "revenue_native",
        "revenue_sar",
        "purchase_value",
    }
)


def _advertising_evidence(rows: Iterable[dict]) -> list[str]:
    """Grant read evidence only for fields actually present in local rows."""
    evidence: set[str] = set()
    for row in rows:
        campaign_id = str(row.get("campaign_id") or "").strip()
        ad_id = str(row.get("ad_id") or "").strip()
        if campaign_id and campaign_id != "_default":
            evidence.add("campaigns.read")
        if ad_id and ad_id != "_default":
            evidence.add("ads.read")
        if any(field in row for field in _INSIGHT_FIELDS):
            evidence.add("insights.read")
        if any(field in row for field in _CONVERSION_FIELDS):
            evidence.add("conversions.read")
    return sorted(evidence)


def _snapshot(
    definition: ProviderDefinition,
    *,
    connection_status: str,
    connection_provenance: str,
    source_mode: str,
    accounts: list[dict] | None = None,
    current_permissions: list[str] | None = None,
    permissions_observed: bool | None = None,
    last_sync_at: Any = None,
    latest_error: dict | None = None,
    has_data: bool = False,
    capability_evidence: Iterable[str] = (),
) -> dict:
    current = sorted(set(current_permissions or []))
    required = set(definition.required_permissions)
    permission_evidence_present = (
        bool(current) if permissions_observed is None else bool(permissions_observed)
    )
    connection_can_have_permissions = connection_status in {
        "connected",
        "active",
        "healthy",
        "needs_reauth",
        "expired",
        "error",
    }
    # No API connection is not the same as a denied permission. Likewise, an
    # absent scope field is unknown evidence, not proof that every scope is
    # missing.
    missing = (
        sorted(required - set(current))
        if permission_evidence_present and connection_can_have_permissions
        else []
    )
    delay = data_delay_minutes(last_sync_at)
    if has_data and delay is not None and delay <= 24 * 60:
        quality = "good"
    elif has_data and delay is not None and delay <= 7 * 24 * 60:
        quality = "delayed"
    elif has_data:
        quality = "stale"
    elif connection_status in {"connected", "active"}:
        quality = "missing"
    else:
        quality = "unavailable"
    return {
        "provider": definition.provider,
        "connection_status": connection_status,
        "connection_provenance": connection_provenance,
        "source_mode": source_mode,
        "accounts": _deduplicate_accounts(accounts or []),
        "current_permissions": current,
        "missing_permissions": missing,
        "permissions_observed": permission_evidence_present,
        "last_sync_at": _latest_timestamp(last_sync_at),
        "data_delay_minutes": delay,
        "latest_error": sanitize_for_output(latest_error) if latest_error else None,
        "has_data": bool(has_data),
        "data_quality": quality,
        "capability_evidence": sorted(set(capability_evidence)),
    }


async def _read_salla(db: Any, user_id: str, definition: ProviderDefinition) -> dict:
    integration = await _find_one(
        db,
        "salla_integrations",
        {"user_id": user_id},
    )
    sync = await _find_one(
        db,
        "salla_sync_logs",
        {"user_id": user_id},
        sort=[("started_at", -1)],
    )
    credential_present = await _credential_present(
        db,
        "salla_integrations",
        {
            "user_id": user_id,
            "$or": [
                {
                    "access_token_encrypted": {
                        "$exists": True,
                        "$nin": ["", None],
                    }
                },
                {
                    "refresh_token_encrypted": {
                        "$exists": True,
                        "$nin": ["", None],
                    }
                },
            ],
        },
    )
    if not integration:
        return _snapshot(
            definition,
            connection_status="not_connected",
            connection_provenance="disconnected",
            source_mode="legacy_fallback",
        )

    raw_status = str(integration.get("status") or "").lower()
    expires_at = _parse_datetime(integration.get("expires_at"))
    if raw_status == "needs_reauth":
        status = "needs_reauth"
    elif raw_status in {"error", "failed"}:
        status = "error"
    elif expires_at and expires_at <= datetime.now(timezone.utc):
        status = "expired"
    elif raw_status == "connected" and credential_present:
        status = "connected"
    elif raw_status == "connected":
        # Fail closed when a stale status row remains without either encrypted
        # credential. Credential values are never projected into Python.
        status = "unknown"
    else:
        status = "unknown"

    sync_status = str((sync or {}).get("status") or "").lower()
    latest_error = None
    if status == "error" or sync_status in {"error", "failed", "failure"}:
        latest_error = _safe_legacy_error(
            provider_label=definition.name,
            code="legacy_salla_error",
            occurred_at=integration.get("last_error_at")
            or (sync or {}).get("ended_at"),
            source_mode="legacy_fallback",
        )
    store_id = integration.get("store_id")
    accounts = []
    if store_id or integration.get("store_name"):
        accounts.append(
            _account(
                user_id,
                definition.provider,
                external_account_id=store_id,
                store_id=store_id,
                display_name=integration.get("store_name"),
                currency="SAR",
                timezone_name="Asia/Riyadh",
            )
        )
    last_sync = _latest_timestamp(
        (sync or {}).get("ended_at"),
        (sync or {}).get("started_at"),
        integration.get("last_refreshed_at"),
        integration.get("updated_at"),
    )
    has_data = bool(sync and str(sync.get("status") or "").lower() in {"success", "completed"})
    permissions = _split_permissions(integration.get("scope"))
    return _snapshot(
        definition,
        connection_status=status,
        connection_provenance=(
            "api_connection" if credential_present else "disconnected"
        ),
        source_mode="legacy_connection",
        accounts=accounts,
        current_permissions=permissions,
        permissions_observed=bool(permissions),
        last_sync_at=last_sync,
        latest_error=latest_error,
        has_data=has_data,
    )


async def _read_snapchat(
    db: Any,
    user_id: str,
    definition: ProviderDefinition,
) -> dict:
    connection = await _find_one(
        db,
        "snapchat_connections",
        {"user_id": user_id},
    )
    credential_present = await _credential_present(
        db,
        "snapchat_connections",
        {
            "user_id": user_id,
            "refresh_token": {"$exists": True, "$nin": ["", None]},
        },
    )
    rows = await _find_many(
        db,
        "snapchat_ad_accounts",
        {"user_id": user_id, "enabled": {"$ne": False}},
        sort=[("updated_at", -1)],
    )
    daily = await _find_one(
        db,
        "snapchat_account_daily",
        {"user_id": user_id},
        sort=[("date", -1), ("updated_at", -1)],
    )

    accounts = [
        _account(
            user_id,
            definition.provider,
            external_account_id=row.get("ad_account_id"),
            ad_account_id=row.get("ad_account_id"),
            display_name=row.get("name"),
            currency=row.get("currency_native") or row.get("currency"),
            timezone_name=row.get("timezone"),
        )
        for row in rows
        if row.get("ad_account_id")
    ]
    if connection and connection.get("ad_account_id"):
        accounts.append(
            _account(
                user_id,
                definition.provider,
                external_account_id=connection.get("ad_account_id"),
                ad_account_id=connection.get("ad_account_id"),
                display_name=connection.get("ad_account_name"),
                currency=connection.get("ad_account_currency"),
                timezone_name=connection.get("ad_account_timezone"),
            )
        )
    has_data = bool(daily)
    status = (
        "connected"
        if credential_present
        else "data_available"
        if has_data
        else "not_connected"
    )
    last_sync = _latest_timestamp(
        (daily or {}).get("synced_at"),
        (daily or {}).get("updated_at"),
        (daily or {}).get("date"),
        *(row.get("last_sync_at") or row.get("updated_at") for row in rows),
        (connection or {}).get("updated_at"),
    )
    return _snapshot(
        definition,
        connection_status=status,
        connection_provenance=(
            "legacy_integration"
            if credential_present
            else "data_feed"
            if has_data
            else "disconnected"
        ),
        source_mode="legacy_connection" if credential_present else "legacy_data",
        accounts=accounts,
        # The legacy connector stores the requested Snapchat scope, not the
        # provider-granted scope. Do not present that request as verified.
        current_permissions=[],
        permissions_observed=False,
        last_sync_at=last_sync,
        has_data=has_data,
        capability_evidence=_advertising_evidence([daily] if daily else []),
    )


async def _read_tiktok(
    db: Any,
    user_id: str,
    definition: ProviderDefinition,
) -> dict:
    connection = await _find_one(
        db,
        "tiktok_connections",
        {"user_id": user_id},
    )
    credential_present = await _credential_present(
        db,
        "tiktok_connections",
        {
            "user_id": user_id,
            "$or": [
                {"refresh_token": {"$exists": True, "$nin": ["", None]}},
                {"access_token": {"$exists": True, "$nin": ["", None]}},
            ],
        },
    )
    rows = await _find_many(
        db,
        "tiktok_ads_daily",
        {"user_id": user_id},
        sort=[("date", -1), ("updated_at", -1)],
    )
    data_rows = [row for row in rows if row]
    account_rows: list[dict] = []
    if connection and connection.get("advertiser_id"):
        account_rows.append(
            _account(
                user_id,
                definition.provider,
                external_account_id=connection.get("advertiser_id"),
                ad_account_id=connection.get("advertiser_id"),
                display_name=connection.get("advertiser_name"),
                currency=connection.get("currency"),
                timezone_name=connection.get("timezone"),
            )
        )
    for row in data_rows:
        if row.get("advertiser_id"):
            account_rows.append(
                _account(
                    user_id,
                    definition.provider,
                    external_account_id=row.get("advertiser_id"),
                    ad_account_id=row.get("advertiser_id"),
                    display_name=row.get("advertiser_name"),
                )
            )
    has_data = bool(data_rows)
    raw_status = str((connection or {}).get("connection_status") or "").lower()
    if raw_status in {"expired", "token_expired", "needs_reauth"}:
        status = "needs_reauth"
        source_mode = "legacy_connection"
    elif raw_status in {"error", "failed", "last_check_failed"}:
        status = "error"
        source_mode = "legacy_connection"
    elif credential_present and raw_status in {
        "connected",
        "active",
        "healthy",
        "verified",
    }:
        status = "connected"
        source_mode = "legacy_connection"
    elif has_data:
        status = "data_available"
        source_mode = "data_feed"
    else:
        status = "not_connected"
        source_mode = "legacy_fallback"
    last_sync = _latest_timestamp(
        (connection or {}).get("last_sync_at"),
        *(row.get("updated_at") or row.get("date") for row in data_rows),
    )
    latest_error = (
        _safe_legacy_error(
            provider_label=definition.name,
            code="legacy_tiktok_error",
            occurred_at=(connection or {}).get("last_error_at"),
            source_mode=source_mode,
        )
        if status == "error"
        else None
    )
    current_permissions = _split_permissions((connection or {}).get("scope"))
    return _snapshot(
        definition,
        connection_status=status,
        connection_provenance=(
            "api_connection"
            if credential_present
            and status in {"connected", "needs_reauth", "error"}
            else "data_feed"
            if has_data
            else "disconnected"
        ),
        source_mode=source_mode,
        accounts=account_rows,
        current_permissions=current_permissions,
        permissions_observed=bool(current_permissions),
        last_sync_at=last_sync,
        latest_error=latest_error,
        has_data=has_data,
        capability_evidence=_advertising_evidence(data_rows),
    )


async def _read_meta(db: Any, user_id: str, definition: ProviderDefinition) -> dict:
    connection = await _find_one(
        db,
        "meta_connections",
        {"user_id": user_id},
    )
    credential_present = await _credential_present(
        db,
        "meta_connections",
        {
            "user_id": user_id,
            "access_token": {"$exists": True, "$nin": ["", None]},
        },
    )
    daily = await _find_one(
        db,
        "meta_ads_daily",
        {"user_id": user_id},
        sort=[("date", -1), ("updated_at", -1)],
    )
    ads_accounts = await _find_many(
        db,
        "ads_accounts",
        {
            "user_id": user_id,
            "provider": {"$in": ["meta", "meta_ads"]},
            "soft_deleted": {"$ne": True},
        },
        sort=[("updated_at", -1)],
    )

    accounts = [
        _account(
            user_id,
            definition.provider,
            external_account_id=row.get("external_account_id"),
            ad_account_id=row.get("external_account_id"),
            display_name=row.get("display_name"),
            currency=row.get("currency_native"),
            timezone_name=row.get("timezone"),
        )
        for row in ads_accounts
        if row.get("external_account_id")
    ]
    connection_account_id = (connection or {}).get("ad_account_id")
    daily_account_id = (daily or {}).get("account_id") or (daily or {}).get("ad_account_id")
    if connection_account_id:
        accounts.append(
            _account(
                user_id,
                definition.provider,
                external_account_id=connection_account_id,
                ad_account_id=connection_account_id,
                display_name=(connection or {}).get("ad_account_name"),
                currency=(connection or {}).get("currency"),
                timezone_name=(connection or {}).get("timezone"),
            )
        )
    if daily_account_id:
        accounts.append(
            _account(
                user_id,
                definition.provider,
                external_account_id=daily_account_id,
                ad_account_id=daily_account_id,
                display_name=daily_account_id,
            )
        )

    raw_status = str((connection or {}).get("connection_status") or "").lower()
    has_data = bool(daily)
    if raw_status in {"expired", "token_expired", "needs_reauth"}:
        status = "needs_reauth"
    elif raw_status in {"error", "failed", "last_check_failed"}:
        status = "error"
    elif credential_present and raw_status in {
        "ok",
        "connected",
        "active",
        "healthy",
    }:
        status = "connected"
    elif credential_present:
        status = "unknown"
    elif has_data:
        status = "data_available"
    else:
        status = "not_connected"
    last_sync = _latest_timestamp(
        (connection or {}).get("last_sync_at"),
        (connection or {}).get("updated_at"),
        (daily or {}).get("updated_at"),
        (daily or {}).get("date"),
        *(row.get("last_sync_at") or row.get("updated_at") for row in ads_accounts),
    )
    latest_error = (
        _safe_legacy_error(
            provider_label=definition.name,
            code="legacy_meta_error",
            occurred_at=(connection or {}).get("last_error_at"),
            source_mode="legacy_connection",
        )
        if status == "error"
        else None
    )
    current_permissions = _split_permissions((connection or {}).get("scope"))
    return _snapshot(
        definition,
        connection_status=status,
        connection_provenance=(
            "api_connection"
            if credential_present
            else "data_feed"
            if has_data
            else "disconnected"
        ),
        source_mode="legacy_connection" if credential_present else "legacy_data",
        accounts=accounts,
        current_permissions=current_permissions,
        # Meta's live diagnostic can read granted scopes but the legacy
        # connector does not persist them. Empty scope evidence is therefore
        # unknown, not a confirmed list of missing permissions.
        permissions_observed=bool(current_permissions),
        last_sync_at=last_sync,
        latest_error=latest_error,
        has_data=has_data,
        capability_evidence=_advertising_evidence([daily] if daily else []),
    )


async def _read_qoyod(db: Any, user_id: str, definition: ProviderDefinition) -> dict:
    # The current Qoyod connector intentionally uses the legacy singleton
    # tenant "main".  Prefer a user-scoped row and only then inspect that
    # established singleton source.  The V2 snapshot remains scoped to user_id.
    legacy_tenant = user_id
    credential_present = await _credential_present(
        db,
        "qoyod_credentials",
        {"user_id": user_id, "api_key_enc": {"$exists": True, "$ne": None}},
    )
    if not credential_present and user_id != "main":
        credential_present = await _credential_present(
            db,
            "qoyod_credentials",
            {"user_id": "main", "api_key_enc": {"$exists": True, "$ne": None}},
        )
        if credential_present:
            legacy_tenant = "main"
    credential = await _find_one(
        db,
        "qoyod_credentials",
        {"user_id": legacy_tenant},
    )
    settings = await _find_one(
        db,
        "qoyod_settings",
        {"user_id": legacy_tenant},
    )
    invoice = await _find_one(
        db,
        "qoyod_invoices",
        {"user_id": legacy_tenant},
        sort=[("updated_at", -1)],
    )
    verified_at = _parse_datetime((credential or {}).get("last_verified_at"))
    rotated_at = _parse_datetime((credential or {}).get("rotated_at"))
    credential_verified = bool(
        credential_present
        and verified_at
        and (rotated_at is None or verified_at >= rotated_at)
    )
    status = (
        "connected"
        if credential_verified
        else "unknown"
        if credential_present
        else "not_connected"
    )
    invoice_status = str((invoice or {}).get("status") or "").lower()
    invoice_stage = str((invoice or {}).get("pipeline_stage") or "").lower()
    latest_error = None
    if (invoice or {}).get("error_code") or invoice_status in {
        "error",
        "failed",
        "dead_letter",
    } or invoice_stage in {"error", "failed", "dead_letter"}:
        latest_error = _safe_legacy_error(
            provider_label=definition.name,
            code=(invoice or {}).get("error_code") or "legacy_qoyod_error",
            occurred_at=(invoice or {}).get("updated_at"),
            source_mode="legacy_connection",
        )
    last_sync = _latest_timestamp(
        (invoice or {}).get("sent_at"),
        (invoice or {}).get("updated_at"),
        (credential or {}).get("last_verified_at"),
        (credential or {}).get("updated_at"),
        (settings or {}).get("last_verified_at"),
        (settings or {}).get("updated_at"),
    )
    accounts = []
    if credential_present or settings:
        accounts.append(
            _account(
                user_id,
                definition.provider,
                external_account_id=legacy_tenant,
                display_name="Qoyod",
                currency="SAR",
                timezone_name="Asia/Riyadh",
            )
        )
    return _snapshot(
        definition,
        connection_status=status,
        connection_provenance=(
            "legacy_integration" if credential_present else "disconnected"
        ),
        source_mode="legacy_connection",
        accounts=accounts,
        current_permissions=["api_credentials"] if credential_present else [],
        permissions_observed=credential_present,
        last_sync_at=last_sync,
        latest_error=latest_error,
        has_data=bool(invoice),
    )


async def read_provider_snapshot(
    db: Any,
    user_id: str,
    definition: ProviderDefinition,
) -> dict:
    """Read one provider using only its catalogued transitional sources."""
    provider = definition.provider
    if provider == "salla":
        result = await _read_salla(db, user_id, definition)
    elif provider == "snapchat_ads":
        result = await _read_snapchat(db, user_id, definition)
    elif provider == "tiktok_ads":
        result = await _read_tiktok(db, user_id, definition)
    elif provider == "meta_ads":
        result = await _read_meta(db, user_id, definition)
    elif provider == "qoyod":
        result = await _read_qoyod(db, user_id, definition)
    elif definition.planned:
        result = _snapshot(
            definition,
            connection_status="planned",
            connection_provenance="planned",
            source_mode="planned",
        )
    else:
        result = _snapshot(
            definition,
            connection_status="not_configured",
            connection_provenance="disconnected",
            source_mode="not_configured",
        )
    return sanitize_for_output(result)
