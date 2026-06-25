"""Fernet wrapper for Qoyod API keys.

ADR-001 #14 (Secrets Discipline) — API keys are never stored in plain
text and never appear in logs or API responses. We mirror the proven
pattern from `salla_integration/crypto.py` but use a SEPARATE key so a
key rotation in one connector doesn't affect the other.

Env vars:
    QOYOD_TOKEN_ENC_KEY      primary Fernet key (mandatory).
    QOYOD_TOKEN_ENC_KEY_OLD  optional rotation key (decrypt-only).

Key rotation procedure:
    1. Generate a new Fernet key.
    2. Set the OLD env var to the current key, and the primary env var
       to the new key.
    3. Re-encrypt records lazily as they pass through `credentials.py`.
"""
from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


def _load_fernet() -> MultiFernet:
    primary = os.environ.get("QOYOD_TOKEN_ENC_KEY", "").strip()
    if not primary:
        raise RuntimeError(
            "QOYOD_TOKEN_ENC_KEY is not set in backend/.env — generate one "
            "with `python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'`",
        )
    keys = [Fernet(primary.encode())]
    rotation = os.environ.get("QOYOD_TOKEN_ENC_KEY_OLD", "").strip()
    if rotation:
        keys.append(Fernet(rotation.encode()))
    return MultiFernet(keys)


_fernet: Optional[MultiFernet] = None


def _get() -> MultiFernet:
    global _fernet
    if _fernet is None:
        _fernet = _load_fernet()
    return _fernet


def encrypt_secret(plaintext: str) -> bytes:
    """Encrypt a secret string → opaque bytes safe to store in Mongo."""
    if not plaintext:
        return b""
    return _get().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: Optional[bytes]) -> str:
    """Reverse of encrypt_secret. Returns '' on falsy input.

    Raises ValueError on tamper/wrong-key so callers can flag the
    connector as needs_reauth and prompt the merchant.
    """
    if not ciphertext:
        return ""
    try:
        return _get().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Qoyod credential decryption failed — encryption key may "
            "have rotated") from exc
