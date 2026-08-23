from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

SNAPCHAT_LEASE_COLLECTION = "mezan_snapchat_leases_v2"

def lease_key(user_id: str, ad_account_id: str) -> dict[str, str]:
    return {
        "user_id": user_id,
        "provider": "snapchat_ads",
        "ad_account_id": ad_account_id,
    }

async def acquire_lease(
    db: Any,
    user_id: str,
    ad_account_id: str,
    owner_id: str,
    ttl_minutes: int = 15,
) -> bool:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=ttl_minutes)

    result = await db[SNAPCHAT_LEASE_COLLECTION].update_one(
        {
            **lease_key(user_id, ad_account_id),
            "$or": [
                {"expires_at": {"$lte": now}},
                {"owner_id": owner_id},
            ],
        },
        {
            "$set": {
                "owner_id": owner_id,
                "heartbeat_at": now,
                "expires_at": expires,
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )

    return result.modified_count > 0 or result.upserted_id is not None

async def release_lease(
    db: Any,
    user_id: str,
    ad_account_id: str,
    owner_id: str,
) -> None:
    await db[SNAPCHAT_LEASE_COLLECTION].delete_one(
        {
            **lease_key(user_id, ad_account_id),
            "owner_id": owner_id,
        }
    )
