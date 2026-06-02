"""Fernet token encryption for Salla integration.

Why a separate module?
    Tokens are by far the most sensitive piece of data in the entire
    application — anyone with a valid Salla refresh_token can act as the
    merchant on Salla's API. We isolate the encryption key in env-var
    (`SALLA_TOKEN_ENC_KEY`) and never log raw tokens.

Why Fernet (symmetric authenticated encryption)?
    Fernet (per the cryptography library) is AES-128-CBC + HMAC-SHA-256
    with version + timestamp + IV baked in. It's tamper-evident — flip
    a bit in the ciphertext and decrypt() raises InvalidToken. Simpler
    than MongoDB FLE and good enough for our threat model (db dump leak,
    accidental log exposure).

Key rotation
    Set BOTH `SALLA_TOKEN_ENC_KEY` (new key, used for new encryptions)
    and `SALLA_TOKEN_ENC_KEY_OLD` (previous key, decrypt-only fallback).
    Re-encrypt records lazily on next refresh.
"""
from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


def _load_fernet() -> MultiFernet:
    """Build a MultiFernet from primary + optional rotation key.

    Raises a clear runtime error if SALLA_TOKEN_ENC_KEY is unset because
    silent fallbacks to plaintext storage would be catastrophic.
    """
    primary = os.environ.get("SALLA_TOKEN_ENC_KEY", "").strip()
    if not primary:
        raise RuntimeError(
            "SALLA_TOKEN_ENC_KEY is not set in backend/.env — generate one "
            "with `python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'`",
        )
    keys = [Fernet(primary.encode())]
    rotation = os.environ.get("SALLA_TOKEN_ENC_KEY_OLD", "").strip()
    if rotation:
        keys.append(Fernet(rotation.encode()))
    return MultiFernet(keys)


_fernet: Optional[MultiFernet] = None


def _get() -> MultiFernet:
    global _fernet
    if _fernet is None:
        _fernet = _load_fernet()
    return _fernet


def encrypt_token(plaintext: str) -> bytes:
    """Encrypt a token string → opaque bytes safe to store in Mongo."""
    if not plaintext:
        return b""
    return _get().encrypt(plaintext.encode("utf-8"))


def decrypt_token(ciphertext: Optional[bytes]) -> str:
    """Reverse of encrypt_token. Returns "" if input is falsy/empty.

    Raises ValueError on tamper/wrong-key so callers can mark the
    integration as needs_reauth and prompt the user.
    """
    if not ciphertext:
        return ""
    try:
        return _get().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Token decryption failed — encryption key may have rotated") from exc
