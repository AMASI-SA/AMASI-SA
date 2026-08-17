from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import sys
import types

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request


# The isolated security-test environment intentionally installs only the small
# unit-test dependency set. Provide deterministic stand-ins so this file tests
# Mezan's revocation and byte-limit policy without requiring crypto wheels.
_fake_bcrypt = types.ModuleType("bcrypt")
_fake_bcrypt.gensalt = lambda: b"unit-test-salt"
_fake_bcrypt.hashpw = lambda password, salt: b"$unit$" + hashlib.sha256(password).hexdigest().encode()
_fake_bcrypt.checkpw = lambda password, hashed: hashed == _fake_bcrypt.hashpw(password, b"")
sys.modules.setdefault("bcrypt", _fake_bcrypt)

_fake_jwt = types.ModuleType("jwt")
_fake_jwt_store = {}
_fake_jwt_counter = {"value": 0}


class _ExpiredSignatureError(Exception):
    pass


class _InvalidTokenError(Exception):
    pass


def _jwt_encode(payload, secret, algorithm):
    _fake_jwt_counter["value"] += 1
    token = f"unit-token-{_fake_jwt_counter['value']}"
    stored = {}
    for key, value in payload.items():
        stored[key] = int(value.timestamp()) if isinstance(value, datetime) else value
    _fake_jwt_store[token] = stored
    return token


def _jwt_decode(token, secret, algorithms):
    if token not in _fake_jwt_store:
        raise _InvalidTokenError()
    return deepcopy(_fake_jwt_store[token])


_fake_jwt.encode = _jwt_encode
_fake_jwt.decode = _jwt_decode
_fake_jwt.ExpiredSignatureError = _ExpiredSignatureError
_fake_jwt.InvalidTokenError = _InvalidTokenError
sys.modules.setdefault("jwt", _fake_jwt)

import auth


class _Users:
    def __init__(self, user):
        self.user = dict(user)

    async def find_one(self, query):
        if query.get("id") != self.user.get("id"):
            return None
        return dict(self.user)


class _DB:
    def __init__(self, user):
        self.users = _Users(user)


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "unit-test-session-revocation-secret")


def _request(token: str) -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/api/auth/me",
        "raw_path": b"/api/auth/me",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    })


def _cookie_request(name: str, token: str, path: str = "/api/auth/refresh") -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"cookie", f"{name}={token}".encode())],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    })


def _token(*, issued_at: datetime | None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "user-1",
        "email": "viewer@example.com",
        "mfa": True,
        "exp": now + timedelta(minutes=5),
        "type": "access",
    }
    if issued_at is not None:
        payload["iat"] = issued_at
    return auth.jwt.encode(
        payload,
        auth.get_jwt_secret(),
        algorithm=auth.JWT_ALGORITHM,
    )


def _user(changed_at: datetime) -> dict:
    return {
        "id": "user-1",
        "email": "viewer@example.com",
        "role": "viewer",
        "password_hash": "must-not-leak",
        "password_updated_at": changed_at.isoformat(),
    }


def test_access_and_refresh_tokens_include_issued_at():
    access = auth.jwt.decode(
        auth.create_access_token("user-1", "viewer@example.com", mfa_verified=True),
        auth.get_jwt_secret(),
        algorithms=[auth.JWT_ALGORITHM],
    )
    refresh = auth.jwt.decode(
        auth.create_refresh_token("user-1", mfa_verified=True),
        auth.get_jwt_secret(),
        algorithms=[auth.JWT_ALGORITHM],
    )

    assert isinstance(access["iat"], int)
    assert isinstance(refresh["iat"], int)
    assert access["type"] == "access"
    assert refresh["type"] == "refresh"


def test_refresh_token_and_cookie_contract_is_thirty_days():
    assert auth.REFRESH_TOKEN_TTL == timedelta(days=30)
    assert auth.REFRESH_COOKIE_MAX_AGE_SECONDS == 60 * 60 * 24 * 30


@pytest.mark.asyncio
async def test_refresh_rotates_both_cookies_and_preserves_verified_mfa():
    user = {
        "id": "user-1",
        "email": "viewer@example.com",
        "role": "viewer",
    }
    old_refresh = auth.create_refresh_token("user-1", mfa_verified=True)
    response = JSONResponse({"placeholder": True})

    result = await auth.refresh_browser_session(
        _cookie_request("refresh_token", old_refresh),
        response,
        _DB(user),
    )

    assert result == {"ok": True}
    cookies = response.headers.getlist("set-cookie")
    assert any("access_token=" in value and "Max-Age=43200" in value for value in cookies)
    assert any("refresh_token=" in value and "Max-Age=2592000" in value for value in cookies)


@pytest.mark.asyncio
async def test_refresh_rechecks_password_revocation():
    issued = datetime.now(timezone.utc) - timedelta(minutes=5)
    changed = datetime.now(timezone.utc)
    user = _user(changed)
    token = auth.jwt.encode(
        {
            "sub": "user-1",
            "mfa": True,
            "iat": issued,
            "exp": issued + timedelta(days=30),
            "type": "refresh",
        },
        auth.get_jwt_secret(),
        algorithm=auth.JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException) as exc:
        await auth.refresh_browser_session(
            _cookie_request("refresh_token", token),
            JSONResponse({"placeholder": True}),
            _DB(user),
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "Session revoked"


@pytest.mark.asyncio
async def test_refresh_rechecks_account_disabled_state():
    user = {
        "id": "user-1",
        "email": "viewer@example.com",
        "role": "viewer",
        "disabled": True,
    }
    token = auth.create_refresh_token("user-1", mfa_verified=True)

    with pytest.raises(HTTPException) as exc:
        await auth.refresh_browser_session(
            _cookie_request("refresh_token", token),
            JSONResponse({"placeholder": True}),
            _DB(user),
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "Account disabled"


@pytest.mark.asyncio
async def test_token_issued_before_password_change_is_revoked():
    changed = datetime.now(timezone.utc)
    token = _token(issued_at=changed - timedelta(seconds=30))

    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user_from_db(_request(token), _DB(_user(changed)))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Session revoked"


@pytest.mark.asyncio
async def test_legacy_token_without_iat_is_revoked_after_password_change():
    changed = datetime.now(timezone.utc)

    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user_from_db(
            _request(_token(issued_at=None)),
            _DB(_user(changed)),
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "Session revoked"


@pytest.mark.asyncio
async def test_token_issued_after_password_change_is_accepted():
    changed = datetime.now(timezone.utc) - timedelta(seconds=30)
    token = _token(issued_at=changed + timedelta(seconds=1))

    user = await auth.get_current_user_from_db(_request(token), _DB(_user(changed)))

    assert user["id"] == "user-1"
    assert "password_hash" not in user


def test_bcrypt_secret_limit_is_utf8_byte_based():
    with pytest.raises(ValueError):
        auth.hash_password("🔐" * 19)  # 76 UTF-8 bytes

    hashed = auth.hash_password("StrongPassword-2026")
    assert auth.verify_password("StrongPassword-2026", hashed)
