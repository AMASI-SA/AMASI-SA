"""One-shot Campaign AI worker executed outside the FastAPI web process.

The web application intentionally does not run provider/OpenAI analysis in its
own event loop. This entrypoint is launched as a short-lived child process by
the lightweight scheduler in ``campaign_ai_subprocess_scheduler``.

A Mongo-backed global cadence gate is claimed before any heavy provider/OpenAI
work. This is deliberately stronger than the historical short concurrency
lease: with multiple web replicas, a released concurrency lock allowed replica
B and C to run sequentially minutes after replica A and publish different
snapshots. The global gate makes one decision cycle every five hours a
production invariant, with the short retry window reserved for real failures.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

from campaign_ai_global_cadence import claim_global_cycle, finish_global_cycle


ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

logger = logging.getLogger("campaign_ai_worker_runner")


async def run_once() -> dict[str, Any]:
    mongo_url = (os.environ.get("MONGO_URL") or "").strip()
    db_name = (os.environ.get("DB_NAME") or "").strip()
    if not mongo_url or not db_name:
        raise RuntimeError("campaign_ai_worker_database_config_missing")

    # Import the heavy Campaign AI/provider graph only in this child process.
    from campaign_ai_monitor import (
        RUN_COLLECTION,
        ensure_campaign_ai_indexes,
        run_all_campaign_ai_monitors,
    )

    client = AsyncIOMotorClient(mongo_url)
    try:
        db = client[db_name]
        await ensure_campaign_ai_indexes(db)

        cadence = await claim_global_cycle(db)
        if not cadence.get("claimed"):
            return {
                "users": 0,
                "completed": 0,
                "failed": 0,
                "cadence_skipped": True,
                "cadence_skip_reason": cadence.get("skip_reason"),
                "next_run_at": cadence.get("next_run_at"),
                "lease_until": cadence.get("lease_until"),
            }

        owner = str(cadence["owner"])
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            summary = await run_all_campaign_ai_monitors(db)

            # A provider run can complete technically while OpenAI itself is
            # unavailable. Treat that as retryable so only the replica that won
            # the global cycle retries after the short window. Other replicas
            # remain blocked by next_run_at and cannot publish competing snapshots.
            retryable_ai_runs = await db[RUN_COLLECTION].count_documents({
                "started_at": {"$gte": started_at},
                "recommendation_source": {
                    "$in": ["openai_unavailable", "mezan_fallback"],
                },
            })
            failed = int(summary.get("failed") or 0)
            legacy_lease_collision = summary.get("skipped") == "lease_held"
            retryable = bool(
                failed > 0
                or int(retryable_ai_runs) > 0
                or legacy_lease_collision
            )
            outcome = (
                "legacy_lease_collision"
                if legacy_lease_collision
                else "retryable_ai_failure"
                if int(retryable_ai_runs) > 0
                else "worker_failure"
                if failed > 0
                else "success"
            )
            finished = await finish_global_cycle(
                db,
                owner,
                retryable=retryable,
                outcome=outcome,
            )
            return {
                **summary,
                "retryable_ai_runs": int(retryable_ai_runs),
                "cadence_claimed": True,
                "global_cycle_outcome": outcome,
                "next_run_at": finished.get("next_run_at"),
            }
        except Exception:
            await finish_global_cycle(
                db,
                owner,
                retryable=True,
                outcome="worker_exception",
            )
            raise
    finally:
        client.close()


async def _main() -> int:
    summary = await run_once()
    # Summary is operational metadata only; it contains no provider tokens or
    # credentials and is safe for process logs.
    print(json.dumps(summary, ensure_ascii=False, default=str))

    # Code 3 means the Mongo cadence gate intentionally prevented heavy work.
    # The lightweight scheduler will re-check the gate in a few minutes. This
    # is not an OpenAI failure and it must never generate another snapshot.
    if summary.get("cadence_skip_reason") in {"running_elsewhere", "not_due"}:
        return 3
    if int(summary.get("failed") or 0) > 0:
        return 1
    if int(summary.get("retryable_ai_runs") or 0) > 0:
        return 2
    return 0


def main() -> int:
    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # Provider/OpenAI exception text can contain credential fragments or
        # request details. Persisted Campaign AI run documents already keep
        # sanitized error codes, so process logs record only the exception type.
        logger.error("Campaign AI isolated worker failed: %s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
