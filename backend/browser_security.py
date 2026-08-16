"""Browser-facing API security headers and cookie-CSRF protection."""
from __future__ import annotations

from collections.abc import Iterable

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_COOKIE_NAMES = (b"access_token=", b"refresh_token=")
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


def _headers(scope: Scope) -> dict[bytes, bytes]:
    return {key.lower(): value for key, value in scope.get("headers", [])}


def _has_auth_cookie(raw_cookie: bytes) -> bool:
    lowered = raw_cookie.lower()
    return any(name in lowered for name in _COOKIE_NAMES)


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

        headers = _headers(scope)
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
                )
                await response(scope, receive, send)
                return

        async def send_with_security_headers(message: Message) -> None:
            if message.get("type") == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers.setdefault("Content-Security-Policy", _API_CSP)
                response_headers.setdefault("X-Frame-Options", "DENY")
                response_headers.setdefault("X-Content-Type-Options", "nosniff")
                response_headers.setdefault("Referrer-Policy", "no-referrer")
                response_headers.setdefault(
                    "Permissions-Policy",
                    "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
                )
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


__all__ = ["BrowserSecurityMiddleware"]
