"""Privileged MFA enforcement for Mezan Owner/Admin sign-in.

The existing FastAPI password route remains the password authority. This ASGI
layer observes a successful password response for Owner/Admin and replaces it
with a short-lived MFA flow, so password-only privileged cookies/tokens never
reach the client.

First enrollment additionally requires MFA_BOOTSTRAP_CODE. The bootstrap secret
is an out-of-band deployment secret and is never stored in MongoDB. This closes
the first-enrollment takeover gap: possession of the account password alone is
not sufficient to register an attacker's authenticator.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote, urlencode

import jwt
from cryptography.fernet import Fernet, InvalidToken
from pymongo import ReturnDocument
from starlette.middleware import Middleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

LOGIN_PATH = "/api/auth/login"
VERIFY_PATH = "/api/auth/mfa/verify"
PRIVILEGED_ROLES = {"owner", "admin"}
JWT_ALGORITHM = "HS256"
MIN_BOOTSTRAP_LENGTH = 12


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _challenge_seconds() -> int:
    return _env_int("AUTH_MFA_CHALLENGE_SECONDS", 5 * 60)


def _max_attempts() -> int:
    return _env_int("AUTH_MFA_MAX_ATTEMPTS", 5)


def _event_retention_seconds() -> int:
    return _env_int("AUTH_SECURITY_EVENT_RETENTION_SECONDS", 90 * 24 * 60 * 60)


def _jwt_secret() -> str:
    value = (os.environ.get("JWT_SECRET") or "").strip()
    if not value:
        raise RuntimeError("JWT_SECRET is required for MFA")
    return value


def _bootstrap_secret() -> str | None:
    value = (os.environ.get("MFA_BOOTSTRAP_CODE") or "").strip()
    if len(value) < MIN_BOOTSTRAP_LENGTH:
        return None
    return value


def _bootstrap_matches(value: str | None) -> bool:
    expected = _bootstrap_secret()
    provided = (value or "").strip()
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


def _derive_fernet(source: str) -> Fernet:
    material = hashlib.sha256(("mezan:mfa:fernet:v1:" + source).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def _primary_fernet() -> Fernet:
    explicit = (os.environ.get("MFA_ENCRYPTION_KEY") or "").strip()
    return _derive_fernet(explicit or _jwt_secret())


def encrypt_totp_secret(secret: str) -> str:
    return _primary_fernet().encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_totp_secret(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None

    # If operators later introduce MFA_ENCRYPTION_KEY, try it first but retain
    # the previous JWT-derived key as a migration fallback. That lets existing
    # enrollments survive the transition to an independent encryption secret.
    sources: list[str] = []
    explicit = (os.environ.get("MFA_ENCRYPTION_KEY") or "").strip()
    if explicit:
        sources.append(explicit)
    sources.append(_jwt_secret())

    seen: set[str] = set()
    for source in sources:
        if source in seen:
            continue
        seen.add(source)
        try:
            return _derive_fernet(source).decrypt(ciphertext.encode("ascii")).decode("ascii")
        except (InvalidToken, ValueError, UnicodeError):
            continue
    return None


def generate_totp_secret() -> str:
    # 160-bit secret is the conventional RFC 4226/6238 strength for SHA-1 TOTP.
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _base32_decode(secret: str) -> bytes:
    normalized = "".join(secret.strip().upper().split())
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    return base64.b32decode(normalized + padding, casefold=True)


def hotp(secret: str, counter: int, digits: int = 6) -> str:
    key = _base32_decode(secret)
    digest = hmac.new(key, struct.pack(">Q", int(counter)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10 ** digits)).zfill(digits)


def match_totp_counter(
    secret: str,
    code: str,
    *,
    timestamp: float | None = None,
    period: int = 30,
    window: int = 1,
) -> int | None:
    candidate = (code or "").strip().replace(" ", "")
    if len(candidate) != 6 or not candidate.isdigit():
        return None
    current = int((time.time() if timestamp is None else timestamp) // period)
    for offset in range(-window, window + 1):
        counter = current + offset
        if counter < 0:
            continue
        if hmac.compare_digest(hotp(secret, counter), candidate):
            return counter
    return None


def provisioning_uri(email: str, secret: str) -> str:
    issuer = "MEZAN"
    label = quote(f"{issuer}:{email.strip().lower()}", safe="")
    query = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": "6",
            "period": "30",
        }
    )
    return f"otpauth://totp/{label}?{query}"


def _normalize_recovery_code(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def recovery_code_digest(value: str) -> str:
    normalized = _normalize_recovery_code(value)
    return hmac.new(
        _jwt_secret().encode("utf-8"),
        f"mfa-recovery:{normalized}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_recovery_codes(count: int = 8) -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # avoid 0/O and 1/I ambiguity
    codes: list[str] = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        codes.append(f"{raw[:4]}-{raw[4:8]}-{raw[8:]}")
    return codes


def _challenge_token(*, user_id: str, purpose: str, jti: str) -> str:
    now = _now()
    payload = {
        "sub": user_id,
        "type": "mfa_challenge",
        "purpose": purpose,
        "jti": jti,
        "iat": now,
        "exp": now + timedelta(seconds=_challenge_seconds()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def _decode_challenge_token(token: str) -> dict[str, Any]:
    payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    if payload.get("type") != "mfa_challenge":
        raise jwt.InvalidTokenError("invalid challenge type")
    if payload.get("purpose") not in {"setup", "login"}:
        raise jwt.InvalidTokenError("invalid challenge purpose")
    if not payload.get("sub") or not payload.get("jti"):
        raise jwt.InvalidTokenError("incomplete challenge")
    return payload


class MfaChallengeStore:
    def __init__(self, db):
        self.db = db
        self.challenges = db.auth_mfa_challenges
        self.events = db.auth_security_events

    async def ensure_indexes(self) -> None:
        await self.challenges.create_index("jti", unique=True)
        await self.challenges.create_index("expires_at", expireAfterSeconds=0)
        # login_security creates the same event indexes; these are idempotent.
        await self.events.create_index([("created_at", -1)])
        await self.events.create_index("expires_at", expireAfterSeconds=0)

    async def event(self, event_type: str, user: dict, **extra: Any) -> None:
        now = _now()
        doc = {
            "event_type": event_type,
            "user_id": user.get("id"),
            "role": (user.get("role") or "").lower(),
            "created_at": now,
            "expires_at": now + timedelta(seconds=_event_retention_seconds()),
        }
        doc.update(extra)
        await self.events.insert_one(doc)

    async def safe_event(self, event_type: str, user: dict, **extra: Any) -> None:
        try:
            await self.event(event_type, user, **extra)
        except Exception:
            # Audit storage degradation must be loud but cannot strand a user
            # after a cryptographically successful MFA verification.
            logger.exception("MFA security event write failed: %s", event_type)

    async def create(self, *, user: dict, purpose: str) -> str:
        now = _now()
        jti = uuid.uuid4().hex
        expires_at = now + timedelta(seconds=_challenge_seconds())
        await self.challenges.insert_one(
            {
                "jti": jti,
                "user_id": user["id"],
                "purpose": purpose,
                "failures": 0,
                "created_at": now,
                "expires_at": expires_at,
            }
        )
        return _challenge_token(user_id=user["id"], purpose=purpose, jti=jti)

    async def resolve(self, token: str) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = _decode_challenge_token(token)
        now = _now()
        doc = await self.challenges.find_one(
            {
                "jti": payload["jti"],
                "user_id": payload["sub"],
                "purpose": payload["purpose"],
                "expires_at": {"$gt": now},
                "failures": {"$lt": _max_attempts()},
            }
        )
        if not doc:
            raise jwt.InvalidTokenError("challenge expired or consumed")
        return payload, doc

    async def fail(self, jti: str) -> int:
        doc = await self.challenges.find_one_and_update(
            {"jti": jti},
            {"$inc": {"failures": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            return 0
        failures = int(doc.get("failures") or 0)
        remaining = max(0, _max_attempts() - failures)
        if remaining <= 0:
            await self.challenges.delete_one({"jti": jti})
        return remaining

    async def consume(self, jti: str) -> None:
        await self.challenges.delete_one({"jti": jti})


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


def _session_response(user: dict, *, recovery_codes: list[str] | None = None) -> JSONResponse:
    from auth import create_access_token, create_refresh_token, set_auth_cookies

    access = create_access_token(user["id"], user["email"], mfa_verified=True)
    refresh = create_refresh_token(user["id"], mfa_verified=True)
    payload: dict[str, Any] = {
        "ok": True,
        "mfa_verified": True,
        "id": user["id"],
        "name": user.get("name"),
        "email": user.get("email"),
        "role": user.get("role"),
        # Kept for non-browser API-client compatibility. Browser AuthContext
        # never persists this token and authenticates with HttpOnly cookies.
        "access_token": access,
    }
    if recovery_codes:
        payload["recovery_codes"] = recovery_codes
    response = JSONResponse(payload, status_code=200)
    set_auth_cookies(response, access, refresh)
    return response


class MfaSecurityMiddleware:
    def __init__(self, app, *, db):
        self.app = app
        self.db = db
        self.store = MfaChallengeStore(db)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "").upper()
        path = scope.get("path")
        if method == "POST" and path == VERIFY_PATH:
            await self._verify(scope, receive, send)
            return
        if method == "POST" and path == LOGIN_PATH:
            await self._login(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _login(self, scope, receive, send) -> None:
        body, request_messages = await _read_body(receive)
        payload = _json_object(body)
        email = str((payload or {}).get("email") or "").strip().lower()
        if not email:
            await self.app(scope, _replay_receive(request_messages), send)
            return

        captured: list[dict[str, Any]] = []

        async def capture_send(message: dict[str, Any]):
            captured.append(dict(message))

        # First let the existing route prove the password. Until it returns 200
        # we do not reveal role/MFA state and we do not inspect bootstrap input.
        await self.app(scope, _replay_receive(request_messages), capture_send)
        if _response_status(captured) != 200:
            await _send_messages(captured, send)
            return

        user = await self.db.users.find_one({"email": email})
        role = ((user or {}).get("role") or "").strip().lower()
        if not user or role not in PRIVILEGED_ROLES:
            await _send_messages(captured, send)
            return

        try:
            if bool(user.get("mfa_enabled")):
                challenge = await self.store.create(user=user, purpose="login")
                await self.store.safe_event("mfa_challenge_issued", user, purpose="login")
                response = JSONResponse(
                    {
                        "mfa_required": True,
                        "challenge_token": challenge,
                        "message": "أدخل رمز التحقق من تطبيق المصادقة لإكمال تسجيل الدخول.",
                    },
                    status_code=202,
                )
            else:
                expected_bootstrap = _bootstrap_secret()
                provided_bootstrap = str((payload or {}).get("mfa_bootstrap_code") or "").strip()

                if not expected_bootstrap:
                    response = JSONResponse(
                        {
                            "detail": "لم يتم تجهيز رمز التفعيل الأولي للتحقق بخطوتين على الخادم. يلزم ضبط MFA_BOOTSTRAP_CODE أولاً.",
                            "code": "mfa_bootstrap_not_configured",
                        },
                        status_code=503,
                    )
                elif not provided_bootstrap:
                    # Password is correct, but no bootstrap proof was supplied.
                    # Return a challenge *request*, not the TOTP secret itself.
                    response = JSONResponse(
                        {
                            "mfa_bootstrap_required": True,
                            "message": "أدخل رمز التفعيل الأولي الخاص بميزان لبدء ربط تطبيق المصادقة.",
                        },
                        status_code=202,
                    )
                elif not _bootstrap_matches(provided_bootstrap):
                    # 401 is intentional: the outer login-security middleware
                    # counts these as failed sign-in attempts, so five wrong
                    # bootstrap guesses lock the browser device for one hour.
                    await self.store.safe_event("mfa_bootstrap_failed", user)
                    response = JSONResponse(
                        {"detail": "رمز التفعيل الأولي غير صحيح."},
                        status_code=401,
                    )
                else:
                    secret = generate_totp_secret()
                    encrypted = encrypt_totp_secret(secret)
                    now = _now()
                    await self.db.users.update_one(
                        {"id": user["id"]},
                        {
                            "$set": {
                                "mfa_pending_secret_enc": encrypted,
                                "mfa_pending_created_at": now,
                            }
                        },
                    )
                    challenge = await self.store.create(user=user, purpose="setup")
                    await self.store.safe_event("mfa_enrollment_started", user)
                    response = JSONResponse(
                        {
                            "mfa_setup_required": True,
                            "challenge_token": challenge,
                            "setup_secret": secret,
                            "otpauth_uri": provisioning_uri(user.get("email") or email, secret),
                            "message": "يلزم تفعيل التحقق بخطوتين لهذا الحساب قبل المتابعة.",
                        },
                        status_code=202,
                    )

            # The legacy login route already built password-only auth cookies,
            # but its captured response is discarded for privileged accounts.
            # Explicit deletion also removes any older privileged session.
            _clear_auth_on_response(response)
            await response(scope, _replay_receive(request_messages), send)
        except Exception:
            logger.exception("failed to issue privileged MFA challenge")
            response = JSONResponse(
                {"detail": "تعذر بدء التحقق بخطوتين مؤقتاً. حاول مرة أخرى لاحقاً."},
                status_code=503,
            )
            _clear_auth_on_response(response)
            await response(scope, _replay_receive(request_messages), send)

    async def _verify(self, scope, receive, send) -> None:
        body, request_messages = await _read_body(receive)
        payload = _json_object(body) or {}
        token = str(payload.get("challenge_token") or "").strip()
        code = str(payload.get("code") or "").strip()
        if not token or not code:
            response = JSONResponse(
                {"detail": "رمز التحقق وبيانات الجلسة مطلوبان."}, status_code=400
            )
            await response(scope, _replay_receive(request_messages), send)
            return

        try:
            challenge, _doc = await self.store.resolve(token)
        except jwt.ExpiredSignatureError:
            response = JSONResponse(
                {"detail": "انتهت صلاحية محاولة التحقق. سجّل الدخول من جديد."},
                status_code=401,
            )
            await response(scope, _replay_receive(request_messages), send)
            return
        except jwt.InvalidTokenError:
            response = JSONResponse(
                {"detail": "محاولة التحقق غير صالحة أو تم استخدامها مسبقاً."},
                status_code=401,
            )
            await response(scope, _replay_receive(request_messages), send)
            return

        user = await self.db.users.find_one({"id": challenge["sub"]})
        role = ((user or {}).get("role") or "").strip().lower()
        if not user or role not in PRIVILEGED_ROLES:
            await self.store.consume(challenge["jti"])
            response = JSONResponse({"detail": "تعذر إكمال التحقق."}, status_code=401)
            await response(scope, _replay_receive(request_messages), send)
            return

        purpose = challenge["purpose"]
        ok = False
        recovery_used = False
        recovery_codes_to_show: list[str] | None = None

        if purpose == "setup":
            if bool(user.get("mfa_enabled")):
                await self.store.consume(challenge["jti"])
                response = JSONResponse(
                    {"detail": "التحقق بخطوتين مفعّل بالفعل لهذا الحساب."},
                    status_code=409,
                )
                await response(scope, _replay_receive(request_messages), send)
                return

            pending = user.get("mfa_pending_secret_enc")
            secret = decrypt_totp_secret(pending)
            matched_counter = match_totp_counter(secret or "", code) if secret else None
            if matched_counter is not None:
                recovery_codes_to_show = generate_recovery_codes()
                recovery_hashes = [recovery_code_digest(item) for item in recovery_codes_to_show]
                now = _now()
                result = await self.db.users.update_one(
                    {
                        "id": user["id"],
                        "mfa_enabled": {"$ne": True},
                        "mfa_pending_secret_enc": pending,
                    },
                    {
                        "$set": {
                            "mfa_enabled": True,
                            "mfa_totp_secret_enc": pending,
                            "mfa_recovery_code_hashes": recovery_hashes,
                            "mfa_last_totp_counter": matched_counter,
                            "mfa_enabled_at": now,
                        },
                        "$unset": {
                            "mfa_pending_secret_enc": "",
                            "mfa_pending_created_at": "",
                        },
                    },
                )
                ok = result.modified_count == 1
        else:  # normal MFA login
            if bool(user.get("mfa_enabled")):
                secret = decrypt_totp_secret(user.get("mfa_totp_secret_enc"))
                matched_counter = match_totp_counter(secret or "", code) if secret else None
                if matched_counter is not None:
                    # One 30-second code may authorize only one session. Atomic
                    # compare/update closes concurrent replay races.
                    counter_filter: dict[str, Any] = {
                        "id": user["id"],
                        "mfa_enabled": True,
                        "$or": [
                            {"mfa_last_totp_counter": None},
                            {"mfa_last_totp_counter": {"$exists": False}},
                            {"mfa_last_totp_counter": {"$lt": matched_counter}},
                        ],
                    }
                    result = await self.db.users.update_one(
                        counter_filter,
                        {"$set": {"mfa_last_totp_counter": matched_counter}},
                    )
                    ok = result.modified_count == 1
                else:
                    normalized_recovery = _normalize_recovery_code(code)
                    if normalized_recovery:
                        digest = recovery_code_digest(normalized_recovery)
                        if digest in (user.get("mfa_recovery_code_hashes") or []):
                            result = await self.db.users.update_one(
                                {
                                    "id": user["id"],
                                    "mfa_recovery_code_hashes": digest,
                                },
                                {"$pull": {"mfa_recovery_code_hashes": digest}},
                            )
                            ok = result.modified_count == 1
                            recovery_used = ok

        if not ok:
            remaining = await self.store.fail(challenge["jti"])
            await self.store.safe_event(
                "mfa_verification_failed",
                user,
                purpose=purpose,
                attempts_remaining=remaining,
            )
            if remaining <= 0:
                response = JSONResponse(
                    {
                        "detail": "تم إيقاف محاولة التحقق الحالية بعد عدة رموز غير صحيحة. سجّل الدخول من جديد.",
                        "code": "mfa_challenge_locked",
                    },
                    status_code=429,
                )
            else:
                response = JSONResponse(
                    {
                        "detail": "رمز التحقق غير صحيح أو تم استخدامه مسبقاً.",
                        "attempts_remaining": remaining,
                    },
                    status_code=401,
                )
            await response(scope, _replay_receive(request_messages), send)
            return

        await self.store.consume(challenge["jti"])
        user = await self.db.users.find_one({"id": user["id"]}) or user
        await self.store.safe_event(
            "mfa_enabled" if purpose == "setup" else "mfa_login_succeeded",
            user,
            recovery_code_used=recovery_used,
        )
        response = _session_response(user, recovery_codes=recovery_codes_to_show)
        await response(scope, _replay_receive(request_messages), send)


async def install_mfa_security(app, db, *, initialize_indexes: bool = True) -> None:
    """Install privileged MFA middleware once and prepare TTL indexes."""
    if getattr(app.state, "mezan_mfa_security_installed", False):
        return

    store = MfaChallengeStore(db)
    # Independent web installation delegates index writes to the migration role.
    if initialize_indexes:
        await store.ensure_indexes()
    app.user_middleware.append(Middleware(MfaSecurityMiddleware, db=db))
    app.middleware_stack = app.build_middleware_stack()
    app.state.mezan_mfa_security_installed = True
    logger.info(
        "Mezan privileged MFA enabled: roles=%s challenge=%ss attempts=%s bootstrap=%s",
        ",".join(sorted(PRIVILEGED_ROLES)),
        _challenge_seconds(),
        _max_attempts(),
        "configured" if _bootstrap_secret() else "missing",
    )
