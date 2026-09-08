"""Email OTP second factor for Mezan Admin and sensitive employee sign-in.

This middleware is intentionally installed *inside* the existing Owner TOTP
middleware. It replaces a successful password-only response with a short-lived
email challenge only when ``email_otp_policy.requires_email_otp`` says the
non-Owner account is sensitive. Owner authentication remains TOTP/passkey.

Security invariants:
- six random digits, valid for five minutes by default
- only an HMAC digest is stored; plaintext OTP is never persisted or logged
- one-time atomic consumption
- five verification attempts per challenge
- resend cooldown and bounded resend count
- password-only auth cookies are discarded while the challenge is pending
- SMTP is fail-closed at startup whenever EMAIL_OTP_ENABLED=1
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import math
import os
import secrets
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Iterable

import jwt
from pymongo import ReturnDocument
from starlette.middleware import Middleware
from starlette.responses import JSONResponse

from email_otp_policy import email_otp_enabled, requires_email_otp

logger = logging.getLogger(__name__)

LOGIN_PATH = "/api/auth/login"
VERIFY_PATH = "/api/auth/email-otp/verify"
RESEND_PATH = "/api/auth/email-otp/resend"
JWT_ALGORITHM = "HS256"
OTP_DIGITS = 6


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _challenge_seconds() -> int:
    return _env_int("EMAIL_OTP_TTL_SECONDS", 5 * 60)


def _max_attempts() -> int:
    return _env_int("EMAIL_OTP_MAX_ATTEMPTS", 5)


def _resend_cooldown_seconds() -> int:
    return _env_int("EMAIL_OTP_RESEND_COOLDOWN_SECONDS", 60)


def _max_resends() -> int:
    return _env_int("EMAIL_OTP_MAX_RESENDS", 5)


def _event_retention_seconds() -> int:
    return _env_int("AUTH_SECURITY_EVENT_RETENTION_SECONDS", 90 * 24 * 60 * 60)


def _jwt_secret() -> str:
    value = (os.environ.get("JWT_SECRET") or "").strip()
    if not value:
        raise RuntimeError("JWT_SECRET is required for email OTP")
    return value


def generate_otp() -> str:
    return str(secrets.randbelow(10**OTP_DIGITS)).zfill(OTP_DIGITS)


def otp_digest(jti: str, code: str) -> str:
    normalized = str(code or "").strip()
    return hmac.new(
        _jwt_secret().encode("utf-8"),
        f"mezan:email-otp:v1:{jti}:{normalized}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _challenge_token(*, user_id: str, jti: str, expires_at: datetime) -> str:
    now = _now()
    return jwt.encode(
        {
            "sub": user_id,
            "type": "email_otp_challenge",
            "jti": jti,
            "iat": now,
            "exp": expires_at,
        },
        _jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )


def _decode_challenge_token(token: str) -> dict[str, Any]:
    payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    if payload.get("type") != "email_otp_challenge":
        raise jwt.InvalidTokenError("invalid email OTP challenge type")
    if not payload.get("sub") or not payload.get("jti"):
        raise jwt.InvalidTokenError("incomplete email OTP challenge")
    return payload


def mask_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if "@" not in email:
        return "***"
    local, domain = email.rsplit("@", 1)
    if not local or not domain:
        return "***"
    visible = local[:1]
    return f"{visible}{'*' * max(3, min(8, len(local) - 1))}@{domain}"


def _valid_recipient(value: str) -> bool:
    email = str(value or "").strip()
    return (
        bool(email)
        and "@" in email
        and not email.startswith("@")
        and not email.endswith("@")
        and "\n" not in email
        and "\r" not in email
    )


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _safe_header(value: str, fallback: str) -> str:
    text = str(value or fallback).strip()
    if not text or "\n" in text or "\r" in text:
        return fallback
    return text[:160]


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    username: str
    password: str
    from_email: str
    from_name: str
    use_starttls: bool
    use_ssl: bool
    timeout_seconds: int


def smtp_settings() -> SmtpSettings:
    host = (os.environ.get("EMAIL_OTP_SMTP_HOST") or "").strip()
    username = (os.environ.get("EMAIL_OTP_SMTP_USERNAME") or "").strip()
    password = os.environ.get("EMAIL_OTP_SMTP_PASSWORD") or ""
    from_email = (os.environ.get("EMAIL_OTP_FROM_EMAIL") or username).strip()
    use_ssl = _truthy(os.environ.get("EMAIL_OTP_SMTP_SSL"), False)
    use_starttls = _truthy(os.environ.get("EMAIL_OTP_SMTP_STARTTLS"), not use_ssl)
    if use_ssl and use_starttls:
        raise RuntimeError("EMAIL_OTP_SMTP_SSL and STARTTLS cannot both be enabled")
    if not host:
        raise RuntimeError("EMAIL_OTP_SMTP_HOST is required when email OTP is enabled")
    if not _valid_recipient(from_email):
        raise RuntimeError("EMAIL_OTP_FROM_EMAIL must be a valid sender address")
    if bool(username) != bool(password):
        raise RuntimeError(
            "EMAIL_OTP_SMTP_USERNAME and EMAIL_OTP_SMTP_PASSWORD must be configured together"
        )
    return SmtpSettings(
        host=host,
        port=_env_int("EMAIL_OTP_SMTP_PORT", 465 if use_ssl else 587),
        username=username,
        password=password,
        from_email=from_email,
        from_name=_safe_header(os.environ.get("EMAIL_OTP_FROM_NAME") or "AMASI", "AMASI"),
        use_starttls=use_starttls,
        use_ssl=use_ssl,
        timeout_seconds=_env_int("EMAIL_OTP_SMTP_TIMEOUT_SECONDS", 12),
    )


def validate_email_otp_runtime() -> None:
    """Fail startup only when the operator explicitly enables email OTP."""
    if not email_otp_enabled():
        return
    _jwt_secret()
    smtp_settings()


def _send_email_sync(recipient: str, code: str, recipient_name: str | None = None) -> None:
    settings = smtp_settings()
    if not _valid_recipient(recipient):
        raise ValueError("invalid email OTP recipient")

    normalized_name = " ".join(str(recipient_name or "").split())[:80]
    greeting = f"مرحبًا {normalized_name}،" if normalized_name else "مرحبًا،"
    ttl_minutes = max(1, math.ceil(_challenge_seconds() / 60))

    message = EmailMessage()
    message["Subject"] = "رمز التحقق لتسجيل الدخول إلى نظام أماسي"
    message["From"] = f"{settings.from_name} <{settings.from_email}>"
    message["To"] = recipient
    message.set_content(
        f"{greeting}\n\n"
        + f"رمز التحقق الخاص بك لتسجيل الدخول إلى نظام أماسي هو: {code}\n\n"
        f"الرمز صالح لمدة {ttl_minutes} دقائق، "
        "ويعمل مرة واحدة فقط.\n"
        "إذا لم تحاول تسجيل الدخول إلى نظام أماسي فتجاهل هذه الرسالة ولا تشارك الرمز مع أي شخص.\n\n"
        "AMASI"
    )
    message.add_alternative(
        f"""<!doctype html>
