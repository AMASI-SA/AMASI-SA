"""Finite cookie-only authentication relay and explicit read-only HTTP gate."""
from __future__ import annotations
import asyncio
import base64
import json
import re

import httpx
from starlette.responses import JSONResponse, Response

from private_common import AUTH_ORIGIN

PREFIX = "/api/operational-balances"
GET_SUFFIXES = frozenset({
    "/workspace/bootstrap", "/workspace/home", "/workspace/directory", "/workspace/position", "/workspace/report",
    "/workspace/movements", "/movements", "/automation", "/freshness", "/context", "/financial-position",
    "/customer-returns", "/customer-exchanges", "/provider-history-reviews", "/courier-tariffs",
    "/provider-statements", "/provider-statements/saved-files", "/access/current", "/access/catalog",
    "/workspace/options", "/workspace/context", "/workspace/order-issues", "/workspace/parties", "/access/users",
})
GET_TEMPLATES = frozenset({
    "/entities/{kind}", "/workspace/selection/{kind}/{identity}", "/workspace/accounts/{kind}",
    "/workspace/resource/{kind}/{identity}", "/workspace/forecast/{kind}/{identity}",
    "/workspace/ledger/{kind}/{identity}", "/workspace/statements/{kind}/{identity}",
    "/workspace/history/{kind}/{identity}", "/workspace/supplier-invoices/{supplier_id}/{invoice_id}",
    "/access/users/{user_id}",
})
FEE_PATHS = frozenset({"/api/accounting-module/onboarding/fee-policies", "/api/accounting-module/onboarding/fee-policy-candidates"})
AUTH_METHODS = {"/api/auth/me": "GET", "/api/auth/login": "POST", "/api/auth/refresh": "POST", "/api/auth/logout": "POST"}
RELEASE = re.compile(re.escape(PREFIX) + r"/workspace/read-demand/[a-f0-9]{64}\Z")
ENTITY = re.compile(re.escape(PREFIX) + r"/entities/(?:employee|provider|courier|store_driver|ad_account|pos_receivable|supplier|external_person|bank|cash|employee_custody|operating_expense|owner_withdrawal)\Z")
SEGMENT = r"[^/\\\x00-\x20\x7f?#]{1,256}"
KIND = r"[a-z][a-z0-9_-]{0,63}"
DRILLDOWN = re.compile(re.escape(PREFIX) + r"(?:/workspace/(?:selection|resource|forecast|ledger|statements|history)/" + KIND + "/" + SEGMENT
    + r"|/workspace/accounts/" + KIND + r"|/workspace/supplier-invoices/" + SEGMENT + "/" + SEGMENT
    + r"|/access/users/[A-Za-z0-9_-]{1,160})\Z")
FORWARD_HEADERS = frozenset({b"cookie", b"origin", b"sec-fetch-site", b"sec-fetch-mode", b"sec-fetch-dest",
                             b"content-type", b"accept", b"accept-language", b"user-agent"})
RESPONSE_HEADERS = frozenset({b"content-type", b"cache-control", b"pragma", b"expires", b"retry-after", b"set-cookie",
                              b"content-security-policy", b"x-frame-options", b"x-content-type-options", b"referrer-policy",
                              b"permissions-policy", b"strict-transport-security", b"x-permitted-cross-domain-policies"})
MAX_AUTH_BODY = 32768
MAX_AUTH_RESPONSE = 1024 * 1024


def path_allowed(method, path):
    return (AUTH_METHODS.get(path) == method or method == "GET" and (
        path in FEE_PATHS or path.startswith(PREFIX) and path[len(PREFIX):] in GET_SUFFIXES or ENTITY.fullmatch(path) or DRILLDOWN.fullmatch(path))
        or method == "DELETE" and bool(RELEASE.fullmatch(path)))


def denied(code, status=403):
    return JSONResponse({"detail": {"code": code}}, status_code=status, headers={"Cache-Control": "no-store"})


