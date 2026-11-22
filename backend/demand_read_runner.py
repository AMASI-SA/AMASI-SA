"""Fixed read-projection child entry; no ASGI app, providers or schedulers."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import threading


async def execute(key, token, generation):
    from dotenv import load_dotenv
    from motor.motor_asyncio import AsyncIOMotorClient
    from mongo_observability import mongo_metrics
    from review_acceptance_config_guard import AcceptanceConfigDatabase
    from runtime_mongo import main_client_options
    from demand_read_jobs import execute_claim
    load_dotenv(Path(__file__).resolve().parent / ".env")
    client = AsyncIOMotorClient(os.environ["MONGO_URL"],
        **main_client_options(event_listener=mongo_metrics), w="majority", journal=True)
    try:
        return await execute_claim(AcceptanceConfigDatabase(client[os.environ["DB_NAME"]]), key, token, generation)
    finally:
        client.close()


def watch_parent_pipe():
    def parent_closed():
        sys.stdin.buffer.read()
        os._exit(2)
    threading.Thread(target=parent_closed, daemon=True).start()


if __name__ == "__main__":
    if len(sys.argv) != 4 or os.environ.get("MEZAN_BACKGROUND_WORKER_PROCESS") != "1":
        raise SystemExit(2)
    # The supervisor owns this anonymous pipe. Its crash or graceful shutdown
    # cannot leave an orphan CPU child behind, even during synchronous work.
    watch_parent_pipe()
    try:
        raise SystemExit(0 if asyncio.run(execute(*sys.argv[1:])) else 1)
    except Exception:
        raise SystemExit(1) from None
