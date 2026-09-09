"""Qoyod credential store — encrypted API key persistence.

ADR-001 #14 (Secrets Discipline) — the only module that ever sees the
plaintext API key. Callers always go through `save_api_key()` /
`get_api_key()`; never read `qoyod_credentials.api_key_enc` directly.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pymongo import ReturnDocument

from integrations.qoyod.crypto import encrypt_secret, decrypt_secret


class QoyodCredentialDecryptionError(Exception):
    """Safe signal for an unreadable stored credential.

    The exception deliberately carries only the already-public fingerprint;
    callers must never expose the ciphertext or the underlying crypto error.
    """

    def __init__(self, fingerprint: Optional[str]):
        super().__init__("stored Qoyod credential could not be decrypted")
        self.fingerprint = fingerprint


class QoyodCredentialEncryptionError(Exception):
    """Safe signal for a failure before encrypted persistence."""


class QoyodCredentialStorageError(Exception):
    """Safe signal for a failed or unconfirmed credential write."""


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
    try:
        ciphertext = encrypt_secret(api_key)
    except Exception as exc:
        raise QoyodCredentialEncryptionError(
            "Qoyod credential encryption failed"
        ) from exc
    fp = _fingerprint(api_key)
    now = _now()
    credential_version = uuid4().hex
    try:
        persisted = await db.qoyod_credentials.find_one_and_update(
            {"user_id": user_id},
            {
                "$set": {
                    "user_id": user_id,
                    "api_key_enc": ciphertext,
                    "fingerprint": fp,
                    "credential_version": credential_version,
                    # Preserve last_verified_at as historical evidence, but
                    # detach it from the newly saved credential version.
                    "last_verified_credential_version": None,
                    "updated_at": now,
                    "rotated_at": now,
                },
                "$setOnInsert": {
                    "schema_version": 1,
                    "created_at": now,
                },
            },
            upsert=True,
            projection={
                "_id": 0,
                "user_id": 1,
                "fingerprint": 1,
                "credential_version": 1,
                "updated_at": 1,
            },
            return_document=ReturnDocument.AFTER,
        )
    except Exception as exc:
        raise QoyodCredentialStorageError(
            "Qoyod credential persistence failed"
        ) from exc
    if (
        not persisted
        or persisted.get("credential_version") != credential_version
        or persisted.get("fingerprint") != fp
    ):
        raise QoyodCredentialStorageError(
            "Qoyod credential persistence was not confirmed"
        )
    return {
        "user_id": persisted["user_id"],
        "fingerprint": persisted["fingerprint"],
        "updated_at": persisted["updated_at"],
    }


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


async def get_api_key_with_fingerprint(
    db,
    user_id: str,
) -> Optional[tuple[str, Optional[str], str]]:
    """Read the key and its fingerprint from one credential document.

    This is used where the public result must identify the exact credential
    that was consumed.  A later rotation therefore cannot relabel an older
    in-flight connection result with the new fingerprint.
    """
    projection = {
        "_id": 0,
        "api_key_enc": 1,
        "fingerprint": 1,
        "credential_version": 1,
        "last_verified_at": 1,
        "last_verified_credential_version": 1,
        "rotated_at": 1,
    }
    for _attempt in range(2):
        try:
            doc = await db.qoyod_credentials.find_one(
                {"user_id": user_id}, projection,
            )
        except Exception as exc:
            raise QoyodCredentialStorageError(
                "Qoyod credential read failed"
            ) from exc
        if not doc or not doc.get("api_key_enc"):
            return None
        try:
            key = decrypt_secret(doc["api_key_enc"])
        except Exception as exc:
            raise QoyodCredentialDecryptionError(
                doc.get("fingerprint")
            ) from exc
        credential_version = doc.get("credential_version")
        if credential_version:
            return key, doc.get("fingerprint"), credential_version

        # Compatibility for credentials saved before credential_version was
        # introduced.  The ciphertext is an internal, exact compare token;
        # if a rotation wins this race, the conditional update matches zero
        # rows and the loop rereads the new document before any provider call.
        credential_version = uuid4().hex
        last_verified_at = doc.get("last_verified_at")
        rotated_at = doc.get("rotated_at")
        legacy_verification_current = bool(last_verified_at)
        if legacy_verification_current and rotated_at is not None:
            try:
                legacy_verification_current = last_verified_at >= rotated_at
            except TypeError:
                legacy_verification_current = False
        try:
            migrated = await db.qoyod_credentials.find_one_and_update(
                {
                    "user_id": user_id,
                    "api_key_enc": doc["api_key_enc"],
                },
                {"$set": {
                    "credential_version": credential_version,
                    "last_verified_credential_version": (
                        credential_version
                        if legacy_verification_current
                        else None
                    ),
                }},
                projection=projection,
                return_document=ReturnDocument.AFTER,
            )
        except Exception as exc:
            raise QoyodCredentialStorageError(
                "Qoyod credential version migration failed"
            ) from exc
        if migrated:
            return key, migrated.get("fingerprint"), credential_version
    raise QoyodCredentialStorageError(
        "Qoyod credential changed repeatedly while being read"
    )


async def get_fingerprint(db, user_id: str) -> Optional[str]:
    """Returns the public fingerprint for UI display (no plaintext)."""
    doc = await db.qoyod_credentials.find_one(
        {"user_id": user_id}, {"_id": 0, "fingerprint": 1})
    return (doc or {}).get("fingerprint")


async def mark_verified(
    db,
    user_id: str,
    *,
    credential_version: str,
) -> bool:
    verified_at = _now()
    try:
        result = await db.qoyod_credentials.update_one(
            {
                "user_id": user_id,
                "credential_version": credential_version,
            },
            {
                "$set": {
                    "last_verified_at": verified_at,
                    "last_verified_credential_version": credential_version,
                },
            },
        )
    except Exception as exc:
        raise QoyodCredentialStorageError(
            "Qoyod credential verification persistence failed"
        ) from exc
    return bool(getattr(result, "matched_count", 0) == 1)


async def delete_api_key(db, user_id: str) -> bool:
    res = await db.qoyod_credentials.delete_one({"user_id": user_id})
    return res.deleted_count > 0
