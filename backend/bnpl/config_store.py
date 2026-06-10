"""BNPL config store — per-user, per-provider credentials + fees.

Collection: `bnpl_settings`
Document shape (one per user × provider):
{
    user_id, provider ('tamara'|'tabby'),
    # — secrets (Fernet ciphertext) —
    api_token_encrypted, notification_token_encrypted,
    secret_key_encrypted, merchant_code,        # tabby
    # — webhook routing (Iter-116 Phase 2B) —
    webhook_secret,                              # per-user URL token
    # — flags —
    environment ('sandbox'|'production'),
    enabled (bool),
    activation_date (str YYYY-MM-DD),           # only sync from this day
    # — fee settings (Iter-116) —
    mdr_percent (float, e.g. 0.06 = 6%),
    fixed_fee_per_order (float, e.g. 1.0 for Tabby),
    vat_on_fees_percent (float, e.g. 0.15),
    settlement_period_days (int, e.g. 7),         # LEGACY — fallback only
    transfer_days (int, e.g. 2),                  # LEGACY — fallback only
    # — Iter-121: weekday-based settlement cycle —
    invoice_weekdays (list[str], lowercase English: "monday"…"sunday"),
    transfer_weekdays (list[str], same vocabulary),
    # — bookkeeping —
    last_sync_at, last_test_ok, last_test_error,
    last_webhook_at,
    created_at, updated_at,
}
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from .crypto import decrypt_token, encrypt_token


BNPL_PROVIDERS = ("tabby", "tamara")

# Canonical weekday vocabulary — lowercase English so we don't have
# any localisation ambiguity in the database.  UI maps these to Arabic.
WEEKDAYS = (
    "saturday", "sunday", "monday", "tuesday",
    "wednesday", "thursday", "friday",
)

# Default fee settings — user can override via Settings UI.
DEFAULTS = {
    "tabby": {
        "mdr_percent": 0.06,
        "fixed_fee_per_order": 1.0,    # user-confirmed: 1 SAR not 1.5
        "vat_on_fees_percent": 0.15,
        "settlement_period_days": 7,
        "transfer_days": 2,
        # Iter-121 — weekday cycle.  Tabby officially closes invoices
        # on Mondays and pays out 1-2 business days later.
        "invoice_weekdays":  ["monday"],
        "transfer_weekdays": ["tuesday", "wednesday"],
        "settlement_fee_per_invoice": 5.0,   # SAR charged ONCE per
                                             # weekly settlement invoice
                                             # (not per order)
        "api_base_url": "https://api.tabby.sa",
    },
    "tamara": {
        "mdr_percent": 0.07,
        "fixed_fee_per_order": 0.0,
        "vat_on_fees_percent": 0.15,
        "settlement_period_days": 7,
        "transfer_days": 2,
        # Iter-121 — Tamara closes invoices Sunday, pays Tuesday.
        "invoice_weekdays":  ["sunday"],
        "transfer_weekdays": ["tuesday"],
        "settlement_fee_per_invoice": 0.0,   # Tamara doesn't charge it
        "api_base_url": "https://api.tamara.co",
    },
}


# Sandbox base URLs — picked automatically when environment == "sandbox"
# so a sandbox API token doesn't get sent to the production endpoint
# (which always returns 401 even for valid sandbox creds).
SANDBOX_URLS = {
    "tabby": "https://api.tabby.ai",       # Tabby uses single URL; sandbox = test keys
    "tamara": "https://api-sandbox.tamara.co",
}


def _resolve_base_url(provider: str, environment: str,
                      explicit: Optional[str] = None) -> str:
    """Pick the correct base URL based on env, with explicit override."""
    if explicit:
        return explicit
    if (environment or "production") == "sandbox":
        return SANDBOX_URLS.get(provider, DEFAULTS[provider]["api_base_url"])
    return DEFAULTS[provider]["api_base_url"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask(s: str, keep: int = 4) -> str:
    """Return e.g. 'sk_live_•••••••abcd' so the UI confirms a value is
    saved AND surfaces its environment (live vs test) at a glance.

    Preserves common credential prefixes when present:
      • sk_live_, sk_test_, pk_live_, pk_test_
    Falls back to first-2 chars for opaque tokens.
    """
    if not s:
        return ""
    if len(s) <= keep:
        return "•" * len(s)
    prefixes = ("sk_live_", "sk_test_", "pk_live_", "pk_test_")
    for pref in prefixes:
        if s.startswith(pref):
            return f"{pref}{'•' * 7}{s[-keep:]}"
    return f"{s[:2]}{'•' * 6}{s[-keep:]}"


async def ensure_indexes(db) -> None:
    try:
        await db.bnpl_settings.create_index(
            [("user_id", 1), ("provider", 1)],
            unique=True, name="bnpl_settings_uniq",
        )
    except Exception:
        pass
    try:
        await db.bnpl_settings.create_index(
            [("webhook_secret", 1)], unique=True, sparse=True,
            name="bnpl_webhook_secret_uniq",
        )
    except Exception:
        pass


async def _ensure_webhook_secret(db, user_id: str, provider: str) -> str:
    """Lazily generate a per-(user × provider) webhook_secret on first
    read. Lookups by this secret resolve to a user_id without exposing
    primary IDs in webhook URLs."""
    doc = await db.bnpl_settings.find_one(
        {"user_id": user_id, "provider": provider},
        {"_id": 0, "webhook_secret": 1},
    )
    if doc and doc.get("webhook_secret"):
        return doc["webhook_secret"]
    secret = uuid.uuid4().hex
    await db.bnpl_settings.update_one(
        {"user_id": user_id, "provider": provider},
        {"$set": {"webhook_secret": secret, "updated_at": _now_iso()},
         "$setOnInsert": {
             "user_id": user_id, "provider": provider,
             "created_at": _now_iso(),
         }},
        upsert=True,
    )
    return secret


async def find_user_by_webhook_secret(
    db, secret: str, provider: str,
) -> Optional[dict]:
    """Reverse-lookup for incoming webhooks. Returns the bnpl_settings
    doc or None.  Index `bnpl_webhook_secret_uniq` makes this O(1)."""
    if not secret or not secret.strip():
        return None
    return await db.bnpl_settings.find_one(
        {"webhook_secret": secret.strip(), "provider": provider},
        {"_id": 0},
    )


def _detect_key_type(decrypted: str) -> str:
    """Return 'live', 'test', or 'unknown' based on the key prefix."""
    if not decrypted:
        return "unknown"
    if decrypted.startswith(("sk_live_", "pk_live_")):
        return "live"
    if decrypted.startswith(("sk_test_", "pk_test_")):
        return "test"
    return "unknown"


async def get_settings(db, user_id: str, provider: str) -> dict:
    """Return MASKED + flag info — never raw secrets."""
    if provider not in BNPL_PROVIDERS:
        return {}
    # Lazily provision a webhook_secret so the UI can display the
    # webhook URL even before any credentials are saved.
    webhook_secret = await _ensure_webhook_secret(db, user_id, provider)
    doc = await db.bnpl_settings.find_one(
        {"user_id": user_id, "provider": provider}, {"_id": 0},
    ) or {}
    defaults = DEFAULTS.get(provider, {})

    return {
        "provider": provider,
        "enabled": bool(doc.get("enabled", False)),
        "environment": doc.get("environment", "production"),
        "activation_date": doc.get("activation_date"),
        "has_api_token": bool(doc.get("api_token_encrypted")),
        "has_notification_token": bool(doc.get("notification_token_encrypted")),
        "has_secret_key": bool(doc.get("secret_key_encrypted")),
        "merchant_code": doc.get("merchant_code") or "",  # not secret
        "api_token_masked": _mask(_try_decrypt(doc.get("api_token_encrypted"))),
        "notification_token_masked": _mask(_try_decrypt(doc.get("notification_token_encrypted"))),
        "secret_key_masked": _mask(_try_decrypt(doc.get("secret_key_encrypted"))),
        "secret_key_type": _detect_key_type(_try_decrypt(doc.get("secret_key_encrypted"))),
        "api_token_type": _detect_key_type(_try_decrypt(doc.get("api_token_encrypted"))),
        "webhook_secret": webhook_secret,
        "mdr_percent": float(doc.get("mdr_percent", defaults.get("mdr_percent", 0))),
        "fixed_fee_per_order": float(doc.get("fixed_fee_per_order",
                                             defaults.get("fixed_fee_per_order", 0))),
        "vat_on_fees_percent": float(doc.get("vat_on_fees_percent",
                                             defaults.get("vat_on_fees_percent", 0))),
        "settlement_period_days": int(doc.get("settlement_period_days",
                                              defaults.get("settlement_period_days", 7))),
        "transfer_days": int(doc.get("transfer_days",
                                     defaults.get("transfer_days", 2))),
        # Iter-121 — weekday cycle (canonical).  Falls back to provider
        # defaults if user hasn't customised yet.
        "invoice_weekdays": list(doc.get("invoice_weekdays")
                                 or defaults.get("invoice_weekdays") or []),
        "transfer_weekdays": list(doc.get("transfer_weekdays")
                                  or defaults.get("transfer_weekdays") or []),
        "settlement_fee_per_invoice": float(doc.get(
            "settlement_fee_per_invoice",
            defaults.get("settlement_fee_per_invoice", 0),
        )),
        "api_base_url": _resolve_base_url(
            provider, doc.get("environment", "production"),
            doc.get("api_base_url"),
        ),
        "last_sync_at": doc.get("last_sync_at"),
        "last_test_ok": doc.get("last_test_ok"),
        "last_test_error": doc.get("last_test_error"),
        "last_webhook_at": doc.get("last_webhook_at"),
    }


def _try_decrypt(blob) -> str:
    if not blob:
        return ""
    try:
        return decrypt_token(blob)
    except ValueError:
        return ""


async def get_raw_secrets(db, user_id: str, provider: str) -> dict:
    """Internal — returns decrypted secrets for client use."""
    if provider not in BNPL_PROVIDERS:
        return {}
    doc = await db.bnpl_settings.find_one(
        {"user_id": user_id, "provider": provider}, {"_id": 0},
    ) or {}
    environment = doc.get("environment", "production")
    return {
        "api_token": _try_decrypt(doc.get("api_token_encrypted")),
        "notification_token": _try_decrypt(doc.get("notification_token_encrypted")),
        "secret_key": _try_decrypt(doc.get("secret_key_encrypted")),
        "merchant_code": doc.get("merchant_code") or "",
        "api_base_url": _resolve_base_url(
            provider, environment, doc.get("api_base_url"),
        ),
        "environment": environment,
        "enabled": bool(doc.get("enabled", False)),
        "activation_date": doc.get("activation_date"),
    }


async def save_settings(
    db, user_id: str, provider: str, payload: dict,
) -> dict:
    """Upsert. Empty-string secret values are IGNORED (keep existing)
    so the masked UI value doesn't accidentally erase the stored key."""
    if provider not in BNPL_PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")

    existing = await db.bnpl_settings.find_one(
        {"user_id": user_id, "provider": provider}, {"_id": 0},
    ) or {}

    update: dict = {
        "user_id": user_id,
        "provider": provider,
        "updated_at": _now_iso(),
    }
    if "created_at" not in existing:
        update["created_at"] = _now_iso()

    # ── Explicit clearing — `clear_secrets: true` wipes all keys ──
    unset: dict = {}
    if payload.get("clear_secrets"):
        for field in ("api_token_encrypted", "notification_token_encrypted",
                      "secret_key_encrypted"):
            unset[field] = ""
        # Also clear the cached error state so the UI resets cleanly.
        unset["last_test_ok"] = ""
        unset["last_test_error"] = ""

    # ── Secrets (only update when a non-empty value is provided) ──
    if (tok := (payload.get("api_token") or "").strip()):
        update["api_token_encrypted"] = encrypt_token(tok)
    if (tok := (payload.get("notification_token") or "").strip()):
        update["notification_token_encrypted"] = encrypt_token(tok)
    if (tok := (payload.get("secret_key") or "").strip()):
        update["secret_key_encrypted"] = encrypt_token(tok)

    # ── Plain fields ──
    if "merchant_code" in payload:
        update["merchant_code"] = (payload.get("merchant_code") or "").strip()
    if "environment" in payload:
        env = payload.get("environment")
        if env in ("sandbox", "production"):
            update["environment"] = env
    if "enabled" in payload:
        update["enabled"] = bool(payload.get("enabled"))
    if "activation_date" in payload:
        update["activation_date"] = payload.get("activation_date") or None
    if "api_base_url" in payload and payload.get("api_base_url"):
        update["api_base_url"] = payload["api_base_url"].strip()

    # ── Fees ──
    for k in ("mdr_percent", "fixed_fee_per_order", "vat_on_fees_percent",
              "settlement_fee_per_invoice"):
        if k in payload and payload.get(k) is not None:
            try:
                update[k] = float(payload[k])
            except (TypeError, ValueError):
                pass
    for k in ("settlement_period_days", "transfer_days"):
        if k in payload and payload.get(k) is not None:
            try:
                update[k] = int(payload[k])
            except (TypeError, ValueError):
                pass

    # Iter-121 — weekday lists.  Sanitize against the canonical
    # vocabulary so we never store typos like "satrday".
    for k in ("invoice_weekdays", "transfer_weekdays"):
        if k in payload and isinstance(payload.get(k), list):
            clean = [
                str(d).strip().lower()
                for d in payload[k]
                if str(d).strip().lower() in WEEKDAYS
            ]
            # Deduplicate while preserving order
            seen = set()
            update[k] = [d for d in clean if not (d in seen or seen.add(d))]

    await db.bnpl_settings.update_one(
        {"user_id": user_id, "provider": provider},
        {"$set": update, **({"$unset": unset} if unset else {})},
        upsert=True,
    )

    # Iter-126 — UNIFIED SOURCE OF TRUTH: also mirror fee fields into
    # `users.settings.payment_methods` so the Settings → Payment Methods
    # page reflects the same numbers.  This keeps both UIs in lockstep
    # without forcing the merchant to update the rate in two places.
    PROVIDER_AR_NAME = {"tabby": "تابي", "tamara": "تمارا"}
    ar_name = PROVIDER_AR_NAME.get(provider)
    if ar_name and any(
        k in payload and payload.get(k) is not None
        for k in ("mdr_percent", "fixed_fee_per_order", "vat_on_fees_percent")
    ):
        pm_update: dict = {}
        if payload.get("mdr_percent") is not None:
            try:
                pm_update["settings.payment_methods.$.commission_percent"] = round(
                    float(payload["mdr_percent"]) * 100, 4,
                )
            except (TypeError, ValueError):
                pass
        if payload.get("vat_on_fees_percent") is not None:
            try:
                pm_update["settings.payment_methods.$.vat_percent"] = round(
                    float(payload["vat_on_fees_percent"]) * 100, 4,
                )
            except (TypeError, ValueError):
                pass
        if payload.get("fixed_fee_per_order") is not None:
            try:
                pm_update["settings.payment_methods.$.fixed_fee"] = float(
                    payload["fixed_fee_per_order"],
                )
            except (TypeError, ValueError):
                pass
        if pm_update:
            await db.users.update_one(
                {"id": user_id, "settings.payment_methods.name": ar_name},
                {"$set": pm_update},
            )

    return await get_settings(db, user_id, provider)


async def record_test_result(db, user_id: str, provider: str,
                             ok: bool, error: Optional[str] = None) -> None:
    await db.bnpl_settings.update_one(
        {"user_id": user_id, "provider": provider},
        {"$set": {
            "last_test_ok": bool(ok),
            "last_test_error": (error or "") if not ok else "",
            "last_test_at": _now_iso(),
        }},
        upsert=True,
    )


async def record_sync(db, user_id: str, provider: str) -> None:
    await db.bnpl_settings.update_one(
        {"user_id": user_id, "provider": provider},
        {"$set": {"last_sync_at": _now_iso()}},
        upsert=True,
    )
