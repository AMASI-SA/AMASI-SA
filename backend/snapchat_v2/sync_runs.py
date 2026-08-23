from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from typing import Any


SNAPCHAT_SYNC_RUNS_COLLECTION = "mezan_snapchat_sync_runs_v2"


def new_sync_run(
    user_id: str,
    ad_account_id: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)

    return {
        "sync_run_id": str(uuid4()),
        "user_id": user_id,
        "provider": "snapchat_ads",
        "ad_account_id": ad_account_id,
        "status": "running",
        "stage": "initialized",
        "created_at": now,
        "updated_at": now,
    }


async def create_sync_run(
    db: Any,
    run: dict[str, Any],
) -> None:
    await db[SNAPCHAT_SYNC_RUNS_COLLECTION].insert_one(run)


async def update_sync_stage(
    db: Any,
    sync_run_id: str,
    stage: str,
    status: str | None = None,
) -> None:
    update: dict[str, Any] = {
        "stage": stage,
        "updated_at": datetime.now(timezone.utc),
    }

    if status:
        update["status"] = status

    await db[SNAPCHAT_SYNC_RUNS_COLLECTION].update_one(
        {"sync_run_id": sync_run_id},
        {"$set": update},
    )


async def update_sync_stage(
    db: Any,
    sync_run_id: str,
    stage: str,
    status: str | None = None,
) -> None:
    update: dict[str, Any] = {
        "stage": stage,
        "updated_at": datetime.now(timezone.utc),
    }

    if status:
        update["status"] = status

    await db[SNAPCHAT_SYNC_RUNS_COLLECTION].update_one(
        {"sync_run_id": sync_run_id},
        {"$set": update},
    )
