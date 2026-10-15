"""Snapchat V2 account registry and primary-account selection."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from .models import SNAPCHAT_PROVIDER, clean_text

SNAPCHAT_ACCOUNTS_COLLECTION = "mezan_snapchat_accounts_v2"
LEGACY_INTEGRATION_ACCOUNTS_COLLECTION = "mezan_integration_accounts_v2"
MAX_ACCOUNTS = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _to_list(cursor: Any, *, limit: int = MAX_ACCOUNTS) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        try:
            return list(await cursor.to_list(length=limit))
        except TypeError:
            return list(await cursor.to_list(limit))
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _normalize_permissions(value: Any) -> list[str]:
    if isinstance(value, str):
        values: Iterable[Any] = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = []
    return sorted({clean_text(item, limit=120) for item in values if clean_text(item, limit=120)})


def normalize_account(user_id: str, account: dict[str, Any]) -> dict[str, Any]:
    account_id = clean_text(
        account.get("ad_account_id")
        or account.get("external_account_id")
        or account.get("id"),
        limit=128,
    )
    if not account_id:
        raise ValueError("Snapchat account is missing ad_account_id")
    display_name = clean_text(
        account.get("display_name") or account.get("name") or account_id,
        limit=240,
    )
    currency = clean_text(account.get("currency"), limit=12).upper()
    timezone_name = clean_text(
        account.get("timezone") or account.get("account_timezone"),
        limit=80,
    )
    permissions = _normalize_permissions(
        account.get("permissions") or account.get("scope")
    )
    return {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": account_id,
        "external_account_id": account_id,
        "display_name": display_name,
        "currency": currency or None,
        "timezone": timezone_name or None,
        "permissions": permissions,
        "organization_id": clean_text(account.get("organization_id"), limit=128)
        or None,
        "organization_name": clean_text(account.get("organization_name"), limit=240)
        or None,
        "account_status": clean_text(account.get("account_status") or account.get("status"), limit=64)
        or None,
        "connection_status": clean_text(account.get("connection_status") or "connected", limit=64),
        "active": account.get("active") is not False,
        "selected_hint": bool(
            account.get("selected") is True
            or account.get("mezan_selected") is True
            or account.get("is_primary") is True
        ),
    }


async def ensure_account_indexes(db: Any) -> None:
    collection = db[SNAPCHAT_ACCOUNTS_COLLECTION]
    await collection.create_index(
        [("user_id", 1), ("provider", 1), ("ad_account_id", 1)],
        unique=True,
        name="snapchat_v2_account_identity_unique",
    )
    await collection.create_index(
        [("user_id", 1), ("provider", 1)],
        unique=True,
        partialFilterExpression={"selected": True, "active": True},
        name="snapchat_v2_one_selected_account",
    )
    await collection.create_index(
        [("user_id", 1), ("active", 1), ("display_name", 1)],
        name="snapchat_v2_accounts_active_name",
    )


async def list_accounts(
    db: Any,
    user_id: str,
    *,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
    }
    if active_only:
        query["active"] = True
    cursor = db[SNAPCHAT_ACCOUNTS_COLLECTION].find(query, {"_id": 0})
    if hasattr(cursor, "sort"):
        cursor = cursor.sort([("selected", -1), ("display_name", 1)])
    return await _to_list(cursor)


async def get_selected_account(db: Any, user_id: str) -> dict[str, Any] | None:
    return await db[SNAPCHAT_ACCOUNTS_COLLECTION].find_one(
        {
            "user_id": str(user_id),
            "provider": SNAPCHAT_PROVIDER,
            "selected": True,
            "active": True,
        },
        {"_id": 0},
    )


async def select_account(db: Any, user_id: str, ad_account_id: str) -> dict[str, Any]:
    user_id = str(user_id)
    ad_account_id = clean_text(ad_account_id, limit=128)
    if not ad_account_id:
        raise ValueError("ad_account_id is required")
    collection = db[SNAPCHAT_ACCOUNTS_COLLECTION]
    account = await collection.find_one(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER,
            "ad_account_id": ad_account_id,
            "active": True,
        },
        {"_id": 0},
    )
    if not account:
        raise LookupError("snapchat_account_not_found")
    now = _utcnow()
    await collection.update_many(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER,
            "selected": True,
            "ad_account_id": {"$ne": ad_account_id},
        },
        {"$set": {"selected": False, "updated_at": now}},
    )
    result = await collection.update_one(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER,
            "ad_account_id": ad_account_id,
            "active": True,
        },
        {"$set": {"selected": True, "selected_at": now, "updated_at": now}},
    )
    if int(getattr(result, "matched_count", 0) or 0) != 1:
        raise LookupError("snapchat_account_not_found")
    account.update({"selected": True, "selected_at": now, "updated_at": now})
    return account


async def sync_accounts(
    db: Any,
    user_id: str,
    accounts: Iterable[dict[str, Any]],
    *,
    selected_account_id: str | None = None,
) -> list[dict[str, Any]]:
    await ensure_account_indexes(db)
    user_id = str(user_id)
    collection = db[SNAPCHAT_ACCOUNTS_COLLECTION]
    existing_selected = await get_selected_account(db, user_id)
    normalized_by_id: dict[str, dict[str, Any]] = {}
    hinted: list[str] = []
    for account in accounts:
        normalized = normalize_account(user_id, dict(account))
        account_id = normalized["ad_account_id"]
        if account_id in normalized_by_id:
            raise ValueError(f"duplicate Snapchat account: {account_id}")
        normalized_by_id[account_id] = normalized
        if normalized.pop("selected_hint", False):
            hinted.append(account_id)
        if len(normalized_by_id) > MAX_ACCOUNTS:
            raise ValueError("Snapchat account discovery exceeded the safe limit")

    now = _utcnow()
    account_ids = sorted(normalized_by_id)
    for account_id in account_ids:
        normalized = normalized_by_id[account_id]
        await collection.update_one(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER,
                "ad_account_id": account_id,
            },
            {
                "$set": {
                    **normalized,
                    "active": True,
                    "missing_from_last_discovery": False,
                    "last_seen_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "selected": False,
                    "created_at": now,
                },
            },
            upsert=True,
        )

    missing_query: dict[str, Any] = {
        "user_id": user_id,
        "provider": SNAPCHAT_PROVIDER,
        "active": True,
    }
    if account_ids:
        missing_query["ad_account_id"] = {"$nin": account_ids}
    await collection.update_many(
        missing_query,
        {
            "$set": {
                "active": False,
                "selected": False,
                "missing_from_last_discovery": True,
                "connection_status": "stale",
                "updated_at": now,
            }
        },
    )

    preferred = clean_text(selected_account_id, limit=128)
    if preferred not in normalized_by_id:
        current_id = clean_text((existing_selected or {}).get("ad_account_id"), limit=128)
        preferred = current_id if current_id in normalized_by_id else ""
    if not preferred:
        preferred = next((item for item in hinted if item in normalized_by_id), "")
    if not preferred and account_ids:
        preferred = account_ids[0]
    if preferred:
        await select_account(db, user_id, preferred)
    return await list_accounts(db, user_id, active_only=False)


async def import_existing_accounts(db: Any, user_id: str) -> list[dict[str, Any]]:
    cursor = db[LEGACY_INTEGRATION_ACCOUNTS_COLLECTION].find(
        {
            "user_id": str(user_id),
            "provider": SNAPCHAT_PROVIDER,
            "connection_status": "connected",
        },
        {"_id": 0},
    )
    rows = await _to_list(cursor)
    return await sync_accounts(db, str(user_id), rows)
