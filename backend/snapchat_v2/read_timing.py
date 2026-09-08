"""Privacy-safe Server-Timing for read-only Snapchat V2 endpoints."""
from __future__ import annotations

import logging
import math
import os
from time import perf_counter
from typing import Any

from starlette.datastructures import MutableHeaders

LOGGER = logging.getLogger(__name__)
_TARGETS = {
    "/api/integrations-v2/snapchat-v2/status": "status",
    "/api/integrations-v2/snapchat-v2/report": "report",
    "/api/integrations-v2/snapchat-v2/hourly": "hourly",
    "/api/integrations-v2/snapchat-v2/campaigns": "campaigns",
    "/api/integrations-v2/snapchat-v2/ad-squads": "ad-squads",
    "/api/integrations-v2/snapchat-v2/ads": "ads",
}


def _threshold_ms() -> float:
    try:
        value = float(os.environ.get("SNAPCHAT_V2_SLOW_GET_LOG_MS", "1000"))
        return max(value, 0.0) if math.isfinite(value) else 1000.0
    except (TypeError, ValueError):
        return 1000.0


class SnapchatV2ReadTimingMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        endpoint = _TARGETS.get(str(scope.get("path") or ""))
        if scope.get("type") != "http" or scope.get("method") != "GET" or not endpoint:
            await self.app(scope, receive, send)
            return
        started = perf_counter()
        response_status: int | None = None

        async def timed_send(message: dict[str, Any]) -> None:
            nonlocal response_status
            if message.get("type") == "http.response.start":
                response_status = int(message.get("status") or 0)
                elapsed_ms = max((perf_counter() - started) * 1000.0, 0.0)
                headers = MutableHeaders(scope=message)
                own = f"snapchat-v2-app;dur={elapsed_ms:.2f}"
                existing = headers.get("Server-Timing")
                headers["Server-Timing"] = f"{existing}, {own}" if existing else own
            await send(message)

        try:
            await self.app(scope, receive, timed_send)
        finally:
            total_ms = max((perf_counter() - started) * 1000.0, 0.0)
            if total_ms >= _threshold_ms():
                LOGGER.info(
                    "snapchat_v2_read_timing endpoint=%s status=%s total_ms=%.2f",
                    endpoint,
                    response_status,
                    total_ms,
                )


__all__ = ["SnapchatV2ReadTimingMiddleware"]
