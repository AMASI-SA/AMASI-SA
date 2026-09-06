"""Progressive account+device login lockouts for Mezan.

This guard sits outside the existing distributed login-security middleware and
adds a user-friendly escalation ladder for repeated wrong credentials on the
same account + signed Mezan device identity.

Policy (five wrong login responses per stage):
1. 5 minutes
2. 10 minutes
3. 1 hour
4. 5 hours
5. 1 day
6. 5 days
7. 30 days
8. hard lock for that account+device pair until it is explicitly cleared

A successful password step (HTTP 2xx from /api/auth/login, including MFA or
passkey challenge responses) clears the progressive history for that exact
account+device pair. Device-wide and IP-wide spray protection remain in the
legacy login-security guard and are intentionally separate.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ReturnDocument
from starlette.middleware import Middleware
from starlette.responses import JSONResponse

from login_security import (
    LOGIN_PATHS,
    LoginIdentity,
    _device_cookie_header,
    _email_from_body,
    _identity,
    _read_body,
    _replay_receive,
)

logger = logging.getLogger(__name__)

# Exact merchant-approved escalation ladder.
LOCK_LADDER_SECONDS = (
    5 * 60,
    10 * 60,
    60 * 60,
    5 * 60 * 60,
    24 * 60 * 60,
    5 * 24 * 60 * 60,
    30 * 24 * 60 * 60,
)
FAILURES_PER_STAGE = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event_retention_seconds() -> int:
    try:
        value = int(os.environ.get("AUTH_SECURITY_EVENT_RETENTION_SECONDS", str(90 * 24 * 60 * 60)))
    except (TypeError, ValueError):
        value = 90 * 24 * 60 * 60
    return max(24 * 60 * 60, value)


def _progressive_state_retention_seconds() -> int:
    try:
        value = int(os.environ.get("AUTH_PROGRESSIVE_STATE_RETENTION_SECONDS", str(90 * 24 * 60 * 60)))
    except (TypeError, ValueError):
        value = 90 * 24 * 60 * 60
    return max(30 * 24 * 60 * 60, value)


def lock_seconds_for_stage(stage_index: int) -> int | None:
    """Return the temporary block duration for a zero-based stage.

    Stages beyond the temporary ladder are represented by ``None`` and become
    hard-locked. Keeping this pure makes the merchant policy easy to regression
    test without a database.
    """
    if stage_index < 0:
        stage_index = 0
    if stage_index >= len(LOCK_LADDER_SECONDS):
        return None
    return int(LOCK_LADDER_SECONDS[stage_index])


def _duration_label(seconds: int) -> str:
    if seconds == 5 * 60:
        return "5 دقائق"
    if seconds == 10 * 60:
        return "10 دقائق"
    if seconds == 60 * 60:
        return "ساعة واحدة"
    if seconds == 5 * 60 * 60:
        return "5 ساعات"
    if seconds == 24 * 60 * 60:
        return "يوم واحد"
    if seconds == 5 * 24 * 60 * 60:
        return "5 أيام"
    if seconds == 30 * 24 * 60 * 60:
        return "30 يومًا"
    minutes = max(1, int(math.ceil(seconds / 60)))
    return f"{minutes} دقيقة"


@dataclass(frozen=True)
class ProgressiveBlockState:
    key: str
    stage: int
    blocked_until: datetime | None
    hard_locked: bool = False

    def retry_after_seconds(self, now: datetime | None = None) -> int | None:
        if self.hard_locked or self.blocked_until is None:
            return None
        current = now or _now()
        return max(1, int(math.ceil((self.blocked_until - current).total_seconds())))


class ProgressiveLoginStore:
    """Mongo-backed progressive penalty state scoped to account+device."""

    def __init__(self, db):
        self.db = db
        self.states = db.auth_login_progressive_locks
        self.events = db.auth_security_events

    async def ensure_indexes(self) -> None:
        await self.states.create_index("key", unique=True)
        await self.states.create_index("expires_at", expireAfterSeconds=0)
        await self.states.create_index([("updated_at", -1)])
        # Shared audit collection; index creation is idempotent.
        await self.events.create_index([("created_at", -1)])
        await self.events.create_index("expires_at", expireAfterSeconds=0)

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

    async def safe_event(self, identity: LoginIdentity, event_type: str, **extra: Any) -> None:
        try:
            await self._event(identity, event_type, **extra)
        except Exception:
            logger.exception("progressive login security event write failed: %s", event_type)

    async def active_block(self, identity: LoginIdentity) -> ProgressiveBlockState | None:
        now = _now()
        doc = await self.states.find_one({"key": identity.pair_key})
        if not doc:
            return None

        stage = int(doc.get("stage") or 0)
        if bool(doc.get("hard_locked")):
            return ProgressiveBlockState(
                key=identity.pair_key,
                stage=stage,
                blocked_until=None,
                hard_locked=True,
            )

        blocked_until = doc.get("blocked_until")
        if not isinstance(blocked_until, datetime):
            return None
        if blocked_until.tzinfo is None:
            blocked_until = blocked_until.replace(tzinfo=timezone.utc)
        if blocked_until <= now:
            return None
        return ProgressiveBlockState(
            key=identity.pair_key,
            stage=stage,
            blocked_until=blocked_until,
            hard_locked=False,
        )

    async def record_failure(self, identity: LoginIdentity) -> ProgressiveBlockState | None:
        now = _now()
        retention_until = now + timedelta(seconds=_progressive_state_retention_seconds())

        # Ignore blocked states here; the middleware prevents blocked requests
        # from reaching the password route. The filter is still defensive for
        # concurrent requests that began just before a block was activated.
        doc = await self.states.find_one_and_update(
            {
                "key": identity.pair_key,
                "hard_locked": {"$ne": True},
                "$or": [
                    {"blocked_until": {"$lte": now}},
                    {"blocked_until": None},
                    {"blocked_until": {"$exists": False}},
                ],
            },
            {
                "$inc": {"failures": 1},
                "$set": {
                    "updated_at": now,
                    "expires_at": retention_until,
                },
                "$setOnInsert": {
                    "key": identity.pair_key,
                    "stage": 0,
                    "created_at": now,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        # A concurrent request may race with block activation and no longer
        # match the update filter. Return the active state if one now exists.
        if not doc:
            return await self.active_block(identity)

        failures = int(doc.get("failures") or 0)
        stage = int(doc.get("stage") or 0)
        await self.safe_event(
            identity,
            "login_progressive_failure",
            stage=stage,
            failures_in_stage=failures,
            failures_per_stage=FAILURES_PER_STAGE,
        )
        if failures < FAILURES_PER_STAGE:
            return None

        duration = lock_seconds_for_stage(stage)
        if duration is None:
            result = await self.states.update_one(
                {
                    "key": identity.pair_key,
                    "hard_locked": {"$ne": True},
                    "stage": stage,
                    "failures": {"$gte": FAILURES_PER_STAGE},
                },
                {
                    "$set": {
                        "hard_locked": True,
                        "failures": 0,
                        "updated_at": now,
                        "hard_locked_at": now,
                    },
                    "$unset": {
                        "blocked_until": "",
                        "expires_at": "",
                    },
                },
            )
            if result.modified_count == 1:
                block = ProgressiveBlockState(
                    key=identity.pair_key,
                    stage=stage,
                    blocked_until=None,
                    hard_locked=True,
                )
                await self.safe_event(
                    identity,
                    "login_progressive_hard_locked",
                    stage=stage,
                )
                return block
            return await self.active_block(identity)

        blocked_until = now + timedelta(seconds=duration)
        expiry = blocked_until + timedelta(seconds=_progressive_state_retention_seconds())
        result = await self.states.update_one(
            {
                "key": identity.pair_key,
                "hard_locked": {"$ne": True},
                "stage": stage,
                "failures": {"$gte": FAILURES_PER_STAGE},
            },
            {
                "$set": {
                    "stage": stage + 1,
                    "failures": 0,
                    "blocked_until": blocked_until,
                    "updated_at": now,
                    "expires_at": expiry,
                }
            },
        )
        if result.modified_count == 1:
            block = ProgressiveBlockState(
                key=identity.pair_key,
                stage=stage + 1,
                blocked_until=blocked_until,
                hard_locked=False,
            )
            await self.safe_event(
                identity,
                "login_progressive_blocked",
                stage=stage + 1,
                block_seconds=duration,
                blocked_until=blocked_until,
            )
            return block
        return await self.active_block(identity)

    async def record_success(self, identity: LoginIdentity) -> None:
        deleted = await self.states.delete_one({"key": identity.pair_key})
        await self.safe_event(
            identity,
            "login_progressive_reset",
            previous_state_cleared=bool(deleted.deleted_count),
        )

    async def clear_pair(self, pair_key: str) -> bool:
        """Explicit administrative/recovery hook for future security UI."""
        result = await self.states.delete_one({"key": pair_key})
        return bool(result.deleted_count)


def _response_status(messages: list[dict[str, Any]]) -> int:
    for message in messages:
        if message.get("type") == "http.response.start":
            return int(message.get("status") or 0)
    return 0


async def _send_messages(messages: list[dict[str, Any]], send) -> None:
    for message in messages:
        await send(message)


def _block_response(block: ProgressiveBlockState, device_cookie_header) -> JSONResponse:
    if block.hard_locked:
        response = JSONResponse(
            {
                "detail": "تم قفل تسجيل الدخول لهذا الحساب على هذا الجهاز بعد تكرار عدد كبير من المحاولات غير الصحيحة. يلزم فك القفل أمنيًا قبل استخدام هذا الجهاز مرة أخرى.",
                "code": "login_device_hard_locked",
                "lock_stage": block.stage,
                "hard_locked": True,
            },
            status_code=423,
        )
        response.raw_headers.append(device_cookie_header)
        return response

    retry_after = block.retry_after_seconds() or 1
    duration_label = _duration_label(retry_after)
    response = JSONResponse(
        {
            "detail": f"تم إيقاف محاولات تسجيل الدخول مؤقتًا لمدة {duration_label} بعد 5 محاولات غير صحيحة. حاول بعد انتهاء المدة.",
            "code": "login_progressive_blocked",
            "retry_after_seconds": retry_after,
            "lock_stage": block.stage,
            "hard_locked": False,
        },
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )
    response.raw_headers.append(device_cookie_header)
    return response


class ProgressiveLoginSecurityMiddleware:
    def __init__(self, app, *, db):
        self.app = app
        self.store = ProgressiveLoginStore(db)

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or str(scope.get("method") or "").upper() != "POST"
            or scope.get("path") not in LOGIN_PATHS
        ):
            await self.app(scope, receive, send)
            return

        body, request_messages = await _read_body(receive)
        email = _email_from_body(body)
        if not email:
            await self.app(scope, _replay_receive(request_messages), send)
            return

        try:
            identity = _identity(scope, email)
            active = await self.store.active_block(identity)
        except Exception:
            logger.exception("progressive login security pre-check failed")
            # Preserve auth availability. The legacy distributed login guard is
            # still active underneath this middleware.
            await self.app(scope, _replay_receive(request_messages), send)
            return

        device_cookie_header = _device_cookie_header(identity.device_token)
        if active is not None:
            response = _block_response(active, device_cookie_header)
            await response(scope, _replay_receive(request_messages), send)
            return

        captured: list[dict[str, Any]] = []

        async def capture_send(message: dict[str, Any]):
            captured.append(dict(message))

        await self.app(scope, _replay_receive(request_messages), capture_send)
        status = _response_status(captured)

        replacement: JSONResponse | None = None
        try:
            if status == 401:
                activated = await self.store.record_failure(identity)
                # The fifth wrong request is still allowed to return its normal
                # 401. The *next* request sees the newly active lock, matching
                # the merchant rule "5 attempts, then lock".
                if activated is not None:
                    logger.info(
                        "progressive login stage activated: stage=%s hard=%s",
                        activated.stage,
                        activated.hard_locked,
                    )
            elif 200 <= status < 300:
                # 202 includes MFA/passkey challenges after a correct password.
                await self.store.record_success(identity)
        except Exception:
            logger.exception("progressive login security outcome write failed")

        if replacement is not None:
            await replacement(scope, _replay_receive(request_messages), send)
            return
        await _send_messages(captured, send)


async def install_progressive_login_security(app, db, *, initialize_indexes: bool = True) -> None:
    if getattr(app.state, "mezan_progressive_login_security_installed", False):
        return

    store = ProgressiveLoginStore(db)
    # Independent web installation delegates index writes to the migration role.
    if initialize_indexes:
        await store.ensure_indexes()
    app.user_middleware.append(Middleware(ProgressiveLoginSecurityMiddleware, db=db))
    app.middleware_stack = app.build_middleware_stack()
    app.state.mezan_progressive_login_security_installed = True
    logger.info(
        "Mezan progressive login lockout enabled: failures=%s ladder=%s hard-lock-after=%s stages",
        FAILURES_PER_STAGE,
        ",".join(str(item) for item in LOCK_LADDER_SECONDS),
        len(LOCK_LADDER_SECONDS) + 1,
    )
