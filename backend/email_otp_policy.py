"""Shared policy for Mezan email one-time-password (OTP) sign-in.

The Owner deliberately stays on TOTP + trusted-device passkeys. Every other
employee account must complete email OTP. The only password-only exception is
the isolated, time-bounded Meta reviewer account.
"""
from __future__ import annotations

from typing import Any


def email_otp_enabled() -> bool:
    """Email OTP is mandatory and cannot be disabled by deployment flags."""
    return True


async def requires_email_otp(db: Any, user: dict[str, Any] | None) -> bool:
    """Resolve whether an account must complete email OTP.

    Policy:
    - missing account -> false
    - Owner -> false (Owner keeps TOTP/passkey protection)
    - exact Meta reviewer role -> false
    - every other employee account -> true

    The db argument remains in the signature because callers share this async
    policy contract and older deployments still pass a database handle.
    """
    del db

    if not user:
        return False

    role = str(user.get("role") or "").strip().lower()
    if role == "owner":
        return False

    if role == "meta_reviewer":
        return False

    return True


__all__ = [
    "email_otp_enabled",
    "requires_email_otp",
]
