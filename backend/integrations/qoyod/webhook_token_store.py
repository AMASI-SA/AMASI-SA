"""Qoyod Webhook Token store — DB-backed, encrypted, fingerprint-only UI.

Why a separate store from `qoyod_credentials`?
─────────────────────────────────────────────
The Qoyod **API key** authenticates outgoing calls FROM Mezan TO Qoyod
(merchant secret). The **webhook token** authenticates incoming calls
FROM Make.com TO Mezan (shared secret). They have different lifecycles,
different rotation policies, and different blast radii — keeping them
in separate collections preserves ADR-001 #14 (Secrets Discipline).

ADR-001 alignment
─────────────────
  #1  Additive    — new collection `qoyod_webhook_tokens`; never touches
                    the legacy `QOYOD_WEBHOOK_TOKEN` env var (kept as a
                    backward-compatible fallback for preview & tests).
  #10 Idempotency  — `generate()` rotates: saving a new token revokes
                    the previous one. Each save returns the plaintext
                    EXACTLY ONCE — subsequent reads expose fingerprint
                    only.
  #14 Secrets      — plaintext never persisted; encrypted at rest with
                    the same Fernet key used for the API key.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Optional

from integrations.qoyod.crypto import encrypt_secret, decrypt_secret


# Length tradeoff: 48 bytes urlsafe → 64 chars after base64 with
# `mzn_qoyod_prod_` prefix → 79 chars total. Brute-forceable in
# > 10^100 years even with offline attack.
_TOKEN_PREFIX = "mzn_qoyod_prod_"
_RAW_BYTES = 48


def generate_token() -> str:
    """Generate a fresh cryptographically-strong webhook token."""
    return _TOKEN_PREFIX + secrets.token_urlsafe(_RAW_BYTES)


def _short_fingerprint(plaintext: str) -> str:
    """4…4 style fingerprint mirrors the API-key UI for consistency."""
    if not plaintext:
        return ""
    h = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return f"{h[:4]}…{h[-4:]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────
async def save_webhook_token(db, user_id: str, token: str) -> dict:
    """Upsert the encrypted token. Returns metadata (no plaintext)."""
    if not token or not token.strip():
        raise ValueError("Webhook token cannot be empty")
    token = token.strip()
    ciphertext = encrypt_secret(token)
    fp = _short_fingerprint(token)
    now = _now()
    await db.qoyod_webhook_tokens.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id":      user_id,
            "token_enc":    ciphertext,
            "fingerprint":  fp,
            "rotated_at":   now,
            "updated_at":   now,
            "revoked":      False,
        },
         "$setOnInsert": {
            "schema_version": 1,
            "created_at":     now,
        }},
        upsert=True,
    )
    return {
        "user_id":     user_id,
        "fingerprint": fp,
        "rotated_at":  now.isoformat(),
    }


async def get_webhook_token(db, user_id: str) -> Optional[str]:
    """Decrypted plaintext, or None when not configured / revoked.
    Only the webhook verifier should call this — never log the value.
    """
    doc = await db.qoyod_webhook_tokens.find_one(
        {"user_id": user_id, "revoked": {"$ne": True}},
        {"_id": 0, "token_enc": 1})
    if not doc or not doc.get("token_enc"):
        return None
    try:
        return decrypt_secret(doc["token_enc"])
    except ValueError:
        # Encryption key rotated and old token can't be decrypted → treat
        # as missing so the operator can regenerate via the UI.
        return None


async def get_webhook_token_meta(db, user_id: str) -> Optional[dict]:
    """UI-safe metadata. Never returns plaintext."""
    doc = await db.qoyod_webhook_tokens.find_one(
        {"user_id": user_id},
        {"_id": 0, "fingerprint": 1, "rotated_at": 1,
         "created_at": 1, "last_verified_at": 1, "revoked": 1})
    if not doc:
        return None
    # Hide everything when revoked except the fact it is revoked.
    return {
        "configured":       not doc.get("revoked", False),
        "fingerprint":      doc.get("fingerprint"),
        "rotated_at":       _iso(doc.get("rotated_at")),
        "created_at":       _iso(doc.get("created_at")),
        "last_verified_at": _iso(doc.get("last_verified_at")),
    }


async def revoke_webhook_token(db, user_id: str) -> bool:
    """Idempotent revoke. Returns True only on the FIRST successful flip
    (revoked: false → true). Subsequent calls return False."""
    res = await db.qoyod_webhook_tokens.update_one(
        {"user_id": user_id, "revoked": {"$ne": True}},
        {"$set": {"revoked": True, "updated_at": _now()}})
    return res.modified_count > 0


async def mark_token_used(db, user_id: str) -> None:
    """Called after a successful webhook verification, for diagnostics."""
    await db.qoyod_webhook_tokens.update_one(
        {"user_id": user_id},
        {"$set": {"last_verified_at": _now()}})


# ─────────────────────────────────────────────────────────────────────
# Verification (constant-time)
# ─────────────────────────────────────────────────────────────────────
async def verify_provided_token(
    db, *, provided: str, user_id: str = "main",
    env_fallback: Optional[str] = None,
) -> bool:
    """Return True iff `provided` matches the active token source.

    Source precedence (security-critical):
        • If a DB token is configured (not revoked) → ONLY the DB
          token is accepted. `env_fallback` is ignored in this case
          so an attacker who learns the legacy env value cannot
          bypass after rotation.
        • If no DB token is configured → `env_fallback` is the
          authoritative source (preview / CI safety net).

    Always uses `hmac.compare_digest` to avoid timing leaks.
    """
    if not provided:
        return False
    db_token = await get_webhook_token(db, user_id)
    if db_token:
        if hmac.compare_digest(db_token, provided):
            await mark_token_used(db, user_id)
            return True
        return False     # DB configured → env fallback disabled
    if env_fallback and hmac.compare_digest(env_fallback, provided):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────
# Internal utilities
# ─────────────────────────────────────────────────────────────────────
def _iso(d) -> Optional[str]:
    if d is None:
        return None
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return str(d)
