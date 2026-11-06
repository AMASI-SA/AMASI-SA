"""Dedicated process for the existing automatic jobs, with parent-death cleanup."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import os
import signal
import sys
import threading


async def parent_closed():
    # A real anonymous pipe, not a file path, is owned by the web parent.
    # A daemon reader does not keep asyncio.run's executor alive on SIGTERM.
    closed = asyncio.Event()
    loop = asyncio.get_running_loop()
    def read_pipe():
        sys.stdin.buffer.read()
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(closed.set)
    threading.Thread(target=read_pipe, daemon=True).start()
    await closed.wait()


async def heartbeat(db, tasks):
    from integrations.qoyod.worker import liveness
    parent_id = os.environ['MEZAN_BACKGROUND_PARENT_ID']
    while True:
        pipeline = liveness()
        await db.background_worker_status.update_one({'_id': parent_id}, [{'$set': {
            'updated_at': '$$NOW', 'expires_at': {'$add': ['$$NOW', 45000]},
            'jobs': {task.get_name(): not task.done() for task in tasks},
            'qoyod_pipeline': {key: pipeline.get(key) for key in
                               ('running', 'last_run_at', 'last_run_ok')},
        }}], upsert=True)
        await asyncio.sleep(10)


async def run():
    if os.environ.get('MEZAN_BACKGROUND_WORKER_PROCESS') != '1':
        raise RuntimeError('background_worker_requires_parent')
    # Compose the same canonical modules and safety installers as the web.
    # Importing the ASGI application does not execute its startup callbacks.
    import server
    from background_worker_runtime import start_tasks, stop_tasks
    from boot_runtime import process_local_readiness_event
    await server.install_process_local_auth_security(server.db)
    await server.db.background_worker_status.create_index('expires_at', expireAfterSeconds=0)
    process_local_readiness_event.set()
    tasks = []
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    monitors = []
    try:
        tasks = await start_tasks(server.db)
        monitors = [asyncio.create_task(parent_closed()), asyncio.create_task(stop.wait()),
                    asyncio.create_task(heartbeat(server.db, tasks))]
        done, _ = await asyncio.wait([*tasks, *monitors], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                raise RuntimeError('background_job_failed') from task.exception()
    finally:
        process_local_readiness_event.clear()
        await stop_tasks(tasks)
        for task in monitors:
            task.cancel()
        await asyncio.gather(*monitors, return_exceptions=True)
        server.client.close()


if __name__ == '__main__':
    try:
        asyncio.run(run())
    except Exception:
        # Parent logs only the exit code; never emit raw provider exceptions.
        raise SystemExit(1) from None
