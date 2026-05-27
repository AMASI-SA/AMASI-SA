"""Auth helpers: password hashing, JWT, current user dependency, admin seed."""
import os
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import Request, HTTPException

JWT_ALGORITHM = "HS256"


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
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    elif not verify_password(admin_password, existing.get("password_hash", "")):
        await db.users.update_one(
            {"email": admin_email},
            {"$set": {"password_hash": hash_password(admin_password)}},
        )


DEFAULT_PAYMENT_METHODS = [
    {"name": "مدى", "commission_percent": 1.0, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": "Apple Pay", "commission_percent": 2.5, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": "تمارا", "commission_percent": 6.99, "fixed_fee": 0.0, "vat_percent": 15.0},
    {"name": "تابي", "commission_percent": 5.0, "fixed_fee": 0.0, "vat_percent": 15.0},
    {"name": "إمكان", "commission_percent": 5.0, "fixed_fee": 0.0, "vat_percent": 15.0},
    {"name": "بطاقة ائتمانية", "commission_percent": 2.75, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": "الدفع عند الاستلام", "commission_percent": 0.0, "fixed_fee": 0.0, "vat_percent": 0.0},
]

DEFAULT_SHIPPING_COMPANIES = [
    {"name": "سمسا", "cost_per_order": 23.0, "vat_percent": 15.0},
    {"name": "جندل", "cost_per_order": 19.0, "vat_percent": 15.0},
    {"name": "أرامكس", "cost_per_order": 27.0, "vat_percent": 15.0},
    {"name": "DHL", "cost_per_order": 35.0, "vat_percent": 15.0},
    {"name": "ريد بوكس", "cost_per_order": 21.0, "vat_percent": 15.0},
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
    settings.pop("_id", None)
    return settings