<html lang="ar" dir="rtl">
  <body dir="rtl" style="margin:0; padding:24px; background-color:#ffffff; direction:rtl; text-align:right; font-family:Arial, Tahoma, sans-serif; color:#111827;">
    <table role="presentation" width="100%" dir="rtl" cellspacing="0" cellpadding="0" border="0" style="width:100%; direction:rtl; text-align:right;">
      <tr>
        <td dir="rtl" align="right" style="direction:rtl; text-align:right; font-size:16px; line-height:1.8;">
          <p dir="rtl" style="margin:0 0 16px; direction:rtl; text-align:right;">{html.escape(greeting)}</p>
          <p dir="rtl" style="margin:0 0 16px; direction:rtl; text-align:right;">
            رمز التحقق الخاص بك لتسجيل الدخول إلى نظام أماسي هو:
            <strong dir="ltr" style="display:inline-block; direction:ltr; unicode-bidi:isolate;">{html.escape(code)}</strong>
          </p>
          <p dir="rtl" style="margin:0 0 8px; direction:rtl; text-align:right;">الرمز صالح لمدة {ttl_minutes} دقائق، ويعمل مرة واحدة فقط.</p>
          <p dir="rtl" style="margin:0 0 24px; direction:rtl; text-align:right;">إذا لم تحاول تسجيل الدخول إلى نظام أماسي فتجاهل هذه الرسالة ولا تشارك الرمز مع أي شخص.</p>
          <p dir="ltr" style="margin:0; direction:ltr; text-align:right;">AMASI</p>
        </td>
      </tr>
    </table>
  </body>
