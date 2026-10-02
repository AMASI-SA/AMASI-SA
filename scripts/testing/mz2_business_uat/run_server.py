"""Run the isolated fixture with a task-owned graceful stop marker."""
import asyncio
import os
from pathlib import Path
import sys

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


async def main():
    stop = Path(os.environ["MZ2_C5_EVIDENCE"]) / "stop-server"
    if stop.exists():
        raise RuntimeError("Refusing reused stopped fixture directory")
    server = uvicorn.Server(uvicorn.Config("scripts.testing.mz2_business_uat.server:app",
        host="127.0.0.1", port=int(os.environ.get("MZ2_C5_PORT", "18771")), log_level="info", access_log=False))
    async def watch():
        while not server.should_exit:
            if stop.exists():
                server.should_exit = True
                return
            await asyncio.sleep(0.2)
    watcher = asyncio.create_task(watch())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
