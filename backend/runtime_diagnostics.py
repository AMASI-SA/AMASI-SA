"""Low-overhead runtime diagnostics with no database/provider dependency."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from resource_governor import governor
from mongo_observability import mongo_metrics

_event_loop_lag_ms = 0.0
_monitor_task: asyncio.Task | None = None


async def _lag_monitor() -> None:
    global _event_loop_lag_ms
    interval = 1.0
    expected = time.monotonic() + interval
    while True:
        await asyncio.sleep(interval)
        now = time.monotonic()
        _event_loop_lag_ms = max(0.0, (now - expected) * 1000)
        expected = now + interval


def start_lag_monitor() -> asyncio.Task:
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(_lag_monitor(), name="event-loop-lag-monitor")
    return _monitor_task


def diagnostics(*, mongo_client: Any | None = None) -> dict[str, Any]:
    result = governor.diagnostics()
    result["event_loop_lag_ms"] = round(_event_loop_lag_ms, 2)
    configured_pool_size = None
    if mongo_client is not None:
        try:
            configured_pool_size = mongo_client.options.pool_options.max_pool_size
        except (AttributeError, TypeError):
            pass
    if configured_pool_size is None and os.environ.get("MONGO_MAX_POOL_SIZE"):
        try:
            configured_pool_size = int(os.environ["MONGO_MAX_POOL_SIZE"])
        except ValueError:
            pass
    result["mongo"] = {
        "configured_max_pool_size": configured_pool_size,
        **mongo_metrics.snapshot(),
    }
    return result


__all__ = ["diagnostics", "start_lag_monitor"]
