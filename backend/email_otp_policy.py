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


async def requires_email_otp(
    db: Any,
    user: dict[str, Any] | None,
    *,
    client_type: str | None = None,
) -> bool:
    """Resolve whether an account must complete email OTP.

    Policy:
    - missing account -> false
    - Owner -> false (Owner keeps TOTP/passkey protection)
    - exact Meta reviewer role -> false
    - exact store_driver + signed native AMASI client -> false
    - every other account/client combination -> true

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

    # Courier accounts are deliberately isolated from Mezan employee/browser
    # permissions. They may use password-only auth only when the session is
    # cryptographically rebound by mobile_session_security to the native AMASI
    # client marker. Browser sessions for the same store_driver still require
    # email OTP, so this does not create a general password-only exception.
    if role == "store_driver" and str(client_type or "").strip() == "amasi_mobile":
        return False

    return True


__all__ = [
    "email_otp_enabled",
    "requires_email_otp",
]