class ReadOnlyGate:
    def __init__(self, app, *, origin):
        self.app, self.origin = app, origin

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        origin = headers.get(b"origin", b"").decode("latin-1")
        fetch_site = headers.get(b"sec-fetch-site", b"").decode("latin-1")
        method, path = scope["method"], scope["path"]
        if b"authorization" in headers:
            response = denied("preview_cookie_browser_session_required")
        elif (origin and origin != self.origin) or fetch_site == "cross-site":
            response = denied("csrf_origin_denied")
        elif method in {"POST", "DELETE"} and (origin != self.origin or fetch_site != "same-origin"):
            response = denied("preview_same_origin_browser_required")
        elif not path_allowed(method, path):
            response = denied("preview_read_only_route_denied")
        elif path in AUTH_METHODS and scope.get("query_string"):
            response = denied("preview_auth_query_not_supported", 400)
        else:
            return await self.app(scope, receive, send)
        return await response(scope, receive, send)


async def bounded_body(request):
    body = bytearray()
    async with asyncio.timeout(10):
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_AUTH_BODY:
                raise ValueError("auth_request_too_large")
    return bytes(body)


def browser_login_payload(body):
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        return False
    return (isinstance(payload, dict) and set(payload) <= {"email", "password", "mobile_client"}
            and (payload.get("mobile_client") is None or payload.get("mobile_client") is False))


def cookie_has_nonbrowser_envelope(token, kind):
    """Denial-only shape check; the existing auth service verifies the signature.

    Never read identity/role from these unverified claims. /auth/me does not expose
    its signed native client marker, so reject any such cookie before scope can be
    treated as browser-only. This cannot authorize an otherwise invalid cookie.
    """
    try:
        if not isinstance(token, str) or len(token) > 16384:
            return True
        parts = token.split(".")
        if len(parts) != 3:
            return True
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        return not isinstance(payload, dict) or payload.get("type") != kind or bool(payload.get("client"))
    except (ValueError, UnicodeError):
        return True


async def relay_auth(method, path, raw_headers, body=b"", *, transport=None):
    """One new client per call: no cookies, credentials or responses survive it."""
    if AUTH_METHODS.get(path) != method:
        raise ValueError("auth_route_not_allowlisted")
    forwarded = [(k, v) for k, v in raw_headers if k.lower() in FORWARD_HEADERS]
    if any(k.lower() == b"authorization" for k, _ in raw_headers):
        raise ValueError("bearer_not_supported")
    if len(body) > MAX_AUTH_BODY:
        raise ValueError("auth_request_too_large")
    try:
        async with asyncio.timeout(15):
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, transport=transport,
                    timeout=httpx.Timeout(connect=2, read=10, write=5, pool=2),
                    limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
                async with client.stream(method, AUTH_ORIGIN + path, headers=forwarded, content=body) as upstream:
                    if 300 <= upstream.status_code < 400:
                        return denied("preview_auth_redirect_refused", 502)
                    received = bytearray()
                    async for chunk in upstream.aiter_bytes():
                        received.extend(chunk)
                        if len(received) > MAX_AUTH_RESPONSE:
                            return denied("preview_auth_response_too_large", 502)
                    response = Response(bytes(received), status_code=upstream.status_code)
                    # raw_headers retains each Set-Cookie independently and unchanged.
                    response.raw_headers.extend((k.lower(), v) for k, v in upstream.headers.raw if k.lower() in RESPONSE_HEADERS)
                    return response
    except (httpx.HTTPError, TimeoutError):
        return denied("preview_existing_auth_unavailable", 503)


def authenticated_owner(response, expected):
    if response.status_code != 200:
        return False
    try:
        user = json.loads(response.body)
    except (ValueError, UnicodeError):
        return False
    return bool(isinstance(user, dict) and user.get("id") == expected and user.get("role") == "owner")
