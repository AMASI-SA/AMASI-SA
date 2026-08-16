from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

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
