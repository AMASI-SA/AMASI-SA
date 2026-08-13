"""Application service for the Apps & Integrations Control Center V2."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .catalog import (
    PROVIDERS,
    PROVIDER_BY_ID,
    ProviderDefinition,
    build_capability_matrix,
    build_safety_policy,
)
from .legacy_readers import (
    data_delay_minutes,
    read_provider_snapshot,
    sanitize_for_output,
)
from .snapchat_analytics_backfill import (
    SnapchatAnalyticsBackfill,
    SnapchatAnalyticsSyncError,
    SnapchatAnalyticsSyncInput,
    enumerate_sync_dates,
    snapchat_analytics_sync_enabled,
)

logger = logging.getLogger(__name__)
SNAPCHAT_SYNC_LOCK_TTL = timedelta(hours=4)
SNAPCHAT_IDEMPOTENCY_WINDOW = timedelta(minutes=5)
SNAPCHAT_RESPONSE_KEYS = (
    "run_id",
    "provider",
    "status",
    "date_from",
    "date_to",
    "accounts_attempted",
    "accounts_complete",
    "rows_saved",
    "errors_count",
    "source_only",
    "accounting_write_reached",
    "qoyod_write_reached",
)

V2_PROJECTIONS: dict[str, dict[str, int]] = {
    "mezan_integrations_v2": {
        "_id": 0,
        "user_id": 1,
        "provider": 1,
        "connection_status": 1,
        "connection_provenance": 1,
        "source_mode": 1,
        "last_sync_at": 1,
        "data_delay_minutes": 1,
        "data_quality": 1,
        "has_data": 1,
        "capability_evidence": 1,
        "permissions_observed": 1,
        "permission_observation_id": 1,
        "checked_at": 1,
        "created_at": 1,
        "updated_at": 1,
    },
    "mezan_integration_accounts_v2": {
        "_id": 0,
        "user_id": 1,
        "mezan_integration_account_id": 1,
        "provider": 1,
        "external_account_id": 1,
        "store_id": 1,
        "ad_account_id": 1,
        "display_name": 1,
        "currency": 1,
        "timezone": 1,
        "connection_status": 1,
        "connection_provenance": 1,
        "permissions": 1,
        "permissions_observed": 1,
        "capabilities": 1,
        "capability_evidence": 1,
        "has_data": 1,
        "last_sync_at": 1,
        "data_delay_minutes": 1,
        "health_score": 1,
        "source_mode": 1,
        "last_observed_at": 1,
    },
    "mezan_integration_permissions_v2": {
        "_id": 0,
        "user_id": 1,
        "provider": 1,
        "permission_key": 1,
        "permission_status": 1,
        "permission_observation_id": 1,
        "source_mode": 1,
        "observed_at": 1,
    },
    "mezan_integration_health_v2": {
        "_id": 0,
        "user_id": 1,
        "provider": 1,
        "health_status": 1,
        "health_score": 1,
        "data_quality": 1,
        "connection_status": 1,
        "connection_provenance": 1,
        "data_delay_minutes": 1,
        "checked_at": 1,
        "source_mode": 1,
    },
    "mezan_integration_sync_runs_v2": {
        "_id": 0,
        "run_id": 1,
        "user_id": 1,
        "provider": 1,
        "run_type": 1,
        "status": 1,
        "started_at": 1,
        "finished_at": 1,
        "source_mode": 1,
        "summary": 1,
        "error": 1,
    },
    "mezan_integration_errors_v2": {
        "_id": 0,
        "error_id": 1,
        "user_id": 1,
        "provider": 1,
        "code": 1,
        "message": 1,
        "occurred_at": 1,
        "retryable": 1,
        "source_mode": 1,
        "run_id": 1,
    },
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(now: datetime | None = None) -> str:
    return (now or _utcnow()).astimezone(timezone.utc).isoformat()


def _collection(db: Any, name: str) -> Any:
    try:
        return db[name]
    except (KeyError, TypeError, AttributeError):
        return getattr(db, name)


async def _find_one_v2(
    db: Any,
    collection_name: str,
    query: dict,
    *,
    sort: list[tuple[str, int]] | None = None,
) -> dict | None:
    collection = _collection(db, collection_name)
    projection = V2_PROJECTIONS[collection_name]
    if sort:
        doc = await collection.find_one(query, projection, sort=sort)
    else:
        doc = await collection.find_one(query, projection)
    return sanitize_for_output(doc) if doc else None


async def _find_many_v2(
    db: Any,
    collection_name: str,
    query: dict,
    *,
    sort: list[tuple[str, int]],
    limit: int,
) -> list[dict]:
    cursor = _collection(db, collection_name).find(
        query,
        V2_PROJECTIONS[collection_name],
    )
    cursor = cursor.sort(sort).limit(limit)
    if hasattr(cursor, "to_list"):
        rows = await cursor.to_list(length=limit)
    else:
        rows = [row async for row in cursor]
    return [sanitize_for_output(row) for row in rows]


def _normalise_connection_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "ok": "connected",
        "active": "connected",
        "healthy": "connected",
        "missing": "not_connected",
        "token_invalid": "needs_reauth",
        "token_expired": "needs_reauth",
        "failed": "error",
    }
    normalised = mapping.get(raw, raw)
    allowed = {
        "connected",
        "data_available",
        "not_connected",
        "not_configured",
        "needs_reauth",
        "expired",
        "error",
        "planned",
        "unknown",
    }
    return normalised if normalised in allowed else "unknown"


def _normalise_connection_provenance(
    value: Any,
    *,
    connection_status: str,
    source_mode: str,
    has_data: bool,
) -> str:
    raw = str(value or "").strip().lower()
    allowed = {
        "api_connection",
        "legacy_integration",
        "data_feed",
        "disconnected",
        "planned",
        "unknown",
    }
    if raw in allowed:
        return raw
    if connection_status == "planned":
        return "planned"
    if connection_status in {"not_connected", "not_configured"}:
        return "disconnected"
    if connection_status == "data_available" or source_mode in {
        "data_feed",
        "legacy_data",
    }:
        return "data_feed"
    # Never promote a legacy/v2 row to a real API connection merely because
    # its operational status string says "connected".
    return "unknown"


def _enforce_connection_invariants(snapshot: dict) -> dict:
    safe = dict(snapshot)
    status = _normalise_connection_status(safe.get("connection_status"))
    has_data = bool(safe.get("has_data"))
    provenance = _normalise_connection_provenance(
        safe.get("connection_provenance"),
        connection_status=status,
        source_mode=str(safe.get("source_mode") or ""),
        has_data=has_data,
    )
    if provenance == "planned":
        status = "planned"
    elif provenance in {"data_feed", "disconnected", "unknown"} and status == "connected":
        status = (
            "data_available"
            if has_data
            else "not_connected"
            if provenance == "disconnected"
            else "unknown"
        )
    safe["connection_status"] = status
    safe["connection_provenance"] = provenance
    return safe


def _health_for(snapshot: dict, *, checked_at: str | None = None) -> dict:
    status = _normalise_connection_status(snapshot.get("connection_status"))
    missing = len(snapshot.get("missing_permissions") or [])
    delay = snapshot.get("data_delay_minutes")
    latest_error = snapshot.get("latest_error")
    has_data = bool(snapshot.get("has_data"))

    if status == "planned":
        return {
            "status": "planned",
            "score": None,
            "checked_at": checked_at,
            "data_quality": "unavailable",
        }
    if status in {"not_connected", "not_configured", "unknown"}:
        return {
            "status": "not_available",
            "score": None,
            "checked_at": checked_at,
            "data_quality": snapshot.get("data_quality") or "unavailable",
        }

    if status == "connected":
        score = 100
    elif status == "data_available":
        score = 68
    elif status in {"needs_reauth", "expired"}:
        score = 25
    else:
        score = 15
    score -= min(missing * 8, 32)
    if latest_error:
        score -= 15
    if isinstance(delay, (int, float)):
        if delay > 7 * 24 * 60:
            score -= 30
        elif delay > 24 * 60:
            score -= 15
    if status == "connected" and not has_data:
        score -= 12
    score = max(0, min(100, int(score)))
    health_status = "healthy" if score >= 80 else "degraded" if score >= 50 else "unhealthy"
    return {
        "status": health_status,
        "score": score,
        "checked_at": checked_at,
        "data_quality": snapshot.get("data_quality") or "unknown",
    }


_SAFE_SETTINGS_DEEP_LINKS = {
    "salla": "/settings/salla",
    "instagram": "/integrations-v2/instagram",
    "meta_ads": "/settings",
    "qoyod": "/integrations-v2/qoyod",
}


def _actions(definition: ProviderDefinition, snapshot: dict) -> dict:
    can_inspect = bool(definition.legacy_sources) and not definition.planned
    settings_href = _SAFE_SETTINGS_DEEP_LINKS.get(definition.provider)
    snapchat_sync_enabled = bool(
        definition.provider == "snapchat_ads"
        and snapshot.get("connection_status") == "connected"
        and snapshot.get("accounts")
        and snapchat_analytics_sync_enabled()
    )
    return {
        "test_connection": {
            "enabled": can_inspect,
            "reason": (
                None
                if can_inspect
                else "No local read-only probe is available for this provider yet."
            ),
            "href": None,
        },
        "reconnect": {
            "enabled": bool(settings_href),
            "reason": (
                None
                if settings_href
                else "No approved provider-specific reconnect flow is available yet."
            ),
            "href": settings_href,
        },
        "settings": {
            "enabled": bool(settings_href),
            "reason": (
                None
                if settings_href
                else "Unified settings mutation is not part of Phase 1."
            ),
            "href": settings_href,
        },
        "disconnect": {
            "enabled": False,
            "reason": "Disconnect is a destructive credential action and is blocked here.",
            "href": None,
        },
        "sync_data": {
            "enabled": snapchat_sync_enabled,
            "reason": (
                None
                if snapchat_sync_enabled
                else (
                    "Snapchat analytics refresh is disabled by the runtime safety switch."
                    if (
                        definition.provider == "snapchat_ads"
                        and not snapchat_analytics_sync_enabled()
                    )
                    else "Connect Snapchat and select at least one ad account first."
                    if definition.provider == "snapchat_ads"
                    else "A V2-owned analytics refresh is not available for this provider yet."
                )
            ),
            "href": None,
        },
    }


def _ai_actions(
    definition: ProviderDefinition,
    snapshot: dict,
    capabilities: dict[str, dict],
) -> dict[str, list[str]]:
    connected_or_data = snapshot.get("connection_status") in {
        "connected",
        "data_available",
    }
    can = list(definition.ai_can_when_ready) if connected_or_data else []
    if definition.advertising:
        available = [
            key for key, value in capabilities.items() if value.get("state") == "available"
        ]
        if available:
            can.append("القدرات المتاحة حاليًا: " + "، ".join(available))
    cannot = list(definition.ai_cannot_phase_one)
    if not connected_or_data:
        cannot.insert(0, "لا توجد أدلة ربط أو بيانات كافية لهذه المنصة.")
    if snapshot.get("missing_permissions"):
        cannot.append(
            "صلاحيات ناقصة: " + "، ".join(snapshot["missing_permissions"])
        )
    return {"can": can, "cannot": cannot}


def _decorate_account(
    account: dict,
    *,
    definition: ProviderDefinition,
    snapshot: dict,
    health: dict,
) -> dict:
    account_count = len(snapshot.get("accounts") or [])
    if "has_data" in account:
        account_has_data = bool(account.get("has_data"))
    else:
        account_has_data = bool(
            account.get("last_sync_at")
            or account.get("data_delay_minutes") is not None
            or (account_count <= 1 and snapshot.get("has_data"))
        )
    account_status = (
        _normalise_connection_status(account.get("connection_status"))
        if account.get("connection_status") is not None
        else snapshot["connection_status"]
    )
    account_provenance = (
        _normalise_connection_provenance(
            account.get("connection_provenance"),
            connection_status=account_status,
            source_mode=str(
                account.get("source_mode") or snapshot.get("source_mode") or ""
            ),
            has_data=account_has_data,
        )
        if account.get("connection_provenance") is not None
        else snapshot["connection_provenance"]
    )
    account_state = _enforce_connection_invariants(
        {
            "connection_status": account_status,
            "connection_provenance": account_provenance,
            "source_mode": account.get("source_mode")
            or snapshot.get("source_mode")
            or "unknown",
            "has_data": account_has_data,
        }
    )
    if "permissions" in account:
        account_permissions = list(account.get("permissions") or [])
    else:
        account_permissions = list(snapshot.get("current_permissions") or [])
    account_permissions_observed = (
        bool(account.get("permissions_observed"))
        if "permissions_observed" in account
        else bool(snapshot.get("permissions_observed"))
    )
    if "capability_evidence" in account:
        account_evidence = list(account.get("capability_evidence") or [])
    elif account_count <= 1:
        account_evidence = list(snapshot.get("capability_evidence") or [])
    else:
        account_evidence = []
    account_capabilities = build_capability_matrix(
        definition,
        connection_status=account_state["connection_status"],
        has_data=account_has_data,
        current_permissions=account_permissions,
        permissions_observed=account_permissions_observed,
        evidence_capabilities=account_evidence,
    )
    return {
        "mezan_integration_account_id": str(
            account.get("mezan_integration_account_id") or ""
        ),
        "provider": str(account.get("provider") or snapshot["provider"]),
        "external_account_id": account.get("external_account_id"),
        "store_id": account.get("store_id"),
        "ad_account_id": account.get("ad_account_id"),
        "display_name": account.get("display_name"),
        "currency": account.get("currency"),
        "timezone": account.get("timezone"),
        "connection_status": account_state["connection_status"],
        "capabilities": account_capabilities,
        "permissions": account_permissions,
        "last_sync_at": account.get("last_sync_at") or snapshot.get("last_sync_at"),
        "data_delay_minutes": (
            account.get("data_delay_minutes")
            if account.get("data_delay_minutes") is not None
            else snapshot.get("data_delay_minutes")
        ),
        "health_score": (
            account.get("health_score")
            if account.get("health_score") is not None
            else health.get("score")
        ),
        "source_mode": str(
            account.get("source_mode") or snapshot.get("source_mode") or "unknown"
        ),
        "connection_provenance": account_state["connection_provenance"],
    }


def _card_from_snapshot(
    definition: ProviderDefinition,
    snapshot: dict,
    *,
    checked_at: str | None = None,
    health_override: dict | None = None,
) -> dict:
    snapshot = _enforce_connection_invariants(snapshot)
    snapshot["provider"] = definition.provider
    current = sorted(set(snapshot.get("current_permissions") or []))
    missing = sorted(set(snapshot.get("missing_permissions") or []))
    capabilities = build_capability_matrix(
        definition,
        connection_status=snapshot["connection_status"],
        has_data=bool(snapshot.get("has_data")),
        current_permissions=current,
        permissions_observed=bool(snapshot.get("permissions_observed")),
        evidence_capabilities=snapshot.get("capability_evidence") or [],
    )
    if health_override:
        observed_status = _normalise_connection_status(
            health_override.get("connection_status")
        )
        observed_provenance = _normalise_connection_provenance(
            health_override.get("connection_provenance"),
            connection_status=observed_status,
            source_mode=str(health_override.get("source_mode") or ""),
            has_data=bool(snapshot.get("has_data")),
        )
        if (
            observed_status != snapshot["connection_status"]
            or observed_provenance != snapshot["connection_provenance"]
        ):
            health_override = None
    health = health_override or _health_for(snapshot, checked_at=checked_at)
    # Stored health is evidence only; recalculate status fields that could
    # otherwise weaken the policy.  Score/data-quality may come from V2.
    if health_override:
        health = {
            "status": health_override.get("health_status")
            or health_override.get("status")
            or _health_for(snapshot).get("status"),
            "score": health_override.get("health_score")
            if health_override.get("health_score") is not None
            else health_override.get("score"),
            "checked_at": health_override.get("checked_at"),
            "data_quality": health_override.get("data_quality")
            or snapshot.get("data_quality")
            or "unknown",
        }
    accounts = [
        _decorate_account(
            account,
            definition=definition,
            snapshot=snapshot,
            health=health,
        )
        for account in snapshot.get("accounts") or []
        if account.get("mezan_integration_account_id")
    ]
    return sanitize_for_output(
        {
            "provider": definition.provider,
            "name": definition.name,
            "name_ar": definition.name_ar,
            "category": definition.category,
            "connection_status": snapshot["connection_status"],
            "connection_provenance": snapshot["connection_provenance"],
            "source_mode": snapshot.get("source_mode") or "unknown",
            "accounts": accounts,
            "permissions": {
                "current": current,
                "missing": missing,
                "unknown": bool(
                    snapshot["connection_provenance"]
                    in {"api_connection", "legacy_integration"}
                    and not snapshot.get("permissions_observed")
                    and bool(definition.required_permissions)
                ),
            },
            "capabilities": capabilities,
            "last_sync_at": snapshot.get("last_sync_at"),
            "data_delay_minutes": snapshot.get("data_delay_minutes"),
            "health": health,
            "latest_error": snapshot.get("latest_error"),
            "ai": _ai_actions(definition, snapshot, capabilities),
            "actions": _actions(definition, snapshot),
        }
    )


class IntegrationsControlCenterService:
    def __init__(
        self,
        db: Any,
        *,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.db = db
        self._now = now

    async def _v2_snapshot(
        self,
        user_id: str,
        definition: ProviderDefinition,
    ) -> tuple[dict, dict | None] | None:
        integration = await _find_one_v2(
            self.db,
            "mezan_integrations_v2",
            {"user_id": user_id, "provider": definition.provider},
        )
        if not integration:
            return None
        permission_query: dict[str, Any] = {
            "user_id": user_id,
            "provider": definition.provider,
        }
        permission_observation_id = integration.get("permission_observation_id")
        if permission_observation_id:
            permission_query["permission_observation_id"] = (
                permission_observation_id
            )
        accounts, permissions, health, latest_error = await asyncio.gather(
            _find_many_v2(
                self.db,
                "mezan_integration_accounts_v2",
                {"user_id": user_id, "provider": definition.provider},
                sort=[("last_observed_at", -1)],
                limit=50,
            ),
            _find_many_v2(
                self.db,
                "mezan_integration_permissions_v2",
                permission_query,
                sort=[("permission_key", 1)],
                limit=100,
            ),
            _find_one_v2(
                self.db,
                "mezan_integration_health_v2",
                {"user_id": user_id, "provider": definition.provider},
                sort=[("checked_at", -1)],
            ),
            _find_one_v2(
                self.db,
                "mezan_integration_errors_v2",
                {"user_id": user_id, "provider": definition.provider},
                sort=[("occurred_at", -1)],
            ),
        )
        permissions_observed = (
            bool(integration.get("permissions_observed"))
            if "permissions_observed" in integration
            else bool(permissions)
        )
        current = [
            row["permission_key"]
            for row in permissions
            if permissions_observed
            and row.get("permission_status") == "current"
            and row.get("permission_key")
        ]
        missing = (
            sorted(
                {
                    row["permission_key"]
                    for row in permissions
                    if row.get("permission_status") == "missing"
                    and row.get("permission_key")
                }
                | (set(definition.required_permissions) - set(current))
            )
            if permissions_observed
            else []
        )
        last_sync_at = integration.get("last_sync_at")
        delay = integration.get("data_delay_minutes")
        if delay is None:
            delay = data_delay_minutes(last_sync_at, now=self._now())
        snapshot = {
            "provider": definition.provider,
            "connection_status": integration.get("connection_status"),
            "connection_provenance": integration.get("connection_provenance"),
            "source_mode": integration.get("source_mode") or "v2_snapshot",
            "accounts": accounts,
            "current_permissions": current,
            "missing_permissions": missing,
            "last_sync_at": last_sync_at,
            "data_delay_minutes": delay,
            "latest_error": latest_error,
            "has_data": bool(integration.get("has_data")),
            "data_quality": integration.get("data_quality") or "unknown",
            "capability_evidence": list(
                integration.get("capability_evidence") or []
            ),
            "permissions_observed": permissions_observed,
        }
        return sanitize_for_output(snapshot), health

    async def _snapshot(
        self,
        user_id: str,
        definition: ProviderDefinition,
    ) -> tuple[dict, dict | None]:
        # Transitional providers must stay live: read their current local
        # evidence on every request.  V2 health is an append-only observation
        # that may decorate the card, never a frozen replacement for the
        # existing connection/account state.
        if definition.legacy_sources:
            legacy_snapshot, stored_health = await asyncio.gather(
                read_provider_snapshot(self.db, user_id, definition),
                _find_one_v2(
                    self.db,
                    "mezan_integration_health_v2",
                    {"user_id": user_id, "provider": definition.provider},
                    sort=[("checked_at", -1)],
                ),
            )
            return legacy_snapshot, stored_health

        # Providers without a legacy connector can use a future native V2
        # snapshot as their primary source.
        v2 = await self._v2_snapshot(user_id, definition)
        if v2 is not None:
            return v2
        return await read_provider_snapshot(self.db, user_id, definition), None

    async def overview(self, user_id: str) -> dict:
        now_iso = _iso(self._now())
        snapshots = await asyncio.gather(
            *(self._snapshot(user_id, definition) for definition in PROVIDERS)
        )
        cards = [
            _card_from_snapshot(
                definition,
                snapshot,
                checked_at=now_iso,
                health_override=stored_health,
            )
            for definition, (snapshot, stored_health) in zip(PROVIDERS, snapshots)
        ]
        connected = sum(card["connection_status"] == "connected" for card in cards)
        api_connections = sum(
            card["connection_provenance"] == "api_connection" for card in cards
        )
        legacy_integrations = sum(
            card["connection_provenance"] == "legacy_integration" for card in cards
        )
        data_feeds = sum(
            card["connection_provenance"] == "data_feed" for card in cards
        )
        disconnected = sum(
            card["connection_provenance"] == "disconnected" for card in cards
        )
        planned = sum(
            card["connection_provenance"] == "planned" for card in cards
        )
        unknown = sum(
            card["connection_provenance"] == "unknown" for card in cards
        )
        healthy = sum(card["health"]["status"] == "healthy" for card in cards)
        missing_permissions = sum(
            bool(card["permissions"]["missing"]) for card in cards
        )
        attention_required = sum(
            card["health"]["status"] in {"degraded", "unhealthy"}
            or card["connection_status"] in {"needs_reauth", "expired", "error"}
            for card in cards
        )
        return sanitize_for_output(
            {
                "generated_at": now_iso,
                "summary": {
                    "total": len(cards),
                    "connected": connected,
                    "api_connections": api_connections,
                    "legacy_integrations": legacy_integrations,
                    "data_feeds": data_feeds,
                    "disconnected": disconnected,
                    "planned": planned,
                    "unknown": unknown,
                    "healthy": healthy,
                    "missing_permissions": missing_permissions,
                    "attention_required": attention_required,
                },
                "providers": cards,
                "safety_policy": build_safety_policy(
                    analytics_refresh_enabled=(
                        snapchat_analytics_sync_enabled()
                    )
                ),
            }
        )

    async def capabilities(self, user_id: str) -> dict:
        overview = await self.overview(user_id)
        return {
            "generated_at": overview["generated_at"],
            "providers": [
                {
                    "provider": card["provider"],
                    "name": card["name"],
                    "name_ar": card["name_ar"],
                    "connection_status": card["connection_status"],
                    "connection_provenance": card["connection_provenance"],
                    "source_mode": card["source_mode"],
                    "capabilities": card["capabilities"],
                }
                for card in overview["providers"]
            ],
            "safety_policy": overview["safety_policy"],
        }

    async def list_sync_runs(
        self,
        user_id: str,
        *,
        provider: str | None = None,
        limit: int = 50,
    ) -> dict:
        bounded_limit = min(max(int(limit), 1), 100)
        query: dict[str, Any] = {"user_id": user_id}
        if provider:
            query["provider"] = provider
        items = await _find_many_v2(
            self.db,
            "mezan_integration_sync_runs_v2",
            query,
            sort=[("started_at", -1)],
            limit=bounded_limit,
        )
        total = await _collection(
            self.db, "mezan_integration_sync_runs_v2"
        ).count_documents(query)
        return {
            "items": items,
            "total": int(total),
            "limit": bounded_limit,
        }

    async def list_errors(
        self,
        user_id: str,
        *,
        provider: str | None = None,
        limit: int = 50,
    ) -> dict:
        bounded_limit = min(max(int(limit), 1), 100)
        query: dict[str, Any] = {"user_id": user_id}
        if provider:
            query["provider"] = provider
        items = await _find_many_v2(
            self.db,
            "mezan_integration_errors_v2",
            query,
            sort=[("occurred_at", -1)],
            limit=bounded_limit,
        )
        total = await _collection(
            self.db, "mezan_integration_errors_v2"
        ).count_documents(query)
        return {
            "items": items,
            "total": int(total),
            "limit": bounded_limit,
        }

    async def test_connection(self, user_id: str, provider: str) -> dict:
        """Inspect local evidence and write only sanitized V2 snapshots.

        The method name mirrors the UI action, but Phase 1 deliberately does
        not contact a provider, refresh a token, or mutate a legacy source.
        """
        definition = PROVIDER_BY_ID[provider]
        started_at = _iso(self._now())
        run_id = str(uuid.uuid4())
        snapshot = _enforce_connection_invariants(
            await read_provider_snapshot(self.db, user_id, definition)
        )
        snapshot["data_delay_minutes"] = data_delay_minutes(
            snapshot.get("last_sync_at"),
            now=self._now(),
        )
        health = _health_for(snapshot, checked_at=started_at)

        if snapshot["connection_status"] == "connected":
            run_status = "passed"
            message = (
                "Local configuration evidence is present. "
                "No provider network request was made."
            )
        elif snapshot["connection_status"] == "data_available":
            run_status = "data_only"
            message = "Local data exists, but no native management connection is proven."
        elif snapshot["connection_status"] == "planned":
            run_status = "planned"
            message = "This provider connector is planned for a later phase."
        else:
            run_status = "not_connected"
            message = "No verified local connection evidence was found."

        await self._persist_test_snapshot(
            user_id=user_id,
            definition=definition,
            snapshot=snapshot,
            health=health,
            run_id=run_id,
            run_status=run_status,
            started_at=started_at,
            message=message,
        )
        return sanitize_for_output(
            {
                "provider": provider,
                "run_id": run_id,
                "status": run_status,
                "health": health,
                "message": message,
            }
        )

    async def sync_snapchat_analytics(
        self,
        user_id: str,
        payload: SnapchatAnalyticsSyncInput,
        *,
        include_legacy_details: bool = False,
    ) -> dict:
        """Run the V2-owned Snapchat analytics refresh and audit the result."""
        provider = "snapchat_ads"
        runtime_sync_enabled = snapchat_analytics_sync_enabled()
        started_at = _iso(self._now())
        source_mode = "v2_owned_analytics"
        run_collection = _collection(
            self.db,
            "mezan_integration_sync_runs_v2",
        )
        running = await run_collection.find_one(
            {
                "user_id": user_id,
                "provider": provider,
                "run_type": "analytics_refresh",
                "status": "running",
            },
            {
                "_id": 0,
                "run_id": 1,
                "lock_expires_at": 1,
            },
        )
        if running:
            try:
                lock_expires_at = datetime.fromisoformat(
                    str(running.get("lock_expires_at") or "")
                )
            except (TypeError, ValueError):
                lock_expires_at = self._now() + SNAPCHAT_SYNC_LOCK_TTL
            if lock_expires_at > self._now():
                conflict = SnapchatAnalyticsSyncError(
                    "snapchat_analytics_sync_in_progress",
                    "A Snapchat analytics refresh is already running.",
                    status_code=409,
                    retryable=True,
                )
                conflict.run_id = running.get("run_id")
                raise conflict
            await run_collection.update_one(
                {
                    "user_id": user_id,
                    "run_id": running.get("run_id"),
                    "status": "running",
                },
                {
                    "$set": {
                        "status": "failed",
                        "finished_at": started_at,
                        "error": {
                            "code": "stale_sync_lock_recovered",
                        },
                    }
                },
            )

        try:
            from zoneinfo import ZoneInfo

            business_today = self._now().astimezone(
                ZoneInfo("Asia/Riyadh")
            ).date()
        except ImportError:  # pragma: no cover
            business_today = (
                self._now() + timedelta(hours=3)
            ).date()
        requested_dates = enumerate_sync_dates(
            payload,
            today=business_today,
        )
        fingerprint_source = (
            f"{user_id}:{provider}:{requested_dates[0].isoformat()}:"
            f"{requested_dates[-1].isoformat()}:"
            f"{payload.idempotency_key or ''}"
        )
        idempotency_key = hashlib.sha256(
            fingerprint_source.encode("utf-8")
        ).hexdigest()
        replay_cutoff = _iso(self._now() - SNAPCHAT_IDEMPOTENCY_WINDOW)
        prior = (
            await run_collection.find_one(
                {
                    "user_id": user_id,
                    "provider": provider,
                    "run_type": "analytics_refresh",
                    "idempotency_key": idempotency_key,
                    "status": {"$in": ["complete", "partial"]},
                    "finished_at": {"$gte": replay_cutoff},
                },
                {"_id": 0, "summary": 1},
                sort=[("finished_at", -1)],
            )
            if runtime_sync_enabled
            else None
        )
        if prior and isinstance(prior.get("summary"), dict):
            replay = {
                key: prior["summary"].get(key)
                for key in SNAPCHAT_RESPONSE_KEYS
            }
            if replay.get("status") in {"complete", "partial"}:
                return sanitize_for_output(replay)

        run_id = str(uuid.uuid4())
        lock_expires_at = _iso(self._now() + SNAPCHAT_SYNC_LOCK_TTL)
        try:
            await run_collection.insert_one(
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "provider": provider,
                    "run_type": "analytics_refresh",
                    "status": "running",
                    "started_at": started_at,
                    "finished_at": None,
                    "lock_expires_at": lock_expires_at,
                    "idempotency_key": idempotency_key,
                    "source_mode": source_mode,
                    "summary": {
                        "requested_days": payload.days,
                        "requested_from": payload.from_date,
                        "requested_to": payload.to_date,
                    },
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            if (
                getattr(exc, "code", None) == 11000
                or type(exc).__name__ == "DuplicateKeyError"
            ):
                conflict = SnapchatAnalyticsSyncError(
                    "snapchat_analytics_sync_in_progress",
                    "A Snapchat analytics refresh is already running.",
                    status_code=409,
                    retryable=True,
                )
                raise conflict from exc
            raise
        try:
            engine_result = await SnapchatAnalyticsBackfill(
                self.db,
                now=self._now,
            ).run(user_id, payload)
        except SnapchatAnalyticsSyncError as exc:
            await self._record_snapchat_sync_failure(
                user_id=user_id,
                run_id=run_id,
                started_at=started_at,
                source_mode=source_mode,
                error=exc,
            )
            exc.run_id = run_id
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Unexpected Snapchat V2 analytics refresh failure run_id=%s",
                run_id,
            )
            safe_error = SnapchatAnalyticsSyncError(
                "snapchat_analytics_sync_failed",
                "Snapchat analytics refresh failed.",
                status_code=502,
                retryable=True,
            )
            await self._record_snapchat_sync_failure(
                user_id=user_id,
                run_id=run_id,
                started_at=started_at,
                source_mode=source_mode,
                error=safe_error,
            )
            safe_error.run_id = run_id
            raise safe_error from exc

        finished_at = _iso(self._now())
        status_value = (
            "complete"
            if engine_result.get("sync_status") == "complete"
            else "partial"
        )
        errors_count = int(
            engine_result.get("errors_count")
            if engine_result.get("errors_count") is not None
            else len(engine_result.get("errors") or [])
        )
        response = sanitize_for_output(
            {
                "run_id": run_id,
                "provider": provider,
                "status": status_value,
                "date_from": engine_result.get("date_from"),
                "date_to": engine_result.get("date_to"),
                "accounts_attempted": int(
                    engine_result.get("accounts_synced") or 0
                ),
                "accounts_complete": int(
                    engine_result.get("accounts_complete") or 0
                ),
                "rows_saved": int(engine_result.get("rows_saved") or 0),
                "errors_count": errors_count,
                "source_only": True,
                "accounting_write_reached": False,
                "qoyod_write_reached": False,
            }
        )
        summary = {
            **response,
            "business_timezone": engine_result.get("business_timezone"),
            "errors_truncated": bool(
                engine_result.get("errors_truncated")
            ),
        }
        partial_error = None
        if status_value == "partial":
            needs_reauth = bool(engine_result.get("needs_reauth"))
            partial_code = (
                "snapchat_analytics_needs_reauth"
                if needs_reauth
                else "snapchat_analytics_partial"
            )
            partial_error = await self._insert_snapchat_sync_error(
                user_id=user_id,
                run_id=run_id,
                code=partial_code,
                message=(
                    "Snapchat authorization must be renewed."
                    if needs_reauth
                    else (
                        "Snapchat analytics refresh completed with "
                        f"{errors_count} bounded errors."
                    )
                ),
                occurred_at=finished_at,
                retryable=not needs_reauth,
                source_mode=source_mode,
            )
        await run_collection.update_one(
            {"user_id": user_id, "run_id": run_id},
            {
                "$set": {
                    "status": status_value,
                    "finished_at": finished_at,
                    "summary": summary,
                    "error": (
                        {
                            "error_id": partial_error,
                            "code": partial_code,
                        }
                        if partial_error
                        else None
                    ),
                }
            },
        )
        health_status = "healthy" if status_value == "complete" else "degraded"
        health_score = 100 if status_value == "complete" else 65
        data_quality = "complete" if status_value == "complete" else "partial"
        needs_reauth = bool(engine_result.get("needs_reauth"))
        observed_connection_status = (
            "needs_reauth" if needs_reauth else "connected"
        )
        await _collection(
            self.db,
            "mezan_integration_health_v2",
        ).insert_one(
            {
                "user_id": user_id,
                "provider": provider,
                "health_status": health_status,
                "health_score": health_score,
                "data_quality": data_quality,
                "connection_status": observed_connection_status,
                "connection_provenance": "legacy_integration",
                "data_delay_minutes": (
                    0 if status_value == "complete" else None
                ),
                "checked_at": finished_at,
                "source_mode": source_mode,
                "run_id": run_id,
            }
        )
        integration_patch = {
            "user_id": user_id,
            "provider": provider,
            "connection_status": observed_connection_status,
            "connection_provenance": "legacy_integration",
            "source_mode": source_mode,
            "data_quality": data_quality,
            "checked_at": finished_at,
            "updated_at": finished_at,
        }
        if int(engine_result.get("rows_saved") or 0) > 0:
            integration_patch["has_data"] = True
        if (
            status_value == "complete"
            and int(engine_result.get("rows_saved") or 0) > 0
        ):
            integration_patch["last_sync_at"] = finished_at
            integration_patch["data_delay_minutes"] = 0
        await _collection(self.db, "mezan_integrations_v2").update_one(
            {"user_id": user_id, "provider": provider},
            {
                "$set": integration_patch,
                "$setOnInsert": {"created_at": started_at},
            },
            upsert=True,
        )
        for item in engine_result.get("items") or []:
            account_id = str(item.get("ad_account_id") or "")
            if not account_id:
                continue
            account_complete = (
                int(item.get("rows_saved") or 0)
                == int(engine_result.get("days_requested") or 0)
                and int(item.get("errors") or 0) == 0
            )
            account_doc = {
                "user_id": user_id,
                "provider": provider,
                "mezan_integration_account_id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        "mezan-integration:"
                        f"{user_id}:{provider}:{account_id}",
                    )
                ),
                "external_account_id": account_id,
                "ad_account_id": account_id,
                "display_name": item.get("name"),
                "currency": item.get("currency_native"),
                "connection_status": observed_connection_status,
                "connection_provenance": "legacy_integration",
                "permissions": [],
                "permissions_observed": False,
                "capabilities": {},
                "capability_evidence": ["insights.read"],
                "has_data": int(item.get("rows_saved") or 0) > 0,
                "health_score": 100 if account_complete else 65,
                "source_mode": source_mode,
                "last_observed_at": finished_at,
            }
            if account_complete:
                account_doc["last_sync_at"] = finished_at
                account_doc["data_delay_minutes"] = 0
            await _collection(
                self.db,
                "mezan_integration_accounts_v2",
            ).update_one(
                {
                    "user_id": user_id,
                    "provider": provider,
                    "external_account_id": account_id,
                },
                {
                    "$set": account_doc,
                    "$setOnInsert": {"created_at": started_at},
                },
                upsert=True,
            )
        if include_legacy_details:
            return {
                **engine_result,
                **response,
                "sync_status": response["status"],
            }
        return response

    async def _insert_snapchat_sync_error(
        self,
        *,
        user_id: str,
        run_id: str,
        code: str,
        message: str,
        occurred_at: str,
        retryable: bool,
        source_mode: str,
    ) -> str:
        error_id = str(uuid.uuid4())
        await _collection(
            self.db,
            "mezan_integration_errors_v2",
        ).insert_one(
            {
                "error_id": error_id,
                "user_id": user_id,
                "provider": "snapchat_ads",
                "code": code,
                "message": message,
                "occurred_at": occurred_at,
                "retryable": retryable,
                "source_mode": source_mode,
                "run_id": run_id,
            }
        )
        return error_id

    async def _record_snapchat_sync_failure(
        self,
        *,
        user_id: str,
        run_id: str,
        started_at: str,
        source_mode: str,
        error: SnapchatAnalyticsSyncError,
    ) -> None:
        finished_at = _iso(self._now())
        failure_result = error.result or {}
        provider_state_failure = bool(
            error.code
            in {
                "snapchat_not_connected",
                "snapchat_needs_reauth",
                "snapchat_analytics_no_rows",
                "snapchat_token_refresh_rejected",
                "snapchat_token_refresh_failed",
                "snapchat_token_missing",
                "snapchat_currency_unverified",
                "snapchat_usd_rate_unverified",
            }
            or failure_result.get("needs_reauth")
        )
        failure_connection_status = (
            "not_connected"
            if error.code == "snapchat_not_connected"
            else "needs_reauth"
            if (
                error.code == "snapchat_needs_reauth"
                or failure_result.get("needs_reauth")
            )
            else "error"
            if provider_state_failure
            else "unknown"
        )
        error_id = await self._insert_snapchat_sync_error(
            user_id=user_id,
            run_id=run_id,
            code=error.code,
            message=error.message,
            occurred_at=finished_at,
            retryable=error.retryable,
            source_mode=source_mode,
        )
        await _collection(
            self.db,
            "mezan_integration_sync_runs_v2",
        ).update_one(
            {"user_id": user_id, "run_id": run_id},
            {
                "$set": {
                    "status": "failed",
                    "finished_at": finished_at,
                    "summary": {
                        "run_id": run_id,
                        "provider": "snapchat_ads",
                        "status": "failed",
                        "date_from": failure_result.get("date_from"),
                        "date_to": failure_result.get("date_to"),
                        "accounts_attempted": int(
                            failure_result.get("accounts_synced") or 0
                        ),
                        "accounts_complete": int(
                            failure_result.get("accounts_complete") or 0
                        ),
                        "rows_saved": int(
                            failure_result.get("rows_saved") or 0
                        ),
                        "errors_count": int(
                            failure_result.get("errors_count") or 1
                        ),
                        "source_only": True,
                        "accounting_write_reached": False,
                        "qoyod_write_reached": False,
                    },
                    "error": {
                        "error_id": error_id,
                        "code": error.code,
                    },
                }
            },
        )
        await _collection(
            self.db,
            "mezan_integration_health_v2",
        ).insert_one(
            {
                "user_id": user_id,
                "provider": "snapchat_ads",
                "health_status": (
                    "unhealthy" if provider_state_failure else "unknown"
                ),
                "health_score": 20 if provider_state_failure else None,
                "data_quality": (
                    "unavailable" if provider_state_failure else "unknown"
                ),
                "connection_status": failure_connection_status,
                "connection_provenance": (
                    "disconnected"
                    if error.code == "snapchat_not_connected"
                    else "legacy_integration"
                ),
                "data_delay_minutes": None,
                "checked_at": finished_at,
                "source_mode": source_mode,
                "run_id": run_id,
            }
        )
        if provider_state_failure:
            await _collection(self.db, "mezan_integrations_v2").update_one(
                {"user_id": user_id, "provider": "snapchat_ads"},
                {
                    "$set": {
                        "user_id": user_id,
                        "provider": "snapchat_ads",
                        "connection_status": failure_connection_status,
                        "connection_provenance": (
                            "disconnected"
                            if failure_connection_status == "not_connected"
                            else "legacy_integration"
                        ),
                        "source_mode": source_mode,
                        "data_quality": "unavailable",
                        "checked_at": finished_at,
                        "updated_at": finished_at,
                    },
                    "$setOnInsert": {"created_at": started_at},
                },
                upsert=True,
            )

    async def _persist_test_snapshot(
        self,
        *,
        user_id: str,
        definition: ProviderDefinition,
        snapshot: dict,
        health: dict,
        run_id: str,
        run_status: str,
        started_at: str,
        message: str,
    ) -> None:
        now_iso = _iso(self._now())
        safe_snapshot = sanitize_for_output(snapshot)
        integration_doc = {
            "user_id": user_id,
            "provider": definition.provider,
            "connection_status": safe_snapshot["connection_status"],
            "connection_provenance": safe_snapshot["connection_provenance"],
            "source_mode": safe_snapshot.get("source_mode") or "unknown",
            "last_sync_at": safe_snapshot.get("last_sync_at"),
            "data_delay_minutes": safe_snapshot.get("data_delay_minutes"),
            "data_quality": safe_snapshot.get("data_quality") or "unknown",
            "has_data": bool(safe_snapshot.get("has_data")),
            "capability_evidence": list(
                safe_snapshot.get("capability_evidence") or []
            ),
            "permissions_observed": bool(
                safe_snapshot.get("permissions_observed")
            ),
            "permission_observation_id": run_id,
            "checked_at": started_at,
            "updated_at": now_iso,
        }
        await _collection(self.db, "mezan_integrations_v2").update_one(
            {"user_id": user_id, "provider": definition.provider},
            {
                "$set": integration_doc,
                "$setOnInsert": {"created_at": now_iso},
            },
            upsert=True,
        )

        for account in safe_snapshot.get("accounts") or []:
            decorated_account = _decorate_account(
                account,
                definition=definition,
                snapshot=safe_snapshot,
                health=health,
            )
            account_doc = {
                "user_id": user_id,
                "provider": definition.provider,
                "mezan_integration_account_id": decorated_account[
                    "mezan_integration_account_id"
                ],
                "external_account_id": decorated_account.get("external_account_id"),
                "store_id": decorated_account.get("store_id"),
                "ad_account_id": decorated_account.get("ad_account_id"),
                "display_name": decorated_account.get("display_name"),
                "currency": decorated_account.get("currency"),
                "timezone": decorated_account.get("timezone"),
                "connection_status": decorated_account["connection_status"],
                "connection_provenance": decorated_account[
                    "connection_provenance"
                ],
                "permissions": decorated_account.get("permissions") or [],
                "permissions_observed": bool(
                    account.get("permissions_observed")
                    if "permissions_observed" in account
                    else safe_snapshot.get("permissions_observed")
                ),
                "capabilities": decorated_account["capabilities"],
                "capability_evidence": list(
                    account.get("capability_evidence") or []
                ),
                "has_data": bool(
                    account.get("has_data")
                    if "has_data" in account
                    else (
                        account.get("last_sync_at")
                        or account.get("data_delay_minutes") is not None
                        or (
                            len(safe_snapshot.get("accounts") or []) <= 1
                            and safe_snapshot.get("has_data")
                        )
                    )
                ),
                "last_sync_at": decorated_account.get("last_sync_at"),
                "data_delay_minutes": decorated_account.get("data_delay_minutes"),
                "health_score": decorated_account.get("health_score"),
                "source_mode": decorated_account.get("source_mode") or "unknown",
                "last_observed_at": now_iso,
            }
            await _collection(
                self.db, "mezan_integration_accounts_v2"
            ).update_one(
                {
                    "user_id": user_id,
                    "provider": definition.provider,
                    "external_account_id": account_doc["external_account_id"],
                },
                {
                    "$set": account_doc,
                    "$setOnInsert": {"created_at": now_iso},
                },
                upsert=True,
            )

        current = set(safe_snapshot.get("current_permissions") or [])
        missing = set(safe_snapshot.get("missing_permissions") or [])
        for permission_key in sorted(current | missing):
            permission_doc = {
                "user_id": user_id,
                "provider": definition.provider,
                "permission_key": permission_key,
                "permission_status": (
                    "current" if permission_key in current else "missing"
                ),
                "permission_observation_id": run_id,
                "source_mode": safe_snapshot.get("source_mode") or "unknown",
                "observed_at": now_iso,
            }
            await _collection(
                self.db, "mezan_integration_permissions_v2"
            ).update_one(
                {
                    "user_id": user_id,
                    "provider": definition.provider,
                    "permission_key": permission_key,
                },
                {"$set": permission_doc},
                upsert=True,
            )

        await _collection(self.db, "mezan_integration_health_v2").insert_one(
            {
                "user_id": user_id,
                "provider": definition.provider,
                "health_status": health["status"],
                "health_score": health["score"],
                "data_quality": health["data_quality"],
                "connection_status": safe_snapshot["connection_status"],
                "connection_provenance": safe_snapshot["connection_provenance"],
                "data_delay_minutes": safe_snapshot.get("data_delay_minutes"),
                "checked_at": started_at,
                "source_mode": safe_snapshot.get("source_mode") or "unknown",
            }
        )

        safe_error = safe_snapshot.get("latest_error")
        error_ref = None
        if safe_error:
            error_ref = str(uuid.uuid4())
            await _collection(
                self.db, "mezan_integration_errors_v2"
            ).insert_one(
                {
                    "error_id": error_ref,
                    "user_id": user_id,
                    "provider": definition.provider,
                    "code": safe_error.get("code") or "provider_error",
                    "message": safe_error.get("message") or "Provider error",
                    "occurred_at": safe_error.get("occurred_at") or started_at,
                    "retryable": False,
                    "source_mode": safe_snapshot.get("source_mode") or "unknown",
                    "run_id": run_id,
                }
            )

        await _collection(
            self.db, "mezan_integration_sync_runs_v2"
        ).insert_one(
            {
                "run_id": run_id,
                "user_id": user_id,
                "provider": definition.provider,
                "run_type": "local_connection_test",
                "status": run_status,
                "started_at": started_at,
                "finished_at": now_iso,
                "source_mode": safe_snapshot.get("source_mode") or "unknown",
                "summary": {
                    "message": message,
                    "connection_status": safe_snapshot["connection_status"],
                    "connection_provenance": safe_snapshot[
                        "connection_provenance"
                    ],
                    "health_status": health["status"],
                    "health_score": health["score"],
                    "data_quality": health["data_quality"],
                    "account_count": len(safe_snapshot.get("accounts") or []),
                    "current_permissions_count": len(current),
                    "missing_permissions_count": len(missing),
                },
                "error": (
                    {"error_id": error_ref, "code": safe_error.get("code")}
                    if safe_error
                    else None
                ),
            }
        )
