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
logger = logging.getLogger(__name__)


def get_jwt_secret() -> str:
    return os.environ["JWT_SECRET"]


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=60 * 12),
        "type": "access",
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "type": "refresh",
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def set_auth_cookies(response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=60 * 60 * 12,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=60 * 60 * 24 * 7,
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
        user.pop("password_hash", None)
        user.pop("_id", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def _install_login_security_for_loaded_app(db) -> None:
    """Attach the login guard during the existing auth startup sequence.

    ``server.py`` is a large legacy bootstrap module.  Keeping the hook here
    lets the security layer remain isolated without touching order, ads,
    accounting, or fulfillment routes.  The import is intentionally local so
    unit tests that use the auth helpers alone do not import the application.
    """
    app = None
    for module_name in ("server", "backend.server"):
        module = sys.modules.get(module_name)
        candidate = getattr(module, "app", None) if module else None
        if candidate is not None:
            app = candidate
            break

    if app is None:
        logger.warning("Mezan login security hook skipped: FastAPI app is not loaded")
        return

    from login_security import install_login_security

    await install_login_security(app, db)


async def seed_admin(db) -> None:
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@hesab.app").lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if existing is None:
        import uuid
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": admin_email,
            "password_hash": hash_password(admin_password),
            "name": "المدير",
            # iter-51 — the seeded user is the Owner (highest privilege).
            # Only one Owner per installation; cannot be downgraded via API.
            "role": "owner",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    else:
        updates = {}
        if not verify_password(admin_password, existing.get("password_hash", "")):
            updates["password_hash"] = hash_password(admin_password)
        # iter-51 — promote the existing seeded admin to Owner so the team
        # management endpoints work out of the box. Idempotent: skipped
        # if already owner.
        if (existing.get("role") or "").lower() != "owner":
            updates["role"] = "owner"
        if updates:
            await db.users.update_one({"email": admin_email}, {"$set": updates})

    # Install the distributed login guard after the normal seed work succeeds.
    # Any index/setup failure is allowed to fail startup rather than silently
    # claiming that the abuse protection is active when it is not.
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
