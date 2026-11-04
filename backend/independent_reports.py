"""Run unchanged report/dashboard handlers on a separate HTTP worker process.

Only the explicit independent entrypoint installs this boundary. Authentication,
tenant checks, calculations and response formats remain in original handlers.
There is no retry/local fallback: a lost upstream response is not re-executed.
"""
from __future__ import annotations

import asyncio
import os
import re
from urllib.parse import urlsplit

import httpx
from starlette.middleware import Middleware
from starlette.responses import JSONResponse


REPORT_PATHS = re.compile(
    r"^/api/(?:dashboard(?:/[^/]+)?|dashboard-v2(?:/[^/]+(?:/[^/]+)?)?"
    r"|reports/ads|operational-reports|analyses(?:/[^/]+(?:/export/(?:excel|pdf))?)?)$"
)
BOOT_PATHS = {"/live", "/api/live", "/health", "/api/health", "/ready", "/api/ready"}
HOP_HEADERS = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
    b"te", b"trailer", b"transfer-encoding", b"upgrade"}


def is_report_request(method, path):
    return method == "GET" and REPORT_PATHS.fullmatch(path) is not None


def validate_worker_url(value):
    parsed = urlsplit(value)
    parsed.port  # Reject malformed/non-numeric ports before accepting traffic.
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("report worker URL must be an HTTP(S) origin without credentials")
    return value.rstrip("/")


def end_to_end_headers(headers):
    connection = set()
    for key, value in headers:
        if key.lower() == b"connection":
            connection.update(part.strip().lower() for part in value.split(b","))
    return [(key, value) for key, value in headers
        if key.lower() not in HOP_HEADERS | connection]


class ReportBoundary:
    def __init__(self, app, *, role, worker_url="", client=None):
        self.app, self.role = app, role
        self.worker_url = validate_worker_url(worker_url) if worker_url else ""
        self.client = client
        self.slots = asyncio.Semaphore(4)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        report = is_report_request(method, path)
        if self.role == "worker-reports":
            if not report and path not in BOOT_PATHS:
                return await JSONResponse({"detail": "report worker route not found"}, 404)(scope, receive, send)
            return await self.app(scope, receive, send)
        if not report:
            return await self.app(scope, receive, send)
        if not self.worker_url or self.client is None:
            return await self.unavailable(scope, receive, send)
        try:
            await asyncio.wait_for(self.slots.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            return await self.unavailable(scope, receive, send)
        response = None
        try:
            query = scope.get("query_string", b"").decode("ascii")
            # Build from this trusted origin, never from request Host/X-Forwarded-*.
            url = self.worker_url + path + (("?" + query) if query else "")
            headers = [(k, v) for k, v in end_to_end_headers(scope["headers"])
                if k.lower() not in {b"host", b"content-length", b"cookie"}]
            # Explicitly override the shared HTTP client's cookie jar, including
            # for unauthenticated requests. Never borrow another user's session.
            cookies = b"; ".join(v for k, v in scope["headers"] if k.lower() == b"cookie")
            headers.append((b"cookie", cookies))
            request = self.client.build_request("GET", url, headers=headers)
            try:
                response = await self.client.send(request, stream=True)
            except (httpx.HTTPError, ValueError):
                return await self.unavailable(scope, receive, send)
            await send({"type": "http.response.start", "status": response.status_code,
                "headers": end_to_end_headers(response.headers.raw)})
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            try:
                if response is not None:
                    await response.aclose()
            finally:
                self.slots.release()

    @staticmethod
    async def unavailable(scope, receive, send):
        await JSONResponse({"detail": {"code": "report_worker_unavailable", "retryable": True}},
            status_code=503, headers={"Retry-After": "5"})(scope, receive, send)


def install_report_boundary(app, role):
    url = os.environ.get("MEZAN_REPORT_WORKER_URL", "") if role == "web" else ""
    if role == "web" and os.environ.get("APP_ENV") != "test" and not url:
        raise ValueError("independent web requires MEZAN_REPORT_WORKER_URL")
    if url:
        validate_worker_url(url)
    app.state.report_worker_client = None

    # The client is created and closed inside this process's lifespan.
    class Boundary(ReportBoundary):
        async def __call__(self, scope, receive, send):
            self.client = app.state.report_worker_client
            return await super().__call__(scope, receive, send)

    # Keep original CORS/browser/readiness middleware outside this boundary,
    # including for a locally generated 503 when the reports worker is down.
    app.user_middleware.append(Middleware(Boundary, role=role, worker_url=url))
