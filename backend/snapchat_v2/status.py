from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


SYNC_RUNS = "mezan_snapchat_sync_runs_v2"
LEASES = "mezan_snapchat_leases_v2"


async def snapchat_v2_status(
    db: Any,
    user_id: str,
    ad_account_id: str,
) -> dict[str, Any]:
    run = await db[SYNC_RUNS].find_one(
        {
            "user_id": user_id,
            "provider": "snapchat_ads",
            "ad_account_id": ad_account_id,
        },
        sort=[("updated_at", -1)],
        projection={"_id": 0},
    )

    lease = await db[LEASES].find_one(
        {
            "user_id": user_id,
            "provider": "snapchat_ads",
            "ad_account_id": ad_account_id,
        },
        projection={"_id": 0},
    )

    now = datetime.now(timezone.utc)

    return {
        "provider": "snapchat_ads",
        "account_id": ad_account_id,
        "last_run": run,
        "lease": lease,
        "checked_at": now,
    }

