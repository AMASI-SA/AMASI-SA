"""One-shot isolated runner for the fast advertising product watch."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

from advertising_product_watch_v3 import scan_all_product_watch


ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")
logger = logging.getLogger("advertising_product_watch_runner_v3")


async def run_once() -> dict:
    mongo_url = (os.environ.get("MONGO_URL") or "").strip()
    db_name = (os.environ.get("DB_NAME") or "").strip()
    if not mongo_url or not db_name:
        raise RuntimeError("product_watch_database_config_missing")
    client = AsyncIOMotorClient(mongo_url)
    try:
        return await scan_all_product_watch(client[db_name])
    finally:
        client.close()


async def _main() -> int:
    result = await run_once()
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


def main() -> int:
    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.error("Advertising product watch failed: %s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
