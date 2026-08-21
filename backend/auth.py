"""Auth helpers: password hashing, JWT, current user dependency, admin seed."""
import logging
import os
import sys
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import Request, HTTPException

JWT_ALGORITHM = "HS256"
PRIVILEGED_MFA_ROLES = {"owner", "admin"}
BCRYPT_MAX_SECRET_BYTES = 72
ACCESS_TOKEN_TTL = timedelta(hours=12)
REFRESH_TOKEN_TTL = timedelta(days=30)
ACCESS_COOKIE_MAX_AGE_SECONDS = int(ACCESS_TOKEN_TTL.total_seconds())
REFRESH_COOKIE_MAX_AGE_SECONDS = int(REFRESH_TOKEN_TTL.total_seconds())
logger = logging.getLogger(__name__)

from meta_reviewer_access import (
    is_meta_reviewer,
    review_access_expired,
    reviewer_api_path_allowed,
)


def get_jwt_secret() -> str:
    return os.environ["JWT_SECRET"]


def _as_utc_timestamp(value) -> float | None:
    """Parse a stored ISO datetime for session-revocation checks."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def validate_bcrypt_secret(value: str | None) -> str | None:
    """Reject secrets bcrypt would otherwise truncate after 72 UTF-8 bytes."""
    if value is not None and len(value.encode("utf-8")) > BCRYPT_MAX_SECRET_BYTES:
        raise ValueError("يجب ألا تتجاوز كلمة المرور 72 بايت بعد ترميز UTF-8")
    return value


def hash_password(password: str) -> str:
    validate_bcrypt_secret(password)
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def account_is_disabled(user: dict | None) -> bool:
    """Return whether an account must be denied at every authentication gate."""
    return bool(
        user
        and (
            user.get("disabled") is True
            or user.get("is_active") is False
            or user.get("deleted_at")
            or review_access_expired(user)
        )
    )


def create_access_token(
    user_id: str,
    email: str,
    *,
    mfa_verified: bool = False,
    client_type: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "mfa": bool(mfa_verified),
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        "type": "access",
    }
    if client_type:
        payload["client"] = str(client_type).strip()
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(
    user_id: str,
    *,
    mfa_verified: bool = False,
    client_type: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "mfa": bool(mfa_verified),
        "iat": now,
        "exp": now + REFRESH_TOKEN_TTL,
        "type": "refresh",
    }
    if client_type:
        payload["client"] = str(client_type).strip()
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def set_auth_cookies(response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=ACCESS_COOKIE_MAX_AGE_SECONDS,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=REFRESH_COOKIE_MAX_AGE_SECONDS,
        path="/",
    )


def clear_auth_cookies(response) -> None:
    # Must mirror set_auth_cookies attributes (path, secure, samesite, httponly)
    # otherwise modern browsers treat the Set-Cookie as a different cookie and
    # do NOT remove the original session cookie — making logout a no-op.
    for name in ("access_token", "refresh_token"):
        response.delete_cookie(
            key=name,
            path="/",
            secure=True,
            samesite="none",
            httponly=True,
        )


def _extract_token(request: Request) -> Optional[str]:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    return token


def _token_predates_password_change(payload: dict, user: dict) -> bool:
    """Return whether a token must be revoked after a password change."""
    changed_at = _as_utc_timestamp(user.get("password_updated_at"))
    issued_at = payload.get("iat")
    return changed_at is not None and (
        not isinstance(issued_at, (int, float)) or float(issued_at) <= changed_at
    )


async def refresh_browser_session(request: Request, response, db) -> dict:
    """Rotate a valid browser refresh cookie into a fresh 30-day session.

    Refresh tokens are accepted only from the HttpOnly cookie, never from an
    Authorization header. Account disabling, reviewer expiry, password-change
    revocation, and MFA/OTP policy are re-checked on every rotation so a long
    browser session never bypasses current security state.
    """
    token = request.cookies.get("refresh_token")
    if not token:
        clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh token missing")

    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")

        user_id = payload.get("sub")
        user = await db.users.find_one({"id": user_id})
        if not user or account_is_disabled(user):
            raise HTTPException(status_code=401, detail="Account disabled")
        if _token_predates_password_change(payload, user):
            raise HTTPException(status_code=401, detail="Session revoked")

        mfa_verified = payload.get("mfa") is True
        role = str(user.get("role") or "").strip().lower()
        if role in PRIVILEGED_MFA_ROLES and not mfa_verified:
            raise HTTPException(status_code=401, detail="MFA verification required")

        if not mfa_verified:
            from email_otp_policy import requires_email_otp

            if await requires_email_otp(db, user):
                raise HTTPException(status_code=401, detail="Email OTP verification required")

        access = create_access_token(
            user["id"],
            user["email"],
            mfa_verified=mfa_verified,
        )
        refresh = create_refresh_token(user["id"], mfa_verified=mfa_verified)
        set_auth_cookies(response, access, refresh)
        return {"ok": True}
    except jwt.ExpiredSignatureError:
        clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.InvalidTokenError:
        clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    except HTTPException:
        clear_auth_cookies(response)
        raise


async def get_current_user_from_db(request: Request, db) -> dict:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id = payload.get("sub")
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if account_is_disabled(user):
            raise HTTPException(status_code=401, detail="Account disabled")

        # Password changes revoke every token minted before the change. Legacy
        # tokens without iat are rejected once password_updated_at exists.
        if _token_predates_password_change(payload, user):
            raise HTTPException(status_code=401, detail="Session revoked")

        if is_meta_reviewer(user) and not reviewer_api_path_allowed(request.url.path):
            raise HTTPException(
                status_code=403,
                detail={"code": "meta_review_path_denied"},
            )

        # Owner/Admin sessions are valid only after a second factor has been
        # verified. This intentionally invalidates privileged browser/API tokens
        # minted before MFA rollout; the user must sign in again and complete
        # enrollment/verification instead of silently inheriting a password-only
        # privileged session.
        role = (user.get("role") or "").strip().lower()
        if role in PRIVILEGED_MFA_ROLES and payload.get("mfa") is not True:
            raise HTTPException(status_code=401, detail="يلزم التحقق بخطوتين لإكمال تسجيل الدخول")

        # When email OTP is enabled, the same rule also applies immediately to
        # sensitive Employee OS accounts. This database-backed policy check
        # prevents an older password-only employee session from remaining valid
        # after its role gains a high-impact permission.
        if payload.get("mfa") is not True:
            from email_otp_policy import requires_email_otp

            if await requires_email_otp(db, user):
                raise HTTPException(
                    status_code=401,
                    detail="يلزم رمز التحقق المرسل إلى البريد لإكمال تسجيل الدخول",
                )

        user.pop("password_hash", None)
        user.pop("_id", None)
        # Private request context used only by server-side native-app policy.
        # It is derived from the signed JWT and cannot be supplied by a header.
        user["_session_client"] = str(payload.get("client") or "").strip() or None
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def _install_login_security_for_loaded_app(db) -> None:
    """Attach progressive abuse protection, passkeys, Owner MFA, and email OTP."""
    app = None
    for module_name in ("server", "backend.server"):
        module = sys.modules.get(module_name)
        candidate = getattr(module, "app", None) if module else None
        if candidate is not None:
            app = candidate
            break

    if app is None:
        logger.warning("Mezan auth security hook skipped: FastAPI app is not loaded")
        return

    # The merchant-approved five-attempt escalation is now owned by the
    # progressive account+device guard. Keep the older pair threshold out of
    # its way, while retaining broader spray protection across many accounts.
    os.environ.setdefault("AUTH_LOGIN_PAIR_LIMIT", "1000000")
    os.environ.setdefault("AUTH_LOGIN_DEVICE_LIMIT", "30")

    from meta_reviewer_bootstrap import install_meta_reviewer_bootstrap
    from mobile_session_security import install_mobile_session_security
    from progressive_login_security import install_progressive_login_security
    from login_security import install_login_security
    from passkey_security import install_passkey_security
    from mfa_security import install_mfa_security
    from email_otp_security import install_email_otp_security

    install_meta_reviewer_bootstrap(app, db)
    await install_mobile_session_security(app, db)

    # Order matters. Starlette appends each later guard inside the earlier one:
    # progressive_login_security remains outermost and owns the exact
    # account+device escalation ladder; login_security keeps device/IP spray
    # controls and signed identity; passkey can replace Owner TOTP only for an
    # exact trusted device; MFA remains the Owner TOTP authority. Email OTP is
    # deliberately innermost so Admin/sensitive accounts return its 202 email
    # challenge to the outer MFA layer, while Owner still reaches MFA/TOTP.
    await install_progressive_login_security(app, db)
    await install_login_security(app, db)
    await install_passkey_security(app, db)
    await install_mfa_security(app, db)
    await install_email_otp_security(app, db)


def _initial_owner_password() -> str:
    """Return the explicitly configured password for a *new* installation.

    Historical builds silently used ``admin123`` when ADMIN_PASSWORD was
    absent.  A production authentication system must never manufacture a
    privileged credential from source-code defaults, so fresh installations
    now fail closed until an operator supplies a real secret.
    """
    value = (os.environ.get("ADMIN_PASSWORD") or "").strip()
    if not value:
        raise RuntimeError(
            "ADMIN_PASSWORD must be configured before creating the initial Owner account"
        )
    if value.lower() == "admin123":
        raise RuntimeError(
            "ADMIN_PASSWORD cannot use the retired insecure default value"
        )
    return value


async def seed_admin(db) -> None:
    """Ensure one initial Owner exists without ever resetting a live password.

    Security invariants:
    - No source-code/default privileged password.
    - ADMIN_PASSWORD is creation-only; changing the environment later does not
      mutate an existing user's password.
    - If another Owner already exists under a different email, do not create a
      second Owner simply because ADMIN_EMAIL changed.
    """
    admin_email = (os.environ.get("ADMIN_EMAIL") or "admin@hesab.app").strip().lower()
    existing = await db.users.find_one({"email": admin_email})

    if existing is None:
        existing_owner = await db.users.find_one({"role": "owner"})
        if existing_owner is None:
            import uuid

            admin_password = _initial_owner_password()
            await db.users.insert_one({
                "id": str(uuid.uuid4()),
                "email": admin_email,
                "password_hash": hash_password(admin_password),
                "name": "المدير",
                "role": "owner",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            logger.info("Created initial Owner account for %s", admin_email)
        else:
            logger.info(
                "Owner already exists; skipped seed creation for configured ADMIN_EMAIL=%s",
                admin_email,
            )
    elif (existing.get("role") or "").lower() != "owner":
        # Preserve the historical one-owner invariant. If a separate owner
        # already exists, changing ADMIN_EMAIL must not silently create a second
        # owner via promotion.
        other_owner = await db.users.find_one(
            {"role": "owner", "id": {"$ne": existing.get("id")}}
        )
        if other_owner is None:
            await db.users.update_one(
                {"email": admin_email},
                {"$set": {"role": "owner"}},
            )
            logger.info("Promoted configured admin account to Owner: %s", admin_email)
        else:
            logger.warning(
                "Configured ADMIN_EMAIL is not Owner, but another Owner already exists; "
                "left roles unchanged"
            )

    # Deliberately do not compare or overwrite password_hash for an existing
    # user. Password changes must go through authenticated account-management
    # flows, never through a process restart or environment-variable drift.

    # Install the distributed auth guards after the normal seed work succeeds.
    # Index/setup failures fail startup rather than claiming the controls are on.
    await _install_login_security_for_loaded_app(db)


from payment_methods import DEFAULT_PAYMENT_METHODS  # noqa: F401 — re-exported


DEFAULT_SHIPPING_COMPANIES = [
    {"name": "سمسا", "cost_per_order": 23.0, "vat_percent": 15.0, "is_deferred": False},
    {"name": "جندل", "cost_per_order": 19.0, "vat_percent": 15.0, "is_deferred": False},
    {"name": "أرامكس", "cost_per_order": 27.0, "vat_percent": 15.0, "is_deferred": False},
    {"name": "DHL", "cost_per_order": 35.0, "vat_percent": 15.0, "is_deferred": False},
    {"name": "ريد بوكس", "cost_per_order": 21.0, "vat_percent": 15.0, "is_deferred": False},
]


async def ensure_user_settings(db, user_id: str) -> dict:
    settings = await db.settings.find_one({"user_id": user_id})
    if settings is None:
        settings = {
            "user_id": user_id,
            "payment_methods": DEFAULT_PAYMENT_METHODS,
            "shipping_companies": DEFAULT_SHIPPING_COMPANIES,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.settings.insert_one(settings)
    else:
        # iter-62 — backfill any new canonical payment methods that the
        # user is missing (added in newer releases). Preserves the user's
        # existing commission/vat edits — only APPENDS new rows.
        current_pms = settings.get("payment_methods") or []
        current_names = {(pm.get("name") or "").strip() for pm in current_pms}
        added = [pm for pm in DEFAULT_PAYMENT_METHODS
                 if (pm.get("name") or "").strip() not in current_names]
        if added:
            new_pms = list(current_pms) + added
            await db.settings.update_one(
                {"user_id": user_id},
                {"$set": {
                    "payment_methods": new_pms,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
            settings["payment_methods"] = new_pms
    settings.pop("_id", None)
    return settings
