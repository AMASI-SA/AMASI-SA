"""Pipe-owned independent process for explicitly opted-in operational owners."""
import asyncio
from contextlib import suppress
import logging
import os
import signal
import stat
import sys
import threading


def lower_cpu_priority():
    """Only lower this child; unsupported/denied hosts retain a visible flag."""
    if not hasattr(os, 'nice'):
        logging.getLogger(__name__).warning('Operational automation CPU priority unsupported')
        return False
    try:
        os.nice(5)
        return True
    except OSError:
        logging.getLogger(__name__).warning('Operational automation CPU priority unavailable')
        return False


async def parent_closed():
    """Observe the real pipe without retaining a buffered-stdin shutdown lock."""
    fd = sys.stdin.fileno()
    if not stat.S_ISFIFO(os.fstat(fd).st_mode):
        raise RuntimeError('operational_automation_requires_parent_pipe')
    closed, loop = asyncio.Event(), asyncio.get_running_loop()

    def read_pipe():
        try:
            while os.read(fd, 32):
                pass
        except OSError:
            pass
        finally:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(closed.set)
    # os.read holds no Python BufferedReader lock. A pending daemon read cannot
    # make SIGTERM cleanup abort Python during finalization of sys.stdin.
    threading.Thread(target=read_pipe, daemon=True).start()
    await closed.wait()


async def run():
    if os.environ.get('MEZAN_BACKGROUND_WORKER_PROCESS') != '1':
        raise RuntimeError('operational_automation_requires_parent')
    priority_lowered = lower_cpu_priority()
    # Compose the identical guarded DB/adapters. ASGI startup is not invoked;
    # this child starts only the explicit opt-in scheduler, no other workers.
    import server
    from boot_runtime import process_local_readiness_event
    from operational_balance_automation import run as automate
    await server.install_process_local_auth_security(server.db)
    process_local_readiness_event.set()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    job = asyncio.create_task(automate(server.db, stop_event=stop, cpu_priority_lowered=priority_lowered),
                              name='operational-owner-automation')
    monitors = [asyncio.create_task(parent_closed(), name='automation-parent-closed'),
                asyncio.create_task(stop.wait(), name='automation-stop-signal')]
    try:
        done, _ = await asyncio.wait([job, *monitors], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                raise RuntimeError('operational_automation_failed') from task.exception()
    finally:
        # First prevent further admission, then join owned reads/calculations
        # and any admitted CAS before the canonical client is closed.
        stop.set()
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        for monitor in monitors:
            monitor.cancel()
        await asyncio.gather(*monitors, return_exceptions=True)
        process_local_readiness_event.clear()
        server.client.close()


if __name__ == '__main__':
    try:
        asyncio.run(run())
    except Exception:
        raise SystemExit(1) from None
