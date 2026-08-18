"""One-shot Campaign AI worker executed outside the FastAPI web process.

The web application intentionally does not run provider/OpenAI analysis in its
own event loop.  This entrypoint is launched as a short-lived child process by
the lightweight scheduler in ``campaign_ai_subprocess_scheduler``.  The Mongo
lease inside ``run_all_campaign_ai_monitors`` remains the authority that
prevents duplicate analysis when multiple web replicas launch a worker at the
same time.
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
        started_at = datetime.now(timezone.utc).isoformat()
        summary = await run_all_campaign_ai_monitors(db)

        # A provider run can complete technically while OpenAI itself is
        # unavailable.  Treat that as retryable so the scheduler uses its short
        # retry window instead of waiting the full five-hour decision interval.
        retryable_ai_runs = await db[RUN_COLLECTION].count_documents({
            "started_at": {"$gte": started_at},
            "recommendation_source": {
                "$in": ["openai_unavailable", "mezan_fallback"],
            },
        })
        return {
            **summary,
            "retryable_ai_runs": int(retryable_ai_runs),
        }
    finally:
        client.close()


async def _main() -> int:
    summary = await run_once()
    # Summary is operational metadata only; it contains no provider tokens or
    # credentials and is safe for process logs.
    print(json.dumps(summary, ensure_ascii=False, default=str))
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
        # request details.  Persisted Campaign AI run documents already keep
        # sanitized error codes, so process logs record only the exception type.
        logger.error("Campaign AI isolated worker failed: %s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
