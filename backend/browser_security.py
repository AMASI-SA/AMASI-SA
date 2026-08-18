"""Browser-facing API security headers, request correlation and cookie-CSRF protection."""
from __future__ import annotations

import time
import uuid
from collections.abc import Iterable

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_COOKIE_NAMES = (b"access_token=", b"refresh_token=")
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_HSTS = "max-age=31536000; includeSubDomains"


def _headers(scope: Scope) -> dict[bytes, bytes]:
    return {key.lower(): value for key, value in scope.get("headers", [])}


def _has_auth_cookie(raw_cookie: bytes) -> bool:
    lowered = raw_cookie.lower()
    return any(name in lowered for name in _COOKIE_NAMES)


def _request_is_https(scope: Scope, headers: dict[bytes, bytes]) -> bool:
    if str(scope.get("scheme") or "").lower() == "https":
        return True
    forwarded_proto = (
        headers.get(b"x-forwarded-proto", b"")
        .decode("latin-1")
        .split(",", 1)[0]
        .strip()
        .lower()
    )
    return forwarded_proto == "https"


def _request_id(headers: dict[bytes, bytes]) -> str:
    candidate = headers.get(b"x-request-id", b"").decode("latin-1").strip()
    if candidate:
        return candidate[:128]
    return uuid.uuid4().hex


class BrowserSecurityMiddleware:
    """Block cross-site cookie mutations and add defense-in-depth API headers."""

    def __init__(self, app: ASGIApp, trusted_origins: Iterable[str]) -> None:
        self.app = app
        self.trusted_origins = {
            str(origin).strip().rstrip("/")
            for origin in trusted_origins
            if str(origin).strip()
        }

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_started = time.perf_counter()
        headers = _headers(scope)
        request_id = _request_id(headers)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        method = str(scope.get("method") or "GET").upper()
        raw_cookie = headers.get(b"cookie", b"")
        if method not in _SAFE_METHODS and _has_auth_cookie(raw_cookie):
            origin = headers.get(b"origin", b"").decode("latin-1").strip().rstrip("/")
            fetch_site = headers.get(b"sec-fetch-site", b"").decode("latin-1").strip().lower()
            untrusted_origin = bool(origin) and origin not in self.trusted_origins
            cross_site_without_trust = fetch_site == "cross-site" and origin not in self.trusted_origins
            if untrusted_origin or cross_site_without_trust:
                response = JSONResponse(
                    {
                        "detail": {
                            "code": "csrf_origin_denied",
                            "message": "تم رفض الطلب لعدم تطابق مصدر الجلسة.",
                        }
                    },
                    status_code=403,
                    headers={"X-Request-ID": request_id},
                )
                await response(scope, receive, send)
                return

        is_https = _request_is_https(scope, headers)

        async def send_with_security_headers(message: Message) -> None:
            if message.get("type") == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers.setdefault("X-Request-ID", request_id)
                response_headers.setdefault(
                    "Server-Timing",
                    f"total;dur={(time.perf_counter() - request_started) * 1000:.2f}",
                )
                response_headers.setdefault("Content-Security-Policy", _API_CSP)
                response_headers.setdefault("X-Frame-Options", "DENY")
                response_headers.setdefault("X-Content-Type-Options", "nosniff")
                response_headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
                response_headers.setdefault("Referrer-Policy", "no-referrer")
                response_headers.setdefault(
                    "Permissions-Policy",
                    "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
                )
                if is_https:
                    response_headers.setdefault("Strict-Transport-Security", _HSTS)
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


__all__ = ["BrowserSecurityMiddleware"]
