"""Salla OAuth credentials store — DB-backed with .env fallback.

Lets the merchant paste Client ID / Client Secret / Redirect URI into the
Settings page UI instead of editing backend/.env. The values are stored
in the `salla_oauth_config` collection (singleton, _id='global'), with
the Client Secret encrypted via the same Fernet key used for tokens.

Resolution order at runtime:
    1. DB (salla_oauth_config) — if present
    2. backend/.env (SALLA_CLIENT_ID / SALLA_CLIENT_SECRET)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from .crypto import encrypt_token, decrypt_token


_CONFIG_ID = "global"


async def get_config(db) -> Optional[dict]:
    """Return the DB row (with decrypted secret) or None."""
    doc = await db.salla_oauth_config.find_one({"_id": _CONFIG_ID})
    if not doc:
        return None
    out = {
        "client_id": doc.get("client_id") or "",
        "redirect_uri": doc.get("redirect_uri") or "",
        "has_client_secret": bool(doc.get("client_secret_encrypted")),
        "updated_at": doc.get("updated_at"),
    }
    try:
        out["client_secret"] = decrypt_token(doc.get("client_secret_encrypted") or b"")
    except ValueError:
        out["client_secret"] = ""
    return out


async def save_config(db, *, client_id: str, client_secret: Optional[str], redirect_uri: Optional[str]) -> None:
    """Persist OAuth credentials. If client_secret is None/empty, the previous
    value is kept (so the UI can update client_id/redirect_uri alone)."""
    update: dict = {
        "client_id": (client_id or "").strip(),
        "redirect_uri": (redirect_uri or "").strip(),
        "updated_at": datetime.now(timezone.utc),
    }
    if client_secret:
        update["client_secret_encrypted"] = encrypt_token(client_secret.strip())
    await db.salla_oauth_config.update_one(
        {"_id": _CONFIG_ID},
        {"$set": update, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def delete_config(db) -> int:
    res = await db.salla_oauth_config.delete_one({"_id": _CONFIG_ID})
    return res.deleted_count


# ── Resolver helpers consumed by service.py ────────────────────────────
_DB_REF: dict = {"db": None}


def set_db_ref(db) -> None:
    """Called once at startup so the synchronous helpers below can read
    the OAuth config without taking an event-loop hop on every call."""
    _DB_REF["db"] = db


async def resolve_client_id(db) -> str:
    cfg = await get_config(db)
    if cfg and cfg.get("client_id"):
        return cfg["client_id"]
    return (os.environ.get("SALLA_CLIENT_ID") or "").strip()


async def resolve_client_secret(db) -> str:
    cfg = await get_config(db)
    if cfg and cfg.get("client_secret"):
        return cfg["client_secret"]
    return (os.environ.get("SALLA_CLIENT_SECRET") or "").strip()


async def resolve_redirect_uri(db) -> str:
    cfg = await get_config(db)
    if cfg and cfg.get("redirect_uri"):
        return cfg["redirect_uri"]
    return ""  # falls back to request-derived URL in routes.py
