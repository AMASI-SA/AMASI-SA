"""Qoyod credential store — encrypted API key persistence.

ADR-001 #14 (Secrets Discipline) — the only module that ever sees the
plaintext API key. Callers always go through `save_api_key()` /
`get_api_key()`; never read `qoyod_credentials.api_key_enc` directly.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from integrations.qoyod.crypto import encrypt_secret, decrypt_secret


def _fingerprint(plaintext: str) -> str:
    """Short, non-reversible identifier shown in the UI ("ends …a3f9")."""
    if not plaintext:
        return ""
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return f"{digest[:4]}…{digest[-4:]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def save_api_key(db, user_id: str, api_key: str) -> dict:
    """Upsert the encrypted credential. Returns the document minus the
    ciphertext (safe for response payloads)."""
    if not api_key or not api_key.strip():
        raise ValueError("Qoyod API key cannot be empty")
    api_key = api_key.strip()
    ciphertext = encrypt_secret(api_key)
    fp = _fingerprint(api_key)
    now = _now()
    await db.qoyod_credentials.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id":        user_id,
            "api_key_enc":    ciphertext,
            "fingerprint":    fp,
            "updated_at":     now,
            "rotated_at":     now,
        },
         "$setOnInsert": {
            "schema_version": 1,
            "created_at":     now,
        }},
        upsert=True,
    )
    return {"user_id": user_id, "fingerprint": fp, "updated_at": now}


async def get_api_key(db, user_id: str) -> Optional[str]:
    """Returns the decrypted plaintext key or None if missing.
    Only the API client should call this — never log the return value."""
    doc = await db.qoyod_credentials.find_one(
        {"user_id": user_id}, {"_id": 0, "api_key_enc": 1})
    if not doc:
        return None
    ciphertext = doc.get("api_key_enc")
    if not ciphertext:
        return None
    return decrypt_secret(ciphertext)


async def get_fingerprint(db, user_id: str) -> Optional[str]:
    """Returns the public fingerprint for UI display (no plaintext)."""
    doc = await db.qoyod_credentials.find_one(
        {"user_id": user_id}, {"_id": 0, "fingerprint": 1})
    return (doc or {}).get("fingerprint")


async def mark_verified(db, user_id: str) -> None:
    await db.qoyod_credentials.update_one(
        {"user_id": user_id},
        {"$set": {"last_verified_at": _now()}},
    )


async def delete_api_key(db, user_id: str) -> bool:
    res = await db.qoyod_credentials.delete_one({"user_id": user_id})
    return res.deleted_count > 0
