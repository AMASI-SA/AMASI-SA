"""Shared policy for Mezan email one-time-password (OTP) sign-in.

The Owner deliberately stays on TOTP + trusted-device passkeys. Email OTP is
reserved for Admin and other sensitive non-Owner accounts. This module has no
HTTP or mail-delivery code so authentication and middleware can share exactly
the same policy without circular imports.
"""
from __future__ import annotations

import os
from typing import Any

from ai_store_access_contract import ROLE_ASSIGNMENTS, effective_permissions


DEFAULT_SENSITIVE_ROLES = {"admin", "accountant"}
DEFAULT_SENSITIVE_PERMISSIONS = {
    "products.publish",
    "products.cost.write",
    "employees.manage",
    "roles.manage",
    "suppliers.manage",
    "inventory.salla_sync.publish",
    "products.ai.execute_high_risk",
}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def email_otp_enabled() -> bool:
    """Return whether email OTP is enabled for this deployment."""
    return _truthy(os.environ.get("EMAIL_OTP_ENABLED", "0"))


def _env_csv(name: str, defaults: set[str]) -> set[str]:
    raw = os.environ.get(name)
    if raw is None:
        return set(defaults)
    return {
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    }


def sensitive_roles() -> set[str]:
    return _env_csv("EMAIL_OTP_SENSITIVE_ROLES", DEFAULT_SENSITIVE_ROLES)


def sensitive_permissions() -> set[str]:
    return _env_csv(
        "EMAIL_OTP_SENSITIVE_PERMISSIONS",
        DEFAULT_SENSITIVE_PERMISSIONS,
    )


async def requires_email_otp(db: Any, user: dict[str, Any] | None) -> bool:
    """Resolve whether a non-Owner account must complete email OTP.

    Resolution order:
    - feature disabled -> false (existing TOTP behavior stays intact)
    - Owner -> false, always
    - explicit per-user flag -> true/false when present
    - configured legacy account role (Admin/Accountant by default) -> true
    - Employee OS operational assignment with a sensitive effective permission
      -> true

    The assignment lookup is scoped by the login account's user_id and uses the
    same effective-permission resolver as Employee OS, so denied permissions are
    respected rather than inferred from role names alone.
    """
    if not email_otp_enabled() or not user:
        return False

    role = str(user.get("role") or "").strip().lower()
    if role == "owner":
        return False

    if "email_otp_required" in user:
        return bool(user.get("email_otp_required"))

    if role in sensitive_roles():
        return True

    user_id = str(user.get("id") or "").strip()
    if not user_id:
        return False

    assignment = await db[ROLE_ASSIGNMENTS].find_one(
        {"user_id": user_id},
        {"_id": 0},
    )
    if not assignment or assignment.get("enabled", True) is False:
        return False

    return bool(
        set(effective_permissions(assignment))
        & sensitive_permissions()
    )


__all__ = [
    "DEFAULT_SENSITIVE_PERMISSIONS",
    "DEFAULT_SENSITIVE_ROLES",
    "email_otp_enabled",
    "requires_email_otp",
    "sensitive_permissions",
    "sensitive_roles",
]
