"""Safe per-stage timing for read-only Snapchat V2 GET endpoints."""
from __future__ import annotations

import logging
import math
import os
import asyncio
from contextvars import ContextVar
from time import perf_counter
from typing import Any, Awaitable, Callable, TypeVar

from starlette.datastructures import MutableHeaders

LOGGER = logging.getLogger(__name__)

_TARGET_ROUTES = {
    "/api/integrations-v2/snapchat-v2/status": "status",
    "/api/integrations-v2/snapchat-v2/report": "report",
    "/api/integrations-v2/snapchat-v2/hourly": "hourly",
}
_ALLOWED_STAGES = {
    "db-selected-account",
    "db-connection",
    "db-run-snapshot",
    "db-lease",
    "db-latest-fact",
    "db-projection-lookup",
    "db-reconciliation-lookup",
    "headline-resolve",
}

T = TypeVar("T")


class _ReadTimingRecorder:
    def __init__(self) -> None:
        self.durations_ms: dict[str, float] = {}

    async def await_stage(self, stage: str, awaitable: Awaitable[T]) -> T:
        if stage not in _ALLOWED_STAGES:
            raise ValueError("unknown Snapchat V2 read timing stage")
        started = perf_counter()
        try:
            return await awaitable
        finally:
            self.durations_ms[stage] = max(
                (perf_counter() - started) * 1000.0,
                0.0,
            )

    def call_stage(self, stage: str, callback: Callable[[], T]) -> T:
        if stage not in _ALLOWED_STAGES:
            raise ValueError("unknown Snapchat V2 read timing stage")
        started = perf_counter()
        try:
            return callback()
        finally:
            self.durations_ms[stage] = max(
                (perf_counter() - started) * 1000.0,
                0.0,
            )


_RECORDER: ContextVar[_ReadTimingRecorder | None] = ContextVar(
    "snapchat_v2_read_timing_recorder",
    default=None,
)


async def timed_awaitable(stage: str, awaitable: Awaitable[T]) -> T:
    recorder = _RECORDER.get()
    if recorder is None:
        return await awaitable
    return await recorder.await_stage(stage, awaitable)


async def gather_cancel_on_error(*awaitables: Awaitable[Any]) -> tuple[Any, ...]:
    """Run independent reads together without leaving orphan work on failure."""
    tasks = [asyncio.create_task(awaitable) for awaitable in awaitables]
    try:
        return tuple(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def timed_call(stage: str, callback: Callable[[], T]) -> T:
    recorder = _RECORDER.get()
    if recorder is None:
        return callback()
    return recorder.call_stage(stage, callback)


def _slow_threshold_ms() -> float:
    try:
        value = float(os.environ.get("SNAPCHAT_V2_SLOW_GET_LOG_MS", "1000"))
        return max(value, 0.0) if math.isfinite(value) else 1000.0
    except (TypeError, ValueError):
        return 1000.0


class SnapchatV2ReadTimingMiddleware:
    """Add safe Server-Timing diagnostics to the three read-heavy GETs."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        endpoint = _TARGET_ROUTES.get(str(scope.get("path") or ""))
        if scope.get("type") != "http" or scope.get("method") != "GET" or not endpoint:
            await self.app(scope, receive, send)
            return

        recorder = _ReadTimingRecorder()
        token = _RECORDER.set(recorder)
        started = perf_counter()
        response_status: int | None = None

        async def send_with_timing(message: dict[str, Any]) -> None:
            nonlocal response_status
            if message.get("type") == "http.response.start":
                response_status = int(message.get("status") or 0)
                app_ms = max((perf_counter() - started) * 1000.0, 0.0)
                metrics = [
                    f"{name};dur={duration:.2f}"
                    for name, duration in sorted(recorder.durations_ms.items())
                ]
                metrics.append(f"app;dur={app_ms:.2f}")
                headers = MutableHeaders(scope=message)
                existing = headers.get("Server-Timing")
                own_metrics = ", ".join(metrics)
                headers["Server-Timing"] = (
                    f"{existing}, {own_metrics}" if existing else own_metrics
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            total_ms = max((perf_counter() - started) * 1000.0, 0.0)
            _RECORDER.reset(token)
            if total_ms >= _slow_threshold_ms():
                stages = " ".join(
                    f"{name}={duration:.2f}"
                    for name, duration in sorted(recorder.durations_ms.items())
                )
                LOGGER.info(
                    "snapchat_v2_read_timing endpoint=%s status=%s total_ms=%.2f stages=%s",
                    endpoint,
                    response_status,
                    total_ms,
                    stages,
                )


__all__ = [
    "SnapchatV2ReadTimingMiddleware",
    "gather_cancel_on_error",
    "timed_awaitable",
    "timed_call",
]
