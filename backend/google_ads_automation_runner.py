"""One-shot, bounded Google automation child; no web application import.

Only an explicit worker flag permits database bootstrap. All native reads,
reasoning and conditional execution stay in this child, with the existing
policy/worker/authority modules responsible for account claims and permissions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
CYCLE_TIMEOUT_SECONDS = 450.0
logger = logging.getLogger(__name__)


def worker_enabled() -> bool:
    return os.getenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _load_runtime():
    # Match the existing isolated Campaign AI bootstrap without importing
    # server.py or its startup handlers. Never create or replace credentials.
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")
    from motor.motor_asyncio import AsyncIOMotorClient
    from integrations_control_center.google_ads_automation_worker import run_google_automation_cycle
    return AsyncIOMotorClient, run_google_automation_cycle


async def run_once() -> dict:
    if not worker_enabled():
        return {"status": "disabled", "accounts": 0}
    client_factory, run_cycle = _load_runtime()
    mongo_url = (os.getenv("MONGO_URL") or "").strip()
    database_name = (os.getenv("DB_NAME") or "").strip()
    if not mongo_url or not database_name:
        raise RuntimeError("google_automation_database_config_missing")
    client = client_factory(mongo_url, serverSelectionTimeoutMS=15000,
        connectTimeoutMS=10000, socketTimeoutMS=30000)
    try:
        # The 30-second margin below the parent deadline lets cancellation
        # preserve execution fences and close the database before termination.
        return await asyncio.wait_for(run_cycle(client[database_name]), timeout=CYCLE_TIMEOUT_SECONDS)
    finally:
        client.close()


async def _main() -> int:
    summary = await run_once()
    status = summary.get("status") if isinstance(summary, dict) else None
    if status not in {"disabled", "complete", "recovery_only"}:
        status = "failed"
    count = summary.get("accounts") if isinstance(summary, dict) else 0
    count = count if type(count) is int and 0 <= count <= 4 else 0
    # No account IDs, proposal content, provider bodies or model output.
    print(json.dumps({"status": status, "accounts": count}, separators=(",", ":")))
    rows = summary.get("results") if isinstance(summary, dict) else None
    failed = isinstance(rows, list) and any(isinstance(row, dict) and row.get("status") == "failed" for row in rows)
    return 1 if status == "failed" or failed else 0


def main() -> int:
    try:
        return asyncio.run(_main())
    except asyncio.TimeoutError:
        logger.warning("google_automation_child_timeout")
        return 124
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except Exception:
        logger.error("google_automation_child_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
