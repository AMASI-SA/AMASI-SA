"""ASGI boot/readiness primitives shared by production and lifecycle tests."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

BOOT_ALLOWED_PATHS = {
    "/live", "/api/live", "/health", "/api/health", "/ready", "/api/ready",
    "/health/diagnostics", "/api/health/diagnostics",
}

class ProcessReadinessEvent:
    """Small loop-neutral event for process lifecycle and isolated test loops."""

    def __init__(self) -> None:
        self._ready = False
        self._waiters: set[asyncio.Future[None]] = set()

    def is_set(self) -> bool:
        return self._ready

    def set(self) -> None:
        self._ready = True
        for waiter in tuple(self._waiters):
            if not waiter.done():
                waiter.set_result(None)

    def clear(self) -> None:
        self._ready = False

    async def wait(self) -> None:
        if self._ready:
            return
        waiter = asyncio.get_running_loop().create_future()
        self._waiters.add(waiter)
        try:
            await waiter
        finally:
            self._waiters.discard(waiter)


# Created before router startup callbacks are registered. Heavy process-local
# schedulers wait on this exact event and therefore cannot reach Mongo or a
# provider until release startup and local initialization have both succeeded.
process_local_readiness_event = ProcessReadinessEvent()


def is_ready(app) -> bool:
    return getattr(app.state, "readiness", "starting") == "ready"


async def wait_for_local_readiness() -> None:
    await process_local_readiness_event.wait()


async def readiness_traffic_gate(request: Request, call_next):
    if (
        request.url.path not in BOOT_ALLOWED_PATHS
        and not is_ready(request.app)
    ):
        return JSONResponse(
            status_code=503,
            content={"detail": {
                "code": "backend_initializing", "retryable": True,
                "phase": getattr(request.app.state, "startup_phase", "unknown"),
            }},
            headers={"Retry-After": "5"},
        )
    return await call_next(request)


def start_deferred_task(app, work: Callable[[], Awaitable[None]]) -> asyncio.Task:
    async def guarded() -> None:
        try:
            await work()
        except asyncio.CancelledError:
            raise
        except Exception:
            app.state.readiness = "failed"
            app.state.startup_phase = "initialization_failed"
            raise

    task = asyncio.create_task(guarded(), name="deferred-backend-initialization")
    app.state.deferred_startup_task = task
    return task


async def cancel_deferred_task(app) -> None:
    task = getattr(app.state, "deferred_startup_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


__all__ = [
    "BOOT_ALLOWED_PATHS", "cancel_deferred_task", "readiness_traffic_gate",
    "is_ready", "ProcessReadinessEvent", "process_local_readiness_event", "start_deferred_task",
    "wait_for_local_readiness",
]