</html>""",
        subtype="html",
    )

    if settings.use_ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.host,
            settings.port,
            timeout=settings.timeout_seconds,
            context=ssl.create_default_context(),
        )
    else:
        server = smtplib.SMTP(
            settings.host,
            settings.port,
            timeout=settings.timeout_seconds,
        )
    try:
        server.ehlo()
        if settings.use_starttls:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if settings.username:
            server.login(settings.username, settings.password)
        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception:
            server.close()


async def send_otp_email(recipient: str, code: str, recipient_name: str | None = None) -> None:
    await asyncio.to_thread(_send_email_sync, recipient, code, recipient_name)


class EmailOtpChallengeStore:
    def __init__(self, db: Any):
        self.db = db
        self.challenges = db.auth_email_otp_challenges
        self.events = db.auth_security_events

    async def ensure_indexes(self) -> None:
        await self.challenges.create_index("jti", unique=True)
        await self.challenges.create_index([("user_id", 1), ("expires_at", -1)])
        await self.challenges.create_index("expires_at", expireAfterSeconds=0)
        await self.events.create_index([("created_at", -1)])
        await self.events.create_index("expires_at", expireAfterSeconds=0)

    async def safe_event(self, event_type: str, user: dict[str, Any], **extra: Any) -> None:
        try:
            now = _now()
            await self.events.insert_one(
                {
                    "event_type": event_type,
                    "user_id": user.get("id"),
                    "role": str(user.get("role") or "").strip().lower(),
                    "created_at": now,
                    "expires_at": now + timedelta(seconds=_event_retention_seconds()),
                    **extra,
                }
            )
        except Exception:
            logger.exception("Email OTP security event write failed: %s", event_type)

    async def recent_active(self, user_id: str) -> dict[str, Any] | None:
        return await self.challenges.find_one(
            {
                "user_id": user_id,
                "expires_at": {"$gt": _now()},
                "failures": {"$lt": _max_attempts()},
            },
            {"_id": 0},
            sort=[("sent_at", -1)],
        )

    async def issue_for_login(
        self, user: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        """Return an active challenge and a plaintext code only when mail is due."""
        now = _now()
        existing = await self.recent_active(str(user["id"]))
        if existing:
            sent_at = _as_utc(existing.get("sent_at"))
            if sent_at and (now - sent_at).total_seconds() < _resend_cooldown_seconds():
                return existing, None

        jti = uuid.uuid4().hex
        code = generate_otp()
        expires_at = now + timedelta(seconds=_challenge_seconds())
        doc = {
            "jti": jti,
            "user_id": str(user["id"]),
            "otp_hash": otp_digest(jti, code),
            "failures": 0,
            "resend_count": 0,
            "created_at": now,
            "sent_at": now,
            "expires_at": expires_at,
        }
        await self.challenges.delete_many({"user_id": str(user["id"])})
        await self.challenges.insert_one(dict(doc))
        return doc, code

    async def resolve(self, token: str) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = _decode_challenge_token(token)
        doc = await self.challenges.find_one(
            {
                "jti": payload["jti"],
                "user_id": payload["sub"],
                "expires_at": {"$gt": _now()},
                "failures": {"$lt": _max_attempts()},
            },
            {"_id": 0},
        )
        if not doc:
            raise jwt.InvalidTokenError("email OTP challenge expired or consumed")
        return payload, doc

    async def fail(self, jti: str) -> int:
        doc = await self.challenges.find_one_and_update(
            {"jti": jti},
            {"$inc": {"failures": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            return 0
        remaining = max(0, _max_attempts() - int(doc.get("failures") or 0))
        if remaining <= 0:
            await self.challenges.delete_one({"jti": jti})
        return remaining

    async def consume_matching(
        self, *, jti: str, user_id: str, digest: str
    ) -> dict[str, Any] | None:
        return await self.challenges.find_one_and_delete(
            {
                "jti": jti,
                "user_id": user_id,
                "otp_hash": digest,
                "expires_at": {"$gt": _now()},
                "failures": {"$lt": _max_attempts()},
            }
        )

    async def consume(self, jti: str) -> None:
        await self.challenges.delete_one({"jti": jti})

    async def prepare_resend(
        self, token: str
    ) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        payload, doc = await self.resolve(token)
        now = _now()
        sent_at = _as_utc(doc.get("sent_at")) or now
        elapsed = max(0.0, (now - sent_at).total_seconds())
        if elapsed < _resend_cooldown_seconds():
            retry_after = max(1, math.ceil(_resend_cooldown_seconds() - elapsed))
            raise EmailOtpCooldown(retry_after)
        if int(doc.get("resend_count") or 0) >= _max_resends():
            raise EmailOtpResendLimit()

        code = generate_otp()
        expires_at = now + timedelta(seconds=_challenge_seconds())
        updated = await self.challenges.find_one_and_update(
            {
                "jti": payload["jti"],
                "user_id": payload["sub"],
                "failures": {"$lt": _max_attempts()},
            },
            {
                "$set": {
                    "otp_hash": otp_digest(payload["jti"], code),
                    "sent_at": now,
                    "expires_at": expires_at,
                },
                "$inc": {"resend_count": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            raise jwt.InvalidTokenError("email OTP challenge unavailable")
        new_token = _challenge_token(
            user_id=payload["sub"],
            jti=payload["jti"],
            expires_at=expires_at,
        )
        return payload, updated, code, new_token


class EmailOtpCooldown(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = max(1, int(retry_after))
        super().__init__(str(self.retry_after))


class EmailOtpResendLimit(Exception):
    pass


async def _read_body(receive) -> tuple[bytes, list[dict[str, Any]]]:
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


def _json_object(body: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


async def _send_messages(messages: list[dict[str, Any]], send) -> None:
    for message in messages:
        await send(message)


def _response_status(messages: list[dict[str, Any]]) -> int:
    for message in messages:
        if message.get("type") == "http.response.start":
            return int(message.get("status") or 0)
    return 0


def _clear_auth_on_response(response: JSONResponse) -> None:
    from auth import clear_auth_cookies

    clear_auth_cookies(response)


def _session_response(user: dict[str, Any]) -> JSONResponse:
    from auth import create_access_token, create_refresh_token, set_auth_cookies

    access = create_access_token(user["id"], user["email"], mfa_verified=True)
    refresh = create_refresh_token(user["id"], mfa_verified=True)
    response = JSONResponse(
        {
            "ok": True,
            "mfa_verified": True,
            "mfa_channel": "email",
            "id": user["id"],
            "name": user.get("name"),
            "email": user.get("email"),
            "role": user.get("role"),
            "access_token": access,
        },
        status_code=200,
    )
    set_auth_cookies(response, access, refresh)
    return response


def _challenge_payload(doc: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    expires_at = _as_utc(doc.get("expires_at")) or (
        _now() + timedelta(seconds=_challenge_seconds())
    )
    sent_at = _as_utc(doc.get("sent_at")) or _now()
    remaining_cooldown = max(
        0,
        math.ceil(
            _resend_cooldown_seconds()
            - max(0.0, (_now() - sent_at).total_seconds())
        ),
    )
    return {
        "mfa_required": True,
        "mfa_channel": "email",
        "challenge_token": _challenge_token(
            user_id=str(user["id"]),
            jti=str(doc["jti"]),
            expires_at=expires_at,
        ),
        "masked_email": mask_email(str(user.get("email") or "")),
        "expires_in_seconds": max(
            1, math.ceil((expires_at - _now()).total_seconds())
        ),
        "resend_after_seconds": remaining_cooldown,
        "message": "أرسلنا رمز تحقق من 6 أرقام إلى بريد الحساب لإكمال تسجيل الدخول.",
    }


class EmailOtpSecurityMiddleware:
    def __init__(self, app, *, db: Any):
        self.app = app
        self.db = db
        self.store = EmailOtpChallengeStore(db)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "").upper()
        path = str(scope.get("path") or "")
        if method == "POST" and path == VERIFY_PATH:
            await self._verify(scope, receive, send)
            return
        if method == "POST" and path == RESEND_PATH:
            await self._resend(scope, receive, send)
            return
        if method == "POST" and path == LOGIN_PATH:
            await self._login(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _login(self, scope, receive, send) -> None:
        if not email_otp_enabled():
            await self.app(scope, receive, send)
            return

        body, messages = await _read_body(receive)
        payload = _json_object(body) or {}
        email = str(payload.get("email") or "").strip().lower()
        if not email:
            await self.app(scope, _replay_receive(messages), send)
            return

        captured: list[dict[str, Any]] = []

        async def capture_send(message: dict[str, Any]):
            captured.append(dict(message))

        # Let the canonical FastAPI route prove the password first. A bad
        # password is passed through unchanged and reveals no OTP policy state.
        await self.app(scope, _replay_receive(messages), capture_send)
        if _response_status(captured) != 200:
            await _send_messages(captured, send)
            return

        user = await self.db.users.find_one({"email": email})
        if not user or not await requires_email_otp(self.db, user):
            await _send_messages(captured, send)
            return

        try:
            recipient = str(user.get("email") or "").strip().lower()
            if not _valid_recipient(recipient):
                raise ValueError("account has no deliverable email address")
            challenge, code = await self.store.issue_for_login(user)
            if code is not None:
                try:
                    await send_otp_email(recipient, code, user.get("name"))
                except Exception:
                    await self.store.consume(str(challenge["jti"]))
                    await self.store.safe_event("email_otp_delivery_failed", user)
                    raise
                await self.store.safe_event("email_otp_challenge_issued", user)
            else:
                await self.store.safe_event("email_otp_challenge_reused", user)

            response = JSONResponse(
                _challenge_payload(challenge, user),
                status_code=202,
            )
            # Discard password-only cookies generated by the inner route and
            # clear any older browser session while the second factor is pending.
            _clear_auth_on_response(response)
            await response(scope, _replay_receive(messages), send)
        except Exception:
            logger.exception("Failed to issue email OTP challenge")
            response = JSONResponse(
                {
                    "detail": "تعذر إرسال رمز التحقق إلى البريد مؤقتاً. حاول مرة أخرى لاحقاً.",
                    "code": "email_otp_delivery_unavailable",
                },
                status_code=503,
            )
            _clear_auth_on_response(response)
            await response(scope, _replay_receive(messages), send)

    async def _verify(self, scope, receive, send) -> None:
        body, messages = await _read_body(receive)
        payload = _json_object(body) or {}
        token = str(payload.get("challenge_token") or "").strip()
        code = str(payload.get("code") or "").strip().replace(" ", "")
        if not token or len(code) != OTP_DIGITS or not code.isdigit():
            response = JSONResponse(
                {"detail": "أدخل رمز التحقق المكوّن من 6 أرقام."},
                status_code=400,
            )
            await response(scope, _replay_receive(messages), send)
            return

        try:
            challenge, doc = await self.store.resolve(token)
        except jwt.ExpiredSignatureError:
            response = JSONResponse(
                {"detail": "انتهت صلاحية رمز التحقق. سجّل الدخول من جديد."},
                status_code=401,
            )
            await response(scope, _replay_receive(messages), send)
            return
        except jwt.InvalidTokenError:
            response = JSONResponse(
                {"detail": "محاولة التحقق غير صالحة أو تم استخدامها مسبقاً."},
                status_code=401,
            )
            await response(scope, _replay_receive(messages), send)
            return

        user = await self.db.users.find_one({"id": challenge["sub"]})
        if (
            not user
            or user.get("disabled") is True
            or user.get("is_active") is False
            or user.get("deleted_at")
            or not await requires_email_otp(self.db, user)
        ):
            await self.store.consume(str(challenge["jti"]))
            response = JSONResponse({"detail": "تعذر إكمال التحقق."}, status_code=401)
            await response(scope, _replay_receive(messages), send)
            return

        digest = otp_digest(str(challenge["jti"]), code)
        if not hmac.compare_digest(str(doc.get("otp_hash") or ""), digest):
            remaining = await self.store.fail(str(challenge["jti"]))
            await self.store.safe_event(
                "email_otp_verification_failed",
                user,
                attempts_remaining=remaining,
            )
            response = JSONResponse(
                {
                    "detail": (
                        "تم إيقاف محاولة التحقق الحالية بعد عدة رموز غير صحيحة. سجّل الدخول من جديد."
                        if remaining <= 0
                        else "رمز التحقق غير صحيح."
                    ),
                    "attempts_remaining": remaining,
                    "code": "email_otp_challenge_locked" if remaining <= 0 else "email_otp_invalid",
                },
                status_code=429 if remaining <= 0 else 401,
            )
            await response(scope, _replay_receive(messages), send)
            return

        consumed = await self.store.consume_matching(
            jti=str(challenge["jti"]),
            user_id=str(user["id"]),
            digest=digest,
        )
        if not consumed:
            response = JSONResponse(
                {"detail": "رمز التحقق تم استخدامه أو انتهت صلاحيته."},
                status_code=401,
            )
            await response(scope, _replay_receive(messages), send)
            return

        await self.store.safe_event("email_otp_login_succeeded", user)
        response = _session_response(user)
        await response(scope, _replay_receive(messages), send)

    async def _resend(self, scope, receive, send) -> None:
        body, messages = await _read_body(receive)
        payload = _json_object(body) or {}
        token = str(payload.get("challenge_token") or "").strip()
        if not token:
            response = JSONResponse({"detail": "بيانات محاولة التحقق مطلوبة."}, status_code=400)
            await response(scope, _replay_receive(messages), send)
            return

        try:
            challenge, current = await self.store.resolve(token)
            user = await self.db.users.find_one({"id": challenge["sub"]})
            if (
                not user
                or user.get("disabled") is True
                or user.get("is_active") is False
                or user.get("deleted_at")
                or not await requires_email_otp(self.db, user)
            ):
                await self.store.consume(str(challenge["jti"]))
                raise jwt.InvalidTokenError("email OTP policy no longer applies")

            _, updated, code, new_token = await self.store.prepare_resend(token)
            try:
                await send_otp_email(str(user.get("email") or ""), code, user.get("name"))
            except Exception:
                await self.store.consume(str(challenge["jti"]))
                await self.store.safe_event("email_otp_delivery_failed", user, resend=True)
                raise
            await self.store.safe_event(
                "email_otp_resent",
                user,
                resend_count=int(updated.get("resend_count") or 0),
            )
            response = JSONResponse(
                {
                    "ok": True,
                    "challenge_token": new_token,
                    "masked_email": mask_email(str(user.get("email") or "")),
                    "expires_in_seconds": _challenge_seconds(),
                    "resend_after_seconds": _resend_cooldown_seconds(),
                }
            )
        except EmailOtpCooldown as exc:
            response = JSONResponse(
                {
                    "detail": "انتظر قليلاً قبل إعادة إرسال رمز جديد.",
                    "retry_after_seconds": exc.retry_after,
                },
                status_code=429,
                headers={"Retry-After": str(exc.retry_after)},
            )
        except EmailOtpResendLimit:
            response = JSONResponse(
                {"detail": "تم الوصول إلى الحد الأقصى لإعادة الإرسال. سجّل الدخول من جديد."},
                status_code=429,
            )
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            response = JSONResponse(
                {"detail": "انتهت محاولة التحقق. سجّل الدخول من جديد."},
                status_code=401,
            )
        except Exception:
            logger.exception("Failed to resend email OTP")
            response = JSONResponse(
                {"detail": "تعذر إعادة إرسال رمز التحقق مؤقتاً."},
                status_code=503,
            )
        await response(scope, _replay_receive(messages), send)


async def install_email_otp_security(app, db: Any, *, initialize_indexes: bool = True) -> None:
    """Install email OTP inside Owner MFA and prepare Mongo TTL indexes."""
    if getattr(app.state, "mezan_email_otp_security_installed", False):
        return

    validate_email_otp_runtime()
    store = EmailOtpChallengeStore(db)
    # Independent web installation delegates index writes to the migration role.
    if initialize_indexes:
        await store.ensure_indexes()
    app.user_middleware.append(Middleware(EmailOtpSecurityMiddleware, db=db))
    app.middleware_stack = app.build_middleware_stack()
    app.state.mezan_email_otp_security_installed = True
    logger.info(
        "Mezan email OTP security installed: enabled=%s ttl=%ss attempts=%s resend=%ss",
        email_otp_enabled(),
        _challenge_seconds(),
        _max_attempts(),
        _resend_cooldown_seconds(),
    )


__all__ = [
    "EmailOtpChallengeStore",
    "EmailOtpSecurityMiddleware",
    "RESEND_PATH",
    "VERIFY_PATH",
    "generate_otp",
    "install_email_otp_security",
    "mask_email",
    "otp_digest",
    "smtp_settings",
    "validate_email_otp_runtime",
]
