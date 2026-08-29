"""Read-only health and diagnostics for Snapchat Integration V2."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .accounts import get_selected_account
from .connection import SNAPCHAT_CONNECTIONS_COLLECTION
from .facts import SNAPCHAT_HOURLY_FACTS_COLLECTION
from .lease import SNAPCHAT_LEASE_COLLECTION, get_lease_status
from .models import SNAPCHAT_PROVIDER
from .read_timing import gather_cancel_on_error, timed_awaitable
from .sync_runs import LEVEL_STATUS_FIELDS, SNAPCHAT_SYNC_RUNS_COLLECTION


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        current = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            current = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


async def _run_snapshot(
    db: Any,
    query: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any] | None]]:
    success_projection = {
        "_id": 0,
        "sync_run_id": 1,
        "finished_at": 1,
        "started_at": 1,
    }
    facets: dict[str, list[dict[str, Any]]] = {
        "latest": [
            {"$limit": 1},
            {"$project": {"_id": 0}},
        ],
    }
    for level, field in LEVEL_STATUS_FIELDS.items():
        facets[level] = [
            {"$match": {field: "complete"}},
            {"$limit": 1},
            {"$project": success_projection},
        ]
    cursor = db[SNAPCHAT_SYNC_RUNS_COLLECTION].aggregate(
        [
            {"$match": query},
            {"$sort": {"started_at": -1}},
            {"$facet": facets},
        ]
    )
    rows = await cursor.to_list(length=1)
    snapshot = rows[0] if rows else {}
    latest_rows = snapshot.get("latest") or []
    last_success = {
        level: ((snapshot.get(level) or [None])[0])
        for level in LEVEL_STATUS_FIELDS
    }
    return (latest_rows[0] if latest_rows else None), last_success


async def snapchat_v2_status(
    db: Any,
    user_id: str,
    ad_account_id: str | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or _utcnow()).astimezone(timezone.utc)
    user_id = str(user_id)
    selected, connection_row = await gather_cancel_on_error(
        timed_awaitable(
            "db-selected-account",
            get_selected_account(db, user_id),
        ),
        timed_awaitable(
            "db-connection",
            db[SNAPCHAT_CONNECTIONS_COLLECTION].find_one(
                {"user_id": user_id, "provider": SNAPCHAT_PROVIDER},
                {"_id": 0},
            ),
        ),
    )
    account_id = str(ad_account_id or (selected or {}).get("ad_account_id") or "").strip()

    connection = connection_row or {}
    if not account_id:
        return {
            "provider": SNAPCHAT_PROVIDER,
            "connection": connection,
            "selected_account": selected,
            "account_id": None,
            "status": "not_ready",
            "reason": "selected_account_missing",
            "checked_at": current,
        }

    base_query = {
        "user_id": user_id,
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": account_id,
    }
    (latest, last_success), lease, latest_fact = await gather_cancel_on_error(
        timed_awaitable(
            "db-run-snapshot",
            _run_snapshot(db, base_query),
        ),
        timed_awaitable(
            "db-lease",
            get_lease_status(db, user_id, account_id),
        ),
        timed_awaitable(
            "db-latest-fact",
            db[SNAPCHAT_HOURLY_FACTS_COLLECTION].find_one(
                base_query,
                {"_id": 0, "updated_at": 1, "hour_end_utc": 1, "sync_run_id": 1},
                sort=[("updated_at", -1)],
            ),
        ),
    )
    fact_updated = _as_utc((latest_fact or {}).get("updated_at"))
    data_age_seconds = (
        max(int((current - fact_updated).total_seconds()), 0)
        if fact_updated
        else None
    )
    lease_expiry = _as_utc((lease or {}).get("expires_at"))
    lock_held = bool(
        lease
        and lease.get("status") == "held"
        and lease_expiry
        and lease_expiry > current
    )
    latest_error = (latest or {}).get("last_error")
    financial_ok = bool(last_success.get("financial"))
    status = (
        "healthy"
        if financial_ok and not latest_error
        else "degraded"
        if financial_ok
        else "not_ready"
    )

    return {
        "provider": SNAPCHAT_PROVIDER,
        "account_id": account_id,
        "selected_account": selected,
        "connection": connection,
        "status": status,
        "financial_sync_status": (latest or {}).get("financial_sync_status"),
        "campaign_sync_status": (latest or {}).get("campaign_sync_status"),
        "ad_squad_sync_status": (latest or {}).get("ad_squad_sync_status"),
        "ad_sync_status": (latest or {}).get("ad_sync_status"),
        "identity_sync_status": (latest or {}).get("identity_sync_status"),
        "last_run": latest,
        "last_success": last_success,
        "last_error": latest_error,
        "lock": {
            "held": lock_held,
            "status": (lease or {}).get("status"),
            "owner_id": (lease or {}).get("owner_id") if lock_held else None,
            "heartbeat_at": (lease or {}).get("heartbeat_at"),
            "expires_at": (lease or {}).get("expires_at"),
        },
        "data": {
            "latest_fact_updated_at": fact_updated,
            "latest_fact_hour_end_utc": (latest_fact or {}).get("hour_end_utc"),
            "latest_fact_sync_run_id": (latest_fact or {}).get("sync_run_id"),
            "age_seconds": data_age_seconds,
        },
        "next_due_at": connection.get("next_due_at"),
        "checked_at": current,
    }


__all__ = [
    "SNAPCHAT_CONNECTIONS_COLLECTION",
    "SNAPCHAT_HOURLY_FACTS_COLLECTION",
    "SNAPCHAT_LEASE_COLLECTION",
    "SNAPCHAT_SYNC_RUNS_COLLECTION",
    "snapchat_v2_status",
]
