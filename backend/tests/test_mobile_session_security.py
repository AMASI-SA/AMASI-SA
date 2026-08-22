import json

import jwt
import pytest

from auth import create_access_token, create_refresh_token, get_jwt_secret
from mobile_session_security import MobileSessionSecurityMiddleware


class _Users:
    def __init__(self, user):
        self.user = user

    async def find_one(self, query):
        if self.user and query.get("id") == self.user.get("id"):
            return dict(self.user)
        return None


class _Db:
    def __init__(self, user):
        self.users = _Users(user)


async def _request(middleware, path, payload):
    request_messages = [
        {
            "type": "http.request",
            "body": json.dumps(payload).encode("utf-8"),
            "more_body": False,
        }
    ]
    sent = []

    async def receive():
        if request_messages:
            return request_messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(dict(message))

    await middleware(
        {"type": "http", "method": "POST", "path": path},
        receive,
        send,
    )
    start = next(item for item in sent if item["type"] == "http.response.start")
    body = b"".join(
        item.get("body", b"")
        for item in sent
        if item["type"] == "http.response.body"
    )
    return start, json.loads(body.decode("utf-8"))


def _auth_app(refresh_cookie="refresh-value", access_token="access-value"):
    async def app(scope, receive, send):
        await receive()
        payload = {"access_token": access_token, "ok": True}
        body = json.dumps(payload).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if refresh_cookie is not None:
            headers.append(
                (
                    b"set-cookie",
                    (
                        "refresh_token=" + refresh_cookie
                        + "; Path=/; HttpOnly; Secure; SameSite=None"
                    ).encode("latin-1"),
                )
            )
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})

    return app


@pytest.mark.asyncio
async def test_mobile_auth_response_replaces_browser_tokens_with_native_bound_tokens(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "mobile-session-test-secret")
    browser_access = create_access_token(
        "owner-1",
        "owner@example.com",
        mfa_verified=True,
    )
    middleware = MobileSessionSecurityMiddleware(
        _auth_app(access_token=browser_access),
        db=_Db(None),
    )
    start, payload = await _request(
        middleware,
        "/api/auth/mfa/verify",
        {"mobile_client": True, "challenge_token": "x", "code": "123456"},
    )

    assert start["status"] == 200
    assert payload["access_token"] != browser_access
    access_payload = jwt.decode(
        payload["access_token"],
        get_jwt_secret(),
        algorithms=["HS256"],
    )
    refresh_payload = jwt.decode(
        payload["refresh_token"],
        get_jwt_secret(),
        algorithms=["HS256"],
    )
    assert access_payload["client"] == "amasi_mobile"
    assert refresh_payload["client"] == "amasi_mobile"
    assert payload["refresh_expires_in_seconds"] == 30 * 24 * 60 * 60


@pytest.mark.asyncio
async def test_mobile_auth_derives_refresh_when_success_cookie_is_missing(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "mobile-session-test-secret")
    access = create_access_token(
        "owner-1",
        "owner@example.com",
        mfa_verified=True,
    )
    middleware = MobileSessionSecurityMiddleware(
        _auth_app(refresh_cookie=None, access_token=access),
        db=_Db(None),
    )

    start, payload = await _request(
        middleware,
        "/api/auth/mfa/verify",
        {"mobile_client": True, "challenge_token": "x", "code": "123456"},
    )

    assert start["status"] == 200
    assert payload["access_token"] != access
    assert payload["refresh_token"]
    refresh_payload = jwt.decode(
        payload["refresh_token"],
        get_jwt_secret(),
        algorithms=["HS256"],
    )
    assert refresh_payload["sub"] == "owner-1"
    assert refresh_payload["type"] == "refresh"
    assert refresh_payload["mfa"] is True
    assert refresh_payload["client"] == "amasi_mobile"
    headers = {
        key.lower(): value
        for key, value in start["headers"]
    }
    assert headers[b"cache-control"] == b"no-store, no-cache, must-revalidate"
    assert headers[b"pragma"] == b"no-cache"


