"""Fixed read-projection child entry; no ASGI app, providers or schedulers."""
from __future__ import annotations

import time
_RUNNER_ENTERED = time.monotonic()

import asyncio
import os
from pathlib import Path
import sys
import threading


async def execute(key, token, generation, startup_started=None):
    from dotenv import load_dotenv
    from motor.motor_asyncio import AsyncIOMotorClient
    from mongo_observability import mongo_metrics
    from review_acceptance_config_guard import AcceptanceConfigDatabase
    from runtime_mongo import main_client_options
    from demand_read_jobs import execute_claim
    imports_ms = (time.monotonic() - _RUNNER_ENTERED) * 1000
    launch = float(startup_started) if startup_started is not None else _RUNNER_ENTERED
    load_dotenv(Path(__file__).resolve().parent / ".env")
    client = AsyncIOMotorClient(os.environ["MONGO_URL"],
        **main_client_options(event_listener=mongo_metrics), w="majority", journal=True)
    try:
        return await execute_claim(AcceptanceConfigDatabase(client[os.environ["DB_NAME"]]), key, token, generation,
                                   startup_started=launch, runner_import_ms=imports_ms)
    finally:
        client.close()


def watch_parent_pipe():
    # A buffered stdin read would hold its lock during interpreter shutdown.
    parent_fd = sys.stdin.fileno()
    def parent_closed():
        while os.read(parent_fd, 65536):
            pass
        os._exit(2)
    threading.Thread(target=parent_closed, daemon=True).start()


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5) or os.environ.get("MEZAN_BACKGROUND_WORKER_PROCESS") != "1":
        raise SystemExit(2)
    # The supervisor owns this anonymous pipe. Its crash or graceful shutdown
    # cannot leave an orphan CPU child behind, even during synchronous work.
    watch_parent_pipe()
    try:
        raise SystemExit(0 if asyncio.run(execute(*sys.argv[1:])) else 1)
    except Exception:
        raise SystemExit(1) from None

