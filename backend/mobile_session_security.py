"""Native AMASI mobile session support.

The web application keeps its refresh token in an HttpOnly cookie. The Android
application cannot depend on that browser cookie jar across process restarts, so
it receives the same refresh token explicitly only when the auth request opts in
with ``mobile_client: true``. The token is stored by the app in Android secure
storage and exchanged through a dedicated rotation endpoint.

Security invariants:
- browser auth responses are unchanged and never expose the refresh token;
- only successful native auth completions receive a refresh token;
- refresh tokens are checked against account disablement, password revocation,
  MFA, and email-OTP policy on every rotation;
- a native refresh returns one fresh access token and one rotated refresh token;
- responses are never cacheable.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from typing import Any, Iterable

import jwt
from starlette.middleware import Middleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

MOBILE_REFRESH_PATH = "/api/auth/mobile/refresh"
MOBILE_SESSION_PATHS = {
    "/api/auth/login",
    "/api/auth/mfa/verify",
    "/api/auth/email-otp/verify",
}


def _json_object(body: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


async def _read_body(receive) -> tuple[bytes, list[dict[str, Any]]]:
    chunks: list[bytes] = []
    messages: list[dict[str, Any]] = []
    while True:
        message = await receive()
        messages.append(dict(message))
        if message.get("type") != "http.request":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks), messages


def _replay_receive(messages: Iterable[dict[str, Any]]):
    queue = [dict(item) for item in messages]

    async def receive():
        if queue:
            return queue.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    return receive


async def _send_messages(messages: Iterable[dict[str, Any]], send) -> None:
    for message in messages:
        await send(message)


def _response_start(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (message for message in messages if message.get("type") == "http.response.start"),
        None,
    )


def _response_body(messages: list[dict[str, Any]]) -> bytes:
    return b"".join(
        message.get("body", b"")
        for message in messages
        if message.get("type") == "http.response.body"
    )


def _header_values(start: dict[str, Any], name: bytes) -> list[bytes]:
    lowered = name.lower()
    return [
        value
        for key, value in start.get("headers", [])
        if bytes(key).lower() == lowered
    ]


def _refresh_cookie(start: dict[str, Any]) -> str | None:
    for raw in _header_values(start, b"set-cookie"):
        cookie = SimpleCookie()
        try:
            cookie.load(raw.decode("latin-1"))
        except Exception:
            continue
        morsel = cookie.get("refresh_token")
        if morsel and morsel.value:
            return morsel.value
    return None


def _replace_content_length(
    headers: list[tuple[bytes, bytes]],
    body: bytes,
) -> list[tuple[bytes, bytes]]:
    filtered = [
        (key, value)
        for key, value in headers
        if bytes(key).lower() != b"content-length"
    ]
    filtered.append((b"content-length", str(len(body)).encode("ascii")))
    return filtered


def _inject_mobile_refresh_token(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    start = _response_start(messages)
    if not start or int(start.get("status") or 0) != 200:
        return messages

    content_types = _header_values(start, b"content-type")
    if not any(b"application/json" in value.lower() for value in content_types):
        return messages

    refresh_token = _refresh_cookie(start)
    if not refresh_token:
        return messages

    payload = _json_object(_response_body(messages))
    if payload is None or not payload.get("access_token"):
        return messages

    from auth import REFRESH_COOKIE_MAX_AGE_SECONDS

    payload["refresh_token"] = refresh_token
    payload["refresh_expires_in_seconds"] = REFRESH_COOKIE_MAX_AGE_SECONDS
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    rewritten: list[dict[str, Any]] = []
    body_written = False
    for message in messages:
        if message.get("type") == "http.response.start":
            next_message = dict(message)
            next_message["headers"] = _replace_content_length(
                list(message.get("headers", [])),
                body,
            )
            rewritten.append(next_message)
        elif message.get("type") == "http.response.body":
            if not body_written:
                next_message = dict(message)
                next_message["body"] = body
                next_message["more_body"] = False
                rewritten.append(next_message)
                body_written = True
        else:
            rewritten.append(dict(message))
    if not body_written:
        rewritten.append({"type": "http.response.body", "body": body, "more_body": False})
    return rewritten


def _as_utc_timestamp(value: Any) -> float | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def _token_predates_password_change(payload: dict[str, Any], user: dict[str, Any]) -> bool:
    changed_at = _as_utc_timestamp(user.get("password_updated_at"))
    issued_at = payload.get("iat")
    return changed_at is not None and (
        not isinstance(issued_at, (int, float)) or float(issued_at) <= changed_at
    )


def _no_store(response: JSONResponse) -> JSONResponse:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


class MobileSessionSecurityMiddleware:
    def __init__(self, app, *, db):
        self.app = app
        self.db = db

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "").upper()
        path = str(scope.get("path") or "")

        if method == "POST" and path == MOBILE_REFRESH_PATH:
            body, messages = await _read_body(receive)
            await self._refresh(scope, body, messages, send)
            return

        if method != "POST" or path not in MOBILE_SESSION_PATHS:
            await self.app(scope, receive, send)
            return

        body, messages = await _read_body(receive)
        payload = _json_object(body) or {}
        if payload.get("mobile_client") is not True:
            await self.app(scope, _replay_receive(messages), send)
            return

        captured: list[dict[str, Any]] = []

        async def capture_send(message: dict[str, Any]):
            captured.append(dict(message))

        await self.app(scope, _replay_receive(messages), capture_send)
        await _send_messages(_inject_mobile_refresh_token(captured), send)

    async def _refresh(self, scope, body: bytes, messages, send) -> None:
        payload = _json_object(body) or {}
        token = str(payload.get("refresh_token") or "").strip()
        if not token:
            response = _no_store(JSONResponse(
                {
                    "detail": "Refresh token missing",
                    "code": "mobile_refresh_token_missing",
                },
                status_code=400,
            ))
            await response(scope, _replay_receive(messages), send)
            return

        from auth import (
            ACCESS_COOKIE_MAX_AGE_SECONDS,
            JWT_ALGORITHM,
            PRIVILEGED_MFA_ROLES,
            REFRESH_COOKIE_MAX_AGE_SECONDS,
            account_is_disabled,
            create_access_token,
            create_refresh_token,
            get_jwt_secret,
        )

        try:
            decoded = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
            if decoded.get("type") != "refresh":
                raise jwt.InvalidTokenError("invalid token type")

            user_id = str(decoded.get("sub") or "").strip()
            user = await self.db.users.find_one({"id": user_id})
            if not user or account_is_disabled(user):
                response = _no_store(JSONResponse(
                    {
                        "detail": "Account disabled",
                        "code": "mobile_session_account_unavailable",
                    },
                    status_code=401,
                ))
                await response(scope, _replay_receive(messages), send)
                return
            if _token_predates_password_change(decoded, user):
                response = _no_store(JSONResponse(
                    {
                        "detail": "Session revoked",
                        "code": "mobile_session_revoked",
                    },
                    status_code=401,
                ))
                await response(scope, _replay_receive(messages), send)
                return

            mfa_verified = decoded.get("mfa") is True
            role = str(user.get("role") or "").strip().lower()
            if role in PRIVILEGED_MFA_ROLES and not mfa_verified:
                response = _no_store(JSONResponse(
                    {
                        "detail": "MFA verification required",
                        "code": "mobile_session_mfa_required",
                    },
                    status_code=401,
                ))
                await response(scope, _replay_receive(messages), send)
                return

            if not mfa_verified:
                from email_otp_policy import requires_email_otp

                if await requires_email_otp(self.db, user):
                    response = _no_store(JSONResponse(
                        {
                            "detail": "Email OTP verification required",
                            "code": "mobile_session_email_otp_required",
                        },
                        status_code=401,
                    ))
                    await response(scope, _replay_receive(messages), send)
                    return

            access = create_access_token(
                user["id"],
                user["email"],
                mfa_verified=mfa_verified,
            )
            refresh = create_refresh_token(
                user["id"],
                mfa_verified=mfa_verified,
            )
            response = _no_store(JSONResponse(
                {
                    "ok": True,
                    "access_token": access,
                    "refresh_token": refresh,
                    "expires_in_seconds": ACCESS_COOKIE_MAX_AGE_SECONDS,
                    "refresh_expires_in_seconds": REFRESH_COOKIE_MAX_AGE_SECONDS,
                },
                status_code=200,
            ))
            await response(scope, _replay_receive(messages), send)
        except jwt.ExpiredSignatureError:
            response = _no_store(JSONResponse(
                {
                    "detail": "Refresh token expired",
                    "code": "mobile_refresh_token_expired",
                },
                status_code=401,
            ))
            await response(scope, _replay_receive(messages), send)
        except jwt.InvalidTokenError:
            response = _no_store(JSONResponse(
                {
                    "detail": "Invalid refresh token",
                    "code": "mobile_refresh_token_invalid",
                },
                status_code=401,
            ))
            await response(scope, _replay_receive(messages), send)
        except Exception:
            logger.exception("mobile session refresh failed")
            response = _no_store(JSONResponse(
                {
                    "detail": "تعذر تجديد جلسة التطبيق مؤقتاً.",
                    "code": "mobile_session_refresh_unavailable",
                },
                status_code=503,
            ))
            await response(scope, _replay_receive(messages), send)


async def install_mobile_session_security(app, db) -> None:
    """Install the native session boundary before the auth challenge layers."""
    if getattr(app.state, "mezan_mobile_session_security_installed", False):
        return
    app.user_middleware.append(Middleware(MobileSessionSecurityMiddleware, db=db))
    app.middleware_stack = app.build_middleware_stack()
    app.state.mezan_mobile_session_security_installed = True
    logger.info("AMASI mobile 30-day refresh session support enabled")
