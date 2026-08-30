"""ASGI boot/readiness primitives shared by production and lifecycle tests."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

BOOT_ALLOWED_PATHS = {
    "/health", "/api/health", "/ready", "/api/ready",
    "/health/diagnostics", "/api/health/diagnostics",
}


async def readiness_traffic_gate(request: Request, call_next):
    if (
        request.url.path not in BOOT_ALLOWED_PATHS
        and getattr(request.app.state, "readiness", "starting") != "ready"
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
    "start_deferred_task",
]
