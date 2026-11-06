"""ASGI timing only: never read headers, body, query string or identifiers."""
import asyncio
import time
import logging

from observability_metrics import EMPLOYEE_ROUTES, metrics

log = logging.getLogger('mezan.api_timing')


def route_group(scope):
    # Read only the declared route template, never the raw request path.
    template = getattr(scope.get("route"), "path", "")
    if scope.get("method") == "POST" and template in (
        "/api/preparation-work-v1/assembly/pieces/{piece_id}/ready",
        "/preparation-work-v1/assembly/pieces/{piece_id}/ready",
    ):
        return "ready"
    if "supplier" in template or "purchase-invoice" in template:
        return "supplier_invoice"
    if "shipping" in template or "carrier-label" in template:
        return "shipping"
    return "other"


class DiagnosticsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = metrics.begin_request()
        started = time.monotonic()
        status = 500
        cancelled = False
        finished = False
        def finish():
            nonlocal finished
            if finished:
                return
            finished = True
            metrics.end_request(token)
            elapsed = time.monotonic() - started
            metrics.observe("api.duration." + route_group(scope), elapsed)
            category = str(status) if status in (500, 502, 503, 504) else f"{status // 100}xx"
            outcome = "cancelled" if cancelled else category
            metrics.increment("api.status." + outcome)
            template = getattr(scope.get("route"), "path", "")
            # Only the router's declared template, never the request URL,
            # query, headers, user/order identity or exception text.
            method = scope.get('method')
            if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}:
                method = 'OTHER'
            log.info('api_timing method=%s route=%s status=%s duration_ms=%.3f outcome=%s',
                     method, template[:200] or 'unmatched', status, elapsed * 1000, outcome)
            route = EMPLOYEE_ROUTES.get((scope.get("method"), template.removeprefix("/api")))
            if route:
                metrics.observe("employee_route.duration." + route, elapsed)
                if not cancelled and status in (401, 403):
                    outcome = str(status)
                metrics.increment(f"employee_route.status.{route}.{outcome}")
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
