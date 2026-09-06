"""Distributed login-abuse protection for Mezan.

The guard is installed at application startup by ``auth.seed_admin`` and runs
as an ASGI middleware around the existing ``POST /api/auth/login`` route.  It
is intentionally isolated from order/accounting/ads code.

Policy (defaults, configurable via environment):
- 5 failed attempts for the same account + browser device in 60 minutes
  -> block that pair for 60 minutes.
- 12 failed attempts from one signed browser device in 60 minutes
  -> block the device for 60 minutes.
- 30 failed attempts from one client IP in 60 minutes
  -> block the IP for 60 minutes.

Counters and blocks live in MongoDB, so the protection works across backend
instances and survives process restarts.  Raw email addresses, device tokens,
and IP addresses are never persisted in these security collections; keyed
HMAC digests are stored instead.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from typing import Any, Iterable

from starlette.middleware import Middleware
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

LOGIN_PATHS = {"/api/auth/login"}
DEVICE_COOKIE = "mezan_device_id"


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _window_seconds() -> int:
    return _env_int("AUTH_LOGIN_WINDOW_SECONDS", 60 * 60)


def _block_seconds() -> int:
    return _env_int("AUTH_LOGIN_BLOCK_SECONDS", 60 * 60)


def _pair_limit() -> int:
    return _env_int("AUTH_LOGIN_PAIR_LIMIT", 5)


def _device_limit() -> int:
    return _env_int("AUTH_LOGIN_DEVICE_LIMIT", 12)


def _ip_limit() -> int:
    return _env_int("AUTH_LOGIN_IP_LIMIT", 30)


def _event_retention_seconds() -> int:
    return _env_int("AUTH_SECURITY_EVENT_RETENTION_SECONDS", 90 * 24 * 60 * 60)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _secret() -> bytes:
    value = os.environ.get("JWT_SECRET", "")
    if not value:
        raise RuntimeError("JWT_SECRET is required for login security")
    return value.encode("utf-8")


def _digest(kind: str, value: str) -> str:
    """Return a non-reversible stable identifier scoped to this installation."""
    payload = f"{kind}:{value}".encode("utf-8", "ignore")
    return hmac.new(_secret(), payload, hashlib.sha256).hexdigest()


def _sign_device_token(token: str) -> str:
    signature = hmac.new(
        _secret(), f"device:{token}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{token}.{signature}"


def _verify_device_cookie(value: str | None) -> str | None:
    if not value or "." not in value:
        return None
    token, signature = value.rsplit(".", 1)
    if not token or len(token) > 128 or len(signature) != 64:
        return None
    expected = hmac.new(
        _secret(), f"device:{token}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return token


def _headers_dict(scope: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers") or []:
        name = raw_name.decode("latin-1").lower()
        # Keep the first proxy/security header.  Repeated ordinary headers do
        # not matter for this middleware.
        headers.setdefault(name, raw_value.decode("latin-1"))
    return headers


def _cookie_value(headers: dict[str, str], name: str) -> str | None:
    raw = headers.get("cookie", "")
    if not raw:
        return None
    try:
        jar = SimpleCookie()
        jar.load(raw)
        morsel = jar.get(name)
        return morsel.value if morsel else None
    except Exception:
        return None


def _client_ip(scope: dict[str, Any], headers: dict[str, str]) -> str | None:
    trust_proxy = os.environ.get("AUTH_TRUST_PROXY_HEADERS", "1").strip().lower()
    trust_proxy = trust_proxy not in {"0", "false", "no", "off"}
    if trust_proxy:
        for name in ("cf-connecting-ip", "x-real-ip"):
            value = (headers.get(name) or "").strip()
            if value:
                return value[:128]
        forwarded = (headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded[:128]
    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        value = str(client[0]).strip()
        return value[:128] if value else None
    return None


def _new_device_token() -> str:
    return secrets.token_urlsafe(24)


def _device_cookie_header(token: str) -> tuple[bytes, bytes]:
    # Use Starlette's cookie serializer so quoting and browser attributes stay
    # aligned with the existing auth cookies.
    response = Response()
    response.set_cookie(
        key=DEVICE_COOKIE,
        value=_sign_device_token(token),
        max_age=365 * 24 * 60 * 60,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )
    for name, value in response.raw_headers:
        if name.lower() == b"set-cookie":
            return name, value
    raise RuntimeError("failed to serialize device cookie")


@dataclass(frozen=True)
class LoginIdentity:
    email_hash: str
    device_hash: str
    ip_hash: str | None
    device_token: str

    @property
    def pair_key(self) -> str:
        return f"pair:{self.email_hash}:{self.device_hash}"

    @property
    def device_key(self) -> str:
        return f"device:{self.device_hash}"

    @property
    def ip_key(self) -> str | None:
        return f"ip:{self.ip_hash}" if self.ip_hash else None

    def active_keys(self) -> list[str]:
        keys = [self.pair_key, self.device_key]
        if self.ip_key:
            keys.append(self.ip_key)
        return keys


def _identity(scope: dict[str, Any], email: str) -> LoginIdentity:
    headers = _headers_dict(scope)
    raw_cookie = _cookie_value(headers, DEVICE_COOKIE)
    device_token = _verify_device_cookie(raw_cookie) or _new_device_token()
    ip = _client_ip(scope, headers)
    normalized_email = email.strip().lower()
    return LoginIdentity(
        email_hash=_digest("email", normalized_email),
        device_hash=_digest("device", device_token),
        ip_hash=_digest("ip", ip) if ip else None,
        device_token=device_token,
    )


@dataclass(frozen=True)
class BlockState:
    key: str
    kind: str
    blocked_until: datetime

    def retry_after_seconds(self, now: datetime | None = None) -> int:
        current = now or _now()
        return max(1, int(math.ceil((self.blocked_until - current).total_seconds())))


class MongoLoginSecurityStore:
    """Mongo-backed sliding-window failure counters and temporary blocks."""

    def __init__(self, db):
        self.db = db
        self.attempts = db.auth_login_attempts
        self.blocks = db.auth_login_blocks
        self.events = db.auth_security_events

    async def ensure_indexes(self) -> None:
        await self.attempts.create_index([("key", 1), ("created_at", 1)])
        await self.attempts.create_index("expires_at", expireAfterSeconds=0)
        await self.blocks.create_index("key", unique=True)
        await self.blocks.create_index("expires_at", expireAfterSeconds=0)
        await self.events.create_index([("created_at", -1)])
        await self.events.create_index("expires_at", expireAfterSeconds=0)

    async def active_block(self, identity: LoginIdentity) -> BlockState | None:
        now = _now()
        doc = await self.blocks.find_one(
            {"key": {"$in": identity.active_keys()}, "blocked_until": {"$gt": now}},
            sort=[("blocked_until", -1)],
        )
        if not doc:
            return None
        blocked_until = doc.get("blocked_until")
        if not isinstance(blocked_until, datetime):
            return None
        if blocked_until.tzinfo is None:
            blocked_until = blocked_until.replace(tzinfo=timezone.utc)
        return BlockState(
            key=str(doc.get("key") or ""),
            kind=str(doc.get("kind") or "unknown"),
            blocked_until=blocked_until,
        )

    async def _count_recent(self, key: str, since: datetime) -> int:
        return int(await self.attempts.count_documents({"key": key, "created_at": {"$gte": since}}))

    async def _insert_attempt(self, key: str, kind: str, now: datetime) -> None:
        await self.attempts.insert_one(
            {
                "key": key,
                "kind": kind,
                "created_at": now,
                "expires_at": now + timedelta(seconds=_window_seconds() + _block_seconds()),
            }
        )

    async def _activate_block(self, key: str, kind: str, now: datetime) -> BlockState:
        blocked_until = now + timedelta(seconds=_block_seconds())
        await self.blocks.update_one(
            {"key": key},
            {
                "$set": {
                    "key": key,
                    "kind": kind,
                    "blocked_until": blocked_until,
                    "expires_at": blocked_until,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return BlockState(key=key, kind=kind, blocked_until=blocked_until)

    async def _event(self, identity: LoginIdentity, event_type: str, **extra: Any) -> None:
        now = _now()
        doc = {
            "event_type": event_type,
            "email_hash": identity.email_hash,
            "device_hash": identity.device_hash,
            "ip_hash": identity.ip_hash,
            "created_at": now,
            "expires_at": now + timedelta(seconds=_event_retention_seconds()),
        }
        doc.update(extra)
        await self.events.insert_one(doc)

    async def record_failure(self, identity: LoginIdentity) -> BlockState | None:
        now = _now()
        since = now - timedelta(seconds=_window_seconds())
        counters: list[tuple[str, str, int]] = [
            (identity.pair_key, "account_device", _pair_limit()),
            (identity.device_key, "device", _device_limit()),
        ]
        if identity.ip_key:
            counters.append((identity.ip_key, "ip", _ip_limit()))

        # Each key gets its own event so a legitimate successful login can
        # clear the account+device failures without erasing global IP/device
        # abuse signals.
        for key, kind, _limit in counters:
            await self._insert_attempt(key, kind, now)

        triggered: list[BlockState] = []
        for key, kind, limit in counters:
            count = await self._count_recent(key, since)
            if count >= limit:
                triggered.append(await self._activate_block(key, kind, now))

        await self._event(identity, "login_failed")
        if triggered:
            # Prefer the longest block in case multiple thresholds fire at once.
            block = max(triggered, key=lambda item: item.blocked_until)
            await self._event(
                identity,
                "login_blocked",
                block_kind=block.kind,
                blocked_until=block.blocked_until,
            )
            return block
        return None

    async def record_success(self, identity: LoginIdentity) -> None:
        # A correct password resets only the account+device mistake counter.
        # Device/IP counters intentionally remain as abuse signals.
        await self.attempts.delete_many({"key": identity.pair_key})
        await self._event(identity, "login_succeeded")


async def _read_body(receive) -> tuple[bytes, list[dict[str, Any]]]:
    """Buffer one HTTP request body and return messages for deterministic replay."""
    chunks: list[bytes] = []
    messages: list[dict[str, Any]] = []
    while True:
        message = await receive()
        messages.append(message)
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


def _email_from_body(body: bytes) -> str | None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    email = payload.get("email")
    if not isinstance(email, str):
        return None
    email = email.strip()
    return email if email else None


class LoginSecurityMiddleware:
    """ASGI middleware that protects only the canonical Mezan login route."""

    def __init__(self, app, *, db):
        self.app = app
        self.store = MongoLoginSecurityStore(db)

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or str(scope.get("method") or "").upper() != "POST"
            or scope.get("path") not in LOGIN_PATHS
        ):
            await self.app(scope, receive, send)
            return

        body, messages = await _read_body(receive)
        email = _email_from_body(body)
        if not email:
            # Let FastAPI return its normal 4xx validation response.  Malformed
            # bodies are not counted as password failures.
            await self.app(scope, _replay_receive(messages), send)
            return

        try:
            identity = _identity(scope, email)
            block = await self.store.active_block(identity)
        except Exception:
            # Authentication itself must remain available during a diagnostics
            # collection/index failure.  The failure is loud in logs and the
            # ordinary password check still runs; AUTH_LOGIN_GUARD_FAIL_CLOSED
            # can be enabled for environments that prefer availability loss to
            # rate-limit bypass.
            logger.exception("login security pre-check failed")
            if os.environ.get("AUTH_LOGIN_GUARD_FAIL_CLOSED", "0").lower() in {"1", "true", "yes", "on"}:
                response = JSONResponse(
                    {"detail": "تعذر إكمال التحقق الأمني مؤقتاً. حاول مرة أخرى لاحقاً."},
                    status_code=503,
                )
                await response(scope, _replay_receive(messages), send)
                return
            await self.app(scope, _replay_receive(messages), send)
            return

        cookie_header = _device_cookie_header(identity.device_token)
        if block is not None:
            retry_after = block.retry_after_seconds()
            response = JSONResponse(
                {
                    "detail": "تم إيقاف محاولات تسجيل الدخول مؤقتاً لهذا الجهاز أو المصدر بسبب محاولات متكررة غير صحيحة. حاول لاحقاً.",
                    "code": "login_temporarily_blocked",
                    "retry_after_seconds": retry_after,
                },
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            response.raw_headers.append(cookie_header)
            await response(scope, _replay_receive(messages), send)
            return

        processed_start = False

        async def guarded_send(message: dict[str, Any]):
            nonlocal processed_start
            if message.get("type") == "http.response.start" and not processed_start:
                processed_start = True
                status = int(message.get("status") or 0)
                headers = list(message.get("headers") or [])
                headers.append(cookie_header)
                message = dict(message)
                message["headers"] = headers
                try:
                    if status == 401:
                        await self.store.record_failure(identity)
                    elif 200 <= status < 300:
                        await self.store.record_success(identity)
                except Exception:
                    # A logging/rate-limit write must never turn a correctly
                    # authenticated request into an application error.
                    logger.exception("login security outcome write failed")
            await send(message)

        await self.app(scope, _replay_receive(messages), guarded_send)


async def install_login_security(app, db, *, initialize_indexes: bool = True) -> None:
    """Install the guard once and prepare its distributed MongoDB indexes.

    ``seed_admin`` calls this during FastAPI startup.  At that point Starlette
    has already built its middleware stack for the lifespan request, so we add
    the middleware descriptor and rebuild the stack for subsequent HTTP
    requests.  Appending places this guard *inside* existing CORS middleware,
    ensuring browser clients can read 429/503 responses without weakening the
    configured CORS origin policy.
    """
    if getattr(app.state, "mezan_login_security_installed", False):
        return

    store = MongoLoginSecurityStore(db)
    # Independent web installation delegates index writes to the migration role.
    if initialize_indexes:
        await store.ensure_indexes()

    app.user_middleware.append(Middleware(LoginSecurityMiddleware, db=db))
    app.middleware_stack = app.build_middleware_stack()
    app.state.mezan_login_security_installed = True
    logger.info(
        "Mezan login security enabled: pair=%s/device=%s/ip=%s window=%ss block=%ss",
        _pair_limit(),
        _device_limit(),
        _ip_limit(),
        _window_seconds(),
        _block_seconds(),
    )
