from __future__ import annotations

from typing import Any
from uuid import uuid4

from .facts import upsert_hourly_fact
from .lease import acquire_lease, release_lease
from .sync_runs import create_sync_run, new_sync_run, update_sync_stage


class SnapchatV2SyncPipeline:
    def __init__(self, db: Any):
        self.db = db

    async def run(
        self,
        user_id: str,
        ad_account_id: str,
    ) -> dict[str, Any]:
        owner_id = str(uuid4())

        acquired = await acquire_lease(
            self.db,
            user_id,
            ad_account_id,
            owner_id,
        )

        if not acquired:
            return {
                "status": "skipped",
                "reason": "lease_unavailable",
            }

        run = new_sync_run(
            user_id,
            ad_account_id,
        )

        await create_sync_run(
            self.db,
            run,
        )

        try:
            await update_sync_stage(
                self.db,
                run["sync_run_id"],
                "connection_validation",
            )

            await update_sync_stage(
                self.db,
                run["sync_run_id"],
                "facts_sync",
            )

            await update_sync_stage(
                self.db,
                run["sync_run_id"],
                "completed",
                "success",
            )

            return {
                "status": "success",
                "sync_run_id": run["sync_run_id"],
            }

        finally:
            await release_lease(
                self.db,
                user_id,
                ad_account_id,
                owner_id,
            )

