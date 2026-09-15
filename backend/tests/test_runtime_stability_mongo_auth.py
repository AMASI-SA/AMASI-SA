from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pymongo.errors import (
    NetworkTimeout,
    ServerSelectionTimeoutError,
    WaitQueueTimeoutError,
)

import auth
from integrations.qoyod import worker as legacy_worker
from integrations.qoyod_manual import auto_send
from mobile_app_permissions import MOBILE_APP_PERMISSIONS, mobile_app_access_for_user
from runtime_mongo import bounded_readiness, main_client_options


def test_main_mongo_client_has_conservative_bounds_and_keeps_metrics_listener():
    listener = object()
    options = main_client_options(event_listener=listener)

    assert options == {
        "maxPoolSize": 20,
        "minPoolSize": 0,
        "serverSelectionTimeoutMS": 3_000,
        "connectTimeoutMS": 3_000,
        "waitQueueTimeoutMS": 2_000,
        "maxIdleTimeMS": 60_000,
        "event_listeners": [listener],
    }
    assert "socketTimeoutMS" not in options


class _Admin:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def command(self, name):
        assert name == "ping"
        if self.error:
            raise self.error
        if self.result == "hang":
            await asyncio.Future()
        return {"ok": 1}


@pytest.mark.asyncio
async def test_readiness_is_bounded_when_mongo_never_answers():
    started = time.monotonic()
    ready = await bounded_readiness(
        SimpleNamespace(admin=_Admin(result="hang")),
        timeout_seconds=0.02,
    )
    elapsed = time.monotonic() - started

    assert ready is False
    assert elapsed < 0.25


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    ServerSelectionTimeoutError("selection timeout"),
    WaitQueueTimeoutError("checkout timeout"),
    NetworkTimeout("network timeout"),
])
async def test_readiness_fails_closed_for_driver_pressure(error):
    ready = await bounded_readiness(
        SimpleNamespace(admin=_Admin(error=error)),
        timeout_seconds=0.1,
    )
    assert ready is False


class _Request:
    headers = {"Authorization": "Bearer opaque"}
    cookies = {}
    url = SimpleNamespace(path="/api/auth/me")


class _Users:
    def __init__(self, *, row=None, error=None):
        self.row = row
        self.error = error
        self.reads = 0

    async def find_one(self, _query):
        self.reads += 1
        if self.error:
            raise self.error
        return dict(self.row) if self.row else None


class _AuthDb:
    def __init__(self, users):
        self.users = users


@pytest.mark.asyncio
async def test_mongo_unavailable_is_503_not_fake_logout(monkeypatch):
    monkeypatch.setattr(auth.jwt, "decode", lambda *_args, **_kwargs: {
        "type": "access",
        "sub": "owner-1",
        "mfa": True,
    })
    users = _Users(error=ServerSelectionTimeoutError("mongo unavailable"))

    with pytest.raises(HTTPException) as caught:
        await auth.get_current_user_from_db(_Request(), _AuthDb(users))

    assert caught.value.status_code == 503
    assert caught.value.detail == {
        "code": "auth_dependency_unavailable",
        "retryable": True,
    }
    assert caught.value.headers == {"Retry-After": "2"}
    assert users.reads == 1


@pytest.mark.asyncio
async def test_refresh_mongo_failure_keeps_session_cookies(monkeypatch):
    monkeypatch.setattr(auth.jwt, "decode", lambda *_args, **_kwargs: {
        "type": "refresh",
        "sub": "owner-1",
        "mfa": True,
    })
    users = _Users(error=WaitQueueTimeoutError("pool exhausted"))

    class _RefreshRequest:
        cookies = {"refresh_token": "opaque"}

    class _Response:
        deleted = []

        def delete_cookie(self, **kwargs):
            self.deleted.append(kwargs)

    response = _Response()
    with pytest.raises(HTTPException) as caught:
        await auth.refresh_browser_session(
            _RefreshRequest(),
            response,
            _AuthDb(users),
        )

    assert caught.value.status_code == 503
    assert response.deleted == []


@pytest.mark.asyncio
async def test_missing_disabled_and_revoked_sessions_remain_401(monkeypatch):
    monkeypatch.setattr(auth.jwt, "decode", lambda *_args, **_kwargs: {
        "type": "access",
        "sub": "owner-1",
        "mfa": True,
        "iat": 10,
    })
    cases = [
        None,
        {"id": "owner-1", "role": "owner", "disabled": True},
        {
            "id": "owner-1",
            "email": "owner@example.invalid",
            "role": "owner",
            "password_updated_at": "2026-01-01T00:00:00+00:00",
        },
    ]
    for row in cases:
        with pytest.raises(HTTPException) as caught:
            await auth.get_current_user_from_db(_Request(), _AuthDb(_Users(row=row)))
        assert caught.value.status_code == 401


class _NoLookupDb:
    def __getitem__(self, _name):
        raise AssertionError("owner mobile access must not query Mongo")


@pytest.mark.asyncio
async def test_owner_mobile_access_is_catalogue_derived_without_second_db_read():
    result = await mobile_app_access_for_user(
        _NoLookupDb(),
        {"id": "owner-1", "role": " owner ", "is_owner": True},
    )

    assert result["owner_override"] is True
    assert result["enabled"] is True
    assert set(result["permissions"]) == set(MOBILE_APP_PERMISSIONS)


def test_worker_polling_is_adaptive_without_changing_send_gates():
    assert legacy_worker._next_poll_delay(
        {"status": "legacy_pipeline_frozen"}, interval_sec=5,
    ) == 300
    assert legacy_worker._next_poll_delay({}, interval_sec=5) == 5

    assert auto_send._next_poll_delay(
        {"status": "unified_auto_rollout_disabled"}, interval_sec=15,
    ) == 300
    assert auto_send._next_poll_delay(
        {"status": "not_armed"}, interval_sec=15,
    ) == 300
    assert auto_send._next_poll_delay(
        {"status": "idle"}, interval_sec=15,
    ) == 60
    assert auto_send._next_poll_delay(
        {"status": "succeeded", "candidate_count": 3}, interval_sec=15,
    ) == 15


def test_five_minute_poll_benchmark_contract():
    assert 300 // 5 == 60
    assert 300 // int(legacy_worker._next_poll_delay(
        {"status": "legacy_pipeline_frozen"}, interval_sec=5,
    )) == 1
    assert 300 // 15 == 20
    assert 300 // int(auto_send._next_poll_delay(
        {"status": "idle"}, interval_sec=15,
    )) == 5
    assert 300 // int(auto_send._next_poll_delay(
        {"status": "not_armed"}, interval_sec=15,
    )) == 1

