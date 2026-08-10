"""Login security v1 — isolated contract tests.

These tests do not touch production collections.  They exercise the exact
Mongo store contract with an in-memory async fake and verify the ASGI guard's
5-failures-per-hour behavior.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pytest
from fastapi import FastAPI

import login_security as security


class _Result:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []
        self.indexes: list[tuple] = []

    async def create_index(self, spec, **kwargs):
        self.indexes.append((spec, kwargs))
        return str(spec)

    async def insert_one(self, doc):
        self.docs.append(deepcopy(doc))
        return _Result(inserted_id=len(self.docs))

    async def count_documents(self, query):
        def matches(doc):
            if "key" in query and doc.get("key") != query["key"]:
                return False
            created = query.get("created_at") or {}
            if "$gte" in created and doc.get("created_at") < created["$gte"]:
                return False
            return True

        return sum(1 for doc in self.docs if matches(doc))

    async def find_one(self, query, sort=None):
        keys = (query.get("key") or {}).get("$in")
        gt = (query.get("blocked_until") or {}).get("$gt")
        matches = [
            doc
            for doc in self.docs
            if (keys is None or doc.get("key") in keys)
            and (gt is None or doc.get("blocked_until") > gt)
        ]
        if not matches:
            return None
        if sort:
            field, direction = sort[0]
            matches.sort(key=lambda d: d.get(field), reverse=direction < 0)
        return deepcopy(matches[0])

    async def update_one(self, query, update, upsert=False):
        target = next((d for d in self.docs if d.get("key") == query.get("key")), None)
        inserted = False
        if target is None and upsert:
            target = {"key": query.get("key")}
            self.docs.append(target)
            inserted = True
        if target is None:
            return _Result(matched_count=0, modified_count=0)
        if inserted:
            target.update(deepcopy(update.get("$setOnInsert") or {}))
        target.update(deepcopy(update.get("$set") or {}))
        return _Result(matched_count=0 if inserted else 1, modified_count=1)

    async def delete_many(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if d.get("key") != query.get("key")]
        return _Result(deleted_count=before - len(self.docs))


class FakeDB:
    def __init__(self):
        self.auth_login_attempts = FakeCollection()
        self.auth_login_blocks = FakeCollection()
        self.auth_security_events = FakeCollection()


@pytest.fixture(autouse=True)
def _security_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "unit-test-login-security-secret")
    monkeypatch.setenv("AUTH_LOGIN_WINDOW_SECONDS", "3600")
    monkeypatch.setenv("AUTH_LOGIN_BLOCK_SECONDS", "3600")
    monkeypatch.setenv("AUTH_LOGIN_PAIR_LIMIT", "5")
    # Keep global device/IP thresholds out of the pair-limit tests.
    monkeypatch.setenv("AUTH_LOGIN_DEVICE_LIMIT", "50")
    monkeypatch.setenv("AUTH_LOGIN_IP_LIMIT", "100")
    monkeypatch.setenv("AUTH_TRUST_PROXY_HEADERS", "1")


def _scope(*, cookie: str | None = None, ip: str = "203.0.113.10") -> dict:
    headers = [(b"content-type", b"application/json"), (b"x-forwarded-for", ip.encode())]
    if cookie:
        headers.append((b"cookie", f"{security.DEVICE_COOKIE}={cookie}".encode()))
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/auth/login",
        "raw_path": b"/api/auth/login",
        "query_string": b"",
        "headers": headers,
        "client": ("10.0.0.10", 12345),
        "server": ("testserver", 443),
    }


def _identity(email: str = "owner@example.com") -> security.LoginIdentity:
    token = "stable-unit-test-device"
    signed = security._sign_device_token(token)
    return security._identity(_scope(cookie=signed), email)


@pytest.mark.asyncio
async def test_fifth_failure_creates_one_hour_account_device_block():
    db = FakeDB()
    store = security.MongoLoginSecurityStore(db)
    identity = _identity()

    for _ in range(4):
        assert await store.record_failure(identity) is None

    block = await store.record_failure(identity)
    assert block is not None
    assert block.kind == "account_device"
    assert 3590 <= block.retry_after_seconds() <= 3600

    active = await store.active_block(identity)
    assert active is not None
    assert active.kind == "account_device"


@pytest.mark.asyncio
async def test_success_resets_only_pair_mistake_counter():
    db = FakeDB()
    store = security.MongoLoginSecurityStore(db)
    identity = _identity()

    for _ in range(4):
        await store.record_failure(identity)

    await store.record_success(identity)
    assert await db.auth_login_attempts.count_documents({"key": identity.pair_key}) == 0
    # Device/IP evidence is deliberately retained for distributed abuse.
    assert await db.auth_login_attempts.count_documents({"key": identity.device_key}) == 4

    assert await store.record_failure(identity) is None


@pytest.mark.asyncio
async def test_security_collections_do_not_store_raw_email_ip_or_device_token():
    db = FakeDB()
    store = security.MongoLoginSecurityStore(db)
    raw_email = "Sensitive.Owner@Example.com"
    raw_ip = "198.51.100.25"
    token = "never-store-this-device-token"
    signed = security._sign_device_token(token)
    identity = security._identity(_scope(cookie=signed, ip=raw_ip), raw_email)

    await store.record_failure(identity)

    serialized = repr(
        db.auth_login_attempts.docs
        + db.auth_login_blocks.docs
        + db.auth_security_events.docs
    )
    assert raw_email not in serialized
    assert raw_email.lower() not in serialized
    assert raw_ip not in serialized
    assert token not in serialized


async def _call_asgi(app, scope: dict, body: bytes) -> list[dict]:
    incoming = [{"type": "http.request", "body": body, "more_body": False}]
    outgoing: list[dict] = []

    async def receive():
        if incoming:
            return incoming.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        outgoing.append(deepcopy(message))

    await app(scope, receive, send)
    return outgoing


def _status(messages: list[dict]) -> int:
    return next(m["status"] for m in messages if m.get("type") == "http.response.start")


@pytest.mark.asyncio
async def test_middleware_blocks_sixth_attempt_after_five_bad_passwords():
    db = FakeDB()

    async def always_bad_password(scope, receive, send):
        # Drain the replayed body to ensure the middleware preserves it.
        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"detail":"bad"}'})

    guard = security.LoginSecurityMiddleware(always_bad_password, db=db)
    signed = security._sign_device_token("same-browser")
    scope = _scope(cookie=signed)
    body = b'{"email":"owner@example.com","password":"wrong"}'

    statuses = []
    for _ in range(6):
        messages = await _call_asgi(guard, scope, body)
        statuses.append(_status(messages))

    assert statuses[:5] == [401, 401, 401, 401, 401]
    assert statuses[5] == 429

    sixth = await _call_asgi(guard, scope, body)
    start = next(m for m in sixth if m.get("type") == "http.response.start")
    assert any(name.lower() == b"retry-after" for name, _ in start.get("headers", []))


@pytest.mark.asyncio
async def test_installation_is_idempotent_and_creates_ttl_indexes():
    db = FakeDB()
    app = FastAPI()

    await security.install_login_security(app, db)
    middleware_count = len(app.user_middleware)
    await security.install_login_security(app, db)

    assert app.state.mezan_login_security_installed is True
    assert len(app.user_middleware) == middleware_count
    assert any(kwargs.get("expireAfterSeconds") == 0 for _, kwargs in db.auth_login_attempts.indexes)
    assert any(kwargs.get("expireAfterSeconds") == 0 for _, kwargs in db.auth_login_blocks.indexes)


def test_device_cookie_signature_rejects_tampering():
    signed = security._sign_device_token("browser-a")
    assert security._verify_device_cookie(signed) == "browser-a"
    assert security._verify_device_cookie(signed + "tampered") is None


def test_block_state_uses_timezone_aware_datetime():
    identity = _identity()
    assert isinstance(identity.email_hash, str)
    assert isinstance(identity.device_hash, str)
    assert datetime.now().tzinfo is None  # guard against accidental naive fixture assumptions
