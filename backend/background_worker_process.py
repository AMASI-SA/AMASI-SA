"""Lightweight web-owned supervision; application jobs never run in this loop."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
WORKER_FLAG = 'MEZAN_BACKGROUND_WORKER_PROCESS'
_current = None
log = logging.getLogger(__name__)


def is_worker_process():
    return os.environ.get(WORKER_FLAG) == '1'


async def _discard(stream):
    # Drain without accumulating output or publishing provider/customer data.
    while await stream.read(8192):
        pass


class ManagedWorker:
    def __init__(self, *, worker_path=None, restart_delay=15, shutdown_timeout=30):
        self.path = Path(worker_path or ROOT / 'background_worker_runner.py').resolve()
        self.parent_id = uuid.uuid4().hex
        self.restart_delay = restart_delay
        self.shutdown_timeout = shutdown_timeout
        self.process = None
        self.task = None
        self.restarts = 0

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._loop(), name='background-worker-supervisor')
        return self.task

    def status(self):
        return {'running': bool(self.task and not self.task.done() and self.process
                               and self.process.returncode is None),
                'execution': 'background_process', 'restarts': self.restarts}

    async def _stop_child(self):
        process = self.process
        if process is None or process.returncode is not None:
            return
        # EOF also reaches the child when the web parent unexpectedly dies.
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), self.shutdown_timeout)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

    async def _loop(self):
        failures = 0
        while True:
            drains = []
            started = time.monotonic()
            try:
                env = os.environ.copy()
                env[WORKER_FLAG] = '1'
                env['MEZAN_BACKGROUND_PARENT_ID'] = self.parent_id
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable, '-B', str(self.path), cwd=str(ROOT), env=env,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                drains = [asyncio.create_task(_discard(stream)) for stream in
                          (self.process.stdout, self.process.stderr)]
                code = await self.process.wait()
                log.warning('Background worker exited code=%s; restarting after cooldown', code)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('Background worker launch unavailable code=%s', type(exc).__name__)
            finally:
                await self._stop_child()
                await asyncio.gather(*drains, return_exceptions=True)
            self.restarts += 1
            failures = 0 if time.monotonic() - started >= 60 else min(failures + 1, 5)
            await asyncio.sleep(min(300, self.restart_delay * 2 ** max(0, failures - 1)))

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def wake(self, *, assembly=False):
        if not self.status()['running'] or self.process.stdin is None:
            return False
        try:
            self.process.stdin.write(b'assembly\n' if assembly else b'wake\n')
            await asyncio.wait_for(self.process.stdin.drain(), 1)
            return True
        except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError):
            return False


def start_background_process(app):
    global _current
    if is_worker_process():
        raise RuntimeError('background_worker_may_not_spawn_another_runtime')
    worker = getattr(app.state, 'background_worker', None)
    if worker is None:
        worker = ManagedWorker()
        app.state.background_worker = worker
    _current = worker
    worker.start()


async def stop_background_process(app):
    worker = getattr(app.state, 'background_worker', None)
    if worker is not None:
        await worker.stop()


def status():
    return _current.status() if _current else {'running': False, 'execution': 'background_process'}


async def wake_background_process(*, assembly=False):
    return await _current.wake(assembly=assembly) if _current is not None else False


async def read_status(db):
    if _current is None:
        return status()
    row = await db.background_worker_status.find_one({
        '_id': _current.parent_id, '$expr': {'$gt': ['$expires_at', '$$NOW']},
    }, {'_id': 0, 'jobs': 1, 'qoyod_pipeline': 1, 'updated_at': 1}) or {}
    return {**status(), **row}
