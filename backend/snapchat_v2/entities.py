"""Identity facts for Snapchat V2 campaigns, ad squads, and ads."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from .models import SNAPCHAT_PROVIDER, clean_text

SNAPCHAT_ENTITY_FACTS_COLLECTION = "mezan_snapchat_entity_facts_v2"
ENTITY_TYPES = {"campaign", "ad_squad", "ad"}
MAX_ENTITY_ROWS = 20_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_entity_indexes(db: Any) -> None:
    collection = db[SNAPCHAT_ENTITY_FACTS_COLLECTION]
    await collection.create_index(
        [("user_id", 1), ("ad_account_id", 1), ("entity_type", 1), ("external_id", 1)],
        unique=True,
        name="snapchat_v2_entity_identity_unique",
    )
    await collection.create_index(
        [("user_id", 1), ("ad_account_id", 1), ("entity_type", 1), ("active", 1)],
        name="snapchat_v2_entity_active_type",
    )
    await collection.create_index(
        [("sync_run_id", 1), ("updated_at", -1)],
        name="snapchat_v2_entity_sync_run",
    )


def normalize_entity(
    user_id: str,
    ad_account_id: str,
    entity_type: str,
    row: dict[str, Any],
    *,
    sync_run_id: str,
) -> dict[str, Any]:
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"Unsupported Snapchat entity type: {entity_type}")
    external_id = clean_text(row.get("external_id") or row.get("id"), limit=128)
    if not external_id:
        raise ValueError("Snapchat entity is missing external_id")
    raw = dict(row.get("raw") or {})
    for secret in ("access_token", "refresh_token", "authorization", "client_secret"):
        raw.pop(secret, None)
    return {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": str(ad_account_id),
        "entity_type": entity_type,
        "external_id": external_id,
        "name": clean_text(row.get("name") or external_id, limit=300),
        "status": clean_text(row.get("status"), limit=64) or None,
        "campaign_id": clean_text(row.get("campaign_id"), limit=128) or None,
        "ad_squad_id": clean_text(row.get("ad_squad_id"), limit=128) or None,
        "creative_id": clean_text(row.get("creative_id"), limit=128) or None,
        "raw": raw,
        "sync_run_id": str(sync_run_id),
        "active": True,
    }


async def sync_entities(
    db: Any,
    *,
    user_id: str,
    ad_account_id: str,
    entity_type: str,
    rows: Iterable[dict[str, Any]],
    sync_run_id: str,
    now: datetime | None = None,
) -> dict[str, int]:
    await ensure_entity_indexes(db)
    current = (now or _utcnow()).astimezone(timezone.utc)
    normalized_by_id: dict[str, dict[str, Any]] = {}
    for source in rows:
        normalized = normalize_entity(
            user_id,
            ad_account_id,
            entity_type,
            dict(source),
            sync_run_id=sync_run_id,
        )
        external_id = normalized["external_id"]
        if external_id in normalized_by_id:
            raise ValueError(f"duplicate Snapchat {entity_type}: {external_id}")
        normalized_by_id[external_id] = normalized
        if len(normalized_by_id) > MAX_ENTITY_ROWS:
            raise ValueError("Snapchat entity write exceeded the safety limit")

    collection = db[SNAPCHAT_ENTITY_FACTS_COLLECTION]
    for external_id, normalized in normalized_by_id.items():
        await collection.update_one(
            {
                "user_id": str(user_id),
                "provider": SNAPCHAT_PROVIDER,
                "ad_account_id": str(ad_account_id),
                "entity_type": entity_type,
                "external_id": external_id,
            },
            {
                "$set": {
                    **normalized,
                    "missing_from_latest_sync": False,
                    "last_seen_at": current,
                    "updated_at": current,
                },
                "$setOnInsert": {"created_at": current},
            },
            upsert=True,
        )

    missing_query: dict[str, Any] = {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": str(ad_account_id),
        "entity_type": entity_type,
        "active": True,
    }
    if normalized_by_id:
        missing_query["external_id"] = {"$nin": sorted(normalized_by_id)}
    stale = await collection.update_many(
        missing_query,
        {
            "$set": {
                "active": False,
                "missing_from_latest_sync": True,
                "stale_at": current,
                "updated_at": current,
            }
        },
    )
    return {
        "rows_received": len(normalized_by_id),
        "rows_saved": len(normalized_by_id),
        "rows_staled": int(getattr(stale, "modified_count", 0) or 0),
    }


async def list_entities(
    db: Any,
    *,
    user_id: str,
    ad_account_id: str,
    entity_type: str,
    active_only: bool = True,
    limit: int = 10_000,
) -> list[dict[str, Any]]:
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"Unsupported Snapchat entity type: {entity_type}")
    query: dict[str, Any] = {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": str(ad_account_id),
        "entity_type": entity_type,
    }
    if active_only:
        query["active"] = True
    cursor = db[SNAPCHAT_ENTITY_FACTS_COLLECTION].find(query, {"_id": 0})
    if hasattr(cursor, "sort"):
        cursor = cursor.sort([("name", 1), ("external_id", 1)])
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit + 1)
    if hasattr(cursor, "to_list"):
        try:
            rows = list(await cursor.to_list(length=limit + 1))
        except TypeError:
            rows = list(await cursor.to_list(limit + 1))
    else:
        rows = []
        async for row in cursor:
            rows.append(row)
            if len(rows) > limit:
                break
    if len(rows) > limit:
        raise ValueError("Snapchat entity read was truncated")
    return rows


__all__ = [
    "ENTITY_TYPES",
    "SNAPCHAT_ENTITY_FACTS_COLLECTION",
    "list_entities",
    "sync_entities",
]
