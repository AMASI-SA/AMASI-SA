"""Rate-limited server timing for web/mobile APIs; no client identity inference."""
import asyncio
import time

import latency_evidence as evidence
from observability_middleware import route_group


class LatencyEvidenceMiddleware:
    def __init__(self, app):
        self.app = app
        self.last = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not evidence.enabled():
            return await self.app(scope, receive, send)
        started, session_id = time.monotonic(), evidence.session()
        status, finished, cancelled = 500, False, False

        def finish():
            nonlocal finished
            if finished:
                return
            finished = True
            if not evidence.enabled() or evidence.session() != session_id:
                return
            template = getattr(scope.get("route"), "path", "")
            # Control/health calls do not produce business API samples.
            if template.startswith(("/api/internal/", "/api/health", "/health")):
                return
            group = route_group(scope)
            now = time.monotonic()
            if now - self.last.get(group, float("-inf")) < 30:
                return
            self.last[group] = now
            evidence._emit("api_sample", _session_id=session_id, group=group,
                           status_class="cancelled" if cancelled else f"{status // 100}xx",
                           elapsed_ms=round((now - started) * 1000, 3))

        async def measured_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finish()

        try:
            return await self.app(scope, receive, measured_send)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            finish()