@pytest.mark.asyncio
async def test_mobile_auth_fails_closed_for_unsigned_access_without_cookie():
    middleware = MobileSessionSecurityMiddleware(
        _auth_app(refresh_cookie=None, access_token="not-a-jwt"),
        db=_Db(None),
    )
    _, payload = await _request(
        middleware,
        "/api/auth/mfa/verify",
        {"mobile_client": True, "challenge_token": "x", "code": "123456"},
    )

    assert payload == {"access_token": "not-a-jwt", "ok": True}
    assert "refresh_token" not in payload


@pytest.mark.asyncio
async def test_browser_auth_response_never_exposes_refresh_cookie():
    middleware = MobileSessionSecurityMiddleware(_auth_app(), db=_Db(None))
    _, payload = await _request(
        middleware,
        "/api/auth/login",
        {"email": "owner@example.com", "password": "secret"},
    )

    assert payload == {"access_token": "access-value", "ok": True}
    assert "refresh_token" not in payload


@pytest.mark.asyncio
async def test_mobile_refresh_rotates_access_and_refresh_tokens(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "mobile-session-test-secret")
    user = {
        "id": "user-1",
        "email": "owner@example.com",
        "role": "owner",
        "mfa_enabled": True,
    }
    refresh = create_refresh_token(
        user["id"],
        mfa_verified=True,
        client_type="amasi_mobile",
    )

    async def inner_app(scope, receive, send):  # pragma: no cover - must not run
        raise AssertionError("mobile refresh must terminate in its own boundary")

    middleware = MobileSessionSecurityMiddleware(inner_app, db=_Db(user))
    start, payload = await _request(
        middleware,
        "/api/auth/mobile/refresh",
        {"refresh_token": refresh},
    )

    assert start["status"] == 200
    assert payload["ok"] is True
    assert payload["access_token"] != refresh
    assert payload["refresh_token"]
    access_payload = jwt.decode(
        payload["access_token"],
        get_jwt_secret(),
        algorithms=["HS256"],
    )
    rotated_payload = jwt.decode(
        payload["refresh_token"],
        get_jwt_secret(),
        algorithms=["HS256"],
    )
    assert access_payload["type"] == "access"
    assert access_payload["mfa"] is True
    assert access_payload["client"] == "amasi_mobile"
    assert rotated_payload["type"] == "refresh"
    assert rotated_payload["mfa"] is True
    assert rotated_payload["client"] == "amasi_mobile"


@pytest.mark.asyncio
async def test_mobile_refresh_rejects_an_untagged_browser_refresh_token(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "mobile-session-test-secret")
    user = {"id": "user-1", "email": "user@example.com", "role": "viewer"}
    browser_refresh = create_refresh_token(user["id"])

    async def inner_app(scope, receive, send):  # pragma: no cover
        raise AssertionError("mobile refresh must terminate in its own boundary")

    start, payload = await _request(
        MobileSessionSecurityMiddleware(inner_app, db=_Db(user)),
        "/api/auth/mobile/refresh",
        {"refresh_token": browser_refresh},
    )
    assert start["status"] == 401
    assert payload["code"] == "mobile_refresh_token_invalid"


@pytest.mark.asyncio
async def test_mobile_refresh_rejects_expired_or_invalid_token(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "mobile-session-test-secret")

    async def inner_app(scope, receive, send):  # pragma: no cover - must not run
        raise AssertionError("mobile refresh must terminate in its own boundary")

    middleware = MobileSessionSecurityMiddleware(inner_app, db=_Db(None))
    start, payload = await _request(
        middleware,
        "/api/auth/mobile/refresh",
        {"refresh_token": "not-a-jwt"},
    )

    assert start["status"] == 401
    assert payload["code"] == "mobile_refresh_token_invalid"

def test_explicit_bearer_token_wins_over_ambient_cookie():
    from starlette.requests import Request

    from auth import _extract_token

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/orders-v2",
        "headers": [
            (b"authorization", b"Bearer native-mobile-token"),
            (b"cookie", b"access_token=browser-cookie-token"),
        ],
    })

    assert _extract_token(request) == "native-mobile-token"


def test_browser_cookie_remains_supported_without_bearer_header():
    from starlette.requests import Request

    from auth import _extract_token

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/dashboard",
        "headers": [(b"cookie", b"access_token=browser-cookie-token")],
    })

    assert _extract_token(request) == "browser-cookie-token"
