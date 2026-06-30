"""Salla Easy Mode webhook receiver (Iter-292).

Salla's Partners Portal supports two OAuth flows:

  • **Custom Mode** — merchant browser is redirected through
    `/oauth/login → Salla consent screen → /oauth/callback?code=...` and
    Mezan calls `/oauth2/token` to exchange the `code` for tokens. This
    is implemented in `routes.py`.

  • **Easy Mode (this module)** — for apps published on the Salla App
    Store. Salla itself runs the entire OAuth exchange server-side and
    POSTs the resulting tokens to Mezan via a webhook event called
    `app.store.authorize`. The merchant never sees the consent screen
    in our domain — they just click "Install" inside their Salla
    dashboard.

Endpoint contract
-----------------
  POST /api/salla/webhooks/app   (PUBLIC — gated by HMAC signature)

Headers (Salla → Mezan):
  x-salla-signature : HMAC-SHA256(raw_body, webhook_secret), hex digest.

Body schema (Salla docs, doc-421413):
  {
    "event":      "app.store.authorize",
    "merchant":   "1233666",                    # store id (numeric)
    "created_at": "Sun Apr 30 2023 11:57:42 GMT+0300",
    "data": {
      "access_token":  "ory_at_...",
      "expires":       1714463862,             # unix timestamp
      "refresh_token": "ory_rt_...",
      "scope":         "settings.read offline_access ...",
      "token_type":    "Bearer"
    }
  }

Events we handle (all others = 200 OK no-op log):
  • app.store.authorize  → store tokens, mark connected
  • app.installed        → log (tokens come with .authorize, not here)
  • app.updated          → re-fetch /store/info
  • app.uninstalled      → mark not_connected (keep history)

Security invariants (NON-NEGOTIABLE)
------------------------------------
  1. NO token is ever persisted if signature verification fails.
  2. If SALLA_WEBHOOK_SECRET is unset in env, the endpoint returns
     503 SALLA_WEBHOOK_SECRET_NOT_CONFIGURED — never silently accepts.
  3. Constant-time comparison via hmac.compare_digest.
  4. Verification runs on the RAW request body, not after JSON parsing
     (whitespace/key-order changes would otherwise break the HMAC).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from .service import (
    DEFAULT_SCOPES,  # noqa: F401 — re-exported for diagnostic logs
    _expires_at,
    encrypt_token,
    upsert_integration,
)

log = logging.getLogger("salla.easy_mode")


# ── Constants ─────────────────────────────────────────────────────────
SIGNATURE_HEADER = "x-salla-signature"
ENV_WEBHOOK_SECRET = "SALLA_WEBHOOK_SECRET"

# Map event names to handler functions (registered below).
# Anything not in this map is ack'd with 200 + a no-op log line.
EVENT_HANDLERS: dict[str, str] = {
    "app.store.authorize": "_handle_store_authorize",
    "app.installed":       "_handle_app_installed",
    "app.updated":         "_handle_app_updated",
    "app.uninstalled":     "_handle_app_uninstalled",
}


# ── Signature verification ────────────────────────────────────────────
def get_webhook_secret() -> Optional[str]:
    """Return the configured Salla webhook secret (or None if unset).

    This is a function (not a module-level constant) so a process can
    pick up a new value on .env change after `supervisorctl restart
    backend` — no reload of this module needed.
    """
    raw = os.environ.get(ENV_WEBHOOK_SECRET) or ""
    return raw.strip() or None


def verify_signature(raw_body: bytes, provided_signature: str, secret: str) -> bool:
    """Constant-time HMAC-SHA256 verification.

    Returns True ONLY if `provided_signature` (hex) matches
    HMAC-SHA256(raw_body, secret). Strictly rejects empty / malformed
    signatures.
    """
    if not provided_signature or not secret:
        return False
    expected = hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    # Normalize: some clients send the signature uppercased.
    return hmac.compare_digest(expected, (provided_signature or "").strip().lower())


# ── Owner resolution ──────────────────────────────────────────────────
async def resolve_owner_user_id(db) -> tuple[Optional[str], Optional[str]]:
    """Return `(user_id, email)` of the merchant who owns this Mezan
    install. Strategy: oldest user with `role == 'owner'`.

    Mezan is currently a single-tenant SaaS — one merchant per instance.
    `created_at` is stored as an ISO-8601 string at registration so
    lexicographic sort is equivalent to chronological sort.

    If multiple owners exist (rare), we pick the EARLIEST one and emit
    a warning. If no owner exists we fall through to the earliest user
    of any role — still deterministic.
    """
    # 1) Earliest owner.
    cursor = (
        db.users
        .find({"role": "owner"}, {"id": 1, "email": 1, "created_at": 1})
        .sort("created_at", 1)
        .limit(5)
    )
    owners = await cursor.to_list(length=5)
    if owners:
        if len(owners) > 1:
            log.warning(
                "easy_mode.multiple_owners count=%d picked=%s emails=%s",
                len(owners),
                owners[0].get("email"),
                [o.get("email") for o in owners],
            )
        first = owners[0]
        return first.get("id"), first.get("email")

    # 2) No owner → fall back to earliest user (so an Easy Mode install
    #    on a fresh DB still has *somewhere* to land — we mark this with
    #    a warning so it's diagnosable).
    fallback = await db.users.find_one(
        {}, {"id": 1, "email": 1, "created_at": 1},
        sort=[("created_at", 1)],
    )
    if fallback:
        log.warning(
            "easy_mode.no_owner_using_first_user email=%s",
            fallback.get("email"),
        )
        return fallback.get("id"), fallback.get("email")

    return None, None


# ── Event handlers ────────────────────────────────────────────────────
async def _handle_store_authorize(db, event_body: dict) -> dict:
    """Persist tokens delivered by Salla via `app.store.authorize`.

    `event_body` is the full JSON payload Salla sent us (already
    HMAC-verified by the caller).
    """
    data = event_body.get("data") or {}
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    expires_unix = data.get("expires")  # unix timestamp (seconds), per docs
    scope = data.get("scope") or DEFAULT_SCOPES
    token_type = data.get("token_type") or "Bearer"
    merchant_id = event_body.get("merchant")  # store id

    if not access:
        return {
            "ok": False,
            "stored": False,
            "reason": "missing access_token in payload",
        }

    # Resolve which Mezan user owns this install.
    user_id, user_email = await resolve_owner_user_id(db)
    if not user_id:
        log.error(
            "easy_mode.no_user_for_install merchant=%s — no users in DB at all",
            merchant_id,
        )
        return {
            "ok": False,
            "stored": False,
            "reason": "no_owner_user_found",
        }

    # Compute expires_at. Salla sends a future unix timestamp (seconds).
    # Convert to seconds-from-now then reuse the same safety-margin logic
    # as the OAuth refresh flow.
    now_ts = int(datetime.now(timezone.utc).timestamp())
    expires_in_sec = max(0, int(expires_unix or 0) - now_ts) if expires_unix else 0

    update: dict[str, Any] = {
        "access_token_encrypted": encrypt_token(access),
        "scope": scope,
        "token_type": token_type,
        "expires_at": _expires_at(expires_in_sec),
        "expires_in_seconds": expires_in_sec,
        "last_refreshed_at": datetime.now(timezone.utc),
        "status": "connected",
        "last_error": None,
        "last_error_at": None,
        # Easy-Mode-specific provenance (so /status can show how this
        # install was created — useful when both Custom + Easy Mode
        # exist).
        "install_mode": "easy_mode",
        "store_id": merchant_id,
        "easy_mode_owner_email": user_email,
    }
    if refresh:
        update["refresh_token_encrypted"] = encrypt_token(refresh)

    await upsert_integration(db, user_id, update)

    log.info(
        "easy_mode.store_authorize merchant=%s → user_id=%s email=%s expires_in=%ss scope=%r",
        merchant_id, user_id, user_email, expires_in_sec, scope,
    )
    return {
        "ok": True,
        "stored": True,
        "user_id": user_id,
        "user_email": user_email,
        "merchant_id": merchant_id,
        "expires_in_seconds": expires_in_sec,
    }


async def _handle_app_installed(db, event_body: dict) -> dict:  # noqa: ARG001
    """`app.installed` arrives alongside `app.store.authorize` and is
    informational only. We log it so the merchant-side install timeline
    is visible in logs, but persistence happens in .authorize."""
    log.info("easy_mode.app_installed merchant=%s", event_body.get("merchant"))
    return {"ok": True, "stored": False, "reason": "informational_event"}


async def _handle_app_updated(db, event_body: dict) -> dict:
    """`app.updated` carries a fresh access_token (subscription/scope
    changes rotate the token). Persist the new token like .authorize.
    """
    # Same shape as .authorize for token data — delegate.
    return await _handle_store_authorize(db, event_body)


async def _handle_app_uninstalled(db, event_body: dict) -> dict:
    """Merchant uninstalled Mezan from their Salla store. Mark the
    integration `not_connected` but keep the row for historical
    auditing (refund disputes, etc.)."""
    merchant_id = event_body.get("merchant")
    user_id, _ = await resolve_owner_user_id(db)
    if not user_id:
        return {"ok": True, "stored": False, "reason": "no_owner_user"}
    result = await db.salla_integrations.update_one(
        {"user_id": user_id},
        {"$set": {
            "status": "not_connected",
            "last_error": "Merchant uninstalled the app from Salla store.",
            "last_error_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    log.info(
        "easy_mode.app_uninstalled merchant=%s user_id=%s matched=%s",
        merchant_id, user_id, result.matched_count,
    )
    return {"ok": True, "stored": result.matched_count > 0, "merchant_id": merchant_id}


# ── Event router ──────────────────────────────────────────────────────
async def dispatch_event(db, event_body: dict) -> dict:
    """Route a parsed (and signature-verified) webhook to its handler.

    Unknown events are ack'd 200 + logged — Salla retries on non-2xx,
    so we MUST 200 anything we don't care about.
    """
    event_name = (event_body.get("event") or "").strip()
    handler_name = EVENT_HANDLERS.get(event_name)
    if not handler_name:
        log.info("easy_mode.ignored event=%r", event_name)
        return {"ok": True, "stored": False, "reason": "event_not_handled", "event": event_name}
    handler = globals()[handler_name]
    return await handler(db, event_body)
