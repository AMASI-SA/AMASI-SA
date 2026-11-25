"""Own API or exact source demand-only loop; no general server/startup hooks."""
from __future__ import annotations
import asyncio
import os
import sys

from private_common import MONGO_URL, RuntimeBlocked, load_config, verify_database
from private_guard import require_installed


async def worker(config):
    from motor.motor_asyncio import AsyncIOMotorClient
    from review_acceptance_config_guard import AcceptanceConfigDatabase
    from runtime_mongo import main_client_options
    from mongo_observability import mongo_metrics
    import demand_read_jobs
    client = AsyncIOMotorClient(MONGO_URL, **main_client_options(event_listener=mongo_metrics), w="majority", journal=True)
    try:
        db, _ = await verify_database(client, config)
        await demand_read_jobs.run(AcceptanceConfigDatabase(db))
    finally:
        client.close()


if __name__ == "__main__":
    try:
        require_installed()
        config = load_config(os.environ["MEZAN_PRIVATE_RUNTIME_CONFIG"])
        mode = os.environ["MEZAN_PRIVATE_RUNTIME_MODE"]
        if sys.argv[1:] != [mode]:
            raise RuntimeBlocked("private_mode_mismatch")
        if mode == "api":
            import uvicorn
            from private_app import create_app
            uvicorn.run(create_app(config), host="127.0.0.1", port=8002, proxy_headers=False,
                        access_log=False, server_header=False, lifespan="on", log_level="warning")
        else:
            asyncio.run(worker(config))
    except RuntimeBlocked as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(78) from None
    except KeyboardInterrupt:
        pass
    except Exception:
        print("private_runtime_failed", file=sys.stderr)
        raise SystemExit(78) from None
