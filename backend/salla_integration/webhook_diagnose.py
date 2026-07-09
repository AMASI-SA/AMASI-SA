"""Diagnostic-only Salla webhook integration inspector.

READ-ONLY. Surfaces the exact state needed to diagnose "Salla says
installed but Mezan shows disconnected":

    • users (id, email, role, created_at) — to see who resolve_owner
      would pick.
    • salla_integrations documents (WITHOUT any tokens) — to see if
      any install actually landed.
    • Env fingerprint of SALLA_WEBHOOK_SECRET — proves it is set,
      shows length + short SHA256 prefix so operator can compare
      against Partner Portal WITHOUT the secret leaking.
    • Recent webhook events from `salla_webhook_events_log` (an
      opt-in append-only trace populated by the app webhook
      handler as of 2026-07-09).

REMOVE this module + its route once the connection state is
recovered and stabilised.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any


async def diagnose_salla_webhook_state(db) -> dict:
    """Collect the four evidence blocks. Never returns tokens."""
    # 1) Users — everyone that resolve_owner_user_id might pick.
    #    Cap the payload: send only the earliest owners + earliest 20
    #    overall — the resolver only ever picks from that head anyway.
    users_all: list[dict] = []
    async for u in db.users.find(
            {}, {"_id": 0, "id": 1, "email": 1, "role": 1,
                 "created_at": 1}).sort("created_at", 1).limit(20):
        users_all.append({
            "id":         u.get("id"),
            "email":      u.get("email"),
            "role":       u.get("role"),
            "created_at": (u["created_at"].isoformat()
                            if hasattr(u.get("created_at"), "isoformat")
                            else u.get("created_at")),
        })
    total_users = await db.users.count_documents({})
    owners_cursor = db.users.find(
        {"role": "owner"},
        {"_id": 0, "id": 1, "email": 1, "role": 1, "created_at": 1},
    ).sort("created_at", 1).limit(10)
    owners: list[dict] = []
    async for u in owners_cursor:
        owners.append({
            "id":         u.get("id"),
            "email":      u.get("email"),
            "role":       u.get("role"),
            "created_at": (u["created_at"].isoformat()
                            if hasattr(u.get("created_at"), "isoformat")
                            else u.get("created_at")),
        })
    users = owners or users_all
    resolver_would_pick = (owners[0] if owners else
                            (users_all[0] if users_all else None))

    # 2) salla_integrations — every install, no tokens ever.
    integrations: list[dict] = []
    async for doc in db.salla_integrations.find(
            {}, {"_id": 0,
                 "user_id":               1,
                 "status":                1,
                 "store_id":              1,
                 "install_mode":          1,
                 "easy_mode_owner_email": 1,
                 "expires_at":            1,
                 "expires_in_seconds":    1,
                 "last_refreshed_at":     1,
                 "last_error":            1,
                 "last_error_at":         1,
                 "created_at":            1,
                 "updated_at":            1,
                 "scope":                 1}):
        # Whitelist projection above already excludes token fields.
        # Convert datetimes.
        for k in ("expires_at", "last_refreshed_at", "last_error_at",
                   "created_at", "updated_at"):
            if hasattr(doc.get(k), "isoformat"):
                doc[k] = doc[k].isoformat()
        integrations.append(doc)

    # 3) Env fingerprint of SALLA_WEBHOOK_SECRET — proves it's set
    # without leaking. Operator compares the fingerprint against the
    # Portal-side value by running the same hash on the Portal input.
    secret_val = os.environ.get("SALLA_WEBHOOK_SECRET") or ""
    if secret_val:
        secret_hash = hashlib.sha256(
            secret_val.encode("utf-8")).hexdigest()
        secret_info: dict[str, Any] = {
            "is_set":               True,
            "length":               len(secret_val),
            "sha256_prefix":        secret_hash[:12],
            "first_2_chars":        secret_val[:2] + "…",
            "last_2_chars":         "…" + secret_val[-2:],
            "compare_hint":         ("Paste your Partner Portal secret "
                                      "value into any SHA-256 tool and "
                                      "verify the first 12 chars of the "
                                      "hex digest match `sha256_prefix`."),
        }
    else:
        secret_info = {"is_set": False}

    other_env = {
        "SALLA_CLIENT_ID_set":     bool(os.environ.get("SALLA_CLIENT_ID")),
        "SALLA_CLIENT_SECRET_set": bool(os.environ.get("SALLA_CLIENT_SECRET")),
        "SALLA_APP_ID_set":        bool(os.environ.get("SALLA_APP_ID")),
        "SALLA_TOKEN_ENC_KEY_set": bool(os.environ.get("SALLA_TOKEN_ENC_KEY")),
        "SALLA_AUTH_BASE":         os.environ.get("SALLA_AUTH_BASE"),
        "SALLA_API_BASE":          os.environ.get("SALLA_API_BASE"),
    }

    # 4) Recent webhook events from the append-only trace collection.
    events: list[dict] = []
    async for ev in db.salla_webhook_events_log.find(
            {}, {"_id": 0}).sort("received_at", -1).limit(50):
        if hasattr(ev.get("received_at"), "isoformat"):
            ev["received_at"] = ev["received_at"].isoformat()
        events.append(ev)

    verdict = _compose_verdict(
        users=users_all, integrations=integrations,
        resolver_would_pick=resolver_would_pick,
        events=events, secret_set=secret_info.get("is_set", False))

    return {
        "ok":                  True,
        "users_total":         total_users,
        "users_owners_count":  len(owners),
        "owners":              owners,
        "users_first_20":      users_all,
        "resolver_would_pick": resolver_would_pick,
        "integrations":        integrations,
        "webhook_secret":      secret_info,
        "env":                 other_env,
        "recent_events":       events,
        "verdict":             verdict,
    }


def _compose_verdict(*, users, integrations, resolver_would_pick,
                      events, secret_set: bool) -> dict:
    """Return the most likely root cause + suggested next step."""
    if not secret_set:
        return {
            "code":   "webhook_secret_not_set",
            "action": ("Set SALLA_WEBHOOK_SECRET in backend/.env and "
                       "restart. Copy the value from Salla Partner "
                       "Portal → Webhooks → Secret."),
        }
    if not users:
        return {"code":   "no_users_in_db",
                 "action": "Create a merchant user first."}
    # Look at events: any recent webhook arrivals?
    seen_authorize = any(
        e.get("event") == "app.store.authorize" for e in events)
    seen_any = bool(events)
    if not seen_any:
        return {
            "code":   "no_webhook_events_logged",
            "action": ("Either the webhook URL in Partner Portal is "
                       "wrong (must be "
                       "https://mezansalla.com/api/salla/webhooks/app), "
                       "the webhook secret differs, or Salla hasn't "
                       "retried yet. Re-trigger 'Resend last event' "
                       "from Partner Portal and re-run this diagnostic."),
        }
    invalid_sig_hits = [e for e in events
                         if e.get("verified") is False]
    if invalid_sig_hits and not seen_authorize:
        return {
            "code":   "signature_verification_failed",
            "action": ("SALLA_WEBHOOK_SECRET in backend/.env does NOT "
                       "match the value in Partner Portal → Webhooks. "
                       "Compare the sha256_prefix above with the same "
                       "hash of the Portal secret."),
        }
    if seen_authorize:
        # Check where it landed.
        stored_hits = [e for e in events
                        if e.get("event") == "app.store.authorize"
                        and e.get("stored") is True]
        if not stored_hits:
            return {
                "code":   "authorize_received_but_not_stored",
                "action": ("Webhook arrived but token persistence "
                           "failed. Check recent_events for the "
                           "`reason` field on the most recent "
                           "app.store.authorize entry."),
            }
        # Stored — but does it match the JWT user opening /status?
        if integrations and resolver_would_pick:
            picked_uid = resolver_would_pick.get("id")
            if not any(i.get("user_id") == picked_uid
                        for i in integrations):
                return {
                    "code":   "stored_under_different_user_id",
                    "action": ("Integration exists but under a "
                               "different user_id than the earliest "
                               "owner. Manual repair required."),
                }
        return {
            "code":   "install_appears_complete",
            "action": ("Integration is stored under the earliest "
                       "owner. If /status still says 'disconnected', "
                       "the logged-in user in the UI is DIFFERENT "
                       "from the earliest owner — try re-login with "
                       "the owner account, or use the repair "
                       "endpoint to move the integration."),
        }
    return {"code":   "inconclusive",
             "action": "Share recent_events with the developer."}
