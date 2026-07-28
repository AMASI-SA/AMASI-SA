"""Application service for the Apps & Integrations Control Center V2."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .catalog import (
    PROVIDERS,
    PROVIDER_BY_ID,
    SAFETY_POLICY,
    ProviderDefinition,
    build_capability_matrix,
)
from .legacy_readers import (
    data_delay_minutes,
    read_provider_snapshot,
    sanitize_for_output,
)


V2_PROJECTIONS: dict[str, dict[str, int]] = {
    "mezan_integrations_v2": {
        "_id": 0,
        "user_id": 1,
        "provider": 1,
        "connection_status": 1,
        "source_mode": 1,
        "last_sync_at": 1,
        "data_delay_minutes": 1,
        "data_quality": 1,
        "has_data": 1,
        "capability_evidence": 1,
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
        "permissions": 1,
        "capabilities": 1,
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
    "snapchat_ads": "/snapchat-accounts",
    "meta_ads": "/settings",
    "qoyod": "/integrations/qoyod/settings",
}


def _actions(definition: ProviderDefinition) -> dict:
    can_inspect = bool(definition.legacy_sources) and not definition.planned
    settings_href = _SAFE_SETTINGS_DEEP_LINKS.get(definition.provider)
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
    snapshot: dict,
    capabilities: dict[str, dict],
    health: dict,
) -> dict:
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
        "connection_status": _normalise_connection_status(
            account.get("connection_status") or snapshot.get("connection_status")
        ),
        "capabilities": capabilities,
        "permissions": list(
            account.get("permissions") or snapshot.get("current_permissions") or []
        ),
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
    }


def _card_from_snapshot(
    definition: ProviderDefinition,
    snapshot: dict,
    *,
    checked_at: str | None = None,
    health_override: dict | None = None,
) -> dict:
    snapshot = dict(snapshot)
    snapshot["provider"] = definition.provider
    snapshot["connection_status"] = _normalise_connection_status(
        snapshot.get("connection_status")
    )
    current = sorted(set(snapshot.get("current_permissions") or []))
    missing = sorted(set(snapshot.get("missing_permissions") or []))
    capabilities = build_capability_matrix(
        definition,
        connection_status=snapshot["connection_status"],
        has_data=bool(snapshot.get("has_data")),
        current_permissions=current,
        evidence_capabilities=snapshot.get("capability_evidence") or [],
    )
    if health_override:
        observed_status = _normalise_connection_status(
            health_override.get("connection_status")
        )
        if observed_status != snapshot["connection_status"]:
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
            snapshot=snapshot,
            capabilities=capabilities,
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
            "source_mode": snapshot.get("source_mode") or "unknown",
            "accounts": accounts,
            "permissions": {
                "current": current,
                "missing": missing,
                "unknown": bool(
                    snapshot["connection_status"] == "connected"
                    and not current
                    and bool(definition.required_permissions)
                ),
            },
            "capabilities": capabilities,
            "last_sync_at": snapshot.get("last_sync_at"),
            "data_delay_minutes": snapshot.get("data_delay_minutes"),
            "health": health,
            "latest_error": snapshot.get("latest_error"),
            "ai": _ai_actions(definition, snapshot, capabilities),
            "actions": _actions(definition),
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
                {"user_id": user_id, "provider": definition.provider},
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
        current = [
            row["permission_key"]
            for row in permissions
            if row.get("permission_status") == "current" and row.get("permission_key")
        ]
        missing = [
            row["permission_key"]
            for row in permissions
            if row.get("permission_status") == "missing" and row.get("permission_key")
        ]
        last_sync_at = integration.get("last_sync_at")
        delay = integration.get("data_delay_minutes")
        if delay is None:
            delay = data_delay_minutes(last_sync_at, now=self._now())
        snapshot = {
            "provider": definition.provider,
            "connection_status": integration.get("connection_status"),
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
        connected = sum(
            card["connection_status"] in {"connected", "data_available"}
            for card in cards
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
                    "healthy": healthy,
                    "missing_permissions": missing_permissions,
                    "attention_required": attention_required,
                },
                "providers": cards,
                "safety_policy": SAFETY_POLICY,
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
        snapshot = await read_provider_snapshot(self.db, user_id, definition)
        snapshot["connection_status"] = _normalise_connection_status(
            snapshot.get("connection_status")
        )
        snapshot["data_delay_minutes"] = data_delay_minutes(
            snapshot.get("last_sync_at"),
            now=self._now(),
        )
        health = _health_for(snapshot, checked_at=started_at)

        if snapshot["connection_status"] == "connected":
            run_status = "passed"
            message = "Local connection evidence is present."
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
            "source_mode": safe_snapshot.get("source_mode") or "unknown",
            "last_sync_at": safe_snapshot.get("last_sync_at"),
            "data_delay_minutes": safe_snapshot.get("data_delay_minutes"),
            "data_quality": safe_snapshot.get("data_quality") or "unknown",
            "has_data": bool(safe_snapshot.get("has_data")),
            "capability_evidence": list(
                safe_snapshot.get("capability_evidence") or []
            ),
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

        capabilities = build_capability_matrix(
            definition,
            connection_status=safe_snapshot["connection_status"],
            has_data=bool(safe_snapshot.get("has_data")),
            current_permissions=safe_snapshot.get("current_permissions") or [],
            evidence_capabilities=safe_snapshot.get("capability_evidence") or [],
        )
        for account in safe_snapshot.get("accounts") or []:
            account_doc = {
                "user_id": user_id,
                "provider": definition.provider,
                "mezan_integration_account_id": account[
                    "mezan_integration_account_id"
                ],
                "external_account_id": account.get("external_account_id"),
                "store_id": account.get("store_id"),
                "ad_account_id": account.get("ad_account_id"),
                "display_name": account.get("display_name"),
                "currency": account.get("currency"),
                "timezone": account.get("timezone"),
                "connection_status": safe_snapshot["connection_status"],
                "permissions": safe_snapshot.get("current_permissions") or [],
                "capabilities": capabilities,
                "last_sync_at": safe_snapshot.get("last_sync_at"),
                "data_delay_minutes": safe_snapshot.get("data_delay_minutes"),
                "health_score": health.get("score"),
                "source_mode": safe_snapshot.get("source_mode") or "unknown",
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
