from __future__ import annotations

from datetime import datetime
from typing import Any


SNAPCHAT_HOURLY_FACTS_COLLECTION = "mezan_snapchat_hourly_facts_v2"


def hourly_fact_identity(
    fact: dict[str, Any],
) -> dict[str, Any]:
    return {
        "user_id": fact["user_id"],
        "provider": "snapchat_ads",
        "ad_account_id": fact["ad_account_id"],
        "campaign_id": fact.get("campaign_id"),
        "ad_squad_id": fact.get("ad_squad_id"),
        "ad_id": fact.get("ad_id"),
        "hour_start_utc": fact["hour_start_utc"],
    }


async def upsert_hourly_fact(
    db: Any,
    fact: dict[str, Any],
) -> None:
    identity = hourly_fact_identity(fact)
    now = datetime.utcnow()

    await db[SNAPCHAT_HOURLY_FACTS_COLLECTION].update_one(
        identity,
        {
            "$set": {
                **fact,
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )
