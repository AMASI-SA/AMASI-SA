"""Dedicated process for the existing automatic jobs, with parent-death cleanup."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import os
import signal
import sys
import threading


async def parent_closed(wake_event, assembly_wake_event=None):
    # A real anonymous pipe, not a file path, is owned by the web parent.
    # A daemon reader does not keep asyncio.run's executor alive on SIGTERM.
    closed = asyncio.Event()
    loop = asyncio.get_running_loop()
    def read_pipe():
        while True:
            line = sys.stdin.buffer.readline(32)
            if not line:
                break
            if line == b'wake\n':
                with suppress(RuntimeError):
                    loop.call_soon_threadsafe(wake_event.set)
            elif line == b'assembly\n' and assembly_wake_event is not None:
                with suppress(RuntimeError):
                    loop.call_soon_threadsafe(assembly_wake_event.set)
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


async def record_worker_exit(db, done, jobs, monitors=()):
    """Best-effort bounded cause record, without provider messages or payloads."""
    def summarize(tasks):
        outcomes = []
        for task in tasks:
            if task not in done:
                continue
            outcome = {'job': task.get_name()[:80]}
            if task.cancelled():
                outcome['outcome'] = 'cancelled'
            elif task.exception() is not None:
                outcome.update(outcome='failed', error_type=type(task.exception()).__name__)
            else:
                outcome['outcome'] = 'returned'
            outcomes.append(outcome)
        return outcomes
    # Diagnostics must never prevent the normal shutdown/restart path.
    with suppress(Exception):
        await asyncio.wait_for(db.background_worker_status.update_one(
            {'_id': os.environ['MEZAN_BACKGROUND_PARENT_ID']},
            [{'$set': {'last_exit': {'jobs': summarize(jobs), 'monitors': summarize(monitors)},
                       'last_exit_at': '$$NOW'}}],
            upsert=True,
        ), timeout=2)


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
        wake_event = asyncio.Event()
        assembly_wake_event = asyncio.Event()
        tasks = await start_tasks(server.db, wake_event=wake_event, assembly_wake_event=assembly_wake_event)
        monitors = [asyncio.create_task(parent_closed(wake_event, assembly_wake_event), name='parent-closed'),
                    asyncio.create_task(stop.wait(), name='stop-signal'),
                    asyncio.create_task(heartbeat(server.db, tasks), name='heartbeat')]
        done, _ = await asyncio.wait([*tasks, *monitors], return_when=asyncio.FIRST_COMPLETED)
        await record_worker_exit(server.db, done, tasks, monitors)
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
