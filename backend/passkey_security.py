"""Owner trusted-device WebAuthn/passkey security for Mezan.

This layer sits between the distributed password-abuse guard and the existing
Owner/Admin TOTP MFA middleware. It never weakens password verification or TOTP:

- The Owner must first complete normal MFA before registering or renewing a
  trusted device.
- A trusted device is bound to Mezan's signed ``mezan_device_id`` cookie and a
  platform WebAuthn credential (Windows Hello / Touch ID / Face ID / device PIN).
- Trust lasts 30 days by default and does not silently extend on use.
- After trust expires, the Owner must complete TOTP again before renewing it.
- A different browser/device cannot reuse a synced passkey because the server
  also requires the signed device binding created by Login Security V1.

Admin/employee email OTP is intentionally a separate rollout; this module only
changes the Owner experience after the existing TOTP protection is active.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from typing import Any, Iterable
from urllib.parse import urlparse

from fastapi import HTTPException
from pymongo import ReturnDocument
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

logger = logging.getLogger(__name__)

LOGIN_PATH = "/api/auth/login"
PASSKEY_AUTH_VERIFY_PATH = "/api/auth/passkey/authenticate/verify"
PASSKEY_TRUST_OPTIONS_PATH = "/api/auth/passkey/trust/options"
PASSKEY_TRUST_VERIFY_PATH = "/api/auth/passkey/trust/verify"
DEVICE_COOKIE = "mezan_device_id"
OWNER_ROLE = "owner"
RP_NAME = "MEZAN"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _challenge_seconds() -> int:
    return _env_int("AUTH_PASSKEY_CHALLENGE_SECONDS", 5 * 60)


def _max_attempts() -> int:
    return _env_int("AUTH_PASSKEY_MAX_ATTEMPTS", 5)


def _trust_days() -> int:
    return _env_int("AUTH_TRUSTED_DEVICE_DAYS", 30)


def _event_retention_seconds() -> int:
    return _env_int("AUTH_SECURITY_EVENT_RETENTION_SECONDS", 90 * 24 * 60 * 60)


def _jwt_secret() -> bytes:
    value = (os.environ.get("JWT_SECRET") or "").strip()
    if not value:
        raise RuntimeError("JWT_SECRET is required for trusted-device binding")
    return value.encode("utf-8")


def _configured_origin() -> str:
    """Return the exact HTTPS WebAuthn origin used by the production frontend."""
    raw = (
        os.environ.get("WEBAUTHN_ORIGIN")
        or os.environ.get("FRONTEND_URL")
        or "https://salla-analytics.emergent.host"
    ).strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise RuntimeError("WEBAUTHN_ORIGIN/FRONTEND_URL must be an absolute URL")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("WebAuthn requires HTTPS outside localhost")
    return f"{parsed.scheme}://{parsed.netloc}"


def _rp_id() -> str:
    explicit = (os.environ.get("WEBAUTHN_RP_ID") or "").strip()
    if explicit:
        return explicit
    hostname = urlparse(_configured_origin()).hostname
    if not hostname:
        raise RuntimeError("Unable to derive WebAuthn RP ID")
    return hostname


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _headers(scope: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers") or []:
        result.setdefault(
            raw_name.decode("latin-1").lower(), raw_value.decode("latin-1")
        )
    return result


def _cookie(headers: dict[str, str], name: str) -> str | None:
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


def _trusted_device_hash(scope: dict[str, Any]) -> str | None:
    """Validate the Login Security V1 device cookie and return its HMAC identity."""
    signed = _cookie(_headers(scope), DEVICE_COOKIE)
    if not signed or "." not in signed:
        return None
    token, signature = signed.rsplit(".", 1)
    if not token or len(token) > 128 or len(signature) != 64:
        return None
    expected = hmac.new(
        _jwt_secret(), f"device:{token}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    # Login Security's device digest uses the same domain-separated payload.
    return expected


def _user_handle(user_id: str) -> bytes:
    return hashlib.sha256(f"mezan-passkey-user:{user_id}".encode("utf-8")).digest()


def _safe_label(value: Any) -> str:
    rendered = str(value or "").strip()
    return rendered[:80] or "جهاز موثوق"


def _trust_ceremony(
    reusable_for_device: list[dict], reusable_for_user: list[dict]
) -> str:
    """Choose renewal/rebinding before attempting a new registration."""
    if reusable_for_device:
        return "renew"
    if reusable_for_user:
        return "rebind"
    return "register"


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


def _response_json(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    body = b"".join(
        item.get("body", b"")
        for item in messages
        if item.get("type") == "http.response.body"
    )
    return _json_object(body)


def _session_response(user: dict, *, passkey_verified: bool = False) -> JSONResponse:
    from auth import create_access_token, create_refresh_token, set_auth_cookies

    access = create_access_token(user["id"], user["email"], mfa_verified=True)
    refresh = create_refresh_token(user["id"], mfa_verified=True)
    response = JSONResponse(
        {
            "ok": True,
            "mfa_verified": True,
            "passkey_verified": bool(passkey_verified),
            "id": user["id"],
            "name": user.get("name"),
            "email": user.get("email"),
            "role": user.get("role"),
            # API-client compatibility only; browser AuthContext remains cookie-only.
            "access_token": access,
        },
        status_code=200,
    )
    set_auth_cookies(response, access, refresh)
    return response


def _clear_auth(response: JSONResponse) -> None:
    from auth import clear_auth_cookies

    clear_auth_cookies(response)


class PasskeyStore:
    def __init__(self, db):
        self.db = db
        self.credentials = db.auth_passkey_credentials
        self.challenges = db.auth_passkey_challenges
        self.events = db.auth_security_events

    async def ensure_indexes(self) -> None:
        await self.credentials.create_index("credential_id_b64", unique=True)
        await self.credentials.create_index([("user_id", 1), ("device_hash", 1)])
        await self.credentials.create_index("trust_expires_at")
        await self.challenges.create_index("challenge_id", unique=True)
        await self.challenges.create_index("expires_at", expireAfterSeconds=0)
        await self.events.create_index([("created_at", -1)])
        await self.events.create_index("expires_at", expireAfterSeconds=0)

    async def safe_event(self, event_type: str, user: dict, **extra: Any) -> None:
        try:
            now = _now()
            await self.events.insert_one(
                {
                    "event_type": event_type,
                    "user_id": user.get("id"),
                    "role": (user.get("role") or "").strip().lower(),
                    "created_at": now,
                    "expires_at": now + timedelta(seconds=_event_retention_seconds()),
                    **extra,
                }
            )
        except Exception:
            logger.exception("Passkey security event write failed: %s", event_type)

    async def active_for_device(self, user_id: str, device_hash: str) -> list[dict]:
        now = _now()
        return await self.credentials.find(
            {
                "user_id": user_id,
                "device_hash": device_hash,
                "revoked_at": {"$exists": False},
                "trust_expires_at": {"$gt": now},
            },
            {"_id": 0},
        ).to_list(20)

    async def reusable_for_device(self, user_id: str, device_hash: str) -> list[dict]:
        return await self.credentials.find(
            {
                "user_id": user_id,
                "device_hash": device_hash,
                "revoked_at": {"$exists": False},
            },
            {"_id": 0},
        ).to_list(20)

    async def reusable_for_user(self, user_id: str) -> list[dict]:
        """Return credentials that can be rebound after fresh Owner MFA.

        The signed browser device cookie is deliberately long-lived, but a
        browser can still rotate or lose it while its platform passkey remains
        installed (for example after switching Mezan users in one browser).
        In that case registration would be rejected by the authenticator as a
        duplicate.  A fresh password + TOTP session may instead prove the
        existing credential and bind it to the browser's current signed ID.
        """
        return await self.credentials.find(
            {
                "user_id": user_id,
                "revoked_at": {"$exists": False},
            },
            {"_id": 0},
        ).to_list(50)

    async def all_user_credentials(self, user_id: str) -> list[dict]:
        return await self.credentials.find(
            {"user_id": user_id, "revoked_at": {"$exists": False}},
            {"_id": 0},
        ).to_list(50)

    async def create_challenge(
        self,
        *,
        user: dict,
        device_hash: str,
        purpose: str,
        challenge: bytes,
        credential_ids: list[str] | None = None,
    ) -> str:
        now = _now()
        challenge_id = secrets.token_urlsafe(32)
        await self.challenges.insert_one(
            {
                "challenge_id": challenge_id,
                "user_id": user["id"],
                "device_hash": device_hash,
                "purpose": purpose,
                "challenge_b64": _b64u(challenge),
                "credential_ids": list(credential_ids or []),
                "failures": 0,
                "created_at": now,
                "expires_at": now + timedelta(seconds=_challenge_seconds()),
            }
        )
        return challenge_id

    async def resolve_challenge(
        self, challenge_id: str, *, purpose: str | None = None
    ) -> dict | None:
        query: dict[str, Any] = {
            "challenge_id": challenge_id,
            "expires_at": {"$gt": _now()},
            "failures": {"$lt": _max_attempts()},
        }
        if purpose:
            query["purpose"] = purpose
        return await self.challenges.find_one(query, {"_id": 0})

    async def fail_challenge(self, challenge_id: str) -> int:
        doc = await self.challenges.find_one_and_update(
            {"challenge_id": challenge_id},
            {"$inc": {"failures": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            return 0
        failures = int(doc.get("failures") or 0)
        remaining = max(0, _max_attempts() - failures)
        if remaining <= 0:
            await self.challenges.delete_one({"challenge_id": challenge_id})
        return remaining

    async def consume_challenge(self, challenge_id: str) -> None:
        await self.challenges.delete_one({"challenge_id": challenge_id})


class PasskeySecurityMiddleware:
    def __init__(self, app, *, db):
        self.app = app
        self.db = db
        self.store = PasskeyStore(db)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "").upper()
        path = str(scope.get("path") or "")
        if method == "POST" and path == PASSKEY_AUTH_VERIFY_PATH:
            await self._authenticate_verify(scope, receive, send)
            return
        if method == "POST" and path == PASSKEY_TRUST_OPTIONS_PATH:
            await self._trust_options(scope, receive, send)
            return
        if method == "POST" and path == PASSKEY_TRUST_VERIFY_PATH:
            await self._trust_verify(scope, receive, send)
            return
        if method == "POST" and path == LOGIN_PATH:
            await self._login(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _login(self, scope, receive, send) -> None:
        body, messages = await _read_body(receive)
        payload = _json_object(body) or {}
        email = str(payload.get("email") or "").strip().lower()
        if not email:
            await self.app(scope, _replay_receive(messages), send)
            return

        captured: list[dict[str, Any]] = []

        async def capture_send(message: dict[str, Any]):
            captured.append(dict(message))

        # Let the inner MFA middleware prove the password and decide whether a
        # second factor is required. We only replace its normal TOTP challenge
        # for an already-enrolled Owner on this exact trusted device.
        await self.app(scope, _replay_receive(messages), capture_send)
        result = _response_json(captured) or {}
        if (
            _response_status(captured) != 202
            or result.get("mfa_required") is not True
            or payload.get("force_totp") is True
        ):
            await _send_messages(captured, send)
            return

        user = await self.db.users.find_one({"email": email})
        if not user or (user.get("role") or "").strip().lower() != OWNER_ROLE:
            await _send_messages(captured, send)
            return

        device_hash = _trusted_device_hash(scope)
        if not device_hash:
            await _send_messages(captured, send)
            return

        try:
            credentials = await self.store.active_for_device(user["id"], device_hash)
            if not credentials:
                await _send_messages(captured, send)
                return

            challenge = secrets.token_bytes(32)
            descriptors = [
                PublicKeyCredentialDescriptor(
                    id=base64url_to_bytes(item["credential_id_b64"])
                )
                for item in credentials
            ]
            options = generate_authentication_options(
                rp_id=_rp_id(),
                challenge=challenge,
                allow_credentials=descriptors,
                user_verification=UserVerificationRequirement.REQUIRED,
                timeout=60_000,
            )
            challenge_id = await self.store.create_challenge(
                user=user,
                device_hash=device_hash,
                purpose="authenticate",
                challenge=challenge,
                credential_ids=[item["credential_id_b64"] for item in credentials],
            )
            await self.store.safe_event(
                "passkey_challenge_issued", user, device_hash=device_hash
            )
            response = JSONResponse(
                {
                    "passkey_required": True,
                    "challenge_id": challenge_id,
                    "webauthn_options": json.loads(options_to_json(options)),
                    "fallback_totp_allowed": True,
                    "message": "استخدم بصمة الجهاز أو Windows Hello / PIN لإكمال الدخول.",
                },
                status_code=202,
            )
            # The inner MFA response deliberately cleared any old privileged
            # session. Preserve that invariant when replacing the response.
            _clear_auth(response)
            await response(scope, _replay_receive(messages), send)
        except Exception:
            # Passkey availability must never lock the Owner out. Fall back to
            # the already-generated TOTP challenge if WebAuthn is unavailable.
            logger.exception("Failed to issue trusted-device passkey challenge")
            await _send_messages(captured, send)

    async def _authenticated_owner(self, scope) -> dict:
        from auth import get_current_user_from_db

        try:
            user = await get_current_user_from_db(Request(scope), self.db)
        except HTTPException:
            raise
        if (user.get("role") or "").strip().lower() != OWNER_ROLE:
            raise HTTPException(status_code=403, detail="هذه الميزة متاحة للمالك فقط.")
        return user

    async def _trust_options(self, scope, receive, send) -> None:
        body, messages = await _read_body(receive)
        _ = body  # endpoint currently needs no request fields
        try:
            user = await self._authenticated_owner(scope)
            device_hash = _trusted_device_hash(scope)
            if not device_hash:
                raise HTTPException(
                    status_code=409,
                    detail="تعذر تثبيت هوية هذا المتصفح. أعد تسجيل الدخول ثم حاول مرة أخرى.",
                )

            reusable = await self.store.reusable_for_device(user["id"], device_hash)
            reusable_for_user = (
                [] if reusable else await self.store.reusable_for_user(user["id"])
            )
            ceremony = _trust_ceremony(reusable, reusable_for_user)
            challenge = secrets.token_bytes(32)
            if ceremony == "renew":
                # The device already owns a credential, but its 30-day trust may
                # be expired. Re-authenticate the local device to renew it rather
                # than creating duplicate passkeys every month.
                options = generate_authentication_options(
                    rp_id=_rp_id(),
                    challenge=challenge,
                    allow_credentials=[
                        PublicKeyCredentialDescriptor(
                            id=base64url_to_bytes(item["credential_id_b64"])
                        )
                        for item in reusable
                    ],
                    user_verification=UserVerificationRequirement.REQUIRED,
                    timeout=60_000,
                )
                challenge_id = await self.store.create_challenge(
                    user=user,
                    device_hash=device_hash,
                    purpose="renew",
                    challenge=challenge,
                    credential_ids=[item["credential_id_b64"] for item in reusable],
                )
            elif ceremony == "rebind":
                # The platform passkey survived but Mezan's signed browser ID
                # changed. This commonly happens while switching users in the
                # same browser. The Owner has just completed password + TOTP,
                # so authenticate the existing passkey and safely rebind it to
                # the current browser ID instead of attempting a duplicate
                # registration that Windows Hello/Chrome will reject.
                options = generate_authentication_options(
                    rp_id=_rp_id(),
                    challenge=challenge,
                    allow_credentials=[
                        PublicKeyCredentialDescriptor(
                            id=base64url_to_bytes(item["credential_id_b64"])
                        )
                        for item in reusable_for_user
                    ],
                    user_verification=UserVerificationRequirement.REQUIRED,
                    timeout=60_000,
                )
                challenge_id = await self.store.create_challenge(
                    user=user,
                    device_hash=device_hash,
                    purpose="rebind",
                    challenge=challenge,
                    credential_ids=[
                        item["credential_id_b64"] for item in reusable_for_user
                    ],
                )
            else:
                all_credentials = await self.store.all_user_credentials(user["id"])
                options = generate_registration_options(
                    rp_id=_rp_id(),
                    rp_name=RP_NAME,
                    user_id=_user_handle(user["id"]),
                    user_name=user.get("email") or user["id"],
                    user_display_name=user.get("name") or "Owner",
                    challenge=challenge,
                    exclude_credentials=[
                        PublicKeyCredentialDescriptor(
                            id=base64url_to_bytes(item["credential_id_b64"])
                        )
                        for item in all_credentials
                    ],
                    authenticator_selection=AuthenticatorSelectionCriteria(
                        authenticator_attachment=AuthenticatorAttachment.PLATFORM,
                        resident_key=ResidentKeyRequirement.PREFERRED,
                        user_verification=UserVerificationRequirement.REQUIRED,
                    ),
                    timeout=60_000,
                )
                challenge_id = await self.store.create_challenge(
                    user=user,
                    device_hash=device_hash,
                    purpose="register",
                    challenge=challenge,
                )

            await self.store.safe_event(
                "passkey_trust_started",
                user,
                device_hash=device_hash,
                ceremony=ceremony,
            )
            response = JSONResponse(
                {
                    "ok": True,
                    "ceremony": ceremony,
                    "challenge_id": challenge_id,
                    "webauthn_options": json.loads(options_to_json(options)),
                    "trust_days": _trust_days(),
                }
            )
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        except Exception:
            logger.exception("Failed to start trusted-device registration")
            response = JSONResponse(
                {"detail": "تعذر بدء توثيق الجهاز مؤقتاً. حاول لاحقاً."},
                status_code=503,
            )
        await response(scope, _replay_receive(messages), send)

    async def _trust_verify(self, scope, receive, send) -> None:
        body, messages = await _read_body(receive)
        payload = _json_object(body) or {}
        challenge_id = str(payload.get("challenge_id") or "").strip()
        credential = payload.get("credential")
        label = _safe_label(payload.get("device_label"))
        if not challenge_id or not isinstance(credential, dict):
            response = JSONResponse(
                {"detail": "بيانات توثيق الجهاز غير مكتملة."}, status_code=400
            )
            await response(scope, _replay_receive(messages), send)
            return

        try:
            user = await self._authenticated_owner(scope)
            device_hash = _trusted_device_hash(scope)
            challenge_doc = await self.store.resolve_challenge(challenge_id)
            if (
                not device_hash
                or not challenge_doc
                or challenge_doc.get("user_id") != user["id"]
                or challenge_doc.get("device_hash") != device_hash
                or challenge_doc.get("purpose") not in {"register", "renew", "rebind"}
            ):
                raise HTTPException(
                    status_code=401,
                    detail="انتهت أو تغيرت محاولة توثيق الجهاز. ابدأ من جديد.",
                )

            purpose = challenge_doc["purpose"]
            expected_challenge = base64url_to_bytes(challenge_doc["challenge_b64"])
            now = _now()
            trust_expires_at = now + timedelta(days=_trust_days())

            if purpose == "register":
                verification = verify_registration_response(
                    credential=credential,
                    expected_challenge=expected_challenge,
                    expected_origin=_configured_origin(),
                    expected_rp_id=_rp_id(),
                    require_user_verification=True,
                )
                credential_id_b64 = _b64u(verification.credential_id)
                public_key_b64 = _b64u(verification.credential_public_key)
                transports = (
                    credential.get("response", {}).get("transports", [])
                    if isinstance(credential.get("response"), dict)
                    else []
                )
                await self.store.credentials.update_one(
                    {"credential_id_b64": credential_id_b64},
                    {
                        "$setOnInsert": {
                            "credential_id_b64": credential_id_b64,
                            "user_id": user["id"],
                            "created_at": now,
                        },
                        "$set": {
                            "device_hash": device_hash,
                            "credential_public_key_b64": public_key_b64,
                            "sign_count": int(verification.sign_count or 0),
                            "label": label,
                            "transports": list(transports or [])[:8],
                            "trust_expires_at": trust_expires_at,
                            "updated_at": now,
                            "last_verified_at": now,
                        },
                        "$unset": {"revoked_at": ""},
                    },
                    upsert=True,
                )
            else:
                credential_id_b64 = str(credential.get("id") or "").strip()
                if credential_id_b64 not in (challenge_doc.get("credential_ids") or []):
                    raise ValueError("credential is not part of this renewal challenge")
                stored_query = {
                    "credential_id_b64": credential_id_b64,
                    "user_id": user["id"],
                    "revoked_at": {"$exists": False},
                }
                if purpose == "renew":
                    stored_query["device_hash"] = device_hash
                stored = await self.store.credentials.find_one(stored_query)
                if not stored:
                    raise ValueError("trusted credential was not found")
                verification = verify_authentication_response(
                    credential=credential,
                    expected_challenge=expected_challenge,
                    expected_rp_id=_rp_id(),
                    expected_origin=_configured_origin(),
                    credential_public_key=base64url_to_bytes(
                        stored["credential_public_key_b64"]
                    ),
                    credential_current_sign_count=int(stored.get("sign_count") or 0),
                    require_user_verification=True,
                )
                await self.store.credentials.update_one(
                    {"credential_id_b64": credential_id_b64},
                    {
                        "$set": {
                            "sign_count": int(verification.new_sign_count or 0),
                            "device_hash": device_hash,
                            "label": label or stored.get("label") or "جهاز موثوق",
                            "trust_expires_at": trust_expires_at,
                            "updated_at": now,
                            "last_verified_at": now,
                        }
                    },
                )

            await self.store.consume_challenge(challenge_id)
            await self.store.safe_event(
                "passkey_device_trusted",
                user,
                device_hash=device_hash,
                ceremony=purpose,
                trust_expires_at=trust_expires_at,
            )
            response = JSONResponse(
                {
                    "ok": True,
                    "trusted": True,
                    "trust_days": _trust_days(),
                    "trust_expires_at": trust_expires_at.isoformat(),
                }
            )
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        except Exception:
            remaining = await self.store.fail_challenge(challenge_id)
            logger.info("Trusted-device WebAuthn verification rejected")
            response = JSONResponse(
                {
                    "detail": "تعذر التحقق من بصمة/PIN هذا الجهاز. حاول مرة أخرى.",
                    "attempts_remaining": remaining,
                },
                status_code=401 if remaining > 0 else 429,
            )
        await response(scope, _replay_receive(messages), send)

    async def _authenticate_verify(self, scope, receive, send) -> None:
        body, messages = await _read_body(receive)
        payload = _json_object(body) or {}
        challenge_id = str(payload.get("challenge_id") or "").strip()
        credential = payload.get("credential")
        if not challenge_id or not isinstance(credential, dict):
            response = JSONResponse(
                {"detail": "بيانات التحقق من الجهاز غير مكتملة."}, status_code=400
            )
            await response(scope, _replay_receive(messages), send)
            return

        challenge_doc = await self.store.resolve_challenge(
            challenge_id, purpose="authenticate"
        )
        device_hash = _trusted_device_hash(scope)
        if not challenge_doc or not device_hash or challenge_doc.get("device_hash") != device_hash:
            response = JSONResponse(
                {"detail": "انتهت أو تغيرت محاولة التحقق. سجّل الدخول من جديد."},
                status_code=401,
            )
            await response(scope, _replay_receive(messages), send)
            return

        user = await self.db.users.find_one({"id": challenge_doc.get("user_id")})
        if not user or (user.get("role") or "").strip().lower() != OWNER_ROLE:
            await self.store.consume_challenge(challenge_id)
            response = JSONResponse({"detail": "تعذر إكمال التحقق."}, status_code=401)
            await response(scope, _replay_receive(messages), send)
            return

        credential_id_b64 = str(credential.get("id") or "").strip()
        try:
            if credential_id_b64 not in (challenge_doc.get("credential_ids") or []):
                raise ValueError("credential was not offered")
            stored = await self.store.credentials.find_one(
                {
                    "credential_id_b64": credential_id_b64,
                    "user_id": user["id"],
                    "device_hash": device_hash,
                    "revoked_at": {"$exists": False},
                    "trust_expires_at": {"$gt": _now()},
                }
            )
            if not stored:
                raise ValueError("trusted credential is expired or unavailable")
            verification = verify_authentication_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge_doc["challenge_b64"]),
                expected_rp_id=_rp_id(),
                expected_origin=_configured_origin(),
                credential_public_key=base64url_to_bytes(
                    stored["credential_public_key_b64"]
                ),
                credential_current_sign_count=int(stored.get("sign_count") or 0),
                require_user_verification=True,
            )
            now = _now()
            await self.store.credentials.update_one(
                {"credential_id_b64": credential_id_b64},
                {
                    "$set": {
                        "sign_count": int(verification.new_sign_count or 0),
                        "last_used_at": now,
                        "updated_at": now,
                    }
                },
            )
            await self.store.consume_challenge(challenge_id)
            await self.store.safe_event(
                "passkey_login_succeeded", user, device_hash=device_hash
            )
            response = _session_response(user, passkey_verified=True)
        except Exception:
            remaining = await self.store.fail_challenge(challenge_id)
            await self.store.safe_event(
                "passkey_login_failed",
                user,
                device_hash=device_hash,
                attempts_remaining=remaining,
            )
            response = JSONResponse(
                {
                    "detail": "تعذر التحقق من بصمة/PIN الجهاز.",
                    "attempts_remaining": remaining,
                },
                status_code=401 if remaining > 0 else 429,
            )
        await response(scope, _replay_receive(messages), send)


async def install_passkey_security(app, db, *, initialize_indexes: bool = True) -> None:
    """Install trusted-device passkey middleware once and prepare indexes."""
    if getattr(app.state, "mezan_passkey_security_installed", False):
        return
    store = PasskeyStore(db)
    # Independent web installation delegates index writes to the migration role.
    if initialize_indexes:
        await store.ensure_indexes()
    app.user_middleware.append(Middleware(PasskeySecurityMiddleware, db=db))
    app.middleware_stack = app.build_middleware_stack()
    app.state.mezan_passkey_security_installed = True
    logger.info(
        "Mezan Owner trusted-device passkeys enabled: rp_id=%s trust_days=%s",
        _rp_id(),
        _trust_days(),
    )
